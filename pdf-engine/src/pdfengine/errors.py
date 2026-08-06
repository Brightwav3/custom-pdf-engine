class PdfEngineError(RuntimeError):
    """Base exception raised for PDF engine failures."""


class PdfParseError(PdfEngineError):
    """Raised when PDF syntax cannot be parsed."""

    def __init__(self, message: str, offset: int) -> None:
        super().__init__(f"{message} at byte offset {offset}")
        self.offset = offset
