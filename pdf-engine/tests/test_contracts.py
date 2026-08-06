from __future__ import annotations

import base64
import json

import pytest

from pdfengine.api.contracts import (
    API_VERSION,
    SCHEMA_NAMES,
    CommandDispatcher,
    parse_operation,
    schema_bytes,
)
from pdfengine.errors import InvalidRequestError

from test_engine import StubRenderer
from test_rewrite import page_texts


@pytest.fixture
def dispatcher(tmp_path):
    from pdfengine import PdfEngine

    dispatcher = CommandDispatcher(
        PdfEngine(cache_root=tmp_path / "cache", renderer=StubRenderer())
    )
    yield dispatcher
    dispatcher.close()


def request(command: str, request_id: str = "r-1", **fields) -> dict:
    return {
        "apiVersion": API_VERSION,
        "requestId": request_id,
        "command": command,
        **fields,
    }


@pytest.fixture
def opened(dispatcher, write_pdf):
    response = dispatcher.dispatch(
        request("open", path=str(write_pdf(["alpha", "beta", "gamma"], title="Original")))
    )
    assert response["ok"] is True
    return response["result"]


def test_invalid_operation_has_a_machine_readable_error_envelope(
    dispatcher, opened
) -> None:
    response = dispatcher.dispatch(
        request("apply", "r-7", sessionId=opened["sessionId"], operations=[])
    )

    assert response == {
        "apiVersion": "v1",
        "requestId": "r-7",
        "ok": False,
        "error": {
            "code": "invalid_request",
            "message": "operations must not be empty",
            "details": {"field": "operations"},
        },
        "warnings": [],
    }


def test_open_returns_pages_capabilities_and_next_actions(opened) -> None:
    assert opened["sessionId"].startswith("session_")
    assert opened["document"]["pageCount"] == 3
    assert opened["document"]["title"] == "Original"
    assert opened["document"]["pages"][0]["pageId"].startswith("page_")
    assert opened["capabilities"]["preview"]["state"] == "ready"
    assert opened["nextActions"] == ["inspect", "render", "apply", "save", "close"]


def test_inspect_reports_the_current_projection(dispatcher, opened) -> None:
    session_id = opened["sessionId"]
    page_a = opened["document"]["pages"][0]["pageId"]
    dispatcher.dispatch(
        request(
            "apply",
            sessionId=session_id,
            operations=[{"kind": "delete_pages", "pageIds": [page_a]}],
        )
    )

    response = dispatcher.dispatch(request("inspect", sessionId=session_id))

    assert response["result"]["document"]["pageCount"] == 2
    assert response["result"]["canUndo"] is True
    assert response["result"]["canRedo"] is False


def test_a_dry_run_apply_projects_without_recording(dispatcher, opened) -> None:
    session_id = opened["sessionId"]
    page_a = opened["document"]["pages"][0]["pageId"]

    response = dispatcher.dispatch(
        request(
            "apply",
            sessionId=session_id,
            dryRun=True,
            operations=[{"kind": "delete_pages", "pageIds": [page_a]}],
        )
    )

    assert response["result"]["dryRun"] is True
    assert response["result"]["document"]["pageCount"] == 2
    assert (
        dispatcher.dispatch(request("inspect", sessionId=session_id))["result"][
            "document"
        ]["pageCount"]
        == 3
    )


def test_undo_and_redo_are_addressable_commands(dispatcher, opened) -> None:
    session_id = opened["sessionId"]
    page_a = opened["document"]["pages"][0]["pageId"]
    dispatcher.dispatch(
        request(
            "apply",
            sessionId=session_id,
            operations=[{"kind": "delete_pages", "pageIds": [page_a]}],
        )
    )

    undone = dispatcher.dispatch(request("undo", sessionId=session_id))
    redone = dispatcher.dispatch(request("redo", sessionId=session_id))

    assert undone["result"]["document"]["pageCount"] == 3
    assert redone["result"]["document"]["pageCount"] == 2


def test_render_returns_an_artifact_id_and_inline_png(dispatcher, opened) -> None:
    page_a = opened["document"]["pages"][0]["pageId"]

    response = dispatcher.dispatch(
        request("render", sessionId=opened["sessionId"], pageId=page_a, width=64)
    )

    result = response["result"]
    assert result["artifactId"].startswith("artifact_")
    assert result["contentType"] == "image/png"
    assert base64.b64decode(result["imageBase64"]).startswith(b"\x89PNG\r\n\x1a\n")
    assert dispatcher.artifacts[result["artifactId"]] == base64.b64decode(
        result["imageBase64"]
    )
    assert (result["width"], result["height"], result["cacheHit"]) == (64, 128, False)


def test_save_reports_the_written_path(dispatcher, opened, tmp_path) -> None:
    target = tmp_path / "saved.pdf"

    response = dispatcher.dispatch(
        request("save", sessionId=opened["sessionId"], path=str(target))
    )

    assert response["result"]["written"] is True
    assert page_texts(target) == ["alpha", "beta", "gamma"]


def test_a_dry_run_save_writes_no_file(dispatcher, opened, tmp_path) -> None:
    target = tmp_path / "saved.pdf"

    response = dispatcher.dispatch(
        request("save", sessionId=opened["sessionId"], path=str(target), dryRun=True)
    )

    assert response["result"]["dryRun"] is True
    assert response["result"]["written"] is False
    assert not target.exists()


