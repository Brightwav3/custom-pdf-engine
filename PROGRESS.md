# Custom PDF Engine — Progress

**Where things stand:** v0.1, real-world document support, and OCR are all
built. The first two are merged; OCR is awaiting review.

| Milestone | State | Tests |
| --- | --- | --- |
| v0.1 — parser, editing, rendering, writer, three public surfaces | merged, PR #1 | 242 |
| 0 + 0b — real-world documents, correct previews, capability discovery, DPI and batch rendering | merged, PR #2 | 290 |
| 9 — OCR searchable PDF | **open, PR #3** | 383 |

## Project

- Local: `C:\Users\Sajmon\pdf engine`
- Package root: `pdf-engine/`
- Remote: `https://github.com/Brightwav3/custom-pdf-engine` (private)
- Active branch: `feat/ocr-text-layer`

Run the suite with:

```bash
python -m pytest pdf-engine/tests -q
```

383 tests, zero skipped — the real-Poppler and real-Tesseract tests all run on
this machine rather than skipping.

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

## Sub-project 0 — real-world documents (merged)

**The problem:** a single JPEG made a document unopenable. The reader decoded
every stream eagerly and rejected anything that was not Flate, so most real
PDFs could not be opened at all — which capped the value of every other feature
at zero.

**The insight:** structural edits never read stream contents. Reordering pages
does not care what is inside an image.

`PdfStream` now holds raw bytes plus its declared filter chain and decodes on
demand. Beyond the JPEG fix this bought byte-exact round-tripping (the writer
copies raw bytes, so untouched streams are no longer re-deflated at a different
level), faster opens and saves, and exactly the API the OCR and text stacks
needed.

**The preview bug.** `render_page` keyed its cache on the projected rotation and
crop, so an edit correctly missed the cache — and then re-rendered the *original*
page, which had neither. Previews now come from a materialized copy of the
projected state, so what is rendered is what a save would produce.

That bug shipped because `StubRenderer` returned a fixed-size PNG regardless of
input, making an assertion about page geometry impossible to write. The old test
could only ever count renderer calls. `GeometryRenderer` reads the file it is
handed; against the old code the new tests fail with
`assert (612, 792) == (792, 612)`.

## Sub-project 0b — capability discovery and faster rendering (merged)

`capabilities(session)` gained a `read` section so an agent learns *before* it
tries that reordering will work and text extraction will not:

```
with-image.pdf   structural=ready   text=blocked   filters=['DCTDecode']
one-page.pdf     structural=ready   text=ready     filters=[]
```

Computed lazily and cached, so opening a large scan stays cheap. Plus
`render_at_dpi` (the OCR feed) and `render_range` (one `pdftoppm` call for a
whole thumbnail strip instead of one process per page).

## Sub-project 9 — OCR searchable PDF (PR #3)

An OCR text layer is *additive*: it appends an invisible layer over the page
rather than rewriting existing content. It therefore needs none of the content
stream parser or font model that text editing requires, which is why it shipped
first.

```python
engine.apply_operations(session, [AddTextLayer((page_id,), language="ces", dpi=300)])
engine.save(session, "searchable.pdf")
```

Verified end to end against the real install:

```
ocr capability   state=ready  engine=tesseract v5.4.0  modes=['lstm']
source           662 B  ->  searchable 1930 B
source bytes     unchanged
invisible layer  "3 Tr" present in the saved content stream
reopens          cleanly
```

`modes: ['lstm']` is the capability probe working correctly, not a limitation
of the code — see decision 6.

Recognition stays out of the projection. `DocumentState.apply` has no I/O and
only validates and records the request; `PdfEngine.apply_operations` rasterizes
and recognizes. A result recognized under different settings is not treated as
an answer, so changing DPI, language, or mode re-runs, while `min_confidence` —
which filters at write time — does not.

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
   character-level boxes, and the capability probe *runs* each mode rather than
   inferring it from a version number or a file size.
7. **The invisible text layer uses a glyphless CIDFontType2.** Invisible text
   never draws a glyph: extraction reads the `ToUnicode` CMap and selection uses
   the width array. So a ~1 KB glyphless font gives full Unicode coverage with
   no embedding or subsetting. Czech forces this — Latin-1 cannot encode
   `ř`, `ů`, or `ě`.
