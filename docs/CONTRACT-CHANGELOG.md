# Contract changelog

Entries are additive unless marked otherwise. See `docs/contract-policy.md` for
what may change within a version.

## v1 — 0.2.0

### Added

- Command `artifact`: `{sessionId, artifactId}` returns an artifact descriptor
  and its bytes as base64. Available on all three surfaces.
- Artifact kinds `page_render`, `thumbnail`, and `saved_document`.
- Operation kind `add_text_layer`, previously reachable only from Python.
- `capabilities` accepts an optional `sessionId`; with one it returns a
  `document` block and `allowedCommands`. Without one the response is unchanged
  in shape apart from the additions below.
- `capabilities` gains `filters`, listing the stream filters this build can
  decode, and a per-operation `state`/`detail` so a caller learns that
  `add_text_layer` is unavailable before it starts a batch rather than by
  catching an error halfway through.
- Capability state `unavailable`, meaning "this installation cannot provide it",
  distinct from `blocked`, meaning "this document blocks it".
- `render` results gain `artifact`; `save` results gain `artifact` on a real
  write.
- `inspect` results gain `state`. In practice it is always `"open"`, because
  `inspect` on a closed session fails with `session_invalid_state` before it can
  answer; the field exists so callers can read lifecycle state from the response
  rather than infer it.
- Capability entries for `ocr` list `languages` and `modes`.
- Schemas `artifact-request` and `capabilities-response`.
- `response.json`: the error-code enum gains `session_invalid_state` and
  `ocr_unavailable`.
- `response.json`: the error-code enum gains `ocr_error`. The code existed
  before this release, but only Python callers could ever see it: before v0.2
  `parse_operation` had no `add_text_layer` branch, so no JSON request could
  reach the OCR path. Exposing `add_text_layer` on the JSON surfaces made
  `ocr_error` an emittable wire code, and the schema this project serves at
  `GET /v1/schema/response` has to list it or a validating client rejects a
  legitimate error envelope whenever Tesseract fails on a page. The full enum
  now matches every `PdfEngineError` subclass in `errors.py`; `ocr_error` was
  the only one missing.

### Changed (behaviour)

- A command naming a **closed** session now fails with
  `session_invalid_state` instead of `session_not_found`, carrying
  `details.state` and `details.allowed`. An ID that was never issued still
  returns `session_not_found`. Both cases failed before; only the code and the
  detail are new. This is a changed error code for an existing situation, and it
  is listed here rather than under "Added" because a client that switched on
  `session_not_found` to mean "closed" must be updated.
- `undo` and `redo` now reject unknown request fields with `invalid_request` and
  `details.field` naming the offending key. They previously accepted them
  silently, so `{"command": "undo", "sessionId": ..., "bogusField": 1}` appeared
  to succeed. Every sibling command — `open`, `inspect`, `capabilities`,
  `render`, `apply`, `save`, `artifact`, `close` — already rejected them; these
  two were an oversight, not a designed exemption.

  This is called out here rather than filed under "Added" because it is
  strictly a tightening of validation, and the policy says tightening forces a
  new version. That rule is being overruled for this one case deliberately: it
  corrects an inconsistency rather than changing an intended behaviour, and no
  caller can reasonably have depended on `undo` swallowing a typo'd field — the
  realistic effect of the old behaviour was a malformed request that looked
  like it worked. A caller sending extra fields to `undo` or `redo` today will
  start seeing `invalid_request`, which is the point.

- A missing Tesseract or Poppler installation now reports `unavailable` rather
  than `blocked`. The adapters in `ocr/tesseract.py` and `rendering/poppler.py`
  changed their missing-executable path accordingly.

### Changed (schema)

- `response.json`: the two capability-state enums that read
  `["ready", "blocked"]` now read `["ready", "blocked", "unavailable", "error"]`.
  This widens what validates, so no previously valid response became invalid.

### Deprecated

- `capabilities.read` is retained as an alias of `capabilities.document`. It is
  not removed, per the policy. Prefer `document`.

### Renamed (product only)

- The product and the distribution are now **FreeDF**: `pyproject.toml` builds
  `freedf`, and the prose across the repository says FreeDF. Nothing on the wire
  or in an import moved. The Python package is still `pdfengine`, the console
  script is still `pdfengine`, and every command name, operation kind, error
  `code`, artifact kind, schema name, and JSON field is byte-for-byte what it
  was.

  This split is deliberate rather than unfinished work. `docs/contract-policy.md`
  says an import path or an identifier may not move without a version bump, and
  it was published in this same release; renaming the package here would have
  broken every documented import immediately after promising not to. Renaming
  the Python package is tracked as its own future release with a migration note.
  `tests/contracts/test_packaging.py` pins both halves so the boundary cannot be
  crossed by accident.

### Known limitation

- `filters.decodable` is `["FlateDecode"]`, but Flate *with a predictor* is not
  actually decodable by this version. A flat list of filter names cannot express
  that distinction. `document.textContent` is what reports the truth for a
  specific file.

## v1 — 0.1.0

Initial contract: commands `open`, `inspect`, `capabilities`, `render`, `apply`,
`undo`, `redo`, `save`, `close`; operation kinds `rotate_pages`,
`delete_pages`, `reorder_pages`, `extract_pages`, `insert_blank_page`,
`crop_pages`, `set_metadata`, `import_pages`.
