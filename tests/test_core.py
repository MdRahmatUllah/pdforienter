"""
Basic smoke tests for PDFOrienter.

Run with:  pytest tests/ -v
"""

from __future__ import annotations

from pdforienter.config import MAX_WORKERS, VALID_ROTATIONS
from pdforienter.core.corrector import _normalise_rotation
from pdforienter.core.detector import _direction_to_angle
from pdforienter.logging.formatter import format_run_log
from pdforienter.models import FileResult, PageResult, PageType, RunResult


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def test_max_workers_positive() -> None:
    assert MAX_WORKERS >= 1


def test_valid_rotations_contain_cardinals() -> None:
    assert set(VALID_ROTATIONS) == {0, 90, 180, 270}


# ---------------------------------------------------------------------------
# Detector helpers
# ---------------------------------------------------------------------------

def test_direction_to_angle_right() -> None:
    assert _direction_to_angle((1.0, 0.0)) == 0


def test_direction_to_angle_left() -> None:
    assert _direction_to_angle((-1.0, 0.0)) == 180


def test_direction_to_angle_down() -> None:
    assert _direction_to_angle((0.0, 1.0)) == 270


def test_direction_to_angle_up() -> None:
    assert _direction_to_angle((0.0, -1.0)) == 90


# ---------------------------------------------------------------------------
# Corrector helpers
# ---------------------------------------------------------------------------

def test_normalise_rotation_wraps() -> None:
    assert _normalise_rotation(360) == 0
    assert _normalise_rotation(450) == 90
    assert _normalise_rotation(270) == 270


# ---------------------------------------------------------------------------
# Log formatter
# ---------------------------------------------------------------------------

def _make_run_result() -> RunResult:
    page = PageResult(
        page_number=1,
        page_type=PageType.TEXT,
        detected_angle=90,
        existing_rotation=0,
        correction_applied=90,
        changed=True,
        confidence=95.0,
        reason="Test rotation.",
        duration_seconds=0.05,
    )
    fr = FileResult(
        input_path="/tmp/test.pdf",
        output_path="/tmp/out/test_corrected.pdf",
        total_pages=1,
        pages_changed=1,
        text_pages=1,
        scanned_pages=0,
        skipped_pages=0,
        page_results=[page],
        detection_duration_seconds=0.05,
        correction_duration_seconds=0.01,
        total_duration_seconds=0.06,
    )
    return RunResult(
        total_files=1,
        total_pages=1,
        total_pages_changed=1,
        total_text_pages=1,
        total_scanned_pages=0,
        total_skipped_pages=0,
        workers_used=4,
        peak_ram_mb=120.0,
        total_duration_seconds=0.1,
        file_results=[fr],
    )


def test_format_run_log_contains_key_fields() -> None:
    log = format_run_log(_make_run_result())
    assert "PDFOrienter Run Log" in log
    assert "test.pdf" in log
    assert "CHANGED" in log
    assert "RUN SUMMARY" in log
