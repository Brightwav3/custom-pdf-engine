"""Build an ordered, inherited view of a PDF page tree."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from pdfengine.api.models import DocumentInfo, PageInfo
from pdfengine.errors import PdfParseError
from pdfengine.parser.reader import PdfReader
from pdfengine.parser.values import PdfArray, PdfDictionary, PdfName, PdfReference

from .metadata import extract_title


_INHERITED_KEYS = ("MediaBox", "CropBox", "Rotate", "Resources")


@dataclass(frozen=True)
class PageRecord:
    """A page's resolved, inherited attributes for one document open."""

    id: str
    info: PageInfo
    media_box: tuple[float, float, float, float]
    crop_box: tuple[float, float, float, float] | None
    resources: PdfDictionary | None
    reference: PdfReference | None = None

    @property
    def page_id(self) -> str:
        """Compatibility-friendly explicit name for the stable open-session ID."""

        return self.id


@dataclass(frozen=True)
class DocumentModel:
    """Ordered page records and basic document information."""

    pages: tuple[PageRecord, ...]
    info: DocumentInfo

    @property
    def document_info(self) -> DocumentInfo:
        return self.info

    @classmethod
    def from_reader(cls, reader: PdfReader) -> "DocumentModel":
        root = reader.trailer.entries.get(PdfName("Root"))
        if root is None:
            raise PdfParseError("trailer is missing Root", 0)
        catalog = _dictionary(reader, root, "trailer Root")
        if catalog.entries.get(PdfName("Type")) != PdfName("Catalog"):
            raise PdfParseError("trailer Root must be a Catalog dictionary", 0)
        pages_root = catalog.entries.get(PdfName("Pages"))
        if pages_root is None:
            raise PdfParseError("Catalog is missing Pages", 0)

        records: list[PageRecord] = []
        _walk_page_tree(reader, pages_root, {}, set(), records)
        page_infos = tuple(record.info for record in records)
        return cls(
            pages=tuple(records),
            info=DocumentInfo(
                page_count=len(records), pages=page_infos, title=extract_title(reader)
            ),
        )


def _walk_page_tree(
    reader: PdfReader,
    node_value: object,
    inherited: dict[str, object],
    active_references: set[PdfReference],
    records: list[PageRecord],
) -> None:
    reference = node_value if isinstance(node_value, PdfReference) else None
    if reference is not None:
        if reference in active_references:
            raise PdfParseError("cycle in page tree", 0)
        active_references.add(reference)
    try:
        node = _dictionary(reader, node_value, "page-tree node")
        node_type = node.entries.get(PdfName("Type"))
        if node_type not in (PdfName("Page"), PdfName("Pages")):
            raise PdfParseError("page-tree leaf must be a Page or Pages dictionary", 0)

        merged = dict(inherited)
        for key in _INHERITED_KEYS:
            value = node.entries.get(PdfName(key))
            if value is not None:
                merged[key] = value

        if node_type == PdfName("Page"):
            records.append(_page_record(reader, reference, merged, len(records)))
            return

        kids = node.entries.get(PdfName("Kids"))
        kids = _resolve(reader, kids) if kids is not None else None
        if not isinstance(kids, PdfArray):
            raise PdfParseError("Pages dictionary must contain a Kids array", 0)
        for child in kids.items:
            _walk_page_tree(reader, child, merged, active_references, records)
    finally:
        if reference is not None:
            active_references.remove(reference)


def _page_record(
    reader: PdfReader,
    reference: PdfReference | None,
    inherited: dict[str, object],
    index: int,
) -> PageRecord:
    media_value = inherited.get("MediaBox")
    if media_value is None:
        raise PdfParseError("Page is missing an inherited MediaBox", 0)
    media_box = _box(reader, media_value, "MediaBox")
    crop_value = inherited.get("CropBox")
    crop_box = _box(reader, crop_value, "CropBox") if crop_value is not None else None
    if crop_box is not None:
        crop_box = _intersect(crop_box, media_box)
    visible_box = crop_box if crop_box is not None else media_box
    rotation = _rotation(reader, inherited.get("Rotate", 0))
    resources_value = inherited.get("Resources")
    resources = (
        _dictionary(reader, resources_value, "Resources")
        if resources_value is not None
        else None
    )
    page_id = f"page_{uuid4().hex}"
    return PageRecord(
        id=page_id,
        info=PageInfo(
            index=index,
            width=visible_box[2] - visible_box[0],
            height=visible_box[3] - visible_box[1],
            rotation=rotation,
            page_id=page_id,
            source_index=index,
        ),
        media_box=media_box,
        crop_box=crop_box,
        resources=resources,
        reference=reference,
    )


def _box(reader: PdfReader, value: object, name: str) -> tuple[float, float, float, float]:
    value = _resolve(reader, value)
    if not isinstance(value, PdfArray) or len(value.items) != 4:
        raise PdfParseError(f"{name} must contain four numbers", 0)
    numbers = []
    for item in value.items:
        item = _resolve(reader, item)
        if not isinstance(item, (int, float)) or isinstance(item, bool):
            raise PdfParseError(f"{name} must contain four numbers", 0)
        numbers.append(float(item))
    box = tuple(numbers)
    if box[2] <= box[0]:
        raise PdfParseError(f"{name} must have positive width", 0)
    if box[3] <= box[1]:
        raise PdfParseError(f"{name} must have positive height", 0)
    return box  # type: ignore[return-value]


def _intersect(
    crop_box: tuple[float, float, float, float],
    media_box: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Clip a CropBox to its MediaBox, as required for visible page dimensions."""

    box = (
        max(crop_box[0], media_box[0]),
        max(crop_box[1], media_box[1]),
        min(crop_box[2], media_box[2]),
        min(crop_box[3], media_box[3]),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        raise PdfParseError("CropBox does not overlap MediaBox", 0)
    return box


def _rotation(reader: PdfReader, value: object) -> int:
    value = _resolve(reader, value)
    if not isinstance(value, int) or isinstance(value, bool) or value % 90:
        raise PdfParseError("Rotate must be an integer multiple of 90", 0)
    return value % 360


def _dictionary(reader: PdfReader, value: object, context: str) -> PdfDictionary:
    value = _resolve(reader, value)
    if not isinstance(value, PdfDictionary):
        raise PdfParseError(f"{context} must be a dictionary", 0)
    return value


def _resolve(reader: PdfReader, value: object) -> object:
    if isinstance(value, PdfReference):
        return reader.resolve(value)
    return value
