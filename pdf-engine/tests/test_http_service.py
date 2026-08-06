from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from pdfengine.api.contracts import API_VERSION, CommandDispatcher, schema_bytes
from pdfengine.service.http import MAX_BODY_BYTES, create_server

from test_engine import StubRenderer


@pytest.fixture
def service(tmp_path):
    from pdfengine import PdfEngine

    dispatcher = CommandDispatcher(
        PdfEngine(cache_root=tmp_path / "cache", renderer=StubRenderer())
    )
    server = create_server("127.0.0.1", 0, dispatcher=dispatcher)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


def _request(service, method: str, path: str, body: bytes | None = None, headers=None):
    host, port = service.server_address[:2]
    connection = HTTPConnection(host, port, timeout=10)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def post_json(service, path: str, payload: dict):
    status, _headers, body = _request(
        service,
        "POST",
        path,
        json.dumps(payload).encode(),
        {"Content-Type": "application/json"},
    )
    return status, json.loads(body)


def open_document(service, path: Path) -> dict:
    status, body = post_json(
        service,
        "/v1/commands",
        {
            "apiVersion": API_VERSION,
            "requestId": "open-1",
            "command": "open",
            "path": str(path),
        },
    )
    assert status == 200
    return body["result"]


def test_health_reports_the_api_version(service) -> None:
    status, _headers, body = _request(service, "GET", "/v1/health")

    assert status == 200
    assert json.loads(body) == {"apiVersion": "v1", "status": "ok"}


def test_http_command_uses_the_agent_response_contract(service, write_pdf) -> None:
    status, body = post_json(
        service,
        "/v1/commands",
        {
            "apiVersion": "v1",
            "requestId": "open-1",
            "command": "open",
            "path": str(write_pdf(["a"])),
        },
    )

    assert status == 200
    assert body["apiVersion"] == "v1" and body["ok"] is True
    assert body["result"]["document"]["pages"][0]["pageId"].startswith("page_")


def test_schema_endpoint_returns_the_identical_published_bytes(service) -> None:
    status, headers, body = _request(service, "GET", "/v1/schema/response")

    assert status == 200
    assert body == schema_bytes("response")
    assert headers["Content-Type"] == "application/schema+json"


def test_an_unknown_schema_is_a_404_envelope(service) -> None:
    status, _headers, body = _request(service, "GET", "/v1/schema/nope")

    assert status == 404
    assert json.loads(body)["error"]["code"] == "invalid_request"


def test_render_artifacts_are_served_by_opaque_id_not_cache_path(
    service, write_pdf
) -> None:
    opened = open_document(service, write_pdf(["a"]))
    _status, rendered = post_json(
        service,
        "/v1/commands",
        {
            "apiVersion": "v1",
            "requestId": "r-1",
            "command": "render",
            "sessionId": opened["sessionId"],
            "pageId": opened["document"]["pages"][0]["pageId"],
            "width": 32,
        },
    )
    artifact_id = rendered["result"]["artifactId"]

    status, headers, body = _request(service, "GET", f"/v1/artifacts/{artifact_id}")

    assert status == 200
    assert headers["Content-Type"] == "image/png"
    assert headers["Cache-Control"] == "no-store"
    assert body.startswith(b"\x89PNG\r\n\x1a\n")


def test_an_unknown_artifact_is_a_404_envelope(service) -> None:
    status, _headers, body = _request(service, "GET", "/v1/artifacts/artifact_absent")

    assert status == 404
    assert json.loads(body)["error"]["message"] == "unknown artifact"


def test_a_failing_command_is_a_400_with_the_shared_envelope(service) -> None:
    status, body = post_json(
        service,
        "/v1/commands",
        {"apiVersion": "v1", "requestId": "x", "command": "inspect", "sessionId": "nope"},
    )

    assert status == 400
    assert body["ok"] is False
    assert body["error"]["code"] == "session_not_found"


def test_malformed_json_is_a_400_envelope(service) -> None:
    status, _headers, body = _request(
        service, "POST", "/v1/commands", b"{not json", {"Content-Type": "application/json"}
    )

    assert status == 400
    assert json.loads(body)["error"]["code"] == "invalid_request"


def test_an_oversize_body_is_refused_before_it_is_parsed(service) -> None:
    status, _headers, body = _request(
        service,
        "POST",
        "/v1/commands",
        b"x" * (MAX_BODY_BYTES + 1),
        {"Content-Type": "application/json"},
    )

    assert status == 413
    assert "exceeds" in json.loads(body)["error"]["message"]


def test_unroutable_paths_and_methods_are_rejected(service) -> None:
    assert _request(service, "GET", "/v1/nope")[0] == 404
    assert _request(service, "POST", "/v1/nope", b"{}")[0] == 404
    assert _request(service, "DELETE", "/v1/commands")[0] == 405


def test_binding_off_loopback_requires_an_explicit_flag() -> None:
    with pytest.raises(ValueError, match="requires --allow-network"):
        create_server("0.0.0.0", 0)


def test_closing_the_server_closes_its_sessions(tmp_path, write_pdf) -> None:
    from pdfengine import PdfEngine

    dispatcher = CommandDispatcher(PdfEngine(cache_root=tmp_path / "cache"))
    server = create_server("127.0.0.1", 0, dispatcher=dispatcher)
    session = dispatcher.engine.open_document(write_pdf(["a"]))

    server.server_close()

    assert session.closed is True
