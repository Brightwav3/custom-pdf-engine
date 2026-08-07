from __future__ import annotations

import shutil
import subprocess
import zlib
from pathlib import Path

import pytest

from pdfengine.errors import OcrError, OcrUnavailableError
from pdfengine.ocr.base import QUIET_ZONE_PX
from pdfengine.ocr.tesseract import TesseractOcr, pad_png, parse_tsv
from pdfengine.rendering.poppler import PopplerRenderer

from conftest import make_png


HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
    "left\ttop\twidth\theight\tconf\ttext"
)


def tsv(*rows: str) -> str:
    return "\n".join((HEADER, *rows)) + "\n"


class FakeTesseract:
    """Stand in for ``subprocess.run`` and record what the adapter asked for."""

    def __init__(
        self,
        stdout: str = "",
        returncode: int = 0,
        stderr: bytes = b"",
        langs: tuple[str, ...] = ("ces", "eng", "jpn"),
        version: str = "tesseract 5.4.0",
        failing_modes: tuple[str, ...] = (),
    ) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.langs = langs
        self.version = version
        self.failing_modes = failing_modes
        self.calls: list[list[str]] = []
        self.timeouts: list[float] = []

    def __call__(self, command, capture_output, timeout, check):
        command = list(command)
        self.calls.append(command)
        self.timeouts.append(timeout)

        if "--list-langs" in command:
            listing = "List of available languages (3):\n" + "\n".join(self.langs) + "\n"
            return subprocess.CompletedProcess(command, 0, listing.encode(), b"")
        if "--version" in command:
            banner = f"{self.version}\n leptonica-1.84.1\n"
            return subprocess.CompletedProcess(command, 0, banner.encode(), b"")

        oem = command[command.index("--oem") + 1]
        if ("legacy" in self.failing_modes and oem == "0") or (
            "lstm" in self.failing_modes and oem == "1"
        ):
            return subprocess.CompletedProcess(
                command,
                1,
                b"",
                b"Error: Tesseract (legacy) engine requested, but components "
                b"are not present in eng.traineddata!!",
            )

        return subprocess.CompletedProcess(
            command, self.returncode, self.stdout.encode("utf-8"), self.stderr
        )


@pytest.fixture
def executable(tmp_path: Path) -> Path:
    path = tmp_path / "tesseract"
    path.write_bytes(b"")
    return path


@pytest.fixture
def page_image(tmp_path: Path) -> Path:
    path = tmp_path / "page.png"
    path.write_bytes(make_png(120, 80))
    return path


def engine(executable: Path, **kwargs) -> TesseractOcr:
    kwargs.setdefault("tessdata_dir", None)
    return TesseractOcr(executable, **kwargs)


# ----------------------------------------------------------------- argv shape


def test_tsv_is_requested_by_config_variable_never_by_config_name(
    monkeypatch, executable, page_image
) -> None:
    """A custom --tessdata-dir has no configs/, so the bare ``tsv`` name fails."""

    fake = FakeTesseract(tsv())
    monkeypatch.setattr(subprocess, "run", fake)

    engine(executable).recognize(page_image)

    command = fake.calls[0]
    assert command[command.index("-c") + 1] == "tessedit_create_tsv=1"
    assert "tsv" not in command


def test_recognition_passes_language_mode_psm_and_dpi(
    monkeypatch, executable, page_image
) -> None:
    fake = FakeTesseract(tsv())
    monkeypatch.setattr(subprocess, "run", fake)

    engine(executable, timeout_seconds=9).recognize(
        page_image, dpi=400, language="ces", mode="lstm", psm=6
    )

    command = fake.calls[0]
    assert command[0] == str(executable)
    assert command[command.index("-l") + 1] == "ces"
    assert command[command.index("--oem") + 1] == "1"
    assert command[command.index("--psm") + 1] == "6"
    assert command[command.index("--dpi") + 1] == "400"
    assert "stdout" in command
    assert fake.timeouts == [9]


def test_legacy_mode_maps_to_oem_zero(monkeypatch, executable, page_image) -> None:
    fake = FakeTesseract(tsv())
    monkeypatch.setattr(subprocess, "run", fake)

    engine(executable).recognize(page_image, mode="legacy")

    command = fake.calls[0]
    assert command[command.index("--oem") + 1] == "0"


def test_an_unknown_mode_is_rejected(monkeypatch, executable, page_image) -> None:
    monkeypatch.setattr(subprocess, "run", FakeTesseract(tsv()))

    with pytest.raises(OcrError, match="unknown OCR mode"):
        engine(executable).recognize(page_image, mode="magic")


