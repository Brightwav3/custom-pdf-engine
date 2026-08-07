"""OCR must be reachable over JSON, not only from Python."""

from __future__ import annotations

import pytest

from pdfengine import PdfEngine
from pdfengine.api.contracts import API_VERSION, CommandDispatcher
from pdfengine.api.models import AddTextLayer
from pdfengine.api.contracts import parse_operation
from pdfengine.errors import InvalidRequestError
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


def test_a_minimal_payload_parses_with_documented_defaults() -> None:
    operation = parse_operation({"kind": "add_text_layer", "pageIds": ["page_a"]})

    assert isinstance(operation, AddTextLayer)
    assert operation.page_ids == ("page_a",)
    assert operation.language == "eng"
    assert operation.mode == "lstm"
    assert operation.dpi == 300
    assert operation.min_confidence == 0.0


def test_every_field_round_trips_from_camel_case() -> None:
    operation = parse_operation(
        {
            "kind": "add_text_layer",
            "pageIds": ["page_a", "page_b"],
            "language": "ces",
            "mode": "legacy",
            "dpi": 150,
            "minConfidence": 60.5,
        }
    )

    assert operation.language == "ces"
    assert operation.mode == "legacy"
    assert operation.dpi == 150
    assert operation.min_confidence == 60.5


def test_an_unknown_field_is_still_rejected() -> None:
    with pytest.raises(InvalidRequestError) as caught:
        parse_operation(
            {"kind": "add_text_layer", "pageIds": ["page_a"], "psm": 6}
        )

    assert caught.value.field == "psm"


def test_an_unknown_mode_is_an_invalid_request_not_a_crash() -> None:
    with pytest.raises(InvalidRequestError):
        parse_operation(
            {"kind": "add_text_layer", "pageIds": ["page_a"], "mode": "magic"}
        )


def test_applying_a_text_layer_over_json_recognizes_the_page(
    dispatcher, write_pdf
) -> None:
    opened = dispatcher.dispatch(
        {
            "apiVersion": API_VERSION,
            "requestId": "open",
            "command": "open",
            "path": str(write_pdf(["scanned"])),
        }
    )["result"]
    page_id = opened["document"]["pages"][0]["pageId"]

    response = dispatcher.dispatch(
        {
            "apiVersion": API_VERSION,
            "requestId": "ocr",
            "command": "apply",
            "sessionId": opened["sessionId"],
            "operations": [{"kind": "add_text_layer", "pageIds": [page_id]}],
        }
    )

    assert response["ok"] is True, response.get("error")
    assert response["result"]["document"]["pageCount"] == 1