def test_close_ends_the_session(dispatcher, opened) -> None:
    session_id = opened["sessionId"]

    assert dispatcher.dispatch(request("close", sessionId=session_id))["result"] == {
        "sessionId": session_id,
        "closed": True,
    }
    follow_up = dispatcher.dispatch(request("inspect", sessionId=session_id))
    assert follow_up["error"]["code"] == "session_not_found"


@pytest.mark.parametrize(
    ("payload", "code", "field"),
    [
        ({"requestId": "r", "command": "open"}, "invalid_request", "apiVersion"),
        ({"apiVersion": "v2", "requestId": "r", "command": "open"}, "invalid_request", "apiVersion"),
        ({"apiVersion": "v1", "command": "open"}, "invalid_request", "requestId"),
        ({"apiVersion": "v1", "requestId": "r", "command": "explode"}, "invalid_request", "command"),
    ],
)
def test_malformed_envelopes_are_rejected_deterministically(
    dispatcher, payload, code: str, field: str
) -> None:
    response = dispatcher.dispatch(payload)

    assert response["ok"] is False
    assert response["error"]["code"] == code
    assert response["error"]["details"]["field"] == field


def test_unknown_request_fields_are_rejected(dispatcher, write_pdf) -> None:
    response = dispatcher.dispatch(
        request("open", path=str(write_pdf(["a"])), colour="red")
    )

    assert response["error"]["details"]["field"] == "colour"


def test_an_unsupported_document_names_the_blocking_feature(dispatcher, tmp_path) -> None:
    from conftest import assemble_pdf

    path = tmp_path / "encrypted.pdf"
    path.write_bytes(
        assemble_pdf([b"<< /Filter /Standard >>"], trailer_entries=b" /Encrypt 1 0 R")
    )

    response = dispatcher.dispatch(request("open", path=str(path)))

    assert response["error"]["code"] == "unsupported_pdf"
    assert response["error"]["details"]["feature"] == "encryption"


def test_engine_errors_keep_their_stable_codes(dispatcher, opened) -> None:
    response = dispatcher.dispatch(
        request(
            "apply",
            sessionId=opened["sessionId"],
            operations=[{"kind": "delete_pages", "pageIds": ["page_missing"]}],
        )
    )

    assert response["error"]["code"] == "invalid_operation"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"kind": "explode"}, "unknown operation kind"),
        ({"kind": "rotate_pages", "pageIds": ["p"], "degrees": 45}, "90, 180, or 270"),
        ({"kind": "rotate_pages", "pageIds": [], "degrees": 90}, "must not be empty"),
        ({"kind": "delete_pages", "pageIds": "p"}, "must be an array"),
        ({"kind": "delete_pages", "pageIds": ["p"], "extra": 1}, "unknown operation field"),
        ({"kind": "crop_pages", "pageIds": ["p"], "box": [0, 0, 1]}, "four numbers"),
        ({"kind": "set_metadata", "entries": {"colour": "red"}}, "unsupported metadata"),
    ],
)
def test_operation_payloads_are_validated(payload, message: str) -> None:
    with pytest.raises(InvalidRequestError, match=message):
        parse_operation(payload)


def test_every_operation_kind_round_trips_from_json() -> None:
    payloads = [
        {"kind": "rotate_pages", "pageIds": ["p"], "degrees": 90},
        {"kind": "delete_pages", "pageIds": ["p"]},
        {"kind": "reorder_pages", "pageIds": ["p"]},
        {"kind": "extract_pages", "pageIds": ["p"]},
        {"kind": "insert_blank_page", "afterPageId": "p", "width": 10, "height": 20},
        {"kind": "crop_pages", "pageIds": ["p"], "box": [0, 0, 10, 10]},
        {"kind": "set_metadata", "entries": {"title": "T"}},
        {"kind": "import_pages", "sourceSessionId": "s", "pageIds": ["p"]},
    ]

    assert [parse_operation(item).kind for item in payloads] == [
        item["kind"] for item in payloads
    ]


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_every_declared_schema_is_valid_json(name: str) -> None:
    document = json.loads(schema_bytes(name))

    assert document["$schema"].startswith("https://json-schema.org/")
    assert schema_bytes(f"{name}.json") == schema_bytes(name)


def test_an_unknown_schema_is_rejected() -> None:
    with pytest.raises(InvalidRequestError, match="unknown schema"):
        schema_bytes("nope")


def test_open_reports_read_capability_for_a_clean_document(opened) -> None:
    read = opened["capabilities"]["read"]

    assert read["structuralEdit"] == {"state": "ready", "detail": ""}
    assert read["textContent"] == {
        "state": "ready",
        "detail": "",
        "filters": [],
        "objectCount": 0,
    }


def test_open_reports_blocked_text_content_for_a_scanned_document(
    dispatcher, tmp_path
) -> None:
    from test_engine import WITH_IMAGE

    source = tmp_path / "scanned.pdf"
    source.write_bytes(WITH_IMAGE.read_bytes())

    result = dispatcher.dispatch(request("open", path=str(source)))["result"]
    read = result["capabilities"]["read"]

    assert read["structuralEdit"]["state"] == "ready"
    assert read["textContent"]["state"] == "blocked"
    assert read["textContent"]["filters"] == ["DCTDecode"]
    assert read["textContent"]["objectCount"] == 1
    assert read["textContent"]["detail"]


def test_a_bare_capabilities_command_describes_no_document(dispatcher) -> None:
    result = dispatcher.dispatch(request("capabilities"))["result"]

    assert "read" not in result["capabilities"]
