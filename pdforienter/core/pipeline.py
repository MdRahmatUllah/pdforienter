"""
run_pipeline — public entry point for PDFOrienter.

Accepts one or more PDF paths, processes them (potentially in parallel
across files), aggregates results, and returns a RunResult.

Responsibility: top-level orchestration only; no PDF logic here.
"""

from __future__ import annotations

import time
from pathlib import Path

from pdforienter.config import MAX_WORKERS
from pdforienter.core.processor import process_file
from pdforienter.models import FileResult, RunResult
from pdforienter.utils.fs import ensure_dir
from pdforienter.utils.resources import peak_ram_mb


def run_pipeline(
    pdf_paths: list[str],
    output_dir: str,
) -> RunResult:
    """
    Process one or more PDF files and return a *RunResult*.

    Parameters
    ----------
    pdf_paths:
        List of absolute or relative paths to PDF files.
    output_dir:
        Directory where corrected PDFs and the log file will be written.
    """
    ensure_dir(output_dir)
    run_start = time.perf_counter()

    file_results: list[FileResult] = [
        process_file(str(Path(p).resolve()), output_dir)
        for p in pdf_paths
    ]

    return RunResult(
        total_files=len(file_results),
        total_pages=sum(r.total_pages for r in file_results),
        total_pages_changed=sum(r.pages_changed for r in file_results),
        total_text_pages=sum(r.text_pages for r in file_results),
        total_scanned_pages=sum(r.scanned_pages for r in file_results),
        total_skipped_pages=sum(r.skipped_pages for r in file_results),
        workers_used=MAX_WORKERS,
        peak_ram_mb=peak_ram_mb(),
        total_duration_seconds=time.perf_counter() - run_start,
        file_results=file_results,
    )
