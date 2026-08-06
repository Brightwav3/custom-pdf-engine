# Agent guide

This is the whole contract an automated caller needs. You never have to parse
prose, guess whether a feature is supported, or infer a page position.

## Two transports, one contract

```bash
pdfengine agent                        # one JSON request per stdin line
pdfengine serve --port 8757            # POST /v1/commands
```

Both hand the request body to the same dispatcher, so responses are
equivalent. On the CLI, **stdout contains response JSON and nothing else**;
diagnostics go to stderr. Over HTTP, a successful envelope is `200` and a
failing one is `400`, with the same body either way.

Schemas are published and byte-identical on both:

```bash
pdfengine schema operation-request
curl http://127.0.0.1:8757/v1/schema/operation-request
```

Available names: `open-request`, `operation-request`, `save-request`,
`response`.

## The envelope

Every response:

```json
{
  "apiVersion": "v1",
  "requestId": "r-1",
  "ok": true,
  "result": { "...": "..." },
  "warnings": []
}
```

On failure, `result` is absent and `error` is present:

```json
{
  "apiVersion": "v1",
  "requestId": "r-7",
  "ok": false,
  "error": {
    "code": "invalid_request",
    "message": "operations must not be empty",
    "details": { "field": "operations" }
  },
  "warnings": []
}
```

`code` is a fixed enum — see `response.json`. Branch on `code`, not on
`message`.

## The workflow

`open` → read `capabilities` → preview → `apply` with `dryRun` → `apply` →
`save` → `inspect` the result.

### 1. Open

```json
{"apiVersion":"v1","requestId":"open-1","command":"open","path":"/docs/report.pdf"}
```

The result carries `sessionId`, the `document`, `capabilities`, and
`nextActions`. Page IDs are stable for the life of the session; **positions are
not**. Always address pages by `pageId`.

```json
{
  "sessionId": "session_5f3c…",
  "path": "/docs/report.pdf",
  "document": {
    "pageCount": 2,
    "title": "Report",
    "pages": [
      {"pageId":"page_a1…","index":0,"sourceIndex":0,"width":612.0,"height":792.0,"rotation":0},
      {"pageId":"page_b2…","index":1,"sourceIndex":1,"width":612.0,"height":792.0,"rotation":0}
    ]
  },
  "capabilities": {
    "preview": {"state":"ready","detail":""},
    "operations": [{"kind":"rotate_pages","safe":true,"requires":[],"schema":"operation-request.json"}],
    "save": {"fullRewriteOnly": true, "inPlaceRequiresOptIn": true}
  },
  "nextActions": ["inspect","render","apply","save","close"]
}
```

Check `capabilities.preview.state` before rendering: `ready`, `blocked`
(Poppler is not installed — everything else still works), or `error`.

### 2. Preview

```json
{"apiVersion":"v1","requestId":"r-1","command":"render","sessionId":"session_5f3c…","pageId":"page_a1…","width":320}
```

The result has `width`, `height`, `cacheHit`, `contentType`, an inline
`imageBase64`, and an `artifactId`. Over HTTP you can fetch the bytes at
`GET /v1/artifacts/<artifactId>` instead of decoding base64. Artifact IDs are
opaque — cache paths are never exposed.

### 3. Dry-run, then apply

Send exactly the batch you intend to commit, with `"dryRun": true`. The
response shows the projected document without recording anything.

```json
{
  "apiVersion":"v1","requestId":"dry-1","command":"apply",
  "sessionId":"session_5f3c…","dryRun":true,
  "operations":[
    {"kind":"reorder_pages","pageIds":["page_b2…","page_a1…"]},
    {"kind":"rotate_pages","pageIds":["page_b2…"],"degrees":90}
  ]
}
```

If it looks right, resend without `dryRun`. A batch is validated as a whole:
if any operation is invalid, nothing is recorded.

`undo` and `redo` take just a `sessionId`. Applying a new batch after an undo
discards the redo branch.

### 4. Save

```json
{"apiVersion":"v1","requestId":"save-1","command":"save","sessionId":"session_5f3c…","path":"/docs/report-final.pdf"}
```

Omit `path` and the engine picks a distinct `<name>-edited.pdf` beside the
source. `"dryRun": true` reports the target without writing. Writing over the
source needs `"allowReplaceSource": true` **and** a source whose fingerprint
still matches; otherwise you get `source_changed`.

The result is `{"sessionId":…,"path":…,"dryRun":false,"written":true}`.

### 5. Verify, then close

`open` the saved path and `inspect` it — the engine re-reads the file it just
wrote, so this is a real check, not an echo. Then `close` each session to drop
its password and delete its preview cache.

## Operations

Full grammar in `operation-request.json`. All page targeting is by ID.

```json
{"kind":"rotate_pages","pageIds":["page_a"],"degrees":90}
{"kind":"delete_pages","pageIds":["page_a"]}
{"kind":"reorder_pages","pageIds":["page_c","page_a","page_b"]}
{"kind":"extract_pages","pageIds":["page_c","page_a"]}
{"kind":"insert_blank_page","afterPageId":"page_a","width":612,"height":792}
{"kind":"crop_pages","pageIds":["page_a"],"box":[0,0,300,400]}
{"kind":"set_metadata","entries":{"title":"Final","author":null}}
{"kind":"import_pages","sourceSessionId":"session_other","pageIds":["page_x"],"afterPageId":"page_a"}
```

`reorder_pages` must list every current page exactly once. `import_pages`
needs the other document opened in the same process first; its `pageIds` are
that session's IDs. `set_metadata` clears an entry with `null`.

## Rules the engine enforces for you

- A normal save never touches the source file.
- An in-place save is refused if the source changed on disk.
- Unknown commands, unknown fields, and unknown operation kinds are rejected
  rather than ignored — a typo will not silently do nothing.
- Unsupported documents fail with `unsupported_pdf` and
  `details.feature` naming the first blocking construct (`xref stream`,
  `encryption`, `object stream`, `stream filter …`).
- The HTTP service binds loopback only unless started with `--allow-network`,
  caps bodies at 1 MiB, and sends `Cache-Control: no-store`.

## Minimal session

```bash
printf '%s\n' \
  '{"apiVersion":"v1","requestId":"1","command":"open","path":"/docs/report.pdf"}' \
  '{"apiVersion":"v1","requestId":"2","command":"capabilities"}' \
  | pdfengine agent
```
