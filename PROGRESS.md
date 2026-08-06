# Custom PDF Engine — Progress

**Where things stand:** v0.1 is merged. Sub-projects 0 and 0b are built and
awaiting review. OCR is under construction.

| Milestone | State | Tests |
| --- | --- | --- |
| v0.1 — parser, editing, rendering, writer, three public surfaces | merged, PR #1 | 242 |
| 0 + 0b — real-world documents, correct previews, capability discovery, DPI and batch rendering | **open, PR #2** | 290 |
| 9 — OCR searchable PDF | in progress on the same branch | 303 and climbing |

## Project

- Local: `C:\Users\Sajmon\pdf engine`
- Package root: `pdf-engine/`
- Remote: `https://github.com/Brightwav3/custom-pdf-engine` (private)
- Active branch: `feat/real-world-documents`

Run the suite with:

```bash
python -m pytest pdf-engine/tests -q
```

---

## v0.1 — merged

All fourteen tasks of the original plan. A standalone package with a `PdfEngine`
façade, a custom classic-xref parser, an immutable operation log, a full-rewrite
writer, a Poppler-backed renderer behind an engine-owned protocol, and three
equal public surfaces — typed Python, a JSONL agent CLI, and a loopback HTTP
service — all routed through one dispatcher and held to that by a parity test.

Guarantees the suite pins down: source files are never modified by a normal
save; pages are addressed by stable IDs rather than positions; in-place saves
are opt-in and fingerprinted; output is reopened and validated before it
replaces anything; a missing Poppler is a reported capability, not a crash.

## Sub-project 0 — real-world documents (PR #2)

**The problem:** a single JPEG made a document unopenable. The reader decoded
every stream eagerly and rejected anything that was not Flate, so most real
PDFs could not be opened at all — which capped the value of every other feature
at zero.

**The insight:** structural edits never read stream contents. Reordering pages
does not care what is inside an image.

`PdfStream` now holds raw bytes plus its declared filter chain and decodes on
demand. Beyond the JPEG fix this bought byte-exact round-tripping (the writer
copies raw bytes, so untouched streams are no longer re-deflated at a different
level), faster opens and saves, and exactly the API the text stack will need.

**The preview bug.** `render_page` keyed its cache on the projected rotation and
crop, so an edit correctly missed the cache — and then re-rendered the *original*
page, which had neither. Previews now come from a materialized copy of the
projected state, so what is rendered is what a save would produce.

That bug shipped because `StubRenderer` returned a fixed-size PNG regardless of
input, making an assertion about page geometry impossible to write. The old test
could only ever count renderer calls. `GeometryRenderer` reads the file it is
handed; against the old code the new tests fail with
`assert (612, 792) == (792, 612)`.

## Sub-project 0b — capability discovery and faster rendering (PR #2)

`capabilities(session)` gained a `read` section so an agent learns *before* it
tries that reordering will work and text extraction will not:

```
with-image.pdf   structural=ready   text=blocked   filters=['DCTDecode']
one-page.pdf     structural=ready   text=ready     filters=[]
```

Computed lazily and cached, so opening a large scan stays cheap. Plus
`render_at_dpi` (300 DPI grayscale, the OCR feed) and `render_range` (one
`pdftoppm` call for a whole thumbnail strip instead of one process per page).

## Sub-project 9 — OCR searchable PDF (in progress)

An OCR text layer is *additive*: it appends an invisible layer over the page
rather than rewriting existing content. It therefore needs none of the content
stream parser or font model that text editing requires, and can ship first.

Built so far: the recognition contract (`OcrPage`, `OcrWord`, `OcrChar`, the
`OcrEngine` protocol, `OcrCapability`). Under construction: the Tesseract
adapter and TSV parser; the pixel-to-user-space transform and the glyphless
font. Still to come: the `AddTextLayer` operation and writer integration.

### Environment, verified rather than assumed

```
Tesseract 5.4.0    C:\Program Files\Tesseract-OCR    auto-discovered, not on PATH
12 models          C:\Users\Sajmon\tessdata          126.5 MB, via --tessdata-dir
Poppler 25.07.0    on PATH
```

Languages: `ara ces chi_sim chi_tra deu eng fra jpn kor osd rus spa`.

Confirmed end to end against real images: Czech
(`příliš žluťoučký kůň úpěl ďábelské ódy`, exact), Arabic, Cyrillic, Japanese,
and Chinese.

