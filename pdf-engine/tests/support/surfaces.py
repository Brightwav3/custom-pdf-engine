"""One request, three transports, one comparable answer.

Comparison is semantic, not byte-for-byte: request IDs, session IDs, artifact
IDs, and filesystem paths are generated per stack and are legitimately
different. Normalizing them is what lets a real difference in *meaning* stand
out instead of drowning in noise.

What is deliberately *not* normalized is just as important. A ``sha256`` over
the produced bytes is the same on every surface when the surfaces agree, so
collapsing it would throw away the single strongest signal a parity test has:
that three independent stacks produced identical content. It stays.
"""

from __future__ import annotations

import io
import json
import re
import threading
from contextlib import contextmanager
from http.client import HTTPConnection

from pdfengine import PdfEngine
from pdfengine.api.contracts import CommandDispatcher
from pdfengine.cli.agent import run_agent
from pdfengine.service.http import create_server

from .fakes import DpiStubRenderer, StubOcr


_NORMALIZED = "<normalized>"
_PATH_SUFFIXES = (".pdf", ".png")

# The engine mints every public identifier as ``<kind>_<uuid4().hex>``. Matching
# that *shape* rather than the bare prefix matters: ``session_invalid_state`` is
# an error code, not an identifier, and a prefix rule would collapse it into the
# same placeholder as ``session_not_found`` — leaving a surface free to return
# the wrong error code and still pass.
_GENERATED_ID = re.compile(r"\b(?:session|artifact|page)_[0-9a-f]{32}\b")


def normalized(value):
    """Replace every per-stack generated identifier with a placeholder.

    Only two families of value qualify: identifiers the engine mints from a
    UUID, and filesystem paths, which live under a per-surface temporary
    directory. Identifiers are replaced wherever they occur, including inside a
    human-readable message, because a message that quotes a session ID is
    otherwise different on every stack for no contractual reason.

    Everything else — states, counts, sizes, content types, error codes, and
    content digests — is part of what the surfaces must agree on and is passed
    through untouched. In particular ``sha256`` is *not* normalized: three
    stacks that agree produce the same digest, and that agreement is the
    strongest evidence this harness can offer.
    """

    if isinstance(value, dict):
        return {key: normalized(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [normalized(item) for item in value]
    if isinstance(value, str):
        if value.endswith(_PATH_SUFFIXES):
            return _NORMALIZED  # a per-stack temporary path
        return _GENERATED_ID.sub(_NORMALIZED, value)
    return value


def semantic(response: dict) -> dict:
    """The part of a response every surface must agree on."""

    trimmed = dict(response)
    trimmed.pop("requestId", None)
    return normalized(trimmed)


@contextmanager
def surface_dispatchers(tmp_path, renderer=None, ocr=None):
    """Yield (via_python, via_jsonl, via_http), each over its own engine."""

    def build(name: str) -> CommandDispatcher:
        return CommandDispatcher(
            PdfEngine(
                cache_root=tmp_path / name,
                renderer=renderer() if renderer else DpiStubRenderer(),
                ocr=ocr() if ocr else StubOcr(),
            )
        )

    direct, cli, http = build("direct"), build("cli"), build("http")
    server = create_server("127.0.0.1", 0, dispatcher=http)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def via_python(payload: dict) -> dict:
        return direct.dispatch(payload)

    def via_jsonl(payload: dict) -> dict:
        stdout = io.StringIO()
        run_agent(io.StringIO(json.dumps(payload) + "\n"), stdout, cli)
        return json.loads(stdout.getvalue())

    def via_http(payload: dict) -> dict:
        host, port = server.server_address[:2]
        connection = HTTPConnection(host, port, timeout=10)
        try:
            connection.request(
                "POST",
                "/v1/commands",
                body=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            return json.loads(connection.getresponse().read())
        finally:
            connection.close()

    try:
        yield (via_python, via_jsonl, via_http)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        direct.close()
        cli.close()
