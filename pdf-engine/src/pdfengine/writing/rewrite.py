"""Serialize a projected document state as a brand new classic PDF.

v0.1 always performs a full rewrite: only the objects reachable from the
pages that survive the operation log are copied, so deleted pages and
cleared metadata leave nothing behind. The source file is never touched —
output is staged in a sibling temporary file, fsynced, reopened through
:class:`PdfReader` for structural validation, and only then moved into place.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from pdfengine.api.models import SaveOptions
from pdfengine.document.pages import DocumentModel
from pdfengine.editing.state import DocumentState, ProjectedPage
from pdfengine.errors import PdfEngineError, PdfParseError
from pdfengine.parser.reader import PdfReader, PdfStream
from pdfengine.parser.values import PdfArray, PdfDictionary, PdfName, PdfReference, PdfString


_HEADER = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
_CATALOG = 1
_PAGE_TREE = 2
_NAME_SAFE = set(range(ord("!"), ord("~") + 1)) - set(b"()<>[]{}/%#")
_INFO_KEYS = {
    "title": "Title",
    "author": "Author",
    "subject": "Subject",
    "keywords": "Keywords",
    "creator": "Creator",
    "producer": "Producer",
}


class FullRewriteWriter:
    """Write the current projection of a state to a new PDF file."""

    def write(
        self,
        state: DocumentState,
        readers: Mapping[str | None, PdfReader],
        target: str | Path,
        options: SaveOptions | None = None,
    ) -> Path:
        options = options or SaveOptions()
        target = Path(target)
        pages = state.projected_pages()
        if not pages:
            raise PdfEngineError("cannot save a document with no pages")
        if target.exists() and not options.allow_replace_source:
            raise PdfEngineError(f"refusing to overwrite an existing file: {target}")
        if not target.parent.is_dir():
            raise PdfEngineError(f"output directory does not exist: {target.parent}")

        document = _build(pages, state.projected_metadata(), readers)
        staged = target.with_name(target.name + ".tmp")
        try:
            with open(staged, "wb") as handle:
                handle.write(document)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                DocumentModel.from_reader(PdfReader(staged))
            except PdfParseError as exc:
                raise PdfEngineError(f"generated PDF failed validation: {exc}") from exc
            os.replace(staged, target)
        finally:
            Path(staged).unlink(missing_ok=True)
        return target


# -- object graph ---------------------------------------------------------


class _Graph:
    """Allocate new object numbers and copy source objects exactly once."""

    def __init__(self) -> None:
        self.objects: dict[int, object] = {}
        self._next = _PAGE_TREE + 1
        self._copied: dict[tuple[str | None, int, int], int] = {}

    def allocate(self) -> int:
        number = self._next
        self._next += 1
        return number

    def copy(self, session: str | None, reader: PdfReader, ref: PdfReference) -> int:
        key = (session, ref.object_number, ref.generation)
        existing = self._copied.get(key)
        if existing is not None:
            return existing
        number = self.allocate()
        # Registered before recursing so a cyclic reference resolves here.
        self._copied[key] = number
        self.objects[number] = self.rewrite(session, reader, reader.resolve(ref))
        return number

    def claim(self, session: str | None, ref: PdfReference, number: int) -> None:
        """Point a source reference at an object this writer builds itself."""

        self._copied[(session, ref.object_number, ref.generation)] = number

    def rewrite(self, session: str | None, reader: PdfReader, value: object) -> object:
        if isinstance(value, PdfReference):
            return PdfReference(self.copy(session, reader, value), 0)
        if isinstance(value, PdfArray):
            return PdfArray(
                tuple(self.rewrite(session, reader, item) for item in value.items)
            )
        if isinstance(value, PdfDictionary):
            return PdfDictionary(
                {
                    name: self.rewrite(session, reader, item)
                    for name, item in value.entries.items()
                }
            )
        if isinstance(value, PdfStream):
            # Streams are copied verbatim. Re-encoding a body this engine
            # never decoded would corrupt it, and re-encoding one it did
            # decode would needlessly churn bytes the user did not edit, so
            # /Filter and /DecodeParms travel with the raw bytes unchanged.
            entries: dict[PdfName, object] = {
                name: self.rewrite(session, reader, item)
                for name, item in value.dictionary.entries.items()
                if name.value != "Length"
            }
            entries[PdfName("Length")] = len(value.raw)
            return PdfStream(
                PdfDictionary(entries),
                value.raw,
                value.filters,
                value.decode_parms,
            )
        return value


def _build(
    pages: tuple[ProjectedPage, ...],
    metadata: Mapping[str, str | None],
    readers: Mapping[str | None, PdfReader],
) -> bytes:
    graph = _Graph()
    page_numbers: list[int] = []

    for page in pages:
        number = graph.allocate()
        page_numbers.append(number)
        if page.is_blank:
            graph.objects[number] = _blank_page(graph, page)
            continue

        session = page.source_session_id
        reader = readers.get(session)
        if reader is None:
            raise PdfEngineError(f"no open source document for session {session!r}")
        record = page.source
        assert record is not None
        if record.reference is not None:
            # An /Annots /P back-reference must land on the page we emit here,
            # not on a second copy of the original page object.
            graph.claim(session, record.reference, number)
        graph.objects[number] = _copied_page(graph, reader, page)

    graph.objects[_CATALOG] = PdfDictionary(
        {
            PdfName("Type"): PdfName("Catalog"),
            PdfName("Pages"): PdfReference(_PAGE_TREE, 0),
        }
    )
    graph.objects[_PAGE_TREE] = PdfDictionary(
        {
            PdfName("Type"): PdfName("Pages"),
            PdfName("Count"): len(page_numbers),
            PdfName("Kids"): PdfArray(
                tuple(PdfReference(number, 0) for number in page_numbers)
            ),
        }
    )

    info_number = _info_object(graph, metadata)
    return _serialize_document(graph.objects, info_number)


def _source_page_dictionary(reader: PdfReader, page: ProjectedPage) -> PdfDictionary:
    record = page.source
    assert record is not None
    if record.reference is None:
        return PdfDictionary({})
    value = reader.resolve(record.reference)
    if not isinstance(value, PdfDictionary):
        raise PdfEngineError("source page object is not a dictionary")
    return value


def _copied_page(graph: _Graph, reader: PdfReader, page: ProjectedPage) -> PdfDictionary:
    session = page.source_session_id
    source = _source_page_dictionary(reader, page)
    entries: dict[PdfName, object] = {}
    for name, value in source.entries.items():
        if name.value in ("Parent", "Type", "MediaBox", "CropBox", "Rotate", "Resources"):
            continue
        entries[name] = graph.rewrite(session, reader, value)

    record = page.source
    assert record is not None
    if record.resources is not None:
        entries[PdfName("Resources")] = graph.rewrite(session, reader, record.resources)
    if PdfName("Contents") not in entries:
        entries[PdfName("Contents")] = PdfReference(_blank_contents(graph), 0)

    entries[PdfName("Type")] = PdfName("Page")
    entries[PdfName("Parent")] = PdfReference(_PAGE_TREE, 0)
    entries[PdfName("MediaBox")] = _box(page.media_box)
    if page.crop_box is not None:
        entries[PdfName("CropBox")] = _box(page.crop_box)
    if page.rotation:
        entries[PdfName("Rotate")] = page.rotation
    return PdfDictionary(entries)


def _blank_page(graph: _Graph, page: ProjectedPage) -> PdfDictionary:
    return PdfDictionary(
        {
            PdfName("Type"): PdfName("Page"),
            PdfName("Parent"): PdfReference(_PAGE_TREE, 0),
            PdfName("MediaBox"): _box(page.media_box),
            PdfName("Resources"): PdfDictionary({}),
            PdfName("Contents"): PdfReference(_blank_contents(graph), 0),
        }
    )


def _blank_contents(graph: _Graph) -> int:
    number = graph.allocate()
    graph.objects[number] = PdfStream(PdfDictionary({PdfName("Length"): 0}), b"")
    return number


def _info_object(graph: _Graph, metadata: Mapping[str, str | None]) -> int | None:
    entries = {
        PdfName(_INFO_KEYS[name]): PdfString(_encode_text(value))
        for name, value in metadata.items()
        if name in _INFO_KEYS and value is not None
    }
    if not entries:
        return None
    number = graph.allocate()
    graph.objects[number] = PdfDictionary(entries)
    return number


def _encode_text(value: str) -> bytes:
    try:
        return value.encode("ascii")
    except UnicodeEncodeError:
        return b"\xfe\xff" + value.encode("utf-16-be")


def _box(box: tuple[float, float, float, float]) -> PdfArray:
    return PdfArray(tuple(float(value) for value in box))


# -- serialization --------------------------------------------------------


def _serialize_document(objects: Mapping[int, object], info_number: int | None) -> bytes:
    highest = max(objects)
    body = bytearray(_HEADER)
    offsets: dict[int, int] = {}
    for number in range(1, highest + 1):
        if number not in objects:
            continue
        offsets[number] = len(body)
        body.extend(f"{number} 0 obj\n".encode("ascii"))
        body.extend(_serialize(objects[number]))
        body.extend(b"\nendobj\n")

    xref_offset = len(body)
    body.extend(f"xref\n0 {highest + 1}\n".encode("ascii"))
    body.extend(b"0000000000 65535 f \n")
    for number in range(1, highest + 1):
        if number in offsets:
            body.extend(f"{offsets[number]:010d} 00000 n \n".encode("ascii"))
        else:
            body.extend(b"0000000000 65535 f \n")

    trailer = f"trailer\n<< /Size {highest + 1} /Root {_CATALOG} 0 R"
    if info_number is not None:
        trailer += f" /Info {info_number} 0 R"
    trailer += f" >>\nstartxref\n{xref_offset}\n%%EOF\n"
    body.extend(trailer.encode("ascii"))
    return bytes(body)


def _serialize(value: object) -> bytes:
    if value is None:
        return b"null"
    if isinstance(value, bool):
        return b"true" if value else b"false"
    if isinstance(value, int):
        return str(value).encode("ascii")
    if isinstance(value, float):
        return _number(value).encode("ascii")
    if isinstance(value, PdfName):
        return b"/" + _name(value.value)
    if isinstance(value, PdfReference):
        return f"{value.object_number} {value.generation} R".encode("ascii")
    if isinstance(value, PdfString):
        return b"(" + _literal(value.value) + b")"
    if isinstance(value, PdfArray):
        return b"[" + b" ".join(_serialize(item) for item in value.items) + b"]"
    if isinstance(value, PdfDictionary):
        parts = b" ".join(
            b"/" + _name(name.value) + b" " + _serialize(item)
            for name, item in value.entries.items()
        )
        return b"<< " + parts + b" >>" if parts else b"<< >>"
    if isinstance(value, PdfStream):
        return (
            _serialize(value.dictionary) + b"\nstream\n" + value.raw + b"\nendstream"
        )
    raise PdfEngineError(f"cannot serialize value of type {type(value).__name__}")


def _number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _name(value: str) -> bytes:
    out = bytearray()
    for byte in value.encode("utf-8"):
        if byte in _NAME_SAFE:
            out.append(byte)
        else:
            out.extend(f"#{byte:02X}".encode("ascii"))
    return bytes(out)


def _literal(value: bytes) -> bytes:
    out = bytearray()
    for byte in value:
        if byte in b"()\\":
            out.extend(b"\\" + bytes([byte]))
        elif byte < 32 or byte > 126:
            out.extend(f"\\{byte:03o}".encode("ascii"))
        else:
            out.append(byte)
    return bytes(out)
