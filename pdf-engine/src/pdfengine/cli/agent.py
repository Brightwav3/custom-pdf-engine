"""One JSON request per stdin line, one response envelope per stdout line.

Nothing but response JSON ever reaches stdout, so an agent can parse the
stream without heuristics. Diagnostics go to stderr.
"""

from __future__ import annotations

import json
from typing import IO

from pdfengine.api.contracts import CommandDispatcher, failure


def dispatch_json_line(line: str, dispatcher: CommandDispatcher) -> dict | None:
    """Handle one input line; ``None`` means the line was blank."""

    line = line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        return failure("unknown", "invalid_request", f"malformed JSON: {exc.msg}")
    return dispatcher.dispatch(payload)


def run_agent(
    stdin: IO[str],
    stdout: IO[str],
    dispatcher: CommandDispatcher | None = None,
) -> int:
    """Serve requests until EOF, keeping sessions alive across lines."""

    owned = dispatcher is None
    dispatcher = dispatcher or CommandDispatcher()
    try:
        for raw_line in stdin:
            response = dispatch_json_line(raw_line, dispatcher)
            if response is None:
                continue
            stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            stdout.flush()
    finally:
        if owned:
            dispatcher.close()
    return 0
