"""
End-to-end smoke test for the published `pdforienter` package on PyPI.

Run from a fresh environment to prove the released package is wired correctly:

    python -m venv .venv
    .\.venv\Scripts\Activate.ps1     # Windows PowerShell
    pip install pdforienter
    python test_pdforienter.py

The script:
  1. Imports `pdforienter` and prints which install is being used (so you can
     tell the published package apart from a local editable checkout).
  2. Runs `run_pipeline` against the bundled test PDF.
  3. Writes a structured log file via the package's own log writer.
  4. Prints a per-file summary plus the first/last few page results.
  5. Verifies the corrected PDF was actually produced on disk.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    try:
        import pdforienter
        from pdforienter import run_pipeline
        from pdforienter.logging.writer import write_log
    except ImportError:
        print("pdforienter is not installed. Run:  pip install pdforienter")
        return 1

    here = Path(__file__).resolve().parent
    input_pdf = here / "sample.pdf"
    output_dir = here / "corrected"

    print("=" * 70)
    print(f"pdforienter version : {getattr(pdforienter, '__version__', 'unknown')}")
    print(f"installed from      : {Path(pdforienter.__file__).parent}")
    print(f"input PDF           : {input_pdf}")
    print(f"output directory    : {output_dir}")
    print("=" * 70)

    if not input_pdf.exists():
        print(f"\nERROR: Test PDF not found at {input_pdf}")
        return 1

    output_dir.mkdir(exist_ok=True)

    print("\nRunning pipeline...\n")
    result = run_pipeline([str(input_pdf)], str(output_dir))
    log_path = write_log(result, str(output_dir))

    fr = result.file_results[0]

    print("-" * 70)
    print("RESULT SUMMARY")
    print("-" * 70)
    if fr.error:
        print(f"  Status            : ERROR")
        print(f"  Message           : {fr.error}")
        return 1

    print(f"  Status            : OK")
    print(f"  Total pages       : {fr.total_pages}")
    print(f"  Pages changed     : {fr.pages_changed}")
    print(f"  Text pages        : {fr.text_pages}")
    print(f"  Scanned pages     : {fr.scanned_pages}")
    print(f"  Skipped pages     : {fr.skipped_pages}")
    print(f"  Detection time    : {fr.detection_duration_seconds:.2f}s (sum of worker time)")
    print(f"  Correction time   : {fr.correction_duration_seconds:.2f}s")
    print(f"  Total wall time   : {fr.total_duration_seconds:.2f}s")
    print(f"  Workers used      : {result.workers_used}")
    print(f"  Peak RAM          : {result.peak_ram_mb:.1f} MB")
    print(f"  Output PDF        : {fr.output_path}")
    print(f"  Log file          : {log_path}")

    print("\n" + "-" * 70)
    print("PAGE SAMPLE (first 5 / last 5)")
    print("-" * 70)
    sample_pages = fr.page_results[:5]
    if len(fr.page_results) > 10:
        sample_pages += [None] + fr.page_results[-5:]
    elif len(fr.page_results) > 5:
        sample_pages += fr.page_results[5:]

    for p in sample_pages:
        if p is None:
            print("    ...")
            continue
        status = "CHANGED" if p.changed else "OK"
        print(
            f"    p{p.page_number:>4} | {p.page_type.value:<7} | {status:<7} | "
            f"angle={p.detected_angle:>3}° | conf={p.confidence:>5.1f} | "
            f"{p.duration_seconds:.2f}s"
        )

    output_path = Path(fr.output_path)
    if not output_path.exists():
        print(f"\nFAIL: corrected PDF was not written to {output_path}")
        return 1

    print(
        f"\nOK: corrected PDF exists "
        f"({output_path.stat().st_size:,} bytes vs "
        f"{input_pdf.stat().st_size:,} bytes input)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
