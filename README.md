# Hextra

Hextra is an offline Windows desktop tweaker for performance, gaming, cleanup, restore points, presets, and quick system tools.

The project is now local-first and open-source friendly:

- no license server
- no login requirement
- no reseller backend
- no online update backend
- no cloud account storage

## Project Structure

```text
Hextra/
├── hextra/
│   ├── __init__.py
│   ├── main.py        # application entrypoint
│   ├── legacy.py      # main UI, tweak logic, workers, local state
│   ├── api.py         # local/offline compatibility helpers
│   ├── auth.py        # local session compatibility helpers
│   ├── ops.py         # tweak catalogue and state re-exports
│   ├── ui.py          # UI re-exports
│   └── workers.py     # worker re-exports
├── icons/             # UI icon assets
├── replica_ui/        # design tokens and UI references
├── Hexa.py            # launcher
├── build_hextra_nuitka.bat
├── hextra.ico
└── .gitignore
```

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

```bat
build_hextra_nuitka.bat
```

> Bump `VERSION` in `hextra/legacy.py` before building a release.

## Local Data

Hextra stores local settings in the current user's home folder:

- `hextra_save.json`
- `hextra_auth.json` from older builds may be removed automatically when local mode is used

No backend is required to run or build the app.
