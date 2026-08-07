"""Every surface must mean the same thing, on backends that always exist.

The Python library, the JSONL CLI, and the HTTP service are three transports
over one contract. Nothing here touches Poppler or Tesseract: a machine with no
external binaries at all must still be able to prove the contract holds, so
that an absent binary stops verifying an *adapter*, never the contract.
"""

from __future__ import annotations

import threading
from http.client import HTTPConnection

import pytest

from pdfengine.api.contracts import API_VERSION, COMMANDS, schema_bytes
from support.surfaces import semantic, surface_dispatchers


@pytest.fixture
def surfaces(tmp_path):
    """Three independent stacks over the same contract."""

    with surface_dispatchers(tmp_path) as three:
        yield three


def _open(surface, path) -> dict:
    return surface(
        {
            "apiVersion": API_VERSION,
            "requestId": "open",
            "command": "open",
            "path": str(path),
        }
    )["result"]


# -- the original five ----------------------------------------------------


def test_python_jsonl_and_http_open_return_equivalent_public_results(
    surfaces, write_pdf
) -> None:
    responses = []
    for surface in surfaces:
        path = write_pdf(["alpha", "beta"], title="Shared")
        responses.append(
            semantic(
                surface(
                    {
                        "apiVersion": API_VERSION,
                        "requestId": "same",
                        "command": "open",
                        "path": str(path),
                    }
                )
            )
        )

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


def test_http_serves_the_same_schema_bytes_as_the_library(tmp_path) -> None:
    """The schema a caller downloads must be the schema the library validates
    against — a drifting copy would let HTTP callers write requests the library
    rejects."""

    from pdfengine import PdfEngine
    from pdfengine.api.contracts import CommandDispatcher
    from pdfengine.service.http import create_server
    from support.fakes import DpiStubRenderer

    dispatcher = CommandDispatcher(
        PdfEngine(cache_root=tmp_path / "schema-cache", renderer=DpiStubRenderer())
    )
    server = create_server("127.0.0.1", 0, dispatcher=dispatcher)
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
        dispatcher.close()

    assert served == schema_bytes("operation-request")


def test_an_edit_batch_projects_identically_on_every_surface(surfaces, write_pdf) -> None:
    projections = []
    for surface in surfaces:
        opened = _open(surface, write_pdf(["a", "b", "c"]))
        page_ids = [page["pageId"] for page in opened["document"]["pages"]]
        projections.append(
            semantic(
                surface(
                    {
                        "apiVersion": API_VERSION,
                        "requestId": "apply",
                        "command": "apply",
                        "sessionId": opened["sessionId"],
                        "operations": [
                            {"kind": "reorder_pages", "pageIds": page_ids[::-1]},
                            {
                                "kind": "rotate_pages",
                                "pageIds": [page_ids[0]],
                                "degrees": 90,
                            },
                        ],
                    }
                )
            )
        )

    assert projections[0] == projections[1] == projections[2]
    assert [
        page["sourceIndex"] for page in projections[0]["result"]["document"]["pages"]
    ] == [2, 1, 0]


# -- the v0.2 additions ---------------------------------------------------


def test_add_text_layer_behaves_identically_on_every_surface(
    surfaces, write_pdf
) -> None:
    """This runs without Tesseract on purpose. Contract parity must never
    depend on an installed binary."""

    answers = []
    for surface in surfaces:
        opened = _open(surface, write_pdf(["scanned"]))
        answers.append(
            semantic(
                surface(
                    {
                        "apiVersion": API_VERSION,
                        "requestId": "ocr",
                        "command": "apply",
                        "sessionId": opened["sessionId"],
                        "operations": [
                            {
                                "kind": "add_text_layer",
                                "pageIds": [opened["document"]["pages"][0]["pageId"]],
                                "language": "eng",
                                "dpi": 150,
                            }
                        ],
                    }
                )
            )
        )

    assert answers[0] == answers[1] == answers[2]
    assert answers[0]["ok"] is True


