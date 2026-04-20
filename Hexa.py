"""Hextra launcher.

The actual app is wired through the hextra package so the codebase no longer
has to grow as one giant file.
"""

import ctypes
import os

from hextra.main import run


APP_USER_MODEL_ID = "Hextra.kHrzA.v2"


def _prepare_windows_process():
    if os.name != "nt":
        return
    try:
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
    except Exception:
        pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


if __name__ == "__main__":
    _prepare_windows_process()
    raise SystemExit(run())
