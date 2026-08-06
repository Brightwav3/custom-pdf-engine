import zlib
from pathlib import Path

import pytest

from pdfengine.document import DocumentModel
from pdfengine.errors import PdfParseError, UnsupportedPdfError
from pdfengine.parser import values
from pdfengine.parser.reader import PdfReader, PdfStream
from pdfengine.parser.values import PdfArray, PdfDictionary, PdfName, PdfReference


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

    assert reader.trailer.entries[PdfName("Root")] == PdfReference(1, 0)
    assert reader.trailer.entries[PdfName("Size")] == 6
    assert reader.resolve(PdfReference(1, 0)) == PdfDictionary(
        {
            PdfName("Type"): PdfName("Catalog"),
            PdfName("Pages"): PdfReference(2, 0),
        }
    )


def test_reader_accepts_a_twenty_byte_crlf_xref_entry(tmp_path, basic_pdf) -> None:
    path = tmp_path / "crlf-xref.pdf"
    path.write_bytes(
        basic_pdf(1).replace(b"0000000009 00000 n \n", b"0000000009 00000 n\r\n")
    )

    reader = PdfReader(path)

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
        compressed,
        (PdfName("FlateDecode"),),
    )
    assert stream.is_decodable
    assert stream.data == b"decoded stream data"


def test_resolve_decodes_a_single_flate_filter_array(tmp_path) -> None:
    compressed = zlib.compress(b"array-filter data")
    value = (
        f"<< /Length {len(compressed)} /Filter [/FlateDecode] >>\nstream\n".encode()
        + compressed
        + b"\nendstream"
    )
    path = tmp_path / "flate-array.pdf"
    path.write_bytes(_pdf_with_objects(value, trailer_entries=b" /Root 1 0 R"))

    stream = PdfReader(path).resolve(PdfReference(1, 0))

    assert stream == PdfStream(
        PdfDictionary(
            {
                PdfName("Length"): len(compressed),
                PdfName("Filter"): PdfArray((PdfName("FlateDecode"),)),
            }
        ),
        compressed,
        (PdfName("FlateDecode"),),
    )
    assert stream.is_decodable
    assert stream.data == b"array-filter data"


def test_resolve_reports_corrupt_flate_data_as_a_pdf_error(tmp_path) -> None:
    path = tmp_path / "corrupt-flate.pdf"
    path.write_bytes(
        _pdf_with_objects(
            b"<< /Length 3 /Filter /FlateDecode >>\nstream\nbad\nendstream",
            trailer_entries=b" /Root 1 0 R",
        )
    )

    stream = PdfReader(path).resolve(PdfReference(1, 0))

    with pytest.raises(PdfParseError, match="FlateDecode"):
        stream.data


def test_resolve_rejects_trailing_data_after_a_flate_member(tmp_path) -> None:
    compressed = zlib.compress(b"decoded") + b"trailing"
    value = (
        f"<< /Length {len(compressed)} /Filter /FlateDecode >>\nstream\n".encode()
        + compressed
        + b"\nendstream"
    )
    path = tmp_path / "trailing-flate.pdf"
    path.write_bytes(_pdf_with_objects(value, trailer_entries=b" /Root 1 0 R"))

    stream = PdfReader(path).resolve(PdfReference(1, 0))

    with pytest.raises(PdfParseError, match="FlateDecode"):
        stream.data


def test_resolve_rejects_a_non_eof_flate_member(tmp_path) -> None:
    compressed = zlib.compress(b"decoded")[:-1]
    value = (
        f"<< /Length {len(compressed)} /Filter /FlateDecode >>\nstream\n".encode()
        + compressed
        + b"\nendstream"
    )
    path = tmp_path / "truncated-flate.pdf"
    path.write_bytes(_pdf_with_objects(value, trailer_entries=b" /Root 1 0 R"))

    stream = PdfReader(path).resolve(PdfReference(1, 0))

    with pytest.raises(PdfParseError, match="FlateDecode"):
        stream.data


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


