"""In-memory representations of PDF object values."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


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


@dataclass(frozen=True)
class PdfDictionary:
    entries: Mapping[PdfName, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", MappingProxyType(dict(self.entries)))
