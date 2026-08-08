# FreeDF

> **A small, explicit, open PDF engine for Python, local services, automation, and AI agents.**

FreeDF is an MIT-licensed PDF engine for parsing, rendering, editing, OCR, and writing PDF documents without relying on a proprietary editing SDK.

It is built around one core rule:

> **One engine. One document model. One contract.**

Whether FreeDF is embedded directly in Python, called over HTTP, or controlled as a JSONL subprocess, the same operations, validation rules, capabilities, error codes, and session semantics apply.

> [!WARNING]
> **FreeDF is currently pre-alpha.**
>
> v0.2 is usable for experimentation, development, and integration work, but the project has not yet undergone formal security auditing or fuzz testing.
>
> Production use with sensitive or hostile PDFs is not recommended yet.

---

## What FreeDF Can Do

FreeDF v0.2 currently supports:

* custom PDF parsing;
* recursive tokenization and PDF primitive values;
* classic cross-reference tables and trailers;
* indirect object resolution;
* stream parsing with lazy decoding;
* `FlateDecode`;
* byte-preserving passthrough of untouched streams;
* immutable document state;
* stable page identities;
* page rotation;
* page deletion;
* page extraction;
* page reordering;
* page cropping;
* blank-page insertion;
* metadata editing;
* cross-document page import;
* undo and redo;
* full-document rewriting;
* validated save operations;
* Poppler-backed rendering;
* high-DPI rendering;
* batch rendering;
* render caching;
* Tesseract OCR;
* searchable PDF generation;
* invisible Unicode text layers;
* capability discovery;
* session lifecycle tracking;
* binary artifacts;
* Python API;
* local HTTP API;
* JSONL agent interface;
* versioned machine-readable contracts;
* contract parity across all public interfaces.

The current automated test suite contains **455 tests with zero skipped** on the reference development environment.

### Not implemented yet

FreeDF does **not** currently provide:

* general text extraction;
* text search;
* arbitrary editing of existing text;
* AcroForm editing;
* annotations;
* watermarks;
* image optimization;
* font subsetting;
* secure redaction.

Existing-text editing requires a proper content-stream interpreter and font model. FreeDF intentionally does not fake this with overlays or unreliable heuristics.

---

# Why FreeDF?

PDF is not simply a collection of pages.

A real PDF is a graph of indirect objects containing page trees, compressed streams, fonts, images, metadata, annotations, color spaces, resources, and drawing instructions accumulated across decades of specification revisions.

Many applications handle that complexity by embedding an existing commercial SDK.

FreeDF takes a different approach.

It implements its own document model and exposes explicit contracts around what the engine understands, what it can safely modify, and what it cannot yet do.

The project follows several principles:

### Explicit behavior

Unsupported features should fail explicitly.

A typed error is preferable to silently producing a corrupted or incorrect document.

### Immutable editing

Opening a PDF never changes it.

Edits are represented as immutable operations projected over the original document state.

### Stable page identities

Public editing operations target stable page IDs rather than mutable page indexes.

An operation therefore continues to target the correct page even after earlier operations reorder, insert, or delete pages.

### Safe writing

Normal saves do not overwrite the source document.

Replacing the source requires explicit opt-in, and FreeDF detects if the source changed on disk after it was opened.

### Replaceable rendering

Rendering lives behind an engine-owned protocol.

Poppler is the current renderer, but the document model does not depend on Poppler.

### Capability discovery

Applications should not need to discover unsupported functionality by catching failures halfway through an operation.

FreeDF exposes machine-readable capability information before work begins.

### Contract parity

Python, HTTP, and JSONL are three interfaces to the same engine rather than three independent implementations.

---

# Architecture

```text
                         PDF file
                            │
                            ▼
                  Parser / object reader
                            │
                            ▼
                 Immutable document model
                            │
             ┌──────────────┼──────────────┐
             │              │              │
             ▼              ▼              ▼
         Rendering       Editing       Capability
          adapter        projection      discovery
             │              │              │
             └──────────────┼──────────────┘
                            ▼
                   Full-rewrite writer
                            │
                            ▼
                    Command dispatcher
                            │
             ┌──────────────┼──────────────┐
             ▼              ▼              ▼
        Python API       HTTP API      JSONL agent
```

