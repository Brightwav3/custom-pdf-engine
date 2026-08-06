from __future__ import annotations

import zlib
from collections.abc import Callable
from pathlib import Path

import pytest


def assemble_pdf(objects: list[bytes], trailer_entries: bytes = b"") -> bytes:
    """Serialize numbered objects into a classic-xref PDF."""

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


def build_pdf(
    page_texts: list[str] | tuple[str, ...],
    *,
    title: str | None = None,
    media_box: tuple[float, float, float, float] = (0, 0, 612, 792),
    compress: bool = False,
) -> bytes:
    """Build a minimal but genuinely valid PDF, one text line per page."""

    page_texts = list(page_texts)
    if not page_texts:
        raise ValueError("a PDF needs at least one page")

    count = len(page_texts)
    font_number = 3
    first_page = 4
    first_content = first_page + count
    info_number = first_content + count

    kids = " ".join(f"{first_page + index} 0 R" for index in range(count))
    box = " ".join(_number(value) for value in media_box)

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {count} /MediaBox [{box}] >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for index in range(count):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 {font_number} 0 R >> >>"
            f" /Contents {first_content + index} 0 R >>".encode()
        )
    for text in page_texts:
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        if compress:
            body = zlib.compress(content)
            objects.append(
                f"<< /Length {len(body)} /Filter /FlateDecode >>\nstream\n".encode()
                + body
                + b"\nendstream"
            )
        else:
            objects.append(
                f"<< /Length {len(content)} >>\nstream\n".encode()
                + content
                + b"\nendstream"
            )

    trailer_entries = b""
    if title is not None:
        objects.append(f"<< /Title ({title}) >>".encode())
        trailer_entries = f" /Info {info_number} 0 R".encode()

    return assemble_pdf(objects, trailer_entries)


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def make_png(width: int = 4, height: int = 3) -> bytes:
    """Build a real, minimal greyscale PNG without any imaging dependency."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            len(payload).to_bytes(4, "big")
            + kind
            + payload
            + (zlib.crc32(kind + payload) & 0xFFFFFFFF).to_bytes(4, "big")
        )

    header = width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes([8, 0, 0, 0, 0])
    raw = b"".join(b"\x00" + b"\xff" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


@pytest.fixture
def png_bytes() -> Callable[..., bytes]:
    return make_png


@pytest.fixture
def basic_pdf() -> Callable[[int], bytes]:
    """Return a self-contained PDF with the requested number of pages."""

    def make(page_count: int = 1) -> bytes:
        if page_count < 1:
            raise ValueError("page_count must be at least 1")
        return build_pdf([f"page {index + 1}" for index in range(page_count)])

    return make


@pytest.fixture
def write_pdf(tmp_path: Path) -> Callable[..., Path]:
    """Write a generated PDF to disk and return its path."""

    counter = {"n": 0}

    def make(page_texts: list[str] | tuple[str, ...] = ("first page",), **kwargs) -> Path:
        counter["n"] += 1
        path = tmp_path / f"source-{counter['n']}.pdf"
        path.write_bytes(build_pdf(page_texts, **kwargs))
        return path

    return make
