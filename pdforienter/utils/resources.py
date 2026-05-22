"""
System resource measurement helpers.

Responsibility: CPU and RAM telemetry only.
"""

from __future__ import annotations

import os

import psutil


def peak_ram_mb() -> float:
    """Return the current process RSS memory usage in megabytes."""
    process = psutil.Process(os.getpid())
    return float(process.memory_info().rss) / (1024 ** 2)


def cpu_count() -> int:
    """Return the number of logical CPUs available to the current process."""
    return os.cpu_count() or 1
