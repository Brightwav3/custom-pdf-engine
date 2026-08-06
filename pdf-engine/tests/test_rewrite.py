from __future__ import annotations

import re
from pathlib import Path

import pytest

from pdfengine.api.models import (
    CropPages,
    DeletePages,
    ExtractPages,
    InsertBlankPage,
    ReorderPages,
    RotatePages,
    SaveOptions,
    SetMetadata,
)
from pdfengine.document import DocumentModel
from pdfengine.editing import DocumentState
from pdfengine.errors import PdfEngineError
from pdfengine.parser.reader import PdfReader, PdfStream
from pdfengine.parser.values import PdfDictionary, PdfName, PdfReference
from pdfengine.writing import FullRewriteWriter


def open_state(path: Path, session_id: str = "s-main") -> tuple[DocumentState, dict]:
    reader = PdfReader(path)
    model = DocumentModel.from_reader(reader)
    return DocumentState.from_model(model, session_id=session_id), {session_id: reader}


def save(state: DocumentState, readers: dict, target: Path, **kwargs) -> Path:
    return FullRewriteWriter().write(state, readers, target, SaveOptions(**kwargs))


def page_texts(path: Path) -> list[str]:
    """Read back the visible text of every page, in document order."""

    reader = PdfReader(path)
    model = DocumentModel.from_reader(reader)
    texts = []
    for record in model.pages:
        page = reader.resolve(record.reference)
        assert isinstance(page, PdfDictionary)
        contents = reader.resolve(page.entries[PdfName("Contents")])
        assert isinstance(contents, PdfStream)
        found = re.search(rb"\((.*)\) Tj", contents.data)
        texts.append(found.group(1).decode("ascii") if found else "")
    return texts


@pytest.fixture
def source(write_pdf) -> Path:
    return write_pdf(["alpha", "beta", "gamma"], title="Original")


def test_rewrite_saves_reordered_copy_and_keeps_source_bytes(source, tmp_path) -> None:
    before = source.read_bytes()
    state, readers = open_state(source)
    page_a, page_b, page_c = state.page_ids

    output = save(
        state.apply(ReorderPages((page_c, page_a, page_b))), readers, tmp_path / "e.pdf"
    )

    assert source.read_bytes() == before
    assert page_texts(output) == ["gamma", "alpha", "beta"]


def test_deleted_pages_are_not_retained_in_the_output(source, tmp_path) -> None:
    state, readers = open_state(source)
    page_a, _page_b, page_c = state.page_ids

    output = save(state.apply(DeletePages((page_a, page_c))), readers, tmp_path / "e.pdf")

    assert page_texts(output) == ["beta"]
    assert b"alpha" not in output.read_bytes()
    assert b"gamma" not in output.read_bytes()


def test_rotation_survives_the_round_trip(source, tmp_path) -> None:
    state, readers = open_state(source)
    page_b = state.page_ids[1]

    output = save(state.apply(RotatePages((page_b,), 270)), readers, tmp_path / "e.pdf")

    model = DocumentModel.from_reader(PdfReader(output))
    assert [page.info.rotation for page in model.pages] == [0, 270, 0]


def test_crop_changes_the_reopened_page_dimensions(source, tmp_path) -> None:
    state, readers = open_state(source)
    page_a = state.page_ids[0]

    output = save(
        state.apply(CropPages((page_a,), (10, 20, 210, 320))), readers, tmp_path / "e.pdf"
    )

    model = DocumentModel.from_reader(PdfReader(output))
    assert (model.pages[0].info.width, model.pages[0].info.height) == (200.0, 300.0)
    assert model.pages[0].crop_box == (10.0, 20.0, 210.0, 320.0)


def test_inserted_blank_page_is_a_real_reopenable_page(source, tmp_path) -> None:
    state, readers = open_state(source)
    page_a = state.page_ids[0]
    blank = InsertBlankPage(after_page_id=page_a, width=300, height=500)

    output = save(state.apply(blank), readers, tmp_path / "e.pdf")

    model = DocumentModel.from_reader(PdfReader(output))
    assert model.info.page_count == 4
    assert (model.pages[1].info.width, model.pages[1].info.height) == (300.0, 500.0)
    assert page_texts(output) == ["alpha", "", "beta", "gamma"]


