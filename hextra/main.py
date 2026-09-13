"""Application entrypoint wiring."""

import sys
import os
import traceback
import tempfile
from pathlib import Path

_CRASH_LOG = Path(tempfile.gettempdir()) / "hextra_crash.log"


def _log(msg):
    try:
        with _CRASH_LOG.open("a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except Exception:
        pass


def _is_interactive_console():
    """True only when a human could actually press Enter.

    Packaged builds (PyInstaller/Nuitka) ship without a console, where
    ``input()`` raises and would hide the real error behind a second one.
    """
    if getattr(sys, "frozen", False) or globals().get("__compiled__") is not None:
        return False
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except Exception:
        return False


def _pause_before_exit(message):
    """Wait for Enter after a crash, but only when that is possible."""
    if not _is_interactive_console():
        return
    try:
        input(message)
    except Exception:
        pass


def run():
    """Start Hextra through the legacy core during the module split."""
    _log("--- run() entered ---")
    try:
        from . import legacy

        _log("imports OK")
        legacy._boot()
        _log("_boot OK")
        legacy._ensure_elevated_start()
        _log("elevation OK, launching legacy.main()")
        return legacy.main()
    except Exception:
        exc = traceback.format_exc()
        _log("CRASH:\n" + exc)
        try:
            with open("crash.log", "w") as f:
                f.write(exc)
        except Exception:
            pass
        traceback.print_exc()
        _pause_before_exit("\nThe application crashed. Press Enter to exit.")
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
