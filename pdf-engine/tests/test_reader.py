import zlib

import pytest

from pdfengine.errors import PdfParseError
from pdfengine.parser.reader import PdfReader, PdfStream
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
        + trailer_entries
        + b" >>\nstartxref\n"
        + str(xref_offset).encode()
        + b"\n%%EOF\n"
    )
    return bytes(body)


def test_reader_parses_classic_xref_trailer_and_resolves_root(
    tmp_path, basic_pdf
) -> None:
    path = tmp_path / "basic.pdf"
    path.write_bytes(basic_pdf(1))

    reader = PdfReader(path)

    assert reader.trailer == PdfDictionary(
        {PdfName("Size"): 4, PdfName("Root"): PdfReference(1, 0)}
    )
    assert reader.resolve(PdfReference(1, 0)) == PdfDictionary(
        {
            PdfName("Type"): PdfName("Catalog"),
            PdfName("Pages"): PdfReference(2, 0),
        }
    )


def test_reader_rejects_a_missing_pdf_header(tmp_path, basic_pdf) -> None:
    path = tmp_path / "not-a-pdf.pdf"
    path.write_bytes(b"NOTPDF!!" + basic_pdf(1)[8:])

    with pytest.raises(PdfParseError) as raised:
        PdfReader(path)

    assert raised.value.offset == 0


def test_resolve_reads_an_unfiltered_stream_by_its_declared_length(tmp_path) -> None:
    path = tmp_path / "stream.pdf"
    path.write_bytes(
        _pdf_with_objects(
            b"<< /Length 22 >>\nstream\ninside endstream bytes\nendstream",
            trailer_entries=b" /Root 1 0 R",
        )
    )

    stream = PdfReader(path).resolve(PdfReference(1, 0))

    assert stream == PdfStream(
        PdfDictionary({PdfName("Length"): 22}), b"inside endstream bytes"
    )


def test_resolve_decodes_a_flate_stream(tmp_path) -> None:
    compressed = zlib.compress(b"decoded stream data")
    value = (
        f"<< /Length {len(compressed)} /Filter /FlateDecode >>\nstream\n".encode()
        + compressed
        + b"\nendstream"
    )
    path = tmp_path / "flate.pdf"
    path.write_bytes(_pdf_with_objects(value, trailer_entries=b" /Root 1 0 R"))

    stream = PdfReader(path).resolve(PdfReference(1, 0))

    assert stream == PdfStream(
        PdfDictionary(
            {
                PdfName("Length"): len(compressed),
                PdfName("Filter"): PdfName("FlateDecode"),
            }
        ),
        b"decoded stream data",
    )


def test_resolve_reports_corrupt_flate_data_as_a_pdf_error(tmp_path) -> None:
    path = tmp_path / "corrupt-flate.pdf"
    path.write_bytes(
        _pdf_with_objects(
            b"<< /Length 3 /Filter /FlateDecode >>\nstream\nbad\nendstream",
            trailer_entries=b" /Root 1 0 R",
        )
    )

    with pytest.raises(PdfParseError, match="FlateDecode"):
        PdfReader(path).resolve(PdfReference(1, 0))


def test_resolve_caches_an_indirect_object(tmp_path, basic_pdf) -> None:
    path = tmp_path / "cached.pdf"
    path.write_bytes(basic_pdf(1))
    reader = PdfReader(path)

    first = reader.resolve(PdfReference(1, 0))
    second = reader.resolve(PdfReference(1, 0))

    assert second is first


def test_reader_rejects_encrypted_pdfs(tmp_path) -> None:
    path = tmp_path / "encrypted.pdf"
    path.write_bytes(
        _pdf_with_objects(
            b"<< /Filter /Standard >>",
            trailer_entries=b" /Encrypt 1 0 R",
        )
    )

    with pytest.raises(PdfParseError, match="encryption"):
        PdfReader(path)


