from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from pdfengine import PdfEngine
from pdfengine.api.models import (
    AddTextLayer,
    CropPages,
    DeletePages,
    ImportPages,
    InsertBlankPage,
    ReorderPages,
    RotatePages,
    SaveOptions,
    SetMetadata,
)
from pdfengine.errors import (
    InvalidOperationError,
    PdfEngineError,
    SessionNotFoundError,
    SessionStateError,
    SourceChangedError,
)

from support.fakes import DpiStubRenderer, GeometryRenderer, StubOcr, StubRenderer
from test_rewrite import page_texts


@pytest.fixture
def renderer() -> StubRenderer:
    return StubRenderer()


@pytest.fixture
def engine(tmp_path, renderer) -> PdfEngine:
    engine = PdfEngine(cache_root=tmp_path / "cache", renderer=renderer)
    yield engine
    engine.close_all()


@pytest.fixture
def session(engine, write_pdf):
    return engine.open_document(write_pdf(["alpha", "beta", "gamma"], title="Original"))


def test_open_exposes_stable_page_ids_and_geometry(engine, session) -> None:
    info = engine.inspect_document(session)

    assert info.page_count == 3
    assert info.title == "Original"
    assert all(page.page_id.startswith("page_") for page in info.pages)
    assert [page.source_index for page in info.pages] == [0, 1, 2]
    assert (info.pages[0].width, info.pages[0].height) == (612.0, 792.0)


def test_opening_a_missing_file_is_a_clear_engine_error(engine, tmp_path) -> None:
    with pytest.raises(PdfEngineError, match="no such PDF file"):
        engine.open_document(tmp_path / "absent.pdf")


def test_edits_project_through_inspection(engine, session) -> None:
    page_a, page_b, page_c = [page.page_id for page in engine.inspect_document(session).pages]

    engine.apply_operations(session, [ReorderPages((page_c, page_a, page_b))])

    assert [page.page_id for page in engine.inspect_document(session).pages] == [
        page_c,
        page_a,
        page_b,
    ]


def test_a_dry_run_leaves_the_session_untouched(engine, session) -> None:
    page_a = engine.inspect_document(session).pages[0].page_id

    state = engine.apply_operations(session, [DeletePages((page_a,))], dry_run=True)

    assert len(state.page_ids) == 2
    assert engine.inspect_document(session).page_count == 3


def test_an_invalid_operation_leaves_the_session_untouched(engine, session) -> None:
    with pytest.raises(InvalidOperationError):
        engine.apply_operations(session, [RotatePages(("page_missing",), 90)])

    assert engine.inspect_document(session).page_count == 3


def test_undo_and_redo_walk_the_session_history(engine, session) -> None:
    page_a = engine.inspect_document(session).pages[0].page_id
    engine.apply_operations(session, [DeletePages((page_a,))])

    assert engine.undo(session).page_ids[0] == page_a
    assert engine.redo(session).page_ids[0] != page_a


def test_render_uses_the_source_page_index_and_caches_the_result(
    engine, session, renderer
) -> None:
    page_b = engine.inspect_document(session).pages[1].page_id

    first = engine.render_page(session, page_b, width=120)
    second = engine.render_page(session, page_b, width=120)

    assert first.image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert (first.cache_hit, second.cache_hit) == (False, True)
    assert len(renderer.calls) == 1
    assert renderer.calls[0][1] == 1
    assert (first.width, first.height) == (120, 240)


def test_rotating_a_page_changes_the_previewed_pixels(tmp_path, write_pdf) -> None:
    """The preview must carry the rotation, not just miss the cache."""

    engine = PdfEngine(cache_root=tmp_path / "cache", renderer=GeometryRenderer())
    try:
        session = engine.open_document(write_pdf(["alpha", "beta"]))
        page_a = engine.inspect_document(session).pages[0].page_id

        before = engine.render_page(session, page_a, width=120)
        engine.apply_operations(session, [RotatePages((page_a,), 90)])
        after = engine.render_page(session, page_a, width=120)

        assert (before.width, before.height) == (612, 792)
        assert (after.width, after.height) == (792, 612)
    finally:
        engine.close_all()


