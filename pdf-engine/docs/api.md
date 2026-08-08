# Python API

FreeDF's Python surface. Everything public lives on the `pdfengine` package —
the distribution is named FreeDF, the import path is not. Import from the
package root.

```python
from pdfengine import PdfEngine, RotatePages, SaveOptions
```

## `PdfEngine`

```python
PdfEngine(
    cache_root: str | Path | None = None,
    renderer: PageRenderer | None = None,
    ocr: OcrEngine | None = None,
)
```

`cache_root` is where per-session preview caches live; it defaults to a
directory under the system temp dir. `renderer` defaults to `PopplerRenderer()`
and can be replaced with anything satisfying the `PageRenderer` protocol. `ocr`
defaults to `TesseractOcr()` and follows the same pattern.

Every method that takes a `session` accepts either a `DocumentSession` or its
`session_id` string.

| Method | Returns | Notes |
| --- | --- | --- |
| `open_document(path, password=None)` | `DocumentSession` | The password is held in memory only. |
| `session(session_id)` | `DocumentSession` | Raises `SessionStateError` if the ID was closed, `SessionNotFoundError` if it was never issued. |
| `tombstone(session_id)` | `SessionTombstone` | The record of a closed session: its ID, when it closed, and its state. |
| `inspect_document(session)` | `DocumentInfo` | Reflects the current operation cursor, not the file on disk. |
| `capabilities(session=None)` | `dict` | Preview and OCR state, the per-operation catalogue, decodable filters, and save constraints. With a session it also returns `document` and `allowedCommands`. |
| `renderer_capability()` | `RendererCapability` | Never raises. |
| `ocr_capability(language="eng", mode="lstm")` | `OcrCapability` | Never raises. A missing Tesseract comes back `unavailable`, not as an exception. |
| `render_page(session, page_id, width=1000)` | `RenderResult` | Cached; `cache_hit` says which. |
| `render_thumbnail(session, page_id, width=180)` | `RenderResult` | Same path, smaller default. |
| `apply_operations(session, operations, dry_run=False)` | `DocumentState` | Validates the whole batch; a `dry_run` leaves the session untouched. |
| `undo(session)` / `redo(session)` | `DocumentState` | Move the cursor only. |
| `default_target(session)` | `Path` | `<name>-edited.pdf`, avoiding any existing file. |
| `save(session, target=None, options=None)` | `Path` | Writes a new file unless you opt into replacing the source. |
| `close(session)` / `close_all()` | `None` | Drops the password, deletes the session cache, forgets its artifacts, and leaves a tombstone. |

`engine.artifacts` is the `ArtifactRegistry` holding every artifact the engine
has issued. `get(artifact_id, session_id)` returns one only to the session that
owns it; a missing artifact and another session's artifact produce the same
`InvalidRequestError`, so the ID cannot be used to probe. Artifact kinds are
`page_render`, `thumbnail`, and `saved_document`.

## Session lifecycle

A session ID is in exactly one of two states. While it is **open**, every
command works. Once `close()` runs it becomes **closed**, and the engine keeps a
`SessionTombstone` so a later call can be told *why* it failed: a closed ID
raises `SessionStateError` (`session_invalid_state`), an ID that was never
issued raises `SessionNotFoundError`. Closing is idempotent from the caller's
point of view in that the tombstone survives; the cache directory and the
password do not.

## Models

`PageInfo(index, width, height, rotation, page_id, source_index)` —
`index` is the position in the edited document; `source_index` is where the
page came from in its source file; `page_id` is the stable handle you pass to
operations.

`DocumentInfo(page_count, pages, title)`.

`RenderResult(page_id, width, height, image_bytes, cache_hit)` — `width` and
`height` are the real PNG pixel dimensions.

`SaveOptions(output_path=None, allow_replace_source=False, dry_run=False)`.

## Operations

Every operation is a frozen dataclass that validates itself on construction,
and every one targets pages by ID.

