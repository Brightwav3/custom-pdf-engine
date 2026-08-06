# Sub-project 0: Real-world documents — design

**Goal:** make the engine open, preview, and round-trip the PDFs people actually
have. Today a single JPEG makes a document unopenable, and a preview of an
edited page shows the unedited page.

**Scope:** two coupled changes plus their capability reporting. Text extraction
and text editing are later sub-projects and are out of scope here.

---

## Problem 1: any unsupported filter makes a document unopenable

`PdfReader._read_stream` decodes every stream eagerly and raises
`UnsupportedPdfError` for any filter that is not `FlateDecode`. Most real PDFs
embed JPEG images (`/DCTDecode`), so most real PDFs cannot be opened at all.

The insight: **structural edits never read stream contents.** Reordering pages
does not care what is inside an image. The engine only needs decoded bytes when
something inspects them — today nothing does, and tomorrow the text stack will.

### Decision: raw bytes with lazy decode

`PdfStream` stores the original bytes and its declared filter chain. Decoding
happens on access. The writer copies raw bytes verbatim for every stream.

Beyond fixing the JPEG case this buys three things:

- **Byte-exact round-trip.** Untouched streams are no longer decompressed and
  re-deflated at a different compression level.
- **Faster open and save.** Nothing inflates unless something reads it.
- **The API the text stack needs.** "Give me the decoded content stream" is
  exactly the lazy `.data` property, for free.

The cost is that `PdfStream` equality compares raw bytes rather than decoded
bytes, so reader tests that construct expected streams must compare `.data`.

### New `PdfStream`

```python
@dataclass(frozen=True)
class PdfStream:
    dictionary: PdfDictionary
    raw: bytes                                  # exact bytes between stream/endstream
    filters: tuple[PdfName, ...] = ()           # full declared chain, in order
    decode_parms: tuple[object, ...] = ()       # parallel to filters; None where absent

    @property
    def residual_filters(self) -> tuple[PdfName, ...]:
        """Filters left after decoding the longest decodable prefix."""

    @property
    def is_decodable(self) -> bool:
        """True when residual_filters is empty."""

    @property
    def data(self) -> bytes:
        """Decoded bytes. Raises UnsupportedPdfError when not decodable."""
```

Rules:

- Filters apply in order, so decode the **longest decodable prefix** and keep the
  remainder as `residual_filters`. `/Filter [/FlateDecode /DCTDecode]` decodes
  its Flate layer and reports `DCTDecode` as residual.
- `FlateDecode` is decodable. Everything else is not. An absent `/Filter` means
  `raw` is already the data.
- `.data` caches its result in a `field(default=None, compare=False, repr=False)`
  slot written through `object.__setattr__`, so equality and hashing stay based
  on `dictionary` and `raw`.
- Decoding is bounded by `MAX_DECODED_BYTES = 128 * 1024 * 1024`. Exceeding it
  raises `PdfParseError`, so a decompression bomb cannot exhaust memory.

### Reader and writer changes

`PdfReader._read_stream` stops decoding. It records `raw`, `filters`, and
`decode_parms`, and no longer raises on unknown filters. Object streams,
xref streams, and encryption stay hard failures — those block structure, not
just content.

`FullRewriteWriter` drops `_compress`. Copying a stream now preserves
`/Filter` and `/DecodeParms` and sets `/Length` to `len(raw)`. Generated blank
page content is written as an uncompressed empty stream.

---

## Problem 2: previews of edited pages show the unedited page

`PdfEngine.render_page` keys its cache on the projected rotation and crop, so an
edit correctly misses the cache — and then re-renders **the original source
page**, which has neither the rotation nor the crop. The result is a fresh cache
entry holding a stale image. Blank and imported pages cannot be previewed at all.

The existing test asserted only that the renderer was called twice. It checked
invalidation and never checked pixels, so it passed on broken behaviour.

### Decision: render from a materialized copy of the edited state

On a preview miss for an edited document, write the current projected state to a
PDF inside the session cache using the existing writer, then render pages from
that file.

This is correct for rotation, crop, blank pages, and imported pages with no new
rendering logic, because the thing being rendered is exactly the thing a save
would produce. One materialization serves a whole thumbnail strip.

```python
def _preview_source(self, session) -> tuple[Path, dict[str, int]]:
    """Return the file to render and a page_id -> page index map."""
```

- **Unedited fast path.** When the operation cursor is at zero, render the
  original file directly at the page's source index. The common case stays free.
- **Materialized path.** Otherwise write `session.cache_dir/state-<hash>.pdf`,
  where `<hash>` is SHA-256 over the base fingerprint and a stable serialization
  of `operations[:cursor]`. Reuse the file if it already exists. Prune older
  `state-*.pdf` files, keeping only the current one.
- Render keys change to `state_hash + page_id + width + renderer.version`, which
  makes the rotation and crop components of the old key unnecessary.

### The test that would have caught the bug

Render a portrait page, rotate it 90°, render again, and assert the PNG
dimensions swapped. Assert pixels, never call counts.

---

## Problem 3: an agent cannot discover these limits before hitting them

### Decision: split read capability from edit capability

`capabilities()` gains a `read` section so a caller learns before it tries that
reordering will work and text extraction will not.

```json
"read": {
  "structuralEdit": {"state": "ready", "detail": ""},
  "textContent": {
    "state": "blocked",
    "detail": "12 streams use filters this version cannot decode",
    "filters": ["DCTDecode"],
    "objectCount": 12
  }
}
```

Computed by walking each page's `/Contents` and `/Resources` graph and
collecting `residual_filters`. It is computed on first request and cached on the
session, not on open, so opening a large scanned document stays cheap.

---

## Testing

| Area | What must be proven |
| --- | --- |
| Stream model | Longest-prefix decode; residual reporting; `.data` raises `UnsupportedPdfError` naming the filter; equality ignores the decode cache; bomb guard trips |
| Reader | A PDF containing a `/DCTDecode` image opens and reports its pages |
| Writer | Undecodable streams round-trip byte-for-byte; Flate streams round-trip byte-for-byte; a reordered document containing a JPEG reopens with the image intact |
| Preview | Rotation swaps rendered dimensions; crop changes them; blank and imported pages render; the unedited path does not materialize a file; a strip materializes once |
| Capability | A JPEG document reports `structuralEdit: ready` and `textContent: blocked` naming `DCTDecode`; a clean document reports both `ready` |

New fixture: `fixtures/basic/with-image.pdf` — one page embedding a tiny
`/DCTDecode` image, under 20 KiB, documented in `fixtures/basic/README.md`.

## Out of scope

Batch range rendering, content stream parsing, font models, text extraction, and
text editing. Each is its own sub-project.
