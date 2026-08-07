"""A glyphless CIDFontType2 for invisible OCR text.

Invisible text is drawn in render mode 3: no glyph is ever rasterized. What a
viewer actually uses is the ``/ToUnicode`` CMap (for copy and search) and the
advance widths (for the selection rectangle). Embedding and subsetting a real
Unicode font would therefore buy nothing but megabytes and a licensing
question.

So this module builds a font with no glyph outlines at all: ``Identity-H``
encoding, one CID per distinct character, a uniform advance from ``/DW``, and a
generated ``/ToUnicode`` CMap mapping every CID back to its true codepoint.
Roughly a kilobyte, no subsetting, and full Unicode coverage — including Czech,
which Latin-1 cannot represent, and CJK beyond the BMP.

No font program is embedded. A non-embedded ``CIDFontType2`` with a symbolic
``/FontDescriptor`` and an ``Adobe-Identity-0`` ``/CIDSystemInfo`` is legal, and
since nothing is ever drawn there is no substitution to go wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from pdfengine.parser.values import PdfDictionary, PdfName, PdfReference, PdfStream, PdfString

BASE_FONT = "GlyphLessFont"
DEFAULT_WIDTH = 500
"""Uniform advance in glyph space units (1/1000 em), emitted as ``/DW``.

The exact value does not matter: the text layer sets a ``Tz`` horizontal scale
computed from this nominal advance so the string ends up exactly as wide as the
OCR box. A uniform width keeps ``/W`` empty and the font tiny.
"""

BFCHAR_CHUNK = 100
"""Maximum ``bfchar`` entries per block; the CMap spec's limit."""

_FIRST_CID = 1
"""CID 0 is ``.notdef`` and is never assigned to real text."""


@dataclass(frozen=True)
class GlyphlessFont:
    """A built font: CID assignments, encoding, and the objects to emit."""

    cids: dict[str, int]
    width: int = DEFAULT_WIDTH

    def encode(self, text: str) -> bytes:
        """``text`` as Identity-H two-byte CIDs, ready for a PDF hex string."""

        out = bytearray()
        for character in text:
            cid = self.cids.get(character)
            if cid is None:
                raise KeyError(f"character {character!r} is not in this font")
            out.extend(cid.to_bytes(2, "big"))
        return bytes(out)

    def advance(self, text: str, font_size: float) -> float:
        """Nominal width of ``text``, used to compute the ``Tz`` scale."""

        return len(text) * self.width / 1000.0 * font_size

    def to_unicode_cmap(self) -> bytes:
        """The ``/ToUnicode`` CMap body."""

        return _cmap_bytes(self.cids)

    def objects(self, first_number: int) -> dict[int, object]:
        """The objects to emit, numbered from ``first_number``.

        ``first_number`` is the Type0 font itself — the object a page's
        ``/Resources /Font`` entry points at. The rest follow it in order:
        descendant CIDFontType2, font descriptor, ``ToUnicode`` stream.
        """

        font = first_number
        descendant = first_number + 1
        descriptor = first_number + 2
        to_unicode = first_number + 3
        cmap = self.to_unicode_cmap()
        return {
            font: PdfDictionary(
                {
                    PdfName("Type"): PdfName("Font"),
                    PdfName("Subtype"): PdfName("Type0"),
                    PdfName("BaseFont"): PdfName(BASE_FONT),
                    PdfName("Encoding"): PdfName("Identity-H"),
                    PdfName("DescendantFonts"): _array(PdfReference(descendant, 0)),
                    PdfName("ToUnicode"): PdfReference(to_unicode, 0),
                }
            ),
            descendant: PdfDictionary(
                {
                    PdfName("Type"): PdfName("Font"),
                    PdfName("Subtype"): PdfName("CIDFontType2"),
                    PdfName("BaseFont"): PdfName(BASE_FONT),
                    PdfName("CIDSystemInfo"): PdfDictionary(
                        {
                            PdfName("Registry"): PdfString(b"Adobe"),
                            PdfName("Ordering"): PdfString(b"Identity"),
                            PdfName("Supplement"): 0,
                        }
                    ),
                    PdfName("FontDescriptor"): PdfReference(descriptor, 0),
                    PdfName("DW"): self.width,
                    PdfName("CIDToGIDMap"): PdfName("Identity"),
                }
            ),
            descriptor: PdfDictionary(
                {
                    PdfName("Type"): PdfName("FontDescriptor"),
                    PdfName("FontName"): PdfName(BASE_FONT),
                    # 4 = symbolic: the font has no standard encoding to honour.
                    PdfName("Flags"): 4,
                    PdfName("FontBBox"): _array(0, 0, 0, 0),
                    PdfName("ItalicAngle"): 0,
                    PdfName("Ascent"): 1000,
                    PdfName("Descent"): -200,
                    PdfName("CapHeight"): 700,
                    PdfName("StemV"): 80,
                }
            ),
            to_unicode: PdfStream(
                PdfDictionary({PdfName("Length"): len(cmap)}), cmap
            ),
        }


def build_font(characters: Iterable[str]) -> GlyphlessFont:
    """Assign a CID to every distinct character in ``characters``.

    ``characters`` may be an iterable of single characters or of whole strings;
    both are flattened. CIDs are assigned sequentially in order of first
    appearance, starting at 1 so ``.notdef`` keeps CID 0. A codepoint above
    U+FFFF is a single CID like any other — only its ``ToUnicode`` value needs
    a surrogate pair.
    """

    cids: dict[str, int] = {}
    for chunk in characters:
        for character in chunk:
            if character not in cids:
                cids[character] = _FIRST_CID + len(cids)
    return GlyphlessFont(cids=cids)


def _cmap_bytes(cids: dict[str, int]) -> bytes:
    entries = sorted(cids.items(), key=lambda item: item[1])
    lines = [
        b"/CIDInit /ProcSet findresource begin",
        b"12 dict begin",
        b"begincmap",
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        b"/CMapName /Adobe-Identity-UCS def",
        b"/CMapType 2 def",
        b"1 begincodespacerange",
        b"<0000> <FFFF>",
        b"endcodespacerange",
    ]
    for start in range(0, len(entries), BFCHAR_CHUNK):
        block = entries[start : start + BFCHAR_CHUNK]
        lines.append(f"{len(block)} beginbfchar".encode("ascii"))
        for character, cid in block:
            lines.append(
                b"<"
                + f"{cid:04X}".encode("ascii")
                + b"> <"
                + _utf16be_hex(character)
                + b">"
            )
        lines.append(b"endbfchar")
    lines.extend(
        [
            b"endcmap",
            b"CMapName currentdict /CMap defineresource pop",
            b"end",
            b"end",
        ]
    )
    return b"\n".join(lines) + b"\n"


def _utf16be_hex(character: str) -> bytes:
    """UTF-16BE hex for one character — a surrogate pair above the BMP."""

    return character.encode("utf-16-be").hex().upper().encode("ascii")


def _array(*items: object):
    from pdfengine.parser.values import PdfArray

    return PdfArray(tuple(items))
