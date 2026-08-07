"""The public PDF engine façade: open, inspect, render, edit, save, close."""

from __future__ import annotations

import tempfile
import time
from hashlib import sha256
from pathlib import Path
from typing import Iterable, Sequence

from pdfengine.editing.state import DocumentState, ProjectedPage
from pdfengine.errors import (
    InvalidOperationError,
    OcrUnavailableError,
    PdfEngineError,
    SessionNotFoundError,
    SessionStateError,
    SourceChangedError,
)
from pdfengine.ocr.base import OcrCapability, OcrEngine
from pdfengine.ocr.tesseract import TesseractOcr
from pdfengine.parser.values import SUPPORTED_FILTERS
from pdfengine.rendering.base import DpiRenderer, PageRenderer, RendererCapability
from pdfengine.rendering.cache import RenderCache
from pdfengine.rendering.poppler import PopplerRenderer

from .models import (
    AddTextLayer,
    DocumentInfo,
    ImportPages,
    OPERATION_TYPES,
    Operation,
    PageInfo,
    RenderResult,
    SaveOptions,
)
from .session import DocumentSession, SessionState, SessionTombstone


DEFAULT_THUMBNAIL_WIDTH = 180
DEFAULT_PREVIEW_WIDTH = 1000

_DEFAULT_OCR: OcrEngine | None = None


def _default_ocr() -> OcrEngine:
    """One shared Tesseract adapter for engines that did not bring their own.

    Everything the adapter caches — where the executable is, which languages
    are installed, whether legacy mode actually works — describes the machine
    rather than any document. Probing that once per process instead of once per
    :class:`PdfEngine` is therefore both correct and considerably cheaper: the
    legacy probe runs a real recognition, and it does not get a different
    answer for the second engine.
    """

    global _DEFAULT_OCR
    if _DEFAULT_OCR is None:
        _DEFAULT_OCR = TesseractOcr()
    return _DEFAULT_OCR


