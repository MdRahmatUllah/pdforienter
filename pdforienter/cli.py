"""
Command-line interface for PDFOrienter.

Usage
-----
    pdforienter <pdf_or_dir> [<pdf_or_dir> ...] --output <dir>
                             [--workers N] [--audit] [--quiet]

Examples
--------
    pdforienter invoice.pdf --output ./fixed
    pdforienter /scans/ report.pdf --output /corrected
    pdforienter /scans/ --output /audit-log --audit       # detect only, no writes
    pdforienter /scans/ --output ./out --workers 4 --quiet
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
    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=None,
        metavar="N",
        help="Override the worker count. Defaults to 75%% of available CPUs.",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Detect-only mode: write the log but never produce corrected PDFs "
             "(no rotation, no input copy).",
    )
    parser.add_argument(
        "--no-bake",
        dest="bake",
        action="store_false",
        help="Metadata-only rotation: set the PDF /Rotate attribute instead of "
             "physically rotating content. Lossless (keeps annotations/forms) "
             "but relies on the viewer honouring /Rotate. Default bakes rotation "
             "into content so it displays upright everywhere.",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress progress chatter; only errors and the final log path "
             "are printed.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.workers is not None and args.workers < 1:
        print("--workers must be >= 1", file=sys.stderr)
        return 2

    pdf_paths = resolve_pdf_paths(args.inputs)
    if not pdf_paths:
        print("No PDF files found in the provided paths.", file=sys.stderr)
        return 1

    if not args.quiet:
        mode = "AUDIT (detect only)" if args.audit else "FULL (detect + correct)"
        print(f"Processing {len(pdf_paths)} PDF file(s) — mode: {mode}")

    result = run_pipeline(
        pdf_paths,
        args.output,
        workers=args.workers,
        audit=args.audit,
        bake=args.bake,
    )
    log_path = write_log(result, args.output)

    if not args.quiet:
        verb = "would be rotated" if args.audit else "rotated"
        print(
            f"\nDone. {result.total_pages_changed}/{result.total_pages} pages {verb} "
            f"across {result.total_files} file(s) in "
            f"{result.total_duration_seconds:.1f}s."
        )
    print(f"Log written to: {log_path}")

    # Surface file-level errors via exit code.
    if any(fr.error for fr in result.file_results):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
