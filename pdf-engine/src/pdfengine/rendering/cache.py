"""Content-addressed PNG cache for rendered page previews."""

from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path

from pdfengine.api.models import RenderResult

from .base import PNG_SIGNATURE, png_dimensions


class RenderCache:
    """Store rendered PNGs under one root, keyed by what produced them.

    The key mixes the source fingerprint, the page ID, the requested width,
    and the renderer version, so a changed source, a re-rendered page, a new
    width, or a renderer upgrade all miss rather than serve a stale image.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def key(self, fingerprint: str, page_id: str, width: int, renderer) -> str:
        version = getattr(renderer, "version", "unknown")
        identity = f"{fingerprint}\0{page_id}\0{width}\0{version}"
        return sha256(identity.encode("utf-8")).hexdigest()

    def get_or_render(
        self,
        fingerprint: str,
        page_id: str,
        width: int,
        renderer,
        source: Path | None = None,
        page_index: int = 0,
        password: str | None = None,
    ) -> RenderResult:
        cached = self._root / f"{self.key(fingerprint, page_id, width, renderer)}.png"
        if cached.is_file():
            data = cached.read_bytes()
            if data.startswith(PNG_SIGNATURE):
                pixel_width, pixel_height = png_dimensions(data)
                return RenderResult(
                    page_id=page_id,
                    width=pixel_width,
                    height=pixel_height,
                    image_bytes=data,
                    cache_hit=True,
                )
            # A truncated or corrupt entry is repaired by re-rendering it.
            cached.unlink(missing_ok=True)

        if source is None:
            raise ValueError("a cache miss needs the source path to render from")
        data = renderer.render(Path(source), page_index, width, password, self._root)
        self._write_atomically(cached, data)
        pixel_width, pixel_height = png_dimensions(data)
        return RenderResult(
            page_id=page_id,
            width=pixel_width,
            height=pixel_height,
            image_bytes=data,
            cache_hit=False,
        )

    def path_for(self, fingerprint: str, page_id: str, width: int, renderer) -> Path:
        return self._root / f"{self.key(fingerprint, page_id, width, renderer)}.png"

    def clear(self) -> None:
        for entry in self._root.glob("*.png"):
            entry.unlink(missing_ok=True)

    def _write_atomically(self, target: Path, data: bytes) -> None:
        if not data.startswith(PNG_SIGNATURE):
            raise ValueError("refusing to cache a non-PNG render result")
        staged = target.with_suffix(".tmp")
        with open(staged, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, target)
