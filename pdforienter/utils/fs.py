"""
Filesystem utility helpers.

Responsibility: path / directory operations only.
"""

from __future__ import annotations

from pathlib import Path


def ensure_dir(directory: str) -> Path:
    """Create *directory* (and all parents) if it does not already exist."""
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_pdf_paths(inputs: list[str]) -> list[str]:
    """
    Expand a list of paths to absolute PDF file paths.

    Directories are walked recursively; non-PDF files are ignored.
    """
    resolved: list[str] = []
    for raw in inputs:
        p = Path(raw).resolve()
        if p.is_dir():
            resolved.extend(str(f) for f in sorted(p.rglob("*.pdf")))
        elif p.suffix.lower() == ".pdf" and p.is_file():
            resolved.append(str(p))
    return resolved
