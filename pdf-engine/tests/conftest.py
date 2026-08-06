from __future__ import annotations

from collections.abc import Callable

import pytest


@pytest.fixture
def basic_pdf() -> Callable[[int], bytes]:
    """Return a self-contained PDF with the requested number of blank pages."""

    def make(page_count: int = 1) -> bytes:
        if page_count < 1:
            raise ValueError("page_count must be at least 1")

        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            f"<< /Type /Pages /Kids [{' '.join(f'{index + 3} 0 R' for index in range(page_count))}] /Count {page_count} >>".encode(),
        ]
        objects.extend(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>"
            for _ in range(page_count)
        )

        body = bytearray(b"%PDF-1.7\n")
        offsets = [0]
        for number, obj in enumerate(objects, start=1):
            offsets.append(len(body))
            body.extend(f"{number} 0 obj\n".encode())
            body.extend(obj)
            body.extend(b"\nendobj\n")

        xref_offset = len(body)
        body.extend(f"xref\n0 {len(offsets)}\n".encode())
        body.extend(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            body.extend(f"{offset:010d} 00000 n \n".encode())
        body.extend(
            f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode()
        )
        return bytes(body)

    return make
