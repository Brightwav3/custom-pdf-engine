from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from pdfengine.errors import RenderError, RendererUnavailableError
from pdfengine.rendering.poppler import PopplerRenderer


class FakePdftoppm:
    """Stand in for ``subprocess.run`` and record what the renderer asked for."""

    def __init__(self, image: bytes, returncode: int = 0, stderr: bytes = b"") -> None:
        self.image = image
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[list[str]] = []
        self.timeouts: list[float] = []

    def __call__(self, command, capture_output, timeout, check):
        self.calls.append(list(command))
        self.timeouts.append(timeout)
        if self.returncode == 0 and self.image:
            # ... -scale-to-y -1 <source> <output prefix> -f N -l N
            prefix = Path(command[command.index("-scale-to-y") + 3])
            prefix.with_suffix(".png").write_bytes(self.image)
        return subprocess.CompletedProcess(
            command, self.returncode, stdout=b"", stderr=self.stderr
        )


@pytest.fixture
def executable(tmp_path: Path) -> Path:
    path = tmp_path / "pdftoppm"
    path.write_bytes(b"")
    return path


def test_poppler_renderer_requests_exactly_one_one_based_page(
    monkeypatch, executable, tmp_path, png_bytes
) -> None:
    fake = FakePdftoppm(png_bytes(160, 200))
    monkeypatch.setattr(subprocess, "run", fake)

    image = PopplerRenderer(executable, timeout_seconds=4).render(
        tmp_path / "doc.pdf", 0, 160, None, tmp_path / "cache"
    )

    assert image == png_bytes(160, 200)
    assert fake.calls[0][-4:] == ["-f", "1", "-l", "1"]
    assert fake.timeouts == [4]


def test_poppler_renderer_maps_page_index_to_a_one_based_page_number(
    monkeypatch, executable, tmp_path, png_bytes
) -> None:
    fake = FakePdftoppm(png_bytes())
    monkeypatch.setattr(subprocess, "run", fake)

    PopplerRenderer(executable).render(tmp_path / "doc.pdf", 4, 160, None, tmp_path / "c")

    assert fake.calls[0][-4:] == ["-f", "5", "-l", "5"]


def test_poppler_renderer_scales_to_the_requested_width_only(
    monkeypatch, executable, tmp_path, png_bytes
) -> None:
    fake = FakePdftoppm(png_bytes())
    monkeypatch.setattr(subprocess, "run", fake)

    PopplerRenderer(executable).render(tmp_path / "doc.pdf", 0, 320, None, tmp_path / "c")

    command = fake.calls[0]
    assert command[:3] == [str(executable), "-png", "-singlefile"]
    assert command[command.index("-scale-to-x") + 1] == "320"
    assert command[command.index("-scale-to-y") + 1] == "-1"
    assert "-upw" not in command


def test_poppler_renderer_passes_a_password_only_when_one_is_given(
    monkeypatch, executable, tmp_path, png_bytes
) -> None:
    fake = FakePdftoppm(png_bytes())
    monkeypatch.setattr(subprocess, "run", fake)

    PopplerRenderer(executable).render(tmp_path / "d.pdf", 0, 90, "secret", tmp_path / "c")

    command = fake.calls[0]
    assert command[command.index("-upw") + 1] == "secret"


def test_missing_executable_is_reported_as_a_capability_not_a_crash(tmp_path) -> None:
    renderer = PopplerRenderer(tmp_path / "absent-pdftoppm")

    capability = renderer.capability()

    assert capability.state == "blocked"
    assert "not found" in capability.detail
    assert capability.ready is False
    with pytest.raises(RendererUnavailableError, match="not found"):
        renderer.render(tmp_path / "d.pdf", 0, 90, None, tmp_path / "c")


def test_present_executable_reports_a_ready_capability(executable) -> None:
    assert PopplerRenderer(executable).capability().state == "ready"


def test_renderer_timeout_becomes_a_typed_render_error(
    monkeypatch, executable, tmp_path
) -> None:
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="pdftoppm", timeout=2)

    monkeypatch.setattr(subprocess, "run", timeout)

    with pytest.raises(RenderError, match="timed out"):
        PopplerRenderer(executable, timeout_seconds=2).render(
            tmp_path / "d.pdf", 0, 90, None, tmp_path / "c"
        )


def test_renderer_stderr_is_surfaced_on_failure(monkeypatch, executable, tmp_path) -> None:
    monkeypatch.setattr(subprocess, "run", FakePdftoppm(b"", 1, b"Syntax Error: broken"))

    with pytest.raises(RenderError, match="Syntax Error: broken"):
        PopplerRenderer(executable).render(tmp_path / "d.pdf", 0, 90, None, tmp_path / "c")


def test_non_png_renderer_output_is_rejected(monkeypatch, executable, tmp_path) -> None:
    monkeypatch.setattr(subprocess, "run", FakePdftoppm(b"not-an-image"))

    with pytest.raises(RenderError, match="not a PNG"):
        PopplerRenderer(executable).render(tmp_path / "d.pdf", 0, 90, None, tmp_path / "c")


def test_absent_output_file_is_reported(monkeypatch, executable, tmp_path) -> None:
    monkeypatch.setattr(subprocess, "run", FakePdftoppm(b""))

    with pytest.raises(RenderError, match="no image file"):
        PopplerRenderer(executable).render(tmp_path / "d.pdf", 0, 90, None, tmp_path / "c")


@pytest.mark.parametrize(
    ("page_index", "width", "message"),
    [(-1, 90, "must not be negative"), (0, 0, "must be positive")],
)
def test_invalid_render_requests_are_rejected(
    executable, tmp_path, page_index: int, width: int, message: str
) -> None:
    with pytest.raises(RenderError, match=message):
        PopplerRenderer(executable).render(
            tmp_path / "d.pdf", page_index, width, None, tmp_path / "c"
        )


@pytest.mark.skipif(
    shutil.which("pdftoppm") is None, reason="Poppler pdftoppm is not installed"
)
def test_installed_poppler_renders_a_real_page(write_pdf, tmp_path) -> None:
    image = PopplerRenderer().render(write_pdf(["hello"]), 0, 120, None, tmp_path / "c")

    assert image.startswith(b"\x89PNG\r\n\x1a\n")
