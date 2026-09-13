# Hextra

Hextra is an offline Windows desktop tweaker for performance, gaming, cleanup, restore points, presets, and quick system tools.

The project is local-first and open-source friendly:

- no license server
- no login requirement
- no reseller backend
- no online update backend
- no cloud account storage

## Project Structure

```text
Hextra/
├── .github/
│   └── workflows/
│       └── build-windows.yml     # CI: builds + smoke tests the exe, publishes artifacts
├── assets/
│   └── hextra.ico                # exe/window icon (16..256 px)
├── hextra/
│   ├── __init__.py
│   ├── api.py
│   ├── auth.py
│   ├── legacy.py
│   ├── main.py
│   ├── ops.py
│   ├── theme.py
│   ├── ui.py
│   └── workers.py
├── packaging/
│   ├── build_info.py             # single source of build metadata (version, names)
│   ├── hextra.spec               # PyInstaller spec
│   ├── smoke_test.ps1            # runs a built exe headlessly
│   └── stage_artifact.ps1        # renames + hashes a built exe
├── replica_ui/
│   ├── __init__.py
│   └── tokens.py
├── Hexa.py
├── build_hextra_nuitka.bat
├── build_hextra_pyinstaller.bat
├── requirements.txt
├── requirements-build.txt
└── .gitignore
```

## Design system

`hextra/theme.py` holds the visual language of the shell so every page stays
consistent: colour roles, type scale, spacing, radii, surface/button/input
styles, scrollbars, line icons and the shared widgets (`PageHeader`, `Card`,
`Pill`, `StatTile`, `EmptyState`).

Pages compose those primitives instead of hand-rolling stylesheets; the accent
colour chosen in Settings re-themes the whole shell live.

## Requirements

- Windows 10/11
- Python 3.11+
- PyQt6
- psutil

Install dependencies:

```bat
python -m pip install -r requirements.txt
```

## Run From Source

```bat
python Hexa.py
```

## Build

Two supported backends, both producing a single self-contained `Hextra.exe`
(no Python install needed on the target machine). Install the build extras once:

```bat
python -m pip install -r requirements.txt -r requirements-build.txt
```

### Nuitka (native compile)

```bat
build_hextra_nuitka.bat
```

Output: `dist-nuitka\Hextra.exe`. Nuitka uses the installed Visual Studio Build
Tools, or downloads MinGW64 automatically. Expect a few minutes.

### PyInstaller (fast bundle)

```bat
build_hextra_pyinstaller.bat
```

Output: `dist\Hextra.exe` (`set HEXTRA_ONEFILE=0` for a folder build in
`dist\Hextra\`). The spec lives in `packaging/hextra.spec`.

Both scripts read the version from `hextra/legacy.py` through
`packaging/build_info.py`, embed `assets/hextra.ico`, and finish by smoke
testing the exe they just built.

Build output is not tracked in Git.

### Continuous integration

`.github/workflows/build-windows.yml` runs both backends on a `windows-latest`
runner for every pull request, every push to `master`, and on demand:

1. build the exe
2. smoke test it headlessly (`Hextra.exe --smoke-test`, offscreen Qt)
3. rename it to `Hextra-<version>-windows-x64[-nuitka].exe`, record its SHA-256
4. upload it as a workflow artifact

Grab the artifact from the run page, or from the CLI:

```bash
gh run list --workflow build-windows.yml
gh run download <run-id> --name Hextra-windows-x64-pyinstaller --dir out
```

Pushing a `v*` tag additionally publishes a GitHub Release with both exes and a
combined `SHA256SUMS.txt`.

The executables are unsigned, so SmartScreen warns on first run — choose
"More info" → "Run anyway", or sign them with your own certificate.

## Local Data

Hextra stores local settings in the current user's home folder:

- `hextra_save.json`
- `hextra_auth.json` from older builds may be removed automatically when local mode is used

No backend is required to run or build the app.
