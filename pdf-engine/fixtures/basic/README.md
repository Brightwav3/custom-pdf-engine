# Fixtures

Small, literal PDFs. Each one exists to pin down a single behaviour, so a
regression points at a specific feature rather than "PDFs broke". Keep every
fixture under 20 KiB and record what it is for here.

## `basic/`

| File | Size | Feature it pins down |
| --- | --- | --- |
| `one-page.pdf` | 662 B | The happy path: classic xref, one page, `/Info /Title`, an uncompressed content stream, and a Type1 font resource. Expected facts: 1 page, 612 × 792, rotation 0, title `One page fixture`. |
| `with-image.pdf` | 1088 B | A filter the engine cannot decode. One 200 × 200 page draws an 8 × 8 `/DCTDecode` image `/XObject` through its content stream. Expected facts: 1 page, the image stream reads without error, `is_decodable` is false, `residual_filters` is `(/DCTDecode,)`, and `.data` raises. Saving must copy the JPEG bytes across verbatim — a writer that re-encodes stream bodies corrupts this file. |
| `inherited-pages.pdf` | 815 B | Inheritance. `/MediaBox`, `/Rotate`, and `/Resources` live only on the `/Pages` node, and the second leaf adds its own `/CropBox`. Expected facts: 2 pages, 595 × 842 rotated 90 and 300 × 400 rotated 90. A reader that ignores inheritance gets both pages wrong. |

## `unsupported/`

| File | Size | Feature it pins down |
| --- | --- | --- |
| `xref-stream.pdf` | 255 B | `startxref` points at a `/Type /XRef` object rather than a classic table. Opening it must raise `UnsupportedPdfError` naming `xref stream` — never a crash and never a silent misread. |

## Regenerating

The fixtures are committed as literal bytes on purpose: a test must not depend
on the same code it is checking. If one genuinely needs to change, edit the
bytes deliberately and update the expected facts in this table and in
`tests/test_roundtrip.py`.
