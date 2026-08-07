"""A closed session must stay distinguishable from one that never existed."""

from __future__ import annotations

import pytest

from pdfengine import PdfEngine
from pdfengine.api.session import SessionState
from pdfengine.errors import SessionNotFoundError, SessionStateError
from support.fakes import StubRenderer


@pytest.fixture
def engine(tmp_path):
    engine = PdfEngine(cache_root=tmp_path / "cache", renderer=StubRenderer())
    yield engine
    engine.close_all()


def test_an_id_that_never_existed_is_not_found(engine) -> None:
    with pytest.raises(SessionNotFoundError):
        engine.session("session_never_issued")


def test_a_closed_id_reports_its_lifecycle_state_instead(engine, write_pdf) -> None:
    session = engine.open_document(write_pdf(["only"]))
    session_id = session.session_id
    engine.close(session)

    with pytest.raises(SessionStateError) as caught:
        engine.session(session_id)

    error = caught.value
    assert error.code == "session_invalid_state"
    assert error.session_id == session_id
    assert error.state == "closed"
    assert error.allowed == ["open"]


def test_an_open_session_reports_the_open_state(engine, write_pdf) -> None:
    session = engine.open_document(write_pdf(["only"]))

    assert session.state_name is SessionState.OPEN


def test_a_tombstone_keeps_no_document_cache_or_password(engine, write_pdf) -> None:
    session = engine.open_document(write_pdf(["only"]), password="secret")
    cache_dir = session.cache_dir
    session_id = session.session_id
    engine.close(session)

    tombstone = engine.tombstone(session_id)

    assert tombstone.session_id == session_id
    assert not hasattr(tombstone, "password")
    assert not hasattr(tombstone, "model")
    assert not cache_dir.exists()


def test_inspect_reports_the_session_state(engine, write_pdf) -> None:
    from pdfengine.api.contracts import API_VERSION, CommandDispatcher

    dispatcher = CommandDispatcher(engine)
    opened = dispatcher.dispatch(
        {
            "apiVersion": API_VERSION,
            "requestId": "open",
            "command": "open",
            "path": str(write_pdf(["only"])),
        }
    )["result"]

    inspected = dispatcher.dispatch(
        {
            "apiVersion": API_VERSION,
            "requestId": "inspect",
            "command": "inspect",
            "sessionId": opened["sessionId"],
        }
    )["result"]

    assert inspected["state"] == "open"


def test_a_command_against_a_closed_session_returns_the_typed_envelope(
    engine, write_pdf
) -> None:
    from pdfengine.api.contracts import API_VERSION, CommandDispatcher

    dispatcher = CommandDispatcher(engine)
    opened = dispatcher.dispatch(
        {
            "apiVersion": API_VERSION,
            "requestId": "open",
            "command": "open",
            "path": str(write_pdf(["only"])),
        }
    )["result"]
    dispatcher.dispatch(
        {
            "apiVersion": API_VERSION,
            "requestId": "close",
            "command": "close",
            "sessionId": opened["sessionId"],
        }
    )

    response = dispatcher.dispatch(
        {
            "apiVersion": API_VERSION,
            "requestId": "after",
            "command": "inspect",
            "sessionId": opened["sessionId"],
        }
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "session_invalid_state"
    assert response["error"]["details"]["state"] == "closed"
    assert response["error"]["details"]["allowed"] == ["open"]
