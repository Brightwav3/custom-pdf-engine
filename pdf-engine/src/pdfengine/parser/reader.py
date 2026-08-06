"""Read classic cross-reference PDF files and resolve indirect objects."""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from pathlib import Path

from pdfengine.errors import PdfParseError

from .tokens import Tokenizer
from .values import PdfArray, PdfDictionary, PdfName, PdfReference


_STARTXREF = re.compile(rb"startxref[\x00\x09\x0a\x0c\x0d\x20]+(\d+)")
_XREF_ENTRY = re.compile(rb"(\d{10}) (\d{5}) ([fn])(?:\r\n| \r| \n)")
_OBJECT_HEADER = re.compile(
    rb"(\d+)[\x00\x09\x0a\x0c\x0d\x20]+"
    rb"(\d+)[\x00\x09\x0a\x0c\x0d\x20]+"
    rb"obj(?:[\x00\x09\x0a\x0c\x0d\x20]|$)"
)


@dataclass(frozen=True)
class PdfStream:
    dictionary: PdfDictionary
    data: bytes


class PdfReader:
    """Read the object graph of a PDF that uses a classic xref table."""

    def __init__(self, path: str | Path) -> None:
        self._data = Path(path).read_bytes()
        if re.match(rb"%PDF-\d\.\d(?:\r\n|\r|\n)", self._data) is None:
            raise PdfParseError("invalid PDF header", 0)
        self._xref: dict[PdfReference, int] = {}
        self._cache: dict[PdfReference, object] = {}
        xref_offset = self._find_startxref()
        self.trailer = self._read_xref_and_trailer(xref_offset)
        if PdfName("XRefStm") in self.trailer.entries:
            raise PdfParseError("PDF xref streams are unsupported", xref_offset)
        if PdfName("Encrypt") in self.trailer.entries:
            raise PdfParseError("PDF encryption is unsupported", xref_offset)

    def resolve(self, reference: PdfReference) -> object:
        if reference in self._cache:
            return self._cache[reference]
        try:
            offset = self._xref[reference]
        except KeyError as exc:
            raise PdfParseError("indirect object is absent from xref", 0) from exc
        if not 0 <= offset < len(self._data):
            raise PdfParseError("xref entry points outside the file", offset)

        header = _OBJECT_HEADER.match(self._data, offset)
        if header is None:
            raise PdfParseError("xref entry does not point to an indirect object", offset)
        actual = PdfReference(int(header.group(1)), int(header.group(2)))
        if actual != reference:
            raise PdfParseError("xref entry points to the wrong indirect object", offset)

        tokenizer = Tokenizer(self._data[header.end() :])
        value = tokenizer.read_value()
        absolute_end = header.end() + tokenizer.offset
        if isinstance(value, PdfDictionary):
            if value.entries.get(PdfName("Type")) == PdfName("ObjStm"):
                raise PdfParseError("PDF object streams are unsupported", offset)
            stream = self._read_stream(value, absolute_end)
            if stream is not None:
                value, absolute_end = stream
        end_position = self._skip_ignored(absolute_end)
        tail = self._data[end_position:]
        end_match = re.match(
            rb"endobj(?:[\x00\x09\x0a\x0c\x0d\x20]|$)",
            tail,
        )
        if end_match is None:
            raise PdfParseError("indirect object is missing endobj", end_position)
        self._cache[reference] = value
        return value

    def _skip_ignored(self, position: int) -> int:
        while position < len(self._data):
            if self._data[position] in b"\x00\x09\x0a\x0c\x0d\x20":
                position += 1
            elif self._data[position] == ord("%"):
                position += 1
                while (
                    position < len(self._data)
                    and self._data[position] not in b"\r\n"
                ):
                    position += 1
            else:
                break
        return position

    def _read_stream(
        self, dictionary: PdfDictionary, position: int
    ) -> tuple[PdfStream, int] | None:
        position = self._skip_ignored(position)
        stream_match = re.match(
            rb"stream(?:\r\n|\r|\n)",
            self._data[position:],
        )
        if stream_match is None:
            return None
        length = dictionary.entries.get(PdfName("Length"))
        if isinstance(length, PdfReference):
            length = self.resolve(length)
        if not isinstance(length, int) or isinstance(length, bool) or length < 0:
            raise PdfParseError("stream Length must be a non-negative integer", position)
        data_start = position + stream_match.end()
        data_end = data_start + length
        if data_end > len(self._data):
            raise PdfParseError("stream data extends outside the file", data_start)
        end_match = re.match(
            rb"(?:\r\n|\r|\n)?endstream(?:[\x00\x09\x0a\x0c\x0d\x20]|$)",
            self._data[data_end:],
        )
        if end_match is None:
            raise PdfParseError("stream Length does not end at endstream", data_end)
        data = self._data[data_start:data_end]
        stream_filter = dictionary.entries.get(PdfName("Filter"))
        if stream_filter == PdfArray((PdfName("FlateDecode"),)):
            stream_filter = PdfName("FlateDecode")
        if stream_filter == PdfName("FlateDecode"):
            try:
                decoder = zlib.decompressobj()
                decoded = decoder.decompress(data)
                decoded += decoder.flush()
            except zlib.error as exc:
                raise PdfParseError("invalid FlateDecode stream", data_start) from exc
            if (
                not decoder.eof
                or decoder.unused_data
                or decoder.unconsumed_tail
            ):
                raise PdfParseError("invalid FlateDecode stream", data_start)
            data = decoded
        elif stream_filter is not None:
            raise PdfParseError("unsupported stream filter", position)
        return PdfStream(dictionary, data), data_end + end_match.end()

    def _find_startxref(self) -> int:
        tail_start = max(0, len(self._data) - 65536)
        tail = self._data[tail_start:]
        matches = list(_STARTXREF.finditer(tail))
        if not matches:
            raise PdfParseError("missing startxref", tail_start)
        return int(matches[-1].group(1))

    def _read_xref_and_trailer(self, offset: int) -> PdfDictionary:
        if not 0 <= offset < len(self._data):
            raise PdfParseError("startxref points outside the file", offset)
        position = offset
        line, position = self._read_line(position)
        if line != b"xref":
            header = _OBJECT_HEADER.match(self._data, offset)
            if header is not None:
                candidate = Tokenizer(self._data[header.end() :]).read_value()
                if (
                    isinstance(candidate, PdfDictionary)
                    and candidate.entries.get(PdfName("Type")) == PdfName("XRef")
                ):
                    raise PdfParseError("PDF xref streams are unsupported", offset)
            raise PdfParseError("startxref does not point to a classic xref table", offset)

        while True:
            line, next_position = self._read_line(position)
            if line == b"trailer":
                position = next_position
                break
            subsection = re.fullmatch(rb"(\d+) (\d+)", line)
            if subsection is None:
                raise PdfParseError("malformed xref subsection", position)
            first = int(subsection.group(1))
            count = int(subsection.group(2))
            position = next_position
            for object_number in range(first, first + count):
                entry_offset = position
                _, position = self._read_line(position)
                entry = _XREF_ENTRY.fullmatch(self._data[entry_offset:position])
                if entry is None:
                    raise PdfParseError("malformed xref entry", entry_offset)
                if entry.group(3) == b"n":
                    reference = PdfReference(object_number, int(entry.group(2)))
                    self._xref[reference] = int(entry.group(1))

        tokenizer = Tokenizer(self._data[position:])
        trailer = tokenizer.read_value()
        if not isinstance(trailer, PdfDictionary):
            raise PdfParseError("trailer must be a dictionary", position)
        return trailer

    def _read_line(self, position: int) -> tuple[bytes, int]:
        if position >= len(self._data):
            raise PdfParseError("unexpected end of file", position)
        match = re.search(rb"\r\n|\r|\n", self._data[position:])
        if match is None:
            raise PdfParseError("unterminated PDF line", position)
        line_end = position + match.start()
        next_position = position + match.end()
        return self._data[position:line_end], next_position
