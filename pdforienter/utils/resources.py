"""
System resource measurement helpers.

Responsibility: CPU and RAM telemetry only.
"""

from __future__ import annotations

import os

import psutil


def current_ram_mb() -> float:
    """
    Return the current process RSS memory usage in megabytes.

    Note: this is *current* RSS at call time, not peak. PyMuPDF and Tesseract
    may have allocated and freed much larger pixmaps earlier in the run that
    won't be reflected here. Use with that caveat — it is meant as a rough
    sanity check, not a true high-water mark.
    """
    process = psutil.Process(os.getpid())
    return float(process.memory_info().rss) / (1024 ** 2)


def cpu_count() -> int:
    """Return the number of logical CPUs available to the current process."""
    return os.cpu_count() or 1
