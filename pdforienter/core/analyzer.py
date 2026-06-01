"""
analyse_page — the unit of work dispatched to each worker process.

Responsibility: open one page, classify it, detect orientation, return
a PageResult.  This function is intentionally self-contained so it can
be safely pickled and sent to a ProcessPoolExecutor worker.
"""

from __future__ import annotations

import time

import fitz  # PyMuPDF

from pdforienter.core.classifier import has_text_layer_from_dict
from pdforienter.core.detector import osd_orientation, text_orientation_from_dict
from pdforienter.models import PageResult, PageType


def analyse_page(pdf_path: str, page_index: int) -> PageResult:
    """
    Analyse a single page and return its *PageResult*.

    Parameters
    ----------
    pdf_path:
        Absolute path to the source PDF.
    page_index:
        Zero-based page index within the document.
    """
    start = time.perf_counter()
    page_number = page_index + 1  # convert to 1-based for reporting

    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        existing_rotation = int(page.rotation)

        # Extract the text dict once and feed both classifier and detector,
        # rather than paying for two separate PyMuPDF text passes.
        text_dict = page.get_text("dict")

        # Detector return-value semantics differ between strategies:
        #   - text_orientation reads `dir` vectors from the content stream.
        #     `dir` is independent of /Rotate, so the returned angle is the
        #     ABSOLUTE target /Rotate needed to make the content read upright.
        #   - osd_orientation rasterises via `get_pixmap`, which APPLIES
        #     /Rotate by default. OSD therefore sees the already-rotated
        #     image and returns the RELATIVE additional rotation needed.
        #
        # We normalise both to "absolute target /Rotate" (`detected_angle`)
        # and derive the relative delta (`correction`) from it. Without this
        # normalisation, re-running pdforienter on its own output would
        # cascade rotations (existing 90 + detected 90 = 180, etc).
        if has_text_layer_from_dict(text_dict):
            page_type = PageType.TEXT
            raw_angle, confidence = text_orientation_from_dict(text_dict)
            detected_angle = raw_angle  # already absolute
        else:
            page_type = PageType.SCANNED
            relative_angle, confidence = osd_orientation(page)
            detected_angle = (existing_rotation + relative_angle) % 360

        correction = (detected_angle - existing_rotation) % 360

        if correction == 0:
            changed = False
            reason = (
                f"Page already at correct /Rotate ({existing_rotation}°)."
                if existing_rotation
                else "No rotation needed."
            )
        else:
            changed = True
            reason = (
                f"Rotating by {correction}° to reach /Rotate={detected_angle}° "
                f"(was {existing_rotation}°, confidence {confidence:.1f})."
            )

    except Exception as exc:  # noqa: BLE001
        return PageResult(
            page_number=page_number,
            page_type=PageType.SKIPPED,
            detected_angle=0,
            existing_rotation=0,
            correction_applied=0,
            changed=False,
            confidence=-1.0,
            reason=f"Error during analysis: {exc}",
            duration_seconds=time.perf_counter() - start,
        )
    finally:
        doc.close()

    return PageResult(
        page_number=page_number,
        page_type=page_type,
        detected_angle=detected_angle,
        existing_rotation=existing_rotation,
        correction_applied=correction,
        changed=changed,
        confidence=confidence,
        reason=reason,
        duration_seconds=time.perf_counter() - start,
    )
