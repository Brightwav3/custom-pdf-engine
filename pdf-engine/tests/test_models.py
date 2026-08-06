from pathlib import Path

import pytest

from pdfengine import PdfEngineError
from pdfengine.api.models import (
    AddTextOperation,
    DocumentInfo,
    ExtractPagesOperation,
    MergeOperation,
    PageInfo,
    RenderResult,
    RotatePagesOperation,
    SaveOptions,
    SplitOperation,
)


def test_page_info_records_page_geometry() -> None:
    page = PageInfo(index=0, width=612.0, height=792.0, rotation=90)

    assert page.index == 0
    assert (page.width, page.height, page.rotation) == (612.0, 792.0, 90)


def test_document_info_owns_immutable_pages() -> None:
    pages = [PageInfo(index=0, width=612.0, height=792.0)]

    document = DocumentInfo(page_count=1, pages=pages, title="Sample")

    pages.append(PageInfo(index=1, width=612.0, height=792.0))
    assert document.pages == (PageInfo(index=0, width=612.0, height=792.0),)
    assert document.title == "Sample"


def test_render_result_exposes_rendered_page_data() -> None:
    result = RenderResult(page_index=0, width=306, height=396, image_bytes=b"PNG")

    assert result.page_index == 0
    assert result.image_bytes == b"PNG"
    assert (result.width, result.height) == (306, 396)


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        (MergeOperation(source_paths=(Path("a.pdf"), Path("b.pdf"))), "merge"),
        (SplitOperation(page_ranges=((0, 1),)), "split"),
        (ExtractPagesOperation(page_indices=(0, 2)), "extract_pages"),
        (RotatePagesOperation(page_indices=(0,), degrees=90), "rotate_pages"),
        (AddTextOperation(page_index=0, text="draft", x=12, y=24), "add_text"),
    ],
)
def test_operations_have_stable_kind(operation: object, expected: str) -> None:
    assert operation.kind == expected


def test_operation_kind_cannot_be_overridden_by_callers() -> None:
    with pytest.raises(TypeError):
        MergeOperation(source_paths=(Path("a.pdf"),), kind="not_merge")


def test_save_options_preserves_explicit_output_preferences() -> None:
    options = SaveOptions(output_path=Path("out.pdf"), overwrite=True, optimize=True)

    assert options.output_path == Path("out.pdf")
    assert options.overwrite is True
    assert options.optimize is True


def test_pdf_engine_error_is_a_runtime_error() -> None:
    error = PdfEngineError("bad pdf")

    assert isinstance(error, RuntimeError)
    assert str(error) == "bad pdf"


def test_basic_pdf_fixture_has_requested_page_count(basic_pdf) -> None:
    pdf = basic_pdf(2)

    assert pdf.startswith(b"%PDF-1.7\n")
    assert b"/Count 2" in pdf
    assert pdf.endswith(b"%%EOF\n")
