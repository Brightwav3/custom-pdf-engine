"""Project an edited document from an immutable operation log.

Nothing here mutates the parsed source document. A :class:`DocumentState`
holds the original page records plus an ordered operation log and a cursor;
every read re-projects the pages from the originals through
``operations[:cursor]``. Undo and redo therefore only move the cursor, and a
new operation after an undo discards the abandoned redo branch.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping, Sequence

from pdfengine.api.models import (
    AddTextLayer,
    CropPages,
    DeletePages,
    ExtractPages,
    ImportPages,
    InsertBlankPage,
    METADATA_FIELDS,
    Operation,
    ReorderPages,
    RotatePages,
    SetMetadata,
)
from pdfengine.document.pages import DocumentModel, PageRecord
from pdfengine.errors import InvalidOperationError, UnsupportedOperationError
from pdfengine.ocr.models import OcrPage


@dataclass(frozen=True)
class TextLayerRequest:
    """What was asked of the recognizer, and its answer once it has run.

    Recognition needs a renderer and a subprocess, so it cannot happen during
    projection. Applying :class:`~pdfengine.api.models.AddTextLayer` therefore
    only records the request; :class:`~pdfengine.api.engine.PdfEngine` performs
    the recognition and hands the result back through
    :meth:`DocumentState.with_recognition`, which re-projection then merges
    into ``recognized``.
    """

    language: str = "eng"
    mode: str = "lstm"
    dpi: int = 300
    min_confidence: float = 0.0
    recognized: OcrPage | None = None

    @property
    def pending(self) -> bool:
        """Whether this page still needs to be recognized."""

        return self.recognized is None

    def matches(self, page: OcrPage) -> bool:
        """Whether ``page`` was recognized under exactly these settings.

        ``min_confidence`` is deliberately not compared: it filters words when
        the layer is written, so changing it must not force a re-recognition.
        """

        return (
            page.language == self.language
            and page.mode == self.mode
            and page.dpi == self.dpi
        )

    def words(self) -> tuple:
        """The recognized words at or above ``min_confidence``."""

        if self.recognized is None:
            return ()
        return self.recognized.above_confidence(self.min_confidence).words


@dataclass(frozen=True)
class ProjectedPage:
    """One page of the edited document, as it will be written."""

    page_id: str
    rotation: int
    media_box: tuple[float, float, float, float]
    crop_box: tuple[float, float, float, float] | None
    source_session_id: str | None = None
    source: PageRecord | None = None
    text_layer: TextLayerRequest | None = None

    @property
    def is_blank(self) -> bool:
        return self.source is None

    @property
    def width(self) -> float:
        box = self.crop_box or self.media_box
        return box[2] - box[0]

    @property
    def height(self) -> float:
        box = self.crop_box or self.media_box
        return box[3] - box[1]


def _initial_page(record: PageRecord, session_id: str | None) -> ProjectedPage:
    return ProjectedPage(
        page_id=record.id,
        rotation=record.info.rotation,
        media_box=record.media_box,
        crop_box=record.crop_box,
        source_session_id=session_id,
        source=record,
    )


@dataclass(frozen=True)
class DocumentState:
    """An immutable edit cursor over one opened document."""

    base_pages: tuple[ProjectedPage, ...]
    base_metadata: Mapping[str, str | None] = None  # type: ignore[assignment]
    operations: tuple[Operation, ...] = ()
    cursor: int = 0
    sources: Mapping[str, tuple[ProjectedPage, ...]] = None  # type: ignore[assignment]
    recognitions: Mapping[str, OcrPage] = None  # type: ignore[assignment]
    """Recognition results by page ID, supplied from outside the projection.

    Kept beside the operation log rather than inside it: the log stays a record
    of what the user asked for, replayable without a recognizer, while these
    are cached answers that projection merges in when they still match what the
    operation asked for.
    """

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_pages", tuple(self.base_pages))
        object.__setattr__(self, "operations", tuple(self.operations))
        object.__setattr__(self, "base_metadata", dict(self.base_metadata or {}))
        object.__setattr__(self, "sources", dict(self.sources or {}))
        object.__setattr__(self, "recognitions", dict(self.recognitions or {}))

    # -- construction ----------------------------------------------------

    @classmethod
    def from_model(
        cls, model: DocumentModel, session_id: str | None = None
    ) -> "DocumentState":
        return cls(
            base_pages=tuple(_initial_page(record, session_id) for record in model.pages),
            base_metadata={"title": model.info.title},
        )

    def with_source(self, session_id: str, model: DocumentModel) -> "DocumentState":
        """Register another open document as an import source."""

        sources = dict(self.sources)
        sources[session_id] = tuple(
            _initial_page(record, session_id) for record in model.pages
        )
        return replace(self, sources=sources)

    def with_recognition(self, page_id: str, page: OcrPage) -> "DocumentState":
        """Attach a recognition result for ``page_id``, computed elsewhere."""

        recognitions = dict(self.recognitions)
        recognitions[page_id] = page
        return replace(self, recognitions=recognitions)

    # -- projection ------------------------------------------------------

    def projected_pages(self) -> tuple[ProjectedPage, ...]:
        pages = self.base_pages
        for operation in self.operations[: self.cursor]:
            pages = _project(operation, pages, self.sources, self.recognitions)
        return pages

    def pending_text_layers(self) -> tuple[ProjectedPage, ...]:
        """Projected pages that carry a text layer request not yet recognized."""

        return tuple(
            page
            for page in self.projected_pages()
            if page.text_layer is not None and page.text_layer.pending
        )

    def projected_metadata(self) -> dict[str, str | None]:
        metadata = dict(self.base_metadata)
        for operation in self.operations[: self.cursor]:
            if isinstance(operation, SetMetadata):
                metadata.update(operation.entries)
        return {name: metadata.get(name) for name in METADATA_FIELDS}

    @property
    def page_ids(self) -> tuple[str, ...]:
        return tuple(page.page_id for page in self.projected_pages())

    # -- cursor ----------------------------------------------------------

    def apply(self, operation: Operation) -> "DocumentState":
        """Return a new state with ``operation`` appended after validation."""

        _project(operation, self.projected_pages(), self.sources, self.recognitions)
        return replace(
            self,
            operations=self.operations[: self.cursor] + (operation,),
            cursor=self.cursor + 1,
        )

    def apply_all(self, operations: Sequence[Operation]) -> "DocumentState":
        state = self
        for operation in operations:
            state = state.apply(operation)
        return state

    @property
    def can_undo(self) -> bool:
        return self.cursor > 0

    @property
    def can_redo(self) -> bool:
        return self.cursor < len(self.operations)

    def undo(self) -> "DocumentState":
        return replace(self, cursor=self.cursor - 1) if self.can_undo else self

    def redo(self) -> "DocumentState":
        return replace(self, cursor=self.cursor + 1) if self.can_redo else self


def _index_of(pages: tuple[ProjectedPage, ...], page_id: str) -> int:
    for index, page in enumerate(pages):
        if page.page_id == page_id:
            return index
    raise InvalidOperationError(f"unknown page ID: {page_id}")


def _require_known(pages: tuple[ProjectedPage, ...], page_ids: tuple[str, ...]) -> None:
    known = {page.page_id for page in pages}
    missing = [page_id for page_id in page_ids if page_id not in known]
    if missing:
        raise InvalidOperationError(f"unknown page ID: {missing[0]}")


def _project(
    operation: Operation,
    pages: tuple[ProjectedPage, ...],
    sources: Mapping[str, tuple[ProjectedPage, ...]],
    recognitions: Mapping[str, OcrPage] | None = None,
) -> tuple[ProjectedPage, ...]:
    if isinstance(operation, AddTextLayer):
        recognitions = recognitions or {}
        _require_known(pages, operation.page_ids)
        targets = set(operation.page_ids)
        layered = []
        for page in pages:
            if page.page_id not in targets:
                layered.append(page)
                continue
            request = TextLayerRequest(
                language=operation.language,
                mode=operation.mode,
                dpi=operation.dpi,
                min_confidence=operation.min_confidence,
            )
            recognized = recognitions.get(page.page_id)
            # A result recognized under different settings is not an answer to
            # this request, so it is left out and the page stays pending.
            if recognized is not None and request.matches(recognized):
                request = replace(request, recognized=recognized)
            layered.append(replace(page, text_layer=request))
        return tuple(layered)

    if isinstance(operation, RotatePages):
        _require_known(pages, operation.page_ids)
        targets = set(operation.page_ids)
        return tuple(
            replace(page, rotation=(page.rotation + operation.degrees) % 360)
            if page.page_id in targets
            else page
            for page in pages
        )

    if isinstance(operation, DeletePages):
        _require_known(pages, operation.page_ids)
        targets = set(operation.page_ids)
        remaining = tuple(page for page in pages if page.page_id not in targets)
        if not remaining:
            raise InvalidOperationError("a document must keep at least one page")
        return remaining

    if isinstance(operation, ReorderPages):
        if set(operation.page_ids) != {page.page_id for page in pages}:
            raise InvalidOperationError(
                "reorder must list every current page exactly once"
            )
        by_id = {page.page_id: page for page in pages}
        return tuple(by_id[page_id] for page_id in operation.page_ids)

    if isinstance(operation, ExtractPages):
        _require_known(pages, operation.page_ids)
        by_id = {page.page_id: page for page in pages}
        return tuple(by_id[page_id] for page_id in operation.page_ids)

    if isinstance(operation, InsertBlankPage):
        if any(page.page_id == operation.page_id for page in pages):
            raise InvalidOperationError("blank page ID is already in use")
        blank = ProjectedPage(
            page_id=operation.page_id,
            rotation=0,
            media_box=(0.0, 0.0, operation.width, operation.height),
            crop_box=None,
        )
        if operation.after_page_id is None:
            return (blank,) + pages
        position = _index_of(pages, operation.after_page_id) + 1
        return pages[:position] + (blank,) + pages[position:]

    if isinstance(operation, CropPages):
        _require_known(pages, operation.page_ids)
        targets = set(operation.page_ids)
        cropped = []
        for page in pages:
            if page.page_id not in targets:
                cropped.append(page)
                continue
            box = _clip(operation.box, page.media_box, page.page_id)
            cropped.append(replace(page, crop_box=box))
        return tuple(cropped)

    if isinstance(operation, SetMetadata):
        return pages

    if isinstance(operation, ImportPages):
        source_pages = sources.get(operation.source_session_id)
        if source_pages is None:
            raise InvalidOperationError(
                f"unknown import source session: {operation.source_session_id}"
            )
        by_id = {page.page_id: page for page in source_pages}
        missing = [page_id for page_id in operation.page_ids if page_id not in by_id]
        if missing:
            raise InvalidOperationError(f"unknown source page ID: {missing[0]}")
        existing = {page.page_id for page in pages}
        if existing & set(operation.page_ids):
            raise InvalidOperationError("imported page ID collides with a current page")
        imported = tuple(by_id[page_id] for page_id in operation.page_ids)
        if operation.after_page_id is None:
            return pages + imported
        position = _index_of(pages, operation.after_page_id) + 1
        return pages[:position] + imported + pages[position:]

    raise UnsupportedOperationError(
        f"unsupported operation: {type(operation).__name__}"
    )


def _clip(
    box: tuple[float, float, float, float],
    media_box: tuple[float, float, float, float],
    page_id: str,
) -> tuple[float, float, float, float]:
    clipped = (
        max(box[0], media_box[0]),
        max(box[1], media_box[1]),
        min(box[2], media_box[2]),
        min(box[3], media_box[3]),
    )
    if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
        raise InvalidOperationError(
            f"crop box lies outside the page bounds of {page_id}"
        )
    return clipped