def test_tessdata_dir_is_passed_when_configured(
    monkeypatch, executable, page_image, tmp_path
) -> None:
    fake = FakeTesseract(tsv())
    monkeypatch.setattr(subprocess, "run", fake)
    models = tmp_path / "tessdata"
    models.mkdir()

    TesseractOcr(executable, tessdata_dir=models).recognize(page_image)

    command = fake.calls[0]
    assert command[1:3] == ["--tessdata-dir", str(models)]


def test_tessdata_dir_is_omitted_when_not_configured(
    monkeypatch, executable, page_image, tmp_path
) -> None:
    fake = FakeTesseract(tsv())
    monkeypatch.setattr(subprocess, "run", fake)
    # An empty home has no ~/tessdata, so nothing should be passed.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    TesseractOcr(executable).recognize(page_image)

    assert "--tessdata-dir" not in fake.calls[0]


def test_a_home_tessdata_directory_with_models_is_discovered(
    monkeypatch, executable, page_image, tmp_path
) -> None:
    home = tmp_path / "home"
    (home / "tessdata").mkdir(parents=True)
    (home / "tessdata" / "eng.traineddata").write_bytes(b"x")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    fake = FakeTesseract(tsv())
    monkeypatch.setattr(subprocess, "run", fake)

    TesseractOcr(executable).recognize(page_image)

    assert fake.calls[0][1:3] == ["--tessdata-dir", str(home / "tessdata")]


# ---------------------------------------------------------------- TSV parsing


def test_only_word_rows_survive_parsing() -> None:
    words = parse_tsv(
        tsv(
            "1\t1\t0\t0\t0\t0\t0\t0\t348\t73\t-1\t",
            "2\t1\t1\t0\t0\t0\t5\t12\t339\t59\t-1\t",
            "4\t1\t1\t1\t1\t0\t5\t12\t339\t59\t-1\t",
            "5\t1\t1\t1\t1\t1\t10\t20\t30\t8\t96.5\tone",
            "5\t1\t1\t1\t1\t2\t50\t20\t40\t8\t91\tpage",
        )
    )

    assert [word.text for word in words] == ["one", "page"]
    assert words[0].box == (10.0, 20.0, 40.0, 28.0)
    assert words[0].confidence == 96.5


def test_whitespace_only_words_are_discarded() -> None:
    words = parse_tsv(
        tsv(
            "5\t1\t1\t1\t1\t1\t10\t20\t30\t8\t95\t   ",
            "5\t1\t1\t1\t1\t2\t50\t20\t40\t8\t95\tkept",
        )
    )

    assert [word.text for word in words] == ["kept"]


def test_block_and_line_numbers_are_carried_onto_each_word() -> None:
    words = parse_tsv(
        tsv(
            "5\t1\t2\t1\t3\t1\t10\t20\t30\t8\t95\talpha",
            "5\t1\t7\t1\t4\t1\t10\t40\t30\t8\t95\tbeta",
        )
    )

    assert [(word.block, word.line) for word in words] == [(2, 3), (7, 4)]


def test_a_row_with_the_wrong_column_count_is_an_error() -> None:
    with pytest.raises(OcrError, match="malformed OCR output"):
        parse_tsv(tsv("5\t1\t1\t1\t1\t1\t10\t20\t30\t8\t95"))


def test_a_row_with_an_unparsable_number_is_an_error() -> None:
    with pytest.raises(OcrError, match="malformed OCR output"):
        parse_tsv(tsv("5\t1\t1\t1\t1\t1\tten\t20\t30\t8\t95\tx"))


def test_output_is_decoded_as_utf8_not_the_platform_default(
    monkeypatch, executable, page_image
) -> None:
    fake = FakeTesseract(
        tsv(
            "5\t1\t1\t1\t1\t1\t10\t20\t60\t12\t94\tpříliš",
            "5\t1\t1\t1\t2\t1\t10\t40\t60\t12\t93\t日本語",
        )
    )
    monkeypatch.setattr(subprocess, "run", fake)

    page = engine(executable).recognize(page_image)

    assert [word.text for word in page.words] == ["příliš", "日本語"]


# -------------------------------------------------------------------- padding


