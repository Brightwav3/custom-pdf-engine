"""Render pages by invoking a locally installed Poppler ``pdftoppm``."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from pdfengine.errors import RenderError, RendererUnavailableError

from .base import MAX_DPI, PNG_SIGNATURE, RendererCapability


DEFAULT_EXECUTABLE = "pdftoppm"


class PopplerRenderer:
    """A timeout-bounded ``pdftoppm`` adapter.

    Only the configured executable is ever invoked, and it is always given
    an explicit argument list, so no page value can reach a shell.
    """

    version = "poppler-1"

    def __init__(
        self,
        executable: str | Path = DEFAULT_EXECUTABLE,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._executable = str(executable)
        self._timeout_seconds = timeout_seconds

    @property
    def executable(self) -> str:
        return self._executable

    def _resolve_executable(self) -> str | None:
        candidate = Path(self._executable)
        if candidate.is_file():
            return str(candidate)
        return shutil.which(self._executable)

    def capability(self) -> RendererCapability:
        if self._resolve_executable() is None:
            return RendererCapability(
                "blocked", f"Poppler executable not found: {self._executable}"
            )
        return RendererCapability("ready")

    def render(
        self,
        source: Path,
        page_index: int,
        width: int,
        password: str | None,
        output_dir: Path,
    ) -> bytes:
        if page_index < 0:
            raise RenderError("page index must not be negative")
        if width <= 0:
            raise RenderError("render width must be positive")

        executable = self._require_executable()

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        prefix = output_dir / f"page-{page_index}-{width}"
        page_number = str(page_index + 1)

        command = [executable, "-png", "-singlefile"]
        if password is not None:
            command += ["-upw", password]
        command += [
            "-scale-to-x",
            str(width),
            "-scale-to-y",
            "-1",
            str(Path(source)),
            str(prefix),
            "-f",
            page_number,
            "-l",
            page_number,
        ]

        self._run(command)

        image_path = prefix.with_suffix(".png")
        if not image_path.is_file():
            raise RenderError("renderer produced no image file")
        return self._consume(image_path)

    def render_at_dpi(
        self,
        source: Path,
        page_index: int,
        dpi: int,
        password: str | None,
        output_dir: Path,
    ) -> bytes:
        """Render one page at an exact DPI. Grayscale PNG."""

        if page_index < 0:
            raise RenderError("page index must not be negative")
        if dpi <= 0:
            raise RenderError("render dpi must be positive")
        if dpi > MAX_DPI:
            raise RenderError(f"render dpi must not exceed {MAX_DPI}")

        executable = self._require_executable()

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        prefix = output_dir / f"dpi-{page_index}-{dpi}"
        page_number = str(page_index + 1)

        command = [executable, "-png", "-singlefile", "-gray"]
        if password is not None:
            command += ["-upw", password]
        command += [
            "-r",
            str(dpi),
            str(Path(source)),
            str(prefix),
            "-f",
            page_number,
            "-l",
            page_number,
        ]

        self._run(command)

        image_path = prefix.with_suffix(".png")
        if not image_path.is_file():
            raise RenderError("renderer produced no image file")
        return self._consume(image_path)

    def render_range(
        self,
        source: Path,
        first_index: int,
        last_index: int,
        width: int,
        password: str | None,
        output_dir: Path,
    ) -> list[bytes]:
        """Render an inclusive zero-based page range in one process call."""

        if first_index < 0 or last_index < 0:
            raise RenderError("page index must not be negative")
        if first_index > last_index:
            raise RenderError("first page index must not exceed the last")
        if width <= 0:
            raise RenderError("render width must be positive")

        executable = self._require_executable()

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        prefix = output_dir / f"range-{first_index}-{last_index}-{width}"

        command = [executable, "-png"]
        if password is not None:
            command += ["-upw", password]
        command += [
            "-scale-to-x",
            str(width),
            "-scale-to-y",
            "-1",
            str(Path(source)),
            str(prefix),
            "-f",
            str(first_index + 1),
            "-l",
            str(last_index + 1),
        ]

        self._run(command)

        # Without -singlefile pdftoppm appends "-<page number>" to the prefix,
        # zero-padded to the digit count of the document's highest page number.
        # That width is not knowable here, so the files are discovered and then
        # ordered by the parsed number rather than by name.
        expected = last_index - first_index + 1
        try:
            produced = sorted(
                prefix.parent.glob(f"{prefix.name}-*.png"),
                key=lambda path: int(path.stem[len(prefix.name) + 1 :]),
            )
        except ValueError as exc:
            raise RenderError("renderer produced an unrecognised file name") from exc

        if len(produced) != expected:
            for path in produced:
                path.unlink(missing_ok=True)
            raise RenderError(
                f"renderer produced {len(produced)} images for {expected} requested pages"
            )

        try:
            return [self._consume(path) for path in produced]
        finally:
            # A rejected image must not strand the rest of the batch on disk.
            for path in produced:
                path.unlink(missing_ok=True)

    def _require_executable(self) -> str:
        executable = self._resolve_executable()
        if executable is None:
            raise RendererUnavailableError(
                f"Poppler executable not found: {self._executable}"
            )
        return executable

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[bytes]:
        """Invoke ``pdftoppm`` once, mapping every failure onto a typed error."""

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=self._timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RendererUnavailableError(
                f"Poppler executable not found: {self._executable}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RenderError(
                f"renderer timed out after {self._timeout_seconds:g} seconds"
            ) from exc

        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace").strip()
            raise RenderError(f"renderer failed: {detail or 'no diagnostics'}")
        return completed

    def _consume(self, image_path: Path) -> bytes:
        """Read an intermediate PNG, delete it, and validate its signature."""

        data = image_path.read_bytes()
        image_path.unlink(missing_ok=True)
        if not data.startswith(PNG_SIGNATURE):
            raise RenderError("renderer output is not a PNG image")
        return data
