from __future__ import annotations

import pytest

from pdfengine.api.models import (
    CropPages,
    DeletePages,
    ExtractPages,
    ImportPages,
    InsertBlankPage,
    ReorderPages,
    RotatePages,
    SetMetadata,
)
from pdfengine.document import DocumentModel
from pdfengine.editing import DocumentState
from pdfengine.errors import InvalidOperationError
from pdfengine.parser.reader import PdfReader


@pytest.fixture
def three_page_state(write_pdf) -> DocumentState:
    model = DocumentModel.from_reader(PdfReader(write_pdf(["a", "b", "c"], title="Draft")))
    return DocumentState.from_model(model, session_id="s-main")


def _ids(state: DocumentState) -> tuple[str, ...]:
    return state.page_ids


def test_new_operation_after_undo_discards_redo_branch(three_page_state) -> None:
    page_a, page_b, page_c = _ids(three_page_state)

    rotated = three_page_state.apply(RotatePages((page_b,), 90))
    undone = rotated.undo()
    result = undone.apply(DeletePages((page_a,)))

    assert result.page_ids == (page_b, page_c)
    assert result.redo() == result
    assert result.projected_pages()[0].rotation == 0


def test_original_state_is_untouched_by_later_operations(three_page_state) -> None:
    page_a, page_b, _page_c = _ids(three_page_state)

    three_page_state.apply(DeletePages((page_a,))).apply(RotatePages((page_b,), 90))

    assert len(three_page_state.page_ids) == 3
    assert three_page_state.operations == ()
    assert three_page_state.projected_pages()[0].rotation == 0


def test_rotation_accumulates_and_wraps(three_page_state) -> None:
    page_a = _ids(three_page_state)[0]

    state = three_page_state.apply(RotatePages((page_a,), 270)).apply(
        RotatePages((page_a,), 180)
    )

    assert state.projected_pages()[0].rotation == 90


def test_reorder_produces_the_requested_permutation(three_page_state) -> None:
    page_a, page_b, page_c = _ids(three_page_state)

    state = three_page_state.apply(ReorderPages((page_c, page_a, page_b)))

    assert state.page_ids == (page_c, page_a, page_b)


def test_extract_keeps_only_the_named_pages_in_order(three_page_state) -> None:
    page_a, _page_b, page_c = _ids(three_page_state)

    state = three_page_state.apply(ExtractPages((page_c, page_a)))

    assert state.page_ids == (page_c, page_a)


def test_blank_page_is_inserted_after_the_named_page(three_page_state) -> None:
    page_a, page_b, page_c = _ids(three_page_state)
    operation = InsertBlankPage(after_page_id=page_a, width=200, height=400)

    state = three_page_state.apply(operation)

    assert state.page_ids == (page_a, operation.page_id, page_b, page_c)
    blank = state.projected_pages()[1]
    assert blank.is_blank is True
    assert (blank.width, blank.height) == (200.0, 400.0)


def test_blank_page_without_an_anchor_goes_to_the_front(three_page_state) -> None:
    operation = InsertBlankPage()

    state = three_page_state.apply(operation)

    assert state.page_ids[0] == operation.page_id


def test_crop_is_clipped_to_the_page_media_box(three_page_state) -> None:
    page_a = _ids(three_page_state)[0]

    state = three_page_state.apply(CropPages((page_a,), (-10, -10, 300, 400)))

    assert state.projected_pages()[0].crop_box == (0.0, 0.0, 300.0, 400.0)
    assert (state.projected_pages()[0].width, state.projected_pages()[0].height) == (
        300.0,
        400.0,
    )


def test_metadata_edits_project_over_the_source_document(three_page_state) -> None:
    assert three_page_state.projected_metadata()["title"] == "Draft"

    state = three_page_state.apply(SetMetadata({"title": "Final", "author": "Ada"}))

    assert state.projected_metadata()["title"] == "Final"
    assert state.projected_metadata()["author"] == "Ada"
    assert state.undo().projected_metadata()["title"] == "Draft"


def test_metadata_entry_can_be_cleared(three_page_state) -> None:
    state = three_page_state.apply(SetMetadata({"title": None}))

    assert state.projected_metadata()["title"] is None


def test_undo_and_redo_walk_the_operation_log(three_page_state) -> None:
    page_a = _ids(three_page_state)[0]
    state = three_page_state.apply(DeletePages((page_a,)))

    assert (state.can_undo, state.can_redo) == (True, False)
    assert state.undo().page_ids == three_page_state.page_ids
    assert state.undo().redo().page_ids == state.page_ids
    assert three_page_state.undo() == three_page_state


def test_imported_pages_land_after_the_named_anchor(three_page_state, write_pdf) -> None:
    other = DocumentModel.from_reader(PdfReader(write_pdf(["from second document"])))
    state = three_page_state.with_source("s-other", other)
    page_a = _ids(state)[0]
    imported_id = other.pages[0].id

    merged = state.apply(ImportPages("s-other", (imported_id,), after_page_id=page_a))

    assert merged.page_ids[1] == imported_id
    assert len(merged.page_ids) == 4
    assert merged.projected_pages()[1].source_session_id == "s-other"


def test_imported_pages_append_when_no_anchor_is_given(three_page_state, write_pdf) -> None:
    other = DocumentModel.from_reader(PdfReader(write_pdf(["extra"])))
    state = three_page_state.with_source("s-other", other)

    merged = state.apply(ImportPages("s-other", (other.pages[0].id,)))

    assert merged.page_ids[-1] == other.pages[0].id


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda ids: RotatePages(("page_missing",), 90), "unknown page ID"),
        (lambda ids: DeletePages(("page_missing",)), "unknown page ID"),
        (lambda ids: DeletePages(ids), "at least one page"),
        (lambda ids: ReorderPages(ids[:2]), "every current page exactly once"),
        (lambda ids: ExtractPages(("page_missing",)), "unknown page ID"),
        (lambda ids: InsertBlankPage(after_page_id="page_missing"), "unknown page ID"),
        (lambda ids: CropPages((ids[0],), (900, 900, 1000, 1000)), "outside the page"),
        (lambda ids: ImportPages("s-missing", ("page_x",)), "unknown import source"),
    ],
)
def test_invalid_operations_are_rejected_before_they_enter_the_log(
    three_page_state, build, message: str
) -> None:
    with pytest.raises(InvalidOperationError, match=message):
        three_page_state.apply(build(_ids(three_page_state)))

    assert three_page_state.operations == ()


def test_importing_an_unknown_source_page_is_rejected(three_page_state, write_pdf) -> None:
    other = DocumentModel.from_reader(PdfReader(write_pdf(["extra"])))
    state = three_page_state.with_source("s-other", other)

    with pytest.raises(InvalidOperationError, match="unknown source page ID"):
        state.apply(ImportPages("s-other", ("page_missing",)))
