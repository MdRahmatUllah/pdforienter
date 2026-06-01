"""
run_pipeline — public entry point for PDFOrienter.

Owns the single `ProcessPoolExecutor` that handles every page from every file
in a batch. Files are validated up-front into `FileSpec`s, then ALL pages from
ALL valid files are submitted to the same pool — multi-file parallelism comes
for free. Phase 2 (the single-pass PyMuPDF write per file) runs in the main
process after Phase 1 completes.

Responsibility: dispatch, collection, and result aggregation. No PDF logic
lives here.
"""

from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from pdforienter.config import MAX_WORKERS
from pdforienter.core.analyzer import analyse_page
from pdforienter.core.processor import FileSpec, build_file_result, prepare_file
from pdforienter.models import PageResult, RunResult
from pdforienter.utils.fs import ensure_dir
from pdforienter.utils.resources import current_ram_mb


def run_pipeline(
    pdf_paths: list[str],
    output_dir: str,
    *,
    workers: int | None = None,
    audit: bool = False,
    bake: bool = True,
) -> RunResult:
    """
    Process one or more PDF files and return a `RunResult`.

    Parameters
    ----------
    pdf_paths:
        List of absolute or relative paths to PDF files.
    output_dir:
        Directory where corrected PDFs and the log file will be written.
    workers:
        Override the worker pool size. Defaults to `MAX_WORKERS` (75% of CPUs).
    audit:
        If True, run Phase 1 (detection) only. No output PDFs are written and
        no input files are copied. The returned `RunResult` still reports
        which pages would have been rotated.
    bake:
        If True (default), physically rotate page content so output is upright
        with /Rotate=0 — works in every viewer/tool. If False, only set the
        /Rotate attribute (lossless, preserves annotations, but viewer-dependent).
    """
    ensure_dir(output_dir)
    run_start = time.perf_counter()
    worker_count = workers if workers is not None else MAX_WORKERS

    specs = [prepare_file(str(Path(p).resolve()), output_dir) for p in pdf_paths]
    page_results_by_path = _detect_all(specs, worker_count)

    file_results = [
        build_file_result(
            spec, page_results_by_path.get(spec.input_path), audit=audit, bake=bake
        )
        for spec in specs
    ]

    return RunResult(
        total_files=len(file_results),
        total_pages=sum(r.total_pages for r in file_results),
        total_pages_changed=sum(r.pages_changed for r in file_results),
        total_text_pages=sum(r.text_pages for r in file_results),
        total_scanned_pages=sum(r.scanned_pages for r in file_results),
        total_skipped_pages=sum(r.skipped_pages for r in file_results),
        workers_used=worker_count,
        current_ram_mb=current_ram_mb(),
        total_duration_seconds=time.perf_counter() - run_start,
        file_results=file_results,
    )


def _detect_all(
    specs: list[FileSpec],
    worker_count: int,
) -> dict[str, list[PageResult]]:
    """
    Run Phase 1 across every valid file via one shared ProcessPoolExecutor.

    Returns a mapping from input_path → page_results-in-page-order. Files that
    had a file-level error (`spec.error is not None`) are not represented.
    """
    valid_specs = [s for s in specs if s.error is None and s.page_count > 0]
    if not valid_specs:
        return {}

    results: dict[str, list[PageResult | None]] = {
        spec.input_path: [None] * spec.page_count for spec in valid_specs
    }

    with ProcessPoolExecutor(max_workers=worker_count) as pool:
        from concurrent.futures import Future
        futures: dict[Future[PageResult], tuple[str, int]] = {}
        for spec in valid_specs:
            for page_index in range(spec.page_count):
                fut = pool.submit(analyse_page, spec.input_path, page_index)
                futures[fut] = (spec.input_path, page_index)
        for fut in as_completed(futures):
            path, page_index = futures[fut]
            results[path][page_index] = fut.result()

    # All slots are filled by the time as_completed drains — the cast is safe.
    return {path: [r for r in pages if r is not None] for path, pages in results.items()}
