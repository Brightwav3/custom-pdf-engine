"""Every command rejects unknown request fields — including undo and redo.

The policy in `docs/contract-policy.md` promises that responses may grow but
requests may not, so a typo or a payload built for a newer engine fails at the
boundary instead of being half-applied. `undo` and `redo` were the two commands
that did not keep that promise; this test is what stops them regressing.
"""

from __future__ import annotations

import pytest

from pdfengine import PdfEngine
from pdfengine.api.contracts import API_VERSION, CommandDispatcher
from support.fakes import StubRenderer


@pytest.fixture
def dispatcher(tmp_path):
    dispatcher = CommandDispatcher(
        PdfEngine(cache_root=tmp_path / "cache", renderer=StubRenderer())
    )
    yield dispatcher
    dispatcher.close()


def _request(command: str, **fields) -> dict:
    return {
        "apiVersion": API_VERSION,
        "requestId": "r-1",
        "command": command,
        **fields,
    }


@pytest.fixture
def session_id(dispatcher, write_pdf) -> str:
    opened = dispatcher.dispatch(
        _request("open", path=str(write_pdf(["a", "b"])))
    )["result"]
    dispatcher.dispatch(
        _request(
            "apply",
            sessionId=opened["sessionId"],
            operations=[{"kind": "rotate_pages", "pageIds": [], "degrees": 90}],
        )
    )
    return opened["sessionId"]


@pytest.mark.parametrize("command", ["undo", "redo"])
def test_undo_and_redo_reject_an_unknown_request_field(
    dispatcher, session_id, command: str
) -> None:
    response = dispatcher.dispatch(
        _request(command, sessionId=session_id, bogusField=1)
    )

    assert response["error"]["code"] == "invalid_request"
    assert response["error"]["details"]["field"] == "bogusField"


@pytest.mark.parametrize("command", ["undo", "redo"])
def test_undo_and_redo_still_accept_a_well_formed_request(
    dispatcher, session_id, command: str
) -> None:
    response = dispatcher.dispatch(_request(command, sessionId=session_id))

    assert "error" not in response
    assert response["result"]["sessionId"] == session_id
