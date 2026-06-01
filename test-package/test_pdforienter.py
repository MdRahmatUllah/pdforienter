"""
End-to-end smoke test for the published `pdforienter` package against the
real PDFs in `test-package/test-data/`.

Exercises the post-0.1.0 shared-pool design specifically:
  - Submits every page from every PDF in test-data/ to one shared pool, so
    workers mix pages across files (multi-file parallelism).
  - Compares full mode vs audit mode (`--audit` equivalent) on the same batch.
  - Catches per-file errors via `FileResult.error` rather than crashing.

Run from a fresh environment to prove the released package is wired correctly:

    python -m venv .venv
    .\.venv\Scripts\Activate.ps1     # Windows PowerShell
    pip install pdforienter
    python test_pdforienter.py
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
    data_dir = here / "test-data"
    output_dir = here / "corrected"

    pdf_paths = sorted(data_dir.glob("*.pdf"))

    print("=" * 78)
    print(f"pdforienter version : {getattr(pdforienter, '__version__', 'unknown')}")
    print(f"installed from      : {Path(pdforienter.__file__).parent}")
    print(f"test-data directory : {data_dir}")
    print(f"output directory    : {output_dir}")
    print(f"PDFs discovered     : {len(pdf_paths)}")
    print("=" * 78)

    if not pdf_paths:
        print(f"\nERROR: no PDFs found in {data_dir}")
        return 1

    print("\nInputs:")
    for p in pdf_paths:
        size_kb = p.stat().st_size / 1024
        print(f"  - {p.name}  ({size_kb:,.0f} KB)")

    output_dir.mkdir(exist_ok=True)

    # ----------------------------------------------------------------- pass 1
    print("\n" + "-" * 78)
    print("PASS 1 — FULL MODE (detect + correct)")
    print("-" * 78)
    result = run_pipeline([str(p) for p in pdf_paths], str(output_dir))
    log_path = write_log(result, str(output_dir))

    _print_run_summary(result, label="full")
    _print_per_file_table(result)

    print(f"\nLog file: {log_path}")

    exit_code = 0
    for fr in result.file_results:
        if fr.error:
            exit_code = 1
            continue
        out_path = Path(fr.output_path)
        if not out_path.exists():
            print(f"FAIL: corrected PDF missing for {Path(fr.input_path).name}")
            exit_code = 1

    # ----------------------------------------------------------------- pass 2
    print("\n" + "-" * 78)
    print("PASS 2 — AUDIT MODE (detect only, no writes)")
    print("-" * 78)

    audit_output_dir = here / "audit-output"
    audit_result = run_pipeline(
        [str(p) for p in pdf_paths],
        str(audit_output_dir),
        audit=True,
    )
    _print_run_summary(audit_result, label="audit")

    # Audit mode must not have produced any output PDFs.
    leaked = [
        Path(fr.output_path)
        for fr in audit_result.file_results
        if not fr.error and Path(fr.output_path).exists()
    ]
    if leaked:
        print("FAIL: audit mode produced output files:")
        for p in leaked:
            print(f"  - {p}")
        exit_code = 1
    else:
        print("OK: audit mode produced no output PDFs.")

    # ----------------------------------------------------------------- pass 3
    print("\n" + "-" * 78)
    print("PASS 3 — SINGLE WORKER (workers=1 override)")
    print("-" * 78)
    single = run_pipeline(
        [str(pdf_paths[0])],
        str(here / "single-worker-output"),
        workers=1,
    )
    print(f"  workers_used      : {single.workers_used}")
    if single.workers_used != 1:
        print(f"FAIL: --workers override ignored (got {single.workers_used})")
        exit_code = 1
    else:
        print("OK: workers override honoured.")

    print("\n" + "=" * 78)
    print(f"FINAL EXIT CODE: {exit_code}")
    print("=" * 78)
    return exit_code


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------

def _print_run_summary(result, label: str) -> None:
    print(f"  total files       : {result.total_files}")
    print(f"  total pages       : {result.total_pages}")
    print(f"  pages changed     : {result.total_pages_changed} ({label})")
    print(f"  text pages        : {result.total_text_pages}")
    print(f"  scanned pages     : {result.total_scanned_pages}")
    print(f"  skipped pages     : {result.total_skipped_pages}")
    print(f"  workers used      : {result.workers_used}")
    print(f"  total wall time   : {result.total_duration_seconds:.2f}s")
    print(f"  current RAM       : {result.current_ram_mb:.1f} MB")


def _print_per_file_table(result) -> None:
    print("\n  Per-file detail:")
    print(
        f"    {'file':<48} | {'status':<7} | {'pages':>5} | "
        f"{'rot':>3} | {'text':>4} | {'scan':>4} | {'skip':>4} | "
        f"{'det(s)':>7} | {'cor(s)':>7}"
    )
    print("    " + "-" * 110)
    for fr in result.file_results:
        name = Path(fr.input_path).name
        if len(name) > 48:
            name = name[:45] + "..."
        if fr.error:
            print(f"    {name:<48} | ERROR   | {fr.error[:80]}")
            continue
        print(
            f"    {name:<48} | OK      | "
            f"{fr.total_pages:>5} | {fr.pages_changed:>3} | "
            f"{fr.text_pages:>4} | {fr.scanned_pages:>4} | {fr.skipped_pages:>4} | "
            f"{fr.detection_duration_seconds:>7.2f} | {fr.correction_duration_seconds:>7.2f}"
        )


if __name__ == "__main__":
    sys.exit(main())
