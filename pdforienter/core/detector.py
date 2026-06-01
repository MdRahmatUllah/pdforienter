"""
Detect the orientation of a single PDF page.

Two strategies:
  1. text_orientation  — fast; uses PyMuPDF's character direction vectors.
  2. osd_orientation   — slower; renders the page and calls Tesseract OSD.

Responsibility: return (angle_degrees, confidence) for one page.
"""

from __future__ import annotations

from typing import Any

import fitz  # PyMuPDF

from pdforienter.config import OSD_CONFIDENCE_THRESHOLD, TESSERACT_OSD_PSM

# Resolution used when rasterising a page for OSD.
#
# 300 DPI was chosen empirically against real-world scanned receipts and
# invoices — at 150 DPI Tesseract OSD reports ~1-3% confidence on the same
# content where 300 DPI reports ~12-15%. The memory cost (4x pixmap size)
# is well within budget for the 75%-of-cores worker pool: A4 grayscale at
# 300 DPI is roughly 8 MB, so 6 workers = ~50 MB of pixmap data.
_RENDER_DPI: int = 300

# Pre-rotations applied to the rasterised image for the multi-pass OSD vote.
# See `osd_orientation` docstring for the rationale.
_MULTIPASS_PRE_ROTATIONS: tuple[int, ...] = (0, 90, 180, 270)


# ---------------------------------------------------------------------------
# Text-layer strategy
# ---------------------------------------------------------------------------

def text_orientation(page: fitz.Page) -> tuple[int, float]:
    """
    Infer orientation from character direction vectors in the text layer.

    Returns (angle, confidence) where angle ∈ {0, 90, 180, 270} and
    confidence is a value in [0, 100].
    """
    return text_orientation_from_dict(page.get_text("dict"))


def text_orientation_from_dict(text_dict: dict[str, Any]) -> tuple[int, float]:
    """
    Same as `text_orientation` but operates on an already-extracted PyMuPDF
    text dict. Exposed so `analyse_page` can extract once and feed both the
    classifier and the detector instead of paying for two extractions.
    """
    angle_votes: dict[int, int] = {0: 0, 90: 0, 180: 0, 270: 0}

    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            direction = line.get("dir", (1, 0))
            angle = _direction_to_angle(direction)
            angle_votes[angle] += len(line.get("spans", []))

    total = sum(angle_votes.values())
    if total == 0:
        return 0, 0.0

    dominant = max(angle_votes, key=angle_votes.__getitem__)
    confidence = (angle_votes[dominant] / total) * 100.0
    return dominant, confidence


def _direction_to_angle(direction: tuple[float, float]) -> int:
    """Map a (cos, sin) direction vector to the nearest 90-degree angle."""
    dx, dy = direction
    if dx > 0.5:
        return 0
    if dx < -0.5:
        return 180
    if dy > 0.5:
        return 270
    return 90


# ---------------------------------------------------------------------------
# OSD (Tesseract) strategy — multi-pass
# ---------------------------------------------------------------------------

def osd_orientation(page: fitz.Page) -> tuple[int, float]:
    """
    Detect orientation by rasterising *page* and running Tesseract OSD four
    times — once on the original image and once on each of the 90/180/270
    pre-rotated variants. The pre-rotation that makes OSD report `rotate=0`
    with the highest confidence is the correction angle.

    Why multi-pass? Single-pass OSD on real-world scanned receipts/invoices
    is unreliable: Tesseract often reports the wrong rotation with very low
    confidence (1-5%) on its first look. But it is comparatively confident
    at recognising "this image is upright" when it actually IS upright. So
    we ask the same question four times — "is this orientation upright?" —
    and trust the orientation with the strongest yes. On the test corpus
    this lifts scanned-page detection from ~30% (single-pass at 150 DPI) to
    100%.

    Returns (correction_angle, confidence). Falls back to (0, 0.0) on any
    error. If no pre-rotation lifts confidence above
    `OSD_CONFIDENCE_THRESHOLD`, returns (0, best_confidence) — the page is
    left alone rather than risk a wrong correction.

    Respects the `TESSERACT_CMD` environment variable: if set, that path is
    used as the Tesseract binary location. Useful on Windows where the
    standard UB-Mannheim installer puts `tesseract.exe` in Program Files
    without adding it to PATH.
    """
    try:
        # Heavy imports kept lazy: text-only workloads never need Tesseract,
        # and pytesseract pulls in pandas at module-load time.
        import os

        import pytesseract
        from PIL import Image

        custom_cmd = os.environ.get("TESSERACT_CMD")
        if custom_cmd:
            pytesseract.pytesseract.tesseract_cmd = custom_cmd

        pix = page.get_pixmap(dpi=_RENDER_DPI, colorspace=fitz.csGRAY)
        img = Image.frombytes("L", (pix.width, pix.height), pix.samples)

        best_angle = 0
        best_conf = 0.0
        for pre_rotation in _MULTIPASS_PRE_ROTATIONS:
            # PIL's `rotate(theta)` is CCW; negate so positive `pre_rotation`
            # corresponds to a CW rotation (matching the PDF /Rotate convention).
            rotated = (
                img if pre_rotation == 0 else img.rotate(-pre_rotation, expand=True)
            )
            osd = pytesseract.image_to_osd(
                rotated,
                config=f"--psm {TESSERACT_OSD_PSM}",
                output_type=pytesseract.Output.DICT,
            )
            osd_rotate = int(osd.get("rotate", 0))
            conf = float(osd.get("orientation_conf", 0.0))
            # Only accept votes where OSD declares the pre-rotated image upright.
            if osd_rotate == 0 and conf > best_conf:
                best_conf = conf
                best_angle = pre_rotation

        if best_conf < OSD_CONFIDENCE_THRESHOLD:
            return 0, best_conf
        return best_angle, best_conf
    except Exception:  # noqa: BLE001
        return 0, 0.0
