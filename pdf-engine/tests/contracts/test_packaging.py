"""Every documented entry point must actually exist."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


DOC = Path(__file__).resolve().parents[3] / "docs" / "deployment.md"


def test_the_deployment_document_exists_and_covers_all_three_models() -> None:
    text = DOC.read_text(encoding="utf-8")

    for model in ("Python package", "HTTP service", "JSONL subprocess"):
        assert model in text, f"deployment.md does not describe the {model} model"


@pytest.mark.parametrize(
    "module, attribute",
    [
        ("pdfengine", "PdfEngine"),
        ("pdfengine.cli.main", "main"),
        ("pdfengine.cli.agent", "run_agent"),
        ("pdfengine.service.http", "serve"),
        ("pdfengine.api.contracts", "CommandDispatcher"),
    ],
)
def test_every_documented_entry_point_imports(module: str, attribute: str) -> None:
    assert hasattr(importlib.import_module(module), attribute)


def test_the_documented_cli_subcommands_are_the_real_ones() -> None:
    # The deployment document names `pdfengine agent` and `pdfengine serve`.
    # A doc that names a subcommand the parser does not define is worse than no
    # doc, so the parser itself is the authority here.
    from pdfengine.cli.main import build_parser

    actions = build_parser()._subparsers._group_actions  # type: ignore[union-attr]
    names = set(actions[0].choices)

    assert {"agent", "serve", "schema"} <= names


def test_the_package_version_matches_the_release() -> None:
    import tomllib

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    assert data["project"]["version"] == "0.2.0"


def test_the_distribution_is_freedf_but_the_command_is_still_pdfengine() -> None:
    # v0.2 renamed the product and the distribution to FreeDF and deliberately
    # stopped there. The console script and the import path stay `pdfengine`,
    # because moving either breaks every documented import and every caller's
    # scripts — which docs/contract-policy.md forbids without a version bump.
    # This test exists so a later agent cannot "finish" the rename by accident.
    import tomllib

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    assert data["project"]["name"] == "freedf"
    assert data["project"]["scripts"] == {"pdfengine": "pdfengine.cli.main:main"}
