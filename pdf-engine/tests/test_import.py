from __future__ import annotations

from pathlib import Path

import pytest

from pdfengine.api.models import ImportPages, SaveOptions
from pdfengine.document import DocumentModel
from pdfengine.editing import DocumentState
from pdfengine.errors import InvalidOperationError, PdfEngineError
from pdfengine.parser.reader import PdfReader
from pdfengine.parser.values import PdfName
from pdfengine.writing import FullRewriteWriter

from conftest import assemble_pdf
from test_rewrite import page_texts


def _open(path: Path, session_id: str):
    reader = PdfReader(path)
    return DocumentModel.from_reader(reader), reader


@pytest.fixture
def merged(write_pdf, tmp_path):
    """Return a helper that merges pages from a second document into a first."""

    def build(first_texts, second_texts, page_slice=slice(None), after=None):
        first_path = write_pdf(first_texts)
        second_path = write_pdf(second_texts)
        first_model, first_reader = _open(first_path, "s-a")
        second_model, second_reader = _open(second_path, "s-b")

        state = DocumentState.from_model(first_model, session_id="s-a").with_source(
            "s-b", second_model
        )
        page_ids = tuple(page.id for page in second_model.pages)[page_slice]
        anchor = state.page_ids[after] if after is not None else None
        state = state.apply(ImportPages("s-b", page_ids, after_page_id=anchor))

        output = FullRewriteWriter().write(
            state,
            {"s-a": first_reader, "s-b": second_reader},
            tmp_path / "merged.pdf",
            SaveOptions(),
        )
        return output, first_path, second_path

    return build


def test_merge_imports_foreign_page_with_its_content(merged) -> None:
    output, _first, _second = merged(["only page"], ["from second document"])

    assert DocumentModel.from_reader(PdfReader(output)).info.page_count == 2
    assert page_texts(output) == ["only page", "from second document"]


def test_imported_pages_land_at_the_requested_position(merged) -> None:
    output, _first, _second = merged(["a", "b"], ["x"], after=0)

    assert page_texts(output) == ["a", "x", "b"]


def test_imported_pages_keep_their_own_resources(merged) -> None:
    output, _first, _second = merged(["a"], ["x"])

    reader = PdfReader(output)
    model = DocumentModel.from_reader(reader)
    resources = model.pages[1].resources
    assert resources is not None
    font = reader.resolve(resources.entries[PdfName("Font")].entries[PdfName("F1")])
    assert font.entries[PdfName("BaseFont")] == PdfName("Helvetica")


def test_imported_objects_are_renumbered_so_they_cannot_collide(merged) -> None:
    output, _first, _second = merged(["a", "b", "c"], ["x", "y"], page_slice=slice(0, 2))

    reader = PdfReader(output)
    model = DocumentModel.from_reader(reader)
    references = [page.reference for page in model.pages]
    assert len(set(references)) == len(references) == 5
    assert page_texts(output) == ["a", "b", "c", "x", "y"]


def test_source_documents_are_unchanged_by_a_merge(merged, write_pdf) -> None:
    first_path = write_pdf(["a"])
    second_path = write_pdf(["x"])
    before = (first_path.read_bytes(), second_path.read_bytes())

    output, _f, _s = merged(["a"], ["x"])

    assert output.exists()
    assert (first_path.read_bytes(), second_path.read_bytes()) == before


def test_importing_from_an_unregistered_session_is_rejected(write_pdf) -> None:
    model, _reader = _open(write_pdf(["a"]), "s-a")
    state = DocumentState.from_model(model, session_id="s-a")

    with pytest.raises(InvalidOperationError, match="unknown import source"):
        state.apply(ImportPages("s-missing", ("page_x",)))


def test_saving_an_import_without_its_reader_is_a_clear_failure(
    write_pdf, tmp_path
) -> None:
    first_model, first_reader = _open(write_pdf(["a"]), "s-a")
    second_model, _second_reader = _open(write_pdf(["x"]), "s-b")
    state = DocumentState.from_model(first_model, session_id="s-a").with_source(
        "s-b", second_model
    )
    state = state.apply(ImportPages("s-b", (second_model.pages[0].id,)))

    with pytest.raises(PdfEngineError, match="no open source document"):
        FullRewriteWriter().write(
            state, {"s-a": first_reader}, tmp_path / "out.pdf", SaveOptions()
        )


def test_a_document_using_an_unsupported_construct_cannot_be_imported(tmp_path) -> None:
    path = tmp_path / "lzw.pdf"
    path.write_bytes(
        assemble_pdf(
            [
                b"<< /Type /Catalog /Pages 2 0 R >>",
                b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] /Contents 4 0 R >>",
                b"<< /Length 3 /Filter /LZWDecode >>\nstream\nraw\nendstream",
            ]
        )
    )

    from pdfengine.errors import UnsupportedPdfError

    model, reader = _open(path, "s-a")
    state = DocumentState.from_model(model, session_id="s-a")
    with pytest.raises(UnsupportedPdfError, match="stream filter"):
        FullRewriteWriter().write(state, {"s-a": reader}, tmp_path / "o.pdf", SaveOptions())
