# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Source of truth

`TECHNICAL.md` is the authoritative spec for this project — it documents the architecture, the rationale for every design decision, and the contract of every module. Before changing behaviour, read the relevant section there. `README.md` is the user-facing surface; keep it consistent with `TECHNICAL.md` when the public API or log format changes.

## Common commands

```powershell
# Install in editable mode with dev tools (pytest, ruff, mypy)
pip install -e ".[dev]"

# Run the full test suite
pytest tests/ -v

# Run a single test
pytest tests/test_core.py::test_normalise_rotation_wraps -v

# Lint / type-check
ruff check .
mypy pdforienter

# Run the CLI
pdforienter <pdf_or_dir> [<pdf_or_dir> ...] --output <dir>
```

Tesseract must be installed at the OS level (it is **not** a pip dependency). On Windows: install from UB-Mannheim builds and ensure `tesseract.exe` is on `PATH`. See `README.md` for other platforms.

## Architecture invariants — do not break these

**Two-phase pipeline with one shared pool.** `pipeline.run_pipeline` opens a single `ProcessPoolExecutor` for the entire batch (not per file) and submits every page from every valid file to it. Phase 1 (parallel detection) drains to completion across all files before Phase 2 (per-file `doc.save` with all rotations applied) starts. The single-write-per-file property is the whole reason this project exists — never reintroduce per-page writes. The shared-pool model is also load-bearing — do not nest pools or create one per file.

**Strict module responsibility.** Each module owns exactly one job and the boundaries are enforced by the directory layout:

- `config.py` — constants only, no logic, no imports beyond stdlib
- `models.py` — typed dataclasses only, no logic
- `core/classifier.py` — "is this page text or scanned?" — nothing else. Exposes `has_text_layer(page)` and the dict-based `has_text_layer_from_dict(text_dict)` so the analyzer can extract once and feed both classifier and detector.
- `core/detector.py` — orientation strategies returning `(angle, confidence)`. Like classifier, exposes both a `page`-accepting form and a `text_dict`-accepting form.
- `core/analyzer.py` — picklable per-page worker. Extracts the PyMuPDF text dict once per page and reuses it across classifier + detector — do not reintroduce a second `get_text` call.
- `core/corrector.py` — single-pass PyMuPDF write. Default **bakes** rotation physically into page content (`show_pdf_page`) so output is upright with `/Rotate=0` in every viewer; `bake=False` falls back to metadata-only `/Rotate`.
- `core/processor.py` — per-file plumbing: `prepare_file` (validation + page count) and `build_file_result` (assemble FileResult, run Phase 2 unless `audit=True`). **Does not own the pool.**
- `core/pipeline.py` — owns the shared `ProcessPoolExecutor`; dispatches and aggregates; the public entry point.
- `logging/formatter.py` — `RunResult → str`, pure function, no I/O
- `logging/writer.py` — `str → disk`, thin wrapper
- `utils/{fs,resources}.py` — small helpers

If a change would put logic in the wrong file, push back or split it.

**Worker count.** `MAX_WORKERS = max(1, floor(cpu_count × 0.75))` in `config.py`. This is the default. Callers may override via `run_pipeline(..., workers=N)` or the CLI's `--workers N`. Never hardcode worker counts, never bump the default to 100%, and never grow the budget for multi-file batches — the budget is per-batch, not per-file.

**Lazy heavy imports.** `pytesseract` and `PIL` are imported *inside* `osd_orientation()` in `detector.py`, not at module top. This is deliberate: text-only workloads must not pay for loading pytesseract (which transitively imports pandas), and it lets tests collect even when the OCR stack isn't usable. The public `run_pipeline` is also lazy-loaded via `__getattr__` in the top-level `__init__.py` for the same reason.

**`analyse_page` must stay picklable.** It runs in subprocess workers. Accept only primitives (`pdf_path: str`, `page_index: int`), return a plain dataclass, and catch *all* exceptions so one bad page can't kill the whole batch — return a `PageType.SKIPPED` result instead.

