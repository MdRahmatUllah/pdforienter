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

**Two-phase pipeline.** `processor.process_file` runs Phase 1 (parallel per-page detection via `ProcessPoolExecutor`) to completion before Phase 2 (single PyMuPDF `doc.save` with all rotations applied). The single-write property is the whole reason this project exists — never reintroduce per-page writes.

**Strict module responsibility.** Each module owns exactly one job and the boundaries are enforced by the directory layout:

- `config.py` — constants only, no logic, no imports beyond stdlib
- `models.py` — typed dataclasses only, no logic
- `core/classifier.py` — "is this page text or scanned?" — nothing else
- `core/detector.py` — orientation strategies (`text_orientation`, `osd_orientation`) returning `(angle, confidence)`
- `core/analyzer.py` — picklable per-page worker; only primitives in/out so it survives `ProcessPoolExecutor`
- `core/corrector.py` — single-pass PyMuPDF write
- `core/processor.py` — per-file Phase 1 + Phase 2 coordinator
- `core/pipeline.py` — multi-file orchestrator; the public entry point
- `logging/formatter.py` — `RunResult → str`, pure function, no I/O
- `logging/writer.py` — `str → disk`, thin wrapper
- `utils/{fs,resources}.py` — small helpers

If a change would put logic in the wrong file, push back or split it.

**Worker count.** `MAX_WORKERS = max(1, floor(cpu_count × 0.75))` in `config.py`. This is intentional — never hardcode worker counts, never bump to 100%.

**Lazy heavy imports.** `pytesseract` and `PIL` are imported *inside* `osd_orientation()` in `detector.py`, not at module top. This is deliberate: text-only workloads must not pay for loading pytesseract (which transitively imports pandas), and it lets tests collect even when the OCR stack isn't usable. The public `run_pipeline` is also lazy-loaded via `__getattr__` in the top-level `__init__.py` for the same reason.

**`analyse_page` must stay picklable.** It runs in subprocess workers. Accept only primitives (`pdf_path: str`, `page_index: int`), return a plain dataclass, and catch *all* exceptions so one bad page can't kill the whole batch — return a `PageType.SKIPPED` result instead.

**Confidence-gated OSD.** OSD results below `OSD_CONFIDENCE_THRESHOLD` (default 10.0) are discarded and the page is left unchanged. A wrong rotation is worse than a missed correction — this is the project's stance.

## Public API contract

External callers use:

```python
from pdforienter import run_pipeline
```

This is wired through `pdforienter/__init__.py`'s lazy `__getattr__`. Don't replace it with an eager import — see "Lazy heavy imports" above.

## Tests

`tests/test_core.py` is a smoke suite covering helpers (`_direction_to_angle`, `_normalise_rotation`), config sanity, and the log formatter against a synthetic `RunResult`. It deliberately avoids real PDF I/O so it runs in <1s and stays green even on machines with a broken OCR stack. New tests that need a real PDF should be gated or fixtured separately, not bolted onto `test_core.py`.
