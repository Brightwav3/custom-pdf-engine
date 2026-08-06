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

__all__ = [
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
