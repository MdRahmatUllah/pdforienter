"""
Rotation correctness tests for PDFOrienter.

The integration tests in `test_integration.py` exercise dispatch, error
handling, and audit mode — but every PDF they generate is upright, so
`pages_changed` is always 0. This file plugs that gap by validating the
core promise: PDFs whose text runs in a non-default direction get their
page rotation metadata corrected so the text reads upright.

Three layers of coverage:
  - Unit: `text_orientation_from_dict` on synthetic text dicts for all four
    cardinal angles (no PyMuPDF runtime needed for these).
  - Unit: `has_text_layer_from_dict` threshold + early-exit behaviour.
  - End-to-end: real PDFs generated via PyMuPDF's `insert_text(rotate=N)`,
    round-tripped through `run_pipeline`, and reopened to verify the output
    page rotation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")

from pdforienter import run_pipeline  # noqa: E402
from pdforienter.core.classifier import has_text_layer_from_dict  # noqa: E402
from pdforienter.core.detector import text_orientation_from_dict  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic dict helpers — no PyMuPDF needed for these unit tests
# ---------------------------------------------------------------------------

def _make_text_dict(
    direction: tuple[float, float],
    n_spans_per_line: int = 5,
    n_lines: int = 3,
    span_text: str = "abc",
) -> dict[str, Any]:
    """Build a PyMuPDF-shaped text dict with every line running in *direction*."""
    return {
        "blocks": [
            {
                "lines": [
                    {
                        "dir": direction,
                        "spans": [{"text": span_text} for _ in range(n_spans_per_line)],
                    }
                    for _ in range(n_lines)
                ]
            }
        ]
    }


# ---------------------------------------------------------------------------
# text_orientation_from_dict — all four cardinals + edge cases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "direction,expected_angle",
    [
        ((1.0, 0.0), 0),     # text runs rightward — normal upright
        ((-1.0, 0.0), 180),  # text runs leftward — upside down
        ((0.0, 1.0), 270),   # text runs downward — page rotated 90° CCW
        ((0.0, -1.0), 90),   # text runs upward — page rotated 90° CW
    ],
)
def test_text_orientation_from_dict_cardinals(
    direction: tuple[float, float], expected_angle: int
) -> None:
    angle, confidence = text_orientation_from_dict(_make_text_dict(direction))
    assert angle == expected_angle
    assert confidence == pytest.approx(100.0)


def test_text_orientation_picks_dominant_with_mixed_lines() -> None:
    """Span-weighted vote: 20 spans rightward vs 2 spans upward → rightward wins."""
    text_dict: dict[str, Any] = {
        "blocks": [
            {
                "lines": [
                    {"dir": (1, 0), "spans": [{"text": "a"}] * 10},
                    {"dir": (1, 0), "spans": [{"text": "b"}] * 10},
                    {"dir": (0, -1), "spans": [{"text": "c"}] * 2},
                ]
            }
        ]
    }
    angle, confidence = text_orientation_from_dict(text_dict)
    assert angle == 0
    assert confidence == pytest.approx(20 / 22 * 100, rel=0.01)


def test_text_orientation_returns_zero_confidence_on_empty_dict() -> None:
    angle, confidence = text_orientation_from_dict({"blocks": []})
    assert (angle, confidence) == (0, 0.0)


def test_text_orientation_treats_missing_dir_as_zero() -> None:
    """A line without a `dir` key should default to (1, 0) — angle 0."""
    text_dict: dict[str, Any] = {
        "blocks": [{"lines": [{"spans": [{"text": "x"} for _ in range(5)]}]}]
    }
    angle, _ = text_orientation_from_dict(text_dict)
    assert angle == 0


# ---------------------------------------------------------------------------
# has_text_layer_from_dict — threshold + short-circuit
# ---------------------------------------------------------------------------

def test_has_text_layer_above_threshold() -> None:
    # 5 spans × 3 lines × 3 chars = 45 chars, threshold 20
    assert has_text_layer_from_dict(_make_text_dict((1, 0))) is True


def test_has_text_layer_below_threshold() -> None:
    text_dict: dict[str, Any] = {
        "blocks": [{"lines": [{"dir": (1, 0), "spans": [{"text": "abc"}]}]}]
    }
    assert has_text_layer_from_dict(text_dict) is False


def test_has_text_layer_short_circuits_after_threshold() -> None:
    """
    Once the threshold is hit, the function must return without inspecting
    subsequent blocks. We prove this by stuffing a malformed block after a
    threshold-blowing one — if iteration continues, AttributeError raises.
    """
    text_dict: dict[str, Any] = {
        "blocks": [
            {"lines": [{"dir": (1, 0), "spans": [{"text": "x" * 100}]}]},
            "this string would crash block.get('lines') if reached",
        ]
    }
    assert has_text_layer_from_dict(text_dict) is True


def test_has_text_layer_exactly_at_threshold() -> None:
    text_dict: dict[str, Any] = {
        "blocks": [{"lines": [{"dir": (1, 0), "spans": [{"text": "x" * 20}]}]}]
    }
    assert has_text_layer_from_dict(text_dict) is True


# ---------------------------------------------------------------------------
# End-to-end rotation correction
# ---------------------------------------------------------------------------

def _make_pdf_with_rotated_text(path: Path, draw_rotation: int) -> None:
    """Generate a single-page PDF with text drawn at `draw_rotation` degrees."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4
    sample = "The quick brown fox jumps over the lazy dog. " * 5
    # Pick an anchor that keeps the text on-page after rotation.
    anchor = {0: (72, 100), 90: (300, 700), 180: (500, 600), 270: (200, 100)}[draw_rotation]
    page.insert_text(anchor, sample, rotate=draw_rotation, fontsize=11)
    doc.save(str(path))
    doc.close()


