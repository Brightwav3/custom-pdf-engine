# Custom PDF Engine v0.1 Progress

Status: **all 14 plan tasks complete.** `242 passed`.

## Project

- Local project: `C:\Users\Sajmon\pdf engine`
- Package root: `pdf-engine/`
- GitHub remote: `https://github.com/Brightwav3/custom-pdf-engine` (private)
- Feature branch: `feat/pdf-engine-page-tree`

## Completed

| Task | What landed | Commit |
| --- | --- | --- |
| 1 | Public immutable models, errors, package metadata, fixture factory | `c004be0`, `2282c8f` |
| 2 | PDF value types and recursive byte tokenizer | `eb346bf`, `2562a56` |
| 3 | Classic xref reader, trailer/object resolution, streams, FlateDecode | `8e971d3`, `285662f` |
| 4 | Page-tree model with inheritance, cycle rejection, stable page IDs | `b10147e` |
| 5 | ID-based operation models and immutable `DocumentState` with undo/redo | `7f5e33d` |
| 6, 7 | `PageRenderer` protocol, Poppler adapter, content-addressed render cache | `eeb4b42` |
| 8, 9 | `FullRewriteWriter` and cross-session page import | `3f97ca5` |
| 10 | `PdfEngine` façade, `DocumentSession`, fingerprinting, save policy | `9b60731` |
| 11, 12, 13 | v1 JSON contracts and schemas, JSONL agent CLI, loopback HTTP service | `8a391ea` |
| 14 | Literal fixtures, roundtrip/visual/parity tests, docs, release gate | this commit |

## Decisions taken during the build

1. **Operations migrated from page indexes to stable page IDs.** Task 1 had
   shipped `RotatePagesOperation(page_indices=…)`, which contradicts the plan's
   global constraint. Task 5 replaced the whole operation set with the
   ID-based models the plan specifies and dropped `AddTextOperation`, which is
   explicitly out of scope for v0.1.
2. **`CropBox` is intersected with `MediaBox`** before deriving visible page
   dimensions, per the PDF spec. A non-overlapping crop is rejected.
3. **`UnsupportedPdfError` subclasses `PdfParseError`**, so existing callers
   that only care "this file could not be read" keep working while agents can
   branch on `.feature`.
4. **Copied streams are re-emitted as Flate.** The reader hands back decoded
   bytes; recompressing on write keeps output small and always re-readable.
5. **Blank-page IDs are fixed at operation construction**, so replaying the
   operation log is deterministic.

## Known v0.1 limits (by design, documented)

- Only classic xref, unencrypted, unfiltered-or-Flate documents. A page whose
  resources use another filter (for example a JPEG image) is reported as
  `unsupported_pdf`, not silently mangled.
- No text editing, redaction, annotations, forms, or encryption output.
- Previews require a local Poppler `pdftoppm`; without it preview capability
  is `blocked` and every other feature still works.

## Verification

```bash
python -m pytest pdf-engine/tests -q
```
