"""Visual checks that run only when a real Poppler renderer is installed."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from pdfengine import PdfEngine, RotatePages
from pdfengine.rendering.base import png_dimensions
from pdfengine.rendering.poppler import PopplerRenderer


poppler_missing = pytest.mark.skipif(
    shutil.which("pdftoppm") is None,
    reason="Poppler renderer (pdftoppm) is not installed",
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def engine(tmp_path):
    engine = PdfEngine(cache_root=tmp_path / "cache", renderer=PopplerRenderer())
    yield engine
    engine.close_all()


@poppler_missing
def test_a_preview_is_a_png_of_the_requested_width(engine, tmp_path) -> None:
    source = tmp_path / "one-page.pdf"
    shutil.copyfile(FIXTURES / "basic" / "one-page.pdf", source)
    session = engine.open_document(source)
    page_id = engine.inspect_document(session).pages[0].page_id

    image = engine.render_page(session, page_id, width=320)

    assert image.image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert png_dimensions(image.image_bytes)[0] == 320
    assert image.width == 320


@poppler_missing
def test_a_saved_rotation_still_renders(engine, tmp_path) -> None:
    source = tmp_path / "one-page.pdf"
    shutil.copyfile(FIXTURES / "basic" / "one-page.pdf", source)
    session = engine.open_document(source)
    page_id = engine.inspect_document(session).pages[0].page_id
    engine.apply_operations(session, [RotatePages((page_id,), 90)])

    output = engine.save(session, tmp_path / "rotated.pdf")

    reopened = engine.open_document(output)
    rotated_id = engine.inspect_document(reopened).pages[0].page_id
    image = engine.render_page(reopened, rotated_id, width=200)
    assert image.image_bytes.startswith(b"\x89PNG\r\n\x1a\n")


def test_the_renderer_capability_is_always_reportable(tmp_path) -> None:
    """Whether or not Poppler exists, asking must never raise."""

    capability = PdfEngine(cache_root=tmp_path / "c").renderer_capability()

    assert capability.state in {"ready", "blocked", "error"}
    if capability.state != "ready":
        assert capability.detail