def test_cropping_a_page_changes_the_previewed_pixels(tmp_path, write_pdf) -> None:
    engine = PdfEngine(cache_root=tmp_path / "cache", renderer=GeometryRenderer())
    try:
        session = engine.open_document(write_pdf(["alpha"]))
        page_a = engine.inspect_document(session).pages[0].page_id
        before = engine.render_page(session, page_a, width=120)

        engine.apply_operations(session, [CropPages((page_a,), (0, 0, 300, 400))])
        after = engine.render_page(session, page_a, width=120)

        assert (before.width, before.height) == (612, 792)
        assert (after.width, after.height) == (300, 400)
    finally:
        engine.close_all()


def test_a_generated_blank_page_renders(engine, session, renderer) -> None:
    blank = InsertBlankPage()
    engine.apply_operations(session, [blank])

    result = engine.render_page(session, blank.page_id, width=60)

    assert result.image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert renderer.calls[0][1] == 0


def test_an_imported_page_renders_from_the_edited_document(
    engine, session, write_pdf, renderer
) -> None:
    other = engine.open_document(write_pdf(["from second document"]))
    imported_id = engine.inspect_document(other).pages[0].page_id
    engine.apply_operations(session, [ImportPages(other.session_id, (imported_id,))])

    result = engine.render_page(session, imported_id, width=60)

    assert result.image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    # Rendered from the materialized edit, at its position there — not from
    # the other session's own file.
    assert renderer.calls[0][0].parent == session.cache_dir
    assert renderer.calls[0][1] == 3


def test_an_unedited_document_previews_without_materializing_anything(
    engine, session
) -> None:
    page_a = engine.inspect_document(session).pages[0].page_id

    engine.render_page(session, page_a, width=60)

    assert not list(session.cache_dir.glob("state-*.pdf"))


def test_many_previews_of_one_edit_materialize_a_single_file(engine, session) -> None:
    pages = [page.page_id for page in engine.inspect_document(session).pages]
    engine.apply_operations(session, [RotatePages((pages[0],), 90)])

    for page_id in pages:
        engine.render_thumbnail(session, page_id)

    assert len(list(session.cache_dir.glob("state-*.pdf"))) == 1


def test_a_second_edit_prunes_the_previous_materialized_state(engine, session) -> None:
    pages = [page.page_id for page in engine.inspect_document(session).pages]
    engine.apply_operations(session, [RotatePages((pages[0],), 90)])
    engine.render_page(session, pages[0], width=60)
    first = list(session.cache_dir.glob("state-*.pdf"))

    engine.apply_operations(session, [DeletePages((pages[1],))])
    engine.render_page(session, pages[0], width=60)
    second = list(session.cache_dir.glob("state-*.pdf"))

    assert len(first) == len(second) == 1
    assert first != second


def test_rendering_an_unknown_page_is_rejected(engine, session) -> None:
    with pytest.raises(InvalidOperationError, match="unknown page ID"):
        engine.render_page(session, "page_missing")


def test_a_missing_renderer_is_a_blocked_capability_not_a_crash(tmp_path, write_pdf) -> None:
    engine = PdfEngine(cache_root=tmp_path / "c", renderer=StubRenderer("blocked"))
    engine.open_document(write_pdf(["a"]))

    capabilities = engine.capabilities()

    assert capabilities["preview"]["state"] == "blocked"
    assert {entry["kind"] for entry in capabilities["operations"]} >= {
        "rotate_pages",
        "delete_pages",
        "import_pages",
    }


def test_save_writes_a_distinct_file_and_leaves_the_source_alone(engine, session) -> None:
    before = session.path.read_bytes()
    page_a = engine.inspect_document(session).pages[0].page_id
    engine.apply_operations(session, [DeletePages((page_a,))])

    output = engine.save(session)

    assert output != session.path
    assert output.name.endswith("-edited.pdf")
    assert session.path.read_bytes() == before
    assert page_texts(output) == ["beta", "gamma"]


def test_repeated_saves_never_clobber_an_earlier_copy(engine, session) -> None:
    first = engine.save(session)
    second = engine.save(session)

    assert first != second
    assert first.exists() and second.exists()


