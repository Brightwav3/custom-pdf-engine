# FreeDF

FreeDF is a small, explicit, local-first PDF engine. It opens common unencrypted PDFs,
reports stable document and page facts, renders local previews, applies
structural page edits, and saves an independently valid copy.

It is a standalone package. The product is FreeDF; the Python package it
installs is `pdfengine`, and it stays that way — the compatibility policy in
`docs/contract-policy.md` does not let an import path move without a version
bump. So you install FreeDF and then `import pdfengine`.

Nothing in `src/pdfengine/` imports a host application, and the same contract
is available three ways:

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

## v0.2 capability matrix

Some rows are conditional rather than a flat yes: OCR needs a Tesseract
installation and previews need Poppler, and neither is bundled. That
conditionality is exactly what `capabilities` exists to report — it answers
`unavailable` when this installation cannot do something, and `blocked` when
this particular document will not allow it. Ask before you start a batch rather
than finding out halfway through.

| Area | Supported | Not supported in v0.2 |
| --- | --- | --- |
| Document structure | Classic `xref` tables, trailers, indirect objects, inherited page trees | xref streams, object streams, linearized-only recovery |
| Streams | Any filter opens and survives structural edits — streams are held as raw bytes and decoded only on demand. `/FlateDecode` decodes | Decoding every other filter (`LZWDecode`, `DCTDecode`, …), and `/FlateDecode` **with a `/Predictor`** |
| Security | Unencrypted documents | Encrypted documents, writing encryption |
| Edits | Reorder, delete, rotate, insert blank page, crop, extract, import/merge, metadata | Text editing, redaction, annotations, forms |
| OCR | Searchable PDFs via an invisible text layer, on all three surfaces — **requires a Tesseract installation**; without one, capability reports `unavailable` | Editing recognized text; a scan's text is an image |
| Rendering | PNG previews, thumbnails, high-DPI and batch rendering — **requires Poppler's `pdftoppm` on `PATH`**; without it, capability reports `unavailable` and everything else still works | A built-in graphics renderer, remote rendering |
| Saving | Full rewrite to a new file, opt-in fingerprinted in-place replacement | Incremental update |
| Contract | One frozen `v1` contract across Python, JSONL and HTTP, with a published compatibility policy | Multiple concurrent contract versions |

Anything unsupported is reported as a typed error that names the blocking
feature. The engine never guesses.

The stream row is the one worth reading twice. A document full of JPEGs opens
and can be reordered, because a structural edit never looks inside a stream.
What it cannot do is *read* those bytes — and `filters.decodable` is a flat list
of names that cannot express the predictor exception, so ask
`document.textContent` what is possible with the file in your hand.

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
