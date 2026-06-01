"""
Determine whether a PDF page has a selectable text layer.

Responsibility: single — classify one page as TEXT or SCANNED.
"""

from __future__ import annotations

from typing import Any

import fitz  # PyMuPDF

# Minimum number of characters required to treat a page as text-based.
_MIN_CHAR_COUNT: int = 20


def has_text_layer(page: fitz.Page) -> bool:
    """Return True when *page* contains enough selectable text to analyse."""
    text = page.get_text("text")
    return len(text.strip()) >= _MIN_CHAR_COUNT


def has_text_layer_from_dict(text_dict: dict[str, Any]) -> bool:
    """
    Same threshold as `has_text_layer`, but operates on an already-extracted
    PyMuPDF text dict. Exposed so `analyse_page` can extract once and feed
    both the classifier and the detector instead of paying for two extractions.

    Early-exits as soon as the threshold is hit.
    """
    total = 0
    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                total += len(span.get("text", ""))
                if total >= _MIN_CHAR_COUNT:
                    return True
    return False