8. **CJK words are joined without a separator.** Tesseract returns
   `日本語のテキスト` as `日 本 語 の テキ スト`; written verbatim, a search for
   `日本語` would not match. The join is decided by Unicode range, not by a
   language check.

## Bugs found by review rather than by tests

- **Flate with a `/Predictor` was silently wrong.** `residual_filters` treated
  `FlateDecode` as decodable regardless of `/DecodeParms`, so a predicted stream
  would inflate and hand back un-predicted bytes. Harmless until something reads
  `.data`; silent corruption the moment the text stack lands.
- **The PNG padder rejected the images it exists to pad.** It was written for
  8-bit grayscale because the brief said `render_at_dpi` produces it.
  `pdftoppm -png -gray` renders in grayscale but still writes an **RGB** PNG.
  The near-miss underneath: adding RGB support while leaving filter
  reconstruction at one byte per pixel would have *silently corrupted* every
  non-grayscale image instead of rejecting it — and corrupted pixels reaching
  OCR produce slightly-wrong text, which is very hard to trace.
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
- **A test located an argument by position.** `command[-8]` pointed at the wrong
  argv entry; the resulting `FileNotFoundError` was swallowed by the adapter's
  own handler and reported as a missing executable — an error three layers from
  its cause. Now located by suffix.

## Things only running the tool revealed

Each of these would have cost a build cycle if it had been reasoned about
instead of measured.

- **A quiet zone is mandatory.** Text touching the image border makes Tesseract
  return *empty output* — no warning, no partial result. Pages are padded before
  recognition and the padding is subtracted when mapping boxes back.
- **Never invoke Tesseract by config-file name.** A custom `--tessdata-dir` has
  no `configs/` subdirectory, so `tesseract … tsv` fails with
  `read_params_file: Can't open tsv`. Output is requested with
  `-c tessedit_create_tsv=1`, which is installation-independent.
- **`--psm 3` beats 6 and 7** on Chinese, which misread a character under both.

## Process notes

Subagent worktrees branch from `main`, not from the active branch. Three agents
landed on the wrong base and all three correctly stopped at a baseline
test-count gate rather than building on it. That gate paid for itself
repeatedly. Briefs now name the exact commit to land on, after the base has been
pushed.

A `cd … && git reset --hard` instruction survived a failed `cd` and ran against
a shared checkout. Nothing was lost, by timing alone. Briefs now forbid
`reset --hard` outright and require staging only owned files.

Two agents were killed mid-task by an account spend limit, having written
substantial work without committing it. Both were salvaged from their worktrees
and finished in the main loop rather than re-run.

PowerShell here-string syntax leaking into a bash heredoc corrupted a commit
subject with a stray `@`. Amended; worth knowing the two shells are both
available and take different syntax.

---

## Roadmap

| # | Sub-project | Depends on | State |
| --- | --- | --- | --- |
| 0 | Filter passthrough, preview correctness | — | merged |
| 0b | Capability split, DPI and batch rendering | 0 | merged |
| 9 | OCR searchable PDF | 0b | PR #3 |
| 9b | Expose `AddTextLayer` over JSON, CLI, and HTTP | 9 | not started |
| 1 | Content stream parser and font model | — | not started |
| 2 | Text extraction and search | 1 | not started |
| 3 | Text editing, span replace without reflow | 2 | not started |
| 4 | Annotations | — | not started |
| 6 | Form filling | — | not started |
| 7 | Watermarks, headers, Bates numbering | 1 | not started |
| 8 | Optimize: image downsampling, font subsetting | 0 | not started |

### Known gap

`AddTextLayer` works from Python but is not yet exposed through `contracts.py`,
the CLI, or HTTP, so agents cannot call it over JSON. That breaks the project's
own rule that all three surfaces stay equivalent, and the parity test does not
catch it because it only exercises commands that already exist. Small and
well-scoped — hence 9b above.

### Open questions

- **Is text editing worth sub-project 1?** A content stream parser plus a font
  model is larger than everything built so far combined, and it only pays off
  through text editing. Annotations, form filling, and watermarks are each a
  fraction of that work and read as premium features immediately.
- **Scanned documents cannot be text-edited.** Once a scan has an OCR text
  layer it is searchable, but "editing the text" means editing an image —
  inpainting, not text editing. Worth deciding whether sub-project 3 targets
  born-digital PDFs only.
