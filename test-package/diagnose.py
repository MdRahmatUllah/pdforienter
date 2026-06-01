"""
Deep diagnostic for pdforienter on test-package/test-data/.

For every page of every PDF, dumps:
  - existing_rotation (page.rotation in source file)
  - Page classification: TEXT vs SCANNED (and why — char count from dict)
  - text_orientation result: angle, confidence, vote distribution per direction
  - osd_orientation result (forced even for TEXT pages): angle, confidence
  - Whether the two strategies agree

Also renders each page as a small PNG into test-package/page-thumbs/<file>/p<n>.png
so you can correlate the detector's verdict with what the page actually looks like.

Usage:
    python diagnose.py

After running, look at:
  - The per-page table printed below (what the detector sees)
  - test-package/page-thumbs/<file>/   (what the page actually looks like)

If page-thumbs/<file>/p<n>.png shows the content visually rotated but the
table says angle=0, the detector is missing a real rotation. If the table
says angle=90 but the thumbnail looks upright, the detector is wrong.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _ensure_tesseract_findable() -> str | None:
    if shutil.which("tesseract"):
        return None
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            os.environ["TESSERACT_CMD"] = candidate
            return candidate
    return None


_resolved_tesseract = _ensure_tesseract_findable()

import fitz  # noqa: E402

from pdforienter.core.classifier import _MIN_CHAR_COUNT  # noqa: E402
from pdforienter.core.detector import (  # noqa: E402
    _direction_to_angle,
    osd_orientation,
)


def _count_chars(text_dict: dict) -> int:
    total = 0
    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                total += len(span.get("text", ""))
    return total


def _vote_distribution(text_dict: dict) -> dict[int, int]:
    """Same vote tally text_orientation uses, but return the full distribution."""
    votes = {0: 0, 90: 0, 180: 0, 270: 0}
    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            direction = line.get("dir", (1, 0))
            angle = _direction_to_angle(direction)
            votes[angle] += len(line.get("spans", []))
    return votes


def _force_osd(page) -> tuple[int, float]:
    """Run OSD on this page regardless of classification."""
    try:
        return osd_orientation(page)
    except Exception:  # noqa: BLE001
        return -1, -1.0


def _render_thumb(page, out_path: Path, max_width: int = 400) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rect = page.rect
    zoom = max_width / max(rect.width, rect.height)
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    pix.save(str(out_path))


def main() -> int:
    here = Path(__file__).resolve().parent
    src_dir = here / "test-data"
    thumb_root = here / "page-thumbs"

    if thumb_root.exists():
        shutil.rmtree(thumb_root)

    src_pdfs = sorted(
        p for p in src_dir.glob("*.pdf")
        if not p.stem.endswith("_corrected")
    )

    print("=" * 100)
    print("DIAGNOSTIC RUN")
    print("=" * 100)
    print(f"Source dir       : {src_dir}")
    print(f"PDFs found       : {len(src_pdfs)}")
    print(f"Page thumbs in   : {thumb_root}")
    if _resolved_tesseract:
        print(f"Tesseract        : {_resolved_tesseract}")
    elif shutil.which("tesseract"):
        print(f"Tesseract        : {shutil.which('tesseract')}")
    else:
        print("Tesseract        : NOT FOUND")
    print(f"_MIN_CHAR_COUNT  : {_MIN_CHAR_COUNT}  (TEXT classification threshold)")
    print()

    for pdf_path in src_pdfs:
        print()
        print("#" * 100)
        print(f"# {pdf_path.name}")
        print("#" * 100)

        try:
            doc = fitz.open(str(pdf_path))
        except Exception as exc:  # noqa: BLE001
            print(f"  Failed to open: {exc}")
            continue

        try:
            print(f"  {'page':>4} | {'rot':>4} | {'chars':>6} | {'cls':<7} | "
                  f"{'text_angle':>10} | {'text_conf':>9} | "
                  f"{'votes (0/90/180/270)':<25} | "
                  f"{'osd_angle':>9} | {'osd_conf':>8}")
            print("  " + "-" * 120)

            for i, page in enumerate(doc):
                existing_rot = int(page.rotation)
                text_dict = page.get_text("dict")
                chars = _count_chars(text_dict)
                classified_text = chars >= _MIN_CHAR_COUNT
                cls = "text" if classified_text else "scanned"

                votes = _vote_distribution(text_dict)
                total_votes = sum(votes.values())
                if total_votes > 0:
                    dominant = max(votes, key=votes.__getitem__)
                    text_angle = dominant
                    text_conf = votes[dominant] / total_votes * 100
                else:
                    text_angle = 0
                    text_conf = 0.0

                osd_angle, osd_conf = _force_osd(page)

                votes_str = (
                    f"{votes[0]}/{votes[90]}/{votes[180]}/{votes[270]}"
                )

                print(
                    f"  {i+1:>4} | {existing_rot:>4} | {chars:>6} | {cls:<7} | "
                    f"{text_angle:>10} | {text_conf:>8.1f}% | "
                    f"{votes_str:<25} | "
                    f"{osd_angle:>9} | {osd_conf:>7.1f}%"
                )

                # Render a thumbnail so user can visually confirm.
                thumb_path = thumb_root / pdf_path.stem / f"p{i+1:02}.png"
                try:
                    _render_thumb(page, thumb_path)
                except Exception as exc:  # noqa: BLE001
                    print(f"       (thumb render failed: {exc})")
        finally:
            doc.close()

    print()
    print("=" * 100)
    print("HOW TO READ THIS")
    print("=" * 100)
    print("  - 'rot' is the page's existing rotation metadata (0/90/180/270).")
    print("  - 'chars' is total characters in the extracted text dict.")
    print("  - 'cls' is how pdforienter classifies the page: text >= "
          f"{_MIN_CHAR_COUNT} chars, else scanned.")
    print("  - 'text_angle/conf' is what text_orientation() returns (uses dir vectors).")
    print("  - 'votes' shows the span-weighted vote distribution per cardinal angle.")
    print("  - 'osd_angle/conf' is what Tesseract OSD returns (FORCED even on text pages).")
    print("    Use this as an independent second opinion against the text-based detector.")
    print()
    print("  Cross-check each page against the thumbnail in page-thumbs/<file>/p<n>.png.")
    print("  Disagreements between text_angle, osd_angle, and the visible thumbnail are")
    print("  the most informative — those tell us where detection logic needs work.")
    print()
    print(f"  Thumbnails: {thumb_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
