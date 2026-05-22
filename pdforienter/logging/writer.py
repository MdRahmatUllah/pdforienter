"""
Write formatted log content to disk.

Responsibility: file I/O for log output only.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pdforienter.models import RunResult
from pdforienter.logging.formatter import format_run_log


def write_log(result: RunResult, output_dir: str) -> str:
    """
    Serialise *result* and write the log to *output_dir*.

    Returns the absolute path of the written log file.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = Path(output_dir) / f"pdforienter_{timestamp}.log"
    log_path.write_text(format_run_log(result), encoding="utf-8")
    return str(log_path)