def test_the_artifact_command_behaves_identically_on_every_surface(
    surfaces, write_pdf
) -> None:
    """An artifact fetched back must describe the same bytes everywhere; the
    sha256 is compared, not normalized away."""

    answers = []
    for surface in surfaces:
        opened = _open(surface, write_pdf(["only"]))
        rendered = surface(
            {
                "apiVersion": API_VERSION,
                "requestId": "render",
                "command": "render",
                "sessionId": opened["sessionId"],
                "pageId": opened["document"]["pages"][0]["pageId"],
            }
        )["result"]
        answers.append(
            semantic(
                surface(
                    {
                        "apiVersion": API_VERSION,
                        "requestId": "fetch",
                        "command": "artifact",
                        "sessionId": opened["sessionId"],
                        "artifactId": rendered["artifact"]["artifactId"],
                    }
                )
            )
        )

    assert answers[0] == answers[1] == answers[2]
    assert answers[0]["ok"] is True


def test_capabilities_agree_on_every_surface(surfaces, write_pdf) -> None:
    """A session-scoped capabilities answer drives what a client offers its
    user; a surface that advertises a different menu is a real defect."""

    answers = []
    for surface in surfaces:
        opened = _open(surface, write_pdf(["only"]))
        answers.append(
            semantic(
                surface(
                    {
                        "apiVersion": API_VERSION,
                        "requestId": "caps",
                        "command": "capabilities",
                        "sessionId": opened["sessionId"],
                    }
                )
            )
        )

    assert answers[0] == answers[1] == answers[2]
    assert answers[0]["result"]["capabilities"]["document"]


def test_a_closed_session_fails_identically_on_every_surface(
    surfaces, write_pdf
) -> None:
    """A transport that reported a closed session as a *missing* one would send
    a client hunting for a bug that is not there."""

    answers = []
    for surface in surfaces:
        opened = _open(surface, write_pdf(["only"]))
        surface(
            {
                "apiVersion": API_VERSION,
                "requestId": "close",
                "command": "close",
                "sessionId": opened["sessionId"],
            }
        )
        answers.append(
            semantic(
                surface(
                    {
                        "apiVersion": API_VERSION,
                        "requestId": "after",
                        "command": "inspect",
                        "sessionId": opened["sessionId"],
                    }
                )
            )
        )

    assert answers[0] == answers[1] == answers[2]
    assert answers[0]["error"]["code"] == "session_invalid_state"
    assert answers[0]["error"]["details"]["state"] == "closed"
    assert answers[0]["error"]["details"]["allowed"] == ["open"]


def test_every_command_is_reachable_from_every_surface(surfaces, write_pdf) -> None:
    """A command that only one transport can reach is the bug this catches."""

    for surface in surfaces:
        opened = _open(surface, write_pdf(["a", "b"]))
        session_id = opened["sessionId"]
        page_id = opened["document"]["pages"][0]["pageId"]
        rendered = surface(
            {
                "apiVersion": API_VERSION,
                "requestId": "r",
                "command": "render",
                "sessionId": session_id,
                "pageId": page_id,
            }
        )["result"]
        exercised = {"open", "render"}
        for command, extra in (
            ("inspect", {}),
            ("capabilities", {}),
            (
                "apply",
                {
                    "operations": [
                        {
                            "kind": "rotate_pages",
                            "pageIds": [page_id],
                            "degrees": 90,
                        }
                    ]
                },
            ),
            ("undo", {}),
            ("redo", {}),
            ("artifact", {"artifactId": rendered["artifact"]["artifactId"]}),
            ("save", {"dryRun": True}),
            ("close", {}),
        ):
            response = surface(
                {
                    "apiVersion": API_VERSION,
                    "requestId": command,
                    "command": command,
                    "sessionId": session_id,
                    **extra,
                }
            )
            assert response["ok"] is True, (command, response.get("error"))
            exercised.add(command)

        assert exercised == set(COMMANDS)
