# FreeDF

> **An open, MIT-licensed PDF engine for parsing, rendering, editing and writing PDF documents.**

FreeDF is an independent PDF engine written in Python.

Its goal is simple: provide developers with a transparent, extensible and well-tested foundation for building PDF applications without relying on proprietary editing SDKs.

Unlike most PDF libraries, FreeDF is designed around a single immutable document model shared by multiple public interfaces. Whether the caller is a Python application, a desktop editor, an automation script or an AI agent, every operation ultimately passes through the same engine and follows the same validation pipeline.

FreeDF is the PDF backend that powers **One Tool to Rule Them All**, but it is developed as a completely independent project under the MIT License.

---

> [!WARNING]
> **FreeDF is currently pre-alpha.**
>
> Although the engine already supports structural editing, rendering and multiple public interfaces, the API is still evolving and backward compatibility is **not yet guaranteed**.
>
> Production use is not recommended until the first stable release.

---

# Why FreeDF?

PDF is one of the most complex document formats still in widespread use.

A document is not simply a collection of pages—it is a graph of indirect objects containing page trees, compressed streams, fonts, images, annotations, metadata, color spaces and drawing instructions accumulated over more than thirty years of specification revisions.

Most software solves this problem by embedding a commercial SDK.

FreeDF takes a different approach.

Rather than wrapping an existing editor, it implements the document model itself, exposing clear contracts, explicit capabilities and deterministic behaviour.

The long-term goal is to become a reusable PDF foundation for desktop software, automation systems and AI agents alike.

---

# Design Principles

FreeDF is built around a small number of engineering principles.

## Explicit behaviour

Unsupported PDF features should fail explicitly.

Returning a typed error is preferable to silently producing an incorrect document.

---

## Immutable public models

Public document models never mutate unexpectedly.

Editing is represented as immutable operations that are projected onto a document state.

This makes behaviour deterministic, simplifies testing and allows operation replay.

---

## Stable page identities

Pages are never addressed by mutable indexes.

Every page receives a stable identifier that survives insertions, deletions and reordering.

This guarantees that queued operations always target the intended page.

---

## Safe writing

Saving a document should never risk destroying the original.

Normal saves always write to a temporary destination, validate the result and only then replace the target file atomically.

---

## Renderer independence

Rendering is intentionally isolated behind an engine-owned protocol.

The document model must never depend on a particular rendering library.

This allows rendering implementations to evolve independently from parsing and editing.

---

## Capability discovery

Applications and AI agents should be able to determine what the engine can safely perform before attempting an operation.

FreeDF exposes structured capability information instead of forcing callers to infer support from runtime failures.

---

## Test-first development

Every supported PDF feature is accompanied by focused tests and reproducible fixtures.

Correctness is measured through automated validation rather than manual inspection.

---

# Current Status

FreeDF is under active development.

## Implemented

- Custom PDF parser
- Recursive tokenizer
- PDF primitive value model
- Classic cross-reference tables
- Trailer parsing
- Indirect object resolution
- Stream parsing
- Lazy stream decoding
- `FlateDecode`
- Immutable document model
- Stable page identities
- Structural editing operations
- Full document rewrite
- Safe save pipeline
- Poppler-backed rendering
- High-DPI rendering
- Batch rendering
- Capability discovery
- Python API
- Local HTTP API
- JSONL agent CLI
- Contract-parity validation
- Automated test suite (300+ tests)

---

## In Progress

- OCR searchable PDFs
- Invisible text layer generation
- Tesseract integration
- OCR capability reporting

---

## Planned

- Content stream parser
- Font model
- Text extraction
- Search
- Text editing
- Annotations
- Form filling
- Watermarks
- Image optimization
- Font subsetting
- Secure redaction

---

# Architecture

```text
                PDF Document
                     │
                     ▼
            Parser & Object Reader
                     │
                     ▼
          Immutable Document Model
                     │
     ┌───────────────┼────────────────┐
     │               │                │
     ▼               ▼                ▼
 Rendering      Editing Engine     Capability
   Engine          Operations       Discovery
     │               │                │
     └───────────────┼────────────────┘
                     ▼
               Full-Rewrite Writer
                     │
     ┌───────────────┼────────────────┐
     ▼               ▼                ▼
 Python API      HTTP API        JSONL CLI
```

Every public interface is routed through the same engine.

