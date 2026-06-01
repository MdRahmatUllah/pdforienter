"""
Per-file helpers used by the pipeline orchestrator.

The pipeline (`pdforienter.core.pipeline`) owns the single shared worker pool
that handles every page from every file in a batch. This module exposes the
file-level glue:

  - `prepare_file`    : validate input, compute output path, get page count.
                        Surface file-level errors (missing file, too big,
                        unopenable PDF) as `FileSpec.error` so the pipeline
                        can skip Phase 1 dispatch for them.
  - `build_file_result`: assemble the final `FileResult` from collected page
                        results, applying Phase 2 (`apply_rotations` or copy)
                        unless `audit=True`.

Responsibility: file-level validation + per-file FileResult assembly. The
ProcessPoolExecutor lives one layer up.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

from pdforienter.config import MAX_FILE_SIZE_MB
from pdforienter.core.corrector import apply_rotations
from pdforienter.models import FileResult, PageResult, PageType


@dataclass(frozen=True)
class FileSpec:
    """Pre-flight description of one input file used by the pipeline."""

    input_path: str
    output_path: str
    page_count: int
    error: str | None = None


def prepare_file(input_path: str, output_dir: str) -> FileSpec:
    """
    Validate *input_path* and return a `FileSpec` ready for Phase 1 dispatch.

    File-level failures (missing file, oversize, unopenable PDF) are captured
    in `FileSpec.error` rather than raised — the pipeline must still produce a
    `FileResult` for the file so it shows up in the log.
    """
    output_path = _build_output_path(input_path, output_dir)
    try:
        _check_file_size(input_path)
        page_count = _page_count(input_path)
    except Exception as exc:  # noqa: BLE001
        return FileSpec(
            input_path=input_path,
            output_path=output_path,
            page_count=0,
            error=str(exc),
        )
    return FileSpec(
        input_path=input_path,
        output_path=output_path,
        page_count=page_count,
    )


def build_file_result(
    spec: FileSpec,
    page_results: list[PageResult] | None,
    *,
    audit: bool,
    bake: bool = True,
) -> FileResult:
    """
    Assemble the final `FileResult` for *spec*.

    If `spec.error` is set or `page_results` is None, returns an error-only
    FileResult and skips Phase 2 entirely. Otherwise runs Phase 2 (write or
    copy) unless `audit=True`, in which case no output is produced but the
    detection summary is still returned.

    `bake` selects the write strategy (see `corrector.apply_rotations`):
    True physically rotates content (works everywhere); False is metadata-only.
    """
    if spec.error is not None or page_results is None:
        return FileResult(
            input_path=spec.input_path,
            output_path=spec.output_path,
            total_pages=0,
            pages_changed=0,
            text_pages=0,
            scanned_pages=0,
            skipped_pages=0,
            error=spec.error,
        )

    correction_duration = (
        0.0
        if audit
        else _correct_file(spec.input_path, spec.output_path, page_results, bake=bake)
    )
    detection_duration = sum(r.duration_seconds for r in page_results)

    return FileResult(
        input_path=spec.input_path,
        output_path=spec.output_path,
        total_pages=spec.page_count,
        pages_changed=sum(1 for r in page_results if r.changed),
        text_pages=sum(1 for r in page_results if r.page_type == PageType.TEXT),
        scanned_pages=sum(1 for r in page_results if r.page_type == PageType.SCANNED),
        skipped_pages=sum(1 for r in page_results if r.page_type == PageType.SKIPPED),
        page_results=page_results,
        detection_duration_seconds=detection_duration,
        correction_duration_seconds=correction_duration,
        total_duration_seconds=detection_duration + correction_duration,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _check_file_size(pdf_path: str) -> None:
    path = Path(pdf_path)
    if not path.is_file():
        raise FileNotFoundError(f"Not a file: {pdf_path}")
    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise ValueError(
            f"File exceeds MAX_FILE_SIZE_MB "
            f"({size_mb:.1f} MB > {MAX_FILE_SIZE_MB} MB)"
        )


def _page_count(pdf_path: str) -> int:
    doc = fitz.open(pdf_path)
    try:
        return int(doc.page_count)
    finally:
        doc.close()


def _correct_file(
    input_path: str,
    output_path: str,
    page_results: list[PageResult],
    *,
    bake: bool,
) -> float:
    """Apply rotations, or fall back to a verbatim copy when no page changed."""
    if not any(r.changed for r in page_results):
        shutil.copy2(input_path, output_path)
        return 0.0
    return apply_rotations(input_path, output_path, page_results, bake=bake)


def _build_output_path(input_path: str, output_dir: str) -> str:
    stem = Path(input_path).stem
    return str(Path(output_dir) / f"{stem}_corrected.pdf")
