"""The renderer contract the engine owns, independent of any backend."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

CapabilityState = Literal["ready", "blocked", "error"]


@dataclass(frozen=True)
class RendererCapability:
    """Whether previews can be produced right now, and why not if they cannot."""

    state: CapabilityState
    detail: str = ""

    @property
    def ready(self) -> bool:
        return self.state == "ready"


@runtime_checkable
class PageRenderer(Protocol):
    """Turn one page of a PDF file into PNG bytes."""

    version: str

    def capability(self) -> RendererCapability:
        """Report whether this renderer can run, without raising."""

    def render(
        self,
        source: Path,
        page_index: int,
        width: int,
        password: str | None,
        output_dir: Path,
    ) -> bytes:
        """Return PNG bytes for the zero-based ``page_index`` at ``width`` px."""


def png_dimensions(data: bytes) -> tuple[int, int]:
    """Read pixel width and height out of a PNG IHDR chunk."""

    if not data.startswith(PNG_SIGNATURE) or len(data) < 24:
        raise ValueError("not a PNG image")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height