def test_a_dry_run_save_writes_nothing(engine, session, tmp_path) -> None:
    target = tmp_path / "out.pdf"

    result = engine.save(session, target, SaveOptions(dry_run=True))

    assert result == target
    assert not target.exists()


def test_engine_refuses_in_place_save_without_opt_in(engine, session) -> None:
    with pytest.raises(PdfEngineError, match="allow_replace_source"):
        engine.save(session, session.path)


def test_engine_refuses_in_place_save_after_source_changes(engine, session) -> None:
    session.path.write_bytes(session.path.read_bytes() + b"\n% changed")

    with pytest.raises(SourceChangedError):
        engine.save(session, session.path, SaveOptions(allow_replace_source=True))


def test_an_opted_in_in_place_save_replaces_the_source(engine, session) -> None:
    page_a = engine.inspect_document(session).pages[0].page_id
    engine.apply_operations(session, [DeletePages((page_a,))])

    output = engine.save(session, session.path, SaveOptions(allow_replace_source=True))

    assert output == session.path
    assert page_texts(session.path) == ["beta", "gamma"]
    assert session.source_changed() is False


def test_saving_into_a_missing_directory_is_rejected(engine, session, tmp_path) -> None:
    with pytest.raises(PdfEngineError, match="output directory"):
        engine.save(session, tmp_path / "absent" / "out.pdf")


def test_metadata_edits_reach_the_saved_copy(engine, session) -> None:
    engine.apply_operations(session, [SetMetadata({"title": "Renamed"})])

    output = engine.save(session)

    other = engine.open_document(output)
    assert engine.inspect_document(other).title == "Renamed"


def test_pages_import_across_two_open_sessions(engine, session, write_pdf) -> None:
    other = engine.open_document(write_pdf(["from second document"]))
    imported_id = engine.inspect_document(other).pages[0].page_id

    engine.apply_operations(
        session, [ImportPages(other.session_id, (imported_id,))]
    )
    output = engine.save(session)

    assert page_texts(output)[-1] == "from second document"


def test_importing_from_an_unknown_session_is_rejected(engine, session) -> None:
    with pytest.raises(SessionNotFoundError, match="import source"):
        engine.apply_operations(session, [ImportPages("session_absent", ("page_x",))])


def test_close_drops_the_password_and_deletes_the_cache(engine, write_pdf) -> None:
    session = engine.open_document(write_pdf(["a", "b"]), password="secret")
    page_id = engine.inspect_document(session).pages[0].page_id
    engine.apply_operations(session, [RotatePages((page_id,), 90)])
    engine.render_page(session, page_id, width=40)
    cache_dir = session.cache_dir
    assert list(cache_dir.glob("*.png"))
    assert list(cache_dir.glob("state-*.pdf"))

    engine.close(session)

    assert session.password is None
    assert not cache_dir.exists()
    with pytest.raises(SessionStateError):
        engine.inspect_document(session)


def test_the_password_is_never_written_to_disk(engine, write_pdf, renderer) -> None:
    session = engine.open_document(write_pdf(["a"]), password="hunter2")
    page_id = engine.inspect_document(session).pages[0].page_id

    engine.render_page(session, page_id, width=40)

    assert renderer.calls[0][3] == "hunter2"
    assert "hunter2" not in repr(session)
    assert all(b"hunter2" not in f.read_bytes() for f in session.cache_dir.glob("*"))


# -- read capability ------------------------------------------------------


WITH_IMAGE = Path(__file__).resolve().parents[1] / "fixtures" / "basic" / "with-image.pdf"


@pytest.fixture
def image_pdf(tmp_path) -> Path:
    """A copy of the JPEG fixture, so a test that saves cannot touch the original."""

    target = tmp_path / "with-image.pdf"
    target.write_bytes(WITH_IMAGE.read_bytes())
    return target


def test_a_jpeg_document_can_be_restructured_but_not_read(engine, image_pdf) -> None:
    session = engine.open_document(image_pdf)

    read = engine.capabilities(session)["read"]

    assert read["structuralEdit"] == {"state": "ready", "detail": ""}
    assert read["textContent"]["state"] == "blocked"
    assert read["textContent"]["filters"] == ["DCTDecode"]
    assert read["textContent"]["objectCount"] == 1
    assert "cannot decode" in read["textContent"]["detail"]


