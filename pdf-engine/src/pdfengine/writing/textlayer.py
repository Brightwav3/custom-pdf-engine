"""Build the invisible text layer that makes a scanned page searchable.

The layer is a content stream appended after the page's own content. It draws
nothing: every string is shown in render mode 3, so no glyph is ever
rasterized. What a viewer gets out of it is the ``/ToUnicode`` mapping (search
and copy) and the advance widths (the selection rectangle).

Two measured facts from ``docs/superpowers/specs`` shape this module:

* **A run's ``Tz`` is computed, not guessed.** The glyphless font has a uniform
  nominal advance, so a string's natural width has nothing to do with the
  scanned word underneath it. Setting the horizontal scale to
  ``100 * box_width / nominal_advance`` is what makes a viewer's selection
  highlight land on the ink.
* **Tesseract splits CJK runs into separate words.** ``日本語のテキスト`` comes
  back as ``日 本 語 の テキ スト``; written verbatim a search for ``日本語``
  would not match. Adjacent words are therefore joined with no separator when
  the characters on both sides of the join are CJK, and with a space otherwise.

Rotation is handled by rotating the text matrix rather than by swapping width
for height, matching what :func:`pdfengine.ocr.layout.place_words` promises:
its ``width`` and ``height`` are measured along the word's own reading
direction, in a page whose ``/Rotate`` has not yet been applied.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from pdfengine.ocr.layout import PlacedWord
from pdfengine.writing.glyphless import GlyphlessFont, build_font


DEFAULT_FONT_NAME = "OCR"
"""Preferred resource name. The writer picks another if the page uses it."""

INVISIBLE_RENDER_MODE = 3

_BASELINE_TOLERANCE = 0.5
"""Cross-axis drift, as a fraction of word height, still counted as one line."""

_GAP_TOLERANCE = 2.0
"""Largest gap between words, as a multiple of height, still counted as one run.

