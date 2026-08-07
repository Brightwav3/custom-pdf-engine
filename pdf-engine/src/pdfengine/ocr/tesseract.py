"""Recognize page text by invoking a locally installed Tesseract binary.

The safety envelope matches :class:`~pdfengine.rendering.poppler.PopplerRenderer`:
only a validated executable is ever invoked, always with an explicit argument
list, always under a timeout, and every failure is mapped onto a typed error.

Three details here are measured facts about real installations rather than
preferences:

* Output is requested with ``-c tessedit_create_tsv=1`` and never with the bare
  ``tsv`` config name, because a custom ``--tessdata-dir`` has no ``configs/``
  directory and the config form then fails outright.
* Every page is padded with a white quiet zone before recognition. Text touching
  the image border yields *empty* output with no diagnostic at all.
* Whether ``--oem 0`` works cannot be inferred from the version or the
  traineddata size, so it is probed against a real image and cached.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import zlib
from pathlib import Path

from pdfengine.errors import OcrError, OcrUnavailableError

from .base import (
    DEFAULT_DPI,
    DEFAULT_PSM,
    MODES,
    OEM_BY_MODE,
    QUIET_ZONE_PX,
    OcrCapability,
)
from .models import OcrPage, OcrWord


DEFAULT_EXECUTABLE = "tesseract"

WINDOWS_CANDIDATES: tuple[str, ...] = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

TSV_COLUMNS = 12
WORD_LEVEL = 5


class TesseractOcr:
    """A timeout-bounded ``tesseract`` adapter producing word boxes."""

    version = "tesseract-1"

    def __init__(
        self,
        executable: str | Path | None = None,
        tessdata_dir: str | Path | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._configured = None if executable is None else str(executable)
        self._configured_tessdata = None if tessdata_dir is None else str(tessdata_dir)
        self._timeout_seconds = timeout_seconds
        self._languages: tuple[str, ...] | None = None
        self._engine: str | None = None
        self._probes: dict[tuple[str, str], str | None] = {}

    # ------------------------------------------------------------------ setup

    @property
    def executable(self) -> str:
        """The executable as configured, before discovery."""

        return self._configured or DEFAULT_EXECUTABLE

    @property
    def tessdata_dir(self) -> str | None:
        """The tessdata directory that will be passed, if any."""

        if self._configured_tessdata is not None:
            return self._configured_tessdata
        return _default_tessdata_dir()

    def _resolve_executable(self) -> str | None:
        if self._configured is not None:
            candidate = Path(self._configured)
            if candidate.is_file():
                return str(candidate)
            return shutil.which(self._configured)

        found = shutil.which(DEFAULT_EXECUTABLE)
        if found is not None:
            return found
        # Tesseract is commonly installed on Windows without being put on PATH.
        for candidate in WINDOWS_CANDIDATES:
            if Path(candidate).is_file():
                return candidate
        return None

    def _require_executable(self) -> str:
        executable = self._resolve_executable()
        if executable is None:
            raise OcrUnavailableError(
                f"Tesseract executable not found: {self.executable}"
            )
        return executable

    def _base_command(self) -> list[str]:
        command = [self._require_executable()]
        directory = self.tessdata_dir
        if directory is not None:
            command += ["--tessdata-dir", directory]
        return command

    # ------------------------------------------------------------- reporting

    def languages(self) -> tuple[str, ...]:
        """The installed language codes, sorted. ``()`` when they cannot be read."""

        if self._languages is not None:
            return self._languages

        try:
            completed = self._run(self._base_command() + ["--list-langs"])
        except (OcrError, OcrUnavailableError):
            return ()

        text = completed.stdout.decode("utf-8", "replace")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        # The first line is a header: "List of available languages ...".
        codes = sorted(line for line in lines[1:] if " " not in line)
        self._languages = tuple(codes)
        return self._languages

    def engine(self) -> str:
        """The engine banner, e.g. ``"tesseract 5.4.0"``. Empty when unknown."""

        if self._engine is not None:
            return self._engine
        try:
            completed = self._run(self._base_command() + ["--version"])
        except (OcrError, OcrUnavailableError):
            return ""
        first = completed.stdout.decode("utf-8", "replace").splitlines()
        self._engine = first[0].strip() if first else ""
        return self._engine

    def capability(self, language: str = "eng", mode: str = "lstm") -> OcrCapability:
        """Report whether this language and mode can really run. Never raises."""

        try:
            return self._capability(language, mode)
        except Exception as exc:  # pragma: no cover - defensive, never expected
            return OcrCapability("error", str(exc))

    def _capability(self, language: str, mode: str) -> OcrCapability:
        if self._resolve_executable() is None:
            return OcrCapability(
                "blocked", f"Tesseract executable not found: {self.executable}"
            )

        installed = self.languages()
        if not installed:
            return OcrCapability(
                "blocked", "Tesseract reported no installed language data"
            )
        if language not in installed:
            return OcrCapability(
                "blocked",
                f"language {language!r} is not installed; available: "
                + ", ".join(installed),
                engine=self.engine(),
                languages=installed,
            )

        if mode not in MODES:
            return OcrCapability(
                "blocked",
                f"unknown OCR mode {mode!r}; supported: " + ", ".join(MODES),
                engine=self.engine(),
                languages=installed,
            )

        failures = {candidate: self._probe(language, candidate) for candidate in MODES}
        working = tuple(name for name in MODES if failures[name] is None)

        if mode in working:
            return OcrCapability(
                "ready",
                engine=self.engine(),
                modes=working,
                languages=installed,
            )

        detail = f"{mode} mode is unavailable for {language}: {failures[mode]}"
        alternatives = tuple(name for name in working if name != mode)
        if alternatives:
            detail += f". Use {' or '.join(alternatives)} instead"
        return OcrCapability(
            "blocked",
            detail,
            engine=self.engine(),
            modes=working,
            languages=installed,
        )

    def _probe(self, language: str, mode: str) -> str | None:
        """Run a real recognition in ``mode``; return ``None`` when it works.

        The legacy engine's absence is only observable by asking it to run:
        the version banner and the traineddata file give no hint.
        """

        key = (language, mode)
        if key in self._probes:
            return self._probes[key]

        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "probe.png"
            image.write_bytes(_white_png(120, 60))
            command = self._base_command() + [
                str(image),
                "stdout",
                "-l",
                language,
                "--oem",
                str(OEM_BY_MODE[mode]),
                "--psm",
                str(DEFAULT_PSM),
                "-c",
                "tessedit_create_tsv=1",
            ]
            try:
                self._run(command)
            except (OcrError, OcrUnavailableError) as exc:
                self._probes[key] = str(exc)
                return self._probes[key]

        self._probes[key] = None
        return None

    # ----------------------------------------------------------- recognition

    def recognize(
        self,
        image: Path,
        dpi: int = DEFAULT_DPI,
        language: str = "eng",
        mode: str = "lstm",
        psm: int = DEFAULT_PSM,
    ) -> OcrPage:
        """Recognize one rasterized page and return its words with pixel boxes."""

        if dpi <= 0:
            raise OcrError("dpi must be positive")
        if mode not in OEM_BY_MODE:
            raise OcrError(f"unknown OCR mode {mode!r}; supported: " + ", ".join(MODES))
        if not language:
            raise OcrError("a language code is required")

        source = Path(image)
        try:
            original = source.read_bytes()
        except OSError as exc:
            raise OcrError(f"cannot read page image: {source}") from exc

        width, height = _png_dimensions(original)
        padded = pad_png(original, QUIET_ZONE_PX)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "page.png"
            target.write_bytes(padded)
            command = self._base_command() + [
                str(target),
                "stdout",
                "-l",
                language,
                "--oem",
                str(OEM_BY_MODE[mode]),
                "--psm",
                str(psm),
                "--dpi",
                str(dpi),
                "-c",
                "tessedit_create_tsv=1",
            ]
            completed = self._run(command)

        # Tesseract emits UTF-8 whatever the console codepage is. Decoding with
        # the platform default would corrupt every non-ASCII script.
        words = parse_tsv(completed.stdout.decode("utf-8"))
        return OcrPage(
            words=words,
            width=width,
            height=height,
            dpi=dpi,
            language=language,
            mode=mode,
            padding=QUIET_ZONE_PX,
        )

    # -------------------------------------------------------------- plumbing

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[bytes]:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=self._timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise OcrUnavailableError(
                f"Tesseract executable not found: {self.executable}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise OcrError(
                f"OCR timed out after {self._timeout_seconds:g} seconds"
            ) from exc

        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace").strip()
            raise OcrError(f"OCR failed: {detail or 'no diagnostics'}")
        return completed


def parse_tsv(text: str) -> tuple[OcrWord, ...]:
    """Turn Tesseract's TSV into words, discarding its structural rows."""

    words: list[OcrWord] = []
    for index, raw in enumerate(text.split("\n")):
        line = raw.rstrip("\r")
        if not line.strip():
            continue
        fields = line.split("\t")
        if index == 0 and fields[0] == "level":
            continue
        if len(fields) != TSV_COLUMNS:
            raise OcrError(
                f"malformed OCR output: expected {TSV_COLUMNS} columns, "
                f"got {len(fields)}"
            )

        try:
            level = int(fields[0])
            block = int(fields[2])
            line_number = int(fields[4])
            left = int(fields[6])
            top = int(fields[7])
            width = int(fields[8])
            height = int(fields[9])
            confidence = float(fields[10])
        except ValueError as exc:
            raise OcrError(f"malformed OCR output: {exc}") from exc

        # Levels below 5 describe pages, blocks, paragraphs and lines. They
        # carry conf = -1 and no text.
        if level != WORD_LEVEL:
            continue
        content = fields[11].strip()
        if not content:
            continue
        if width <= 0 or height <= 0:
            continue

        words.append(
            OcrWord(
                text=content,
                box=(float(left), float(top), float(left + width), float(top + height)),
                confidence=confidence,
                block=block,
                line=line_number,
            )
        )
    return tuple(words)


