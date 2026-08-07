"""Fake backends shared by every test tier.

These exist so contract and parity tests can run on a machine with neither
Poppler nor Tesseract installed. Real-backend behaviour is verified in
tests/integration/real_poppler/ and tests/integration/real_tesseract/.
"""

from __future__ import annotations

from pathlib import Path

from conftest import make_png


class StubRenderer:
    version = "stub-1"

    def __init__(self, capability_state: str = "ready") -> None:
        self.capability_state = capability_state
        self.calls: list[tuple[Path, int, int, str | None]] = []

    def capability(self):
        from pdfengine.rendering.base import RendererCapability

        return RendererCapability(self.capability_state, "stub")

    def render(self, source, page_index, width, password, output_dir) -> bytes:
        self.calls.append((Path(source), page_index, width, password))
        return make_png(width, width * 2)


class GeometryRenderer:
    """Render a PNG whose pixel size mirrors the real geometry of the page.

    Unlike ``StubRenderer`` this reads the file it is handed, so a preview that
    was produced from the wrong file is visible in the pixels.
    """

    version = "geometry-1"

    def render(self, source, page_index, width, password, output_dir) -> bytes:
        from pdfengine.document.pages import DocumentModel
        from pdfengine.parser.reader import PdfReader

        page = DocumentModel.from_reader(PdfReader(Path(source))).pages[page_index]
        box = page.crop_box or page.media_box
        page_width = int(round(box[2] - box[0]))
        page_height = int(round(box[3] - box[1]))
        if page.info.rotation in (90, 270):
            page_width, page_height = page_height, page_width
        return make_png(page_width, page_height)


class StubOcr:
    """A recognizer that returns a fixed answer without touching Tesseract."""

    version = "stub-ocr-1"

    def __init__(
        self,
        words=(("hello", (100.0, 200.0, 260.0, 240.0), 91.0),),
        state: str = "ready",
        detail: str = "stub",
    ) -> None:
        self._words = tuple(words)
        self.state = state
        self.detail = detail
        self.calls: list[tuple[int, str, str]] = []

    def capability(self, language: str = "eng", mode: str = "lstm"):
        from pdfengine.ocr.base import OcrCapability

        return OcrCapability(
            self.state,
            self.detail,
            engine="stub 0.0",
            modes=("lstm",) if self.state == "ready" else (),
            languages=("eng",) if self.state == "ready" else (),
        )

    def languages(self) -> tuple[str, ...]:
        return ("eng",)

    def recognize(self, image, dpi=300, language="eng", mode="lstm", psm=3):
        from pdfengine.ocr.models import OcrPage, OcrWord

        self.calls.append((dpi, language, mode))
        return OcrPage(
            words=tuple(
                OcrWord(text=text, box=box, confidence=confidence)
                for text, box, confidence in self._words
            ),
            width=850,
            height=1100,
            dpi=dpi,
            language=language,
            mode=mode,
            padding=0,
        )


class DpiStubRenderer(StubRenderer):
    """A StubRenderer that also satisfies the DpiRenderer protocol."""

    version = "stub-dpi-1"

    def __init__(self, capability_state: str = "ready") -> None:
        super().__init__(capability_state)
        self.dpi_calls: list[tuple[int, int]] = []

    def render_at_dpi(self, source, page_index, dpi, password, output_dir) -> bytes:
        self.dpi_calls.append((page_index, dpi))
        return make_png(40, 50)
