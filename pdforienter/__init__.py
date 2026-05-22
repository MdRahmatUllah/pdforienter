"""
PDFOrienter — Intelligent, parallel PDF page rotation correction.

Public API
----------
    run_pipeline(pdf_paths, output_dir) -> RunResult
"""

from pdforienter.models import FileResult, PageResult, PageType, RunResult

__all__ = [
    "run_pipeline",
    "RunResult",
    "FileResult",
    "PageResult",
    "PageType",
]

__version__ = "0.1.0"


def __getattr__(name: str):
    # Lazy export — defer importing the pipeline (and its transitive
    # pytesseract / PyMuPDF dependencies) until the symbol is actually used.
    if name == "run_pipeline":
        from pdforienter.core.pipeline import run_pipeline
        return run_pipeline
    raise AttributeError(f"module 'pdforienter' has no attribute {name!r}")
