from pathlib import Path

import pytest

from pdfengine import PdfEngineError
from pdfengine.api.models import (
    CropPages,
    DeletePages,
    DocumentInfo,
    ExtractPages,
    ImportPages,
    InsertBlankPage,
    PageInfo,
    RenderResult,
    ReorderPages,
    RotatePages,
    SaveOptions,
    SetMetadata,
)


def test_page_info_records_page_geometry_and_stable_identity() -> None:
    page = PageInfo(
        index=0, width=612.0, height=792.0, rotation=90, page_id="page_a", source_index=3
    )

    assert page.index == 0
    assert (page.width, page.height, page.rotation) == (612.0, 792.0, 90)
    assert (page.page_id, page.source_index) == ("page_a", 3)


def test_document_info_owns_immutable_pages() -> None:
    pages = [PageInfo(index=0, width=612.0, height=792.0)]

    document = DocumentInfo(page_count=1, pages=pages, title="Sample")

    pages.append(PageInfo(index=1, width=612.0, height=792.0))
    assert document.pages == (PageInfo(index=0, width=612.0, height=792.0),)
    assert document.title == "Sample"


def test_render_result_exposes_rendered_page_data() -> None:
    result = RenderResult(page_id="page_a", width=306, height=396, image_bytes=b"PNG")

    assert result.page_id == "page_a"
    assert result.image_bytes == b"PNG"
    assert (result.width, result.height, result.cache_hit) == (306, 396, False)


def test_rotate_operation_requires_stable_page_ids() -> None:
    operation = RotatePages(page_ids=("page_a",), degrees=90)

    assert operation.page_ids == ("page_a",)
    assert operation.degrees == 90


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        (RotatePages(page_ids=("page_a",), degrees=90), "rotate_pages"),
        (DeletePages(page_ids=("page_a",)), "delete_pages"),
        (ReorderPages(page_ids=("page_a",)), "reorder_pages"),
        (ExtractPages(page_ids=("page_a",)), "extract_pages"),
        (InsertBlankPage(), "insert_blank_page"),
        (CropPages(page_ids=("page_a",), box=(0, 0, 10, 10)), "crop_pages"),
        (SetMetadata(entries={"title": "Draft"}), "set_metadata"),
        (ImportPages(source_session_id="s1", page_ids=("page_b",)), "import_pages"),
    ],
)
def test_operations_have_stable_kind(operation: object, expected: str) -> None:
    assert operation.kind == expected


def test_operation_kind_cannot_be_overridden_by_callers() -> None:
    with pytest.raises(TypeError):
        DeletePages(page_ids=("page_a",), kind="not_delete")


@pytest.mark.parametrize(
    "create",
    [
        lambda values: RotatePages(page_ids=values, degrees=90),
        lambda values: DeletePages(page_ids=values),
        lambda values: ReorderPages(page_ids=values),
        lambda values: ExtractPages(page_ids=values),
    ],
)
def test_operations_do_not_retain_mutable_input_collections(create) -> None:
    values = ["page_a"]

    operation = create(values)
    values.append("page_b")

    assert operation.page_ids == ("page_a",)


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda: RotatePages(page_ids=(), degrees=90), "must not be empty"),
        (lambda: RotatePages(page_ids=("page_a",), degrees=45), "90, 180, or 270"),
        (lambda: DeletePages(page_ids=("page_a", "page_a")), "must not repeat"),
        (lambda: ReorderPages(page_ids="page_a"), "sequence of page IDs"),
        (lambda: InsertBlankPage(width=0), "positive points"),
        (lambda: CropPages(page_ids=("page_a",), box=(0, 0, 0, 10)), "non-empty"),
        (lambda: SetMetadata(entries={}), "at least one entry"),
        (lambda: SetMetadata(entries={"colour": "red"}), "unsupported metadata"),
        (lambda: ImportPages(source_session_id="", page_ids=("page_a",)), "source session"),
    ],
)
def test_operations_reject_invalid_arguments(build, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        build()


def test_blank_page_carries_a_stable_generated_id() -> None:
    first = InsertBlankPage()
    second = InsertBlankPage()

    assert first.page_id.startswith("page_") and first.page_id != second.page_id
    assert InsertBlankPage(page_id="page_fixed").page_id == "page_fixed"


def test_save_options_preserves_explicit_output_preferences() -> None:
    options = SaveOptions(output_path=Path("out.pdf"), allow_replace_source=True)

    assert options.output_path == Path("out.pdf")
    assert options.allow_replace_source is True
    assert options.dry_run is False


def test_pdf_engine_error_is_a_runtime_error() -> None:
    error = PdfEngineError("bad pdf")

    assert isinstance(error, RuntimeError)
    assert str(error) == "bad pdf"


def test_basic_pdf_fixture_has_requested_page_count(basic_pdf) -> None:
    pdf = basic_pdf(2)

    assert pdf.startswith(b"%PDF-1.7\n")
    assert b"/Count 2" in pdf
    assert pdf.endswith(b"%%EOF\n")