Every public surface ultimately reaches the same engine behavior.

---

# Installation

FreeDF requires **Python 3.11+**.

Clone the repository:

```bash
git clone https://github.com/Brightwav3/custom-pdf-engine.git
cd custom-pdf-engine/pdf-engine
```

Create a virtual environment:

```bash
python -m venv .venv
```

### Windows PowerShell

```powershell
.venv\Scripts\Activate.ps1
```

### Linux / macOS

```bash
source .venv/bin/activate
```

Install FreeDF:

```bash
python -m pip install --upgrade pip
python -m pip install -e .
```

Install the development test dependency:

```bash
python -m pip install pytest
```

Run the test suite:

```bash
pytest -q
```

---

# Optional System Dependencies

FreeDF separates core document functionality from external rendering and OCR backends.

### Poppler

Poppler's `pdftoppm` is used for:

* page previews;
* thumbnails;
* high-DPI rasterization;
* OCR input rendering.

A missing Poppler installation is reported through capability discovery rather than crashing the engine.

### Tesseract

Tesseract is used for OCR and searchable PDF generation.

A missing Tesseract installation similarly reports OCR as `unavailable`.

Structural PDF editing does not require either dependency.

---

# Python Quick Start

```python
from pdfengine import PdfEngine, RotatePages

engine = PdfEngine()

session = engine.open_document("input.pdf")

info = engine.inspect_document(session)
first_page = info.pages[0]

engine.apply_operations(
    session,
    [
        RotatePages(
            (first_page.page_id,),
            90,
        )
    ],
)

output = engine.save(session, "output.pdf")

engine.close(session)

print(output)
```

The package installed by the distribution is currently imported as:

```python
import pdfengine
```

The distribution itself is named:

```text
freedf
```

This distinction is intentional in v0.2. Renaming the public Python package would be a breaking change and is deferred to an explicitly versioned migration.

---

# Editing Operations

Operations are immutable dataclasses.

```python
from pdfengine import (
    AddTextLayer,
    CropPages,
    DeletePages,
    ExtractPages,
    ImportPages,
    InsertBlankPage,
    ReorderPages,
    RotatePages,
    SetMetadata,
)
```

Current operations:

| Operation         | Purpose                                 |
| ----------------- | --------------------------------------- |
| `RotatePages`     | Rotate one or more pages                |
| `DeletePages`     | Remove pages                            |
| `ReorderPages`    | Replace the current page order          |
| `ExtractPages`    | Keep only selected pages                |
| `InsertBlankPage` | Insert a new empty page                 |
| `CropPages`       | Change visible page bounds              |
| `SetMetadata`     | Modify document metadata                |
| `ImportPages`     | Import pages from another open document |
| `AddTextLayer`    | Add searchable OCR text                 |

All page-oriented operations use stable page IDs.

---

# Searchable OCR

OCR is implemented as an additive operation.

FreeDF does not rewrite the visible page contents. Instead, recognized text is written as an invisible Unicode text layer over the existing page.

```python
from pdfengine import AddTextLayer, PdfEngine

engine = PdfEngine()

session = engine.open_document("scan.pdf")
info = engine.inspect_document(session)

page_ids = tuple(
    page.page_id
    for page in info.pages
)

if engine.ocr_capability(language="eng").state == "ready":
    engine.apply_operations(
        session,
        [
            AddTextLayer(
                page_ids,
                language="eng",
                dpi=300,
            )
        ],
    )

    engine.save(
        session,
        "searchable.pdf",
    )
```

The resulting PDF keeps its original visual appearance while gaining selectable and searchable text.

Recognition is deliberately kept outside the immutable state projection because OCR requires rasterization, subprocess execution, and file I/O.

The operation log records **what should be recognized**; the engine performs recognition when applying the operation.

---

# Capability Discovery

Callers can inspect the engine before attempting work.

```python
caps = engine.capabilities(session)
```

Capabilities describe:

* renderer availability;
* OCR availability;
* installed OCR languages;
* available OCR modes;
* supported operations;
* per-operation readiness;
* decodable stream filters;
* document structural-edit support;
* document text-content readability;
* allowed commands for the current session;
* save constraints.

Capability states include:

