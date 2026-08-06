"""Extract lightweight document metadata from a PDF reader."""

from __future__ import annotations

from pdfengine.errors import PdfParseError
from pdfengine.parser.reader import PdfReader
from pdfengine.parser.values import PdfDictionary, PdfName, PdfReference, PdfString


def extract_title(reader: PdfReader) -> str | None:
    """Return the document title from the optional trailer Info dictionary."""

    info = reader.trailer.entries.get(PdfName("Info"))
    if info is None:
        return None
    info = _resolve(reader, info)
    if not isinstance(info, PdfDictionary):
        raise PdfParseError("trailer Info must be a dictionary", 0)
    title = info.entries.get(PdfName("Title"))
    if title is None:
        return None
    title = _resolve(reader, title)
    if not isinstance(title, PdfString):
        raise PdfParseError("Info Title must be a string", 0)
    return _decode_pdf_string(title.value)


def _resolve(reader: PdfReader, value: object) -> object:
    if isinstance(value, PdfReference):
        return reader.resolve(value)
    return value


def _decode_pdf_string(value: bytes) -> str:
    if value.startswith((b"\xfe\xff", b"\xff\xfe")):
        return value.decode("utf-16")
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.decode("latin-1")
