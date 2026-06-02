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
        input("\nThe application crashed. Press Enter to exit.")
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
