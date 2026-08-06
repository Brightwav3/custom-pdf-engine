"""In-memory representations of PDF object values."""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from pdfengine.errors import PdfParseError, UnsupportedPdfError


MAX_DECODED_BYTES = 128 * 1024 * 1024
"""Ceiling on the size of a single decoded stream body.

A few hundred bytes of Flate can expand into gigabytes, so decoding is
bounded rather than trusted. The limit is a module global on purpose: tests
lower it instead of allocating 128 MiB to prove the guard works.
"""


@dataclass(frozen=True)
class PdfName:
    value: str


@dataclass(frozen=True)
class PdfReference:
    object_number: int
    generation: int


@dataclass(frozen=True)
class PdfString:
    value: bytes


@dataclass(frozen=True)
class PdfArray:
    items: tuple[object, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True)
class PdfDictionary:
    entries: Mapping[PdfName, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", MappingProxyType(dict(self.entries)))


_FLATE = PdfName("FlateDecode")


@dataclass(frozen=True)
class PdfStream:
    """A stream object holding the bytes exactly as they sit in the file.

    Reading a stream never decodes it. ``raw`` is what the file contains,
    ``filters`` is the declared filter chain, and ``data`` decodes on demand.
    That split is what lets a document carrying a filter this engine cannot
    decode — a JPEG, say — still be opened, inspected and copied out byte for
    byte, instead of failing the whole file at read time.
    """

    dictionary: PdfDictionary
    raw: bytes
    filters: tuple[PdfName, ...] = ()
    decode_parms: tuple[object, ...] = ()
    _decoded: bytes | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "filters", tuple(self.filters))
        object.__setattr__(self, "decode_parms", tuple(self.decode_parms))

    def parms_at(self, index: int) -> object:
        """The decode parameters declared for filter ``index``, if any."""

        return self.decode_parms[index] if index < len(self.decode_parms) else None

    def _decodable_at(self, index: int) -> bool:
        if self.filters[index] != _FLATE:
            return False
        # Flate with a predictor needs an un-predicting pass this version does
        # not implement. Inflating alone would return plausible-looking bytes
        # that are simply wrong, so treat it as undecodable rather than lie.
        return not _has_predictor(self.parms_at(index))

    @property
    def residual_filters(self) -> tuple[PdfName, ...]:
        """The filters left over after the longest decodable prefix."""

        index = 0
        while index < len(self.filters) and self._decodable_at(index):
            index += 1
        return self.filters[index:]

    @property
    def is_decodable(self) -> bool:
        return self.residual_filters == ()

    @property
    def data(self) -> bytes:
        """The decoded stream body, decoded once and then remembered."""

        cached = self._decoded
        if cached is not None:
            return cached
        residual = self.residual_filters
        if residual:
            name = residual[0].value
            if residual[0] == _FLATE:
                name = f"{name} with a predictor"
            raise UnsupportedPdfError(f"stream filter {name}")
        decoded = self.raw
        for _ in self.filters:
            decoded = _inflate(decoded)
        object.__setattr__(self, "_decoded", decoded)
        return decoded


def _has_predictor(parms: object) -> bool:
    """True when decode parameters ask for PNG or TIFF prediction."""

    if not isinstance(parms, PdfDictionary):
        return False
    predictor = parms.entries.get(PdfName("Predictor"))
    if isinstance(predictor, bool) or not isinstance(predictor, int):
        return False
    return predictor > 1


def _inflate(data: bytes) -> bytes:
    """Inflate one Flate member, refusing to allocate past the limit."""

    limit = MAX_DECODED_BYTES
    decoder = zlib.decompressobj()
    try:
        decoded = decoder.decompress(data, limit + 1)
        if len(decoded) <= limit:
            decoded += decoder.flush()
    except zlib.error as exc:
        raise PdfParseError("invalid FlateDecode stream", 0) from exc
    if len(decoded) > limit:
        raise PdfParseError("decoded stream exceeds the size limit", 0)
    if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise PdfParseError("invalid FlateDecode stream", 0)
    return decoded