# Installation

FreeDF currently targets **Python 3.11+**.

Clone the repository:

```bash
git clone https://github.com/Brightwav3/custom-pdf-engine.git
cd custom-pdf-engine/pdf-engine
```

Create a virtual environment.

### Windows

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
```

### Linux / macOS

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

Install the package in editable mode.

```bash
python -m pip install --upgrade pip
python -m pip install -e .
```

Install development tools.

```bash
python -m pip install pytest
```

Run the test suite.

```bash
pytest
```

---

# Repository Structure

```text
custom-pdf-engine/
│
├── README.md
├── LICENSE
├── PROGRESS.md
│
└── pdf-engine/
    ├── pyproject.toml
    ├── src/
    │   └── pdfengine/
    │       ├── api/
    │       ├── parser/
    │       ├── rendering/
    │       ├── editing/
    │       ├── writer/
    │       ├── capabilities/
    │       ├── cli/
    │       ├── http/
    │       └── ...
    │
    └── tests/
```

The engine is intentionally divided into independent subsystems.

Parsing, rendering, editing and writing are treated as separate responsibilities connected through a shared immutable document model.

---

# Public Interfaces

FreeDF exposes three equal public interfaces.

## Python API

The native interface for applications embedding the engine directly.

```python
from pdfengine import PdfEngine

engine = PdfEngine()