# --------------------------------------------------------------- PNG padding


def pad_png(data: bytes, border: int) -> bytes:
    """Return ``data`` with a white border of ``border`` pixels on every side.

    Pure Python on purpose: no imaging library is available, and the quiet zone
    is not optional — Tesseract silently returns nothing for text that touches
    the image edge.

    Any 8-bit colour type is accepted. ``pdftoppm -gray`` renders in grayscale
    but still writes an RGB PNG, so restricting this to colour type 0 would
    reject the very images it exists to pad.
    """

    if border < 0:
        raise OcrError("quiet zone must not be negative")

    width, height, rows, colour = _decode_png(data)
    if border == 0:
        return data

    samples = _SAMPLES_PER_PIXEL[colour]
    padded_width = width + 2 * border
    blank = b"\xff" * (padded_width * samples)
    side = b"\xff" * (border * samples)
    out = [blank for _ in range(border)]
    out += [side + row + side for row in rows]
    out += [blank for _ in range(border)]
    return _encode_png(padded_width, height + 2 * border, out, colour)


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if not data.startswith(PNG_SIGNATURE) or len(data) < 24:
        raise OcrError("page image is not a PNG")
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


_SAMPLES_PER_PIXEL = {0: 1, 2: 3, 4: 2, 6: 4}
"""Bytes per pixel at 8-bit depth, by PNG colour type.

0 grayscale, 2 RGB, 4 grayscale+alpha, 6 RGBA. Paletted (3) is deliberately
absent: padding it would mean resolving PLTE entries to find white.
"""


