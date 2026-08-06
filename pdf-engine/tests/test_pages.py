from __future__ import annotations

import pytest

from pdfengine.document import DocumentModel
from pdfengine.errors import PdfParseError
from pdfengine.parser.reader import PdfReader
from pdfengine.parser.values import PdfDictionary, PdfName, PdfReference


def _pdf_with_objects(*objects: bytes, trailer_entries: bytes = b"") -> bytes:
    body = bytearray(b"%PDF-1.7\n")
    offsets = [0]
    for number, value in enumerate(objects, start=1):
        offsets.append(len(body))
        body.extend(f"{number} 0 obj\n".encode())
        body.extend(value)
        body.extend(b"\nendobj\n")
    xref_offset = len(body)
    body.extend(f"xref\n0 {len(offsets)}\n".encode())
    body.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        body.extend(f"{offset:010d} 00000 n \n".encode())
    body.extend(
        b"trailer\n<< /Size "
        + str(len(offsets)).encode()
        + b" /Root 1 0 R"
        + trailer_entries
        + b" >>\nstartxref\n"
        + str(xref_offset).encode()
        + b"\n%%EOF\n"
    )
    return bytes(body)


def _open_model(tmp_path, data: bytes) -> DocumentModel:
    path = tmp_path / "pages.pdf"
    path.write_bytes(data)
    return DocumentModel.from_reader(PdfReader(path))


def test_document_model_collects_pages_depth_first_with_inherited_attributes(
    tmp_path,
) -> None:
    model = _open_model(
        tmp_path,
        _pdf_with_objects(
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /MediaBox [10 20 610 820] /CropBox [20 30 420 630] /Rotate 450 /Resources 3 0 R /Kids [4 0 R 5 0 R] >>",
            b"<< /Font << /F1 6 0 R >> >>",
            b"<< /Type /Page /Parent 2 0 R >>",
            b"<< /Type /Pages /Rotate -90 /Kids [6 0 R] >>",
            b"<< /Type /Page /Parent 5 0 R /CropBox [0 0 200 300] /Resources << /XObject << >> >> >>",
        ),
    )

    assert model.info.page_count == 2
    assert [(page.info.index, page.info.width, page.info.height) for page in model.pages] == [
        (0, 400.0, 600.0),
        (1, 190.0, 280.0),
    ]
    # The second page's CropBox starts outside the inherited MediaBox and is clipped to it.
    assert model.pages[1].crop_box == (10.0, 20.0, 200.0, 300.0)
    assert [page.info.rotation for page in model.pages] == [90, 270]
    assert model.pages[0].media_box == (10.0, 20.0, 610.0, 820.0)
    assert model.pages[0].crop_box == (20.0, 30.0, 420.0, 630.0)
    assert model.pages[0].resources == PdfDictionary(
        {PdfName("Font"): PdfDictionary({PdfName("F1"): PdfReference(6, 0)})}
    )
    assert model.pages[1].resources == PdfDictionary(
        {PdfName("XObject"): PdfDictionary({})}
    )


def test_document_model_exposes_title_and_fresh_page_ids_per_open(tmp_path) -> None:
    data = _pdf_with_objects(
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] >>",
        b"<< /Type /Page /MediaBox [0 0 612 792] >>",
        b"<< /Title (Quarterly report) >>",
        trailer_entries=b" /Info 4 0 R",
    )

    first = _open_model(tmp_path, data)
    second = _open_model(tmp_path, data)

    assert first.info.title == "Quarterly report"
    assert first.pages[0].id.startswith("page_")
    assert len(first.pages[0].id) == len("page_") + 32
    assert first.pages[0].id != second.pages[0].id


def test_public_page_info_carries_the_stable_page_id_and_source_index(tmp_path) -> None:
    model = _open_model(
        tmp_path,
        _pdf_with_objects(
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /MediaBox [0 0 612 792] /Kids [3 0 R 4 0 R] >>",
            b"<< /Type /Page >>",
            b"<< /Type /Page >>",
        ),
    )

    assert [page.source_index for page in model.info.pages] == [0, 1]
    assert [page.page_id for page in model.info.pages] == [page.id for page in model.pages]


def test_document_model_rejects_a_crop_box_outside_the_media_box(tmp_path) -> None:
    with pytest.raises(PdfParseError, match="does not overlap"):
        _open_model(
            tmp_path,
            _pdf_with_objects(
                b"<< /Type /Catalog /Pages 2 0 R >>",
                b"<< /Type /Pages /Kids [3 0 R] >>",
                b"<< /Type /Page /MediaBox [0 0 100 100] /CropBox [200 200 300 300] >>",
            ),
        )


@pytest.mark.parametrize(
    ("objects", "message"),
    [
        (
            (
                b"<< /Type /Catalog /Pages 2 0 R >>",
                b"<< /Type /Pages /Kids [2 0 R] >>",
            ),
            "cycle",
        ),
        (
            (
                b"<< /Type /Catalog /Pages 2 0 R >>",
                b"<< /Type /Pages /Kids [3 0 R] >>",
                b"<< /Type /NotAPage >>",
            ),
            "Page or Pages",
        ),
    ],
)
def test_document_model_rejects_invalid_page_tree(tmp_path, objects, message: str) -> None:
    with pytest.raises(PdfParseError, match=message):
        _open_model(tmp_path, _pdf_with_objects(*objects))


@pytest.mark.parametrize(
    ("page", "message"),
    [
        (b"<< /Type /Page /MediaBox [0 0 10] >>", "four numbers"),
        (b"<< /Type /Page /MediaBox [0 0 0 10] >>", "positive width"),
        (b"<< /Type /Page /MediaBox [0 0 10 10] /Rotate 45 >>", "Rotate"),
    ],
)
def test_document_model_rejects_invalid_inherited_page_attributes(
    tmp_path, page: bytes, message: str
) -> None:
    with pytest.raises(PdfParseError, match=message):
        _open_model(
            tmp_path,
            _pdf_with_objects(
                b"<< /Type /Catalog /Pages 2 0 R >>",
                b"<< /Type /Pages /Kids [3 0 R] >>",
                page,
            ),
        )