def test_an_undecodable_filter_still_reads_and_keeps_its_raw_bytes(tmp_path) -> None:
    body = b"\xff\xd8\xff\xd9raw jpeg"
    value = (
        f"<< /Length {len(body)} /Filter /DCTDecode >>\nstream\n".encode()
        + body
        + b"\nendstream"
    )
    path = tmp_path / "dct.pdf"
    path.write_bytes(_pdf_with_objects(value, trailer_entries=b" /Root 1 0 R"))

    stream = PdfReader(path).resolve(PdfReference(1, 0))

    assert isinstance(stream, PdfStream)
    assert stream.raw == body
    assert stream.is_decodable is False
    assert stream.residual_filters == (PdfName("DCTDecode"),)
    with pytest.raises(UnsupportedPdfError, match="DCTDecode"):
        stream.data


def test_a_filter_chain_decodes_only_its_decodable_prefix(tmp_path) -> None:
    body = zlib.compress(b"\xff\xd8 jpeg bytes")
    value = (
        f"<< /Length {len(body)} /Filter [/FlateDecode /DCTDecode] >>\nstream\n".encode()
        + body
        + b"\nendstream"
    )
    path = tmp_path / "flate-then-dct.pdf"
    path.write_bytes(_pdf_with_objects(value, trailer_entries=b" /Root 1 0 R"))

    stream = PdfReader(path).resolve(PdfReference(1, 0))

    assert stream.raw == body
    assert stream.filters == (PdfName("FlateDecode"), PdfName("DCTDecode"))
    assert stream.residual_filters == (PdfName("DCTDecode"),)
    with pytest.raises(UnsupportedPdfError, match="DCTDecode"):
        stream.data


def test_decode_parms_line_up_with_the_filter_chain(tmp_path) -> None:
    body = b"raw"
    value = (
        b"<< /Length 3 /Filter [/FlateDecode /DCTDecode]"
        b" /DecodeParms [null << /Columns 4 >>] >>\nstream\nraw\nendstream"
    )
    path = tmp_path / "decode-parms.pdf"
    path.write_bytes(_pdf_with_objects(value, trailer_entries=b" /Root 1 0 R"))

    stream = PdfReader(path).resolve(PdfReference(1, 0))

    assert stream.raw == body
    assert len(stream.decode_parms) == 2
    assert stream.decode_parms[0] is None
    assert stream.decode_parms[1] == PdfDictionary({PdfName("Columns"): 4})


def test_a_single_decode_parms_dictionary_is_accepted(tmp_path) -> None:
    value = (
        b"<< /Length 3 /Filter /DCTDecode /DecodeParms << /Columns 4 >> >>"
        b"\nstream\nraw\nendstream"
    )
    path = tmp_path / "single-decode-parms.pdf"
    path.write_bytes(_pdf_with_objects(value, trailer_entries=b" /Root 1 0 R"))

    stream = PdfReader(path).resolve(PdfReference(1, 0))

    assert stream.decode_parms == (PdfDictionary({PdfName("Columns"): 4}),)


def test_equality_ignores_the_decode_cache(tmp_path) -> None:
    compressed = zlib.compress(b"same body")
    dictionary = PdfDictionary({PdfName("Length"): len(compressed)})
    first = PdfStream(dictionary, compressed, (PdfName("FlateDecode"),))
    second = PdfStream(dictionary, compressed, (PdfName("FlateDecode"),))

    assert first.data == b"same body"

    assert first == second


def test_decoding_refuses_to_inflate_past_the_size_limit(tmp_path, monkeypatch) -> None:
    compressed = zlib.compress(b"\x00" * 100_000)
    value = (
        f"<< /Length {len(compressed)} /Filter /FlateDecode >>\nstream\n".encode()
        + compressed
        + b"\nendstream"
    )
    path = tmp_path / "bomb.pdf"
    path.write_bytes(_pdf_with_objects(value, trailer_entries=b" /Root 1 0 R"))
    monkeypatch.setattr(values, "MAX_DECODED_BYTES", 1024)

    stream = PdfReader(path).resolve(PdfReference(1, 0))

    with pytest.raises(PdfParseError, match="exceeds the size limit"):
        stream.data


def test_the_image_fixture_opens_as_a_single_page_document() -> None:
    path = Path(__file__).resolve().parents[1] / "fixtures" / "basic" / "with-image.pdf"

    reader = PdfReader(path)
    model = DocumentModel.from_reader(reader)

    assert model.info.page_count == 1


