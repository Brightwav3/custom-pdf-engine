"""A small, explicit, local-first PDF engine.

The public surface is :class:`PdfEngine` plus the immutable models in
``pdfengine.api.models``. The same contract is served over JSON by
``pdfengine.cli`` and ``pdfengine.service``.
"""

from .api.engine import PdfEngine
from .api.models import (
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
from .api.session import DocumentSession, FileFingerprint
from .errors import (
    InvalidOperationError,
    InvalidRequestError,
    PdfEngineError,
    PdfParseError,
    RenderError,
    RendererUnavailableError,
    SessionNotFoundError,
    SourceChangedError,
    UnsupportedOperationError,
    UnsupportedPdfError,
)

__version__ = "0.1.0"

__all__ = [
    "CropPages",
    "DeletePages",
    "DocumentInfo",
    "DocumentSession",
    "ExtractPages",
    "FileFingerprint",
    "ImportPages",
    "InsertBlankPage",
    "InvalidOperationError",
    "InvalidRequestError",
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
    "SetMetadata",
    "SourceChangedError",
    "UnsupportedOperationError",
    "UnsupportedPdfError",
    "__version__",
]
