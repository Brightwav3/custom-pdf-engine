from __future__ import annotations

import pytest

from pdfengine.ocr import OcrCapability, OcrChar, OcrPage, OcrWord


def word(text: str, box=(0, 0, 10, 10), **kwargs) -> OcrWord:
    return OcrWord(text=text, box=box, confidence=kwargs.pop("confidence", 90.0), **kwargs)


def test_a_word_exposes_its_pixel_geometry() -> None:
    found = word("hello", box=(10, 20, 60, 45))

    assert (found.width, found.height) == (50.0, 25.0)
    assert found.characters == ()


def test_text_groups_words_into_the_lines_they_came_from() -> None:
    page = OcrPage(
        words=(
            word("příliš", block=1, line=1),
            word("žluťoučký", block=1, line=1),
            word("kůň", block=1, line=2),
        ),
        width=100,
        height=100,
        dpi=300,
        language="ces",
        mode="lstm",
    )

    assert page.text == "příliš žluťoučký\nkůň"


def test_low_confidence_words_can_be_filtered_out() -> None:
    page = OcrPage(
        words=(word("sure", confidence=95.0), word("guess", confidence=12.0)),
        width=10,
        height=10,
        dpi=300,
        language="eng",
        mode="lstm",
    )

    kept = page.above_confidence(50.0)

    assert [w.text for w in kept.words] == ["sure"]
    assert [w.text for w in page.words] == ["sure", "guess"]


def test_padding_is_recorded_so_the_transform_can_undo_it() -> None:
    page = OcrPage(
        words=(), width=10, height=10, dpi=300, language="eng", mode="lstm", padding=40
    )

    assert page.padding == 40


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda: word("", box=(0, 0, 1, 1)), "must carry text"),
        (lambda: word("x", box=(5, 0, 5, 10)), "non-empty"),
        (lambda: word("x", box=(0, 5, 10, 5)), "non-empty"),
        (lambda: OcrChar(text="x", box=(9, 0, 1, 10)), "non-empty"),
        (
            lambda: OcrPage(words=(), width=0, height=1, dpi=300, language="e", mode="lstm"),
            "pixel dimensions",
        ),
        (
            lambda: OcrPage(words=(), width=1, height=1, dpi=0, language="e", mode="lstm"),
            "dpi must be positive",
        ),
        (
            lambda: OcrPage(
                words=(), width=1, height=1, dpi=1, language="e", mode="lstm", padding=-1
            ),
            "must not be negative",
        ),
    ],
)
def test_invalid_recognition_results_are_rejected(build, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        build()


def test_capability_serializes_for_the_json_contract() -> None:
    capability = OcrCapability(
        state="ready", engine="tesseract 5.4.0", modes=("lstm",), languages=("ces", "eng")
    )

    assert capability.ready is True
    assert capability.as_dict() == {
        "state": "ready",
        "detail": "",
        "engine": "tesseract 5.4.0",
        "modes": ["lstm"],
        "languages": ["ces", "eng"],
    }


def test_a_blocked_capability_is_not_ready() -> None:
    assert OcrCapability("blocked", detail="not installed").ready is False
