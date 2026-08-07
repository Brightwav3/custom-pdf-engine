"""Turn OCR pixel boxes into placements in PDF user space.

OCR reports boxes in **image pixels, origin top left**, measured on the padded
image the recognizer actually saw. PDF user space is **points, origin bottom
left**, on a page that may be cropped and rotated. This module is the one place
that bridges the two, and it is pure arithmetic on purpose: every rule below is
testable against hand-computed numbers without an OCR engine or a PDF in sight.

The chain, in order:

1. subtract ``OcrPage.padding`` from every coordinate — the quiet zone the
   adapter added before recognition is not part of the page;
2. scale pixels to points with ``72 / dpi``;
3. flip Y, because image Y grows downwards and user-space Y grows upwards;
4. undo the page rotation, because the rasterized image is already rotated
   while the page object stores unrotated coordinates;
5. offset by the lower-left corner of the visible box, because a ``/CropBox``
   shifts what the image shows relative to the coordinate origin.
"""

from __future__ import annotations

from dataclasses import dataclass

from pdfengine.ocr.models import OcrPage

Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class PlacedWord:
    """One recognized word, positioned in unrotated PDF user space.

    ``x`` and ``baseline`` locate the lower-left corner of the word's footprint
    on the page as the page stores it, i.e. *before* ``/Rotate`` is applied by a
    viewer. ``width`` and ``height`` stay measured along the word's own reading
    direction, so they are unaffected by rotation: a caller drawing into a
    rotated page rotates its text matrix, it does not swap advance for height.
    """

    text: str
    x: float
    baseline: float
    width: float
    height: float
    confidence: float


def place_words(
    page: OcrPage,
    media_box: Box,
    crop_box: Box | None = None,
    rotation: int = 0,
) -> tuple[PlacedWord, ...]:
    """Place every word of ``page`` onto the page described by the arguments."""

    turn = _rotation(rotation)
    visible = crop_box if crop_box is not None else media_box
    left, bottom, right, top = _normalized(visible)
    width_pt = right - left
    height_pt = top - bottom

    scale = 72.0 / page.dpi
    pad = float(page.padding)

    placed: list[PlacedWord] = []
    for word in page.words:
        x0, y0, x1, y1 = word.box
        # 1. drop the quiet zone, 2. scale to points. ``y`` still grows down.
        ix0 = (x0 - pad) * scale
        ix1 = (x1 - pad) * scale
        iy0 = (y0 - pad) * scale
        iy1 = (y1 - pad) * scale

        corners = [
            _to_user(ix, iy, turn, width_pt, height_pt)
            for ix, iy in ((ix0, iy0), (ix1, iy0), (ix0, iy1), (ix1, iy1))
        ]
        user_x = min(point[0] for point in corners)
        user_y = min(point[1] for point in corners)

        placed.append(
            PlacedWord(
                text=word.text,
                x=left + user_x,
                # The baseline sits at the bottom of the box. A true baseline
                # has descenders hanging below it, so this is a small
                # approximation, but it is the one that keeps a viewer's
                # selection rectangle flush with the scanned word underneath —
                # which is the whole point of the invisible layer.
                baseline=bottom + user_y,
                width=(x1 - x0) * scale,
                height=(y1 - y0) * scale,
                confidence=word.confidence,
            )
        )
    return tuple(placed)


def _to_user(
    ix: float, iy: float, rotation: int, width_pt: float, height_pt: float
) -> tuple[float, float]:
    """Map a point in rotated image space onto unrotated user space.

    ``ix``/``iy`` are points measured from the top-left of the rasterized
    image; the result is measured from the lower-left of the visible box. For
    90 and 270 the image is ``height_pt`` wide and ``width_pt`` tall, since the
    renderer applied ``/Rotate`` before handing the pixels to the recognizer.
    """

    if rotation == 0:
        return ix, height_pt - iy
    if rotation == 90:
        # Rotating the page 90 degrees clockwise puts its left edge along the
        # top of the image and its bottom edge along the image's left edge.
        return iy, ix
    if rotation == 180:
        return width_pt - ix, iy
    # 270: rotated counter-clockwise, the page's top edge lands on the left.
    return width_pt - iy, height_pt - ix


def _rotation(rotation: int) -> int:
    turn = int(rotation) % 360
    if turn not in (0, 90, 180, 270):
        raise ValueError(f"unsupported page rotation: {rotation}")
    return turn


def _normalized(box: Box) -> Box:
    x0, y0, x1, y1 = (float(value) for value in box)
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