def test_resolve_rejects_an_unknown_stream_filter(tmp_path) -> None:
    path = tmp_path / "unknown-filter.pdf"
    path.write_bytes(
        _pdf_with_objects(
            b"<< /Length 3 /Filter /LZWDecode >>\nstream\nraw\nendstream",
            trailer_entries=b" /Root 1 0 R",
        )
    )

    with pytest.raises(PdfParseError, match="stream filter"):
        PdfReader(path).resolve(PdfReference(1, 0))


def test_resolve_rejects_object_streams(tmp_path) -> None:
    path = tmp_path / "object-stream.pdf"
    path.write_bytes(
        _pdf_with_objects(
            b"<< /Type /ObjStm /Length 0 >>\nstream\n\nendstream",
            trailer_entries=b" /Root 1 0 R",
        )
    )

    with pytest.raises(PdfParseError, match="object streams"):
        PdfReader(path).resolve(PdfReference(1, 0))


def test_reader_rejects_xref_streams(tmp_path) -> None:
    body = bytearray(b"%PDF-1.7\n")
    xref_offset = len(body)
    body.extend(b"1 0 obj\n<< /Type /XRef /Length 0 >>\nstream\n\nendstream\nendobj\n")
    body.extend(f"startxref\n{xref_offset}\n%%EOF\n".encode())
    path = tmp_path / "xref-stream.pdf"
    path.write_bytes(body)

    with pytest.raises(PdfParseError, match="xref streams"):
        PdfReader(path)


def test_resolve_uses_an_indirect_stream_length(tmp_path) -> None:
    path = tmp_path / "indirect-length.pdf"
    path.write_bytes(
        _pdf_with_objects(
            b"<< /Length 2 0 R >>\nstream\nhello\nendstream",
            b"5",
            trailer_entries=b" /Root 1 0 R",
        )
    )

    stream = PdfReader(path).resolve(PdfReference(1, 0))

    assert stream == PdfStream(
        PdfDictionary({PdfName("Length"): PdfReference(2, 0)}), b"hello"
    )


def test_reader_rejects_an_out_of_range_startxref(tmp_path, basic_pdf) -> None:
    path = tmp_path / "bad-startxref.pdf"
    data = basic_pdf(1)
    marker = data.rfind(b"startxref\n") + len(b"startxref\n")
    end = data.index(b"\n", marker)
    path.write_bytes(data[:marker] + b"9999999999" + data[end:])

    with pytest.raises(PdfParseError, match="startxref") as raised:
        PdfReader(path)

    assert raised.value.offset == 9_999_999_999


def test_resolve_rejects_an_out_of_range_xref_entry(tmp_path, basic_pdf) -> None:
    path = tmp_path / "bad-entry-offset.pdf"
    data = basic_pdf(1).replace(
        b"0000000009 00000 n ", b"9999999999 00000 n ", 1
    )
    path.write_bytes(data)

    with pytest.raises(PdfParseError, match="outside the file") as raised:
        PdfReader(path).resolve(PdfReference(1, 0))

    assert raised.value.offset == 9_999_999_999


def test_reader_rejects_a_non_fixed_width_xref_entry(tmp_path, basic_pdf) -> None:
    path = tmp_path / "malformed-entry.pdf"
    path.write_bytes(
        basic_pdf(1).replace(b"0000000009 00000 n ", b"000000000X 00000 n ", 1)
    )

    with pytest.raises(PdfParseError, match="malformed xref entry"):
        PdfReader(path)


def test_resolve_validates_the_indirect_object_generation(tmp_path, basic_pdf) -> None:
    path = tmp_path / "wrong-generation.pdf"
    path.write_bytes(basic_pdf(1).replace(b"1 0 obj", b"1 2 obj", 1))

    with pytest.raises(PdfParseError, match="wrong indirect object"):
        PdfReader(path).resolve(PdfReference(1, 0))


def test_indirect_objects_are_parsed_only_when_resolved(tmp_path) -> None:
    path = tmp_path / "lazy.pdf"
    path.write_bytes(
        _pdf_with_objects(b"not-a-value", trailer_entries=b" /Root 1 0 R")
    )

    reader = PdfReader(path)

    with pytest.raises(PdfParseError):
        reader.resolve(PdfReference(1, 0))