| State         | Meaning                                 |
| ------------- | --------------------------------------- |
| `ready`       | Feature can be used now                 |
| `blocked`     | The current document prevents it        |
| `unavailable` | Required functionality is not installed |
| `error`       | Capability probing itself failed        |

This distinction matters.

A missing Tesseract installation and a PDF whose structure prevents an operation are two fundamentally different problems and should not produce the same result.

---

# Three Public Interfaces

FreeDF exposes one contract through three deployment models.

| Surface | Best suited for                      | Isolation        | Transport               |
| ------- | ------------------------------------ | ---------------- | ----------------------- |
| Python  | Python applications                  | In-process       | Typed Python objects    |
| HTTP    | Desktop apps and non-Python software | Separate process | JSON / binary artifacts |
| JSONL   | Agents and automation                | Separate process | stdin / stdout          |

The common command set is:

```text
open
inspect
capabilities
render
apply
undo
redo
save
artifact
close
```

No interface has privileged behavior.

Contract-parity tests verify that equivalent requests produce equivalent results across all three surfaces.

---

# HTTP Service

Start the local service with:

```bash
pdfengine serve
```

By default the server binds to:

```text
127.0.0.1:8757
```

Network binding requires explicit opt-in.

Primary endpoints:

```text
POST /v1/commands
GET  /v1/health
GET  /v1/schema/<name>
GET  /v1/artifacts/<id>
```

The command endpoint exposes the complete engine contract.

## Artifact security

> [!IMPORTANT]
> `GET /v1/artifacts/<id>` is a convenience streaming endpoint and is **not ownership-checked**.

The normal `artifact` command verifies that an artifact belongs to the requesting session.

The raw GET endpoint does not.

This is acceptable for a loopback service with a single trusted caller, but it must not be exposed to an untrusted network without authentication or another security layer in front of it.

---

# JSONL Agent Interface

Start the agent process with:

```bash
pdfengine agent
```

The process accepts one JSON command per input line and returns one JSON response per output line.

```text
stdin  → request
stdout ← response
stderr ← diagnostics
```

Nothing except response JSON is written to stdout.

That makes the interface suitable for:

* AI agents;
* automation systems;
* subprocess supervisors;
* sandboxed tools;
* applications that do not need an HTTP server.

Malformed requests return an error envelope without terminating the process.

---

# Editing Model

Each open document owns a `DocumentState`.

The state contains:

```text
original document
       +
operation history
       +
history cursor
       =
projected document
```

Applying an operation creates a new state.

Undo and redo only move the cursor.

```text
original
   │
   ├── rotate
   │
   ├── crop
   │
   ├── delete
   │
   └── reorder
          ▲
          │
        cursor
```

Applying a new operation after undo discards the abandoned redo branch.

This model provides deterministic replay and keeps the original parsed document separate from user edits.

---

# Stable Page IDs

Pages are not addressed by mutable positions.

Instead:

```text
page_a12f...
page_42ce...
page_998b...
```

remain the identities used by editing operations.

For example:

```python
RotatePages(
    ("page_a12f",),
    90,
)
```

continues to target that page even if another operation previously moved it from page 1 to page 8.

This is particularly important for:

* queued operations;
* automation;
* AI agents;
* undo/redo;
* cross-document editing;
* multi-operation batches.

---

# Rendering

Rendering is isolated behind an engine-owned renderer protocol.

The default implementation uses Poppler.

```python
result = engine.render_page(
    session,
    page_id,
    width=1000,
)
```

`RenderResult` contains:

```text
page_id
width
height
image_bytes
cache_hit
```

Rendered previews are cached.

Once the document has edits, FreeDF renders a materialized version of the **projected document state**, not the untouched source PDF.

That means previews reflect:

* rotations;
* crops;
* inserted pages;
* deleted pages;
* reordered pages;
* imported pages.

The preview therefore represents what a save would actually produce.

---

# Saving

A normal save creates a new PDF.

```python
engine.save(
    session,
    "edited.pdf",
)
```

Replacing the original source requires explicit permission.

FreeDF fingerprints the source when the document is opened.

If another process modifies the source before FreeDF saves, the operation fails with:

```text
source_changed
```

rather than overwriting a file that no longer matches the document originally opened.

Conceptually:

```text
Projected document
        │
        ▼
Full rewrite
        │
        ▼
Temporary/output PDF
        │
        ▼
Re-open
        │
        ▼
Validate
        │
        ▼
Return / replace target
```

