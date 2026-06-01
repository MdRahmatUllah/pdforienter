# PDFOrienter — Technical Documentation

**Version:** 0.1.0  
**Language:** Python 3.10+  
**Author:** PDFOrienter Contributors

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Design Goals and Constraints](#2-design-goals-and-constraints)
3. [Architecture Overview](#3-architecture-overview)
4. [Key Design Decisions](#4-key-design-decisions)
5. [Module Reference](#5-module-reference)
6. [The Two-Phase Pipeline](#6-the-two-phase-pipeline)
7. [Orientation Detection Strategies](#7-orientation-detection-strategies)
8. [Parallelism Model](#8-parallelism-model)
9. [Resource Management](#9-resource-management)
10. [Data Flow and Models](#10-data-flow-and-models)
11. [Logging System](#11-logging-system)
12. [Performance Characteristics](#12-performance-characteristics)
13. [Use Cases](#13-use-cases)
14. [Known Limitations](#14-known-limitations)
15. [Extension Points](#15-extension-points)

---

## 1. Problem Statement

### 1.1 The Original Issue

PDF files can contain pages with incorrect rotations. This happens routinely when:

- Documents are scanned on a flatbed scanner without consistent paper orientation
- PDFs are merged from multiple sources with different page orientations
- Mobile camera scanning apps produce portrait images inside a landscape page container
- Legacy document management systems export PDFs with missing or wrong rotation metadata

The initial implementation corrected this by processing each page individually and sequentially:

```
for each page:
    1. extract text or run OCR
    2. detect orientation
    3. apply rotation
    4. save PDF
```

### 1.2 Why This Was Slow

This approach had three compounding performance problems:

**Problem 1 — Sequential page processing.**  
Every page waited for the previous one to finish. On a 500-page PDF with mixed scanned content, this meant a single long chain of blocking operations. No CPU core parallelism was exploited.

**Problem 2 — OCR on every page regardless of page type.**  
OCR (Optical Character Recognition) is expensive. It involves rasterising a page into a bitmap, loading language model data, and running a neural classification pipeline. Applying this to pages that already have a perfectly good selectable text layer was a major waste.

**Problem 3 — One PDF write per rotation.**  
Every time a page was corrected, the PDF was opened, mutated, and saved. For a 500-page PDF with 100 rotated pages, this meant 100 separate write passes over the same file — redundant I/O that scaled linearly with the number of corrections.

### 1.3 Scale of the Problem

The system needed to handle:

- PDFs with 500+ pages each
- Large batches of PDFs uploaded simultaneously
- A production server with 8 CPU cores and 16 GB RAM
- Per-page precision (no sampling shortcuts)
- Mixed page types — some pages text-based, some scanned images

Under the old approach, 20 PDFs with 2,000 total pages on an 8-core machine effectively ran on a single core. The rest of the machine sat idle.

---

## 2. Design Goals and Constraints

### Goals

| Goal | Rationale |
|---|---|
| Per-page precision | Some PDFs have mixed orientations; every page must be evaluated independently |
| Maximum throughput | Must handle large batches efficiently on a multi-core server |
| Dynamic core scaling | Server may be upgraded from 8 to 16+ cores with no code changes required |
| System stability | Must not starve the OS or other applications of CPU time |
| Accurate orientation detection | Incorrect rotation is worse than no correction |
| Structured observability | Operators need detailed logs for auditing and debugging |
| Package-ready code | Will be published to PyPI; must be modular, typed, and testable |

### Constraints

| Constraint | Impact on Design |
|---|---|
| No intermediate files | Output is written once; no temp files between steps |
| Mixed page types in one PDF | Cannot make document-level assumptions; must check every page |
| No OCR on text pages | Text layer analysis is always preferred when available |
| Conservative correction | Only apply rotation when confidence is high enough |
| Python standard library parallelism | No external task queue (Celery etc.) for this stage |

---

## 3. Architecture Overview

PDFOrienter is structured as a layered pipeline with strict separation of concerns. No module owns more than one responsibility.

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI / Python API                      │
│                         cli.py / __init__.py                 │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                       pipeline.py                            │
│          Top-level orchestrator — iterates files             │
└──────────────────────────────┬──────────────────────────────┘
                               │  one call per file
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                       processor.py                           │
│     Per-file coordinator — Phase 1 + Phase 2 sequencing      │
└──────────┬────────────────────────────────┬─────────────────┘
           │ Phase 1                        │ Phase 2
           ▼                               ▼
┌──────────────────────┐       ┌───────────────────────────┐
│     analyzer.py      │       │       corrector.py         │
│  Per-page worker     │       │  Single-pass rotation      │
│  (subprocess)        │       │  writer                    │
└──────┬───────────────┘       └───────────────────────────┘
       │
       ├──▶ classifier.py   (text vs scanned?)
       │
       └──▶ detector.py     (text_orientation | osd_orientation)
```

### Supporting layers

```
models.py      — shared typed data containers (PageResult, FileResult, RunResult)
config.py      — all tuneable constants in one place
logging/
  formatter.py — serialises RunResult to structured text
  writer.py    — writes log file to disk
utils/
  fs.py        — filesystem helpers (ensure_dir, resolve_pdf_paths)
  resources.py — RAM and CPU telemetry (psutil)
```

---

## 4. Key Design Decisions

### 4.1 Two-Phase Pipeline Instead of Per-Page Write

**Decision:** Separate orientation detection from rotation correction entirely. Detect all pages first, then apply all rotations in a single write pass.

**Why:** The original approach wrote the PDF once per corrected page. Writing a PDF is not a cheap append operation — PyMuPDF must serialise the entire document structure. On a 500-page document with 100 corrections, the old approach performed 100 full serialisations of what is effectively the same file. The two-phase approach performs exactly one serialisation regardless of how many pages need correction.

**Trade-off:** Phase 1 results must be held in memory until Phase 2 completes. For a 500-page document this is a list of 500 lightweight `PageResult` dataclass instances — negligible memory cost.

### 4.2 ProcessPoolExecutor Over ThreadPoolExecutor

**Decision:** Use `ProcessPoolExecutor` for page-level parallelism, not `ThreadPoolExecutor`.

**Why:** Both text direction analysis (PyMuPDF) and Tesseract OSD are CPU-bound operations. Python's Global Interpreter Lock (GIL) prevents true parallel execution of CPU-bound Python code across threads. `ProcessPoolExecutor` spawns separate OS processes, each with its own Python interpreter and GIL, enabling genuine multi-core utilisation.

**Trade-off:** Inter-process communication is slower than inter-thread communication. Each `analyse_page` call must be serialisable (picklable) to be dispatched to a worker. The function is intentionally designed to accept only primitive arguments (`pdf_path: str`, `page_index: int`) and return a plain dataclass to keep serialisation overhead minimal.

### 4.3 75% Core Utilisation Cap

**Decision:** Limit workers to `floor(cpu_count × 0.75)`, minimum 1.

**Why:** Using all available cores would starve the operating system scheduler, monitoring agents, and any other processes on the same host. On a shared production server, this can cause unresponsive SSH sessions, failed health checks, or degraded performance for concurrent requests. The 75% cap leaves enough headroom for the system to breathe while still achieving strong throughput.

**Why not a fixed number?** A hardcoded worker count (e.g. `max_workers=6`) would not scale when the server is upgraded. By computing the cap from `os.cpu_count()` at runtime, the code automatically exploits more cores on a larger machine with zero configuration changes.

```python
MAX_WORKERS = max(1, math.floor(os.cpu_count() * 0.75))
# 8  cores  → 6  workers
# 16 cores  → 12 workers
# 32 cores  → 24 workers
```

### 4.4 Smart OCR Triggering — Text Pages Skip Tesseract

**Decision:** Only call Tesseract OSD when a page has no usable text layer. Text pages use a fast vector-based direction analysis instead.

**Why:** Tesseract OSD on a typical 150 DPI page takes 1–3 seconds. PyMuPDF's text direction analysis takes ~0.1–0.3 seconds. On a document that is 80% text-based, naive OCR-on-every-page wastes the majority of the processing time.

**How the classification works:** PyMuPDF's `page.get_text("text")` extracts any selectable text layer. If the extracted string contains at least 20 non-whitespace characters, the page is classified as `TEXT`. Otherwise it falls through to `SCANNED` and Tesseract OSD is invoked. The threshold of 20 characters was chosen to avoid treating pages with only a header or a watermark as fully text-based.

### 4.5 Tesseract OSD Mode (PSM 0) Instead of Full OCR

**Decision:** Use Tesseract's Orientation and Script Detection (OSD) mode (`--psm 0`) rather than full OCR.

**Why:** Full OCR reads every character on the page to produce a text transcript. OSD only analyses enough of the image to determine the page's dominant text orientation and script — it produces no character output. This makes OSD roughly 3–5× faster than full OCR for the same input, and for the purpose of rotation detection, the character data is irrelevant.

**Confidence threshold:** OSD returns a confidence score (0–100). Results below `OSD_CONFIDENCE_THRESHOLD` (default: 10.0) are discarded and the page is treated as requiring no correction. This prevents low-quality or nearly-blank scanned pages from triggering spurious rotations.

### 4.4.1 Idempotent Correction (Absolute Target /Rotate)

**Decision:** The analyzer normalises both detector strategies to "absolute target `/Rotate` value" and computes the relative correction from `existing_rotation`. Re-running pdforienter on its own output is a no-op.

**Why:** The two detection strategies have different return semantics:

- `text_orientation_from_dict` reads `dir` vectors from the content stream. PyMuPDF returns these in the content stream's native coordinates, ignoring `/Rotate` metadata. So the returned angle is the **absolute** target `/Rotate` that would make the content display upright.
- `osd_orientation` rasterises via `page.get_pixmap()`, which applies `/Rotate` by default. OSD sees the already-rotated image and returns the **relative** additional rotation needed.

A naive `new_rotation = existing_rotation + detected_angle` works on a fresh file (`existing_rotation=0`) but cascades on the corrected output (existing=90, detected=90 → new=180; next pass 180+180=0; oscillates). The fix:

```python
# Normalise OSD to absolute.
detected_angle = (existing_rotation + osd_relative) % 360   # OSD path
# Text path is already absolute — use raw_angle as detected_angle.

# Compute the delta needed.
correction = (detected_angle - existing_rotation) % 360

# changed iff correction != 0
new_rotation = (existing_rotation + correction) % 360       # equals detected_angle
```

This also normalises a PDF that has `/Rotate=N` set but draws content upright (e.g., a stale tool wrote it) — pdforienter rewrites `/Rotate` to 0 so the visible content is upright.

**Trade-off:** Pages where a user deliberately set `/Rotate` for some non-content reason (rare) will have that metadata overwritten. We consider this correct: the project's job is to make the visible content upright, not to preserve cosmetic metadata.

### 4.4.2 Bake Rotation Into Content (Default)

**Decision:** By default, physically rotate page content so the output is upright with `/Rotate=0`, rather than only setting the `/Rotate` page attribute.

**Why:** Setting `/Rotate` is spec-correct and every compliant viewer honours it — but a surprising number of real-world consumers do **not**: image converters (`pdf2image`, ImageMagick), some print drivers, certain OCR front-ends, and thumbnail generators frequently rasterise the content stream while ignoring `/Rotate`. For those, a page "corrected" with metadata-only rotation still comes out sideways. Baking the rotation into the content stream guarantees the page is upright in every tool, no exceptions.

**How:** For each page, reset the source `/Rotate` to 0 and re-embed it via `show_pdf_page(rect, src, idx, rotate=(360 - detected_angle) % 360)`, swapping width/height for 90/270. The bake angle is the inverse of the target `/Rotate` because `show_pdf_page` rotates counter-clockwise while `/Rotate` is clockwise. The result has `/Rotate=0` and content text running left-to-right — verified objectively in `test_rotation.py` by asserting both `page.rotation == 0` and first-line `dir ≈ (1, 0)`.

**Trade-off:** `show_pdf_page` re-embeds each page as a Form XObject. Vector text survives and remains selectable, but page-level annotations, links, and form fields are dropped, and the file is slightly larger. Users who need those preserved can pass `bake=False` (CLI `--no-bake`) for the lossless metadata-only path. For the project's target workload — scanned/exported documents being normalised for downstream processing — baking is the right default.

### 4.5.1 Multi-Pass OSD

**Decision:** Run Tesseract OSD four times per scanned page — once on the original rasterised image and once on each of the 90/180/270 pre-rotated variants — and pick the orientation where OSD reports `rotate=0` with the highest confidence.

**Why:** Empirical testing against real scanned German receipts and invoices showed single-pass OSD was wildly unreliable: Tesseract often reported the wrong angle with very low confidence (1–5%) on its first look at a rotated page. But Tesseract is far more accurate at confirming "this image is upright" when it actually IS upright. Asking the same question four times — "is this orientation upright?" — and taking the strongest yes lifts scanned-page detection from ~30% to ~100% on the project's test corpus.

**Trade-off:** Four OSD calls per scanned page instead of one. On the test corpus this costs ~9 seconds of worker time per scanned page (at 300 DPI), but because pages run in parallel across `MAX_WORKERS` it adds only seconds of wall-clock per batch. Text pages are unaffected — they never enter the OSD path.

### 4.6 Render at 300 DPI for OSD

**Decision:** When rasterising a page for Tesseract OSD, use 300 DPI.

**Why:** At 150 DPI (the original choice), Tesseract OSD reports ~1–3% confidence on real-world German receipts and invoices — completely unusable. At 300 DPI, the same content yields ~12–15% confidence (well above the 10% threshold). The memory cost is real (a 300 DPI A4 grayscale page is roughly 8 MB vs 2 MB at 150 DPI), but the worker pool budget at 75% of cores leaves easily enough headroom — 6 workers × 8 MB ≈ 50 MB of pixmap data at any moment, far below the 1–2 GB envelope.

**Trade-off:** ~4× slower per OSD call than 150 DPI. Compensated for by the fact that the slower DPI also failed silently — every "fast" miss was a real rotation we should have caught.

### 4.7 Grayscale Rasterisation

**Decision:** Render pages to grayscale (`fitz.csGRAY`) before passing to Tesseract.

**Why:** Tesseract OSD operates on luminance information. Colour adds no useful signal for orientation detection but triples the image data size (RGB vs L). Grayscale rasterisation reduces memory usage and speeds up the Pillow → Tesseract handoff.

### 4.8 Single Responsibility Per Module

**Decision:** Every module has exactly one stated responsibility, enforced by file size limits (150 lines maximum).

**Why:** When a bug is reported ("Tesseract is returning wrong angles"), the developer knows immediately to look in `detector.py`. When a performance issue is found in the write pass, it lives entirely in `corrector.py`. This structure also makes unit testing straightforward — each module can be tested in isolation with minimal mocking.

---

## 5. Module Reference

### `config.py`

Central location for every tuneable constant. No business logic. Importing this module is the only way any other module accesses configuration values.

| Constant | Type | Default | Description |
|---|---|---|---|
| `CPU_COUNT` | `int` | `os.cpu_count()` | Logical CPU count at startup |
| `MAX_WORKERS` | `int` | `floor(CPU_COUNT × 0.75)` | Worker process cap |
| `TESSERACT_OSD_PSM` | `int` | `0` | Tesseract page segmentation mode |
| `OSD_CONFIDENCE_THRESHOLD` | `float` | `10.0` | Minimum OSD confidence to accept a result |
| `VALID_ROTATIONS` | `tuple` | `(0, 90, 180, 270)` | Accepted discrete rotation angles |
| `MAX_FILE_SIZE_MB` | `int` | `200` | Soft file size limit for validation |
| `LOG_DATE_FORMAT` | `str` | `"%Y-%m-%d %H:%M:%S"` | Timestamp format in log output |

---

### `models.py`

Pure data classes. Zero business logic. Shared vocabulary for every module.

**`PageType` (Enum)**

```
TEXT    — page has a selectable text layer
SCANNED — image-only page; OSD was used
SKIPPED — analysis failed or was not attempted
```

**`PageResult`**

Captures everything that happened on a single page: classification, detected angle, existing rotation, applied correction, confidence, timing, and a human-readable reason string.

**`FileResult`**

Aggregates all `PageResult` instances for one PDF along with per-file timing breakdowns and an optional error message.

**`RunResult`**

Top-level summary across all processed files. Includes total page counts by type, worker count used, peak RAM usage, and total wall-clock duration.

---

### `core/classifier.py`

**Responsibility:** Decide whether a page has a usable text layer.

**Function:** `has_text_layer(page: fitz.Page) -> bool`

Extracts the text layer via PyMuPDF and checks the character count against `_MIN_CHAR_COUNT` (20). Returns `True` if the page has enough text to analyse with the vector strategy.

---

### `core/detector.py`

**Responsibility:** Return `(angle_in_degrees, confidence)` for one page using the appropriate strategy.

**Function:** `text_orientation(page) -> tuple[int, float]`

Reads the raw text block structure from PyMuPDF. Each text line carries a direction vector `(cos θ, sin θ)`. The function maps each vector to the nearest cardinal angle, accumulates span-weighted votes per angle, and returns the dominant angle with a confidence score derived from the vote share.

**Function:** `osd_orientation(page) -> tuple[int, float]`

Rasterises the page to a grayscale PIL image at **300 DPI** and runs **multi-pass OSD**: `pytesseract.image_to_osd` is called four times, once on the image and once on each of the 90/180/270 pre-rotated variants. The pre-rotation that makes OSD report `rotate=0` with the highest confidence is the correction angle. Returns `(0, best_conf)` if no orientation clears `OSD_CONFIDENCE_THRESHOLD`, or `(0, 0.0)` on any exception. Honours the `TESSERACT_CMD` environment variable so users on Windows whose Tesseract is installed but not on PATH can point at the binary directly.

**Function:** `_direction_to_angle(direction) -> int`

Maps a `(dx, dy)` vector to `{0, 90, 180, 270}` using threshold comparisons. Internal helper — not part of the public API.

---

### `core/analyzer.py`

**Responsibility:** Produce a `PageResult` for one page. Designed to run inside a worker subprocess.

**Function:** `analyse_page(pdf_path: str, page_index: int) -> PageResult`

This is the unit of work sent to each `ProcessPoolExecutor` worker. It opens the PDF independently (each worker opens its own file handle), classifies the page, calls the appropriate detector, and packages the result. Accepts only primitive arguments to ensure it is safely picklable. All exceptions are caught and returned as a `SKIPPED` result so one bad page cannot kill the entire batch.

---

### `core/corrector.py`

**Responsibility:** Apply rotation corrections to a PDF in a single write pass. Two strategies, selected by the `bake` keyword.

**Function:** `apply_rotations(input_path, output_path, page_results, *, bake=True) -> float`

Dispatches to one of two writers and returns the wall-clock seconds spent. Both save once with `garbage=4` (cross-reference compaction) and `deflate=True` (compression).

**Function:** `_write_baked(input_path, output_path, page_results)` — the default

Physically rotates content so the output is upright with `/Rotate=0`, working in **every** viewer and tool (including ones that ignore `/Rotate`: image converters, some print drivers, OCR front-ends). For each page it resets the source `/Rotate` to 0, then re-embeds the page into a fresh page via `show_pdf_page(rect, src, idx, rotate=bake_angle)` where `bake_angle = (360 - detected_angle) % 360`. The inversion is necessary because `show_pdf_page`'s `rotate` is counter-clockwise while `/Rotate` is clockwise. Width/height are swapped when `detected_angle` is 90 or 270. Vector text is preserved (re-embedded as a Form XObject, still selectable); page-level annotations, links, and form fields are **not** carried over.

**Function:** `_write_metadata_only(input_path, output_path, page_results)` — `bake=False`

Sets `page.set_rotation(detected_angle)` on changed pages and leaves the content stream untouched. Lossless (annotations/links/form fields preserved) and spec-correct, but relies on the consumer honouring `/Rotate`.

**Function:** `_normalise_rotation(degrees: int) -> int`

Reduces any angle to the range `[0, 360)` using modular arithmetic.

---

### `core/processor.py`

**Responsibility:** Per-file plumbing for the pipeline. The pool itself lives one layer up in `pipeline.py`; this module just validates inputs and assembles per-file results.

**Function:** `prepare_file(input_path, output_dir) -> FileSpec`

Pre-flight validation for one input. Builds the output path, enforces `MAX_FILE_SIZE_MB`, opens the PDF just long enough to read the page count, then returns a `FileSpec` describing the file. File-level failures (missing file, oversize, unopenable PDF) are captured as `FileSpec.error` rather than raised — the pipeline still needs a `FileResult` for the failed file so it appears in the log.

**Function:** `build_file_result(spec, page_results, *, audit) -> FileResult`

Called by the pipeline once a file's per-page detections are in hand. Assembles the final `FileResult`. When `audit=True`, skips Phase 2 entirely (no rotation, no copy) but still reports the detection summary.

**Function:** `_correct_file(input_path, output_path, page_results) -> float`

Short-circuits if no pages need rotation: copies the input file to the output path with `shutil.copy2` and returns 0.0. This avoids an unnecessary PyMuPDF serialisation for already-correct files.

---

### `core/pipeline.py`

**Responsibility:** Top-level orchestration. Owns the single shared `ProcessPoolExecutor` that handles every page from every file in the batch.

**Function:** `run_pipeline(pdf_paths, output_dir, *, workers=None, audit=False) -> RunResult`

Pre-flights every file via `prepare_file`, then opens **one** `ProcessPoolExecutor` for the whole batch and submits every page from every valid file to it. Collects results via `as_completed`, then runs Phase 2 per file via `build_file_result`. Ensures the output directory exists before dispatch.

Multi-file parallelism is intrinsic: with `workers > 1` and multiple files, the pool happily mixes pages from different files on the same workers. The pool budget (`MAX_WORKERS`, overridable via the `workers=` keyword) is not divided between file-level and page-level — every page is just one item in a single flat queue.

---

### `logging/formatter.py`

**Responsibility:** Serialise a `RunResult` into a human-readable structured text string.

Produces a fixed-format log with a run summary section followed by a per-file section, each containing a per-page detail table. Formatting is deterministic and testable — the formatter has no side effects and can be tested with synthetic `RunResult` objects.

---

### `logging/writer.py`

**Responsibility:** Write the formatted log string to disk.

Creates a timestamped log file (`pdforienter_YYYYMMDD_HHMMSS.log`) in the output directory. Separated from the formatter so the serialisation logic can be tested without touching the filesystem.

---

### `utils/fs.py`

**Responsibility:** Filesystem operations.

`ensure_dir` creates a directory tree with `parents=True, exist_ok=True`. `resolve_pdf_paths` accepts a mixed list of file paths and directory paths and returns a flat, sorted list of absolute PDF file paths — directories are walked recursively.

---

### `utils/resources.py`

**Responsibility:** Runtime resource telemetry.

`current_ram_mb` returns the current process's resident set size in megabytes via `psutil`. Sampled at the end of the run as a rough sanity check — note that it is *current* RSS, not a true high-water mark, so freed pixmaps and Tesseract buffers are not reflected. `cpu_count` is a thin wrapper around `os.cpu_count()`.

---

## 6. The Two-Phase Pipeline

The two-phase model is the central architectural innovation. Understanding it is essential for reasoning about the system's performance.

```
INPUT PDF
    │
    ▼
┌───────────────────────────────────────────────────────┐
│  PHASE 1 — PARALLEL DETECTION                         │
│                                                       │
│  Page 1 ──▶ [worker] ──▶ PageResult(angle=0,  ok)    │
│  Page 2 ──▶ [worker] ──▶ PageResult(angle=90, fix)   │
│  Page 3 ──▶ [worker] ──▶ PageResult(angle=0,  ok)    │
│  Page 4 ──▶ [worker] ──▶ PageResult(angle=180,fix)   │
│  ...                                                  │
│  Page N ──▶ [worker] ──▶ PageResult(angle=0,  ok)    │
│                                                       │
│  All workers run concurrently up to MAX_WORKERS       │
└───────────────────────────┬───────────────────────────┘
                            │
                            │  list[PageResult]
                            ▼
┌───────────────────────────────────────────────────────┐
│  PHASE 2 — SINGLE-PASS CORRECTION                     │
│                                                       │
│  Open PDF                                             │
│  page[1].set_rotation(0)   ← already correct, skip   │
│  page[2].set_rotation(90)  ← apply correction        │
│  page[3].set_rotation(0)   ← already correct, skip   │
│  page[4].set_rotation(180) ← apply correction        │
│  ...                                                  │
│  doc.save(output_path)     ← ONE write, always        │
└───────────────────────────────────────────────────────┘
                            │
                            ▼
                     OUTPUT PDF
```

Phase 1 dominates total runtime. Phase 2 is typically 0.2–5 seconds regardless of how many pages were changed.

---

## 7. Orientation Detection Strategies

### 7.1 Text Vector Strategy (Fast Path)

PyMuPDF exposes the internal text layer of a PDF as structured blocks, lines, and spans. Each line carries a `dir` property — a unit vector `(cos θ, sin θ)` representing the direction text runs on the page.

For a correctly oriented page where text runs left-to-right, `dir ≈ (1.0, 0.0)`. For a page rotated 90° clockwise (text running bottom-to-top as seen on screen), `dir ≈ (0.0, -1.0)`.

The strategy accumulates span-weighted votes per angle:

```
angle_votes = {0: 0, 90: 0, 180: 0, 270: 0}

for each text line:
    map dir vector → nearest cardinal angle
    angle_votes[angle] += number of spans in this line

dominant_angle = argmax(angle_votes)
confidence = angle_votes[dominant] / sum(angle_votes) × 100
```

Span-weighting means lines with more text content have more influence than short captions or headers, producing more stable results on complex layouts.

### 7.2 OSD Strategy (Slow Path — Scanned Pages Only)

For image-only pages, there is no text vector data. The page must be rasterised and Tesseract must analyse the resulting bitmap.

```
page  →  fitz.get_pixmap(dpi=300, colorspace=GRAY)
      →  PIL.Image (L mode)
      →  multi-pass: for pre_rotation in (0, 90, 180, 270):
             img.rotate(-pre_rotation) → pytesseract.image_to_osd(psm=0)
             → keep pre_rotation where OSD says rotate=0 with highest confidence
      →  ( correction_angle, confidence )
```

OSD on a single orientation is unreliable on real-world scanned content (confidence typically <5%). Asking it four times — "is the image upright in this orientation?" — and trusting the strongest yes is empirically more accurate. The winning `pre_rotation` is the correction angle: rotating the page that many degrees CW makes its content read upright.

If no orientation produces confidence above `OSD_CONFIDENCE_THRESHOLD`, the result is discarded and the page is left unchanged. Conservative by design — a wrong rotation is more damaging than a missed correction.

### 7.3 Strategy Selection

```python
if has_text_layer(page):        # ≥ 20 extractable characters
    angle, conf = text_orientation(page)   # ~0.1–0.3s
else:
    angle, conf = osd_orientation(page)    # ~1–3s
```

---

## 8. Parallelism Model

### 8.1 Worker Pool Lifecycle

A single `ProcessPoolExecutor` is created for the **entire batch**, not per file. All page analysis futures from every valid file are submitted to it before any results are collected. `as_completed` is used so that faster workers (text pages) are collected without waiting for slower workers (scanned pages), and the pool is kept as busy as possible throughout — including by mixing pages from different files on the same worker.

```python
with ProcessPoolExecutor(max_workers=worker_count) as pool:
    futures = {}
    for spec in valid_specs:
        for page_index in range(spec.page_count):
            fut = pool.submit(analyse_page, spec.input_path, page_index)
            futures[fut] = (spec.input_path, page_index)
    for fut in as_completed(futures):
        path, page_index = futures[fut]
        results[path][page_index] = fut.result()
```

This is intentionally a flat queue, not nested pools. Worker count remains capped at `MAX_WORKERS` (default; overridable via `run_pipeline(..., workers=N)`) regardless of how many files are in the batch, so a 50-file batch on an 8-core server still uses 6 workers — it just spreads them across files instead of finishing one file at a time.

The context manager ensures all workers are cleanly shut down even if an exception propagates.

### 8.2 Why Each Worker Opens Its Own File Handle

Workers are separate OS processes with separate memory spaces. There is no shared file handle between the parent process and workers. Each `analyse_page` call opens and closes the PDF independently. This is safe because Phase 1 is read-only — no worker writes to the file.

### 8.3 Worker Count Formula

```
MAX_WORKERS = max(1, floor(cpu_count × 0.75))
```

| Server cores | MAX_WORKERS | Cores left free |
|---|---|---|
| 4 | 3 | 1 |
| 8 | 6 | 2 |
| 16 | 12 | 4 |
| 32 | 24 | 8 |

The formula scales proportionally so that larger servers always leave a sensible amount of headroom.

### 8.4 GIL Bypass

`ProcessPoolExecutor` spawns independent Python interpreter processes. Each has its own GIL. CPU-bound operations (PyMuPDF C extensions, Tesseract via ctypes) in one worker do not block any other worker. This is the critical difference from `ThreadPoolExecutor` for this workload.

---

## 9. Resource Management

### 9.1 Memory Usage Estimate

| Component | Per Worker | 6 Workers |
|---|---|---|
| Tesseract language model (OSD) | ~150–300 MB | ~900 MB–1.8 GB |
| PyMuPDF page pixmap (150 DPI A4 gray) | ~2 MB | ~12 MB |
| PageResult dataclass | < 1 KB | negligible |
| PDF document object | ~5–20 MB | ~120 MB |
| **Total estimate** | | **~1–2 GB** |

On a 16 GB server this is well within safe limits.

### 9.2 CPU Behaviour Under Contention

`ProcessPoolExecutor` does not pin workers to specific cores — the OS scheduler handles allocation. If another application claims CPU time, worker processes are preempted and PDFOrienter slows proportionally. It does not starve, deadlock, or crash. The 75% cap reduces the probability of contention in the first place.

### 9.3 No Intermediate Disk I/O

Phase 1 holds all results in memory. Phase 2 writes a single output file. No temporary files are created. This is important for:

- **Cloud storage workflows** (e.g. Azure Blob): the output file can be streamed directly to the destination
- **SSDs with limited write endurance**: every avoided write extends hardware life
- **Network-mounted filesystems**: reducing I/O operations reduces latency exposure

---

## 10. Data Flow and Models

```
Input: list[str]  (PDF file paths)
         │
         ▼
      pipeline.run_pipeline()
         │
         │ for each file
         ▼
      pipeline submits every page from every valid file
      to one shared ProcessPoolExecutor
         │
         │ Phase 1: workers mix pages from different files
         ▼
      analyzer.analyse_page()  →  PageResult
         │
         │ Phase 2: single pass
         ▼
      corrector.apply_rotations()
         │
         ▼
      FileResult  (aggregates PageResults)
         │
         ▼
      RunResult  (aggregates FileResults)
         │
         ▼
      logging.formatter  →  log string
      logging.writer     →  .log file on disk
```

All intermediate and final data is typed. `RunResult` and `FileResult` are plain Python dataclasses — they can be serialised to JSON by the caller if needed for downstream systems.

---

## 11. Logging System

### 11.1 Design

The logging system follows a strict formatter/writer separation:

- `formatter.py` — pure function: `RunResult → str`. No I/O. Fully testable.
- `writer.py` — impure function: `str → disk`. Thin wrapper around `Path.write_text`.

This means the log format can be tested with synthetic data without any filesystem setup.

### 11.2 Log Structure

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
     p   2 | scanned | CHANGED | angle= 90° | conf= 87.5 | 2.34s | Rotation of 90° detected.
     p   3 | text    | OK      | angle=  0° | conf= 99.1 | 0.09s | No rotation needed.
```

### 11.3 Timing Breakdown

Three timing values are recorded per file:

- **Detection time** — sum of all `PageResult.duration_seconds`. Because pages run in parallel, this is *total worker time*, not wall-clock time. It can exceed the file's wall-clock duration.
- **Correction time** — wall-clock seconds for the single-pass PyMuPDF write.
- **Total time** — sum of detection time + correction time. With the shared-pool model files overlap in wall-clock, so per-file wall-clock is no longer meaningful — this number is closer to "how long would this file have taken alone" than to elapsed seconds.

---

## 12. Performance Characteristics

### 12.1 Complexity

| Operation | Time Complexity |
|---|---|
| Phase 1 (detection) | O(N / W) where N = pages, W = workers |
| Phase 2 (correction) | O(N) — one pass over all pages |
| Log formatting | O(N) |
| Overall | O(N / W) — detection dominates |

Doubling the number of workers approximately halves Phase 1 time, up to the limit of I/O saturation.

### 12.2 Throughput Estimates — 8 Core Server (6 Workers)

| Scenario | Pages | Est. Time |
|---|---|---|
| All text pages | 2,000 | 1–2 min |
| 50% text, 50% scanned | 2,000 | 7–8 min |
| All scanned pages | 2,000 | 15–17 min |

These estimates assume typical A4 documents at 150 DPI. High-resolution scans (300+ DPI source) will be slower because PyMuPDF still rasterises at 150 DPI, but the underlying vector data is denser.

### 12.3 Single-Pass Write Gain

On a 500-page PDF with 100 pages needing rotation, the old approach (100 write passes) vs the new approach (1 write pass):

```
Old: 100 writes × ~0.5s/write = ~50s just for writing
New: 1 write   × ~0.8s       =   0.8s for writing

Speedup on write phase alone: ~62×
```

---

## 13. Use Cases

### Use Case 1 — Bulk Invoice Processing

**Scenario:** An accounts payable team receives 50 supplier invoices per day as PDFs. Many are scanned on different office printers with inconsistent paper orientation. Before the documents are indexed in the ERP system, they need to be correctly oriented for OCR downstream.

**How PDFOrienter handles it:**

```bash
pdforienter /incoming/invoices/ --output /processed/invoices/
```

PDFOrienter walks the directory, finds all PDFs, and processes them. Each invoice is typically 1–5 pages. For a 50-invoice batch totalling ~150 pages (mostly text-layer PDFs from modern scanners), processing completes in under 30 seconds on a 6-worker setup. The log provides a full audit trail per page.

---

### Use Case 2 — Legacy Document Migration

**Scenario:** A company is migrating 10,000 archived PDF documents from a legacy storage system to a modern document management platform. Approximately 30% of the archive was scanned in the 1990s–2000s with inconsistent scanner orientation settings.

**How PDFOrienter handles it:**

```python
from pdforienter import run_pipeline
from pdforienter.logging.writer import write_log
from pdforienter.utils.fs import resolve_pdf_paths

paths = resolve_pdf_paths(["/archive/legacy/"])
result = run_pipeline(paths, "/archive/migrated/")
write_log(result, "/archive/migrated/")

print(f"Corrected {result.total_pages_changed} pages across {result.total_files} files")
```

The batch can be split across multiple runs. Since corrected files are written to a separate output directory, originals are never modified and the migration can be re-run if needed.

---

### Use Case 3 — Server-Side Upload Preprocessing

**Scenario:** A document management web application allows users to upload PDFs. Before indexing, the server corrects any rotation issues automatically. Uploads arrive concurrently from multiple users.

**Integration pattern:**

```python
# In the upload handler (e.g. FastAPI)
from pdforienter import run_pipeline
import tempfile, os

async def handle_upload(file: UploadFile, user_id: str):
    with tempfile.TemporaryDirectory() as tmp:
        input_path = os.path.join(tmp, file.filename)
        output_dir = os.path.join(tmp, "corrected")

        # Save upload to temp location
        with open(input_path, "wb") as f:
            f.write(await file.read())

        # Correct orientation
        result = run_pipeline([input_path], output_dir)
        fr = result.file_results[0]

        if fr.error:
            raise ProcessingError(fr.error)

        # Upload corrected file to Azure Blob
        corrected_path = fr.output_path
        await upload_to_blob(corrected_path, user_id, file.filename)
```

Because PDFOrienter caps worker usage at 75% of cores, concurrent upload handlers from multiple users will compete fairly for the remaining worker slots without a single upload monopolising the server.

---

### Use Case 4 — Quality Audit Without Modification

**Scenario:** An operator wants to audit which pages in an archive have orientation problems before running corrections, without modifying any files.

**Pattern — run detection only and inspect the RunResult:**

```python
from pdforienter import run_pipeline
import os

# Point output to a temp directory — we only care about the result object
result = run_pipeline(pdf_paths, output_dir="/tmp/audit_output")

for fr in result.file_results:
    rotated = [p for p in fr.page_results if p.changed]
    if rotated:
        print(f"{fr.input_path}: {len(rotated)} pages need rotation")
        for p in rotated:
            print(f"  Page {p.page_number}: {p.detected_angle}° ({p.page_type.value})")
```

---

### Use Case 5 — CI/CD Document Pipeline

**Scenario:** A publishing team stores PDF manuscripts in a Git repository. A CI job validates and corrects orientation on every pull request.

```yaml
# .github/workflows/pdf-check.yml
- name: Correct PDF orientation
  run: |
    pip install pdforienter
    pdforienter ./manuscripts/ --output ./manuscripts_corrected/
    
- name: Check log for errors
  run: |
    log=$(ls manuscripts_corrected/*.log | tail -1)
    if grep -q "ERROR" "$log"; then
      echo "PDFOrienter reported errors"
      cat "$log"
      exit 1
    fi
```

---

## 14. Known Limitations

| Limitation | Reason | Mitigation |
|---|---|---|
| Image-only pages with very little text may be skipped | OSD requires enough text to detect orientation | Accepted trade-off; flagged as `SKIPPED` in log |
| Very low-confidence OSD results are ignored | Prevents wrong corrections | Page left unchanged; visible in log |
| Audit mode skips Phase 2 entirely (no input copy either) | By design — `--audit` is for read-only inspection | Use full mode if a copy of every input is needed |
| No support for password-protected PDFs | PyMuPDF raises an error on encrypted files | Returns `FileResult` with `error` field |
| Arabic / Hebrew right-to-left text may confuse vector strategy | RTL text direction vectors differ | OSD fallback partially mitigates this |
| No OCR output — only orientation | OSD mode is orientation-only by design | Not a rotation-correction limitation |

---

## 15. Extension Points

### Adding a New Detection Strategy

Implement a function with this signature in `detector.py` or a new module:

```python
def my_orientation(page: fitz.Page) -> tuple[int, float]:
    ...
    return angle, confidence  # angle ∈ {0, 90, 180, 270}, confidence ∈ [0, 100]
```

Then update `analyzer.py` to call it under the appropriate condition.

### Changing the Output Format

`logging/formatter.py` returns a plain string. To produce JSON logs instead, create `logging/json_formatter.py` with a `format_run_log_json(result: RunResult) -> str` function and call it from `writer.py`. No other modules need to change.

### Integrating with Azure Blob Storage

After `run_pipeline` returns, upload the output files from the `output_dir`:

```python
from azure.storage.blob import BlobServiceClient

result = run_pipeline(pdf_paths, "/tmp/corrected")

client = BlobServiceClient.from_connection_string(conn_str)
container = client.get_container_client("corrected-pdfs")

for fr in result.file_results:
    if not fr.error:
        with open(fr.output_path, "rb") as f:
            container.upload_blob(
                name=os.path.basename(fr.output_path),
                data=f,
                overwrite=True,
            )
```

---

*End of technical documentation.*
