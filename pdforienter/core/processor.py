"""
Orchestrate the two-phase pipeline for a single PDF file.

Phase 1 — Parallel detection  : all pages analysed concurrently.
Phase 2 — Single-pass correction: all rotations applied in one write.

Responsibility: coordinate analyzer + corrector for one file.
"""

from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import fitz  # PyMuPDF

from pdforienter.config import MAX_WORKERS
from pdforienter.core.analyzer import analyse_page
from pdforienter.core.corrector import apply_rotations
from pdforienter.models import FileResult, PageResult, PageType


def process_file(input_path: str, output_dir: str) -> FileResult:
    """
    Run the full detection + correction pipeline for *input_path*.

    Parameters
    ----------
    input_path:
        Absolute path to the source PDF.
    output_dir:
        Directory where the corrected PDF will be written.
    """
    file_start = time.perf_counter()
    output_path = _build_output_path(input_path, output_dir)

    try:
        page_count = _page_count(input_path)
        page_results = _detect_all_pages(input_path, page_count)
        correction_duration = _correct_file(input_path, output_path, page_results)
    except Exception as exc:  # noqa: BLE001
        return FileResult(
            input_path=input_path,
            output_path=output_path,
            total_pages=0,
            pages_changed=0,
            text_pages=0,
            scanned_pages=0,
            skipped_pages=0,
            error=str(exc),
            total_duration_seconds=time.perf_counter() - file_start,
        )

    detection_duration = sum(r.duration_seconds for r in page_results)

    return FileResult(
        input_path=input_path,
        output_path=output_path,
        total_pages=page_count,
        pages_changed=sum(1 for r in page_results if r.changed),
        text_pages=sum(1 for r in page_results if r.page_type == PageType.TEXT),
        scanned_pages=sum(1 for r in page_results if r.page_type == PageType.SCANNED),
        skipped_pages=sum(1 for r in page_results if r.page_type == PageType.SKIPPED),
        page_results=page_results,
        detection_duration_seconds=detection_duration,
        correction_duration_seconds=correction_duration,
        total_duration_seconds=time.perf_counter() - file_start,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _page_count(pdf_path: str) -> int:
    doc = fitz.open(pdf_path)
    count = doc.page_count
    doc.close()
    return count


def _detect_all_pages(pdf_path: str, page_count: int) -> list[PageResult]:
    """Dispatch one worker per page; collect results in page order."""
    results: dict[int, PageResult] = {}

    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_map = {
            pool.submit(analyse_page, pdf_path, idx): idx
            for idx in range(page_count)
        }
        for future in as_completed(future_map):
            idx = future_map[future]
            results[idx] = future.result()

    return [results[i] for i in range(page_count)]


def _correct_file(
    input_path: str,
    output_path: str,
    page_results: list[PageResult],
) -> float:
    """Apply rotations only when at least one page needs changing."""
    if not any(r.changed for r in page_results):
        # Nothing to do — copy input to output as-is.
        import shutil
        shutil.copy2(input_path, output_path)
        return 0.0

    return apply_rotations(input_path, output_path, page_results)


def _build_output_path(input_path: str, output_dir: str) -> str:
    stem = Path(input_path).stem
    return str(Path(output_dir) / f"{stem}_corrected.pdf")
