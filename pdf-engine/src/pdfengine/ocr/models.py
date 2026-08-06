"""What a recognizer returns, independent of which recognizer produced it.

Boxes are in **image pixels with the origin at the top left**, which is what
every OCR engine reports. Converting to PDF user space is the job of
``ocr.layout``, deliberately kept out of the models so the transform can be
tested against hand-computed expectations without involving an OCR engine.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OcrChar:
    """One recognized character. Only the legacy engine reports these."""

    text: str
    box: tuple[float, float, float, float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "box", _valid_box(self.box, "character"))


@dataclass(frozen=True)
class OcrWord:
    """One recognized word, with where it sits and how sure the engine is."""

    text: str
    box: tuple[float, float, float, float]
    confidence: float
    block: int = 0
    line: int = 0
    characters: tuple[OcrChar, ...] = ()

    def __post_init__(self) -> None:
        if not self.text:
            raise ValueError("an OCR word must carry text")
        object.__setattr__(self, "box", _valid_box(self.box, "word"))
        object.__setattr__(self, "characters", tuple(self.characters))

    @property
    def width(self) -> float:
        return self.box[2] - self.box[0]

    @property
    def height(self) -> float:
        return self.box[3] - self.box[1]


@dataclass(frozen=True)
class OcrPage:
    """Everything recognized on one rasterized page.

    ``padding`` records the white quiet zone added around the page before
    recognition. Tesseract returns nothing at all for text touching the image
    border, so the padding is not optional — and every box it reports is offset
    by it, which the layout transform must undo.
    """

    words: tuple[OcrWord, ...]
    width: int
    height: int
    dpi: int
    language: str
    mode: str
    padding: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "words", tuple(self.words))
        if self.width <= 0 or self.height <= 0:
            raise ValueError("page pixel dimensions must be positive")
        if self.dpi <= 0:
            raise ValueError("dpi must be positive")
        if self.padding < 0:
            raise ValueError("padding must not be negative")

    @property
    def text(self) -> str:
        """The recognized text, one line per detected line."""

        lines: dict[tuple[int, int], list[str]] = {}
        for word in self.words:
            lines.setdefault((word.block, word.line), []).append(word.text)
        return "\n".join(" ".join(words) for words in lines.values())

    def above_confidence(self, minimum: float) -> "OcrPage":
        from dataclasses import replace

        return replace(
            self, words=tuple(w for w in self.words if w.confidence >= minimum)
        )


def _valid_box(
    box: tuple[float, float, float, float], label: str
) -> tuple[float, float, float, float]:
    values = tuple(float(value) for value in box)
    if len(values) != 4:
        raise ValueError(f"{label} box must contain four numbers")
    if values[2] <= values[0] or values[3] <= values[1]:
        raise ValueError(f"{label} box must be non-empty")
    return values
