# pdf-engine

A small, explicit, local-first PDF engine. It opens common unencrypted PDFs,
reports stable document and page facts, renders local previews, applies
structural page edits, and saves an independently valid copy.

It is a standalone package. Nothing in `src/pdfengine/` imports a host
application, and the same contract is available three ways:

- a typed **Python library** — `from pdfengine import PdfEngine`
- a **JSONL command line** for scripts and AI agents — `pdfengine agent`
- a **loopback HTTP service** — `pdfengine serve`

All three route through one dispatcher, so a request means the same thing
whichever transport delivers it. See [`docs/api.md`](docs/api.md) for the
Python surface and [`docs/agent-guide.md`](docs/agent-guide.md) for the JSON
workflow.

## Install

```bash
pip install -e pdf-engine
```

Python 3.11+. Previews additionally need Poppler's `pdftoppm` on `PATH`;
without it, preview capability reports `blocked` and everything else still
works.

## Sixty-second tour

```python
from pdfengine import PdfEngine, RotatePages, DeletePages

engine = PdfEngine(cache_root="/tmp/pdfengine")
session = engine.open_document("report.pdf")

info = engine.inspect_document(session)
first, second = info.pages[0].page_id, info.pages[1].page_id

engine.apply_operations(session, [RotatePages((first,), 90), DeletePages((second,))])
output = engine.save(session)          # writes report-edited.pdf, never report.pdf
engine.close(session)
```

## v0.1 capability matrix

| Area | Supported | Not supported in v0.1 |
| --- | --- | --- |
| Document structure | Classic `xref` tables, trailers, indirect objects, inherited page trees | xref streams, object streams, linearized-only recovery |
| Streams | Unfiltered and `/FlateDecode` | Every other filter (`LZWDecode`, `DCTDecode`, …) |
| Security | Unencrypted documents | Encrypted documents, writing encryption |
| Edits | Reorder, delete, rotate, insert blank page, crop, extract, import/merge, metadata | Text editing, redaction, annotations, forms |
| Rendering | Local Poppler `pdftoppm` PNG previews and thumbnails | A built-in graphics renderer, remote rendering |
| Saving | Full rewrite to a new file, opt-in in-place replacement | Incremental update |

Anything unsupported is reported as a typed error that names the blocking
feature. The engine never guesses.

## Guarantees

- **Your source file is not modified.** Every edit is an entry in an
  immutable operation log, and a normal save writes a new file.
- **Pages are addressed by stable IDs**, assigned at open. An operation batch
  stays correct even after an earlier operation reorders or deletes pages.
- **In-place saves are opt-in and fingerprinted.** If the file changed on disk
  since it was opened, the save is refused.
- **Output is validated before it is published.** A save stages a temporary
  file, fsyncs it, reopens it through the reader, and only then replaces the
  target.
- **No network.** Rendering shells out only to a configured local executable,
  under a timeout, writing only below the session cache directory.

## Tests

```bash
python -m pytest pdf-engine/tests -q
```

Visual tests skip with an explicit reason when Poppler is absent.
