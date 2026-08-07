"""The v0.2 acceptance test. Real parser, real writer, fake external binaries.

open -> inspect -> capabilities -> render -> apply -> render -> OCR -> save
     -> reopen -> verify -> close

Parametrized over all three surfaces, because "v0.2 works" has to mean it works
however a caller reached the engine, not just through the Python API.
"""

from __future__ import annotations

import pytest

from pdfengine.api.contracts import API_VERSION
from pdfengine.writing.glyphless import build_font
from pdfengine.writing.textlayer import DEFAULT_FONT_NAME, INVISIBLE_RENDER_MODE
from support.surfaces import surface_dispatchers


_SURFACE_NAMES = ("python", "jsonl", "http")


@pytest.fixture(params=_SURFACE_NAMES)
def surface(request, tmp_path):
    with surface_dispatchers(tmp_path) as three:
        yield three[_SURFACE_NAMES.index(request.param)]


def _ok(response: dict) -> dict:
    assert response["ok"] is True, response.get("error")
    return response["result"]


def _text_layer_markers(word: str) -> tuple[bytes, bytes]:
    """The two byte sequences a glyphless text layer for ``word`` must contain.

    Deliberately specific. ``Tj`` alone proves nothing — the fixture PDF this
    suite starts from already draws its own visible text with ``Tj``, so a
    substring check for it would pass on a file where OCR did nothing at all.

    What only the OCR writer emits is:

    * ``BT 3 Tr /OCR `` — a text object that immediately sets text render mode
      3 (fill none, stroke none: no glyph is ever rasterized) and selects the
      generated glyphless font resource. Nothing in the source document does
      this; a normal visible run leaves ``Tr`` at its default of 0.
    * the hex string for the recognized word, encoded as Identity-H CIDs
      through the font the writer generates for exactly these characters. This
      pins the *content*: the recognized text, not merely some text.
    """

    prefix = f"BT {INVISIBLE_RENDER_MODE} Tr /{DEFAULT_FONT_NAME} ".encode("ascii")
    shown = f"<{build_font([word]).encode(word).hex().upper()}> Tj".encode("ascii")
    return prefix, shown


def test_the_full_workflow_survives_a_round_trip(surface, write_pdf, tmp_path) -> None:
    source = write_pdf(["alpha", "beta", "gamma"], title="Acceptance")
    source_bytes = source.read_bytes()

    opened = _ok(
        surface(
            {
                "apiVersion": API_VERSION,
                "requestId": "1",
                "command": "open",
                "path": str(source),
            }
        )
    )
    session_id = opened["sessionId"]
    page_ids = [page["pageId"] for page in opened["document"]["pages"]]
    assert len(page_ids) == 3

    inspected = _ok(
        surface(
            {
                "apiVersion": API_VERSION,
                "requestId": "2",
                "command": "inspect",
                "sessionId": session_id,
            }
        )
    )
    assert inspected["state"] == "open"
    assert [page["pageId"] for page in inspected["document"]["pages"]] == page_ids

    capabilities = _ok(
        surface(
            {
                "apiVersion": API_VERSION,
                "requestId": "3",
                "command": "capabilities",
                "sessionId": session_id,
            }
        )
    )["capabilities"]
    assert capabilities["document"]["structuralEdit"]["state"] == "ready"

    before = _ok(
        surface(
            {
                "apiVersion": API_VERSION,
                "requestId": "4",
                "command": "render",
                "sessionId": session_id,
                "pageId": page_ids[0],
            }
        )
    )

    _ok(
        surface(
            {
                "apiVersion": API_VERSION,
                "requestId": "5",
                "command": "apply",
                "sessionId": session_id,
                "operations": [
                    {"kind": "rotate_pages", "pageIds": [page_ids[0]], "degrees": 90},
                    {
                        "kind": "crop_pages",
                        "pageIds": [page_ids[1]],
                        "box": [0, 0, 300, 400],
                    },
                    {"kind": "delete_pages", "pageIds": [page_ids[2]]},
                ],
            }
        )
    )

    after = _ok(
        surface(
            {
                "apiVersion": API_VERSION,
                "requestId": "6",
                "command": "render",
                "sessionId": session_id,
                "pageId": page_ids[0],
            }
        )
    )
    assert after["artifact"]["artifactId"] != before["artifact"]["artifactId"]

    _ok(
        surface(
            {
                "apiVersion": API_VERSION,
                "requestId": "7",
                "command": "apply",
                "sessionId": session_id,
                "operations": [
                    {"kind": "add_text_layer", "pageIds": [page_ids[0]], "dpi": 150}
                ],
            }
        )
    )

    target = tmp_path / "acceptance-out.pdf"
    saved = _ok(
        surface(
            {
                "apiVersion": API_VERSION,
                "requestId": "8",
                "command": "save",
                "sessionId": session_id,
                "path": str(target),
            }
        )
    )
    assert saved["artifact"]["kind"] == "saved_document"

    assert source.read_bytes() == source_bytes, "the source must never be mutated"

    reopened = _ok(
        surface(
            {
                "apiVersion": API_VERSION,
                "requestId": "9",
                "command": "open",
                "path": str(target),
            }
        )
    )
    pages = reopened["document"]["pages"]
    assert len(pages) == 2, "the deleted page must not come back"
    assert pages[0]["rotation"] == 90
    assert pages[1]["width"] == 300 and pages[1]["height"] == 400

    written = target.read_bytes()
    invisible_run, recognized = _text_layer_markers("hello")
    assert invisible_run in written, "the invisible OCR text object must reach the file"
    assert recognized in written, "the recognized word must reach the file"

    _ok(
        surface(
            {
                "apiVersion": API_VERSION,
                "requestId": "10",
                "command": "close",
                "sessionId": session_id,
            }
        )
    )
    _ok(
        surface(
            {
                "apiVersion": API_VERSION,
                "requestId": "11",
                "command": "close",
                "sessionId": reopened["sessionId"],
            }
        )
    )

    assert target.exists(), "closing must never delete a committed save"

    closed = surface(
        {
            "apiVersion": API_VERSION,
            "requestId": "12",
            "command": "inspect",
            "sessionId": session_id,
        }
    )
    assert closed["error"]["code"] == "session_invalid_state"
