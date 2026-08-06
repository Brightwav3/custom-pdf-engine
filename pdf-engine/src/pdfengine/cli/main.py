"""The ``pdfengine`` console entry point."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from pdfengine import __version__
from pdfengine.api.contracts import SCHEMA_NAMES, CommandDispatcher, schema_bytes
from pdfengine.errors import PdfEngineError

from .agent import run_agent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdfengine", description="Local-first PDF engine."
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    agent = commands.add_parser(
        "agent", help="read one JSON request per stdin line, write one response per line"
    )
    agent.add_argument("--cache-root", default=None, help="directory for render caches")

    schema = commands.add_parser("schema", help="print a JSON Schema document")
    schema.add_argument("name", choices=SCHEMA_NAMES)

    serve = commands.add_parser("serve", help="run the loopback HTTP service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8757)
    serve.add_argument("--cache-root", default=None)
    serve.add_argument(
        "--allow-network",
        action="store_true",
        help="permit binding to an address other than loopback",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)

    if arguments.command == "schema":
        sys.stdout.buffer.write(schema_bytes(arguments.name))
        sys.stdout.buffer.flush()
        return 0

    if arguments.command == "agent":
        dispatcher = CommandDispatcher(cache_root=arguments.cache_root)
        try:
            return run_agent(sys.stdin, sys.stdout, dispatcher)
        finally:
            dispatcher.close()

    if arguments.command == "serve":
        from pdfengine.service.http import serve

        try:
            serve(
                host=arguments.host,
                port=arguments.port,
                cache_root=arguments.cache_root,
                allow_network=arguments.allow_network,
            )
        except (ValueError, PdfEngineError) as exc:
            print(f"pdfengine: {exc}", file=sys.stderr)
            return 2
        return 0

    parser.error(f"unhandled command: {arguments.command}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
