"""Tokenizer for the PDF object grammar used by the parser."""

from __future__ import annotations

import re

from pdfengine.errors import PdfParseError

from .values import PdfArray, PdfDictionary, PdfName, PdfReference, PdfString


_DELIMITERS = b"()<>[]{}/%"
_NUMBER = re.compile(rb"[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")


class Tokenizer:
    """Read PDF values from a byte sequence."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    def read_value(self) -> object:
        self._skip_ignored()
        start = self.offset
        if self.offset >= len(self.data):
            raise PdfParseError("expected a PDF value", self.offset)

        current = self.data[self.offset]
        if current == ord("/"):
            return self._read_name()
        if current == ord("("):
            return self._read_literal_string()
        if current == ord("["):
            return self._read_array()
        if self.data.startswith(b"<<", self.offset):
            return self._read_dictionary()
        if current == ord("<"):
            return self._read_hex_string()

        token = self._read_regular_token()
        value = self._value_for_regular_token(token, start)
        if isinstance(value, int) and not isinstance(value, bool):
            reference = self._try_read_reference(value)
            if reference is not None:
                return reference
        return value

    def _skip_ignored(self) -> None:
        while self.offset < len(self.data):
            current = self.data[self.offset]
            if current in b"\x00\x09\x0a\x0c\x0d\x20":
                self.offset += 1
                continue
            if current == ord("%"):
                self.offset += 1
                while self.offset < len(self.data) and self.data[self.offset] not in b"\x0a\x0d":
                    self.offset += 1
                continue
            return

    def _read_name(self) -> PdfName:
        self.offset += 1
        content = bytearray()
        while self.offset < len(self.data) and not self._is_separator(self.data[self.offset]):
            current = self.data[self.offset]
            if current == ord("#") and self.offset + 2 < len(self.data):
                digits = self.data[self.offset + 1 : self.offset + 3]
                try:
                    content.append(int(digits, 16))
                except ValueError:
                    content.append(current)
                    self.offset += 1
                else:
                    self.offset += 3
                continue
            content.append(current)
            self.offset += 1
        return PdfName(bytes(content).decode("latin-1"))

    def _read_literal_string(self) -> PdfString:
        start = self.offset
        self.offset += 1
        depth = 1
        content = bytearray()
        while self.offset < len(self.data):
            current = self.data[self.offset]
            self.offset += 1
            if current == ord("\\"):
                self._read_string_escape(content, start)
            elif current == ord("("):
                depth += 1
                content.append(current)
            elif current == ord(")"):
                depth -= 1
                if depth == 0:
                    return PdfString(bytes(content))
                content.append(current)
            elif current == ord("\r"):
                if self.offset < len(self.data) and self.data[self.offset] == ord("\n"):
                    self.offset += 1
                content.append(ord("\n"))
            else:
                content.append(current)
        raise PdfParseError("unterminated literal string", start)

    def _read_string_escape(self, content: bytearray, start: int) -> None:
        if self.offset >= len(self.data):
            raise PdfParseError("unterminated literal string", start)
        current = self.data[self.offset]
        self.offset += 1
        escaped = {
            ord("n"): ord("\n"),
            ord("r"): ord("\r"),
            ord("t"): ord("\t"),
            ord("b"): ord("\b"),
            ord("f"): ord("\f"),
        }
        if current in escaped:
            content.append(escaped[current])
        elif current == ord("\r"):
            if self.offset < len(self.data) and self.data[self.offset] == ord("\n"):
                self.offset += 1
        elif current == ord("\n"):
            return
        elif ord("0") <= current <= ord("7"):
            digits = bytearray([current])
            while len(digits) < 3 and self.offset < len(self.data):
                next_byte = self.data[self.offset]
                if not ord("0") <= next_byte <= ord("7"):
                    break
                digits.append(next_byte)
                self.offset += 1
            content.append(int(digits, 8) & 0xFF)
        else:
            content.append(current)

    def _read_hex_string(self) -> PdfString:
        start = self.offset
        self.offset += 1
        digits = bytearray()
        while self.offset < len(self.data):
            current = self.data[self.offset]
            self.offset += 1
            if current == ord(">"):
                if len(digits) % 2:
                    digits.append(ord("0"))
                try:
                    return PdfString(bytes.fromhex(digits.decode("ascii")))
                except ValueError as exc:
                    raise PdfParseError("invalid hexadecimal string", start) from exc
            if current in b"\x00\x09\x0a\x0c\x0d\x20":
                continue
            digits.append(current)
        raise PdfParseError("unterminated hexadecimal string", start)

    def _read_array(self) -> PdfArray:
        start = self.offset
        self.offset += 1
        items = []
        while True:
            self._skip_ignored()
            if self.offset >= len(self.data):
                raise PdfParseError("unterminated array", start)
            if self.data[self.offset] == ord("]"):
                self.offset += 1
                return PdfArray(tuple(items))
            items.append(self.read_value())

    def _read_dictionary(self) -> PdfDictionary:
        start = self.offset
        self.offset += 2
        entries = {}
        while True:
            self._skip_ignored()
            if self.offset >= len(self.data):
                raise PdfParseError("unterminated dictionary", start)
            if self.data.startswith(b">>", self.offset):
                self.offset += 2
                return PdfDictionary(entries)
            key = self.read_value()
            if not isinstance(key, PdfName):
                raise PdfParseError("dictionary key must be a name", self.offset)
            self._skip_ignored()
            if self.offset >= len(self.data) or self.data.startswith(b">>", self.offset):
                raise PdfParseError("dictionary is missing a value", start)
            entries[key] = self.read_value()

    def _read_regular_token(self) -> bytes:
        start = self.offset
        while self.offset < len(self.data) and not self._is_separator(self.data[self.offset]):
            self.offset += 1
        if start == self.offset:
            raise PdfParseError("unexpected PDF delimiter", start)
        return self.data[start : self.offset]

    def _try_read_reference(self, object_number: int) -> PdfReference | None:
        after_object = self.offset
        self._skip_ignored()
        generation_start = self.offset
        if generation_start >= len(self.data):
            self.offset = after_object
            return None
        if self._is_separator(self.data[generation_start]):
            self.offset = after_object
            return None
        generation_token = self._read_regular_token()
        if not generation_token.isdigit():
            self.offset = after_object
            return None
        self._skip_ignored()
        if self.data.startswith(b"R", self.offset) and self._token_ends_at(self.offset + 1):
            self.offset += 1
            return PdfReference(object_number, int(generation_token))
        self.offset = after_object
        return None

    def _value_for_regular_token(self, token: bytes, start: int) -> object:
        if token == b"true":
            return True
        if token == b"false":
            return False
        if token == b"null":
            return None
        if _NUMBER.fullmatch(token):
            return float(token) if b"." in token else int(token)
        raise PdfParseError(f"unexpected token {token!r}", start)

    def _is_separator(self, current: int) -> bool:
        return current in b"\x00\x09\x0a\x0c\x0d\x20" or current in _DELIMITERS

    def _token_ends_at(self, position: int) -> bool:
        return position >= len(self.data) or self._is_separator(self.data[position])
