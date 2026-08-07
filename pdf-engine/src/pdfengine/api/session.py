"""Per-document session state owned by the engine."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from pdfengine.document.pages import DocumentModel
from pdfengine.editing.state import DocumentState
from pdfengine.parser.reader import PdfReader
from pdfengine.parser.values import PdfName


_SAMPLE_BYTES = 65536


class SessionState(str, Enum):
    """The two states a session ID can be in."""

    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True)
class SessionTombstone:
    """What survives a close: enough to answer "that ID was yours", nothing more.

    Deliberately holds no reader, model, cache directory, or password. Those are
    released at close and must not be resurrectable from a tombstone.
    """

    session_id: str
    closed_at: float

    @property
    def state(self) -> SessionState:
        return SessionState.CLOSED


@dataclass(frozen=True)
class FileFingerprint:
    """Cheap identity for a file that must not change under an open session."""

    size: int
    mtime_ns: int
    digest: str

    @classmethod
    def of(cls, path: str | Path) -> "FileFingerprint":
        path = Path(path)
        stat = path.stat()
        with open(path, "rb") as handle:
            head = handle.read(_SAMPLE_BYTES)
            if stat.st_size > _SAMPLE_BYTES:
                handle.seek(max(0, stat.st_size - _SAMPLE_BYTES))
                tail = handle.read(_SAMPLE_BYTES)
            else:
                tail = b""
        digest = sha256(
            stat.st_size.to_bytes(8, "big") + head + tail
        ).hexdigest()
        return cls(size=stat.st_size, mtime_ns=stat.st_mtime_ns, digest=digest)

    def matches_content(self, other: "FileFingerprint") -> bool:
        """Compare only the bytes, ignoring a touched-but-identical file."""

        return (self.size, self.digest) == (other.size, other.digest)


@dataclass
class DocumentSession:
    """One opened document, its edit state, and its private cache directory."""

    session_id: str
    path: Path
    fingerprint: FileFingerprint
    reader: PdfReader
    model: DocumentModel
    state: DocumentState
    cache_dir: Path
    password: str | None = field(default=None, repr=False)
    closed: bool = False
    undecodable_survey: tuple[tuple[PdfName, ...], int] | None = field(
        default=None, repr=False
    )
    """Cached result of :meth:`DocumentModel.undecodable_streams`.

    ``None`` means the walk has not run yet. It is populated on the first
    capability request rather than at open time, so opening a large scanned
    document does not pay for a survey nobody asked for.
    """

    @property
    def state_name(self) -> SessionState:
        """The lifecycle state, kept separate from ``state`` (the edit history)."""

        return SessionState.CLOSED if self.closed else SessionState.OPEN

    @classmethod
    def open(
        cls,
        path: str | Path,
        cache_root: Path,
        password: str | None = None,
    ) -> "DocumentSession":
        path = Path(path).resolve()
        session_id = f"session_{uuid4().hex}"
        reader = PdfReader(path)
        model = DocumentModel.from_reader(reader)
        cache_dir = Path(cache_root) / session_id
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            session_id=session_id,
            path=path,
            fingerprint=FileFingerprint.of(path),
            reader=reader,
            model=model,
            state=DocumentState.from_model(model, session_id=session_id),
            cache_dir=cache_dir,
            password=password,
        )

    def source_changed(self) -> bool:
        if not self.path.exists():
            return True
        return not self.fingerprint.matches_content(FileFingerprint.of(self.path))

    def close(self) -> None:
        """Drop the password, delete the render cache, and mark the session closed."""

        self.password = None
        self.closed = True
        if self.cache_dir.is_dir():
            for entry in sorted(
                self.cache_dir.rglob("*"), key=lambda item: len(item.parts), reverse=True
            ):
                if entry.is_file():
                    entry.unlink(missing_ok=True)
                else:
                    entry.rmdir()
            os.rmdir(self.cache_dir)