def test_a_clean_document_reports_every_read_capability_ready(engine, session) -> None:
    read = engine.capabilities(session)["read"]

    assert read["structuralEdit"]["state"] == "ready"
    assert read["textContent"] == {
        "state": "ready",
        "detail": "",
        "filters": [],
        "objectCount": 0,
    }


def test_capabilities_without_a_session_describe_no_document(engine) -> None:
    capabilities = engine.capabilities()

    assert "read" not in capabilities
    assert capabilities["preview"]["state"] == "ready"


def test_the_undecodable_survey_runs_once_and_is_reused(
    engine, image_pdf, monkeypatch
) -> None:
    from pdfengine.document.pages import DocumentModel

    calls = []
    original = DocumentModel.undecodable_streams

    def spy(self, reader):
        calls.append(reader)
        return original(self, reader)

    monkeypatch.setattr(DocumentModel, "undecodable_streams", spy)
    session = engine.open_document(image_pdf)

    first = engine.capabilities(session)["read"]
    second = engine.capabilities(session)["read"]

    assert first == second
    assert len(calls) == 1


def test_opening_a_document_does_not_pay_for_the_survey(engine, image_pdf) -> None:
    session = engine.open_document(image_pdf)

    assert session.undecodable_survey is None

    engine.capabilities(session)

    assert session.undecodable_survey is not None


def test_a_jpeg_document_still_reorders_saves_and_reopens(
    engine, image_pdf, tmp_path, write_pdf
) -> None:
    session = engine.open_document(image_pdf)
    extra = engine.open_document(write_pdf(["appended"]))
    imported = [page.page_id for page in engine.inspect_document(extra).pages]
    engine.apply_operations(
        session,
        [ImportPages(source_session_id=extra.session_id, page_ids=imported)],
    )
    order = [page.page_id for page in engine.inspect_document(session).pages]
    engine.apply_operations(session, [ReorderPages(page_ids=list(reversed(order)))])

    target = engine.save(session, tmp_path / "restructured.pdf")
    reopened = engine.open_document(target)

    assert engine.inspect_document(reopened).page_count == 2
    # The JPEG rode through the rewrite, so the reopened copy is still unreadable
    # for the same reason — which is exactly the point: the edit needed no decode.
    assert engine.capabilities(reopened)["read"]["textContent"]["filters"] == [
        "DCTDecode"
    ]


# -- OCR text layer -------------------------------------------------------


@pytest.fixture
def ocr() -> StubOcr:
    return StubOcr()


@pytest.fixture
def ocr_engine(tmp_path, ocr):
    engine = PdfEngine(
        cache_root=tmp_path / "ocr-cache", renderer=DpiStubRenderer(), ocr=ocr
    )
    yield engine
    engine.close_all()


def test_capabilities_report_an_ocr_section(ocr_engine) -> None:
    section = ocr_engine.capabilities()["ocr"]

    assert section["state"] == "ready"
    assert section["engine"] == "stub 0.0"
    assert section["modes"] == ["lstm"]
    assert section["languages"] == ["eng"]


def test_add_text_layer_is_advertised_as_an_operation(ocr_engine) -> None:
    kinds = {entry["kind"] for entry in ocr_engine.capabilities()["operations"]}

    assert "add_text_layer" in kinds


def test_a_missing_ocr_engine_is_an_unavailable_capability_not_a_crash(
    tmp_path,
) -> None:
    engine = PdfEngine(
        cache_root=tmp_path / "cache",
        renderer=DpiStubRenderer(),
        ocr=StubOcr(state="unavailable", detail="Tesseract executable not found"),
    )

    section = engine.capabilities()["ocr"]

    assert section["state"] == "unavailable"
    assert "not found" in section["detail"]
    assert section["modes"] == []