Untouched stream bytes are copied through rather than unnecessarily decoded and recompressed.

---

# Sessions

Opening a PDF creates a document session.

```python
session = engine.open_document(
    "document.pdf"
)
```

Sessions have explicit lifecycle state.

```text
OPEN
 │
 │ close()
 ▼
CLOSED
```

Closing a session removes its working cache and artifacts but leaves a lightweight tombstone.

This lets FreeDF distinguish between:

```text
session_not_found
```

meaning the ID was never issued, and:

```text
session_invalid_state
```

meaning the session existed but has already been closed.

A caller therefore receives enough information to determine what actually happened instead of guessing from a generic not-found response.

---

# Artifacts

Binary results are represented as artifacts across the public contract.

Current artifact kinds are:

```text
page_render
thumbnail
saved_document
```

Artifacts allow rendering and save results to be represented consistently over Python, HTTP, and JSONL.

The JSON interfaces can retrieve artifact data through the `artifact` command, while HTTP additionally provides the raw streaming route.

---

# Errors

All public Python errors inherit:

```python
PdfEngineError
```

and expose a stable `.code`.

Current error codes include:

```text
parse_error
unsupported_pdf
invalid_operation
invalid_request
renderer_unavailable
render_error
ocr_unavailable
ocr_error
source_changed
session_not_found
session_invalid_state
```

The same codes appear in JSON error envelopes.

This allows callers to branch on stable machine-readable behavior rather than parsing error messages.

---

# Versioned Contract

FreeDF maintains an explicit public contract covering:

* commands;
* operation kinds;
* error codes;
* capability states;
* artifact kinds;
* request schemas;
* response schemas.

The contract surface is frozen into a golden manifest.

Tests fail on undocumented **additions as well as removals**.

This is deliberate.

An API that grows silently is still changing its contract.

Contract changes are recorded in:

```text
docs/CONTRACT-CHANGELOG.md
```

Compatibility rules live in:

```text
docs/contract-policy.md
```

---

# Current Parser Boundary

FreeDF intentionally does not claim complete PDF specification coverage.

The current parser is sufficient for the structural editing, rendering, writing, and OCR workflows implemented by v0.2.

A key design property is that structural editing often does not require understanding page content.

For example, reordering a page containing a JPEG does not require decoding that JPEG.

FreeDF therefore keeps stream data lazy and preserves unsupported-but-unmodified content where it can safely do so.

This makes it possible to structurally edit many real-world PDFs without pretending the engine understands every stream inside them.

Anything requiring actual interpretation of page content has stricter requirements.

`capabilities(session)` reports those limitations per document.

### Known v0.2 limitation

The top-level filter capability list cannot describe every possible `/DecodeParms` combination.

For example, plain `FlateDecode` support does not imply support for every predictor configuration layered on top of Flate.

Document-level capability reporting is therefore more authoritative than the flat filter catalogue.

---

# Repository Structure

```text
custom-pdf-engine/
├── README.md
├── LICENSE
├── PROGRESS.md
├── docs/
│
└── pdf-engine/
    ├── pyproject.toml
    ├── docs/
    │
    ├── src/
    │   └── pdfengine/
    │       ├── api/
    │       ├── capabilities/
    │       ├── cli/
    │       ├── editing/
    │       ├── http/
    │       ├── ocr/
    │       ├── parser/
    │       ├── rendering/
    │       └── writing/
    │
    └── tests/
```

Detailed documentation is kept outside the README where appropriate, including:

```text
Python API
Deployment models
Contract policy
Contract changelog
Development progress
```

The README describes the project.

The documentation describes the contract.

---

# Roadmap

The next major technical boundary is the **text stack**.

## Content-stream parser

Interpret PDF graphics and text operators rather than treating page content as opaque stream data.

## Font model

Understand embedded fonts, encodings, character maps, glyph widths, and Unicode mapping.

## Text extraction

Recover text together with its location on the page.

## Search

Locate text and return its page coordinates.

## Text editing

Safely replace existing text spans without pretending PDF has normal word-processing reflow.

After that, planned areas include:

* AcroForms;
* annotations;
* watermarks;
* headers and footers;
* page numbering;
* Bates numbering;
* image optimization;
* object cleanup;
* font subsetting;
* secure redaction.

