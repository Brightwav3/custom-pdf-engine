"""A loopback-only HTTP front end over the same command dispatcher.

Every command body is handed to :meth:`CommandDispatcher.dispatch`, the same
call the JSONL CLI makes, so the two transports cannot drift apart.
"""

from __future__ import annotations

import json
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pdfengine.api.contracts import API_VERSION, CommandDispatcher, failure, schema_bytes
from pdfengine.errors import InvalidRequestError


LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
MAX_BODY_BYTES = 1024 * 1024


class _Handler(BaseHTTPRequestHandler):
    server_version = "pdfengine/0.1"
    protocol_version = "HTTP/1.1"

    @property
    def dispatcher(self) -> CommandDispatcher:
        return self.server.dispatcher  # type: ignore[attr-defined]

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        # Access logs would be noise on a single-user loopback service.
        pass

    # -- responses ---------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict) -> None:
        self._send(
            status,
            json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            "application/json",
        )

    # -- routes ------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/v1/health":
            self._send_json(200, {"apiVersion": API_VERSION, "status": "ok"})
            return
        if path.startswith("/v1/schema/"):
            name = path[len("/v1/schema/") :]
            try:
                self._send(200, schema_bytes(name), "application/schema+json")
            except InvalidRequestError as exc:
                self._send_json(
                    404, failure("unknown", exc.code, str(exc), field="name")
                )
            return
        if path.startswith("/v1/artifacts/"):
            artifact = self.dispatcher.artifacts.get(path[len("/v1/artifacts/") :])
            if artifact is None:
                self._send_json(
                    404, failure("unknown", "invalid_request", "unknown artifact")
                )
                return
            self._send(200, artifact, "image/png")
            return
        self._send_json(404, failure("unknown", "invalid_request", f"no route {path}"))

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/v1/commands":
            self._send_json(
                404, failure("unknown", "invalid_request", f"no route {self.path}")
            )
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0:
            self._send_json(
                400, failure("unknown", "invalid_request", "invalid Content-Length")
            )
            return
        if length > MAX_BODY_BYTES:
            # The body is never read, so the connection cannot be reused.
            self.close_connection = True
            self._send_json(
                413,
                failure(
                    "unknown",
                    "invalid_request",
                    f"request body exceeds {MAX_BODY_BYTES} bytes",
                ),
            )
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json(
                400, failure("unknown", "invalid_request", f"malformed JSON: {exc}")
            )
            return

        response = self.dispatcher.dispatch(payload)
        self._send_json(200 if response["ok"] else 400, response)

    def do_PUT(self) -> None:  # noqa: N802
        self._send_json(
            405, failure("unknown", "invalid_request", "method not allowed")
        )

    do_DELETE = do_PUT
    do_PATCH = do_PUT


class PdfEngineHttpServer(ThreadingHTTPServer):
    """A threading HTTP server that owns one dispatcher."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, dispatcher: CommandDispatcher) -> None:
        super().__init__(address, _Handler)
        self.dispatcher = dispatcher

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            self.dispatcher.close()


def create_server(
    host: str = "127.0.0.1",
    port: int = 8757,
    cache_root: str | Path | None = None,
    allow_network: bool = False,
    dispatcher: CommandDispatcher | None = None,
) -> PdfEngineHttpServer:
    if host not in LOOPBACK_HOSTS and not allow_network:
        raise ValueError("network binding requires --allow-network")
    return PdfEngineHttpServer(
        (host, port), dispatcher or CommandDispatcher(cache_root=cache_root)
    )


def serve(
    host: str = "127.0.0.1",
    port: int = 8757,
    cache_root: str | Path | None = None,
    allow_network: bool = False,
) -> None:
    """Run until SIGINT, then shut down cleanly."""

    server = create_server(host, port, cache_root, allow_network)

    def stop(*_args) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    try:
        signal.signal(signal.SIGINT, stop)
    except ValueError:  # pragma: no cover - not on the main thread
        pass
    try:
        server.serve_forever()
    finally:
        server.server_close()
