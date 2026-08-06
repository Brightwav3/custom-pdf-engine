"""Data contracts shared by PDF engine callers and implementations.

Every mutating operation targets pages by their stable, per-open ``page_id``.
Page positions are never part of a public operation, so an operation batch
stays meaningful after earlier operations reorder or delete pages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Mapping, Union
from uuid import uuid4


_ALLOWED_ROTATIONS = (90, 180, 270)
_METADATA_FIELDS = ("title", "author", "subject", "keywords", "creator", "producer")


@dataclass(frozen=True)
class PageInfo:
    index: int
    width: float
    height: float
    rotation: int = 0
    page_id: str | None = None
    source_index: int | None = None


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
    page_id: str
    width: int
    height: int
    image_bytes: bytes
    cache_hit: bool = False


def _page_id_tuple(value: object, label: str) -> tuple[str, ...]:
    if isinstance(value, str) or not hasattr(value, "__iter__"):
        raise ValueError(f"{label} must be a sequence of page IDs")
    page_ids = tuple(value)
    if not page_ids:
        raise ValueError(f"{label} must not be empty")
    if not all(isinstance(page_id, str) and page_id for page_id in page_ids):
        raise ValueError(f"{label} must contain non-empty page IDs")
    if len(set(page_ids)) != len(page_ids):
        raise ValueError(f"{label} must not repeat a page ID")
    return page_ids


@dataclass(frozen=True)
class RotatePages:
    """Turn each named page by a relative quarter turn."""

    page_ids: tuple[str, ...]
    degrees: int
    kind: ClassVar[str] = "rotate_pages"

    def __post_init__(self) -> None:
        object.__setattr__(self, "page_ids", _page_id_tuple(self.page_ids, "page_ids"))
        if self.degrees not in _ALLOWED_ROTATIONS:
            raise ValueError("rotate requires page IDs and 90, 180, or 270 degrees")


@dataclass(frozen=True)
class DeletePages:
    """Drop each named page from the document."""

    page_ids: tuple[str, ...]
    kind: ClassVar[str] = "delete_pages"

    def __post_init__(self) -> None:
        object.__setattr__(self, "page_ids", _page_id_tuple(self.page_ids, "page_ids"))


@dataclass(frozen=True)
class ReorderPages:
    """Replace the page order with this exact permutation of current pages."""

    page_ids: tuple[str, ...]
    kind: ClassVar[str] = "reorder_pages"

    def __post_init__(self) -> None:
        object.__setattr__(self, "page_ids", _page_id_tuple(self.page_ids, "page_ids"))


@dataclass(frozen=True)
class ExtractPages:
    """Keep only the named pages, in the order given."""

    page_ids: tuple[str, ...]
    kind: ClassVar[str] = "extract_pages"

    def __post_init__(self) -> None:
        object.__setattr__(self, "page_ids", _page_id_tuple(self.page_ids, "page_ids"))


@dataclass(frozen=True)
class InsertBlankPage:
    """Insert one empty page after ``after_page_id`` (or at the front)."""

    after_page_id: str | None = None
    width: float = 612.0
    height: float = 792.0
    page_id: str = field(default="")
    kind: ClassVar[str] = "insert_blank_page"

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("blank page width and height must be positive points")
        if not self.page_id:
            # Fixed at construction so replaying the operation log is deterministic.
            object.__setattr__(self, "page_id", f"page_{uuid4().hex}")


@dataclass(frozen=True)
class CropPages:
    """Set the visible box of each named page, in PDF user-space points."""

    page_ids: tuple[str, ...]
    box: tuple[float, float, float, float]
    kind: ClassVar[str] = "crop_pages"

    def __post_init__(self) -> None:
        object.__setattr__(self, "page_ids", _page_id_tuple(self.page_ids, "page_ids"))
        box = tuple(float(value) for value in self.box)
        if len(box) != 4:
            raise ValueError("crop box must contain four numbers")
        if box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError("crop box must be non-empty")
        object.__setattr__(self, "box", box)


@dataclass(frozen=True)
class SetMetadata:
    """Replace document information entries; ``None`` clears an entry."""

    entries: Mapping[str, str | None]
    kind: ClassVar[str] = "set_metadata"

    def __post_init__(self) -> None:
        entries = dict(self.entries)
        if not entries:
            raise ValueError("set metadata requires at least one entry")
        unknown = sorted(set(entries) - set(_METADATA_FIELDS))
        if unknown:
            raise ValueError(f"unsupported metadata fields: {', '.join(unknown)}")
        for name, value in entries.items():
            if value is not None and not isinstance(value, str):
                raise ValueError(f"metadata {name} must be a string or null")
        object.__setattr__(self, "entries", dict(entries))


@dataclass(frozen=True)
class ImportPages:
    """Append pages taken from another open session."""

    source_session_id: str
    page_ids: tuple[str, ...]
    after_page_id: str | None = None
    kind: ClassVar[str] = "import_pages"

    def __post_init__(self) -> None:
        if not self.source_session_id:
            raise ValueError("import requires a source session ID")
        object.__setattr__(self, "page_ids", _page_id_tuple(self.page_ids, "page_ids"))


Operation = Union[
    RotatePages,
    DeletePages,
    ReorderPages,
    ExtractPages,
    InsertBlankPage,
    CropPages,
    SetMetadata,
    ImportPages,
]

OPERATION_TYPES: tuple[type, ...] = (
    RotatePages,
    DeletePages,
    ReorderPages,
    ExtractPages,
    InsertBlankPage,
    CropPages,
    SetMetadata,
    ImportPages,
)

METADATA_FIELDS: tuple[str, ...] = _METADATA_FIELDS


@dataclass(frozen=True)
class SaveOptions:
    """Control where and how a save materializes the current state."""

    output_path: Path | None = None
    allow_replace_source: bool = False
    dry_run: bool = False
