"""
Apply collected rotation corrections to a PDF in a single write pass.

Responsibility: given a list of PageResults, produce the corrected PDF.

Two write strategies:

  - **bake=True (default)** — physically rotate each page's content so the
    output is genuinely upright with ``/Rotate=0``. This works in *every*
    viewer and downstream tool, including those that ignore the ``/Rotate``
    page attribute (image converters, some print drivers, OCR front-ends).
    Implemented with ``Page.show_pdf_page``, which re-embeds the source page
    rotated as a Form XObject — vector text is preserved (still selectable),
    but page-level annotations, links, and form fields are NOT carried over.

  - **bake=False** — metadata-only: set the ``/Rotate`` attribute and leave
    the content stream untouched. Lossless (annotations/links/form fields
    preserved) and spec-correct, but relies on the consumer honouring
    ``/Rotate``.

Both strategies are idempotent: re-running pdforienter on the output is a
no-op (baked output reads upright; metadata output already has the right
``/Rotate``).
"""

from __future__ import annotations

import time

import fitz  # PyMuPDF

from pdforienter.models import PageResult


def apply_rotations(
    input_path: str,
    output_path: str,
    page_results: list[PageResult],
    *,
    bake: bool = True,
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
        Analysis results. ``detected_angle`` is the absolute target /Rotate
        that makes each page upright (see ``analyzer`` for how the two
        detector strategies are normalised to this).
    bake:
        If True (default) physically bake rotations into page content so the
        output is upright with /Rotate=0. If False, only set the /Rotate
        attribute (lossless but viewer-dependent).

    Returns
    -------
    float
        Wall-clock seconds spent on the correction pass.
    """
    start = time.perf_counter()

    if bake:
        _write_baked(input_path, output_path, page_results)
    else:
        _write_metadata_only(input_path, output_path, page_results)

    return time.perf_counter() - start


def _write_metadata_only(
    input_path: str,
    output_path: str,
    page_results: list[PageResult],
) -> None:
    """Set /Rotate on changed pages; leave content untouched (lossless)."""
    doc = fitz.open(input_path)
    try:
        for result in page_results:
            if not result.changed:
                continue
            page = doc[result.page_number - 1]
            page.set_rotation(_normalise_rotation(result.detected_angle))
        doc.save(output_path, garbage=4, deflate=True)
    finally:
        doc.close()


def _write_baked(
    input_path: str,
    output_path: str,
    page_results: list[PageResult],
) -> None:
    """
    Physically rotate page content so the output is upright with /Rotate=0.

    For each page, the raw (un-rotated) content is re-embedded into a new
    page rotated by ``(360 - detected_angle)``. PyMuPDF's ``show_pdf_page``
    applies a *counter-clockwise* rotation, whereas /Rotate is clockwise, so
    the bake angle is the inverse of the target /Rotate. Pages whose target
    is 90 or 270 get their width/height swapped.
    """
    results_by_index = {r.page_number - 1: r for r in page_results}

    src = fitz.open(input_path)
    dst = fitz.open()
    try:
        for idx in range(src.page_count):
            result = results_by_index.get(idx)
            target = (
                _normalise_rotation(result.detected_angle)
                if result is not None
                else int(src[idx].rotation)
            )

            # Work from raw content: clear any existing /Rotate so the bake
            # angle is computed against un-rotated geometry.
            src[idx].set_rotation(0)
            raw = src[idx].rect

            if target in (90, 270):
                width, height = raw.height, raw.width
            else:
                width, height = raw.width, raw.height

            new_page = dst.new_page(width=width, height=height)
            bake_angle = (360 - target) % 360
            new_page.show_pdf_page(new_page.rect, src, idx, rotate=bake_angle)

        dst.save(output_path, garbage=4, deflate=True)
    finally:
        src.close()
        dst.close()


def _normalise_rotation(degrees: int) -> int:
    """Reduce *degrees* into the half-open range [0, 360)."""
    return degrees % 360
