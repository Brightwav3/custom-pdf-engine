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


MAX_DPI = 1200


@runtime_checkable
class DpiRenderer(Protocol):
    """Render a page at an exact resolution rather than a target pixel width.

    Kept separate from :class:`PageRenderer` on purpose. Widening the base
    protocol would silently invalidate every existing renderer stub, since a
    ``Protocol`` is satisfied structurally and these methods are not optional.
    Backends that can do resolution-exact work advertise it by satisfying this
    protocol as well.
    """

    def render_at_dpi(
        self,
        source: Path,
        page_index: int,
        dpi: int,
        password: str | None,
        output_dir: Path,
    ) -> bytes:
        """Return grayscale PNG bytes for ``page_index`` rendered at ``dpi``."""


@runtime_checkable
class RangeRenderer(Protocol):
    """Render a contiguous run of pages in a single backend invocation."""

    def render_range(
        self,
        source: Path,
        first_index: int,
        last_index: int,
        width: int,
        password: str | None,
        output_dir: Path,
    ) -> list[bytes]:
        """Return PNG bytes for an inclusive zero-based page range, in order."""


def png_dimensions(data: bytes) -> tuple[int, int]:
    """Read pixel width and height out of a PNG IHDR chunk."""

    if not data.startswith(PNG_SIGNATURE) or len(data) < 24:
        raise ValueError("not a PNG image")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height
