"""Every error a caller can catch must be reachable from the package root.

`docs/api.md` tells Python callers that everything public lives on the
`pdfengine` package and to import from the package root. That promise is only
true if every `PdfEngineError` subclass is re-exported there. The v0.2 release
added `SessionStateError`, `OcrUnavailableError` and `OcrError` to the JSON
surfaces while leaving them behind `pdfengine.errors` for Python callers, so
this test exists to stop the Python surface drifting behind the others again.
"""

from __future__ import annotations

import pdfengine
from pdfengine import errors


def _error_classes() -> dict[str, type]:
    return {
        name: value
        for name, value in vars(errors).items()
        if isinstance(value, type) and issubclass(value, errors.PdfEngineError)
    }


def test_every_error_class_is_importable_from_the_package_root() -> None:
    missing = sorted(
        name for name in _error_classes() if not hasattr(pdfengine, name)
    )
    assert not missing, (
        f"{missing} are defined in errors.py but not re-exported from "
        f"pdfengine/__init__.py. docs/api.md promises the package root is the "
        f"whole public surface; add them to the import block and __all__."
    )


def test_every_error_class_is_listed_in_dunder_all() -> None:
    missing = sorted(
        name for name in _error_classes() if name not in pdfengine.__all__
    )
    assert not missing, f"{missing} are importable but absent from __all__."


def test_the_re_exported_class_is_the_same_object() -> None:
    for name, value in _error_classes().items():
        assert getattr(pdfengine, name) is value