def test_the_reported_padding_is_the_quiet_zone_actually_added(
    monkeypatch, executable, page_image, tmp_path
) -> None:
    written: list[bytes] = []

    fake = FakeTesseract(tsv())
    original_call = fake.__call__

    def record(command, capture_output, timeout, check):
        if "--list-langs" not in command and "--version" not in command:
            # Find the image by suffix, not by position: a positional index
            # breaks silently when argv changes, and the resulting
            # FileNotFoundError is swallowed by the adapter's own handler and
            # reported as a missing executable.
            image = next(a for a in command[1:] if str(a).endswith(".png"))
            written.append(Path(image).read_bytes())
        return original_call(command, capture_output, timeout, check)

    monkeypatch.setattr(subprocess, "run", record)

    page = engine(executable).recognize(page_image, dpi=300)

    assert page.padding == QUIET_ZONE_PX
    assert page.width == 120 and page.height == 80
    # The file handed to Tesseract really carries the quiet zone.
    assert _dimensions(written[0]) == (
        120 + 2 * QUIET_ZONE_PX,
        80 + 2 * QUIET_ZONE_PX,
    )


def test_padding_grows_the_image_and_leaves_a_white_border() -> None:
    original = make_png(6, 4)

    padded = pad_png(original, QUIET_ZONE_PX)

    width, height, rows = _decode(padded)
    assert (width, height) == (6 + 2 * QUIET_ZONE_PX, 4 + 2 * QUIET_ZONE_PX)
    assert set(rows[0]) == {0xFF}
    assert set(rows[-1]) == {0xFF}
    assert set(rows[QUIET_ZONE_PX][:QUIET_ZONE_PX]) == {0xFF}


def test_padding_preserves_the_original_pixels_in_the_middle() -> None:
    dark = _grayscale_png([bytes([0, 64, 128, 255]), bytes([255, 128, 64, 0])])

    padded = pad_png(dark, 3)

    _width, _height, rows = _decode(padded)
    middle = [row[3:-3] for row in rows[3:-3]]
    assert middle == [bytes([0, 64, 128, 255]), bytes([255, 128, 64, 0])]


def test_padding_accepts_the_rgb_png_poppler_actually_produces() -> None:
    """`pdftoppm -gray` renders in grayscale but still writes colour type 2."""

    from pdfengine.ocr.tesseract import _decode_png

    red, green = bytes([255, 0, 0]), bytes([0, 255, 0])
    rgb = _png_with_header(
        bytes([8, 2, 0, 0, 0]),
        b"\x00" + red + green + b"\x00" + green + red,
        width=2,
        height=2,
    )

    padded = pad_png(rgb, 3)

    width, height, rows, colour = _decode_png(padded)
    assert (width, height, colour) == (8, 8, 2)
    # White border across all three channels, not merely the first byte.
    assert set(rows[0]) == {0xFF}
    assert rows[3][9:15] == red + green


def test_padding_a_paletted_png_is_refused() -> None:
    """Padding a palette image would mean resolving PLTE to find white."""

    paletted = _png_with_header(bytes([8, 3, 0, 0, 0]), b"\x00\x01\x01")

    with pytest.raises(OcrError, match="colour type"):
        pad_png(paletted, 4)


def test_padding_a_non_png_is_refused() -> None:
    with pytest.raises(OcrError, match="not a PNG"):
        pad_png(b"GIF89a", 4)


# ----------------------------------------------------------------- capability


def test_capability_is_blocked_when_the_binary_is_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(Path, "is_file", lambda self: False)

    capability = TesseractOcr(tmp_path / "nowhere").capability()

    assert capability.state == "blocked"
    assert "not found" in capability.detail


def test_capability_is_blocked_for_a_language_that_is_not_installed(
    monkeypatch, executable
) -> None:
    monkeypatch.setattr(subprocess, "run", FakeTesseract(tsv(), langs=("eng",)))

    capability = engine(executable).capability(language="ces")

    assert capability.state == "blocked"
    assert "'ces'" in capability.detail
    assert capability.languages == ("eng",)


def test_capability_is_ready_and_reports_the_engine_and_working_modes(
    monkeypatch, executable
) -> None:
    monkeypatch.setattr(subprocess, "run", FakeTesseract(tsv()))

    capability = engine(executable).capability()

    assert capability.state == "ready"
    assert capability.engine == "tesseract 5.4.0"
    assert capability.modes == ("lstm", "legacy")
    assert capability.languages == ("ces", "eng", "jpn")


def test_legacy_being_unavailable_is_reported_and_names_lstm(
    monkeypatch, executable
) -> None:
    monkeypatch.setattr(
        subprocess, "run", FakeTesseract(tsv(), failing_modes=("legacy",))
    )

    capability = engine(executable).capability(mode="legacy")

    assert capability.state == "blocked"
    assert capability.modes == ("lstm",)
    assert "legacy mode is unavailable" in capability.detail
    assert "Use lstm instead" in capability.detail


