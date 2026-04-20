# Hextra

A Windows desktop tweaker application with a license-gated backend server.

## Project Structure

```
Hextra/
├── hextra/               # Core application package (PySide6 UI + tweak logic)
│   ├── __init__.py
│   ├── main.py           # Entrypoint wiring
│   ├── legacy.py         # Full application core (tweaks, UI, workers)
│   ├── api.py            # Backend API & update helpers (re-exports)
│   ├── auth.py           # Auth, account state, HWID helpers (re-exports)
│   ├── ops.py            # Tweak catalogue, profiles, state (re-exports)
│   ├── ui.py             # UI shell, pages, dialogs, widgets (re-exports)
│   └── workers.py        # QThread workers (re-exports)
├── icons/                # UI icon assets (PNG/WebP)
├── replica_ui/           # VAX-style shell redesign workspace
│   ├── tokens.py         # Design tokens / colour palette
│   └── README.md
├── server/               # Flask license server backend
│   ├── server.py         # Main server (keys, admin panel, updates, crashes)
│   ├── admin.html        # Admin panel frontend
│   ├── login.html        # Shared admin/reseller login page
│   ├── reseller.html     # Reseller dashboard
│   ├── update.html       # Update management page
│   ├── files.html        # File manager page
│   └── .env.example      # Environment variable reference
├── Hexa.py               # Application launcher
├── HextraBootstrapper.py # Tkinter bootstrapper (downloads latest build)
├── build_hextra_nuitka.bat         # Build Hextra.exe with Nuitka
├── build_hextra_bootstrapper.bat   # Build HextraBootstrapper.exe with Nuitka
├── hextra.ico            # Application icon
└── .gitignore
```

## Requirements

**Client (Hextra)**
- Python 3.11+
- PySide6
- pywin32 (Windows DPAPI auth storage)
- psutil

**Server**
- Python 3.11+
- Flask, Werkzeug

Install server dependencies:
```
pip install flask werkzeug
```

## Server Setup

1. Copy `server/.env.example` to `server/.env` and fill in the values.
2. Run `python server/server.py` (or deploy behind a reverse proxy with gunicorn/uvicorn).

## Building

```bat
# Build main Hextra.exe
build_hextra_nuitka.bat

# Build HextraBootstrapper.exe
build_hextra_bootstrapper.bat
```

> **Note:** Bump `VERSION` in `hextra/legacy.py` before building a release.
