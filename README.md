# FreeDF

**An open, independent PDF parsing and editing engine built from the document structure up.**

FreeDF is an experimental PDF engine written in Python. Its long-term goal is to provide applications with a transparent, dependency-light foundation for inspecting, modifying and writing PDF documents without relying on a closed commercial editing SDK.

The project is being developed as the document backend for a larger desktop editor, but the engine itself is designed to remain independent, reusable and suitable for integration into other software.

> [!WARNING]
> **FreeDF is currently pre-alpha.**
>
> It can parse a limited but growing subset of the PDF format. It cannot yet render, edit or save arbitrary PDF documents, and its public API is not stable.

## Why FreeDF?

A PDF is not a normal editable document. It is a graph of indirect objects containing page trees, resource dictionaries, compressed streams, fonts, images and drawing instructions.

Most applications avoid dealing with that structure directly by licensing an existing PDF SDK. FreeDF takes the longer route: it implements the core document model and editing pipeline as an independent open-source engine.

The project is built around several principles:

- **Explicit behaviour** — unsupported PDF features should produce clear errors instead of silently corrupting a document.
- **Immutable public models** — document state and editing operations should be predictable and safe to reason about.
- **Non-destructive editing** — edits are recorded as operations before a new file is written.
- **Stable page identities** — operations target pages by immutable IDs rather than indexes that change after reordering.
- **Full-rewrite output** — the planned writer will be able to rebuild a clean document instead of preserving stale revisions and unreachable objects.
- **Renderer independence** — parsing and editing should not be permanently tied to one rendering library.
- **Test-driven development** — each supported part of the PDF format is backed by focused fixtures and tests.

## Current status

FreeDF is under active development.

### Implemented

- Public immutable API models
- Base engine and parsing errors
- Recursive PDF byte tokenizer
- PDF primitive value types
- Names, strings, numbers, arrays and dictionaries
- Indirect object references
- Classic cross-reference tables
- Trailer parsing
- Indirect-object resolution
- PDF stream parsing
- `FlateDecode` stream decompression
- Explicit unsupported-feature errors
- Programmatically generated PDF test fixtures
- Automated parser and reader tests

### In progress

- Document catalog resolution
- Page-tree traversal
- Inherited page attributes
- Page dimensions and rotation
- Document metadata
- Stable page records

### Not implemented yet

- Cross-reference streams
- Object streams
- Encrypted documents
- Page rendering
- Editing operations
- Undo and redo
- PDF writing
- Merge and import
- Text extraction
- Forms and annotations
- OCR
- Redaction
- HTTP or CLI integration

FreeDF should not currently be used for production documents.

## Planned architecture

```text
PDF file
   │
   ▼
Byte tokenizer
   │
   ▼
PDF value parser
   │
   ▼
Cross-reference and object resolver
   │
   ▼
Document catalog and page tree
   │
   ▼
Immutable document model
   │
   ├── Renderer adapter
   ├── Edit-operation projection
   ├── Import and merge validation
   └── Full-rewrite PDF writer
```

The engine is deliberately divided into layers. Parsing a PDF, rendering a page and modifying a document are related problems, but they are not the same problem and should not be inseparably coupled.

## Roadmap

### Phase 1 — PDF container parsing

- [x] PDF primitive values
- [x] Recursive tokenizer
- [x] Classic cross-reference tables
- [x] Trailer and indirect-object resolution
- [x] Stream parsing
- [x] `FlateDecode`
- [ ] Cross-reference streams
- [ ] Object streams
- [ ] Additional stream filters
- [ ] Damaged cross-reference recovery

### Phase 2 — Document model

- [ ] Catalog resolution
- [ ] Page-tree traversal
- [ ] Inherited page properties
- [ ] Stable page identities
- [ ] Metadata inspection
- [ ] Annotation discovery
- [ ] Form discovery

### Phase 3 — Editing model

- [ ] Immutable edit operations
- [ ] Undo and redo projection
- [ ] Page rotation
- [ ] Page deletion
- [ ] Page reordering
- [ ] Blank-page insertion
- [ ] Crop-box editing
- [ ] Metadata editing

### Phase 4 — Rendering

- [ ] Renderer protocol
- [ ] Poppler adapter
- [ ] Thumbnail rendering
- [ ] Full-page rendering
- [ ] Disk render cache
- [ ] Cache invalidation after edits

Rendering will initially be delegated to an external renderer. Building a complete PDF rasterizer is outside the first scope of the project.

### Phase 5 — Writing and assembly

- [ ] Full-rewrite PDF writer
- [ ] Cross-reference generation
- [ ] Safe atomic saving
- [ ] Page extraction
- [ ] PDF splitting
- [ ] PDF merging
- [ ] Cross-document resource copying
- [ ] Imported-page validation

### Phase 6 — Integration

- [ ] Public engine lifecycle
- [ ] Document sessions
- [ ] Versioned JSON contracts
- [ ] JSON Schema definitions
- [ ] JSONL agent CLI
- [ ] Loopback HTTP service
- [ ] Contract-parity tests

### Later goals

