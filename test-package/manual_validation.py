"""
Manual validation aid for pdforienter against the real PDFs in
test-package/test-data/.

Does NOT synthesise rotated test inputs — runs the pipeline directly on
test-data/ as-is and surfaces detailed per-page diagnostics so you can:

  - See which pages were classified TEXT vs SCANNED.
  - See the detected angle and confidence for every page.
  - See which pages the pipeline chose to rotate vs leave alone.
  - Find the corrected outputs in test-package/output/ and inspect them
    against the originals in test-package/test-data/ in a PDF viewer.

If a page in test-data is misoriented, you should see `angle != 0` in the
table and the corresponding fixed PDF will have that page's rotation set.
If every page shows `angle = 0`, the pipeline thinks all pages are already
upright.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _ensure_tesseract_findable() -> str | None:
    """
    Auto-discover Tesseract on Windows machines where it isn't on PATH.

    Sets the `TESSERACT_CMD` env var (which pdforienter's `osd_orientation`
    honours) so worker subprocesses inherit the binary location without
    requiring the user to modify their system PATH. Returns the resolved
    path, or None if Tesseract is already on PATH (or genuinely missing).
    """
    if shutil.which("tesseract"):
        return None  # already on PATH, nothing to do

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


# Must happen BEFORE importing pdforienter so the env var is set when the
# ProcessPoolExecutor spawns workers (they inherit env at spawn time).
_resolved_tesseract = _ensure_tesseract_findable()

from pdforienter import run_pipeline  # noqa: E402
from pdforienter.logging.writer import write_log  # noqa: E402


def main() -> int:
    here = Path(__file__).resolve().parent
    src_dir = here / "test-data"
    out_dir = here / "output"

    if not src_dir.is_dir():
        print(f"ERROR: source directory not found: {src_dir}")
        return 1

    # Skip anything that looks like a previous pdforienter output.
    src_pdfs = sorted(
        p for p in src_dir.glob("*.pdf")
        if not p.stem.endswith("_xxxxxcorrected")
    )
    if not src_pdfs:
        print(f"ERROR: no source PDFs in {src_dir}")
        return 1

    # Clean output dir so old runs don't confuse the inspection.
    if out_dir.exists():
        shutil.rmtree(out_dir)

    print("=" * 78)
    print(f"Source PDFs    : {len(src_pdfs)}  (from {src_dir})")
    print(f"Output dir     : {out_dir}")
    if _resolved_tesseract:
        print(f"Tesseract      : {_resolved_tesseract}  (auto-detected, not on PATH)")
    elif shutil.which("tesseract"):
        print(f"Tesseract      : {shutil.which('tesseract')}  (on PATH)")
    else:
        print("Tesseract      : NOT FOUND  -- scanned pages will be skipped")
    print("=" * 78)

    print("\nInputs:")
    for p in src_pdfs:
        size_kb = p.stat().st_size / 1024
        print(f"  - {p.name}  ({size_kb:,.0f} KB)")

    print(f"\nRunning run_pipeline on {len(src_pdfs)} PDF(s)...")
    result = run_pipeline([str(p) for p in src_pdfs], str(out_dir))
    log_path = write_log(result, str(out_dir))

    print()
    print("=" * 78)
    print("OVERALL")
    print("=" * 78)
    print(f"  total files       : {result.total_files}")
    print(f"  total pages       : {result.total_pages}")
    print(f"  pages rotated     : {result.total_pages_changed}")
    print(f"  text pages        : {result.total_text_pages}")
    print(f"  scanned pages     : {result.total_scanned_pages}")
    print(f"  skipped pages     : {result.total_skipped_pages}")
    print(f"  workers used      : {result.workers_used}")
    print(f"  wall time         : {result.total_duration_seconds:.2f}s")
    print(f"  log file          : {log_path}")

    print()
    print("=" * 78)
    print("PER-PAGE DETAIL")
    print("=" * 78)
    print("  (existing = page rotation in the input file)")
    print("  (angle    = what the detector reports)")
    print("  (status   = CHANGED if pdforienter modified page rotation in output)")
    print()

    for fr in result.file_results:
        name = Path(fr.input_path).name
        print(f"-- {name}")
        if fr.error:
            print(f"   ERROR: {fr.error}")
            continue
        print(
            f"   {'page':>4} | {'type':<7} | {'status':<7} | "
            f"{'existing':>8} | {'angle':>5} | {'conf':>5} | "
            f"{'duration':>8}"
        )
        print(f"   {'-' * 4} | {'-' * 7} | {'-' * 7} | "
              f"{'-' * 8} | {'-' * 5} | {'-' * 5} | {'-' * 8}")
        for pr in fr.page_results:
            status = "CHANGED" if pr.changed else "OK"
            print(
                f"   {pr.page_number:>4} | {pr.page_type.value:<7} | {status:<7} | "
                f"{pr.existing_rotation:>8} | {pr.detected_angle:>5} | "
                f"{pr.confidence:>5.1f} | {pr.duration_seconds:>8.2f}s"
            )
        print(f"   output: {fr.output_path}")
        print()

    print("=" * 78)
    print("HOW TO MANUALLY VALIDATE")
    print("=" * 78)
    print("  1. Open the originals from:")
    print(f"       {src_dir}")
    print("  2. Open the corresponding outputs from:")
    print(f"       {out_dir}")
    print("  3. Compare side-by-side in a PDF viewer (Acrobat, Edge, browser).")
    print()
    print("  Reading the per-page detail above:")
    print("    - 'angle = 0'      -> pipeline believes the page is upright")
    print("    - 'angle = 90/180/270' -> pipeline detected a misorientation")
    print("    - 'status = CHANGED' -> output has new page rotation set")
    print("    - 'status = OK'      -> output page kept original rotation")
    print()
    print("  If you have a PDF you KNOW is misoriented but the table shows")
    print("  angle=0 across the board, pdforienter is missing the rotation —")
    print("  please flag the specific file/page and I'll dig in.")

    return 0 if not any(fr.error for fr in result.file_results) else 1


if __name__ == "__main__":
    sys.exit(main())
