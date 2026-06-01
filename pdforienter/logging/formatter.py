"""
Format a RunResult into a human-readable, structured text log.

Responsibility: serialisation only — no I/O, no business logic.
"""

from __future__ import annotations

from datetime import datetime

from pdforienter.config import LOG_DATE_FORMAT
from pdforienter.models import FileResult, RunResult


def format_run_log(result: RunResult) -> str:
    """Return the full log string for *result*."""
    sections = [
        _header(),
        _run_summary(result),
        _separator(),
        *[_file_section(fr) for fr in result.file_results],
        _footer(),
    ]
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _header() -> str:
    now = datetime.now().strftime(LOG_DATE_FORMAT)
    return f"PDFOrienter Run Log — {now}\n{'=' * 60}"


def _run_summary(r: RunResult) -> str:
    return (
        f"\n[RUN SUMMARY]\n"
        f"  Total files processed : {r.total_files}\n"
        f"  Total pages           : {r.total_pages}\n"
        f"  Pages rotated         : {r.total_pages_changed}\n"
        f"  Text pages            : {r.total_text_pages}\n"
        f"  Scanned pages (OCR)   : {r.total_scanned_pages}\n"
        f"  Skipped pages         : {r.total_skipped_pages}\n"
        f"  Workers used          : {r.workers_used}\n"
        f"  Current RAM usage     : {r.current_ram_mb:.1f} MB\n"
        f"  Total time            : {r.total_duration_seconds:.2f}s\n"
    )


def _file_section(fr: FileResult) -> str:
    lines = [
        f"[FILE] {fr.input_path}",
        f"  Output          : {fr.output_path}",
        f"  Total pages     : {fr.total_pages}",
        f"  Pages changed   : {fr.pages_changed}",
        f"  Text pages      : {fr.text_pages}",
        f"  Scanned pages   : {fr.scanned_pages}",
        f"  Skipped pages   : {fr.skipped_pages}",
        f"  Detection time  : {fr.detection_duration_seconds:.2f}s",
        f"  Correction time : {fr.correction_duration_seconds:.2f}s",
        f"  Total time      : {fr.total_duration_seconds:.2f}s",
    ]

    if fr.error:
        lines.append(f"  ERROR           : {fr.error}")
    else:
        lines.append("  [PAGE DETAILS]")
        for pr in fr.page_results:
            status = "CHANGED" if pr.changed else "OK"
            lines.append(
                f"    p{pr.page_number:>4} | {pr.page_type.value:<7} | "
                f"{status:<7} | angle={pr.detected_angle:>3}° | "
                f"conf={pr.confidence:>5.1f} | {pr.duration_seconds:.2f}s | "
                f"{pr.reason}"
            )

    lines.append("")
    return "\n".join(lines)


def _separator() -> str:
    return "-" * 60


def _footer() -> str:
    return "=" * 60 + "\nEnd of log.\n"