def test_a_broken_ocr_adapter_reports_an_error_rather_than_raising(tmp_path) -> None:
    class Exploding:
        version = "boom-1"

        def capability(self, language="eng", mode="lstm"):
            raise RuntimeError("adapter is broken")

        def languages(self):
            return ()

        def recognize(self, image, dpi=300, language="eng", mode="lstm", psm=3):
            raise RuntimeError("adapter is broken")

    engine = PdfEngine(
        cache_root=tmp_path / "cache", renderer=DpiStubRenderer(), ocr=Exploding()
    )

    section = engine.capabilities()["ocr"]

    assert section["state"] == "error"
    assert "broken" in section["detail"]


def test_add_text_layer_projects_and_records_the_recognized_page(
    ocr_engine, ocr, write_pdf
) -> None:
    session = ocr_engine.open_document(write_pdf(["alpha", "beta"]))
    pages = [page.page_id for page in ocr_engine.inspect_document(session).pages]

    state = ocr_engine.apply_operations(
        session, [AddTextLayer(page_ids=(pages[1],), dpi=200)]
    )
    projected = {page.page_id: page for page in state.projected_pages()}

    assert projected[pages[0]].text_layer is None
    layer = projected[pages[1]].text_layer
    assert layer is not None and not layer.pending
    assert layer.recognized.words[0].text == "hello"
    assert ocr.calls == [(200, "eng", "lstm")]


def test_recognition_never_rasterizes_an_unrelated_page(ocr_engine, write_pdf) -> None:
    session = ocr_engine.open_document(write_pdf(["alpha", "beta", "gamma"]))
    pages = [page.page_id for page in ocr_engine.inspect_document(session).pages]

    ocr_engine.apply_operations(session, [AddTextLayer(page_ids=(pages[1],))])

    renderer = ocr_engine._renderer
    assert [index for index, _ in renderer.dpi_calls] == [1]
    # Previews are a separate concern; nothing here should have produced one.
    assert renderer.calls == []


def test_a_dry_run_recognizes_without_committing_the_state(
    ocr_engine, ocr, write_pdf
) -> None:
    session = ocr_engine.open_document(write_pdf(["alpha"]))
    page_id = ocr_engine.inspect_document(session).pages[0].page_id

    state = ocr_engine.apply_operations(
        session, [AddTextLayer(page_ids=(page_id,))], dry_run=True
    )

    assert state.projected_pages()[0].text_layer.recognized is not None
    assert session.state.cursor == 0
    assert len(ocr.calls) == 1


def test_add_text_layer_saves_a_searchable_page(ocr_engine, write_pdf, tmp_path) -> None:
    from test_textlayer import content_streams, page_fonts

    session = ocr_engine.open_document(write_pdf(["alpha"]))
    page_id = ocr_engine.inspect_document(session).pages[0].page_id
    ocr_engine.apply_operations(session, [AddTextLayer(page_ids=(page_id,))])

    target = ocr_engine.save(session, tmp_path / "searchable.pdf")

    streams = content_streams(target)
    assert b"(alpha) Tj" in streams[0]
    assert b"3 Tr" in streams[-1]
    assert "OCR" in page_fonts(target)


def test_an_unknown_page_id_is_rejected_before_any_recognition(
    ocr_engine, ocr, write_pdf
) -> None:
    session = ocr_engine.open_document(write_pdf(["alpha"]))

    with pytest.raises(InvalidOperationError, match="unknown page ID"):
        ocr_engine.apply_operations(session, [AddTextLayer(page_ids=("page_absent",))])

    assert ocr.calls == []


def test_a_renderer_without_dpi_support_is_a_clear_ocr_error(
    tmp_path, write_pdf, ocr
) -> None:
    from pdfengine.errors import OcrUnavailableError

    engine = PdfEngine(cache_root=tmp_path / "cache", renderer=StubRenderer(), ocr=ocr)
    session = engine.open_document(write_pdf(["alpha"]))
    page_id = engine.inspect_document(session).pages[0].page_id

    with pytest.raises(OcrUnavailableError, match="exact DPI"):
        engine.apply_operations(session, [AddTextLayer(page_ids=(page_id,))])


