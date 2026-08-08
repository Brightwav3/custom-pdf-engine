"""The public surface may grow. It may not silently shrink."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdfengine import errors
from pdfengine.api.artifacts import ARTIFACT_KINDS
from pdfengine.api.contracts import COMMANDS, SCHEMA_NAMES
from pdfengine.api.models import OPERATION_TYPES
from pdfengine.ocr.base import CAPABILITY_STATES


MANIFEST = Path(__file__).parent / "golden" / "v1-surface.json"


def _live_surface() -> dict:
    codes = sorted(
        {
            value.code
            for value in vars(errors).values()
            if isinstance(value, type)
            and issubclass(value, errors.PdfEngineError)
        }
    )
    return {
        "apiVersion": "v1",
        "commands": sorted(COMMANDS),
        "operationKinds": sorted(operation.kind for operation in OPERATION_TYPES),
        "errorCodes": codes,
        "artifactKinds": sorted(ARTIFACT_KINDS),
        "capabilityStates": sorted(CAPABILITY_STATES),
        "schemas": sorted(SCHEMA_NAMES),
    }


@pytest.fixture
def frozen() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_no_public_name_has_disappeared(frozen) -> None:
    live = _live_surface()

    for key, names in frozen.items():
        if key == "apiVersion":
            assert live[key] == names
            continue
        missing = sorted(set(names) - set(live[key]))
        assert not missing, (
            f"{key} lost {missing}. Removing a public name is a breaking change "
            f"and requires a new apiVersion, not an edit to this manifest."
        )


def test_growth_is_recorded_rather_than_silent(frozen) -> None:
    live = _live_surface()

    added = {
        key: sorted(set(live[key]) - set(names))
        for key, names in frozen.items()
        if key != "apiVersion" and set(live[key]) - set(names)
    }

    assert not added, (
        f"The surface grew: {added}. That is allowed under the additive policy — "
        f"update tests/contracts/golden/v1-surface.json and add a "
        f"docs/CONTRACT-CHANGELOG.md entry in the same commit."
    )