def _first_content_dir(page: fitz.Page):
    """Return the `dir` vector of the first text line in content-stream space."""
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            if line.get("spans"):
                return line.get("dir")
    return None


def _assert_physically_upright(page: fitz.Page, context: str = "") -> None:
    """
    Assert a page is *physically* upright after baking: /Rotate must be 0 AND
    the content text must run left-to-right (dir ~= (1, 0)). This is stronger
    than checking /Rotate metadata — it proves the content stream itself is
    upright, so the page displays correctly in every viewer/tool.
    """
    assert page.rotation == 0, f"{context}: expected /Rotate=0, got {page.rotation}"
    cdir = _first_content_dir(page)
    assert cdir is not None, f"{context}: no text found to verify orientation"
    assert abs(cdir[0] - 1.0) < 0.01 and abs(cdir[1]) < 0.01, (
        f"{context}: content not upright — first line dir={cdir} (want (1, 0))"
    )


@pytest.mark.parametrize("draw_rotation", [0, 90, 180, 270])
def test_rotation_round_trip_bakes_content_upright(
    tmp_path: Path, draw_rotation: int
) -> None:
    """
    For text drawn at angle θ, the corrected PDF must be PHYSICALLY upright:
    /Rotate=0 and content reading left-to-right. We bake the rotation into the
    content stream rather than just setting /Rotate metadata, so the page reads
    correctly in every viewer — including ones that ignore /Rotate.
    """
    pdf_in = tmp_path / "rotated.pdf"
    out_dir = tmp_path / "out"
    _make_pdf_with_rotated_text(pdf_in, draw_rotation)

    result = run_pipeline([str(pdf_in)], str(out_dir))

    assert result.total_files == 1
    fr = result.file_results[0]
    assert fr.error is None, fr.error
    assert fr.total_pages == 1

    out_doc = fitz.open(str(Path(fr.output_path)))
    try:
        _assert_physically_upright(out_doc[0], context=f"draw_rotation={draw_rotation}")
    finally:
        out_doc.close()


def test_mixed_orientations_all_baked_upright(tmp_path: Path) -> None:
    """
    Per-page precision: one PDF with four pages each drawn at a different angle.
    After correction EVERY page must be physically upright (/Rotate=0, dir=(1,0)),
    independently — no document-level assumption.
    """
    pdf_in = tmp_path / "mixed.pdf"
    out_dir = tmp_path / "out"

    rotations = [0, 90, 180, 270]
    doc = fitz.open()
    sample = "The quick brown fox jumps over the lazy dog. " * 5
    anchors = {0: (72, 100), 90: (300, 700), 180: (500, 600), 270: (200, 100)}
    for r in rotations:
        page = doc.new_page(width=595, height=842)
        page.insert_text(anchors[r], sample, rotate=r, fontsize=11)
    doc.save(str(pdf_in))
    doc.close()

    result = run_pipeline([str(pdf_in)], str(out_dir))
    fr = result.file_results[0]
    assert fr.error is None
    assert fr.total_pages == 4
    assert fr.pages_changed == 3  # everything except the upright page 1

    out_doc = fitz.open(str(Path(fr.output_path)))
    try:
        for i in range(4):
            _assert_physically_upright(out_doc[i], context=f"page {i + 1}")
    finally:
        out_doc.close()