class PdfEngine:
    """Own every open document session, its cache, and its renderer."""

    def __init__(
        self,
        cache_root: str | Path | None = None,
        renderer: PageRenderer | None = None,
        ocr: OcrEngine | None = None,
    ) -> None:
        if cache_root is None:
            cache_root = Path(tempfile.gettempdir()) / "pdfengine-cache"
        self._cache_root = Path(cache_root)
        self._cache_root.mkdir(parents=True, exist_ok=True)
        self._renderer = renderer if renderer is not None else PopplerRenderer()
        self._ocr = ocr if ocr is not None else _default_ocr()
        self._sessions: dict[str, DocumentSession] = {}
        self._tombstones: dict[str, SessionTombstone] = {}

    # -- lifecycle -------------------------------------------------------

    def open_document(
        self, path: str | Path, password: str | None = None
    ) -> DocumentSession:
        source = Path(path)
        if not source.is_file():
            raise PdfEngineError(f"no such PDF file: {source}")
        session = DocumentSession.open(source, self._cache_root, password)
        self._sessions[session.session_id] = session
        return session

    def session(self, session_id: str) -> DocumentSession:
        session = self._sessions.get(session_id)
        if session is not None and not session.closed:
            return session
        tombstone = self._tombstones.get(session_id)
        if tombstone is not None:
            raise SessionStateError(
                f"session is closed: {session_id}",
                session_id=session_id,
                state=tombstone.state.value,
                allowed=["open"],
            )
        raise SessionNotFoundError(f"unknown or closed session: {session_id}")

    def tombstone(self, session_id: str) -> SessionTombstone:
        """The record of a closed session. Raises if the ID was never issued."""

        tombstone = self._tombstones.get(session_id)
        if tombstone is None:
            raise SessionNotFoundError(f"no closed session: {session_id}")
        return tombstone

    def close(self, session: DocumentSession | str) -> None:
        session = self._as_session(session)
        session.close()
        self._sessions.pop(session.session_id, None)
        self._tombstones[session.session_id] = SessionTombstone(
            session_id=session.session_id, closed_at=time.time()
        )

    def close_all(self) -> None:
        for session in list(self._sessions.values()):
            self.close(session)

    # -- inspection ------------------------------------------------------

    def inspect_document(self, session: DocumentSession | str) -> DocumentInfo:
        session = self._as_session(session)
        pages = session.state.projected_pages()
        return DocumentInfo(
            page_count=len(pages),
            pages=tuple(
                PageInfo(
                    index=index,
                    width=page.width,
                    height=page.height,
                    rotation=page.rotation,
                    page_id=page.page_id,
                    source_index=page.source.info.index if page.source else None,
                )
                for index, page in enumerate(pages)
            ),
            title=session.state.projected_metadata().get("title"),
        )

    def renderer_capability(self) -> RendererCapability:
        try:
            return self._renderer.capability()
        except Exception as exc:  # a broken adapter must not crash the caller
            return RendererCapability("error", str(exc))

    def ocr_capability(
        self, language: str = "eng", mode: str = "lstm"
    ) -> OcrCapability:
        """Whether text can be recognized right now. Never raises.

        A missing Tesseract is a fact about the machine, not a programming
        error: it comes back as an ``unavailable`` capability so a caller can
        grey the feature out and say what to install, exactly as a missing
        Poppler does for previews.
        """

        try:
            return self._ocr.capability(language, mode)
        except Exception as exc:  # a broken adapter must not crash the caller
            return OcrCapability("error", str(exc))

    ALLOWED_COMMANDS_WHEN_OPEN: tuple[str, ...] = (
        "inspect",
        "capabilities",
        "render",
        "apply",
        "undo",
        "redo",
        "save",
        "artifact",
        "close",
    )

    def capabilities(self, session: DocumentSession | str | None = None) -> dict:
        preview = self.renderer_capability()
        ocr = self.ocr_capability()
        capabilities = {
            "preview": {"state": preview.state, "detail": preview.detail},
            "ocr": ocr.as_dict(),
            "operations": self._operation_capabilities(ocr),
            "save": {"fullRewriteOnly": True, "inPlaceRequiresOptIn": True},
            "filters": {"decodable": list(SUPPORTED_FILTERS)},
        }
        if session is not None:
            document = self._read_capability(self._as_session(session))
            capabilities["document"] = document
            # Retained under its v0.1 name: the policy forbids removing a field.
            capabilities["read"] = document
            capabilities["allowedCommands"] = list(self.ALLOWED_COMMANDS_WHEN_OPEN)
        return capabilities

    def _operation_capabilities(self, ocr: OcrCapability) -> list[dict]:
        """Report per-operation readiness rather than a flat catalogue.

        Structural edits copy stream bytes through untouched, so they are ready
        whenever the document opened. ``add_text_layer`` is only as available as
        the OCR installation behind it, and saying so here is what stops a caller
        from discovering that by catching an error mid-batch.
        """

        entries = []
        for operation in OPERATION_TYPES:
            state, detail = "ready", ""
            if operation.kind == "add_text_layer" and ocr.state != "ready":
                state, detail = ocr.state, ocr.detail
            entries.append(
                {
                    "kind": operation.kind,
                    "state": state,
                    "detail": detail,
                    "safe": True,
                    "requires": ["ocr"] if operation.kind == "add_text_layer" else [],
                    "schema": "operation-request.json",
                }
            )
        return entries

    def _read_capability(self, session: DocumentSession) -> dict:
        """Describe what this document can be *read* for, not just edited into.

        Structural edits copy stream bytes through untouched, so they work on
        any document the engine could open. Anything that needs to look inside
        those bytes does not, and a caller deserves to learn that before it
        tries rather than by catching an error halfway through.
        """

        filters, count = self._undecodable_survey(session)
        if count:
            subject = "stream uses" if count == 1 else "streams use"
            text = {
                "state": "blocked",
                "detail": f"{count} {subject} filters this version cannot decode",
                "filters": [name.value for name in filters],
                "objectCount": count,
            }
        else:
            text = {"state": "ready", "detail": "", "filters": [], "objectCount": 0}
        return {
            "structuralEdit": {"state": "ready", "detail": ""},
            "textContent": text,
        }

    def _undecodable_survey(
        self, session: DocumentSession
    ) -> tuple[tuple[object, ...], int]:
        if session.undecodable_survey is None:
            session.undecodable_survey = session.model.undecodable_streams(
                session.reader
            )
        return session.undecodable_survey

    # -- rendering -------------------------------------------------------

    def render_page(
        self,
        session: DocumentSession | str,
        page_id: str,
        width: int = DEFAULT_PREVIEW_WIDTH,
    ) -> RenderResult:
        session = self._as_session(session)
        self._projected_page(session, page_id)  # reject unknown page IDs early

        source, indices, state_hash = self._preview_source(session)
        cache = RenderCache(session.cache_dir)
        return cache.get_or_render(
            fingerprint=state_hash,
            page_id=page_id,
            width=width,
            renderer=self._renderer,
            source=source,
            page_index=indices[page_id],
            password=session.password,
        )

    def render_thumbnail(
        self,
        session: DocumentSession | str,
        page_id: str,
        width: int = DEFAULT_THUMBNAIL_WIDTH,
    ) -> RenderResult:
        return self.render_page(session, page_id, width)

    # -- editing ---------------------------------------------------------

    def apply_operations(
        self,
        session: DocumentSession | str,
        operations: Sequence[Operation],
        dry_run: bool = False,
    ) -> DocumentState:
        session = self._as_session(session)
        state = self._with_import_sources(session, operations)
        state = state.apply_all(operations)
        if any(isinstance(operation, AddTextLayer) for operation in operations):
            # Recognition rasterizes pages and shells out to an OCR engine, so
            # it lives here rather than in the projection: DocumentState.apply
            # stays pure and replayable without a renderer on the machine.
            state = self._recognize_text_layers(session, state)
        if not dry_run:
            session.state = state
        return state

    def _recognize_text_layers(
        self, session: DocumentSession, state: DocumentState
    ) -> DocumentState:
        """Recognize every page whose text layer request is still pending."""

        pending = state.pending_text_layers()
        if not pending:
            return state

        if not isinstance(self._renderer, DpiRenderer):
            raise OcrUnavailableError(
                "the configured renderer cannot rasterize at an exact DPI, "
                "which OCR requires"
            )

        source, indices, _ = self._preview_source_for(session, state)
        for page in pending:
            request = page.text_layer
            assert request is not None
            index = indices.get(page.page_id)
            if index is None:
                raise InvalidOperationError(
                    f"page {page.page_id} cannot be rendered for recognition"
                )
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                image_bytes = self._renderer.render_at_dpi(
                    source, index, request.dpi, session.password, output
                )
                image = output / "ocr-page.png"
                image.write_bytes(image_bytes)
                recognized = self._ocr.recognize(
                    image,
                    dpi=request.dpi,
                    language=request.language,
                    mode=request.mode,
                )
            state = state.with_recognition(page.page_id, recognized)
        return state

    def undo(self, session: DocumentSession | str) -> DocumentState:
        session = self._as_session(session)
        session.state = session.state.undo()
        return session.state

    def redo(self, session: DocumentSession | str) -> DocumentState:
        session = self._as_session(session)
        session.state = session.state.redo()
        return session.state

    # -- saving ----------------------------------------------------------

    def default_target(self, session: DocumentSession | str) -> Path:
        session = self._as_session(session)
        parent = session.path.parent
        stem = session.path.stem
        candidate = parent / f"{stem}-edited.pdf"
        counter = 2
        while candidate.exists():
            candidate = parent / f"{stem}-edited-{counter}.pdf"
            counter += 1
        return candidate

    def save(
        self,
        session: DocumentSession | str,
        target: str | Path | None = None,
        options: SaveOptions | None = None,
    ) -> Path:
        # Imported here so the writer's own imports stay off the hot open path.
        from pdfengine.writing.rewrite import FullRewriteWriter

        session = self._as_session(session)
        options = options or SaveOptions()
        target = Path(target) if target is not None else options.output_path
        if target is None:
            target = self.default_target(session)
        target = Path(target).resolve()

        replaces_source = target == session.path
        if replaces_source and not options.allow_replace_source:
            raise PdfEngineError(
                "saving over the source document requires allow_replace_source=True"
            )
        if session.source_changed():
            raise SourceChangedError(
                f"the source document changed on disk since it was opened: {session.path}"
            )
        if options.dry_run:
            return target

        readers = self._readers_for(session)

        write_options = SaveOptions(
            output_path=target,
            allow_replace_source=options.allow_replace_source or replaces_source,
        )
        written = FullRewriteWriter().write(session.state, readers, target, write_options)
        if replaces_source:
            session.fingerprint = type(session.fingerprint).of(written)
        return written

    # -- internals -------------------------------------------------------

    def _as_session(self, session: DocumentSession | str) -> DocumentSession:
        if isinstance(session, str):
            return self.session(session)
        if session.closed:
            raise SessionStateError(
                f"session is closed: {session.session_id}",
                session_id=session.session_id,
                state=SessionState.CLOSED.value,
                allowed=["open"],
            )
        return session

    def _readers_for(self, session: DocumentSession) -> dict:
        readers: dict = {}
        for page in session.state.projected_pages():
            origin_id = page.source_session_id
            if origin_id is not None and origin_id not in readers:
                readers[origin_id] = self._origin_session(session, page).reader
        return readers

    def _preview_source(
        self, session: DocumentSession
    ) -> tuple[Path, dict[str, int], str]:
        return self._preview_source_for(session, session.state)

    def _preview_source_for(
        self, session: DocumentSession, state: DocumentState
    ) -> tuple[Path, dict[str, int], str]:
        """Return (file to render, page_id -> page index in that file, state hash).

        An unedited session previews straight from its own file: nothing is
        written. Once an edit exists, the projected state is materialized once
        per distinct state, so what is previewed is exactly what a save writes —
        rotation, crop, blank pages and imported pages included.
        """

        if state.cursor == 0:
            return (
                session.path,
                {
                    page.page_id: page.source.info.index
                    for page in state.projected_pages()
                    if page.source is not None
                },
                session.fingerprint.digest,
            )

        # Imported here so the writer's own imports stay off the hot open path.
        from pdfengine.writing.rewrite import FullRewriteWriter

        identity = sha256(session.fingerprint.digest.encode("utf-8"))
        for operation in state.operations[: state.cursor]:
            identity.update(b"\0")
            identity.update(repr(operation).encode("utf-8"))
        state_hash = identity.hexdigest()

        target = session.cache_dir / f"state-{state_hash}.pdf"
        if not target.is_file():
            FullRewriteWriter().write(
                state,
                self._readers_for(session),
                target,
                SaveOptions(output_path=target, allow_replace_source=True),
            )
        for stale in session.cache_dir.glob("state-*.pdf"):
            if stale != target:
                stale.unlink(missing_ok=True)

        indices = {
            page.page_id: index for index, page in enumerate(state.projected_pages())
        }
        return target, indices, state_hash

    def _projected_page(
        self, session: DocumentSession, page_id: str
    ) -> ProjectedPage:
        for page in session.state.projected_pages():
            if page.page_id == page_id:
                return page
        raise InvalidOperationError(f"unknown page ID: {page_id}")

    def _origin_session(
        self, session: DocumentSession, page: ProjectedPage
    ) -> DocumentSession:
        origin_id = page.source_session_id
        if origin_id is None or origin_id == session.session_id:
            return session
        origin = self._sessions.get(origin_id)
        if origin is None or origin.closed:
            raise SessionNotFoundError(
                f"the document page {page.page_id} came from is no longer open"
            )
        return origin

    def _with_import_sources(
        self, session: DocumentSession, operations: Iterable[Operation]
    ) -> DocumentState:
        state = session.state
        for operation in operations:
            if not isinstance(operation, ImportPages):
                continue
            source_id = operation.source_session_id
            if source_id in state.sources:
                continue
            source = self._sessions.get(source_id)
            if source is None or source.closed:
                raise SessionNotFoundError(
                    f"unknown or closed import source session: {source_id}"
                )
            state = state.with_source(source_id, source.model)
        return state