doc = engine.open("document.pdf")
```

---

## HTTP API

A lightweight local HTTP server intended for desktop applications and tools written in other languages.

Every operation exposed through Python is also available through HTTP.

---

## JSONL CLI

Designed primarily for automation and AI agents.

Commands are exchanged as newline-delimited JSON.

This avoids parsing human-readable output and allows deterministic machine interaction.

---

# AI-First Design

FreeDF is intentionally designed to work well with autonomous coding agents.

Rather than exposing ad-hoc commands, the engine provides structured contracts that are easy for software to discover and reason about.

Key principles include:

- immutable document models;
- immutable editing operations;
- explicit capability discovery;
- deterministic error reporting;
- machine-readable JSON contracts;
- stable identifiers;
- contract parity across every public interface.

An AI should never need to guess whether an operation is supported.

Instead, it can ask the engine.

Example:

```json
{
  "read": {
    "structural": "ready",
    "text": "blocked",
    "filters": [
        "DCTDecode"
    ]
}
```

The engine reports capabilities before work begins.

---

# Development Philosophy

## Source documents are immutable

Opening a PDF never changes it.

Editing operations are recorded separately.

Saving produces a newly written document.

---

## Operations are immutable

User actions are represented as immutable commands.

```json
{
    "op": "rotate",
    "pageIds": [
        "page_a12f"
    ],
    "degrees": 90
}
```

Undo and redo simply move through the operation history.

---

## Pages have identities

Pages are identified by stable IDs rather than mutable positions.

A queued operation remains valid even if earlier edits insert, delete or reorder pages.

---

## Unsupported means unsupported

FreeDF deliberately avoids pretending to understand structures it cannot safely process.

When support is unavailable, the engine returns an explicit typed error rather than silently producing an incorrect document.

---

## Rendering is replaceable

Rendering is isolated behind an engine-owned protocol.

The parser, editing engine and writer never depend directly on a rendering implementation.

---

## Every save is validated

Saving follows a strict pipeline.

```
Source document
        │
        ▼
Apply operations
        │
        ▼
Write temporary PDF
        │
        ▼
Re-open and validate
        │
        ▼
Atomic replace
```

A failed validation never overwrites the original file.

---

# Roadmap

## Completed

- Custom parser
- Object reader
- Lazy stream decoding
- Structural editing
- Stable page identities
- Safe save pipeline
- Full document rewriting
- Poppler renderer
- Capability discovery
- Python API
- HTTP API
- JSONL CLI
- High-DPI rendering
- Batch rendering
- Contract parity

---

## Currently Under Development

### OCR

The next major milestone is searchable PDF generation.

Unlike text editing, OCR is additive.

The original page remains untouched while an invisible Unicode text layer is added above it.

Current work includes:

- Tesseract adapter
- TSV parsing
- coordinate transforms
- invisible font generation
- searchable PDF writing

---

## Planned

### Content stream parser

Interpret PDF graphics operators.

### Font model

Understand embedded fonts and glyph mapping.

### Text extraction

Recover searchable text with page coordinates.

### Search

Locate text together with its bounding rectangles.

### Text editing

Replace text spans without reflow.

### Forms

Read and update AcroForms.

### Annotations

Create and edit PDF annotations.

### Watermarks

Headers, footers, page numbering and Bates numbering.

### Optimization

Image downsampling, object cleanup and font subsetting.

### Secure redaction

Verified removal of underlying content rather than visual covering.

# Intended Use

FreeDF is designed as a reusable engine rather than an end-user application.

Typical use cases include:

- Desktop PDF editors
- Document management software
- PDF automation pipelines
- Batch document processing
- AI agents
- Local HTTP services
- Command-line tools
- Conversion software
- Research projects

Applications are expected to provide their own interface, workflow, packaging and user experience while relying on FreeDF for PDF functionality.

---

# Relationship to One Tool

FreeDF is the PDF engine behind **One Tool to Rule Them All**.

The two projects intentionally have different responsibilities.

```text
                One Tool
────────────────────────────────────

User Interface
Creator
Editor
Converter
Job Queue
History
Settings
Desktop Packaging

            │
            ▼

────────────────────────────────────
               FreeDF
────────────────────────────────────

PDF Parsing
Document Model
Rendering
Editing
Writing
Capability Discovery
Python API
HTTP API
JSONL CLI
```

Keeping the projects separate allows the engine to evolve independently while remaining reusable by completely unrelated software.

---

# Contributing

Contributions are welcome.

Before implementing a large feature, please open an issue describing:

- the problem being solved;
- the relevant PDF specification;
- proposed public behaviour;
- unsupported cases;
- testing strategy.

Every functional change should include tests.

When possible, keep pull requests focused on a single subsystem.

Examples:

- parser
- rendering
- writing
- OCR
- annotations
- forms

rather than mixing unrelated features.

---

# Testing

Correctness is one of FreeDF's primary goals.

The test suite verifies:

- parser behaviour;
- object resolution;
- document structure;
- rendering contracts;
- editing operations;
- writer correctness;
- API parity;
- regression cases.

Real-world PDFs are preferred whenever licensing allows.

Artificial fixtures are generated for isolated parser tests.

---

# Security

PDF documents are untrusted binary inputs.

FreeDF should assume every document may be malformed or intentionally malicious.

Current security goals include:

- explicit parser failures;
- deterministic behaviour;
- validation before saving;
- atomic writes;
- no silent corruption.

The project has **not** yet undergone formal security auditing or fuzz testing.

Until then, production use on sensitive documents is discouraged.

Security issues should be reported privately before public disclosure.

---

# Versioning

FreeDF currently follows **0.x** versioning.

During this stage:

- APIs may change;
- modules may move;
- serialized formats may evolve;
- backward compatibility is not guaranteed.

Stable APIs will be introduced before the first major release.

---

# License

FreeDF is released under the **MIT License**.

The goal of the project is to provide a permissive PDF engine that can be used freely in both open-source and commercial software.

See the `LICENSE` file for details.

---

# Project Goals

FreeDF is not trying to become another wrapper around an existing PDF SDK.

Its objective is to build a transparent, well-tested and reusable PDF engine whose behaviour is understandable from its source code.

Long-term priorities are:

- correctness over feature count;
- explicit behaviour over hidden magic;
- reusable architecture over application-specific shortcuts;
- deterministic APIs over convenience wrappers;
- long-term maintainability over rapid feature accumulation.

If a feature cannot be implemented safely, FreeDF would rather report that limitation than silently produce an incorrect document.

---

# Acknowledgements

FreeDF builds upon the PDF specification and interoperates with existing PDF tooling where appropriate.

Rendering currently relies on **Poppler**, while the engine itself remains responsible for parsing, document modelling, editing, writing and public API behaviour.

The project intentionally avoids coupling its architecture to any single rendering implementation.

---

# Status

FreeDF is an active long-term project.

The parser, renderer, editing model and writer continue to evolve toward a complete, independent PDF engine suitable for desktop applications, automation and AI-assisted workflows.

Contributions, bug reports and design discussions are always welcome.
No interface has privileged behaviour.

The Python API, HTTP server and JSONL command interface are required to produce identical results for identical operations, and this guarantee is enforced through contract-parity tests.
