"""Structured exceptions raised across every public PDF engine surface."""


class PdfEngineError(RuntimeError):
    """Base exception raised for PDF engine failures."""

    code = "engine_error"


class PdfParseError(PdfEngineError):
    """Raised when PDF syntax cannot be parsed."""

    code = "parse_error"

    def __init__(self, message: str, offset: int) -> None:
        super().__init__(f"{message} at byte offset {offset}")
        self.offset = offset


class UnsupportedPdfError(PdfParseError):
    """Raised when a document uses a construct outside the v0.1 subset.

    It subclasses :class:`PdfParseError` so callers that only care that a
    document could not be read keep working, while callers that want to tell
    the user *which* feature blocked them can catch this instead.
    """

    code = "unsupported_pdf"

    def __init__(self, feature: str, offset: int = 0) -> None:
        super().__init__(f"unsupported PDF feature: {feature}", offset)
        self.feature = feature


class UnsupportedOperationError(PdfEngineError):
    """Raised when an operation cannot be applied to the current document."""

    code = "unsupported_operation"


class InvalidOperationError(PdfEngineError):
    """Raised when an operation is well-formed but invalid for this state."""

    code = "invalid_operation"


class InvalidRequestError(PdfEngineError):
    """Raised when an external request payload is malformed."""

    code = "invalid_request"

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


class RendererUnavailableError(PdfEngineError):
    """Raised when no working page renderer is installed."""

    code = "renderer_unavailable"


class RenderError(PdfEngineError):
    """Raised when a renderer is present but fails to produce an image."""

    code = "render_error"


class SourceChangedError(PdfEngineError):
    """Raised when the opened source file changed underneath the session."""

    code = "source_changed"


class SessionNotFoundError(PdfEngineError):
    """Raised when a request names a session that is closed or unknown."""

    code = "session_not_found"
