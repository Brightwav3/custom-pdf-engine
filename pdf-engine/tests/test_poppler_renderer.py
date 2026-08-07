from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from pdfengine.errors import RenderError, RendererUnavailableError
from pdfengine.rendering.base import png_dimensions
from pdfengine.rendering.poppler import PopplerRenderer


class FakePdftoppm:
    """Stand in for ``subprocess.run`` and record what the renderer asked for."""

    def __init__(self, image: bytes, returncode: int = 0, stderr: bytes = b"") -> None:
        self.image = image
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[list[str]] = []
        self.timeouts: list[float] = []

    def _prefix(self, command) -> Path:
        # The prefix is always the argument just before the trailing -f N -l N.
        return Path(command[-5])

    def _write(self, command) -> None:
        self._prefix(command).with_suffix(".png").write_bytes(self.image)

    def __call__(self, command, capture_output, timeout, check):
        self.calls.append(list(command))
        self.timeouts.append(timeout)
        if self.returncode == 0 and self.image:
            self._write(command)
        return subprocess.CompletedProcess(
            command, self.returncode, stdout=b"", stderr=self.stderr
        )


class FakeRangePdftoppm(FakePdftoppm):
    """A ``pdftoppm`` that emits one zero-padded file per page, as a range call does.

    ``pad`` mirrors Poppler padding filenames to the digit count of the
    document's highest page number, which is independent of the range asked for.
    """

    def __init__(self, image: bytes, pad: int = 2, produced: int | None = None) -> None:
        super().__init__(image)
        self.pad = pad
        self.produced = produced

    def _write(self, command) -> None:
        prefix = self._prefix(command)
        first = int(command[-3])
        last = int(command[-1])
        numbers = list(range(first, last + 1))
        if self.produced is not None:
            numbers = numbers[: self.produced]
        for number in numbers:
            path = prefix.with_name(f"{prefix.name}-{number:0{self.pad}d}.png")
            path.write_bytes(self.image)


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

    assert capability.state == "unavailable"
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


# --- render_at_dpi ---------------------------------------------------------


def test_render_at_dpi_asks_for_a_grayscale_page_at_the_exact_resolution(
    monkeypatch, executable, tmp_path, png_bytes
) -> None:
    fake = FakePdftoppm(png_bytes(150, 200))
    monkeypatch.setattr(subprocess, "run", fake)

    image = PopplerRenderer(executable).render_at_dpi(
        tmp_path / "doc.pdf", 4, 300, None, tmp_path / "c"
    )

    command = fake.calls[0]
    assert image == png_bytes(150, 200)
    assert command[:4] == [str(executable), "-png", "-singlefile", "-gray"]
    assert command[command.index("-r") + 1] == "300"
    assert command[-4:] == ["-f", "5", "-l", "5"]
    assert "-upw" not in command


def test_render_at_dpi_passes_a_password_only_when_one_is_given(
    monkeypatch, executable, tmp_path, png_bytes
) -> None:
    fake = FakePdftoppm(png_bytes())
    monkeypatch.setattr(subprocess, "run", fake)

    PopplerRenderer(executable).render_at_dpi(
        tmp_path / "d.pdf", 0, 150, "secret", tmp_path / "c"
    )

    assert fake.calls[0][fake.calls[0].index("-upw") + 1] == "secret"


@pytest.mark.parametrize(
    ("page_index", "dpi", "message"),
    [
        (0, 0, "must be positive"),
        (0, -72, "must be positive"),
        (0, 1201, "must not exceed 1200"),
        (-1, 300, "must not be negative"),
    ],
)
def test_invalid_dpi_requests_are_rejected(
    executable, tmp_path, page_index: int, dpi: int, message: str
) -> None:
    with pytest.raises(RenderError, match=message):
        PopplerRenderer(executable).render_at_dpi(
            tmp_path / "d.pdf", page_index, dpi, None, tmp_path / "c"
        )


