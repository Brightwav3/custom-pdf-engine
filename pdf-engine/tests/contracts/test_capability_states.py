"""The four capability states must mean exactly one thing each."""

from __future__ import annotations

from pdfengine import PdfEngine
from pdfengine.ocr.base import CAPABILITY_STATES
from support.fakes import DpiStubRenderer, StubOcr


def test_the_vocabulary_is_exactly_four_states() -> None:
    assert CAPABILITY_STATES == ("ready", "blocked", "unavailable", "error")


def test_a_missing_installation_is_unavailable_not_blocked(tmp_path) -> None:
    engine = PdfEngine(
        cache_root=tmp_path / "cache",
        renderer=DpiStubRenderer(),
        ocr=StubOcr(state="unavailable", detail="Tesseract executable not found"),
    )

    section = engine.capabilities()["ocr"]

    assert section["state"] == "unavailable"
    assert section["detail"]


def test_every_capability_entry_carries_a_reason_when_not_ready(tmp_path) -> None:
    engine = PdfEngine(
        cache_root=tmp_path / "cache",
        renderer=DpiStubRenderer(capability_state="unavailable"),
        ocr=StubOcr(state="unavailable", detail="not installed"),
    )

    capabilities = engine.capabilities()

    for name in ("preview", "ocr"):
        entry = capabilities[name]
        if entry["state"] != "ready":
            assert entry["detail"], f"{name} is {entry['state']} without a reason"
