"""
Command-line interface for PDFOrienter.

Usage
-----
    pdforienter <pdf_or_dir> [<pdf_or_dir> ...] --output <dir>

Examples
--------
    pdforienter invoice.pdf --output ./fixed
    pdforienter /scans/ report.pdf --output /corrected
"""

from __future__ import annotations

import argparse
import sys

from pdforienter.core.pipeline import run_pipeline
from pdforienter.logging.writer import write_log
from pdforienter.utils.fs import resolve_pdf_paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdforienter",
        description="Automatically fix PDF page orientations.",
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        metavar="PDF_OR_DIR",
        help="One or more PDF files or directories to process.",
    )
    parser.add_argument(
        "--output", "-o",
        required=True,
        metavar="OUTPUT_DIR",
        help="Directory where corrected PDFs and the log will be saved.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    pdf_paths = resolve_pdf_paths(args.inputs)
    if not pdf_paths:
        print("No PDF files found in the provided paths.", file=sys.stderr)
        return 1

    print(f"Processing {len(pdf_paths)} PDF file(s)…")
    result = run_pipeline(pdf_paths, args.output)
    log_path = write_log(result, args.output)

    print(
        f"\nDone. {result.total_pages_changed}/{result.total_pages} pages rotated "
        f"across {result.total_files} file(s) in {result.total_duration_seconds:.1f}s."
    )
    print(f"Log written to: {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