def test_resolve_rejects_object_streams(tmp_path) -> None:
    path = tmp_path / "object-stream.pdf"
    path.write_bytes(
        _pdf_with_objects(
            b"<< /Type /ObjStm /Length 0 >>\nstream\n\nendstream",
            trailer_entries=b" /Root 1 0 R",
        )
    )

    with pytest.raises(PdfParseError, match="object stream"):
        PdfReader(path).resolve(PdfReference(1, 0))


def test_reader_rejects_xref_streams(tmp_path) -> None:
    body = bytearray(b"%PDF-1.7\n")
    xref_offset = len(body)
    body.extend(b"1 0 obj\n<< /Type /XRef /Length 0 >>\nstream\n\nendstream\nendobj\n")
    body.extend(f"startxref\n{xref_offset}\n%%EOF\n".encode())
    path = tmp_path / "xref-stream.pdf"
    path.write_bytes(body)

    with pytest.raises(PdfParseError, match="xref stream"):
        PdfReader(path)


def test_reader_rejects_a_hybrid_xref_stream_trailer(tmp_path) -> None:
    path = tmp_path / "hybrid-xref.pdf"
    path.write_bytes(
        _pdf_with_objects(
            b"<< /Type /Catalog >>",
            trailer_entries=b" /Root 1 0 R /XRefStm 9",
        )
    )

    with pytest.raises(PdfParseError, match="xref stream"):
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


def test_resolve_accepts_a_comment_between_a_value_and_endobj(tmp_path) -> None:
    path = tmp_path / "comment-before-endobj.pdf"
    path.write_bytes(
        _pdf_with_objects(
            b"42 % comment before endobj",
            trailer_entries=b" /Root 1 0 R",
        )
    )

    assert PdfReader(path).resolve(PdfReference(1, 0)) == 42


def test_resolve_accepts_a_comment_between_a_dictionary_and_stream(tmp_path) -> None:
    path = tmp_path / "comment-before-stream.pdf"
    path.write_bytes(
        _pdf_with_objects(
            b"<< /Length 5 >> % comment before stream\nstream\nhello\nendstream",
            trailer_entries=b" /Root 1 0 R",
        )
    )

    assert PdfReader(path).resolve(PdfReference(1, 0)) == PdfStream(
        PdfDictionary({PdfName("Length"): 5}), b"hello"
    )


def test_flate_with_a_predictor_is_reported_as_undecodable(tmp_path) -> None:
    """Inflating a predicted stream returns plausible but wrong bytes."""

    compressed = zlib.compress(b"\x02rowdata")
    value = (
        f"<< /Length {len(compressed)} /Filter /FlateDecode "
        f"/DecodeParms << /Predictor 12 /Columns 4 >> >>\nstream\n".encode()
        + compressed
        + b"\nendstream"
    )
    path = tmp_path / "predicted.pdf"
    path.write_bytes(_pdf_with_objects(value, trailer_entries=b" /Root 1 0 R"))

    stream = PdfReader(path).resolve(PdfReference(1, 0))

    assert stream.raw == compressed
    assert stream.is_decodable is False
    with pytest.raises(PdfParseError, match="FlateDecode with a predictor"):
        stream.data


def test_flate_with_predictor_one_still_decodes(tmp_path) -> None:
    compressed = zlib.compress(b"plain bytes")
    value = (
        f"<< /Length {len(compressed)} /Filter /FlateDecode "
        f"/DecodeParms << /Predictor 1 >> >>\nstream\n".encode()
        + compressed
        + b"\nendstream"
    )
    path = tmp_path / "unpredicted.pdf"
    path.write_bytes(_pdf_with_objects(value, trailer_entries=b" /Root 1 0 R"))

    assert PdfReader(path).resolve(PdfReference(1, 0)).data == b"plain bytes"


def test_an_undecodable_filter_is_named_without_dataclass_noise(tmp_path) -> None:
    """The message is user-facing: it must read 'DCTDecode', not a repr."""

    path = tmp_path / "jpeg.pdf"
    path.write_bytes(
        _pdf_with_objects(
            b"<< /Length 3 /Filter /DCTDecode >>\nstream\nraw\nendstream",
            trailer_entries=b" /Root 1 0 R",
        )
    )
    stream = PdfReader(path).resolve(PdfReference(1, 0))

    with pytest.raises(PdfParseError) as raised:
        stream.data

    assert str(raised.value).startswith("unsupported PDF feature: stream filter DCTDecode")
    assert "PdfName" not in str(raised.value)
