"""Stable descriptions of what the engine produced, over where it lives.

An artifact is a *descriptor*, not a payload. A rendered page is small and sits
in the session cache; a saved document is a real file the caller asked for and
may be hundreds of megabytes. Re-reading that file into memory just to call it
an artifact would be absurd, so storage is a strategy and the descriptor is the
only thing the contract exposes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable
from uuid import uuid4

from pdfengine.errors import InvalidRequestError


ARTIFACT_KINDS: tuple[str, ...] = ("page_render", "thumbnail", "saved_document")
"""Every artifact kind v0.2 emits.

Extensible by design: adding a kind is an additive contract change. Callers must
treat an unrecognized kind as opaque rather than an error.
"""


@runtime_checkable
class ArtifactStorage(Protocol):
    """Where an artifact's bytes actually are."""

    temporary: bool
    """Whether closing the owning session may discard the underlying bytes."""

    def read(self) -> bytes:
        """Return the artifact's bytes."""

    @property
    def byte_size(self) -> int:
        """The artifact's size without necessarily reading it."""


@dataclass(frozen=True)
class MemoryArtifact:
    """Bytes held in this process. Small and transient by definition."""

    data: bytes
    temporary: bool = True

    def read(self) -> bytes:
        return self.data

    @property
    def byte_size(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class CacheArtifact:
    """A file inside a session's cache directory. Goes away with the session."""

    path: Path
    temporary: bool = True

    def read(self) -> bytes:
        return Path(self.path).read_bytes()

    @property
    def byte_size(self) -> int:
        return Path(self.path).stat().st_size


@dataclass(frozen=True)
class FileArtifact:
    """A file the caller committed to. Outlives the session that made it."""

    path: Path
    temporary: bool = False

    def read(self) -> bytes:
        return Path(self.path).read_bytes()

    @property
    def byte_size(self) -> int:
        return Path(self.path).stat().st_size


@dataclass(frozen=True)
class Artifact:
    """One stable, public description of something the engine produced."""

    artifact_id: str
    kind: str
    content_type: str
    byte_size: int
    sha256: str
    session_id: str
    storage: ArtifactStorage
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def read(self) -> bytes:
        return self.storage.read()

    def as_dict(self) -> dict:
        """The wire form. Deliberately omits ``storage``.

        Where the bytes live is exactly the cache internal the contract promises
        not to expose, so it never reaches a caller.
        """

        return {
            "artifactId": self.artifact_id,
            "kind": self.kind,
            "contentType": self.content_type,
            "byteSize": self.byte_size,
            "sha256": self.sha256,
            "sessionId": self.session_id,
            "metadata": dict(self.metadata),
        }


class ArtifactRegistry:
    """Every artifact the engine has issued, indexed by ID and owning session."""

    def __init__(self) -> None:
        self._artifacts: dict[str, Artifact] = {}

    def register(
        self,
        kind: str,
        content_type: str,
        session_id: str,
        storage: ArtifactStorage,
        metadata: Mapping[str, Any] | None = None,
    ) -> Artifact:
        """Describe a new artifact and remember it for its owning session.

        Note this reads the bytes once, in full, to compute ``sha256``. For a
        large ``FileArtifact`` that is a whole-file read. v0.2 accepts the cost:
        a digest callers can trust matters more than a streaming implementation.
        """

        if kind not in ARTIFACT_KINDS:
            raise ValueError(
                f"unknown artifact kind {kind!r}; supported: "
                + ", ".join(ARTIFACT_KINDS)
            )
        data = storage.read()
        artifact = Artifact(
            artifact_id=f"artifact_{uuid4().hex}",
            kind=kind,
            content_type=content_type,
            byte_size=storage.byte_size,
            sha256=sha256(data).hexdigest(),
            session_id=session_id,
            storage=storage,
            metadata=dict(metadata or {}),
        )
        self._artifacts[artifact.artifact_id] = artifact
        return artifact

    def get(self, artifact_id: str, session_id: str) -> Artifact:
        """Return an artifact only to the session that owns it.

        An unguessable ID is not an authorization mechanism. A missing artifact
        and a foreign one produce the *same* error, so a caller cannot probe for
        the existence of another session's artifacts.
        """

        artifact = self._artifacts.get(artifact_id)
        if artifact is None or artifact.session_id != session_id:
            raise InvalidRequestError("unknown artifact", field="artifactId")
        return artifact

    def forget_session(self, session_id: str) -> None:
        """Drop a closed session's descriptors.

        Temporary storage disappears with the session's cache directory, which
        the session deletes itself. A committed ``FileArtifact`` is a file the
        caller asked for: this forgets the descriptor and never touches the file.
        """

        for artifact_id, artifact in list(self._artifacts.items()):
            if artifact.session_id == session_id:
                del self._artifacts[artifact_id]