def test_changing_the_recognition_settings_reruns_recognition(
    ocr_engine, ocr, write_pdf
) -> None:
    session = ocr_engine.open_document(write_pdf(["alpha"]))
    page_id = ocr_engine.inspect_document(session).pages[0].page_id

    ocr_engine.apply_operations(session, [AddTextLayer(page_ids=(page_id,), dpi=150)])
    ocr_engine.apply_operations(session, [AddTextLayer(page_ids=(page_id,), dpi=400)])

    # A result recognized at 150 DPI is not an answer to a request for 400.
    assert [dpi for dpi, _, _ in ocr.calls] == [150, 400]


def test_changing_only_the_confidence_floor_reuses_the_recognition(
    ocr_engine, ocr, write_pdf
) -> None:
    session = ocr_engine.open_document(write_pdf(["alpha"]))
    page_id = ocr_engine.inspect_document(session).pages[0].page_id

    ocr_engine.apply_operations(session, [AddTextLayer(page_ids=(page_id,))])
    ocr_engine.apply_operations(
        session, [AddTextLayer(page_ids=(page_id,), min_confidence=80.0)]
    )

    # min_confidence filters at write time, so it must not force a re-run.
    assert len(ocr.calls) == 1


# -- end to end, on a real install ----------------------------------------


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _tesseract_ready() -> bool:
    from pdfengine.ocr.tesseract import TesseractOcr

    return TesseractOcr().capability("eng", "lstm").ready


tesseract_missing = pytest.mark.skipif(
    shutil.which("pdftoppm") is None or not _tesseract_ready(),
    reason="a real Poppler and Tesseract install is required",
)


@tesseract_missing
def test_ocr_makes_a_real_scanned_page_searchable(tmp_path) -> None:
    """Render, recognize, save, reopen - and find the words in the output.

    This is the test the feature exists for: everything else uses a stub, so
    only this one proves that a real recognizer's output survives the
    transform, the glyphless font and the writer intact.
    """

    from pdfengine.parser.reader import PdfReader
    from pdfengine.parser.values import PdfName
    from pdfengine.rendering.poppler import PopplerRenderer
    from test_textlayer import content_streams, page_fonts

    source = tmp_path / "one-page.pdf"
    shutil.copyfile(FIXTURES / "basic" / "one-page.pdf", source)

    engine = PdfEngine(cache_root=tmp_path / "cache", renderer=PopplerRenderer())
    try:
        session = engine.open_document(source)
        page_id = engine.inspect_document(session).pages[0].page_id

        state = engine.apply_operations(session, [AddTextLayer(page_ids=(page_id,))])
        recognized = state.projected_pages()[0].text_layer.recognized
        assert recognized is not None
        assert "one" in recognized.text and "page" in recognized.text

        target = engine.save(session, tmp_path / "searchable.pdf")
    finally:
        engine.close_all()

    streams = content_streams(target)
    assert b"3 Tr" in streams[-1]

    # The recognized text has to be recoverable the way a viewer recovers it:
    # decode the shown CIDs through the font's own ToUnicode CMap.
    reopened = PdfReader(target)
    font = reopened.resolve(page_fonts(target)["OCR"])
    cmap = reopened.resolve(font.entries[PdfName("ToUnicode")]).data
    extracted = _extract_text(streams[-1].decode("ascii"), _bfchar_map(cmap))

    assert "one" in extracted
    assert "page" in extracted


def _bfchar_map(cmap: bytes) -> dict[int, str]:
    """Parse the bfchar entries of a ToUnicode CMap into CID -> character."""

    text = cmap.decode("ascii")
    mapping: dict[int, str] = {}
    body = text.split("endcodespacerange", 1)[-1]
    for cid, value in re.findall(r"<([0-9A-Fa-f]{4})>\s*<([0-9A-Fa-f]+)>", body):
        mapping[int(cid, 16)] = bytes.fromhex(value).decode("utf-16-be")
    return mapping


def _extract_text(stream: str, mapping: dict[int, str]) -> str:
    out: list[str] = []
    for hex_text in re.findall(r"<([0-9A-F]*)> Tj", stream):
        raw = bytes.fromhex(hex_text)
        out.append(
            "".join(
                mapping.get(int.from_bytes(raw[index : index + 2], "big"), "")
                for index in range(0, len(raw), 2)
            )
        )
    return " ".join(out)
