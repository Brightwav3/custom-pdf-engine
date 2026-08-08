"""A small, explicit, local-first PDF engine.

The public surface is :class:`PdfEngine` plus the immutable models in
``pdfengine.api.models``. The same contract is served over JSON by
``pdfengine.cli`` and ``pdfengine.service``.
"""

from .api.engine import PdfEngine
from .api.models import (
    AddTextLayer,
    CropPages,
    DeletePages,
    DocumentInfo,
    ExtractPages,
    ImportPages,
    InsertBlankPage,
    Operation,
    PageInfo,
    RenderResult,
    ReorderPages,
    RotatePages,
    SaveOptions,
    SetMetadata,
)
from .api.session import (
    DocumentSession,
    FileFingerprint,
    SessionState,
    SessionTombstone,
)
from .editing.state import DocumentState
from .errors import (
    InvalidOperationError,
    InvalidRequestError,
    OcrError,
    OcrUnavailableError,
    PdfEngineError,
    PdfParseError,
    RenderError,
    RendererUnavailableError,
    SessionNotFoundError,
    SessionStateError,
    SourceChangedError,
    UnsupportedOperationError,
    UnsupportedPdfError,
)

__version__ = "0.2.0"

__all__ = [
    "AddTextLayer",
    "CropPages",
    "DeletePages",
    "DocumentInfo",
    "DocumentSession",
    "DocumentState",
    "ExtractPages",
    "FileFingerprint",
    "ImportPages",
    "InsertBlankPage",
    "InvalidOperationError",
    "InvalidRequestError",
    "OcrError",
    "OcrUnavailableError",
    "Operation",
    "PageInfo",
    "PdfEngine",
    "PdfEngineError",
    "PdfParseError",
    "RenderError",
    "RenderResult",
    "RendererUnavailableError",
    "ReorderPages",
    "RotatePages",
    "SaveOptions",
    "SessionNotFoundError",
    "SessionState",
    "SessionStateError",
    "SessionTombstone",
    "SetMetadata",
    "SourceChangedError",
    "UnsupportedOperationError",
    "UnsupportedPdfError",
    "__version__",
]