Wider than this and the words are more likely two columns than one phrase, and
merging them would put a single selection rectangle across the gutter.
"""

# Ranges whose characters are written without word spacing. Deliberately a
# script property rather than a language check: the recognizer is told a
# language, but a page may mix scripts, and the join rule follows the glyphs.
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x1100, 0x11FF),    # Hangul Jamo
    (0x2E80, 0x2EFF),    # CJK radicals supplement
    (0x3000, 0x303F),    # CJK symbols and punctuation
    (0x3040, 0x309F),    # Hiragana
    (0x30A0, 0x30FF),    # Katakana
    (0x3100, 0x312F),    # Bopomofo
    (0x3130, 0x318F),    # Hangul compatibility Jamo
    (0x31F0, 0x31FF),    # Katakana phonetic extensions
    (0x3400, 0x4DBF),    # CJK unified ideographs extension A
    (0x4E00, 0x9FFF),    # CJK unified ideographs
    (0xA960, 0xA97F),    # Hangul Jamo extended-A
    (0xAC00, 0xD7AF),    # Hangul syllables
    (0xD7B0, 0xD7FF),    # Hangul Jamo extended-B
    (0xF900, 0xFAFF),    # CJK compatibility ideographs
    (0xFF00, 0xFFEF),    # Halfwidth and fullwidth forms
    (0x20000, 0x2FA1F),  # CJK unified ideographs, supplementary planes
)

# The text matrix for each supported /Rotate, as ``a b c d``. The page stores
# unrotated coordinates while the rasterized image the recognizer saw was
# already turned, so the text has to be turned back to line up with the ink.
_MATRIX: dict[int, tuple[int, int, int, int]] = {
    0: (1, 0, 0, 1),
    90: (0, 1, -1, 0),
    180: (-1, 0, 0, -1),
    270: (0, -1, 1, 0),
}


@dataclass(frozen=True)
class TextLayer:
    """A ready-to-emit content stream plus the font it refers to."""

    content: bytes
    font: GlyphlessFont
    resource_name: str

    @property
    def is_empty(self) -> bool:
        return not self.content


def is_cjk(character: str) -> bool:
    """Whether ``character`` belongs to a script written without word spaces."""

    if not character:
        return False
    code = ord(character)
    return any(low <= code <= high for low, high in _CJK_RANGES)


def join_words(left: str, right: str) -> str:
    """Join two adjacent recognized words the way their scripts are written."""

    if not left:
        return right
    if not right:
        return left
    if is_cjk(left[-1]) and is_cjk(right[0]):
        return left + right
    return left + " " + right


def build_text_layer(
    words: Sequence[PlacedWord],
    rotation: int = 0,
    resource_name: str = DEFAULT_FONT_NAME,
    font: GlyphlessFont | None = None,
) -> TextLayer:
    """Turn placed words into an invisible content stream and its font."""

    turn = int(rotation) % 360
    if turn not in _MATRIX:
        raise ValueError(f"unsupported page rotation: {rotation}")

    runs = _runs(words, turn)
    if font is None:
        font = build_font(run.text for run in runs)

    lines: list[str] = []
    for run in runs:
        size = run.height
        if size <= 0:
            continue
        nominal = font.advance(run.text, size)
        # A zero or negative nominal advance would make Tz meaningless, and a
        # zero-width box would collapse the run onto a point. Either way there
        # is nothing sensible to scale, so the run is dropped rather than
        # emitted at a scale a viewer would have to guess about.
        if nominal <= 0 or run.width <= 0:
            continue
        scale = 100.0 * run.width / nominal
        x, y = _origin(run, turn)
        a, b, c, d = _MATRIX[turn]
        lines.append(
            f"BT {INVISIBLE_RENDER_MODE} Tr /{resource_name} {_number(size)} Tf "
            f"{_number(scale)} Tz "
            f"{a} {b} {c} {d} {_number(x)} {_number(y)} Tm "
            f"<{font.encode(run.text).hex().upper()}> Tj ET"
        )

    if not lines:
        return TextLayer(b"", font, resource_name)

    # The layer is wrapped in q/Q because Tf, Tz and Tr are text state, and
    # text state lives in the graphics state: it survives ET and would leak
    # into whatever a later content stream draws. The page's *original*
    # content needs no such wrapper, because this layer is only ever appended
    # after it — nothing emitted here can reach back and change how the
    # original was rendered.
    body = "q\n" + "\n".join(lines) + "\nQ\n"
    return TextLayer(body.encode("ascii"), font, resource_name)


def free_resource_name(taken: Iterable[str], preferred: str = DEFAULT_FONT_NAME) -> str:
    """A font resource name not already used by the page.

    Assuming ``/OCR`` is free would silently shadow a real font on any page
    that happens to use that name, so a free one is picked instead.
    """

    used = set(taken)
    if preferred not in used:
        return preferred
    counter = 1
    while f"{preferred}{counter}" in used:
        counter += 1
    return f"{preferred}{counter}"


# -- runs -----------------------------------------------------------------


@dataclass(frozen=True)
class _Run:
    """One string to show, in a reading frame where +along is the writing
    direction and +cross points from the baseline towards the ascenders."""

    text: str
    along: float
    width: float
    cross: float
    height: float


@dataclass(frozen=True)
class _Framed:
    word: PlacedWord
    along: float
    width: float
    cross_low: float
    cross_high: float


def _framed(word: PlacedWord, turn: int) -> _Framed:
    """Re-express a word in the reading frame for ``turn``.

    Doing this once up front means grouping, sizing and placement are all plain
    increasing-coordinate arithmetic, with the rotation handled in exactly two
    places: here, and :func:`_origin` on the way back out.
    """

    x, y, w, h = word.x, word.baseline, word.width, word.height
    if turn == 0:
        return _Framed(word, x, w, y, y + h)
    if turn == 90:
        return _Framed(word, y, w, -(x + h), -x)
    if turn == 180:
        return _Framed(word, -(x + w), w, -(y + h), -y)
    return _Framed(word, -(y + w), w, x, x + h)


def _origin(run: _Run, turn: int) -> tuple[float, float]:
    """Map a reading-frame origin back into PDF user space."""

    if turn == 0:
        return run.along, run.cross
    if turn == 90:
        return -run.cross, run.along
    if turn == 180:
        return -run.along, -run.cross
    return run.cross, -run.along


def _runs(words: Sequence[PlacedWord], turn: int) -> tuple[_Run, ...]:
    """Group words that read as one phrase, joining their text per script."""

    framed = [
        _framed(word, turn)
        for word in words
        if word.text and word.width > 0 and word.height > 0
    ]

    runs: list[_Run] = []
    group: list[_Framed] = []
    for item in framed:
        if group and _continues(group[-1], item):
            group.append(item)
            continue
        if group:
            runs.append(_collect(group))
        group = [item]
    if group:
        runs.append(_collect(group))
    return tuple(runs)


def _continues(previous: _Framed, current: _Framed) -> bool:
    """Whether ``current`` belongs to the same run as ``previous``."""

    height = max(previous.cross_high - previous.cross_low, 1e-6)
    if abs(current.cross_low - previous.cross_low) > _BASELINE_TOLERANCE * height:
        return False
    gap = current.along - (previous.along + previous.width)
    # A negative gap means the next word starts left of where this one ended:
    # a wrapped line, or a right-to-left script the recognizer reports in
    # reading order. Either way it is a new run.
    if gap < -0.25 * height:
        return False
    return gap <= _GAP_TOLERANCE * height


def _collect(group: Sequence[_Framed]) -> _Run:
    text = ""
    for item in group:
        text = join_words(text, item.word.text)
    first, last = group[0], group[-1]
    return _Run(
        text=text,
        along=first.along,
        # Measured start-to-end, so inter-word gaps are inside the run's width
        # and the space characters that stand in for them are scaled with it.
        width=(last.along + last.width) - first.along,
        cross=min(item.cross_low for item in group),
        height=max(item.cross_high - item.cross_low for item in group),
    )


def _number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")