- Text and image inspection
- Search with page coordinates
- Text and image overlays
- Page numbers and watermarks
- Signatures
- AcroForm editing and flattening
- Password-protected documents
- Content-region replacement
- Verified secure redaction
- OCR integration

These are long-term goals, not promises for the first release.

## Repository structure

```text
custom-pdf-engine/
├── PROGRESS.md
└── pdf-engine/
    ├── pyproject.toml
    ├── src/
    │   └── pdfengine/
    │       ├── api/
    │       │   └── models.py
    │       ├── parser/
    │       │   ├── reader.py
    │       │   ├── tokens.py
    │       │   └── values.py
    │       ├── __init__.py
    │       └── errors.py
    └── tests/
        ├── conftest.py
        ├── test_models.py
        ├── test_reader.py
        └── test_tokens.py
```

The repository currently keeps the Python package inside `pdf-engine/`.

## Development setup

FreeDF requires Python 3.11 or newer.

Clone the repository:

```bash
git clone https://github.com/Brightwav3/custom-pdf-engine.git
cd custom-pdf-engine/pdf-engine
```

Create a virtual environment:

### Windows

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
```

### Linux or macOS

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

Install the package in editable mode with the development test dependency:

```bash
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install pytest
```

Run the test suite:

```bash
pytest
```

The project configures pytest to discover tests from the `tests` directory and import the package from `src`.

## Development philosophy

### Unsupported means unsupported

PDF is an extensive format with decades of compatibility baggage. FreeDF will not pretend to support structures it cannot safely process.

When the engine encounters an unsupported feature, the preferred result is a typed, actionable error—not a partially parsed document that may be corrupted when saved.

### Source documents remain untouched

The planned editor will treat the original file as immutable.

Edits will be represented as a journal of operations:

```json
{
  "op": "rotate",
  "pageIds": ["page_a12f"],
  "degrees": 90
}
```

Undo and redo will move through that operation journal. The engine will write a new PDF only when explicitly asked to save.

### Pages are not indexes

Page numbers change after insertion, deletion and reordering. FreeDF will assign each opened or inserted page a stable internal identity so that queued operations cannot accidentally target a different page.

### Rendering is replaceable

The initial renderer adapter is planned around Poppler, but rendering will sit behind a protocol. The document model and editing API should not expose renderer-specific objects.

### Saving must be safe

The planned writer will:

1. write to a temporary destination;
2. generate a complete cross-reference structure;
3. reopen and validate the result;
4. replace the final target atomically;
5. leave the original untouched when validation fails.

## Intended use

FreeDF is intended to become a backend for:

- desktop PDF editors;
- local document-management software;
- page-organizing and assembly tools;
- conversion applications;
- automation and agent workflows;
- batch PDF processing.

It is not intended to become a user-facing desktop application by itself. Applications are expected to provide their own interface, job management, history, recovery and packaging around the engine.

## Relationship to One Tool

FreeDF is being developed as the future PDF-editing foundation for **One Tool to Rule Them All**, a local file conversion, creation and editing application.

The projects have separate responsibilities:

```text
FreeDF
└── Parses, models, edits and writes PDFs

One Tool
├── User interface
├── Conversion workflows
├── Creator and Editor workspaces
├── Job queue and progress
├── History and session recovery
├── Helper management
└── Desktop packaging
```

Keeping the engine independent prevents the PDF implementation from becoming coupled to one interface or product.

## Contributing

The project is still establishing its architecture and public contracts. Contributions are welcome, particularly in the following areas:

- focused PDF fixtures;
- parser edge cases;
- malformed-input tests;
- PDF specification research;
- typed error design;
- cross-reader validation;
- documentation.

Before implementing a major feature, open an issue describing:

- the PDF structures involved;
- the intended public behaviour;
- unsupported cases;
- proposed tests;
- whether the change affects the public model.

Every parser or writer change should include tests.

## Security

PDF files are untrusted binary inputs.

Do not use FreeDF on sensitive or untrusted documents in production at this stage. The engine has not yet undergone a security audit, fuzzing campaign or adversarial-file review.

Potentially dangerous or malformed samples should not be committed publicly when they contain private information. Generate minimal reproducible fixtures whenever possible.

Security problems should be reported privately rather than disclosed in a public issue before a fix is available.

## Versioning

FreeDF currently uses pre-release `0.x` versioning.

During this period:

- public models may change;
- modules may move;
- serialized contracts may change;
- incomplete features may be removed;
- backward compatibility is not guaranteed.

Versioned JSON contracts are planned before external integrations are considered stable.

## Licence

A licence has not yet been added to this repository.

Until a licence file is committed, the source is publicly visible but normal copyright restrictions still apply. Contributors and users should not assume that the project is open source merely because the code is hosted publicly.

Before accepting outside contributions or integrating FreeDF into another application, choose and add an explicit licence.

For a backend intended to be freely usable inside both open-source and commercial applications, a permissive licence such as **Apache License 2.0** would be a practical choice.

---

**FreeDF is not trying to hide the complexity of PDF behind vague promises. It is building the parts explicitly, testing them independently, and reporting honestly where support ends.**
