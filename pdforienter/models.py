"""
Pure-data classes used throughout PDFOrienter.

No business logic lives here — only typed containers so every module
shares a common vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class PageType(str, Enum):
    TEXT = "text"       # selectable text layer present
    SCANNED = "scanned" # image-only — OCR/OSD required
    SKIPPED = "skipped" # could not be analysed (no text, low confidence …)


@dataclass
class PageResult:
    """Analysis + correction outcome for a single PDF page."""

    page_number: int          # 1-based
    page_type: PageType
    detected_angle: int       # angle reported by detector (degrees)
    existing_rotation: int    # rotation already stored in PDF metadata
    correction_applied: int   # net rotation written to the page (degrees)
    changed: bool
    confidence: float         # 0-100; -1 when N/A
    reason: str               # human-readable explanation
    duration_seconds: float   # wall-clock time for this page


@dataclass
class FileResult:
    """Aggregated outcome for one PDF file."""

    input_path: str
    output_path: str
    total_pages: int
    pages_changed: int
    text_pages: int
    scanned_pages: int
    skipped_pages: int
    page_results: list[PageResult] = field(default_factory=list)
    detection_duration_seconds: float = 0.0
    correction_duration_seconds: float = 0.0
    total_duration_seconds: float = 0.0
    error: str | None = None


@dataclass
class RunResult:
    """Top-level summary for an entire PDFOrienter run."""

    total_files: int
    total_pages: int
    total_pages_changed: int
    total_text_pages: int
    total_scanned_pages: int
    total_skipped_pages: int
    workers_used: int
    current_ram_mb: float
    total_duration_seconds: float
    file_results: list[FileResult] = field(default_factory=list)
