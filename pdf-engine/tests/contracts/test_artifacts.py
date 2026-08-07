"""Artifacts are descriptors over storage, never a bag of bytes."""

from __future__ import annotations

from hashlib import sha256

import pytest

from pdfengine.api.artifacts import (
    ARTIFACT_KINDS,
    ArtifactRegistry,
    CacheArtifact,
    FileArtifact,
    MemoryArtifact,
)
from pdfengine.errors import InvalidRequestError


@pytest.fixture
def registry() -> ArtifactRegistry:
    return ArtifactRegistry()


def test_a_memory_artifact_describes_its_own_bytes(registry) -> None:
    artifact = registry.register(
        kind="page_render",
        content_type="image/png",
        session_id="session_a",
        storage=MemoryArtifact(b"pixels"),
    )

    assert artifact.byte_size == 6
    assert artifact.sha256 == sha256(b"pixels").hexdigest()
    assert artifact.read() == b"pixels"


def test_a_file_artifact_reads_from_disk_without_copying_into_the_descriptor(
    registry, tmp_path
) -> None:
    path = tmp_path / "saved.pdf"
    path.write_bytes(b"%PDF-1.7 saved")

    artifact = registry.register(
        kind="saved_document",
        content_type="application/pdf",
        session_id="session_a",
        storage=FileArtifact(path),
    )

    assert artifact.byte_size == 14
    assert artifact.read() == b"%PDF-1.7 saved"


def test_the_public_dto_never_leaks_storage_or_paths(registry, tmp_path) -> None:
    path = tmp_path / "saved.pdf"
    path.write_bytes(b"%PDF-1.7")
    artifact = registry.register(
        kind="saved_document",
        content_type="application/pdf",
        session_id="session_a",
        storage=FileArtifact(path),
    )

    dto = artifact.as_dict()

    assert set(dto) == {
        "artifactId",
        "kind",
        "contentType",
        "byteSize",
        "sha256",
        "sessionId",
        "metadata",
    }
    assert "storage" not in dto
    assert str(path) not in repr(dto)


def test_retrieval_enforces_session_ownership(registry) -> None:
    artifact = registry.register(
        kind="page_render",
        content_type="image/png",
        session_id="session_owner",
        storage=MemoryArtifact(b"pixels"),
    )

    with pytest.raises(InvalidRequestError) as caught:
        registry.get(artifact.artifact_id, session_id="session_other")

    assert caught.value.field == "artifactId"
    assert "session_owner" not in str(caught.value)


def test_an_unknown_artifact_id_fails_the_same_way_as_a_foreign_one(registry) -> None:
    artifact = registry.register(
        kind="page_render",
        content_type="image/png",
        session_id="session_owner",
        storage=MemoryArtifact(b"pixels"),
    )

    with pytest.raises(InvalidRequestError) as absent:
        registry.get("artifact_never_issued", session_id="session_owner")
    with pytest.raises(InvalidRequestError) as wrong_owner:
        registry.get(artifact.artifact_id, session_id="session_other")

    # A caller must not be able to tell "no such artifact" apart from "not
    # yours": anything observable about the two failures has to match, or the
    # difference itself becomes a probe for another session's artifacts.
    assert str(absent.value) == str(wrong_owner.value)
    assert type(absent.value) is type(wrong_owner.value)
    assert absent.value.field == wrong_owner.value.field
    assert absent.value.code == wrong_owner.value.code
    assert absent.value.args == wrong_owner.value.args
    assert artifact.artifact_id not in str(wrong_owner.value)
    assert "session_owner" not in str(wrong_owner.value)


def test_closing_a_session_forgets_temporary_artifacts(registry, tmp_path) -> None:
    cached = tmp_path / "render.png"
    cached.write_bytes(b"pixels")
    temporary = registry.register(
        kind="page_render",
        content_type="image/png",
        session_id="session_a",
        storage=CacheArtifact(cached),
    )

    registry.forget_session("session_a")

    with pytest.raises(InvalidRequestError):
        registry.get(temporary.artifact_id, session_id="session_a")


def test_closing_one_session_leaves_another_sessions_artifacts_alone(registry) -> None:
    kept = registry.register(
        kind="page_render",
        content_type="image/png",
        session_id="session_b",
        storage=MemoryArtifact(b"pixels"),
    )

    registry.forget_session("session_a")

    assert registry.get(kept.artifact_id, session_id="session_b") is kept


def test_closing_a_session_never_deletes_a_committed_saved_document(
    registry, tmp_path
) -> None:
    saved = tmp_path / "output.pdf"
    saved.write_bytes(b"%PDF-1.7 committed")
    registry.register(
        kind="saved_document",
        content_type="application/pdf",
        session_id="session_a",
        storage=FileArtifact(saved),
    )

    registry.forget_session("session_a")

    assert saved.exists()
    assert saved.read_bytes() == b"%PDF-1.7 committed"


def test_only_documented_kinds_are_accepted(registry) -> None:
    assert ARTIFACT_KINDS == ("page_render", "thumbnail", "saved_document")

    with pytest.raises(ValueError):
        registry.register(
            kind="extracted_document",
            content_type="application/pdf",
            session_id="session_a",
            storage=MemoryArtifact(b""),
        )
