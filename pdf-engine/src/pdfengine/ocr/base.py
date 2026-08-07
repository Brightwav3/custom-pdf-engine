"""The recognizer contract the engine owns, independent of any backend."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from .models import OcrPage


DEFAULT_DPI = 300
DEFAULT_PSM = 3
"""Automatic page segmentation. Measured to beat 6 and 7 on Chinese, where
both misread a character."""

QUIET_ZONE_PX = 40
"""White border added around a page before recognition.

Tesseract returns *empty output* for text touching the image border — no
warning, no partial result. This padding is what stands between an edge-to-edge
scan and a silently blank result.
"""

MODES: tuple[str, ...] = ("lstm", "legacy")
OEM_BY_MODE = {"lstm": 1, "legacy": 0}

CAPABILITY_STATES: tuple[str, ...] = ("ready", "blocked", "unavailable", "error")
"""The complete capability vocabulary, in escalating order of unhelpfulness.

``ready``        engine and document both permit the operation.
``blocked``      the engine supports it; this document or session blocks it.
``unavailable``  this installation cannot provide it right now.
``error``        the capability probe itself failed.

The distinction between ``blocked`` and ``unavailable`` is what lets a caller
tell "try a different document" from "install something", so it must not be
collapsed.
"""

CapabilityState = Literal["ready", "blocked", "unavailable", "error"]


@dataclass(frozen=True)
class OcrCapability:
    """Whether text can be recognized right now, and with what."""

    state: CapabilityState
    detail: str = ""
    engine: str = ""
    modes: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "modes", tuple(self.modes))
        object.__setattr__(self, "languages", tuple(self.languages))

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "detail": self.detail,
            "engine": self.engine,
            "modes": list(self.modes),
            "languages": list(self.languages),
        }


@runtime_checkable
class OcrEngine(Protocol):
    """Recognize text in a rasterized page image."""

    version: str

    def capability(self, language: str = "eng", mode: str = "lstm") -> OcrCapability:
        """Report whether this language and mode can actually run, without raising.

        A mode must not be advertised unless the installed language data really
        supports it: requesting legacy against LSTM-only data fails outright.
        """

    def languages(self) -> tuple[str, ...]:
        """The language codes installed, sorted."""

    def recognize(
        self,
        image: Path,
        dpi: int = DEFAULT_DPI,
        language: str = "eng",
        mode: str = "lstm",
        psm: int = DEFAULT_PSM,
    ) -> OcrPage:
        """Recognize one page image and return its words with pixel boxes."""
