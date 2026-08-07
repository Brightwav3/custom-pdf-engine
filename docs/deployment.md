# Deployment models

FreeDF runs one core behind three interchangeable surfaces. All three route
through `CommandDispatcher.dispatch`, so a request means the same thing
whichever one delivers it. None of them is the canonical deployment — pick the
one that matches how your caller is shaped.

Every surface accepts the same command set: `open`, `inspect`, `capabilities`,
`render`, `apply`, `undo`, `redo`, `save`, `artifact`, `close`.

## Python package

Use when the caller is Python and wants typed models and in-process speed.

```python
from pdfengine import PdfEngine

engine = PdfEngine()
session = engine.open_document("input.pdf")
engine.save(session, "output.pdf")
engine.close(session)
```

**Lifecycle:** the caller owns the `PdfEngine`. Sessions live as long as it
does. `close_all()` releases every session's cache directory.

**Failure modes:** typed exceptions, all subclasses of `PdfEngineError`, each
with a `code` attribute matching the JSON contract's error codes.

**Trade-off:** fastest and most expressive, but the caller must be Python and
shares a process with the engine — a parser crash takes the host down with it.

## HTTP service

Use when there are multiple clients, or the caller is not Python and wants a
socket.

```bash
pdfengine serve
```

Or, in-process, `pdfengine.service.http.serve(host=..., port=...)`. There is no
`python -m pdfengine.service.http` entry point; the module is a library, and
`serve` is what the `serve` subcommand calls.

Binds `127.0.0.1:8757` by default. Binding a non-loopback address requires
`--allow-network` and is a deliberate, explicit act.

- `POST /v1/commands` — the whole contract, one JSON request per call
- `GET /v1/health` — liveness
- `GET /v1/schema/<name>` — the exact schema bytes the library serves
- `GET /v1/artifacts/<id>` — raw artifact bytes, a convenience over the
  ownership-checked `artifact` command

**Security note — the artifact GET route is not ownership-checked.** The
`artifact` command verifies that the artifact belongs to the session asking for
it. `GET /v1/artifacts/<id>` deliberately does not: it exists so a client can
stream bytes without a JSON round trip, and it will hand any artifact to anyone
who can reach the port and knows the ID. That is safe on loopback with a single
trusted caller and unsafe the moment it is not. If you pass `--allow-network`,
or expose the port through a proxy or container mapping, assume every artifact
is readable by everyone who can reach it, and put your own authentication in
front. The `artifact` command is the ownership-checked way in; use it when the
callers are not all trusted.

**Lifecycle:** one process, one dispatcher, sessions shared across connections.
Stopping the process closes every session.

**Failure modes:** HTTP status plus the same JSON error envelope as every other
surface. A failed command is `400` with `ok: false`.

**Trade-off:** language-agnostic and process-isolated, but sessions are global
to the process. The server is threaded, so two concurrent requests can touch one
session; the contract treats single-writer-per-session as the caller's
responsibility.

## JSONL subprocess

Use when an agent or tool speaks stdin/stdout.

```bash
pdfengine agent
```

One JSON request per input line, one response envelope per output line, in
order. Nothing but response JSON reaches stdout; diagnostics go to stderr, so a
caller can parse the stream without heuristics.

**Lifecycle:** sessions persist across lines for the life of the process.
Process exit closes all of them.

**Failure modes:** a malformed line is answered with an `invalid_request`
envelope and the stream continues. The process does not exit on a bad request.

**Trade-off:** trivial to supervise and sandbox, with no network surface at all,
but strictly serial — one request at a time.

## Choosing

| | Python | HTTP | JSONL |
| --- | --- | --- | --- |
| Caller language | Python only | any | any |
| Process isolation | none | full | full |
| Concurrency | caller's problem | threaded | serial |
| Network surface | none | loopback by default | none |
| Binary payloads | native `bytes` | base64 or a GET route | base64 |
