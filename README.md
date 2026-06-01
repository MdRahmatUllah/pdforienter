# PDFOrienter

**Intelligent, parallel PDF page rotation correction for Python.**

PDFOrienter analyses every page of one or more PDF files, detects incorrect orientations, and fixes them in a single write pass — with no unnecessary re-processing.

---

## Features

- **Two-phase pipeline** — detect all pages in parallel, then apply all corrections in a single write
- **Smart strategy selection** — uses fast text-direction analysis for text-based pages; falls back to Tesseract OSD only for image/scanned pages
- **Dynamic parallelism** — automatically uses 75 % of available CPU cores; scales from 4 to 64+ cores without any configuration change
- **Detailed structured logging** — per-page and per-file timing, rotation details, confidence scores, RAM and CPU usage
- **Zero intermediate files** — corrected PDFs are written once; originals are never modified
- **Package-ready** — clean modular design, typed, fully testable

---

## Requirements

### Python

Python 3.10 or newer.

### System dependency — Tesseract

Tesseract must be installed on the host system **before** installing PDFOrienter.

**Ubuntu / Debian**
```bash
sudo apt-get update && sudo apt-get install -y tesseract-ocr
```

**macOS (Homebrew)**
```bash
brew install tesseract
```

**Windows**

Download and run the installer from the [Tesseract UB Mannheim releases](https://github.com/UB-Mannheim/tesseract/wiki), then add the install directory to your `PATH`.

---

## Installation

```bash
pip install pdforienter
```

For development (includes linting + test tools):

```bash
git clone https://github.com/your-org/pdforienter.git
cd pdforienter
pip install -e ".[dev]"
```

---

## Quick Start

### Command line

```bash
# Fix a single PDF
pdforienter invoice.pdf --output ./fixed

# Fix every PDF in a directory
pdforienter /scans/ --output /corrected

# Mix files and directories
pdforienter report.pdf /archive/ receipts.pdf --output ./out
```

### Python API

```python
from pdforienter import run_pipeline
from pdforienter.logging.writer import write_log

result = run_pipeline(
    pdf_paths=["invoice.pdf", "report.pdf"],
    output_dir="./corrected",
)

# Write the structured log file
log_path = write_log(result, "./corrected")

print(f"{result.total_pages_changed} pages corrected in {result.total_duration_seconds:.1f}s")
print(f"Log: {log_path}")
```

---

## Log File

Every run produces a timestamped `.log` file in the output directory.

```
PDFOrienter Run Log — 2024-11-01 14:32:05
============================================================

[RUN SUMMARY]
  Total files processed : 3
  Total pages           : 247
  Pages rotated         : 18
  Text pages            : 201
  Scanned pages (OCR)   : 46
  Skipped pages         : 0
  Workers used          : 6
  Current RAM usage     : 312.4 MB
  Total time            : 42.18s

------------------------------------------------------------
[FILE] /scans/invoice.pdf
  Output          : /corrected/invoice_corrected.pdf
  Total pages     : 12
  Pages changed   : 3
  Text pages      : 8
  Scanned pages   : 4
  Skipped pages   : 0
  Detection time  : 9.41s
  Correction time : 0.23s
  Total time      : 9.64s
  [PAGE DETAILS]
     p   1 | text    | OK      | angle=  0° | conf= 98.2 | 0.11s | No rotation needed.
     p   2 | scanned | CHANGED | angle= 90° | conf= 87.5 | 2.34s | Rotation of 90° detected (confidence 87.5).
     ...
```

---

## Project Structure

```
pdforienter/
├── pdforienter/
│   ├── __init__.py          # Public API: run_pipeline
│   ├── config.py            # Tuneable constants (worker count, thresholds)
│   ├── models.py            # Typed data classes (PageResult, FileResult, RunResult)
│   ├── cli.py               # Command-line interface
│   ├── core/
│   │   ├── pipeline.py      # Top-level orchestrator
│   │   ├── processor.py     # Per-file orchestrator (Phase 1 + Phase 2)
│   │   ├── analyzer.py      # Per-page worker (dispatched to subprocess)
│   │   ├── classifier.py    # Text vs scanned page detection
│   │   ├── detector.py      # Orientation detection (text + OSD strategies)
│   │   └── corrector.py     # Single-pass rotation applier
│   ├── logging/
│   │   ├── formatter.py     # RunResult → structured log string
│   │   └── writer.py        # Write log file to disk
│   └── utils/
│       ├── fs.py            # Filesystem helpers
│       └── resources.py     # CPU / RAM telemetry
├── tests/
│   └── test_core.py
├── pyproject.toml
└── README.md
```

---

## Configuration

All tuneable constants live in `pdforienter/config.py`.

| Constant | Default | Description |
|---|---|---|
| `MAX_WORKERS` | `floor(cpu_count × 0.75)` | Worker processes for parallel page analysis |
| `OSD_CONFIDENCE_THRESHOLD` | `10.0` | Minimum Tesseract OSD confidence to trust a result |
| `TESSERACT_OSD_PSM` | `0` | Tesseract page segmentation mode (0 = OSD only) |
| `_RENDER_DPI` (detector.py) | `150` | DPI used when rasterising pages for OSD |
| `_MIN_CHAR_COUNT` (classifier.py) | `20` | Minimum characters to classify a page as text-based |

---

## How It Works

### Phase 1 — Parallel Detection

Each page is dispatched to a subprocess worker via `ProcessPoolExecutor`. Workers run concurrently up to `MAX_WORKERS`.

For each page:
1. **Classify** — does the page have selectable text?
2. **Detect orientation**
   - *Text page* → analyse character direction vectors (fast, no OCR)
   - *Scanned page* → rasterise at 300 DPI and run multi-pass Tesseract OSD
3. Return a `PageResult` with the detected angle, confidence, and timing

### Phase 2 — Single-Pass Correction

After all pages are analysed, a single write pass produces the corrected PDF. By default PDFOrienter **bakes** the rotation into each page's content so the output is genuinely upright with `/Rotate=0` — it displays correctly in *every* viewer and tool, including those that ignore the `/Rotate` page attribute (image converters, some print drivers, OCR front-ends).

Vector text stays selectable after baking, but page-level annotations, links, and form fields are not preserved. If you need those, pass `--no-bake` (CLI) or `bake=False` (API) for lossless metadata-only rotation that sets `/Rotate` instead.

```bash
# Default: physically upright output, works everywhere
pdforienter scan.pdf --output ./fixed

# Lossless: keep annotations/forms, rely on viewer honouring /Rotate
pdforienter scan.pdf --output ./fixed --no-bake
```

---

## Performance

Typical estimates on an 8-core server (6 workers) with mixed text/scanned PDFs:

| Scenario | Estimate |
|---|---|
| 2 000 pages, all text-based | ~1–2 minutes |
| 2 000 pages, mixed 50/50 | ~7–8 minutes |
| 2 000 pages, all scanned | ~15–17 minutes |

RAM usage: ~200–400 MB per Tesseract worker. 6 workers ≈ 2.5 GB peak. Well within a 16 GB server.

---

## Running Tests

```bash
pytest tests/ -v
```

---

## License

MIT
