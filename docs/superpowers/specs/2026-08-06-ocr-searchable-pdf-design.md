# Sub-project 9: OCR and searchable PDFs — design

**Goal:** turn a scanned PDF into one that can be searched, selected, and copied
from in any viewer, by recognizing the page image and laying invisible text over
it.

**Why this can jump the queue:** an OCR text layer is *additive*. It appends a
content stream over the existing page rather than rewriting one. It therefore
needs none of the content-stream parser or font model that text editing
requires, and can ship long before them.

---

## Engine choice

Both Tesseract engines are trained models that run locally, offline, and cannot
invent text. The meaningful differences:

| | `--oem 0` legacy | `--oem 1` LSTM |
| --- | --- | --- |
| Kind | Trained character classifier, adaptive pattern matching | Recurrent neural network |
| Accuracy on real scans | Lower | Higher |
| Character-level boxes | Yes | No — word and line only |

**Decision: the mode is a per-job setting, defaulting to LSTM.** Legacy stays
selectable for the one thing it genuinely offers, character-level boxes.

### The legacy traineddata trap

`--oem 0` needs traineddata containing legacy components. `tessdata_best` and
`tessdata_fast` are LSTM-only and fail with *"Tesseract (legacy) engine
requested, but components are not present"*. The capability check must probe the
requested mode against the installed language data and report the mismatch
before a job starts, not halfway through one.

---

## Components

Each is separately testable and behind an interface, matching how `PageRenderer`
is already structured.

### 1. DPI rasterization — `rendering/poppler.py`

The renderer currently scales to a pixel width. OCR needs a known DPI, because
the pixel-to-point transform depends on it.

```python
def render_at_dpi(self, source, page_index, dpi, password, output_dir) -> bytes:
    """pdftoppm -r <dpi> -gray -png -singlefile -f n -l n"""
```

300 DPI is the default. Grayscale, because Tesseract discards colour anyway and
the intermediate file is a third the size.

### 2. `OcrEngine` protocol — `ocr/base.py`

```python
class OcrEngine(Protocol):
    version: str
    def capability(self, language: str, mode: str) -> RendererCapability: ...
    def languages(self) -> tuple[str, ...]: ...
    def recognize(self, image: Path, dpi: int, language: str, mode: str) -> OcrPage: ...
```

Reusing `RendererCapability(state, detail)` keeps one capability vocabulary
across the engine.

### 3. `TesseractOcr` adapter — `ocr/tesseract.py`

Invokes only a configured, validated executable with an explicit argument list,
under a timeout — the same safety envelope as `PopplerRenderer`.

- Output format is **TSV** (`tesseract img out tsv`). It is line-oriented,
  carries `left top width height conf text` per word, and needs no XML parser.
- `--oem 0` additionally requests character boxes via `makebox`; the extra call
  is made only in legacy mode.
- `languages()` shells `--list-langs`; unknown languages are rejected up front.
- A missing binary is a `blocked` capability, never a crash.

### 4. Result model — `ocr/models.py`

```python
@dataclass(frozen=True)
class OcrWord:
    text: str
    box: tuple[float, float, float, float]   # pixels, image space, origin top-left
    confidence: float
    characters: tuple[OcrChar, ...] = ()     # populated in legacy mode only

@dataclass(frozen=True)
class OcrPage:
    words: tuple[OcrWord, ...]
    width: int
    height: int
    dpi: int
    language: str
    mode: str
```

### 5. Coordinate transform — `ocr/layout.py`

Image space has its origin top-left in pixels; PDF user space has it bottom-left
in points.

```
x_pt = x_px * 72 / dpi
y_pt = page_height_pt - (y_px + h_px) * 72 / dpi
```

The page's own `/Rotate` and `/CropBox` must be applied, or text lands in the
wrong place on any rotated scan. This module is pure arithmetic and gets direct
unit tests with hand-computed expectations.

### 6. Invisible text layer — `writing/textlayer.py`

Per word, emitted into an appended content stream:

```
BT 3 Tr /OCR 10 Tf <Tz> Tz 1 0 0 1 <x> <y> Tm <cid-hex> Tj ET
```

`3 Tr` is invisible render mode. `Tz` scales horizontally so the string's
advance matches the OCR box width, which is what makes a viewer's selection
highlight line up with the scanned word underneath.

### 7. The glyphless font

**Invisible text never draws a glyph.** Extraction reads the `ToUnicode` CMap
and selection uses the width array; the glyph outlines are never rendered. So
instead of embedding and subsetting a real Unicode font, embed a **glyphless
CIDFontType2**: one empty glyph, `Identity-H` encoding, a uniform `/W` width,
and a generated `/ToUnicode` CMap mapping each CID to its true codepoint.

This is about 1 KB, needs no subsetting, and covers every script Tesseract can
read — including Czech, which Latin-1 cannot represent.

The font program is vendored under `pdfengine/resources/` with its license
recorded in a `NOTICE` file. **Confirm the license before vendoring**; if it
cannot be cleanly redistributed, generate an equivalent minimal glyphless
TrueType instead.

### 8. The operation — `api/models.py`

```python
@dataclass(frozen=True)
class AddTextLayer:
    page_ids: tuple[str, ...]
    language: str = "eng"
    mode: str = "lstm"            # "lstm" | "legacy"
    dpi: int = 300
    min_confidence: float = 0.0
    kind: ClassVar[str] = "add_text_layer"
```

It follows the existing rules: frozen, self-validating, addresses pages by
stable ID. Recognition results are attached to the projected page so the writer
can emit the layer, and so a `dryRun` can report what *would* be added without
writing a file.

### 9. Capability reporting

Extends the `read` section introduced in sub-project 0b:

```json
"ocr": {
  "state": "ready",
  "engine": "tesseract 5.3.4",
  "modes": ["lstm", "legacy"],
  "languages": ["ces", "deu", "eng"],
  "detail": ""
}
```

`"legacy"` appears in `modes` only when the installed traineddata actually
supports it.

---

## Testing

Tesseract is not assumed present. The suite follows the Poppler precedent:

| Layer | How it is tested |
| --- | --- |
| Adapter | `subprocess.run` is monkeypatched; assert exact argv, `--oem` mapping, timeout, missing binary, non-zero exit, unparsable TSV |
| TSV parsing | Literal TSV fixtures, including the header row, low-confidence rows, and the `-1` confidence rows Tesseract emits for block/para/line entries |
| Transform | Hand-computed expectations, including a rotated and a cropped page |
| Text layer | Save, reopen, and assert the content stream contains `3 Tr` and the expected CIDs; assert the `ToUnicode` CMap round-trips a Czech string |
| End to end | `skipif` without Tesseract, exactly as the visual tests skip without Poppler |

The Czech round-trip is the test that proves the glyphless-font decision: OCR a
rendered page containing `příliš žluťoučký kůň`, save, reopen, and assert the
extracted text matches exactly.

## Out of scope

Deskewing, despeckling, and other preprocessing; layout and table
reconstruction; replacing an existing text layer; OCR confidence-driven
correction UI.
