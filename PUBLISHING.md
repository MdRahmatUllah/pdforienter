# Publishing `pdforienter` to PyPI

This guide walks through publishing the package from the IDE terminal (PowerShell) under the **InfinitiBit GmbH** PyPI organisation: <https://pypi.org/org/infinitibit_gmbh/>.

> Do the **TestPyPI dry run first** (Section 5). It catches metadata mistakes that would otherwise burn the `0.1.0` version number on real PyPI — once a version is uploaded, it can never be re-uploaded.

---

## 1. One-time setup

### 1.1 Install build tooling

```powershell
pip install --upgrade build twine
```

- `build` produces the sdist + wheel.
- `twine` uploads them and validates metadata before upload.

### 1.2 Create a PyPI API token (scoped to the org)

1. Sign in at <https://pypi.org/account/login/>.
2. Go to **<https://pypi.org/manage/account/token/>**.
3. Click **Add API token**.
4. **Token name:** `pdforienter-publish` (or similar).
5. **Scope:** for the **first ever upload** of this project, choose **"Entire account (all projects)"** — the project doesn't exist on PyPI yet, so you can't scope to it. Upload once, then come back and replace this token with one scoped to `pdforienter` only.
6. Copy the token (starts with `pypi-…`) — you only see it once.

### 1.3 Create a TestPyPI token

Repeat the same process on **<https://test.pypi.org/manage/account/token/>** (separate account). You'll use this for the dry run in Section 5.

### 1.4 Store the tokens in `%USERPROFILE%\.pypirc`

Create the file `C:\Users\User\.pypirc` with:

```ini
[distutils]
index-servers =
    pypi
    testpypi

[pypi]
username = __token__
password = pypi-AgEI…YOUR_REAL_PYPI_TOKEN…

[testpypi]
repository = https://test.pypi.org/legacy/
username = __token__
password = pypi-AgEI…YOUR_REAL_TESTPYPI_TOKEN…
```

Username is the literal string `__token__` for both. Save the file, then lock it down so other Windows users can't read it:

```powershell
icacls "$env:USERPROFILE\.pypirc" /inheritance:r /grant:r "$($env:USERNAME):(F)"
```

---

## 2. Pre-flight checks

Run these every time before a release.

```powershell
# Tests must pass
pytest tests/ -v

# Lint + type-check (optional but recommended)
ruff check .
mypy pdforienter
```

Confirm the version number in [pyproject.toml](pyproject.toml#L7) is the version you intend to publish. PyPI rejects re-uploads — if `0.1.0` is already on PyPI, you must bump to `0.1.1` (or `0.2.0`, etc.).

---

## 3. Clean previous build artefacts

Stale files in `dist/` or `build/` will get uploaded by accident. Always clear them first.

```powershell
Remove-Item -Recurse -Force dist, build, pdforienter.egg-info -ErrorAction SilentlyContinue
```

---

## 4. Build the distributions

```powershell
python -m build
```

This produces two files under `dist/`:

- `pdforienter-0.1.0.tar.gz` — sdist (source archive)
- `pdforienter-0.1.0-py3-none-any.whl` — wheel (binary install)

Then validate them — `twine check` confirms the README renders correctly on PyPI:

```powershell
twine check dist/*
```

Both files should print `PASSED`. If the README check fails, fix the markdown before continuing.

---

## 5. Dry run — upload to TestPyPI

```powershell
twine upload --repository testpypi dist/*
```

Verify the listing at:
**<https://test.pypi.org/project/pdforienter/>**

Then test that it installs and imports cleanly in a fresh virtualenv:

```powershell
python -m venv .testenv
.\.testenv\Scripts\Activate.ps1
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ pdforienter
python -c "from pdforienter import run_pipeline; print(run_pipeline)"
pdforienter --help
deactivate
Remove-Item -Recurse -Force .testenv
```

The `--extra-index-url` lets pip resolve `PyMuPDF`, `Pillow`, etc. from real PyPI — TestPyPI doesn't mirror them.

If anything looks wrong on TestPyPI, **bump the version** (e.g. `0.1.0` → `0.1.0.post1`), rebuild, and re-upload. You cannot re-upload the same version number even on TestPyPI.

---

## 6. Upload to real PyPI

Once the TestPyPI run looks correct:

```powershell
twine upload dist/*
```

`twine` will use the `[pypi]` section of `.pypirc` automatically. Watch the output — it'll print the project URL on success:

```
View at:
https://pypi.org/project/pdforienter/0.1.0/
```

---

## 7. Claim the project under the InfinitiBit org

When you upload with a personal account token, the project initially belongs to that account, not to the org. To move it under **infinitibit_gmbh**:

1. Go to **<https://pypi.org/manage/project/pdforienter/settings/>**.
2. Scroll to **"Transfer project"**.
3. Select organisation: `infinitibit_gmbh`.
4. Confirm.

After transfer, replace the broad token from §1.2 with a project-scoped token:

1. **<https://pypi.org/manage/account/token/>** → **Add API token**.
2. Scope: **Project: `pdforienter`**.
3. Update `password` in `.pypirc` under `[pypi]`.
4. Delete the old broad-scope token from the same page.

---

## 8. Verify the public install

In a fresh virtualenv (anywhere on the machine, not necessarily this repo):

```powershell
python -m venv .verify
.\.verify\Scripts\Activate.ps1
pip install pdforienter
pdforienter --help
deactivate
Remove-Item -Recurse -Force .verify
```

---

## 9. Tag the release in git

```powershell
git tag -a v0.1.0 -m "Release 0.1.0"
git push origin v0.1.0
```

---

## 10. Releasing a new version later

```powershell
# 1. Bump version in pyproject.toml (e.g. 0.1.0 -> 0.1.1)
# 2. Commit + tag
git commit -am "Bump version to 0.1.1"
git tag -a v0.1.1 -m "Release 0.1.1"

# 3. Rebuild + re-publish
Remove-Item -Recurse -Force dist, build, pdforienter.egg-info -ErrorAction SilentlyContinue
python -m build
twine check dist/*
twine upload --repository testpypi dist/*   # optional dry run
twine upload dist/*

# 4. Push tag
git push origin main --tags
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `HTTPError: 403 Forbidden` on upload | Bad/missing token | Regenerate token, update `.pypirc` |
| `File already exists` | Re-uploading same version | Bump version in `pyproject.toml` |
| `twine check` reports README errors | Invalid markdown in `README.md` | Fix the markdown; tables and code fences are the usual culprits |
| `ModuleNotFoundError: pdforienter` after install | Package layout misconfigured | Verify `[tool.setuptools.packages.find]` in `pyproject.toml` |
| `pdforienter` command not found after install | Entry point broken | Confirm `[project.scripts]` line is intact |
| Wheel filename has `linux_x86_64` instead of `py3-none-any` | Pure-Python detection failed | Make sure no compiled extensions sneaked in |