Text editing is intentionally one of the later milestones because implementing it correctly requires understanding both PDF drawing instructions and font semantics.

---

# AI and Automation

FreeDF is designed to be usable by autonomous software without requiring it to infer engine behavior.

Agents can discover:

```text
What commands exist?
What operations exist?
Can this document be structurally edited?
Can its content streams be interpreted?
Is rendering available?
Is OCR available?
Which OCR languages exist?
Which commands are valid for this session?
What kind of error occurred?
```

through structured contracts rather than human-readable console output.

Important properties for automation include:

* stable identifiers;
* immutable operations;
* deterministic state projection;
* explicit session lifecycle;
* typed errors;
* capability discovery;
* JSON schemas;
* JSONL transport;
* contract parity.

An agent should not need to guess what FreeDF can do.

It can ask the engine.

---

# Relationship to One Tool

FreeDF was originally created as the PDF backend for **One Tool to Rule Them All**, but the engine is intentionally maintained as an independent project.

```text
              One Tool
──────────────────────────────────

UI
Editor
Creator
Converter
Job Queue
History
Settings
Desktop Packaging

               │
               ▼

               FreeDF
──────────────────────────────────

PDF Parsing
Document Model
Rendering
Editing
OCR
Writing
Capabilities
Contracts
Python API
HTTP API
JSONL API
```

One Tool owns the application experience.

FreeDF owns PDF behavior.

Keeping that boundary explicit prevents application-specific assumptions from leaking into the engine and allows FreeDF to be reused by unrelated software.

---

# Testing

Correctness is a primary project goal.

The test suite covers:

* tokenizer behavior;
* parsing;
* indirect object resolution;
* stream handling;
* document structure;
* editing projection;
* stable page identities;
* undo and redo;
* rendering;
* render caching;
* high-DPI rendering;
* writer behavior;
* save validation;
* source-change detection;
* OCR;
* Unicode text layers;
* session lifecycle;
* artifacts;
* HTTP behavior;
* JSONL behavior;
* schema contracts;
* cross-surface parity;
* missing dependency behavior;
* regression cases.

Real Poppler and Tesseract integration tests are included in the appropriate test tiers.

Synthetic fixtures are used where isolated malformed structures or parser edge cases need to be reproduced precisely.

---

# Contributing

Contributions are welcome.

For substantial features, open an issue first describing:

* the problem being solved;
* relevant PDF specification behavior;
* the proposed public contract;
* unsupported cases;
* the testing strategy.

Functional changes should include tests.

Prefer focused pull requests scoped to a subsystem such as:

```text
parser
rendering
editing
writing
OCR
forms
annotations
```

rather than combining unrelated changes.

---

# Security

PDF documents are untrusted binary input.

FreeDF assumes documents may be malformed or intentionally hostile.

Current defensive measures include:

* explicit parser failures;
* typed unsupported-feature errors;
* deterministic validation;
* immutable source state;
* source fingerprinting;
* validated writes;
* explicit network opt-in;
* no silent fallback after unsupported operations.

FreeDF has **not yet undergone a formal security audit or systematic fuzz-testing campaign**.

Until that changes, hostile PDFs should be processed with appropriate process isolation, and sensitive deployments should use conservative network exposure.

---

# Versioning

FreeDF is currently:

```text
0.2.0
```

and follows pre-1.0 versioning.

The distribution name is:

```text
freedf
```

while the current public Python import path and console command remain:

```text
pdfengine
```

That distinction is deliberate.

Breaking identifiers will move only through explicit versioned migrations rather than silently changing alongside branding.

---

# License

FreeDF is released under the **MIT License**.

It may be used in both open-source and commercial software.

See [`LICENSE`](LICENSE) for the full license text.

---

# Status

FreeDF is an active long-term project.

v0.2 establishes the core architecture:

```text
parser
   +
immutable document model
   +
structural editing
   +
rendering
   +
OCR
   +
safe writer
   +
versioned contract
   +
three equivalent public surfaces
```

The next major challenge is understanding the contents of PDF pages deeply enough to support reliable extraction, search, and eventually existing-text editing.

Until then, FreeDF prefers an explicit limitation over pretending to support something it cannot yet do correctly.
