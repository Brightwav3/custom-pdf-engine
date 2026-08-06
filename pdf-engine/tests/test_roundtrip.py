"""Semantic checks against the committed literal fixtures."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from pdfengine import PdfEngine, RotatePages, SaveOptions
from pdfengine.document import DocumentModel
from pdfengine.errors import UnsupportedPdfError
from pdfengine.parser.reader import PdfReader

from test_engine import StubRenderer


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def engine(tmp_path):
    engine = PdfEngine(cache_root=tmp_path / "cache", renderer=StubRenderer())
    yield engine
    engine.close_all()


def copied(name: str, tmp_path: Path) -> Path:
    target = tmp_path / Path(name).name
    shutil.copyfile(FIXTURES / name, target)
    return target


def test_every_fixture_stays_small_enough_to_read_by_hand() -> None:
    for fixture in FIXTURES.rglob("*.pdf"):
        assert fixture.stat().st_size < 20 * 1024, fixture


def test_the_one_page_fixture_reports_its_documented_facts(engine, tmp_path) -> None:
    session = engine.open_document(copied("basic/one-page.pdf", tmp_path))

    info = engine.inspect_document(session)

    assert info.page_count == 1
    assert info.title == "One page fixture"
    assert (info.pages[0].width, info.pages[0].height) == (612.0, 792.0)
    assert info.pages[0].rotation == 0


def test_the_inherited_fixture_reports_its_documented_facts(engine, tmp_path) -> None:
    session = engine.open_document(copied("basic/inherited-pages.pdf", tmp_path))

    info = engine.inspect_document(session)

    assert info.page_count == 2
    assert [(page.width, page.height, page.rotation) for page in info.pages] == [
        (595.0, 842.0, 90),
        (300.0, 400.0, 90),
    ]


def test_an_unsupported_fixture_names_the_blocking_feature(engine, tmp_path) -> None:
    with pytest.raises(UnsupportedPdfError, match="xref stream") as raised:
        engine.open_document(copied("unsupported/xref-stream.pdf", tmp_path))

    assert raised.value.feature == "xref stream"


def test_a_saved_edit_reopens_with_the_edit_applied(engine, tmp_path) -> None:
    source = copied("basic/inherited-pages.pdf", tmp_path)
    before = source.read_bytes()
    session = engine.open_document(source)
    page_a = engine.inspect_document(session).pages[0].page_id

    engine.apply_operations(session, [RotatePages((page_a,), 180)])
    output = engine.save(session, tmp_path / "rotated.pdf")

    assert source.read_bytes() == before
    reopened = engine.open_document(output)
    info = engine.inspect_document(reopened)
    assert info.pages[0].rotation == 270
    assert (info.pages[0].width, info.pages[0].height) == (595.0, 842.0)
    assert info.pages[1].rotation == 90


def test_saving_an_unedited_fixture_preserves_every_page_fact(engine, tmp_path) -> None:
    session = engine.open_document(copied("basic/inherited-pages.pdf", tmp_path))
    before = engine.inspect_document(session)

    output = engine.save(session, tmp_path / "copy.pdf", SaveOptions())

    after = DocumentModel.from_reader(PdfReader(output)).info
    assert after.page_count == before.page_count
    assert [(page.width, page.height, page.rotation) for page in after.pages] == [
        (page.width, page.height, page.rotation) for page in before.pages
    ]


def test_the_engine_package_does_not_import_a_host_application() -> None:
    """The package must stand alone; no product-specific imports anywhere."""

    source_root = Path(__file__).resolve().parents[1] / "src" / "pdfengine"
    forbidden = ("import converter", "from converter", "import app", "from app", "electron")
    for module in source_root.rglob("*.py"):
        text = module.read_text(encoding="utf-8").lower()
        for needle in forbidden:
            assert needle not in text, f"{module} imports {needle}"