def _decode_png(data: bytes) -> tuple[int, int, list[bytes], int]:
    if not data.startswith(PNG_SIGNATURE):
        raise OcrError("page image is not a PNG")

    header: bytes | None = None
    payload = bytearray()
    offset = len(PNG_SIGNATURE)
    while offset + 8 <= len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        body = data[offset + 8 : offset + 8 + length]
        if len(body) != length:
            raise OcrError("truncated PNG chunk")
        if kind == b"IHDR":
            header = body
        elif kind == b"IDAT":
            payload.extend(body)
        elif kind == b"IEND":
            break
        offset += 12 + length

    if header is None or len(header) != 13:
        raise OcrError("PNG has no usable header")

    width = int.from_bytes(header[0:4], "big")
    height = int.from_bytes(header[4:8], "big")
    depth, colour, compression, filtering, interlace = header[8:13]
    if depth != 8 or colour not in _SAMPLES_PER_PIXEL:
        raise OcrError(
            "only 8-bit grayscale, RGB, or alpha PNG images can be padded "
            f"(got colour type {colour}, bit depth {depth})"
        )
    if compression != 0 or filtering != 0 or interlace != 0:
        raise OcrError("only uncompressed-filter, non-interlaced PNG images are supported")
    if width <= 0 or height <= 0:
        raise OcrError("PNG dimensions must be positive")

    try:
        raw = zlib.decompress(bytes(payload))
    except zlib.error as exc:
        raise OcrError("PNG image data could not be decompressed") from exc

    samples = _SAMPLES_PER_PIXEL[colour]
    stride = width * samples
    if len(raw) != height * (stride + 1):
        raise OcrError("PNG image data has the wrong length")

    rows: list[bytes] = []
    previous = bytearray(stride)
    for index in range(height):
        start = index * (stride + 1)
        filter_type = raw[start]
        row = bytearray(raw[start + 1 : start + 1 + stride])
        _unfilter(filter_type, row, previous, samples)
        rows.append(bytes(row))
        previous = row
    return width, height, rows, colour


