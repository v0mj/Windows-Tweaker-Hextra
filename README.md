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
├── replica_ui/
│   ├── __init__.py
│   └── tokens.py
├── Hexa.py
├── build_hextra_nuitka.bat
├── requirements.txt
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

```bat
build_hextra_nuitka.bat
```

Build output is created in `dist-nuitka/` and is not tracked in Git.

## Local Data

Hextra stores local settings in the current user's home folder:

- `hextra_save.json`
- `hextra_auth.json` from older builds may be removed automatically when local mode is used

No backend is required to run or build the app.
