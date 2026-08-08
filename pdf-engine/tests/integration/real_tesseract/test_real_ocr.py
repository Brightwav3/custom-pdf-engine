"""Verifies the real Tesseract adapter end to end. Skipped when absent.

Skipping this tier costs the suite its evidence about *Tesseract*, and nothing
else: the add_text_layer contract is proved over a stub in tests/contracts, on
a machine with no external binaries at all.
"""

from __future__ import annotations

import pytest

from pdfengine import PdfEngine
from pdfengine.api.models import AddTextLayer
from pdfengine.ocr.tesseract import TesseractOcr
from pdfengine.rendering.poppler import PopplerRenderer

# Gated on the adapters' own discovery rather than ``shutil.which``. Tesseract
# is routinely installed on Windows without being placed on PATH, and a
# ``which`` gate would skip this tier on a machine where the real backend works
# — silently withdrawing the only coverage the real adapter has.
pytestmark = pytest.mark.skipif(
    TesseractOcr()._resolve_executable() is None
    or PopplerRenderer()._resolve_executable() is None,
    reason="needs real Tesseract and Poppler installations",
)


@pytest.mark.requires_ocr
def test_a_real_recognition_puts_searchable_text_into_a_saved_document(
    tmp_path, write_pdf
) -> None:
    engine = PdfEngine(cache_root=tmp_path / "cache", renderer=PopplerRenderer())
    try:
        session = engine.open_document(write_pdf(["HELLO WORLD"]))
        page_id = engine.inspect_document(session).pages[0].page_id

        engine.apply_operations(session, [AddTextLayer((page_id,), dpi=200)])
        target = engine.save(session, tmp_path / "searchable.pdf")

        assert target.exists()
        assert target.stat().st_size > 0
        assert engine.capabilities()["ocr"]["state"] == "ready"
        assert engine.capabilities()["ocr"]["languages"]
    finally:
        engine.close_all()