**Confidence-gated OSD.** OSD results below `OSD_CONFIDENCE_THRESHOLD` (default 10.0) are discarded and the page is left unchanged. A wrong rotation is worse than a missed correction — this is the project's stance.

**Multi-pass OSD at 300 DPI.** `osd_orientation` runs Tesseract four times per scanned page (once per 0/90/180/270 pre-rotation of the image) and picks the pre-rotation where OSD reports `rotate=0` with highest confidence. This is *much* more robust than single-pass on real-world content — single-pass OSD on receipts/invoices typically reports 1–5% confidence even when the page is clearly rotated. Do not regress this to single-pass without a strong reason, and do not lower DPI below 300 without re-running `test-package/diagnose.py` to verify confidence stays above threshold.

**Tesseract location override.** `osd_orientation` honours the `TESSERACT_CMD` environment variable. Useful on Windows where the UB-Mannheim installer doesn't add `tesseract.exe` to PATH. If you add auto-discovery logic, do it before importing pdforienter (so the env var propagates to worker subprocesses at spawn time) — see `test-package/manual_validation.py` for an example.

**Detectors have different semantics — normalise to absolute /Rotate in the analyzer.** PyMuPDF's `get_text("dict")` returns text `dir` vectors in the content stream's native coordinates, IGNORING `/Rotate`. PyMuPDF's `get_pixmap` APPLIES `/Rotate` by default. So:

- `text_orientation_from_dict` returns the **absolute** target `/Rotate` value (what the metadata should be set to).
- `osd_orientation` returns a **relative** additional rotation (what to add on top of the existing `/Rotate`).

The analyzer normalises both to absolute `detected_angle = (existing_rotation + osd_relative) % 360` for OSD, leaves text-based as-is. `detected_angle` is "the /Rotate value that makes this page upright". The corrector consumes `detected_angle` (NOT `correction_applied`). **Without this normalisation, re-running pdforienter on its own output would cascade rotations (90 + 90 = 180, etc).** The idempotency tests in `test_rotation.py` enforce this — don't regress.

**Baking is the default write strategy, and it inverts the angle.** `corrector._write_baked` physically rotates content via `show_pdf_page(rect, src, idx, rotate=bake_angle)` where `bake_angle = (360 - detected_angle) % 360`. The inversion is load-bearing: `show_pdf_page`'s `rotate` is counter-clockwise, while `/Rotate` is clockwise. Width/height are swapped when `detected_angle` is 90 or 270. The source page's `/Rotate` is reset to 0 first so the bake is computed against raw geometry. Result: output pages have `/Rotate=0` and content `dir=(1,0)` — physically upright everywhere, not just in `/Rotate`-aware viewers. Verify any change with the objective check: open the output and assert `page.rotation == 0` and first-line `dir ≈ (1,0)`. **Baking re-embeds each page as a Form XObject — vector text survives (still selectable) but page annotations/links/form fields do NOT.** Use `bake=False` when those must be preserved.

**Audit mode short-circuits Phase 2.** When `run_pipeline(..., audit=True)` (or `--audit`), no corrected PDFs are written and no inputs are copied. The `FileResult.output_path` still records *where it would have gone* so users can see the intended destination. Do not "helpfully" copy inputs in audit mode — that defeats the purpose.

**File-level validation lives in `prepare_file`.** `MAX_FILE_SIZE_MB` and "file is openable as a PDF" are enforced upfront and turn into `FileSpec.error` strings, not exceptions. Workers should never see a file the pipeline knows is bad.

## Public API contract

External callers use:

```python
from pdforienter import run_pipeline
```

This is wired through `pdforienter/__init__.py`'s lazy `__getattr__`. Don't replace it with an eager import — see "Lazy heavy imports" above.

## Tests

`tests/test_core.py` is a smoke suite covering helpers (`_direction_to_angle`, `_normalise_rotation`), config sanity, and the log formatter against a synthetic `RunResult`. It deliberately avoids real PDF I/O so it runs in <1s and stays green even on machines with a broken OCR stack. New tests that need a real PDF should be gated or fixtured separately, not bolted onto `test_core.py`.
