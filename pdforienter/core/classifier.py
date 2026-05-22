"""
Determine whether a PDF page has a selectable text layer.

Responsibility: single — classify one page as TEXT or SCANNED.
"""

from __future__ import annotations

import fitz  # PyMuPDF

# Minimum number of characters required to treat a page as text-based.
_MIN_CHAR_COUNT: int = 20


def has_text_layer(page: fitz.Page) -> bool:
    """Return True when *page* contains enough selectable text to analyse."""
    text = page.get_text("text")
    return len(text.strip()) >= _MIN_CHAR_COUNT