def test_render_at_dpi_accepts_the_highest_supported_resolution(
    monkeypatch, executable, tmp_path, png_bytes
) -> None:
    monkeypatch.setattr(subprocess, "run", FakePdftoppm(png_bytes()))

    image = PopplerRenderer(executable).render_at_dpi(
        tmp_path / "d.pdf", 0, 1200, None, tmp_path / "c"
    )

    assert image.startswith(b"\x89PNG\r\n\x1a\n")


def test_render_at_dpi_reports_a_missing_executable(tmp_path) -> None:
    with pytest.raises(RendererUnavailableError, match="not found"):
        PopplerRenderer(tmp_path / "absent-pdftoppm").render_at_dpi(
            tmp_path / "d.pdf", 0, 300, None, tmp_path / "c"
        )


def test_render_at_dpi_timeout_becomes_a_typed_render_error(
    monkeypatch, executable, tmp_path
) -> None:
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="pdftoppm", timeout=2)

    monkeypatch.setattr(subprocess, "run", timeout)

    with pytest.raises(RenderError, match="timed out"):
        PopplerRenderer(executable, timeout_seconds=2).render_at_dpi(
            tmp_path / "d.pdf", 0, 300, None, tmp_path / "c"
        )


def test_render_at_dpi_surfaces_stderr_on_failure(
    monkeypatch, executable, tmp_path
) -> None:
    monkeypatch.setattr(subprocess, "run", FakePdftoppm(b"", 1, b"Syntax Error: broken"))

    with pytest.raises(RenderError, match="Syntax Error: broken"):
        PopplerRenderer(executable).render_at_dpi(
            tmp_path / "d.pdf", 0, 300, None, tmp_path / "c"
        )


def test_render_at_dpi_rejects_non_png_output(monkeypatch, executable, tmp_path) -> None:
    monkeypatch.setattr(subprocess, "run", FakePdftoppm(b"not-an-image"))

    with pytest.raises(RenderError, match="not a PNG"):
        PopplerRenderer(executable).render_at_dpi(
            tmp_path / "d.pdf", 0, 300, None, tmp_path / "c"
        )


def test_render_at_dpi_reports_an_absent_output_file(
    monkeypatch, executable, tmp_path
) -> None:
    monkeypatch.setattr(subprocess, "run", FakePdftoppm(b""))

    with pytest.raises(RenderError, match="no image file"):
        PopplerRenderer(executable).render_at_dpi(
            tmp_path / "d.pdf", 0, 300, None, tmp_path / "c"
        )


# --- render_range ----------------------------------------------------------


def test_render_range_uses_exactly_one_subprocess_call_for_many_pages(
    monkeypatch, executable, tmp_path, png_bytes
) -> None:
    fake = FakeRangePdftoppm(png_bytes())
    monkeypatch.setattr(subprocess, "run", fake)

    images = PopplerRenderer(executable).render_range(
        tmp_path / "d.pdf", 0, 9, 120, None, tmp_path / "c"
    )

    assert len(fake.calls) == 1
    assert len(images) == 10
    command = fake.calls[0]
    assert "-singlefile" not in command
    assert command[-4:] == ["-f", "1", "-l", "10"]


def test_render_range_orders_zero_padded_files_numerically(
    monkeypatch, executable, tmp_path, png_bytes
) -> None:
    # p-01.png .. p-10.png sort the same lexically and numerically, so each
    # page carries a distinct pixel width to prove the order end to end.
    class DistinctPages(FakeRangePdftoppm):
        def _write(self, command):
            prefix = self._prefix(command)
            for number in range(int(command[-3]), int(command[-1]) + 1):
                path = prefix.with_name(f"{prefix.name}-{number:0{self.pad}d}.png")
                path.write_bytes(png_bytes(number, 5))

    monkeypatch.setattr(subprocess, "run", DistinctPages(png_bytes(), pad=2))

    images = PopplerRenderer(executable).render_range(
        tmp_path / "d.pdf", 0, 10, 120, None, tmp_path / "c"
    )

    assert images == [png_bytes(number, 5) for number in range(1, 12)]