| Operation | Effect |
| --- | --- |
| `RotatePages(page_ids, degrees)` | Relative turn; `degrees` ∈ {90, 180, 270}. Accumulates and wraps at 360. |
| `DeletePages(page_ids)` | Removes pages. Refuses to empty the document. |
| `ReorderPages(page_ids)` | The new order. Must list every current page exactly once. |
| `ExtractPages(page_ids)` | Keeps only these, in this order. |
| `InsertBlankPage(after_page_id=None, width=612, height=792, page_id="")` | Inserts one page; `None` means the front. The generated `page_id` is fixed at construction. |
| `CropPages(page_ids, box)` | `box` is `(llx, lly, urx, ury)` in points, clipped to the media box. |
| `SetMetadata(entries)` | Keys: `title`, `author`, `subject`, `keywords`, `creator`, `producer`. A `None` value clears the entry. |
| `ImportPages(source_session_id, page_ids, after_page_id=None)` | Copies pages from another open session, with their resources. |
| `AddTextLayer(page_ids, language="eng", mode="lstm", dpi=300, min_confidence=0.0)` | Lays invisible, searchable OCR text over each page. Nothing about the page's appearance changes. Needs a working OCR engine; check `ocr_capability()` first. |

## Editing model

`DocumentState` holds the original page records, an ordered operation log, and
a cursor. Reads re-project from the originals through `operations[:cursor]`, so:

- applying an operation returns a **new** state and never mutates the old one;
- `undo()` and `redo()` only move the cursor;
- applying after an undo discards the abandoned redo branch.

```python
state = engine.apply_operations(session, [RotatePages((page_id,), 90)])
state.page_ids            # projected order
state.projected_pages()   # ProjectedPage records with rotation, boxes, origin
state.projected_metadata()
state.can_undo, state.can_redo
```

## Errors

All inherit `PdfEngineError` and carry a stable `.code`.

| Class | `code` | Raised when |
| --- | --- | --- |
| `PdfParseError` | `parse_error` | The bytes are not valid PDF. Has `.offset`. |
| `UnsupportedPdfError` | `unsupported_pdf` | A construct is outside the subset this version reads. Has `.feature`. Subclasses `PdfParseError`. |
| `InvalidOperationError` | `invalid_operation` | The operation is well-formed but wrong for this document. |
| `InvalidRequestError` | `invalid_request` | An external payload is malformed. Has `.field`. |
| `RendererUnavailableError` | `renderer_unavailable` | No working renderer is installed. |
| `RenderError` | `render_error` | A renderer is present but failed. |
| `OcrUnavailableError` | `ocr_unavailable` | No working OCR engine is installed. |
| `OcrError` | `ocr_error` | An OCR engine is present but failed to recognize a page. |
| `SourceChangedError` | `source_changed` | The source file changed under an open session. |
| `SessionNotFoundError` | `session_not_found` | The session ID was never issued. |
| `SessionStateError` | `session_invalid_state` | The session exists but its lifecycle state forbids the call — in practice, it was closed. Has `.session_id`, `.state`, `.attempted`, `.allowed`. |

`SessionNotFoundError` and `SessionStateError` are deliberately separate. "This
ID was closed" and "this ID was never issued" call for different fixes, and
collapsing them forces a caller to guess which one it hit. The JSON surfaces
carry the same split: a closed session reports `session_invalid_state` with
`details.sessionId`, `details.state` and `details.allowed`.

## Replacing the renderer

```python
class MyRenderer:
    version = "mine-1"

    def capability(self):
        from pdfengine.rendering.base import RendererCapability
        return RendererCapability("ready")

    def render(self, source, page_index, width, password, output_dir) -> bytes:
        ...  # return PNG bytes

engine = PdfEngine(cache_root="/tmp/c", renderer=MyRenderer())
```

`version` is part of the cache key, so bumping it invalidates old previews.
