import pytest

from pdfengine.errors import PdfParseError
from pdfengine.parser.tokens import Tokenizer
from pdfengine.parser.values import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfReference,
    PdfString,
)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (b"/Type", PdfName("Type")),
        (b"/A#20Name", PdfName("A Name")),
        (b"42", 42),
        (b"-3.5", -3.5),
        (b"true", True),
        (b"false", False),
        (b"null", None),
    ],
)
def test_read_value_parses_pdf_atom_types(source: bytes, expected: object) -> None:
    assert Tokenizer(source).read_value() == expected


def test_read_value_skips_comments_and_whitespace() -> None:
    assert Tokenizer(b" \t% a comment\r\n /Answer").read_value() == PdfName("Answer")


def test_read_value_decodes_literal_string_escapes_and_balanced_parentheses() -> None:
    value = Tokenizer(b"(A\\(B\\)\\\\\\101 (nested))").read_value()

    assert value == PdfString(b"A(B)\\A (nested)")


def test_read_value_decodes_hex_string() -> None:
    assert Tokenizer(b"<4869F>").read_value() == PdfString(b"Hi\xf0")


def test_read_value_parses_array_dictionary_and_indirect_reference() -> None:
    value = Tokenizer(b"[ /Kid 12 0 R << /Count 2 /Text (hi) >> ]").read_value()

    assert value == PdfArray(
        (
            PdfName("Kid"),
            PdfReference(12, 0),
            PdfDictionary(
                {PdfName("Count"): 2, PdfName("Text"): PdfString(b"hi")}
            ),
        )
    )


def test_read_value_keeps_a_number_before_a_dictionary_name_as_a_number() -> None:
    value = Tokenizer(b"<< /Count 2 /Next 3 >>").read_value()

    assert value == PdfDictionary({PdfName("Count"): 2, PdfName("Next"): 3})


@pytest.mark.parametrize(
    "source",
    [b"(unterminated", b"<4A", b"[ /Name", b"<< /Name 1"],
)
def test_read_value_reports_offset_for_unterminated_constructs(source: bytes) -> None:
    with pytest.raises(PdfParseError) as raised:
        Tokenizer(source).read_value()

    assert raised.value.offset == 0
