"""The Python library, the JSONL CLI, and HTTP must agree, byte for byte."""

from __future__ import annotations

import io
import json
import threading
from http.client import HTTPConnection

import pytest

from pdfengine import PdfEngine
from pdfengine.api.contracts import API_VERSION, CommandDispatcher, schema_bytes
from pdfengine.cli.agent import run_agent
from pdfengine.service.http import create_server

from support.fakes import StubRenderer


def _dispatcher(tmp_path, name: str) -> CommandDispatcher:
    return CommandDispatcher(
        PdfEngine(cache_root=tmp_path / name, renderer=StubRenderer())
    )


@pytest.fixture
def surfaces(tmp_path):
    """Three independent stacks over the same contract."""

    direct = _dispatcher(tmp_path, "direct")
    cli = _dispatcher(tmp_path, "cli")
    http = _dispatcher(tmp_path, "http")
    server = create_server("127.0.0.1", 0, dispatcher=http)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def via_python(payload: dict) -> dict:
        return direct.dispatch(payload)

    def via_agent(payload: dict) -> dict:
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

    yield (via_python, via_agent, via_http)

    server.shutdown()
    thread.join(timeout=5)
    server.server_close()
    direct.close()
    cli.close()


def _without_ids(response: dict) -> dict:
    """Drop the values that are unique per stack but not part of the contract."""

    result = dict(response.get("result", {}))
    result.pop("sessionId", None)
    document = result.get("document")
    if document:
        result["document"] = {
            **document,
            "pages": [
                {key: value for key, value in page.items() if key != "pageId"}
                for page in document["pages"]
            ],
        }
    return {**response, "result": result}


def test_python_jsonl_and_http_open_return_equivalent_public_results(
    surfaces, write_pdf
) -> None:
    path = write_pdf(["alpha", "beta"], title="Shared")
    request = {
        "apiVersion": API_VERSION,
        "requestId": "same",
        "command": "open",
        "path": str(path),
    }

    responses = [_without_ids(surface(request)) for surface in surfaces]

    assert responses[0] == responses[1] == responses[2]
    assert responses[0]["result"]["document"]["pageCount"] == 2
    assert responses[0]["result"]["capabilities"]["preview"]["state"] == "ready"


def test_every_surface_reports_an_identical_failure_envelope(surfaces) -> None:
    request = {
        "apiVersion": API_VERSION,
        "requestId": "bad",
        "command": "inspect",
        "sessionId": "session_absent",
    }

    responses = [surface(request) for surface in surfaces]

    assert responses[0] == responses[1] == responses[2]
    assert responses[0]["error"]["code"] == "session_not_found"


def test_every_surface_rejects_an_unsupported_api_version(surfaces) -> None:
    request = {"apiVersion": "v99", "requestId": "old", "command": "capabilities"}

    responses = [surface(request) for surface in surfaces]

    assert responses[0] == responses[1] == responses[2]
    assert responses[0]["error"]["details"]["field"] == "apiVersion"


def test_http_serves_the_same_schema_bytes_as_the_library(surfaces, tmp_path) -> None:
    from pdfengine.service.http import create_server as _create

    server = _create("127.0.0.1", 0, dispatcher=_dispatcher(tmp_path, "schema"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        connection = HTTPConnection(host, port, timeout=10)
        connection.request("GET", "/v1/schema/operation-request")
        served = connection.getresponse().read()
        connection.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert served == schema_bytes("operation-request")


def test_an_edit_batch_projects_identically_on_every_surface(surfaces, write_pdf) -> None:
    projections = []
    for surface in surfaces:
        opened = surface(
            {
                "apiVersion": API_VERSION,
                "requestId": "open",
                "command": "open",
                "path": str(write_pdf(["a", "b", "c"])),
            }
        )["result"]
        page_ids = [page["pageId"] for page in opened["document"]["pages"]]
        applied = surface(
            {
                "apiVersion": API_VERSION,
                "requestId": "apply",
                "command": "apply",
                "sessionId": opened["sessionId"],
                "operations": [
                    {"kind": "reorder_pages", "pageIds": page_ids[::-1]},
                    {"kind": "rotate_pages", "pageIds": [page_ids[0]], "degrees": 90},
                ],
            }
        )
        projections.append(_without_ids(applied))

    assert projections[0] == projections[1] == projections[2]
    assert [page["sourceIndex"] for page in projections[0]["result"]["document"]["pages"]] == [
        2,
        1,
        0,
    ]
