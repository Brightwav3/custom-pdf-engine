"""Verifies the real Poppler adapter, not the contract around it.

The contract is proved in tests/contracts over stubs, on any machine. This tier
proves the one thing a stub cannot: that the arguments handed to a real
``pdftoppm`` mean what the adapter thinks they mean.
"""

from __future__ import annotations

import pytest

from pdfengine import PdfEngine
from pdfengine.rendering.base import png_dimensions
from pdfengine.rendering.poppler import PopplerRenderer

# Gated on the adapter's own discovery, not ``shutil.which``: the adapter knows
# where to look, and the gate must agree with what the engine will actually do.
pytestmark = pytest.mark.skipif(
    PopplerRenderer()._resolve_executable() is None,
    reason="needs a real Poppler installation",
)


@pytest.mark.requires_preview
def test_a_real_render_produces_a_png_of_the_requested_width(
    tmp_path, write_pdf
) -> None:
    engine = PdfEngine(cache_root=tmp_path / "cache", renderer=PopplerRenderer())
    try:
        session = engine.open_document(write_pdf(["real page"]))
        page_id = engine.inspect_document(session).pages[0].page_id

        result = engine.render_page(session, page_id, width=400)

        assert png_dimensions(result.image_bytes)[0] == 400
        assert engine.capabilities()["preview"]["state"] == "ready"
    finally:
        engine.close_all()
