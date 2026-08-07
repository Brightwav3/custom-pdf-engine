"""Optical character recognition and the searchable text layer built from it."""

from .base import (
    DEFAULT_DPI,
    DEFAULT_PSM,
    MODES,
    OEM_BY_MODE,
    QUIET_ZONE_PX,
    OcrCapability,
    OcrEngine,
)
from .models import OcrChar, OcrPage, OcrWord
from .tesseract import TesseractOcr, pad_png, parse_tsv

__all__ = [
    "TesseractOcr",
    "pad_png",
    "parse_tsv",
    "DEFAULT_DPI",
    "DEFAULT_PSM",
    "MODES",
    "OEM_BY_MODE",
    "QUIET_ZONE_PX",
    "OcrCapability",
    "OcrChar",
    "OcrEngine",
    "OcrPage",
    "OcrWord",
]
