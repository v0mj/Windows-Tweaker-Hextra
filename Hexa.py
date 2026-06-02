"""Hextra launcher.

The actual app is wired through the hextra package so the codebase no longer
has to grow as one giant file.
"""

import ctypes
import os

APP_USER_MODEL_ID = "Hextra.kHrzA.v2"


def _suppress_initial_window():
    if os.name != "nt":
        return
    try:
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
    except Exception:
        pass
    try:
        import ctypes.wintypes

        sw_hide = 0
        startf_use_showwindow = 0x00000001
        startup = ctypes.wintypes.STARTUPINFOW()
        startup.cb = ctypes.sizeof(startup)
        ctypes.windll.kernel32.GetStartupInfoW(ctypes.byref(startup))
        if not (startup.dwFlags & startf_use_showwindow and startup.wShowWindow == sw_hide):
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


_suppress_initial_window()

from hextra.main import run


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
