"""The invisible OCR text layer, from placed words to a saved page."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pdfengine.api.models import AddTextLayer, SaveOptions
from pdfengine.document.pages import DocumentModel
from pdfengine.editing.state import DocumentState
from pdfengine.ocr.layout import PlacedWord
from pdfengine.ocr.models import OcrPage, OcrWord
from pdfengine.parser.reader import PdfReader
from pdfengine.parser.values import PdfArray, PdfDictionary, PdfName, PdfReference, PdfStream
from pdfengine.writing.glyphless import build_font
from pdfengine.writing.rewrite import FullRewriteWriter
from pdfengine.writing.textlayer import (
    build_text_layer,
    free_resource_name,
    is_cjk,
    join_words,
)

from conftest import assemble_pdf, build_pdf


# -- helpers --------------------------------------------------------------


def ocr_page(
    *words: tuple[str, tuple[float, float, float, float], float],
    width: int = 850,
    height: int = 1100,
    dpi: int = 100,
    language: str = "eng",
    mode: str = "lstm",
) -> OcrPage:
    """An OcrPage with no quiet zone, so pixel boxes read as written."""

    return OcrPage(
        words=tuple(
            OcrWord(text=text, box=box, confidence=confidence)
            for text, box, confidence in words
        ),
        width=width,
        height=height,
        dpi=dpi,
        language=language,
        mode=mode,
        padding=0,
    )


def save_with_layer(
    source: Path,
    target: Path,
    recognized: OcrPage,
    min_confidence: float = 0.0,
) -> Path:
    """Apply AddTextLayer to page one, attach ``recognized``, and save."""

    reader = PdfReader(source)
    model = DocumentModel.from_reader(reader)
    state = DocumentState.from_model(model)
    page_id = model.pages[0].id
    state = state.with_recognition(page_id, recognized)
    state = state.apply(
        AddTextLayer(
            page_ids=(page_id,),
            dpi=recognized.dpi,
            language=recognized.language,
            mode=recognized.mode,
            min_confidence=min_confidence,
        )
    )
    return FullRewriteWriter().write(
        state,
        {None: reader},
        target,
        SaveOptions(output_path=target, allow_replace_source=True),
    )


def content_streams(path: Path) -> list[bytes]:
    """Every content stream of page one, in the order a viewer concatenates."""

    reader = PdfReader(path)
    model = DocumentModel.from_reader(reader)
    page = reader.resolve(model.pages[0].reference)
    contents = page.entries[PdfName("Contents")]
    if isinstance(contents, PdfReference):
        contents = reader.resolve(contents)
    items = contents.items if isinstance(contents, PdfArray) else (contents,)

    streams: list[bytes] = []
    for item in items:
        value = reader.resolve(item) if isinstance(item, PdfReference) else item
        assert isinstance(value, PdfStream)
        streams.append(value.data)
    return streams


def layer_stream(path: Path) -> str:
    """The appended OCR stream, decoded. It is always the last one."""

    return content_streams(path)[-1].decode("ascii")


def page_fonts(path: Path) -> dict[str, object]:
    reader = PdfReader(path)
    model = DocumentModel.from_reader(reader)
    resources = model.pages[0].resources
    assert resources is not None
    fonts = resources.entries.get(PdfName("Font"))
    if isinstance(fonts, PdfReference):
        fonts = reader.resolve(fonts)
    if not isinstance(fonts, PdfDictionary):
        return {}
    return {name.value: value for name, value in fonts.entries.items()}


SHOW = re.compile(
    r"BT (\d) Tr /(\S+) ([\d.]+) Tf ([\d.]+) Tz "
    r"(-?\d+) (-?\d+) (-?\d+) (-?\d+) ([-\d.]+) ([-\d.]+) Tm <([0-9A-F]*)> Tj ET"
)


def shows(stream: str) -> list[re.Match]:
    return list(SHOW.finditer(stream))


# -- the emitted stream ---------------------------------------------------


def test_a_saved_page_carries_invisible_text_with_the_expected_cids(
    tmp_path, write_pdf
) -> None:
    source = write_pdf(["one page"])
    recognized = ocr_page(("hello", (100.0, 200.0, 200.0, 230.0), 92.0))

    saved = save_with_layer(source, tmp_path / "out.pdf", recognized)
    stream = layer_stream(saved)

    assert "3 Tr" in stream
    # Render mode 3 is the whole point: nothing may become visible.
    assert " Tr" not in stream.replace("3 Tr", "")

    match = shows(stream)[0]
    font = build_font(["hello"])
    assert match.group(11) == font.encode("hello").hex().upper()


def test_the_original_content_survives_intact_and_comes_first(
    tmp_path, write_pdf
) -> None:
    source = write_pdf(["one page"])
    original = content_streams(source)
    recognized = ocr_page(("hello", (100.0, 200.0, 200.0, 230.0), 92.0))

    saved = save_with_layer(source, tmp_path / "out.pdf", recognized)
    streams = content_streams(saved)

    assert len(streams) == len(original) + 1
    assert streams[:-1] == original
    assert b"(one page) Tj" in streams[0]
    assert b"3 Tr" not in streams[0]


def test_the_layer_is_wrapped_so_its_text_state_cannot_leak(
    tmp_path, write_pdf
) -> None:
    source = write_pdf(["one page"])
    recognized = ocr_page(("hello", (100.0, 200.0, 200.0, 230.0), 92.0))

    stream = layer_stream(save_with_layer(source, tmp_path / "out.pdf", recognized))

    # Tf, Tz and Tr are text state, and text state is graphics state: without
    # q/Q the invisible render mode would survive into anything drawn later.
    assert stream.startswith("q\n")
    assert stream.rstrip().endswith("Q")


# -- horizontal scaling ---------------------------------------------------


def test_tz_makes_the_glyph_advance_match_the_box_width() -> None:
    word = PlacedWord(
        text="hello", x=72.0, baseline=700.0, width=48.0, height=12.0, confidence=90.0
    )

    layer = build_text_layer([word])
    match = shows(layer.content.decode("ascii"))[0]

    size = float(match.group(3))
    scale = float(match.group(4))
    advance = layer.font.advance("hello", size) * scale / 100.0
    assert advance == pytest.approx(48.0, abs=1e-6)


def test_a_word_whose_advance_would_be_zero_is_dropped() -> None:
    # A zero-width box leaves nothing meaningful to scale to.
    zero = PlacedWord(
        text="x", x=10.0, baseline=10.0, width=0.0, height=12.0, confidence=90.0
    )
    tall = PlacedWord(
        text="y", x=10.0, baseline=90.0, width=20.0, height=0.0, confidence=90.0
    )

    assert build_text_layer([zero, tall]).is_empty


def test_the_text_matrix_turns_with_the_page() -> None:
    word = PlacedWord(
        text="a", x=72.0, baseline=700.0, width=48.0, height=12.0, confidence=90.0
    )

    matrices = {}
    for rotation in (0, 90, 180, 270):
        match = shows(
            build_text_layer([word], rotation=rotation).content.decode("ascii")
        )[0]
        matrices[rotation] = tuple(int(match.group(index)) for index in range(5, 9))

    assert matrices == {
        0: (1, 0, 0, 1),
        90: (0, 1, -1, 0),
        180: (-1, 0, 0, -1),
        270: (0, -1, 1, 0),
    }


def test_an_unsupported_rotation_is_rejected() -> None:
    with pytest.raises(ValueError, match="rotation"):
        build_text_layer([], rotation=45)


# -- CJK joining ----------------------------------------------------------


def test_cjk_words_join_without_a_space() -> None:
    # Tesseract returns 日本語のテキスト as separate words; written verbatim a
    # search for 日本語 would not match.
    pieces = ["日", "本", "語", "の", "テキ", "スト"]
    words = [
        PlacedWord(
            text=piece,
            x=72.0 + index * 12.0,
            baseline=700.0,
            width=12.0,
            height=12.0,
            confidence=90.0,
        )
        for index, piece in enumerate(pieces)
    ]

    layer = build_text_layer(words)
    match = shows(layer.content.decode("ascii"))[0]

    shown = _decode(match.group(11), layer.font)
    assert shown == "日本語のテキスト"
    assert " " not in shown


def test_latin_words_keep_their_space() -> None:
    words = [
        PlacedWord(
            text="one", x=72.0, baseline=700.0, width=20.0, height=12.0, confidence=90.0
        ),
        PlacedWord(
            text="page", x=96.0, baseline=700.0, width=26.0, height=12.0, confidence=90.0
        ),
    ]

    layer = build_text_layer(words)
    match = shows(layer.content.decode("ascii"))[0]

    assert _decode(match.group(11), layer.font) == "one page"


def test_a_latin_cjk_boundary_keeps_its_space() -> None:
    assert join_words("ISO", "日本") == "ISO 日本"
    assert join_words("日本", "ISO") == "日本 ISO"
    assert join_words("日本", "語") == "日本語"


@pytest.mark.parametrize(
    "character",
    ["日", "語", "の", "テ", "한", "글", "中", "文", "㐀"],
)
def test_scripts_written_without_spaces_are_recognized_as_such(character) -> None:
    assert is_cjk(character)


@pytest.mark.parametrize("character", ["a", "Z", "ř", "1", " ", "م", "П"])
def test_spaced_scripts_are_not_treated_as_cjk(character) -> None:
    assert not is_cjk(character)


def test_words_on_different_lines_are_not_joined() -> None:
    words = [
        PlacedWord(
            text="日", x=72.0, baseline=700.0, width=12.0, height=12.0, confidence=90.0
        ),
        PlacedWord(
            text="本", x=72.0, baseline=600.0, width=12.0, height=12.0, confidence=90.0
        ),
    ]

    assert len(shows(build_text_layer(words).content.decode("ascii"))) == 2


# -- resources ------------------------------------------------------------


def test_a_free_resource_name_is_chosen_when_ocr_is_taken() -> None:
    assert free_resource_name([]) == "OCR"
    assert free_resource_name(["F1"]) == "OCR"
    assert free_resource_name(["OCR"]) == "OCR1"
    assert free_resource_name(["OCR", "OCR1", "OCR2"]) == "OCR3"


def test_a_resource_name_collision_picks_a_different_name(tmp_path) -> None:
    source = tmp_path / "collide.pdf"
    source.write_bytes(_pdf_with_font_named("OCR"))
    recognized = ocr_page(("hello", (100.0, 200.0, 200.0, 230.0), 92.0))

    saved = save_with_layer(source, tmp_path / "out.pdf", recognized)

    fonts = page_fonts(saved)
    assert "OCR" in fonts and "OCR1" in fonts
    # The page's own font must still be the one it was, not the glyphless one.
    assert f"/{'OCR1'} " in layer_stream(saved)
    assert "/OCR " not in layer_stream(saved)


def test_the_glyphless_font_objects_are_copied_into_the_output(
    tmp_path, write_pdf
) -> None:
    source = write_pdf(["one page"])
    recognized = ocr_page(("hello", (100.0, 200.0, 200.0, 230.0), 92.0))

    saved = save_with_layer(source, tmp_path / "out.pdf", recognized)

    reader = PdfReader(saved)
    font = reader.resolve(page_fonts(saved)["OCR"])
    assert font.entries[PdfName("Subtype")] == PdfName("Type0")
    assert font.entries[PdfName("Encoding")] == PdfName("Identity-H")

    to_unicode = reader.resolve(font.entries[PdfName("ToUnicode")])
    assert b"beginbfchar" in to_unicode.data


# -- contents shapes ------------------------------------------------------


def test_a_page_with_a_contents_array_gets_the_layer_appended(tmp_path) -> None:
    source = tmp_path / "array.pdf"
    source.write_bytes(_pdf_with_contents_array())
    recognized = ocr_page(("hello", (100.0, 200.0, 200.0, 230.0), 92.0))

    saved = save_with_layer(source, tmp_path / "out.pdf", recognized)
    streams = content_streams(saved)

    assert len(streams) == 3
    assert b"(first half)" in streams[0]
    assert b"(second half)" in streams[1]
    assert b"3 Tr" in streams[2]


def test_a_page_whose_contents_is_a_reference_to_an_array_stays_flat(
    tmp_path,
) -> None:
    source = tmp_path / "indirect.pdf"
    source.write_bytes(_pdf_with_contents_array(indirect=True))
    recognized = ocr_page(("hello", (100.0, 200.0, 200.0, 230.0), 92.0))

    saved = save_with_layer(source, tmp_path / "out.pdf", recognized)

    # An array holding a reference to another array is not a valid content
    # list, so the shapes have to be flattened rather than nested.
    streams = content_streams(saved)
    assert len(streams) == 3
    assert b"3 Tr" in streams[2]


# -- confidence -----------------------------------------------------------


def test_min_confidence_filters_low_confidence_words_out_of_the_layer(
    tmp_path, write_pdf
) -> None:
    source = write_pdf(["one page"])
    recognized = ocr_page(
        ("sure", (100.0, 200.0, 180.0, 230.0), 95.0),
        ("guess", (100.0, 300.0, 180.0, 330.0), 12.0),
    )

    saved = save_with_layer(source, tmp_path / "out.pdf", recognized, min_confidence=50.0)
    layer = layer_stream(saved)
    font = build_font(["sure", "guess"])

    assert len(shows(layer)) == 1
    assert font.encode("sure").hex().upper() in layer
    assert font.encode("guess").hex().upper() not in layer


def test_filtering_every_word_away_leaves_the_page_untouched(
    tmp_path, write_pdf
) -> None:
    source = write_pdf(["one page"])
    recognized = ocr_page(("guess", (100.0, 200.0, 180.0, 230.0), 12.0))

    saved = save_with_layer(source, tmp_path / "out.pdf", recognized, min_confidence=90.0)

    assert content_streams(saved) == content_streams(source)
    assert "OCR" not in page_fonts(saved)


def test_a_pending_request_writes_no_layer_at_all(tmp_path, write_pdf) -> None:
    """A page whose recognition never ran must still save, unchanged."""

    source = write_pdf(["one page"])
    reader = PdfReader(source)
    model = DocumentModel.from_reader(reader)
    state = DocumentState.from_model(model)
    state = state.apply(AddTextLayer(page_ids=(model.pages[0].id,)))

    target = tmp_path / "out.pdf"
    FullRewriteWriter().write(
        state, {None: reader}, target, SaveOptions(output_path=target)
    )

    assert content_streams(target) == content_streams(source)


# -- fixtures -------------------------------------------------------------


def _decode(hex_text: str, font) -> str:
    reverse = {cid: character for character, cid in font.cids.items()}
    raw = bytes.fromhex(hex_text)
    return "".join(
        reverse[int.from_bytes(raw[index : index + 2], "big")]
        for index in range(0, len(raw), 2)
    )


def _pdf_with_font_named(name: str) -> bytes:
    content = b"BT /" + name.encode() + b" 12 Tf 72 720 Td (hi) Tj ET"
    return assemble_pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 612 792] >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            f"<< /Type /Page /Parent 2 0 R /Resources << /Font << /{name} 3 0 R >> >>"
            " /Contents 5 0 R >>".encode(),
            f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream",
        ]
    )


def _pdf_with_contents_array(indirect: bool = False) -> bytes:
    first = b"BT /F1 12 Tf 72 720 Td (first half) Tj ET"
    second = b"BT /F1 12 Tf 72 700 Td (second half) Tj ET"
    contents = b"[5 0 R 6 0 R]" if not indirect else b"7 0 R"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 612 792] >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 3 0 R >> >>"
        b" /Contents " + contents + b" >>",
        f"<< /Length {len(first)} >>\nstream\n".encode() + first + b"\nendstream",
        f"<< /Length {len(second)} >>\nstream\n".encode() + second + b"\nendstream",
    ]
    if indirect:
        objects.append(b"[5 0 R 6 0 R]")
    return assemble_pdf(objects)
