"""Applications must never have to discover capabilities by failure."""

from __future__ import annotations

import pytest

from pdfengine import PdfEngine
from pdfengine.api.contracts import API_VERSION, CommandDispatcher
from pdfengine.ocr.base import CAPABILITY_STATES
from support.fakes import DpiStubRenderer, StubOcr


@pytest.fixture
def dispatcher(tmp_path):
    dispatcher = CommandDispatcher(
        PdfEngine(
            cache_root=tmp_path / "cache",
            renderer=DpiStubRenderer(),
            ocr=StubOcr(),
        )
    )
    yield dispatcher
    dispatcher.close()


def _capabilities(dispatcher, session_id: str | None = None) -> dict:
    request = {
        "apiVersion": API_VERSION,
        "requestId": "caps",
        "command": "capabilities",
    }
    if session_id is not None:
        request["sessionId"] = session_id
    response = dispatcher.dispatch(request)
    assert response["ok"] is True, response.get("error")
    return response["result"]["capabilities"]


def test_engine_capabilities_need_no_open_document(dispatcher) -> None:
    capabilities = _capabilities(dispatcher)

    assert set(capabilities) >= {"preview", "ocr", "operations", "save", "filters"}
    assert "document" not in capabilities


def test_the_ocr_section_lists_languages_and_modes(dispatcher) -> None:
    section = _capabilities(dispatcher)["ocr"]

    assert section["languages"] == ["eng"]
    assert section["modes"] == ["lstm"]
    assert section["engine"]


def test_a_session_adds_document_capabilities_and_allowed_commands(
    dispatcher, write_pdf
) -> None:
    opened = dispatcher.dispatch(
        {
            "apiVersion": API_VERSION,
            "requestId": "open",
            "command": "open",
            "path": str(write_pdf(["only"])),
        }
    )["result"]

    capabilities = _capabilities(dispatcher, opened["sessionId"])

    assert capabilities["document"]["structuralEdit"]["state"] == "ready"
    assert "textContent" in capabilities["document"]
    assert "close" in capabilities["allowedCommands"]
    assert "open" not in capabilities["allowedCommands"]


def test_the_read_key_survives_as_an_alias_of_document(dispatcher, write_pdf) -> None:
    opened = dispatcher.dispatch(
        {
            "apiVersion": API_VERSION,
            "requestId": "open",
            "command": "open",
            "path": str(write_pdf(["only"])),
        }
    )["result"]

    capabilities = _capabilities(dispatcher, opened["sessionId"])

    assert capabilities["read"] == capabilities["document"]


def test_every_capability_state_is_from_the_shared_vocabulary(
    dispatcher, write_pdf
) -> None:
    opened = dispatcher.dispatch(
        {
            "apiVersion": API_VERSION,
            "requestId": "open",
            "command": "open",
            "path": str(write_pdf(["only"])),
        }
    )["result"]
    capabilities = _capabilities(dispatcher, opened["sessionId"])

    states = [capabilities["preview"]["state"], capabilities["ocr"]["state"]]
    states += [entry["state"] for entry in capabilities["operations"]]
    states += [entry["state"] for entry in capabilities["document"].values()]

    assert all(state in CAPABILITY_STATES for state in states)


def test_every_operation_entry_reports_its_own_state(dispatcher) -> None:
    operations = _capabilities(dispatcher)["operations"]

    kinds = {entry["kind"] for entry in operations}
    assert "add_text_layer" in kinds
    assert all("state" in entry for entry in operations)


def test_ocr_being_unavailable_marks_the_operation_unavailable(tmp_path) -> None:
    dispatcher = CommandDispatcher(
        PdfEngine(
            cache_root=tmp_path / "cache",
            renderer=DpiStubRenderer(),
            ocr=StubOcr(state="unavailable", detail="Tesseract executable not found"),
        )
    )
    try:
        operations = _capabilities(dispatcher)["operations"]
        entry = next(item for item in operations if item["kind"] == "add_text_layer")

        assert entry["state"] == "unavailable"
        assert entry["detail"]
    finally:
        dispatcher.close()


def test_capabilities_for_a_closed_session_report_the_lifecycle_error(
    dispatcher, write_pdf
) -> None:
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
            "requestId": "caps",
            "command": "capabilities",
            "sessionId": opened["sessionId"],
        }
    )

    assert response["error"]["code"] == "session_invalid_state"
    assert response["error"]["details"]["allowed"] == ["open"]