def test_extract_writes_only_the_named_pages(source, tmp_path) -> None:
    state, readers = open_state(source)
    page_a, _page_b, page_c = state.page_ids

    output = save(state.apply(ExtractPages((page_c, page_a))), readers, tmp_path / "e.pdf")

    assert page_texts(output) == ["gamma", "alpha"]


def test_metadata_edits_reach_the_saved_document(source, tmp_path) -> None:
    state, readers = open_state(source)

    output = save(
        state.apply(SetMetadata({"title": "Renamed"})), readers, tmp_path / "e.pdf"
    )

    assert DocumentModel.from_reader(PdfReader(output)).info.title == "Renamed"


def test_cleared_metadata_leaves_nothing_behind(source, tmp_path) -> None:
    state, readers = open_state(source)

    output = save(state.apply(SetMetadata({"title": None})), readers, tmp_path / "e.pdf")

    assert DocumentModel.from_reader(PdfReader(output)).info.title is None
    assert b"Original" not in output.read_bytes()


def test_page_resources_are_preserved_so_content_still_renders(source, tmp_path) -> None:
    state, readers = open_state(source)

    output = save(state, readers, tmp_path / "e.pdf")

    reader = PdfReader(output)
    model = DocumentModel.from_reader(reader)
    resources = model.pages[0].resources
    assert resources is not None
    font = reader.resolve(resources.entries[PdfName("Font")].entries[PdfName("F1")])
    assert font.entries[PdfName("BaseFont")] == PdfName("Helvetica")


def test_an_unedited_save_round_trips_every_page(source, tmp_path) -> None:
    state, readers = open_state(source)

    output = save(state, readers, tmp_path / "copy.pdf")

    assert page_texts(output) == ["alpha", "beta", "gamma"]
    assert DocumentModel.from_reader(PdfReader(output)).info.title == "Original"


def test_saving_over_an_existing_file_requires_explicit_permission(
    source, tmp_path
) -> None:
    state, readers = open_state(source)
    existing = tmp_path / "taken.pdf"
    existing.write_bytes(b"keep me")

    with pytest.raises(PdfEngineError, match="refusing to overwrite"):
        save(state, readers, existing)

    assert existing.read_bytes() == b"keep me"
    assert save(state, readers, existing, allow_replace_source=True) == existing


def test_a_missing_output_directory_is_rejected(source, tmp_path) -> None:
    state, readers = open_state(source)

    with pytest.raises(PdfEngineError, match="output directory"):
        save(state, readers, tmp_path / "absent" / "e.pdf")


def _streams(path: Path) -> list[PdfStream]:
    """Every stream reachable by object number in a document, in order."""

    reader = PdfReader(path)
    found = []
    for number in range(1, reader.trailer.entries[PdfName("Size")]):
        try:
            value = reader.resolve(PdfReference(number, 0))
        except PdfEngineError:
            continue
        if isinstance(value, PdfStream):
            found.append(value)
    return found


def test_an_unedited_save_reproduces_every_stream_body_byte_for_byte(
    source, tmp_path
) -> None:
    state, readers = open_state(source)

    output = save(state, readers, tmp_path / "copy.pdf")

    before = [stream.raw for stream in _streams(source)]
    after = [stream.raw for stream in _streams(output)]
    assert before
    assert after == before


def test_an_undecodable_image_survives_a_reorder_unchanged(tmp_path) -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "basic" / "with-image.pdf"

    def image_bytes(path: Path) -> bytes:
        reader = PdfReader(path)
        model = DocumentModel.from_reader(reader)
        page = reader.resolve(model.pages[0].reference)
        xobjects = page.entries[PdfName("Resources")].entries[PdfName("XObject")]
        image = reader.resolve(xobjects.entries[PdfName("Im0")])
        assert image.residual_filters == (PdfName("DCTDecode"),)
        return image.raw

    state, readers = open_state(fixture)
    output = save(state.apply(ReorderPages(state.page_ids)), readers, tmp_path / "e.pdf")

    assert image_bytes(output) == image_bytes(fixture)


def test_no_temporary_file_survives_a_successful_save(source, tmp_path) -> None:
    state, readers = open_state(source)

    save(state, readers, tmp_path / "e.pdf")

    assert not list(tmp_path.glob("*.tmp"))
