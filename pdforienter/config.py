"""
Global configuration and tuneable constants for PDFOrienter.
"""

import math
import os

# ---------------------------------------------------------------------------
# Worker pool
# ---------------------------------------------------------------------------
# Use 75 % of available logical CPUs, minimum 1, to leave headroom for the OS
# and other processes running on the same host.
CPU_COUNT: int = os.cpu_count() or 1
MAX_WORKERS: int = max(1, math.floor(CPU_COUNT * 0.75))

# ---------------------------------------------------------------------------
# Tesseract / OSD
# ---------------------------------------------------------------------------
# Tesseract Page Segmentation Mode 0 = Orientation and Script Detection only.
# Much faster than full OCR — no character recognition is performed.
TESSERACT_OSD_PSM: int = 0

# Minimum OSD confidence (0-100) required to trust the detected orientation.
OSD_CONFIDENCE_THRESHOLD: float = 10.0

# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------
# Only these discrete angles (degrees) are considered valid rotations.
VALID_ROTATIONS: tuple[int, ...] = (0, 90, 180, 270)

# ---------------------------------------------------------------------------
# File limits
# ---------------------------------------------------------------------------
MAX_FILE_SIZE_MB: int = 200

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S"