def test_probe_results_are_cached_across_capability_calls(
    monkeypatch, executable
) -> None:
    fake = FakeTesseract(tsv())
    monkeypatch.setattr(subprocess, "run", fake)
    ocr = engine(executable)

    ocr.capability()
    probes = [c for c in fake.calls if "--oem" in c]
    ocr.capability()

    assert [c for c in fake.calls if "--oem" in c] == probes
    assert len(probes) == 2  # one per mode, once


def test_languages_skip_the_header_line_and_are_cached(monkeypatch, executable) -> None:
    fake = FakeTesseract(tsv())
    monkeypatch.setattr(subprocess, "run", fake)
    ocr = engine(executable)

    assert ocr.languages() == ("ces", "eng", "jpn")
    ocr.languages()
    assert len([c for c in fake.calls if "--list-langs" in c]) == 1


def test_languages_return_empty_rather_than_raising_when_the_binary_is_missing(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(Path, "is_file", lambda self: False)

    assert TesseractOcr(tmp_path / "nowhere").languages() == ()


# --------------------------------------------------------------- failure maps


def test_a_missing_binary_raises_a_typed_unavailable_error(
    monkeypatch, tmp_path, page_image
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)

    with pytest.raises(OcrUnavailableError, match="not found"):
        TesseractOcr(tmp_path / "nowhere").recognize(page_image)


def test_a_timeout_raises_a_typed_error(monkeypatch, executable, page_image) -> None:
    def explode(command, capture_output, timeout, check):
        raise subprocess.TimeoutExpired(command, timeout)

    monkeypatch.setattr(subprocess, "run", explode)

    with pytest.raises(OcrError, match="timed out"):
        engine(executable, timeout_seconds=3).recognize(page_image)


def test_a_non_zero_exit_raises_a_typed_error_carrying_the_diagnostic(
    monkeypatch, executable, page_image
) -> None:
    monkeypatch.setattr(
        subprocess, "run", FakeTesseract("", returncode=1, stderr=b"Error: boom")
    )

    with pytest.raises(OcrError, match="Error: boom"):
        engine(executable).recognize(page_image)


def test_unparsable_output_raises_a_typed_error(
    monkeypatch, executable, page_image
) -> None:
    monkeypatch.setattr(subprocess, "run", FakeTesseract("not\ttsv\n"))

    with pytest.raises(OcrError, match="malformed OCR output"):
        engine(executable).recognize(page_image)


# ---------------------------------------------------------------- end to end

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

real_ocr = TesseractOcr()
has_tesseract = real_ocr._resolve_executable() is not None
has_poppler = PopplerRenderer().capability().ready

requires_tesseract = pytest.mark.skipif(
    not has_tesseract, reason="Tesseract is not installed"
)
requires_both = pytest.mark.skipif(
    not (has_tesseract and has_poppler),
    reason="Tesseract and Poppler are both required",
)


@requires_tesseract
def test_real_tesseract_reports_its_engine_and_languages() -> None:
    capability = TesseractOcr().capability()

    assert capability.state == "ready"
    assert capability.engine.startswith("tesseract")
    assert "eng" in capability.languages
    assert "lstm" in capability.modes


@requires_both
def test_real_tesseract_reads_a_rendered_pdf_page(tmp_path) -> None:
    image = tmp_path / "one-page.png"
    image.write_bytes(
        PopplerRenderer().render_at_dpi(
            FIXTURES / "basic" / "one-page.pdf", 0, 300, None, tmp_path
        )
    )

    page = TesseractOcr().recognize(image, dpi=300)

    assert "one page" in page.text
    assert page.padding == QUIET_ZONE_PX
    assert all(word.confidence > 0 for word in page.words)


# ----------------------------------------------------------------- PNG helpers


def _dimensions(data: bytes) -> tuple[int, int]:
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        len(payload).to_bytes(4, "big")
        + kind
        + payload
        + (zlib.crc32(kind + payload) & 0xFFFFFFFF).to_bytes(4, "big")
    )


def _png_with_header(tail: bytes, raw: bytes, width: int = 1, height: int = 1) -> bytes:
    header = width.to_bytes(4, "big") + height.to_bytes(4, "big") + tail
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )


def _grayscale_png(rows: list[bytes]) -> bytes:
    raw = b"".join(b"\x00" + row for row in rows)
    return _png_with_header(
        bytes([8, 0, 0, 0, 0]), raw, width=len(rows[0]), height=len(rows)
    )


def _decode(data: bytes) -> tuple[int, int, list[bytes]]:
    from pdfengine.ocr.tesseract import _decode_png

    width, height, rows, _colour = _decode_png(data)
    return width, height, rows
