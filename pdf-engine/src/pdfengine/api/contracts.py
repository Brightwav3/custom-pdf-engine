"""The one JSON contract every public surface speaks.

The Python library, the JSONL agent CLI, and the loopback HTTP service all
route through :meth:`CommandDispatcher.dispatch`, so a given request payload
produces byte-identical results whichever transport delivered it.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from pdfengine.errors import (
    InvalidRequestError,
    PdfEngineError,
    SessionNotFoundError,
)

from .engine import PdfEngine
from .models import (
    AddTextLayer,
    CropPages,
    DeletePages,
    DocumentInfo,
    ExtractPages,
    ImportPages,
    InsertBlankPage,
    Operation,
    ReorderPages,
    RotatePages,
    SaveOptions,
    SetMetadata,
)


API_VERSION = "v1"
SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas" / API_VERSION
SCHEMA_NAMES = (
    "open-request",
    "operation-request",
    "save-request",
    "response",
)
COMMANDS = (
    "open",
    "inspect",
    "capabilities",
    "render",
    "apply",
    "undo",
    "redo",
    "save",
    "close",
)
NEXT_ACTIONS = ("inspect", "render", "apply", "save", "close")


# -- envelope -------------------------------------------------------------


def success(request_id: str, result: Mapping[str, Any], warnings=None) -> dict:
    return {
        "apiVersion": API_VERSION,
        "requestId": request_id,
        "ok": True,
        "result": dict(result),
        "warnings": list(warnings or []),
    }


def failure(request_id: str, code: str, message: str, **details: object) -> dict:
    return {
        "apiVersion": API_VERSION,
        "requestId": request_id,
        "ok": False,
        "error": {"code": code, "message": message, "details": dict(details)},
        "warnings": [],
    }


def schema_bytes(name: str) -> bytes:
    """Return the exact schema bytes served by every transport."""

    if name.endswith(".json"):
        name = name[: -len(".json")]
    if name not in SCHEMA_NAMES:
        raise InvalidRequestError(f"unknown schema: {name}", field="name")
    return (SCHEMA_DIR / f"{name}.json").read_bytes()


# -- payload helpers ------------------------------------------------------


def _require_mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InvalidRequestError(f"{field} must be an object", field=field)
    return value


def _reject_unknown(payload: Mapping[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise InvalidRequestError(
            f"unknown {field} field: {unknown[0]}", field=unknown[0]
        )


def _string(payload: Mapping[str, Any], field: str, required: bool = True) -> str | None:
    value = payload.get(field)
    if value is None:
        if required:
            raise InvalidRequestError(f"{field} is required", field=field)
        return None
    if not isinstance(value, str) or not value:
        raise InvalidRequestError(f"{field} must be a non-empty string", field=field)
    return value


def _page_ids(payload: Mapping[str, Any]) -> tuple[str, ...]:
    value = payload.get("pageIds")
    if not isinstance(value, list):
        raise InvalidRequestError("pageIds must be an array", field="pageIds")
    if not value:
        raise InvalidRequestError("pageIds must not be empty", field="pageIds")
    if not all(isinstance(item, str) and item for item in value):
        raise InvalidRequestError(
            "pageIds must contain non-empty strings", field="pageIds"
        )
    return tuple(value)


def _number(payload: Mapping[str, Any], field: str, default: float | None = None) -> float:
    value = payload.get(field, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidRequestError(f"{field} must be a number", field=field)
    return float(value)


def parse_operation(payload: object) -> Operation:
    """Convert one external camelCase operation into a public model."""

    payload = _require_mapping(payload, "operation")
    kind = _string(payload, "kind")
    try:
        if kind == "rotate_pages":
            _reject_unknown(payload, {"kind", "pageIds", "degrees"}, "operation")
            degrees = payload.get("degrees")
            if not isinstance(degrees, int) or isinstance(degrees, bool):
                raise InvalidRequestError("degrees must be an integer", field="degrees")
            return RotatePages(_page_ids(payload), degrees)
        if kind == "delete_pages":
            _reject_unknown(payload, {"kind", "pageIds"}, "operation")
            return DeletePages(_page_ids(payload))
        if kind == "reorder_pages":
            _reject_unknown(payload, {"kind", "pageIds"}, "operation")
            return ReorderPages(_page_ids(payload))
        if kind == "extract_pages":
            _reject_unknown(payload, {"kind", "pageIds"}, "operation")
            return ExtractPages(_page_ids(payload))
        if kind == "insert_blank_page":
            _reject_unknown(
                payload, {"kind", "afterPageId", "width", "height", "pageId"}, "operation"
            )
            return InsertBlankPage(
                after_page_id=_string(payload, "afterPageId", required=False),
                width=_number(payload, "width", 612.0),
                height=_number(payload, "height", 792.0),
                page_id=_string(payload, "pageId", required=False) or "",
            )
        if kind == "crop_pages":
            _reject_unknown(payload, {"kind", "pageIds", "box"}, "operation")
            box = payload.get("box")
            if not isinstance(box, list) or len(box) != 4:
                raise InvalidRequestError("box must contain four numbers", field="box")
            if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in box):
                raise InvalidRequestError("box must contain four numbers", field="box")
            return CropPages(_page_ids(payload), tuple(float(item) for item in box))
        if kind == "set_metadata":
            _reject_unknown(payload, {"kind", "entries"}, "operation")
            return SetMetadata(_require_mapping(payload.get("entries"), "entries"))
        if kind == "import_pages":
            _reject_unknown(
                payload, {"kind", "sourceSessionId", "pageIds", "afterPageId"}, "operation"
            )
            return ImportPages(
                _string(payload, "sourceSessionId"),
                _page_ids(payload),
                after_page_id=_string(payload, "afterPageId", required=False),
            )
        if kind == "add_text_layer":
            _reject_unknown(
                payload,
                {"kind", "pageIds", "language", "mode", "dpi", "minConfidence"},
                "operation",
            )
            dpi = payload.get("dpi", 300)
            if isinstance(dpi, bool) or not isinstance(dpi, int):
                raise InvalidRequestError("dpi must be an integer", field="dpi")
            return AddTextLayer(
                _page_ids(payload),
                language=_string(payload, "language", required=False) or "eng",
                mode=_string(payload, "mode", required=False) or "lstm",
                dpi=dpi,
                min_confidence=_number(payload, "minConfidence", 0.0),
            )
    except ValueError as exc:
        raise InvalidRequestError(str(exc), field="operation") from exc
    raise InvalidRequestError(f"unknown operation kind: {kind}", field="kind")


def parse_command(payload: object) -> tuple[str, str, Mapping[str, Any]]:
    """Validate the envelope and return ``(request_id, command, payload)``."""

    payload = _require_mapping(payload, "request")
    request_id = payload.get("requestId")
    if not isinstance(request_id, str) or not request_id:
        raise InvalidRequestError("requestId is required", field="requestId")
    version = payload.get("apiVersion")
    if version != API_VERSION:
        raise InvalidRequestError(
            f"unsupported apiVersion: {version!r}", field="apiVersion"
        )
    command = payload.get("command")
    if command not in COMMANDS:
        raise InvalidRequestError(f"unknown command: {command!r}", field="command")
    return request_id, command, payload


# -- DTOs -----------------------------------------------------------------


def document_dto(info: DocumentInfo) -> dict:
    return {
        "pageCount": info.page_count,
        "title": info.title,
        "pages": [
            {
                "pageId": page.page_id,
                "index": page.index,
                "sourceIndex": page.source_index,
                "width": page.width,
                "height": page.height,
                "rotation": page.rotation,
            }
            for page in info.pages
        ],
    }


# -- dispatcher -----------------------------------------------------------


class CommandDispatcher:
    """Turn JSON requests into engine calls and JSON responses."""

    def __init__(self, engine: PdfEngine | None = None, cache_root=None) -> None:
        self.engine = engine if engine is not None else PdfEngine(cache_root=cache_root)
        self.artifacts: dict[str, bytes] = {}

    def dispatch(self, payload: object) -> dict:
        request_id = "unknown"
        try:
            request_id, command, request = parse_command(payload)
            handler = getattr(self, f"_command_{command}")
            return success(request_id, handler(request))
        except PdfEngineError as exc:
            details = {}
            field = getattr(exc, "field", None)
            if field:
                details["field"] = field
            feature = getattr(exc, "feature", None)
            if feature:
                details["feature"] = feature
            for name in ("session_id", "state", "attempted", "allowed"):
                value = getattr(exc, name, None)
                if value:
                    key = "sessionId" if name == "session_id" else name
                    details[key] = value
            return failure(request_id, exc.code, str(exc), **details)
        except (ValueError, TypeError) as exc:
            return failure(request_id, "invalid_request", str(exc))

    def close(self) -> None:
        self.engine.close_all()
        self.artifacts.clear()

    # -- commands ----------------------------------------------------

    def _command_open(self, request: Mapping[str, Any]) -> dict:
        _reject_unknown(
            request,
            {"apiVersion", "requestId", "command", "path", "password"},
            "request",
        )
        session = self.engine.open_document(
            _string(request, "path"), _string(request, "password", required=False)
        )
        return {
            "sessionId": session.session_id,
            "path": str(session.path),
            "document": document_dto(self.engine.inspect_document(session)),
            "capabilities": self.engine.capabilities(session),
            "nextActions": list(NEXT_ACTIONS),
        }

    def _session(self, request: Mapping[str, Any]):
        return self.engine.session(_string(request, "sessionId"))

    def _command_inspect(self, request: Mapping[str, Any]) -> dict:
        _reject_unknown(
            request, {"apiVersion", "requestId", "command", "sessionId"}, "request"
        )
        session = self._session(request)
        return {
            "sessionId": session.session_id,
            "document": document_dto(self.engine.inspect_document(session)),
            "state": session.state_name.value,
            "canUndo": session.state.can_undo,
            "canRedo": session.state.can_redo,
        }

    def _command_capabilities(self, request: Mapping[str, Any]) -> dict:
        return {"capabilities": self.engine.capabilities()}

    def _command_render(self, request: Mapping[str, Any]) -> dict:
        _reject_unknown(
            request,
            {"apiVersion", "requestId", "command", "sessionId", "pageId", "width"},
            "request",
        )
        session = self._session(request)
        result = self.engine.render_page(
            session, _string(request, "pageId"), int(_number(request, "width", 1000))
        )
        artifact_id = f"artifact_{uuid4().hex}"
        self.artifacts[artifact_id] = result.image_bytes
        return {
            "sessionId": session.session_id,
            "pageId": result.page_id,
            "width": result.width,
            "height": result.height,
            "cacheHit": result.cache_hit,
            "artifactId": artifact_id,
            "contentType": "image/png",
            "imageBase64": base64.b64encode(result.image_bytes).decode("ascii"),
        }

    def _command_apply(self, request: Mapping[str, Any]) -> dict:
        _reject_unknown(
            request,
            {"apiVersion", "requestId", "command", "sessionId", "operations", "dryRun"},
            "request",
        )
        session = self._session(request)
        operations = request.get("operations")
        if not isinstance(operations, list):
            raise InvalidRequestError("operations must be an array", field="operations")
        if not operations:
            raise InvalidRequestError("operations must not be empty", field="operations")
        dry_run = bool(request.get("dryRun", False))
        state = self.engine.apply_operations(
            session, [parse_operation(item) for item in operations], dry_run=dry_run
        )
        return {
            "sessionId": session.session_id,
            "dryRun": dry_run,
            "document": document_dto(self.engine.inspect_document(session))
            if not dry_run
            else _projected_dto(state),
            "canUndo": state.can_undo,
            "canRedo": state.can_redo,
        }

    def _command_undo(self, request: Mapping[str, Any]) -> dict:
        session = self._session(request)
        self.engine.undo(session)
        return self._command_inspect(
            {"sessionId": session.session_id, "requestId": "", "command": "inspect"}
        )

    def _command_redo(self, request: Mapping[str, Any]) -> dict:
        session = self._session(request)
        self.engine.redo(session)
        return self._command_inspect(
            {"sessionId": session.session_id, "requestId": "", "command": "inspect"}
        )

    def _command_save(self, request: Mapping[str, Any]) -> dict:
        _reject_unknown(
            request,
            {
                "apiVersion",
                "requestId",
                "command",
                "sessionId",
                "path",
                "allowReplaceSource",
                "dryRun",
            },
            "request",
        )
        session = self._session(request)
        dry_run = bool(request.get("dryRun", False))
        target = _string(request, "path", required=False)
        written = self.engine.save(
            session,
            target,
            SaveOptions(
                allow_replace_source=bool(request.get("allowReplaceSource", False)),
                dry_run=dry_run,
            ),
        )
        return {
            "sessionId": session.session_id,
            "path": str(written),
            "dryRun": dry_run,
            "written": not dry_run,
        }

    def _command_close(self, request: Mapping[str, Any]) -> dict:
        _reject_unknown(
            request, {"apiVersion", "requestId", "command", "sessionId"}, "request"
        )
        session = self._session(request)
        session_id = session.session_id
        self.engine.close(session)
        return {"sessionId": session_id, "closed": True}


def _projected_dto(state) -> dict:
    pages = state.projected_pages()
    return {
        "pageCount": len(pages),
        "title": state.projected_metadata().get("title"),
        "pages": [
            {
                "pageId": page.page_id,
                "index": index,
                "sourceIndex": page.source.info.index if page.source else None,
                "width": page.width,
                "height": page.height,
                "rotation": page.rotation,
            }
            for index, page in enumerate(pages)
        ],
    }


_DEFAULT: CommandDispatcher | None = None


def dispatch(payload: object, dispatcher: CommandDispatcher | None = None) -> dict:
    """Dispatch through a process-wide dispatcher unless one is supplied."""

    global _DEFAULT
    if dispatcher is None:
        if _DEFAULT is None:
            _DEFAULT = CommandDispatcher()
        dispatcher = _DEFAULT
    return dispatcher.dispatch(payload)