def test_no_bake_mode_uses_metadata_rotation(tmp_path: Path) -> None:
    """
    With bake=False the pipeline must use metadata-only rotation: the output
    page keeps its sideways content but carries a non-zero /Rotate that makes
    it display upright. Verifies the lossless fallback path still works.
    """
    pdf_in = tmp_path / "rotated.pdf"
    out_dir = tmp_path / "out"
    _make_pdf_with_rotated_text(pdf_in, 90)

    result = run_pipeline([str(pdf_in)], str(out_dir), bake=False)
    fr = result.file_results[0]
    assert fr.error is None

    out_doc = fitz.open(str(Path(fr.output_path)))
    try:
        page = out_doc[0]
        # Metadata-only: /Rotate is set, content stays in its drawn orientation.
        assert page.rotation == 90
        cdir = _first_content_dir(page)
        assert cdir == (0.0, -1.0)  # content unchanged (drawn at rotate=90)
    finally:
        out_doc.close()


def test_corrected_output_remains_openable(tmp_path: Path) -> None:
    """A corrected PDF must still be a valid, openable PDF (no truncation)."""
    pdf_in = tmp_path / "in.pdf"
    out_dir = tmp_path / "out"
    _make_pdf_with_rotated_text(pdf_in, 90)

    result = run_pipeline([str(pdf_in)], str(out_dir))
    fr = result.file_results[0]
    assert fr.error is None

    # Reopening and reading every page's text should not raise.
    out_doc = fitz.open(str(Path(fr.output_path)))
    try:
        for page in out_doc:
            _ = page.get_text("text")
    finally:
        out_doc.close()


def test_stale_rotate_with_upright_content_is_normalised(tmp_path: Path) -> None:
    """
    A PDF with `/Rotate=90` but upright content displays sideways. After
    correction it must be physically upright: /Rotate=0 and content dir=(1,0).
    """
    pdf_in = tmp_path / "pre-rotated.pdf"
    out_dir = tmp_path / "out"

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Upright text. " * 10, rotate=0, fontsize=11)
    page.set_rotation(90)  # metadata claims 90, but content is upright
    doc.save(str(pdf_in))
    doc.close()

    result = run_pipeline([str(pdf_in)], str(out_dir))
    fr = result.file_results[0]
    assert fr.error is None
    assert fr.pages_changed == 1  # /Rotate=90 must be corrected away

    out_doc = fitz.open(str(Path(fr.output_path)))
    try:
        _assert_physically_upright(out_doc[0])
    finally:
        out_doc.close()


@pytest.mark.parametrize("draw_rotation", [0, 90, 180, 270])
def test_pipeline_is_idempotent_on_its_own_output(
    tmp_path: Path, draw_rotation: int
) -> None:
    """
    Running pdforienter twice must be a no-op on the second pass. The first
    pass bakes the content physically upright (/Rotate=0, dir=(1,0)); the
    second pass must detect angle 0 and change nothing. Guards against the
    cascading-rotation class of bugs.
    """
    pdf_in = tmp_path / "rotated.pdf"
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    _make_pdf_with_rotated_text(pdf_in, draw_rotation)

    # First pass — bakes content upright.
    result1 = run_pipeline([str(pdf_in)], str(first_dir))
    fr1 = result1.file_results[0]
    assert fr1.error is None
    first_out = Path(fr1.output_path)
    assert first_out.exists()

    doc1 = fitz.open(str(first_out))
    try:
        _assert_physically_upright(doc1[0], context="first pass")
    finally:
        doc1.close()

    # Second pass on the corrected output must change nothing.
    result2 = run_pipeline([str(first_out)], str(second_dir))
    fr2 = result2.file_results[0]
    assert fr2.error is None
    assert fr2.pages_changed == 0, (
        f"Cascading-rotation regression: draw_rotation={draw_rotation}, second "
        f"pass tried to change {fr2.pages_changed} page(s)"
    )

    doc2 = fitz.open(str(Path(fr2.output_path)))
    try:
        _assert_physically_upright(doc2[0], context="second pass")
    finally:
        doc2.close()