---

## Decisions and why

1. **Operations address pages by stable ID, not index.** Task 1 had shipped
   `page_indices`, contradicting the plan's own constraint. An ID-addressed
   batch stays correct after an earlier operation reorders or deletes pages.
2. **`CropBox` is intersected with `MediaBox`** before deriving visible
   dimensions, per spec. A non-overlapping crop is rejected.
3. **`UnsupportedPdfError` subclasses `PdfParseError`**, so callers that only
   care "this file could not be read" keep working while agents branch on
   `.feature`.
4. **Streams are copied verbatim on write.** Earlier they were decoded and
   re-deflated, which changed bytes for no reason.
5. **Blank-page IDs are fixed at construction**, so replaying the operation log
   is deterministic.
6. **OCR defaults to LSTM with the engine mode configurable.** The original
   request was to force legacy (`--oem 0`). Testing it on this machine returned
   `Error: Tesseract (legacy) engine requested, but components are not present`
   — this install ships LSTM-only data. Hardcoding legacy would have shipped a
   dead feature. Legacy stays selectable for the one thing it offers,
   character-level boxes, and the capability probe reports which modes really
   work.
7. **The invisible text layer uses a glyphless CIDFontType2.** Invisible text
   never draws a glyph: extraction reads the `ToUnicode` CMap and selection uses
   the width array. So a ~1 KB glyphless font gives full Unicode coverage with
   no embedding or subsetting. Czech forces this — Latin-1 cannot encode
   `ř`, `ů`, or `ě`.

## Bugs found by review rather than by tests

- **Flate with a `/Predictor` was silently wrong.** `residual_filters` treated
  `FlateDecode` as decodable regardless of `/DecodeParms`, so a predicted stream
  would inflate and hand back un-predicted bytes. Harmless until something reads
  `.data`; silent corruption the moment the text stack lands.
- **A user-facing error leaked a dataclass repr** —
  `stream filter PdfName(value='DCTDecode')`. The test matched a substring and
  passed anyway.
- **`pytest -q` hid its own summary.** `addopts` carried `-q`, so the command
  documented throughout the repo resolved to `-qq` and printed only progress
  dots. It cost two agents a detour through `--collect-only`.
- **A specified test was worthless.** A batch-render ordering test using
  `p-01.png … p-10.png` could not detect lexical sorting, because zero-padding
  makes lexical and numeric order identical. Rewritten to give each page a
  distinct width and assert the byte sequence.

## Process notes

Subagent worktrees branch from `main`, not from the active branch. Two agents
landed on the merged v0.1 without the work their task depended on, and both
correctly stopped at a baseline test-count gate rather than building on the
wrong foundation. Briefs now carry an explicit fetch-and-fast-forward step and a
commit to verify.

A `cd … && git reset --hard` instruction survived a failed `cd` and ran against
a shared checkout. Nothing was lost, by timing alone. Briefs now forbid
`reset --hard` outright and require staging only owned files.

---

## Roadmap

| # | Sub-project | Depends on | State |
| --- | --- | --- | --- |
| 0 | Filter passthrough, preview correctness | — | done, PR #2 |
| 0b | Capability split, DPI and batch rendering | 0 | done, PR #2 |
| 9 | OCR searchable PDF | 0b | in progress |
| 1 | Content stream parser and font model | — | not started |
| 2 | Text extraction and search | 1 | not started |
| 3 | Text editing, span replace without reflow | 2 | not started |
| 4 | Annotations | — | not started |
| 6 | Form filling | — | not started |
| 7 | Watermarks, headers, Bates numbering | 1 | not started |
| 8 | Optimize: image downsampling, font subsetting | 0 | not started |

### Open questions

- **Is text editing worth sub-project 1?** A content stream parser plus a font
  model is larger than everything built so far combined, and it only pays off
  through text editing. Annotations, form filling, and watermarks are each a
  fraction of that work and read as premium features immediately.
- **Scanned documents cannot be text-edited.** Once a scan has an OCR text
  layer it is searchable, but "editing the text" means editing an image —
  inpainting, not text editing. Worth deciding whether sub-project 3 targets
  born-digital PDFs only.
- **PR #2 is growing.** OCR is landing on the same branch. Merging PR #2 before
  OCR completes would keep the two reviewable separately.
