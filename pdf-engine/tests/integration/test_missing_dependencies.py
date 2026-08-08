"""An absent dependency is covered behaviour, not an untested hole.

This runs whether or not Poppler and Tesseract exist. When they are missing the
real-backend tiers skip — but the promise that the engine *says so* is verified
right here, every time.
"""

from __future__ import annotations

from pdfengine import PdfEngine
from pdfengine.ocr.tesseract import TesseractOcr
from pdfengine.rendering.poppler import PopplerRenderer


def _tesseract_is_installed() -> bool:
    """Ask the adapter, not ``shutil.which``.

    ``which`` is the wrong question. Tesseract is routinely installed on
    Windows without being placed on PATH, and the adapter knows to look in the
    standard install locations. Gating on ``which`` would make this file assert
    "unavailable" on a machine where the engine correctly reports "ready" — a
    test failing over its own bad probe, on a working installation.
    """

    return TesseractOcr()._resolve_executable() is not None


def _poppler_is_installed() -> bool:
    """Ask the adapter, for the same reason."""

    return PopplerRenderer()._resolve_executable() is not None


def test_a_real_probe_never_raises_whatever_is_installed(tmp_path) -> None:
    """Probing for a binary that is not there must answer, not explode: the
    capabilities call is the one thing a client makes before it knows anything.
    """

    engine = PdfEngine(
        cache_root=tmp_path / "cache", renderer=PopplerRenderer(), ocr=TesseractOcr()
    )

    capabilities = engine.capabilities()

    assert capabilities["preview"]["state"] in {
        "ready",
        "blocked",
        "unavailable",
        "error",
    }
    assert capabilities["ocr"]["state"] in {"ready", "blocked", "unavailable", "error"}


def test_an_absent_binary_is_reported_as_unavailable_with_a_reason(tmp_path) -> None:
    """An unavailable capability that does not say why leaves a user with a
    greyed-out button and no idea what to install."""

    engine = PdfEngine(
        cache_root=tmp_path / "cache", renderer=PopplerRenderer(), ocr=TesseractOcr()
    )
    capabilities = engine.capabilities()
    text_layer = next(
        item for item in capabilities["operations"] if item["kind"] == "add_text_layer"
    )

    # Both branches assert. A test that only checks the absent case goes silent
    # on a developer machine that has the binaries, which is most of them.
    if not _tesseract_is_installed():
        assert capabilities["ocr"]["state"] == "unavailable"
        assert capabilities["ocr"]["detail"], "an unavailable capability must say why"
        assert capabilities["ocr"]["languages"] == []
        assert text_layer["state"] == "unavailable"
    else:
        assert capabilities["ocr"]["state"] == "ready"
        assert capabilities["ocr"]["languages"], "a ready recognizer must list a language"
        assert text_layer["state"] == "ready"

    if not _poppler_is_installed():
        assert capabilities["preview"]["state"] == "unavailable"
        assert capabilities["preview"]["detail"]
    else:
        assert capabilities["preview"]["state"] == "ready"
