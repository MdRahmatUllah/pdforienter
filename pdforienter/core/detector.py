"""
Detect the orientation of a single PDF page.

Two strategies:
  1. text_orientation  — fast; uses PyMuPDF's character direction vectors.
  2. osd_orientation   — slower; renders the page and calls Tesseract OSD.

Responsibility: return (angle_degrees, confidence) for one page.
"""

from __future__ import annotations

import fitz  # PyMuPDF

from pdforienter.config import OSD_CONFIDENCE_THRESHOLD, TESSERACT_OSD_PSM

# Resolution used when rasterising a page for OSD.
_RENDER_DPI: int = 150  # lower = faster; sufficient for orientation detection


# ---------------------------------------------------------------------------
# Text-layer strategy
# ---------------------------------------------------------------------------

def text_orientation(page: fitz.Page) -> tuple[int, float]:
    """
    Infer orientation from character direction vectors in the text layer.

    Returns (angle, confidence) where angle ∈ {0, 90, 180, 270} and
    confidence is a value in [0, 100].
    """
    blocks = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
    angle_votes: dict[int, int] = {0: 0, 90: 0, 180: 0, 270: 0}

    for block in blocks:
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
# OSD (Tesseract) strategy
# ---------------------------------------------------------------------------

def osd_orientation(page: fitz.Page) -> tuple[int, float]:
    """
    Detect orientation by rasterising *page* and running Tesseract OSD.

    Returns (angle, confidence).  Falls back to (0, 0.0) on any error.
    """
    try:
        # Heavy imports kept lazy: text-only workloads never need Tesseract,
        # and pytesseract pulls in pandas at module-load time.
        import pytesseract
        from PIL import Image

        pix = page.get_pixmap(dpi=_RENDER_DPI, colorspace=fitz.csGRAY)
        img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        osd = pytesseract.image_to_osd(
            img,
            config=f"--psm {TESSERACT_OSD_PSM}",
            output_type=pytesseract.Output.DICT,
        )
        angle = int(osd.get("rotate", 0))
        confidence = float(osd.get("orientation_conf", 0.0))
        if confidence < OSD_CONFIDENCE_THRESHOLD:
            return 0, confidence
        return angle, confidence
    except Exception:  # noqa: BLE001
        return 0, 0.0
