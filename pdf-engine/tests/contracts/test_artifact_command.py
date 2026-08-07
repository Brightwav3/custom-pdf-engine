"""Artifact retrieval must work on every surface, not only over HTTP."""

from __future__ import annotations

import base64

import pytest

from pdfengine import PdfEngine
from pdfengine.api.contracts import API_VERSION, COMMANDS, CommandDispatcher
from support.fakes import DpiStubRenderer, StubOcr


@pytest.fixture
def dispatcher(tmp_path):
    dispatcher = CommandDispatcher(
        PdfEngine(
            cache_root=tmp_path / "cache", renderer=DpiStubRenderer(), ocr=StubOcr()
        )
    )
    yield dispatcher
    dispatcher.close()


def _open(dispatcher, write_pdf) -> dict:
    return dispatcher.dispatch(
        {
            "apiVersion": API_VERSION,
            "requestId": "open",
            "command": "open",
            "path": str(write_pdf(["only"])),
        }
    )["result"]


def test_artifact_is_a_documented_command() -> None:
    assert "artifact" in COMMANDS


def test_render_returns_a_descriptor_and_keeps_the_inline_image(
    dispatcher, write_pdf
) -> None:
    opened = _open(dispatcher, write_pdf)
    page_id = opened["document"]["pages"][0]["pageId"]

    result = dispatcher.dispatch(
        {
            "apiVersion": API_VERSION,
            "requestId": "render",
            "command": "render",
            "sessionId": opened["sessionId"],
            "pageId": page_id,
        }
    )["result"]

    assert result["imageBase64"]
    artifact = result["artifact"]
    assert artifact["kind"] == "page_render"
    assert artifact["contentType"] == "image/png"
    assert artifact["byteSize"] > 0
    assert artifact["sessionId"] == opened["sessionId"]
    assert "storage" not in artifact


def test_the_artifact_command_returns_the_same_bytes_that_were_rendered(
    dispatcher, write_pdf
) -> None:
    opened = _open(dispatcher, write_pdf)
    rendered = dispatcher.dispatch(
        {
            "apiVersion": API_VERSION,
            "requestId": "render",
            "command": "render",
            "sessionId": opened["sessionId"],
            "pageId": opened["document"]["pages"][0]["pageId"],
        }
    )["result"]

    fetched = dispatcher.dispatch(
        {
            "apiVersion": API_VERSION,
            "requestId": "fetch",
            "command": "artifact",
            "sessionId": opened["sessionId"],
            "artifactId": rendered["artifact"]["artifactId"],
        }
    )["result"]

    assert fetched["bytes"] == rendered["imageBase64"]
    assert fetched["artifact"] == rendered["artifact"]
    assert base64.b64decode(fetched["bytes"])[:4] == b"\x89PNG"


def test_save_returns_a_saved_document_artifact(dispatcher, write_pdf, tmp_path) -> None:
    opened = _open(dispatcher, write_pdf)
    target = tmp_path / "out.pdf"

    result = dispatcher.dispatch(
        {
            "apiVersion": API_VERSION,
            "requestId": "save",
            "command": "save",
            "sessionId": opened["sessionId"],
            "path": str(target),
        }
    )["result"]

    assert result["path"] == str(target.resolve())
    assert result["artifact"]["kind"] == "saved_document"
    assert result["artifact"]["contentType"] == "application/pdf"


def test_a_dry_run_save_issues_no_artifact(dispatcher, write_pdf, tmp_path) -> None:
    opened = _open(dispatcher, write_pdf)

    result = dispatcher.dispatch(
        {
            "apiVersion": API_VERSION,
            "requestId": "save",
            "command": "save",
            "sessionId": opened["sessionId"],
            "path": str(tmp_path / "out.pdf"),
            "dryRun": True,
        }
    )["result"]

    assert result["written"] is False
    assert result.get("artifact") is None


def test_another_session_cannot_fetch_an_artifact(dispatcher, write_pdf) -> None:
    first = _open(dispatcher, write_pdf)
    second = _open(dispatcher, write_pdf)
    rendered = dispatcher.dispatch(
        {
            "apiVersion": API_VERSION,
            "requestId": "render",
            "command": "render",
            "sessionId": first["sessionId"],
            "pageId": first["document"]["pages"][0]["pageId"],
        }
    )["result"]

    response = dispatcher.dispatch(
        {
            "apiVersion": API_VERSION,
            "requestId": "steal",
            "command": "artifact",
            "sessionId": second["sessionId"],
            "artifactId": rendered["artifact"]["artifactId"],
        }
    )

    assert response["ok"] is False
    assert response["error"]["details"]["field"] == "artifactId"
    assert first["sessionId"] not in response["error"]["message"]


def test_the_artifact_command_rejects_unknown_request_fields(
    dispatcher, write_pdf
) -> None:
    opened = _open(dispatcher, write_pdf)

    response = dispatcher.dispatch(
        {
            "apiVersion": API_VERSION,
            "requestId": "bad",
            "command": "artifact",
            "sessionId": opened["sessionId"],
            "artifactId": "artifact_absent",
            "sneaky": True,
        }
    )

    assert response["ok"] is False
    assert response["error"]["details"]["field"] == "sneaky"


def test_closing_a_session_forgets_its_artifacts_but_keeps_the_saved_file(
    dispatcher, write_pdf, tmp_path
) -> None:
    opened = _open(dispatcher, write_pdf)
    target = tmp_path / "kept.pdf"
    saved = dispatcher.dispatch(
        {
            "apiVersion": API_VERSION,
            "requestId": "save",
            "command": "save",
            "sessionId": opened["sessionId"],
            "path": str(target),
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

    assert target.exists(), "close must never delete a committed saved document"

    response = dispatcher.dispatch(
        {
            "apiVersion": API_VERSION,
            "requestId": "after",
            "command": "artifact",
            "sessionId": opened["sessionId"],
            "artifactId": saved["artifact"]["artifactId"],
        }
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "session_invalid_state"


def test_every_command_but_open_is_allowed_on_an_open_session() -> None:
    """The advertised command list must not drift from the real one."""

    assert PdfEngine.ALLOWED_COMMANDS_WHEN_OPEN == tuple(
        command for command in COMMANDS if command != "open"
    )