def _unfilter(
    filter_type: int, row: bytearray, previous: bytearray, samples: int
) -> None:
    """Undo one PNG scanline filter in place.

    PNG filters reference the byte one *pixel* to the left, not one byte, so
    ``samples`` is load-bearing: using 1 here would silently corrupt any image
    that is not grayscale.
    """

    if filter_type == 0:
        return
    for index in range(len(row)):
        left = row[index - samples] if index >= samples else 0
        up = previous[index]
        if filter_type == 1:
            row[index] = (row[index] + left) & 0xFF
        elif filter_type == 2:
            row[index] = (row[index] + up) & 0xFF
        elif filter_type == 3:
            row[index] = (row[index] + ((left + up) >> 1)) & 0xFF
        elif filter_type == 4:
            upper_left = previous[index - samples] if index >= samples else 0
            row[index] = (row[index] + _paeth(left, up, upper_left)) & 0xFF
        else:
            raise OcrError(f"unsupported PNG filter type {filter_type}")


def _paeth(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    da = abs(estimate - left)
    db = abs(estimate - up)
    dc = abs(estimate - upper_left)
    if da <= db and da <= dc:
        return left
    if db <= dc:
        return up
    return upper_left


def _encode_png(width: int, height: int, rows: list[bytes], colour: int = 0) -> bytes:
    raw = b"".join(b"\x00" + row for row in rows)
    header = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + bytes([8, colour, 0, 0, 0])
    )
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        len(payload).to_bytes(4, "big")
        + kind
        + payload
        + (zlib.crc32(kind + payload) & 0xFFFFFFFF).to_bytes(4, "big")
    )


def _white_png(width: int, height: int) -> bytes:
    return _encode_png(width, height, [b"\xff" * width] * height)


def _default_tessdata_dir() -> str | None:
    """``~/tessdata`` when it holds models, else nothing — Tesseract's default."""

    candidate = Path.home() / "tessdata"
    try:
        if candidate.is_dir() and any(candidate.glob("*.traineddata")):
            return str(candidate)
    except OSError:  # pragma: no cover - unreadable home directory
        return None
    return None
