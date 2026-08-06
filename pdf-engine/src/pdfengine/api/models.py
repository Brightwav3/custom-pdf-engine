"""Data contracts shared by PDF engine callers and implementations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar


@dataclass(frozen=True)
class PageInfo:
    index: int
    width: float
    height: float
    rotation: int = 0


@dataclass(frozen=True)
class DocumentInfo:
    page_count: int
    pages: tuple[PageInfo, ...]
    title: str | None = None

    def __init__(
        self,
        page_count: int,
        pages: tuple[PageInfo, ...] | list[PageInfo],
        title: str | None = None,
    ) -> None:
        object.__setattr__(self, "page_count", page_count)
        object.__setattr__(self, "pages", tuple(pages))
        object.__setattr__(self, "title", title)


@dataclass(frozen=True)
class RenderResult:
    page_index: int
    width: int
    height: int
    image_bytes: bytes


@dataclass(frozen=True)
class MergeOperation:
    source_paths: tuple[Path, ...]
    kind: ClassVar[str] = "merge"

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_paths", tuple(self.source_paths))


@dataclass(frozen=True)
class SplitOperation:
    page_ranges: tuple[tuple[int, int], ...]
    kind: ClassVar[str] = "split"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "page_ranges", tuple(tuple(page_range) for page_range in self.page_ranges)
        )


@dataclass(frozen=True)
class ExtractPagesOperation:
    page_indices: tuple[int, ...]
    kind: ClassVar[str] = "extract_pages"

    def __post_init__(self) -> None:
        object.__setattr__(self, "page_indices", tuple(self.page_indices))


@dataclass(frozen=True)
class RotatePagesOperation:
    page_indices: tuple[int, ...]
    degrees: int
    kind: ClassVar[str] = "rotate_pages"

    def __post_init__(self) -> None:
        object.__setattr__(self, "page_indices", tuple(self.page_indices))


@dataclass(frozen=True)
class AddTextOperation:
    page_index: int
    text: str
    x: float
    y: float
    font_size: float = 12.0
    kind: ClassVar[str] = "add_text"


@dataclass(frozen=True)
class SaveOptions:
    output_path: Path | None = None
    overwrite: bool = False
    optimize: bool = False
