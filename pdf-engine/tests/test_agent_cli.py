from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from pdfengine import __version__
from pdfengine.api.contracts import API_VERSION, CommandDispatcher, SCHEMA_NAMES
from pdfengine.cli.agent import run_agent
from pdfengine.cli.main import main

from test_engine import StubRenderer
from test_rewrite import page_texts


SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture
def agent(tmp_path):
    """Run JSONL requests through the agent loop and return parsed responses."""

    from pdfengine import PdfEngine

    dispatcher = CommandDispatcher(
        PdfEngine(cache_root=tmp_path / "cache", renderer=StubRenderer())
    )

    def run(*requests: dict | str) -> list[dict]:
        lines = [
            item if isinstance(item, str) else json.dumps(item) for item in requests
        ]
        stdout = io.StringIO()
        run_agent(io.StringIO("\n".join(lines) + "\n"), stdout, dispatcher)
        return [json.loads(line) for line in stdout.getvalue().splitlines()]

    yield run
    dispatcher.close()


def open_request(path: Path, request_id: str = "open-1") -> dict:
    return {
        "apiVersion": API_VERSION,
        "requestId": request_id,
        "command": "open",
        "path": str(path),
    }


def test_agent_cli_emits_one_json_response_per_request(agent, write_pdf) -> None:
    responses = agent(open_request(write_pdf(["a"])))

    assert len(responses) == 1
    assert responses[0]["requestId"] == "open-1"
    assert responses[0]["ok"] is True


def test_a_session_survives_across_lines_until_eof(agent, write_pdf) -> None:
    first = agent(open_request(write_pdf(["a", "b"])))[0]
    session_id = first["result"]["sessionId"]
    page_a = first["result"]["document"]["pages"][0]["pageId"]

    responses = agent(
        {
            "apiVersion": "v1",
            "requestId": "apply-1",
            "command": "apply",
            "sessionId": session_id,
            "operations": [{"kind": "delete_pages", "pageIds": [page_a]}],
        },
        {
            "apiVersion": "v1",
            "requestId": "inspect-1",
            "command": "inspect",
            "sessionId": session_id,
        },
    )

    assert [item["requestId"] for item in responses] == ["apply-1", "inspect-1"]
    assert responses[1]["result"]["document"]["pageCount"] == 1


def test_malformed_json_becomes_an_envelope_not_a_crash(agent) -> None:
    responses = agent("{not json")

    assert responses[0]["ok"] is False
    assert responses[0]["error"]["code"] == "invalid_request"
    assert responses[0]["requestId"] == "unknown"


def test_blank_lines_produce_no_output(agent, write_pdf) -> None:
    responses = agent("", "   ", open_request(write_pdf(["a"])))

    assert len(responses) == 1


def test_a_closed_session_reports_a_typed_error(agent, write_pdf) -> None:
    opened = agent(open_request(write_pdf(["a"])))[0]
    session_id = opened["result"]["sessionId"]

    responses = agent(
        {"apiVersion": "v1", "requestId": "c", "command": "close", "sessionId": session_id},
        {"apiVersion": "v1", "requestId": "i", "command": "inspect", "sessionId": session_id},
    )

    assert responses[1]["error"]["code"] == "session_invalid_state"


def test_the_full_agent_workflow_saves_a_verified_copy(agent, write_pdf, tmp_path) -> None:
    opened = agent(open_request(write_pdf(["alpha", "beta"])))[0]["result"]
    session_id = opened["sessionId"]
    page_a, page_b = [page["pageId"] for page in opened["document"]["pages"]]
    target = tmp_path / "final.pdf"

    responses = agent(
        {
            "apiVersion": "v1",
            "requestId": "dry",
            "command": "apply",
            "sessionId": session_id,
            "dryRun": True,
            "operations": [{"kind": "reorder_pages", "pageIds": [page_b, page_a]}],
        },
        {
            "apiVersion": "v1",
            "requestId": "apply",
            "command": "apply",
            "sessionId": session_id,
            "operations": [{"kind": "reorder_pages", "pageIds": [page_b, page_a]}],
        },
        {
            "apiVersion": "v1",
            "requestId": "save",
            "command": "save",
            "sessionId": session_id,
            "path": str(target),
        },
    )

    assert responses[0]["result"]["dryRun"] is True
    assert all(item["ok"] for item in responses)
    assert page_texts(target) == ["beta", "alpha"]


def _run_cli(*arguments: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "from pdfengine.cli.main import main; raise SystemExit(main())", *arguments],
        input=stdin,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(SRC)},
    )


def test_the_installed_command_keeps_stdout_pure_json(write_pdf, tmp_path) -> None:
    result = _run_cli(
        "agent",
        "--cache-root",
        str(tmp_path / "cache"),
        stdin=json.dumps(open_request(write_pdf(["a"]))) + "\n",
    )

    assert result.returncode == 0
    assert result.stderr == ""
    line = json.loads(result.stdout)
    assert line["requestId"] == "open-1" and line["ok"] is True


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_schema_command_prints_the_published_bytes(name: str) -> None:
    from pdfengine.api.contracts import schema_bytes

    result = _run_cli("schema", name)

    assert result.returncode == 0
    assert json.loads(result.stdout) == json.loads(schema_bytes(name))
    assert json.loads(result.stdout)["$id"].endswith(f"{name}.json")


def test_version_is_reported(capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--version"])

    assert raised.value.code == 0
    assert capsys.readouterr().out.strip() == __version__


def test_an_unknown_command_exits_with_a_usage_error() -> None:
    result = _run_cli("explode")

    assert result.returncode == 2
    assert "usage" in result.stderr.lower()
