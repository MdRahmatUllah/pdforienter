"""
Apply collected rotation corrections to a PDF in a single write pass.

Responsibility: given a list of PageResults, mutate the document's
rotation metadata for every changed page and save once.
"""

from __future__ import annotations

import time

import fitz  # PyMuPDF

from pdforienter.models import PageResult


def apply_rotations(
    input_path: str,
    output_path: str,
    page_results: list[PageResult],
) -> float:
    """
    Write *output_path* with all required rotations applied in one pass.

    Parameters
    ----------
    input_path:
        Source PDF path.
    output_path:
        Destination PDF path (will be created / overwritten).
    page_results:
        Analysis results; only pages with ``changed=True`` are modified.

    Returns
    -------
    float
        Wall-clock seconds spent on the correction pass.
    """
    start = time.perf_counter()

    doc = fitz.open(input_path)
    try:
        for result in page_results:
            if not result.changed:
                continue
            page = doc[result.page_number - 1]  # back to 0-based
            new_rotation = _normalise_rotation(
                result.existing_rotation + result.correction_applied
            )
            page.set_rotation(new_rotation)

        doc.save(output_path, garbage=4, deflate=True)
    finally:
        doc.close()

    return time.perf_counter() - start


def _normalise_rotation(degrees: int) -> int:
    """Reduce *degrees* into the half-open range [0, 360)."""
    return degrees % 360
