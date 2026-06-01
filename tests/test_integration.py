"""
Integration tests for the public `run_pipeline` API.

These tests generate small PDFs on the fly with PyMuPDF and run them through
the real shared-pool pipeline (ProcessPoolExecutor and all). They exercise
multi-file parallelism, audit mode, and file-level error handling — things
the unit tests in `test_core.py` deliberately skip.

Skipped automatically if `fitz` (PyMuPDF) is not importable so the suite stays
green on machines that haven't pulled the OCR stack.
"""

from __future__ import annotations

from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")

from pdforienter import run_pipeline  # noqa: E402
from pdforienter.core.processor import prepare_file  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_text_pdf(path: Path, pages: int = 3, rotation: int = 0) -> None:
    """Generate a tiny multi-page text PDF for use as test input."""
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)  # A4
        page.insert_text(
            (72, 72),
            f"This is page {i + 1} of {pages}.\nSome additional text to "
            f"clear the classifier's character-count threshold.\n" * 4,
        )
        if rotation:
            page.set_rotation(rotation)
    doc.save(str(path))
    doc.close()


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def test_run_pipeline_single_file(tmp_path: Path) -> None:
    pdf = tmp_path / "input.pdf"
    out = tmp_path / "out"
    _make_text_pdf(pdf, pages=3)

    result = run_pipeline([str(pdf)], str(out))

    assert result.total_files == 1
    assert result.total_pages == 3
    assert result.total_text_pages == 3

    fr = result.file_results[0]
    assert fr.error is None
    assert Path(fr.output_path).exists()
    assert len(fr.page_results) == 3
    # Generated PDFs are upright → nothing to rotate.
    assert fr.pages_changed == 0


def test_run_pipeline_multi_file_dispatches_all(tmp_path: Path) -> None:
    pdf_a = tmp_path / "a.pdf"
    pdf_b = tmp_path / "b.pdf"
    pdf_c = tmp_path / "c.pdf"
    out = tmp_path / "out"
    _make_text_pdf(pdf_a, pages=2)
    _make_text_pdf(pdf_b, pages=4)
    _make_text_pdf(pdf_c, pages=1)

    result = run_pipeline([str(pdf_a), str(pdf_b), str(pdf_c)], str(out))

    assert result.total_files == 3
    assert result.total_pages == 7
    for fr in result.file_results:
        assert fr.error is None, fr.error
        assert Path(fr.output_path).exists()


def test_run_pipeline_audit_writes_no_pdfs(tmp_path: Path) -> None:
    pdf = tmp_path / "input.pdf"
    out = tmp_path / "out"
    _make_text_pdf(pdf, pages=2)

    result = run_pipeline([str(pdf)], str(out), audit=True)

    fr = result.file_results[0]
    assert fr.error is None
    assert fr.total_pages == 2
    # Audit mode: detection ran, but no output file was produced.
    assert not Path(fr.output_path).exists()
    assert fr.correction_duration_seconds == 0.0


def test_run_pipeline_missing_file_surfaces_error(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.pdf"
    out = tmp_path / "out"

    result = run_pipeline([str(missing)], str(out))

    assert result.total_files == 1
    fr = result.file_results[0]
    assert fr.error is not None
    assert fr.total_pages == 0
    # Workers should never have been spun up for this file.
    assert fr.page_results == []


def test_run_pipeline_workers_override(tmp_path: Path) -> None:
    pdf = tmp_path / "input.pdf"
    out = tmp_path / "out"
    _make_text_pdf(pdf, pages=2)

    result = run_pipeline([str(pdf)], str(out), workers=1)

    assert result.workers_used == 1


# ---------------------------------------------------------------------------
# prepare_file (file-level validation)
# ---------------------------------------------------------------------------

def test_prepare_file_rejects_oversize(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the threshold low so a tiny generated PDF trips the limit."""
    import pdforienter.core.processor as proc

    pdf = tmp_path / "input.pdf"
    _make_text_pdf(pdf, pages=1)
    monkeypatch.setattr(proc, "MAX_FILE_SIZE_MB", 0)  # 0 MB → anything is too big

    spec = prepare_file(str(pdf), str(tmp_path / "out"))

    assert spec.error is not None
    assert "MAX_FILE_SIZE_MB" in spec.error
    assert spec.page_count == 0


def test_prepare_file_rejects_missing(tmp_path: Path) -> None:
    spec = prepare_file(str(tmp_path / "nope.pdf"), str(tmp_path / "out"))

    assert spec.error is not None
    assert spec.page_count == 0