def test_render_range_leaves_no_png_files_behind(
    monkeypatch, executable, tmp_path, png_bytes
) -> None:
    monkeypatch.setattr(subprocess, "run", FakeRangePdftoppm(png_bytes(), pad=3))
    output_dir = tmp_path / "c"

    PopplerRenderer(executable).render_range(
        tmp_path / "d.pdf", 0, 9, 120, None, output_dir
    )

    assert list(output_dir.glob("*.png")) == []


def test_render_range_reports_a_short_read_with_both_counts(
    monkeypatch, executable, tmp_path, png_bytes
) -> None:
    monkeypatch.setattr(
        subprocess, "run", FakeRangePdftoppm(png_bytes(), pad=2, produced=7)
    )
    output_dir = tmp_path / "c"

    with pytest.raises(RenderError, match=r"produced 7 images for 10 requested pages"):
        PopplerRenderer(executable).render_range(
            tmp_path / "d.pdf", 0, 9, 120, None, output_dir
        )

    assert list(output_dir.glob("*.png")) == []


def test_render_range_rejects_non_png_output(
    monkeypatch, executable, tmp_path
) -> None:
    monkeypatch.setattr(subprocess, "run", FakeRangePdftoppm(b"not-an-image"))

    with pytest.raises(RenderError, match="not a PNG"):
        PopplerRenderer(executable).render_range(
            tmp_path / "d.pdf", 0, 2, 120, None, tmp_path / "c"
        )


@pytest.mark.parametrize(
    ("first_index", "last_index", "width", "message"),
    [
        (-1, 2, 120, "must not be negative"),
        (0, -2, 120, "must not be negative"),
        (3, 1, 120, "must not exceed the last"),
        (0, 2, 0, "must be positive"),
    ],
)
def test_invalid_range_requests_are_rejected(
    executable, tmp_path, first_index: int, last_index: int, width: int, message: str
) -> None:
    with pytest.raises(RenderError, match=message):
        PopplerRenderer(executable).render_range(
            tmp_path / "d.pdf", first_index, last_index, width, None, tmp_path / "c"
        )


def test_render_range_reports_a_missing_executable(tmp_path) -> None:
    with pytest.raises(RendererUnavailableError, match="not found"):
        PopplerRenderer(tmp_path / "absent-pdftoppm").render_range(
            tmp_path / "d.pdf", 0, 2, 120, None, tmp_path / "c"
        )


# --- real Poppler ----------------------------------------------------------


@pytest.mark.skipif(
    shutil.which("pdftoppm") is None, reason="Poppler pdftoppm is not installed"
)
def test_installed_poppler_renders_a_real_page_at_an_exact_dpi(
    write_pdf, tmp_path
) -> None:
    source = write_pdf(["hello"], media_box=(0, 0, 144, 72))
    output_dir = tmp_path / "c"

    image = PopplerRenderer().render_at_dpi(source, 0, 150, None, output_dir)

    assert png_dimensions(image) == (300, 150)
    assert list(output_dir.glob("*.png")) == []


@pytest.mark.skipif(
    shutil.which("pdftoppm") is None, reason="Poppler pdftoppm is not installed"
)
def test_installed_poppler_renders_a_real_range_in_page_order(
    write_pdf, tmp_path
) -> None:
    source = write_pdf([f"page {index}" for index in range(1, 13)])
    output_dir = tmp_path / "c"

    images = PopplerRenderer().render_range(source, 2, 11, 100, None, output_dir)

    assert len(images) == 10
    assert all(image.startswith(b"\x89PNG\r\n\x1a\n") for image in images)
    assert list(output_dir.glob("*.png")) == []
