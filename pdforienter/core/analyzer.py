"""
analyse_page — the unit of work dispatched to each worker process.

Responsibility: open one page, classify it, detect orientation, return
a PageResult.  This function is intentionally self-contained so it can
be safely pickled and sent to a ProcessPoolExecutor worker.
"""

from __future__ import annotations

import time

import fitz  # PyMuPDF

from pdforienter.core.classifier import has_text_layer
from pdforienter.core.detector import osd_orientation, text_orientation
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

        if has_text_layer(page):
            page_type = PageType.TEXT
            detected_angle, confidence = text_orientation(page)
        else:
            page_type = PageType.SCANNED
            detected_angle, confidence = osd_orientation(page)

        if detected_angle == 0:
            changed = False
            correction = 0
            reason = "No rotation needed."
        else:
            changed = True
            correction = detected_angle
            reason = f"Rotation of {detected_angle}° detected (confidence {confidence:.1f})."

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
