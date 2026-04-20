import sys, subprocess, os, json, platform, traceback, ctypes, math, time, importlib.util, tempfile, base64, shutil
import csv, threading, re
import hashlib
from collections import deque
from datetime import datetime
from pathlib import Path
import shlex
from replica_ui.tokens import REPLICA

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def _env_flag(name):
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}

def _use_native_window_frame():
    if "HEXTRA_FRAMELESS" in os.environ:
        return not _env_flag("HEXTRA_FRAMELESS")
    return _env_flag("HEXTRA_NATIVE_FRAME")

def _is_frozen_build():
    return bool(getattr(sys, "frozen", False) or globals().get("__compiled__") is not None)

APP_USER_MODEL_ID = "Hextra.kHrzA.v2"
VERSION = "1.1.0"
UPDATE_CHECK_INTERVAL_MS = 4 * 60 * 60 * 1000

def _consume_update_cleanup_args():
    paths = []
    try:
        if "--cleanup-update" not in sys.argv:
            return []
        idx = sys.argv.index("--cleanup-update")
        for _ in range(2):
            if idx + 1 < len(sys.argv):
                paths.append(sys.argv.pop(idx + 1))
        del sys.argv[idx]
    except Exception:
        return []
    cleaned = []
    for path in paths:
        if path:
            try:
                cleaned.append(str(Path(path).resolve()))
            except Exception:
                cleaned.append(os.path.abspath(str(path)))
    return cleaned

_PENDING_UPDATE_CLEANUP = _consume_update_cleanup_args()

if __name__ == "__main__" and os.name == "nt":
    try:
        import ctypes as _c
        _c.windll.user32.ShowWindow(_c.windll.kernel32.GetConsoleWindow(), 0)
    except Exception:
        pass
# cmd stuff and requirements
def _boot():
    if "--run" in sys.argv or "--smoke-test" in sys.argv: return
    try:
        import PyQt6, psutil
    except ImportError:
        print("installing requirements")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "PyQt6", "psutil"])
        print("done launching now")
        kw = {}
        if os.name == "nt": kw["creationflags"] = 0x08000000
        launcher = str(PROJECT_ROOT / "Hexa.py")
        subprocess.Popen([sys.executable, launcher, "--run"], **kw)
        sys.exit()

def _write_elevate_log(message):
    try:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        path = Path(tempfile.gettempdir()) / "hextra_elevate.log"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {message}\n")
    except Exception:
        pass

def _resolve_frozen_exe_path():
    compiled = globals().get("__compiled__")
    preferred = []
    fallback = []
    if compiled is not None:
        preferred.append(getattr(compiled, "original_argv0", None))
    preferred.extend(sys.argv[1:4])
    fallback.extend([sys.argv[0], sys.executable])

    def _resolved_candidates(items):
        for candidate in items:
            if not candidate:
                continue
            try:
                resolved = str(Path(candidate).resolve())
            except Exception:
                resolved = os.path.abspath(str(candidate))
            yield resolved

    def _looks_like_real_app_exe(resolved):
        base = os.path.basename(resolved).lower()
        low = resolved.lower()
        if not low.endswith(".exe") or not os.path.exists(resolved):
            return False
        if base == "python.exe":
            return False
        if "\\appdata\\local\\temp\\onefile_" in low:
            return False
        return True

    for resolved in _resolved_candidates(preferred):
        if _looks_like_real_app_exe(resolved):
            return resolved

    for resolved in _resolved_candidates(fallback):
        if resolved.lower().endswith(".exe") and os.path.exists(resolved):
            return resolved

    for candidate in preferred + fallback:
        if not candidate:
            continue
        try:
            resolved = str(Path(candidate).resolve())
        except Exception:
            resolved = os.path.abspath(str(candidate))
        return resolved
    try:
        return str(Path(sys.argv[0]).resolve())
    except Exception:
        return os.path.abspath(str(sys.argv[0]))

def _powershell_quote(text):
    return "'" + str(text or "").replace("'", "''") + "'"

def _launch_elevated_via_powershell(target, argv, workdir):
    creationflags = 0x08000000 if os.name == "nt" else 0
    arg_list = ", ".join(_powershell_quote(arg) for arg in argv)
    command = (
        "$ErrorActionPreference='Stop'; "
        f"Start-Process -FilePath {_powershell_quote(target)} "
        f"-WorkingDirectory {_powershell_quote(workdir)} "
        "-Verb RunAs "
        f"-ArgumentList @({arg_list})"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        creationflags=creationflags,
        timeout=20,
        check=False,
    )
    _write_elevate_log(f"PowerShell runas exit code: {completed.returncode}")
    return completed.returncode == 0

def _launch_elevated_instance(skip_login=False):
    if os.name != "nt":
        return False
    try:
        frozen = _is_frozen_build()
        if frozen:
            target = _resolve_frozen_exe_path()
            argv = [arg for arg in sys.argv[1:] if arg not in {"--run", "--skip-login"}]
        else:
            target = str(Path(sys.executable).resolve())
            argv = [arg for arg in sys.argv if arg not in {"--run", "--skip-login"}]
        if not frozen and "--run" not in argv:
            argv.append("--run")
        params = subprocess.list2cmdline(argv)
        workdir = str(Path(target).resolve().parent)
        _write_elevate_log(f"Attempting runas target={target} params={params}")
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", target, params, workdir, 1)
        _write_elevate_log(f"ShellExecuteW result={result}")
        if result > 32:
            return True
        if frozen:
            return _launch_elevated_via_powershell(target, argv, workdir)
        return False
    except Exception as exc:
        _write_elevate_log(f"Elevation exception: {exc!r}")
        try:
            frozen = _is_frozen_build()
            if frozen:
                target = _resolve_frozen_exe_path()
                workdir = str(Path(target).resolve().parent)
                argv = [arg for arg in sys.argv[1:] if arg not in {"--run", "--skip-login"}]
                return _launch_elevated_via_powershell(target, argv, workdir)
        except Exception as fallback_exc:
            _write_elevate_log(f"PowerShell fallback exception: {fallback_exc!r}")
        return False

def _ensure_elevated_start():
    if os.name != "nt" or "--smoke-test" in sys.argv:
        return
    if is_admin():
        return
    if _launch_elevated_instance(skip_login=False):
        sys.exit()
    _write_elevate_log("Auto-elevate failed; continuing without admin.")

try:
    from PyQt6.QtCore    import Qt, QTimer, QThread, QAbstractAnimation, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup, QSequentialAnimationGroup, pyqtSignal, pyqtProperty, QRectF, QPointF, QPoint, QSize
    from PyQt6.QtGui     import QColor, QPainter, QPen, QBrush, QPainterPath, QRegion, QFont, QFontDatabase, QIcon, QFontMetrics, QPixmap
    from PyQt6.QtWidgets import (QApplication, QWidget, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                                  QFrame, QPushButton, QStackedWidget, QColorDialog, QGridLayout,
                                  QLineEdit, QScrollArea, QFileDialog,
                                  QAbstractButton, QProgressBar, QSizePolicy, QGraphicsOpacityEffect, QGraphicsDropShadowEffect)
    import psutil
except Exception:
    traceback.print_exc(); input("\nPress Enter to exit."); sys.exit(1)

_CPU_PERCENT_SAMPLES = deque(maxlen=4)
_CPU_PERCENT_PRIMED = False

def stable_cpu_percent():
    global _CPU_PERCENT_PRIMED
    try:
        value = float(psutil.cpu_percent(interval=None))
        if not _CPU_PERCENT_PRIMED:
            _CPU_PERCENT_PRIMED = True
            if value <= 0.0:
                value = float(psutil.cpu_percent(interval=0.05))
        if math.isnan(value) or math.isinf(value):
            value = _CPU_PERCENT_SAMPLES[-1] if _CPU_PERCENT_SAMPLES else 0.0
        value = max(0.0, min(100.0, value))
        _CPU_PERCENT_SAMPLES.append(value)
        if not _CPU_PERCENT_SAMPLES:
            return 0.0
        return round(sum(_CPU_PERCENT_SAMPLES) / len(_CPU_PERCENT_SAMPLES), 1)
    except Exception:
        if _CPU_PERCENT_SAMPLES:
            return round(sum(_CPU_PERCENT_SAMPLES) / len(_CPU_PERCENT_SAMPLES), 1)
        return 0.0


class SmoothScrollArea(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._scroll_anim = None
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setWidgetResizable(True)

    def wheelEvent(self, event):
        bar = self.verticalScrollBar()
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if not delta or bar.maximum() <= bar.minimum():
            return super().wheelEvent(event)

        if self._scroll_anim is not None and self._scroll_anim.state() == QAbstractAnimation.State.Running:
            self._scroll_anim.stop()
        if self._scroll_anim is not None:
            self._scroll_anim.deleteLater()

        self._scroll_anim = QPropertyAnimation(bar, b"value", self)
        self._scroll_anim.setDuration(145)
        self._scroll_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self._scroll_anim.setStartValue(bar.value())
        step = -delta * 0.55
        self._scroll_anim.setEndValue(max(bar.minimum(), min(bar.maximum(), bar.value() + step)))
        self._scroll_anim.finished.connect(lambda: setattr(self, "_scroll_anim", None))
        self._scroll_anim.start()

if __name__ == "__main__" and os.name == "nt":
    try:
        # Use a stable app id so Windows uses this exe's icon for the taskbar/thumbnail.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass

_ICO_B64 = "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"

# json state in ~
DATA_FILE = os.path.join(os.path.expanduser("~"), "hextra_save.json")
AUTH_FILE = os.path.join(os.path.expanduser("~"), "hextra_auth.json")
NO_WIN    = 0x08000000 if os.name == "nt" else 0
PROFILES_KEY = "profiles"
SELECTED_TWEAKS_KEY = "selected_tweaks"
ACTIVITY_LOG_KEY = "activity_log"
SNAPSHOTS_KEY = "tweak_snapshots"
BENCHMARKS_KEY = "benchmarks"
QUICK_TOOLS_KEY = "quick_tools"

# colors
BG    = REPLICA["window_bg"]
PANEL = REPLICA["surface"]
CARD  = REPLICA["card"]
SURFACE2 = REPLICA["surface_alt"]
LINE  = REPLICA["line"]
DIM   = REPLICA["text_soft"]
MID   = REPLICA["text_muted"]
MAIN  = REPLICA["text"]
FIXED_UI_BLUE = REPLICA["cyan"]
ACCENT_LIVE = "#e60000"
UI_FONT = "Segoe UI"
UI_FONT_BOLD = "Segoe UI Semibold"
TITLE_FONT = "Segoe UI Semibold"
MONO_FONT = "Consolas"

def _default_value(value):
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    return value

def _load_json_file(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return _default_value(default)

def _write_json_file(path, payload):
    target = Path(path)
    tmp_path = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", delete=False, dir=str(target.parent), encoding="utf-8") as tf:
            json.dump(payload, tf)
            tmp_path = tf.name
        os.replace(tmp_path, target)
        return True
    except Exception:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return False

def _dpapi_transform(raw, *, protect):
    if os.name != "nt":
        return None
    try:
        from ctypes import wintypes
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        source = ctypes.create_string_buffer(raw, len(raw))
        in_blob = DATA_BLOB(len(raw), source)
        out_blob = DATA_BLOB()
        fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
        if protect:
            ok = fn(ctypes.byref(in_blob), "Hextra", None, None, None, 0, ctypes.byref(out_blob))
        else:
            ok = fn(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob))
        if not ok:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(out_blob.pbData)
    except Exception:
        return None

def _default_save_data():
    return {
        "color": "#e60000",
        "snow": True,
        "restore_made": False,
        "custom_themes": {},
        "game_paths": {},
        PROFILES_KEY: {},
        SELECTED_TWEAKS_KEY: [],
        ACTIVITY_LOG_KEY: [],
        SNAPSHOTS_KEY: {},
        BENCHMARKS_KEY: {},
        QUICK_TOOLS_KEY: {},
    }

def save_data(d):
    return _write_json_file(DATA_FILE, d)

def load_data():
    raw = _load_json_file(DATA_FILE, _default_save_data())
    merged = _default_save_data()
    if isinstance(raw, dict):
        for key, value in raw.items():
            default = merged.get(key)
            if isinstance(default, dict) and not isinstance(value, dict):
                continue
            if isinstance(default, list) and not isinstance(value, list):
                continue
            merged[key] = value
    return merged

def _icon_candidate_paths():
    names = ["hextra.ico", "kHrzA.ico", "hextra_icon.ico", "defy_icon.ico"]
    candidates = []
    seen = set()
    frozen_exe = None

    def _add(candidate):
        if not candidate:
            return
        candidate = Path(candidate)
        key = str(candidate).lower()
        if key in seen:
            return
        seen.add(key)
        candidates.append(candidate)

    if _is_frozen_build():
        try:
            frozen_exe = Path(_resolve_frozen_exe_path())
        except Exception:
            frozen_exe = None
    for base in (PROJECT_ROOT, Path(__file__).resolve().parent):
        try:
            for name in names:
                _add(base / name)
        except Exception:
            pass
    try:
        exe_dir = (frozen_exe.parent if frozen_exe is not None else Path(sys.executable).resolve().parent)
        for name in names:
            _add(exe_dir / name)
    except Exception:
        pass
    try:
        meipass = Path(getattr(sys, "_MEIPASS", "")) if hasattr(sys, "_MEIPASS") else None
        if meipass:
            for name in names:
                _add(meipass / name)
    except Exception:
        pass
    if frozen_exe is not None:
        _add(frozen_exe)
    return candidates

def _load_app_icon():
    icon = QIcon()
    for candidate in _icon_candidate_paths():
        try:
            if candidate.is_file():
                loaded = QIcon(str(candidate))
                if not loaded.isNull():
                    return loaded
        except Exception:
            pass
    try:
        ico_data = base64.b64decode(_ICO_B64)
        ico_tf = tempfile.NamedTemporaryFile(delete=False, suffix=".ico")
        ico_tf.write(ico_data)
        ico_tf.close()
        loaded = QIcon(ico_tf.name)
        if not loaded.isNull():
            return loaded
    except Exception:
        pass
    return icon

def _load_pixel_font():
    candidates = []
    try:
        candidates.append(PROJECT_ROOT / "PressStart2P-Regular.ttf")
        candidates.append(Path(__file__).resolve().parent / "PressStart2P-Regular.ttf")
    except Exception:
        pass
    try:
        exe_dir = Path(sys.executable).resolve().parent
        exe_candidate = exe_dir / "PressStart2P-Regular.ttf"
        if exe_candidate not in candidates:
            candidates.append(exe_candidate)
    except Exception:
        pass
    for candidate in candidates:
        try:
            if candidate.is_file():
                font_id = QFontDatabase.addApplicationFont(str(candidate))
                if font_id != -1:
                    families = QFontDatabase.applicationFontFamilies(font_id)
                    if families:
                        return families[0]
        except Exception:
            pass
    return "Press Start 2P"

def _resolve_icon_file():
    for candidate in _icon_candidate_paths():
        try:
            if candidate.is_file() and candidate.suffix.lower() == ".ico":
                return str(candidate)
        except Exception:
            pass
    return None

def _apply_native_window_icon(widget, icon):
    if os.name != "nt" or widget is None:
        return
    try:
        hwnd = int(widget.winId())
    except Exception:
        return
    try:
        icon_path = _resolve_icon_file()
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        GCLP_HICON = -14
        GCLP_HICONSM = -34
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        handle_big = None
        handle_small = None
        if icon_path:
            LR_LOADFROMFILE = 0x0010
            LR_DEFAULTSIZE = 0x0040
            IMAGE_ICON = 1
            handle_big = user32.LoadImageW(None, icon_path, IMAGE_ICON, 256, 256, LR_LOADFROMFILE | LR_DEFAULTSIZE)
            handle_small = user32.LoadImageW(None, icon_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if (not handle_big or not handle_small) and _is_frozen_build():
            exe_path = _resolve_frozen_exe_path()
            if exe_path:
                large = ctypes.c_void_p()
                small = ctypes.c_void_p()
                extracted = shell32.ExtractIconExW(str(exe_path), 0, ctypes.byref(large), ctypes.byref(small), 1)
                if extracted:
                    handle_big = handle_big or large.value
                    handle_small = handle_small or small.value
        if handle_big:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, handle_big)
            try:
                user32.SetClassLongPtrW(hwnd, GCLP_HICON, handle_big)
            except Exception:
                pass
        if handle_small:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, handle_small)
            try:
                user32.SetClassLongPtrW(hwnd, GCLP_HICONSM, handle_small)
            except Exception:
                pass
    except Exception:
        pass

def save_auth(auth):
    if not isinstance(auth, dict):
        return False
    username = (auth.get("username") or "").strip()
    session_token = auth.get("session_token") or ""
    if not username or not session_token:
        return False
    payload = {
        "ts": time.time(),
        "v": 4,
        "mode": "account",
        "username": username,
        "email": (auth.get("email") or "").strip(),
        "session_expires": (auth.get("session_expires") or "").strip(),
    }
    protected = _dpapi_transform(session_token.encode("utf-8"), protect=True)
    if protected is None:
        return False
    payload["session_token_dpapi"] = base64.b64encode(protected).decode("ascii")
    return _write_json_file(AUTH_FILE, payload)

def load_auth():
    try:
        payload = _load_json_file(AUTH_FILE, {})
        if not isinstance(payload, dict):
            return {}
        if payload.get("mode") == "account" or payload.get("session_token_dpapi"):
            if payload.get("password_dpapi") or payload.get("password_b64"):
                return {}
            auth = {
                "mode": "account",
                "username": (payload.get("username") or "").strip(),
                "email": (payload.get("email") or "").strip(),
                "session_token": "",
                "session_expires": (payload.get("session_expires") or "").strip(),
            }
            if payload.get("session_token_dpapi"):
                raw = base64.b64decode(payload["session_token_dpapi"])
                plain = _dpapi_transform(raw, protect=False)
                auth["session_token"] = plain.decode("utf-8") if plain else ""
            if auth.get("session_expires"):
                try:
                    expires = datetime.fromisoformat(auth["session_expires"].replace("Z", "+00:00"))
                    if expires.timestamp() <= time.time():
                        return {}
                except Exception:
                    return {}
            return auth if auth["username"] and auth["session_token"] else {}
        return {}
    except Exception:
        return {}

def clear_auth():
    try:
        if os.path.exists(AUTH_FILE):
            os.remove(AUTH_FILE)
    except Exception:
        pass

def has_restore_point():
    return load_data().get("restore_made", False)

def is_snow_on():
    return load_data().get("snow", True)

def set_snow(v):
    d = load_data(); d["snow"] = v; save_data(d)

def set_restore_made(v):
    d = load_data(); d["restore_made"] = v; save_data(d)

def is_admin():
    try:    return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except: return getattr(os, "geteuid", lambda: 1)() == 0

def create_restore_point():
    if os.name != "nt":
        return False, "System Restore is only available on Windows."
    script = (
        '$ErrorActionPreference="Stop";'
        'Enable-ComputerRestore -Drive "C:\\" | Out-Null;'
        'Checkpoint-Computer -Description "Hextra" -RestorePointType MODIFY_SETTINGS | Out-Null;'
        'Write-Output "OK"'
    )
    result = _run_process(["powershell", "-NoProfile", "-Command", script], timeout=180, return_output=True)
    if result == "OK":
        set_restore_made(True)
        return True, "Restore point created successfully."
    return False, result or "Could not create a restore point."

def clean_ram():
    if os.name != "nt":
        return "ram cleaner unavailable"
    try:
        import time
        import ctypes as _ctypes
        from ctypes import c_int, c_uint32, c_void_p
        kernel32 = _ctypes.windll.kernel32
        psapi = _ctypes.windll.psapi
        open_process = kernel32.OpenProcess
        open_process.argtypes = [c_uint32, c_int, c_uint32]
        open_process.restype = c_void_p

        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [c_void_p]
        close_handle.restype = c_int

        empty_working_set = psapi.EmptyWorkingSet
        empty_working_set.argtypes = [c_void_p]
        empty_working_set.restype = c_int

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        PROCESS_SET_QUOTA = 0x0100
        access = PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SET_QUOTA

        before = psutil.virtual_memory().available
        trimmed = 0
        attempted = 0

        for process in psutil.process_iter(["pid"]):
            pid = int(process.info["pid"])
            handle = open_process(access, 0, pid)
            if not handle:
                continue
            attempted += 1
            try:
                if empty_working_set(handle):
                    trimmed += 1
            finally:
                close_handle(handle)

        time.sleep(0.35)
        after = psutil.virtual_memory().available
        freed_mb = max(0, int((after - before) / (1024 * 1024)))
        available_gb = after / (1024**3)
        if freed_mb > 0:
            return f"trimmed {trimmed}/{attempted} processes | freed about {freed_mb} MB | available {available_gb:.1f} GB"
        return f"trimmed {trimmed}/{attempted} processes | available {available_gb:.1f} GB"
    except Exception as exc:
        return f"ram cleaner failed: {exc}"

def clean_temp():
    try:
        root = Path(os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir())
        if not root.exists():
            return "temp folder not found"
        removed = 0
        failed = 0
        for child in list(root.iterdir()):
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
                removed += 1
            except Exception:
                failed += 1
        if removed == 0 and failed == 0:
            return "temp folder already clean"
        if failed:
            return f"Temp cleaned ({removed} items removed, {failed} skipped)."
        return f"Temp cleaned ({removed} items removed)."
    except Exception as exc:
        return f"temp cleaner failed: {exc}"

class _RamCleanerWorker(QThread):
    result = pyqtSignal(str)

    def run(self):
        self.result.emit(clean_ram())

_GPU_CACHE = {"ts": 0.0, "val": 0.0}
_GPU_LOCK = threading.Lock()
_GPU_THREAD_STARTED = False

def _read_gpu_percent_once():
    if os.name != "nt":
        return 0.0
    try:
        kw = {"stderr": subprocess.DEVNULL, "text": True, "encoding": "utf-8", "errors": "ignore", "timeout": 2.5}
        if os.name == "nt":
            kw["creationflags"] = NO_WIN
        out = subprocess.check_output(
            ["typeperf", r"\GPU Engine(*)\Utilization Percentage", "-sc", "1"],
            **kw
        )
        lines = [ln.strip() for ln in out.splitlines() if ln.strip().startswith('"')]
        if len(lines) >= 2:
            row = next(csv.reader([lines[-1]]), [])
            vals = []
            for item in row[1:]:
                try:
                    vals.append(float(item))
                except Exception:
                    pass
            if vals:
                return max(0.0, min(100.0, max(vals)))
    except Exception:
        pass
    return 0.0

def _gpu_sampler_loop():
    while True:
        started = time.time()
        val = _read_gpu_percent_once()
        with _GPU_LOCK:
            _GPU_CACHE["ts"] = time.time()
            _GPU_CACHE["val"] = val
        time.sleep(max(0.4, 1.5 - (time.time() - started)))

def start_gpu_sampler():
    global _GPU_THREAD_STARTED
    if os.name != "nt" or _GPU_THREAD_STARTED:
        return
    _GPU_THREAD_STARTED = True
    threading.Thread(target=_gpu_sampler_loop, name="gpu-sampler", daemon=True).start()

def gpu_percent():
    start_gpu_sampler()
    with _GPU_LOCK:
        return _GPU_CACHE["val"]

def _subprocess_defaults(timeout, *, text=True):
    kw = {"capture_output": True, "timeout": timeout}
    if text:
        kw.update({"text": True, "encoding": "utf-8", "errors": "ignore"})
    if os.name == "nt":
        kw["creationflags"] = NO_WIN
    return kw

def _run_process(args, *, timeout=120, return_output=False):
    completed = subprocess.run(args, **_subprocess_defaults(timeout))
    output = (completed.stdout or "").strip()
    error = (completed.stderr or "").strip()
    if completed.returncode != 0:
        detail = error or output or f"exit code {completed.returncode}"
        return f"command failed: {detail}"
    if return_output:
        return output or None
    return None

def _split_command(cmd):
    try:
        return shlex.split(cmd, posix=False)
    except ValueError as exc:
        raise RuntimeError(f"invalid command syntax: {exc}") from exc

def run_cmd(c):
    try:
        if callable(c):
            return c()
        if not isinstance(c, str):
            return "unsupported command type"
        if c.startswith("ps:"):
            return _run_process(["powershell", "-NoProfile", "-Command", c[3:].strip()])
        elif c.startswith("py:"):
            expr = c[3:].strip()
            if expr == "clean_ram()":
                return clean_ram()
            if expr == "clean_temp()":
                return clean_temp()
            return f"unsupported python action: {expr}"
        elif c.startswith("appx:"):
            return _run_process([
                "powershell", "-NoProfile", "-Command",
                f"Get-AppxPackage -AllUsers '{c[5:].strip()}' 2>$null | Remove-AppxPackage -ErrorAction SilentlyContinue"
            ], timeout=180)
        else:
            args = _split_command(c)
            if not args:
                return None
            timeout = 60 if args[0].lower() == "cmd" else 120
            return _run_process(args, timeout=timeout)
    except FileNotFoundError as exc:
        return f"command missing: {exc}"
    except Exception as exc:
        return f"command failed: {exc}"

def _command_failed(output):
    if not isinstance(output, str):
        return False
    low = output.strip().lower()
    return low.startswith((
        "command failed:",
        "command missing:",
        "unsupported ",
        "ram cleaner failed:",
        "ram cleaner unavailable",
        "temp cleaner failed:",
    ))

def ps_q(cmd):
    try:
        out = _run_process(["powershell", "-NoProfile", "-Command", cmd], timeout=8, return_output=True)
        if isinstance(out, str) and out.startswith("command failed"):
            return "N/A"
        ls = [l.strip() for l in (out or "").split("\n") if l.strip()]
        return ls[0] if ls else "N/A"
    except Exception:
        return "N/A"

def accent_vars(ac):
    c = QColor(ac); h, s, l, _ = c.getHslF()
    dim  = QColor.fromHslF(h, s * 0.42, max(0.16, l * 0.42)).name()
    glow = QColor.fromHslF(h, s * 0.92, min(0.90, max(0.62, l * 1.18))).name()
    ghost = QColor.fromHslF(h, s * 0.32, 0.10).name()
    return dim, glow, ghost

def _rgba(color, alpha):
    c = QColor(color)
    a = max(0, min(255, int(alpha)))
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {a})"

def apply_glass_shadow(widget, accent=None, *, blur=34, y=14, alpha=54):
    if widget is None:
        return
    if widget.graphicsEffect() is not None:
        widget.setGraphicsEffect(None)

def liquid_panel_style(ac, radius=22):
    return (
        "QFrame{"
        f"background:{PANEL};"
        f"border:1px solid {LINE};"
        f"border-radius:{radius}px;"
        "}"
    )

def replica_card_style(border=None, radius=14, alt=False):
    surface = SURFACE2 if alt else CARD
    edge_base = border or LINE
    return (
        "QFrame{"
        f"background:{surface};"
        f"border:1px solid {edge_base};"
        f"border-radius:{radius}px;"
        "}"
        "QFrame:hover{"
        f"background:{SURFACE2};"
        f"border-color:{LINE};"
        "}"
    )

def replica_input_style(ac):
    return (
        "QLineEdit{"
        f"background:{PANEL};"
        f"color:{MAIN};border:1px solid {LINE};"
        f"border-radius:4px;font:10pt '{UI_FONT}';padding:0 12px;selection-background-color:{ac};"
        "}"
        "QLineEdit:hover{"
        f"border-color:{LINE};"
        "}"
        "QLineEdit:focus{"
        f"border-color:{_rgba('#ffffff', 32)};"
        f"background:{PANEL};"
        "}"
    )

def replica_badge_style(kind="cyan", *, font_px=8, padding_v=2, padding_h=7, radius=2, letter_spacing=0.7):
    mapping = {
        "green": (f"rgba(40,200,64,31)", "#28c840", "transparent"),
        "red": (f"rgba(230,0,0,31)", ACCENT_LIVE, "transparent"),
        "amber": (f"rgba(240,240,240,15)", MID, "transparent"),
        "gold": (f"rgba(240,240,240,15)", MID, "transparent"),
        "cyan": (f"rgba(240,240,240,15)", MID, "transparent"),
        "info": (f"rgba(240,240,240,15)", MID, "transparent"),
        "ok": (f"rgba(40,200,64,31)", "#28c840", "transparent"),
        "warn": (f"rgba(230,0,0,31)", ACCENT_LIVE, "transparent"),
    }
    bg, fg, edge = mapping.get(kind, (f"rgba(240,240,240,15)", MID, "transparent"))
    return (
        "QLabel{"
        f"background:{bg};"
        f"color:{fg};border:1px solid {edge};border-radius:{radius}px;"
        f"padding:{padding_v}px {padding_h}px;font:500 {font_px}px '{MONO_FONT}';letter-spacing:{letter_spacing}px;"
        "}"
    )

def replica_section_caption(color):
    return f"color:{DIM};font:500 9px '{MONO_FONT}';letter-spacing:1.4px;text-transform:uppercase;border:none;background:transparent;"

def replica_title_style():
    return f"color:{MAIN};font:300 18pt '{UI_FONT}';border:none;background:transparent;"

def replica_hero_style(ac):
    return (
        "QFrame{"
        f"background:{CARD};"
        f"border:1px solid {LINE};"
        "border-radius:4px;"
        "}"
    )

def _api_base_urls():
    urls = ["https://oltrski.de"]
    seen = []
    for url in urls:
        if url.lower().startswith("https://") and url not in seen:
            seen.append(url)
    return seen

def _auth_headers(auth=None):
    auth = auth if isinstance(auth, dict) else load_auth()
    token = (auth.get("session_token") or "").strip()
    username = (auth.get("username") or "").strip()
    if not token or not username:
        return {}
    return {
        "Authorization": f"Bearer {token}",
        "X-Hextra-User": username,
        "X-Hextra-HWID": _current_hwid(),
    }

def _post_json(path, payload, *, timeout=8, auth=None):
    import urllib.request, urllib.error, ssl
    data = json.dumps(payload or {}).encode("utf-8")
    ctx = ssl.create_default_context()
    for base in _api_base_urls():
        try:
            req = urllib.request.Request(f"{base}{path}", data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            for key, value in _auth_headers(auth).items():
                req.add_header(key, value)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                return json.loads(exc.read().decode("utf-8"))
            except Exception:
                return {"success": False, "message": f"Server returned HTTP {exc.code}", "locked": True}
        except Exception:
            continue
    return None

def _fetch_json(path, *, timeout=6, auth=None):
    import urllib.request, urllib.error, ssl
    ctx = ssl.create_default_context()
    for base in _api_base_urls():
        try:
            req = urllib.request.Request(f"{base}{path}", method="GET")
            for key, value in _auth_headers(auth).items():
                req.add_header(key, value)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                return json.loads(exc.read().decode("utf-8"))
            except Exception:
                return {"success": False, "message": f"Server returned HTTP {exc.code}", "locked": True}
        except Exception:
            continue
    return None

def _version_tuple(v):
    try:
        return tuple(int(part) for part in str(v or "0.0.0").split("."))
    except Exception:
        return (0, 0, 0)

def _current_version():
    return str(VERSION or "0.0.0")

def _can_self_update():
    if os.name != "nt" or not _is_frozen_build():
        return False
    try:
        exe_path = _resolve_frozen_exe_path()
    except Exception:
        return False
    return bool(exe_path and exe_path.lower().endswith(".exe") and os.path.isfile(exe_path))

def _safe_update_filename(name):
    raw = Path(str(name or "")).name.strip()
    if not raw:
        raw = "Hextra.exe"
    raw = re.sub(r"[^A-Za-z0-9._ -]", "_", raw)
    if not Path(raw).suffix:
        raw += ".exe"
    return raw

def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 256), b""):
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()

def _delete_path_quietly(path):
    try:
        if not path:
            return True
        if os.path.isdir(path):
            shutil.rmtree(path)
            return True
        if os.path.exists(path):
            os.remove(path)
        return True
    except Exception:
        return False

def _cleanup_update_artifacts(paths):
    pending = [path for path in (paths or []) if path]
    if not pending:
        return
    time.sleep(2.0)
    for _ in range(12):
        remaining = []
        for path in pending:
            if not _delete_path_quietly(path):
                remaining.append(path)
        if not remaining:
            return
        pending = remaining
        time.sleep(1.0)

def _start_update_cleanup_thread(paths):
    if not paths:
        return
    threading.Thread(
        target=_cleanup_update_artifacts,
        args=(list(paths),),
        name="hextra-update-cleanup",
        daemon=True,
    ).start()

def _download_api_file(path, dest_path, *, timeout=45, progress=None, auth=None):
    import urllib.request, urllib.error, ssl
    ctx = ssl.create_default_context()
    last_error = None
    if progress:
        try:
            progress(0, 0)
        except Exception:
            pass
    for base in _api_base_urls():
        try:
            req = urllib.request.Request(f"{base}{path}", method="GET")
            for key, value in _auth_headers(auth).items():
                req.add_header(key, value)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp, open(dest_path, "wb") as fh:
                total = 0
                transferred = 0
                try:
                    total = int(resp.headers.get("Content-Length", "0") or 0)
                except Exception:
                    total = 0
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    fh.write(chunk)
                    transferred += len(chunk)
                    if progress:
                        progress(transferred, total)
            return {"success": True, "base": base, "bytes": transferred}
        except urllib.error.HTTPError as exc:
            last_error = exc
            try:
                body = json.loads(exc.read().decode("utf-8"))
                if isinstance(body, dict) and body.get("message"):
                    last_error = body.get("message")
            except Exception:
                pass
            _delete_path_quietly(dest_path)
        except Exception as exc:
            last_error = exc
            _delete_path_quietly(dest_path)
    return {"success": False, "message": str(last_error or "Could not reach the update server.")}

def _write_update_helper(update_path, target_path):
    old_path = f"{target_path}.previous"
    helper_path = Path(tempfile.gettempdir()) / f"hextra-update-{int(time.time())}.cmd"
    script = (
        "@echo off\r\n"
        "setlocal enableextensions\r\n"
        f"set \"TARGET={target_path}\"\r\n"
        f"set \"SOURCE={update_path}\"\r\n"
        f"set \"OLD={old_path}\"\r\n"
        "ping 127.0.0.1 -n 3 >nul\r\n"
        "for /l %%I in (1,1,90) do (\r\n"
        "  if exist \"%TARGET%\" (\r\n"
        "    move /y \"%TARGET%\" \"%OLD%\" >nul 2>nul\r\n"
        "    if not errorlevel 1 goto moved\r\n"
        "  ) else (\r\n"
        "    goto moved\r\n"
        "  )\r\n"
        "  timeout /t 1 /nobreak >nul\r\n"
        ")\r\n"
        "exit /b 1\r\n"
        ":moved\r\n"
        "for /l %%I in (1,1,90) do (\r\n"
        "  move /y \"%SOURCE%\" \"%TARGET%\" >nul 2>nul\r\n"
        "  if not errorlevel 1 goto launch\r\n"
        "  timeout /t 1 /nobreak >nul\r\n"
        ")\r\n"
        "exit /b 1\r\n"
        ":launch\r\n"
        "start \"\" \"%TARGET%\" --cleanup-update \"%OLD%\" \"%~f0\"\r\n"
        "exit /b 0\r\n"
    )
    helper_path.write_text(script, encoding="utf-8", newline="")
    return str(helper_path)

def _prepare_update_install(meta, progress=None, auth=None):
    if not _can_self_update():
        return {"success": False, "message": "Auto update only works inside the built Windows EXE."}
    version = str((meta or {}).get("version", "") or "").strip() or "latest"
    filename = _safe_update_filename((meta or {}).get("filename", "Hextra.exe"))
    if Path(filename).suffix.lower() != ".exe":
        return {"success": False, "message": "The published update is not a Windows executable."}
    stamp = re.sub(r"[^0-9A-Za-z._-]", "-", version) or "latest"
    download_path = str(Path(tempfile.gettempdir()) / f"hextra-update-{stamp}-{os.getpid()}.exe")
    download = _download_api_file("/update/download", download_path, progress=progress, auth=auth)
    if not download.get("success"):
        return {"success": False, "message": download.get("message", "Could not download the update.")}
    expected_size = 0
    try:
        expected_size = int((meta or {}).get("size") or 0)
    except Exception:
        expected_size = 0
    actual_size = 0
    try:
        actual_size = os.path.getsize(download_path)
    except Exception:
        actual_size = 0
    if expected_size and actual_size and expected_size != actual_size:
        _delete_path_quietly(download_path)
        return {"success": False, "message": "Downloaded update size did not match the published build."}
    expected_checksum = str((meta or {}).get("checksum", "") or "").strip().lower()
    if expected_checksum:
        actual_checksum = _sha256_file(download_path).lower()
        if actual_checksum != expected_checksum:
            _delete_path_quietly(download_path)
            return {"success": False, "message": "Downloaded update failed checksum verification."}
    target_path = _resolve_frozen_exe_path()
    helper_path = _write_update_helper(download_path, target_path)
    return {
        "success": True,
        "version": version,
        "download_path": download_path,
        "target_path": target_path,
        "helper_path": helper_path,
        "size": actual_size,
    }

def _launch_update_helper(helper_path):
    try:
        subprocess.Popen(["cmd", "/c", helper_path], creationflags=NO_WIN, close_fds=True)
        return True, "Update ready. Hextra will restart to finish installing."
    except Exception as exc:
        return False, f"Could not launch the update helper: {exc}"

def _format_bytes(num_bytes):
    try:
        size = float(num_bytes or 0)
    except Exception:
        return "0 B"
    units = ["B", "KB", "MB", "GB"]
    idx = 0
    while size >= 1024.0 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(size)} {units[idx]}"
    return f"{size:.1f} {units[idx]}"

def _current_hwid():
    import uuid
    return str(uuid.getnode())

def _account_payload(username, session_token, email="", session_expires=""):
    return {
        "mode": "account",
        "username": (username or "").strip(),
        "session_token": session_token or "",
        "session_expires": session_expires or "",
        "email": (email or "").strip(),
    }

def client_register(username, email, password, remember=False):
    resp = _post_json("/client/register", {
        "username": (username or "").strip(),
        "email": (email or "").strip(),
        "password": password or "",
        "remember": bool(remember),
        "hwid": _current_hwid(),
    }, timeout=10)
    if not isinstance(resp, dict):
        return False, "Could not reach the account server right now.", {}
    if not resp.get("success") or not resp.get("session_token"):
        return False, resp.get("message", "Login failed"), resp
    return resp.get("success", False), resp.get("message", "Unknown error"), resp

def client_login(username, password, remember=False):
    resp = _post_json("/client/login", {
        "username": (username or "").strip(),
        "password": password or "",
        "remember": bool(remember),
        "hwid": _current_hwid(),
    }, timeout=10)
    if not isinstance(resp, dict):
        return False, "Could not reach the account server right now.", {}
    if not resp.get("success") or not resp.get("session_token"):
        return False, resp.get("message", "Login failed"), resp
    return resp.get("success", False), resp.get("message", "Unknown error"), resp

def client_status(auth):
    auth = auth if isinstance(auth, dict) else {}
    resp = _post_json("/client/status", {
        "username": (auth.get("username") or "").strip(),
        "session_token": auth.get("session_token") or "",
        "hwid": _current_hwid(),
    }, timeout=8, auth=auth)
    return resp if isinstance(resp, dict) else {}

def client_redeem(auth, key):
    auth = auth if isinstance(auth, dict) else {}
    resp = _post_json("/client/redeem", {
        "username": (auth.get("username") or "").strip(),
        "session_token": auth.get("session_token") or "",
        "key": (key or "").strip(),
        "hwid": _current_hwid(),
    }, timeout=10, auth=auth)
    if not isinstance(resp, dict):
        return False, "Could not reach the account server right now.", {}
    return resp.get("success", False), resp.get("message", "Unknown error"), resp

def _account_days_left_text(resp):
    if not isinstance(resp, dict) or not resp:
        return "Backend unavailable", "red"
    if resp.get("success") is False:
        return str(resp.get("message") or "Account error"), "red"
    days_left = int(resp.get("days_left") or 0)
    licensed = bool(resp.get("licensed"))
    if not licensed:
        return "No active plan", "amber"
    label = "day left" if days_left == 1 else "days left"
    tone = "red" if days_left <= 3 else "amber" if days_left <= 14 else "cyan"
    return f"{days_left} {label}", tone

def account_has_active_plan(auth=None):
    auth = auth if isinstance(auth, dict) else load_auth()
    if auth.get("mode") != "account" or not auth.get("username") or not auth.get("session_token"):
        return False, {}
    resp = client_status(auth)
    if not isinstance(resp, dict):
        return False, {}
    return bool(resp.get("success") and resp.get("licensed")), resp

# accent presets
THEMES = {
    "red":   "#e60000",
    "white": "#f5f7fa",
    "gray":  "#a1a1aa",
    "blue":  "#3b82f6",
    "green": "#10b981",
    "amber": "#f59e0b",
    "rainbow": "rainbow",
}

CUSTOM_THEMES_KEY = "custom_themes"
GAME_PATHS_KEY = "game_paths"  # settings paths

def load_custom_themes():
    return load_data().get(CUSTOM_THEMES_KEY, {})

def save_custom_theme(name, color):
    d = load_data()
    ct = d.get(CUSTOM_THEMES_KEY, {}); ct[name.lower().strip()] = color
    d[CUSTOM_THEMES_KEY] = ct; save_data(d)

def delete_custom_theme(name):
    d = load_data()
    ct = d.get(CUSTOM_THEMES_KEY, {}); ct.pop(name, None)
    d[CUSTOM_THEMES_KEY] = ct; save_data(d)

def load_game_paths():
    return load_data().get(GAME_PATHS_KEY, {})

def save_game_path(game_key, folder_path):
    d = load_data()
    gp = dict(d.get(GAME_PATHS_KEY, {}))
    fp = (folder_path or "").strip()
    if fp:
        gp[game_key] = fp
    else:
        gp.pop(game_key, None)
    d[GAME_PATHS_KEY] = gp
    save_data(d)

def _save_named_block(key, value):
    d = load_data()
    d[key] = value
    save_data(d)

def load_selected_tweaks():
    return set(load_data().get(SELECTED_TWEAKS_KEY, []))

def set_selected_tweaks(selected):
    _save_named_block(SELECTED_TWEAKS_KEY, sorted(set(selected)))

def set_tweak_selected(tweak_key, enabled):
    selected = load_selected_tweaks()
    if enabled:
        selected.add(tweak_key)
    else:
        selected.discard(tweak_key)
    set_selected_tweaks(selected)

def load_profiles():
    profiles = load_data().get(PROFILES_KEY, {})
    return profiles if isinstance(profiles, dict) else {}

def save_profile(name, tweak_keys):
    clean = (name or "").strip()
    if not clean:
        return False, "Profile name is required."
    profiles = load_profiles()
    profiles[clean] = {
        "tweaks": sorted(set(tweak_keys)),
        "updated_at": time.time(),
    }
    _save_named_block(PROFILES_KEY, profiles)
    return True, f"Saved profile '{clean}'."

def delete_profile(name):
    profiles = load_profiles()
    if name in profiles:
        profiles.pop(name, None)
        _save_named_block(PROFILES_KEY, profiles)

def load_activity_log():
    data = load_data().get(ACTIVITY_LOG_KEY, [])
    return data if isinstance(data, list) else []

def append_activity(kind, title, detail="", status="info", *, tweak_key="", category="", extra=None):
    log = load_activity_log()
    item = {
        "ts": time.time(),
        "kind": kind,
        "title": title,
        "detail": detail,
        "status": status,
        "tweak_key": tweak_key,
        "category": category,
        "extra": extra or {},
    }
    log.append(item)
    _save_named_block(ACTIVITY_LOG_KEY, log[-250:])

def clear_activity_log():
    _save_named_block(ACTIVITY_LOG_KEY, [])

def load_snapshots():
    data = load_data().get(SNAPSHOTS_KEY, {})
    return data if isinstance(data, dict) else {}

def save_snapshot(tweak_key, payload):
    snaps = load_snapshots()
    snaps[tweak_key] = payload
    _save_named_block(SNAPSHOTS_KEY, snaps)

def remove_snapshot(tweak_key):
    snaps = load_snapshots()
    if tweak_key in snaps:
        snaps.pop(tweak_key, None)
        _save_named_block(SNAPSHOTS_KEY, snaps)

def load_benchmarks():
    data = load_data().get(BENCHMARKS_KEY, {})
    return data if isinstance(data, dict) else {}

def save_benchmark_snapshot(slot, payload):
    benches = load_benchmarks()
    benches[slot] = payload
    _save_named_block(BENCHMARKS_KEY, benches)

def export_settings_file(path):
    payload = load_data()
    payload["_exported_at"] = time.time()
    return _write_json_file(path, payload)

def import_settings_file(path):
    payload = _load_json_file(path, {})
    if not isinstance(payload, dict):
        return False, "Settings file is invalid."
    merged = _default_save_data()
    merged.update(payload)
    if save_data(merged):
        return True, "Settings imported."
    return False, "Could not import settings."

def collect_benchmark_snapshot():
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("C:\\" if os.name == "nt" else "/")
    nio = psutil.net_io_counters()
    return {
        "ts": time.time(),
        "cpu_percent": stable_cpu_percent(),
        "ram_percent": mem.percent,
        "ram_used_gb": round(mem.used / 1073741824, 2),
        "ram_total_gb": round(mem.total / 1073741824, 2),
        "gpu_percent": round(gpu_percent(), 1),
        "processes": len(psutil.pids()),
        "disk_used_gb": round(disk.used / 1073741824, 1),
        "disk_free_gb": round(disk.free / 1073741824, 1),
        "boot_time": psutil.boot_time(),
        "uptime_s": int(time.time() - psutil.boot_time()),
        "net_sent_mb": round(nio.bytes_sent / (1024 * 1024), 1),
        "net_recv_mb": round(nio.bytes_recv / (1024 * 1024), 1),
    }

def quick_tool_entries():
    return [
        {"id": "quick::ram-boost", "category": "Quick Tools", "name": "RAM Boost", "cmds": ["py:clean_ram()"], "desc": "Trim working sets and refresh available memory.", "restart": ""},
        {"id": "quick::flush-dns", "category": "Quick Tools", "name": "Flush DNS", "cmds": ["ipconfig /flushdns"], "desc": "Refresh cached DNS records.", "restart": ""},
        {"id": "quick::clean-temp", "category": "Quick Tools", "name": "Clean Temp", "cmds": ["py:clean_temp()"], "desc": "Clear the current user's temp files.", "restart": ""},
        {"id": "quick::clear-shaders", "category": "Quick Tools", "name": "Clear Shaders", "cmds": ['cmd /c del /f /s /q "%LOCALAPPDATA%\\D3DSCache\\*" 2>nul'], "desc": "Clear DirectX shader cache.", "restart": "relaunch app"},
        {"id": "quick::network-reset", "category": "Quick Tools", "name": "Network Reset", "cmds": ["netsh winsock reset", "netsh int ip reset"], "desc": "Reset core network stacks.", "restart": "restart"},
    ]

# scan for installs
def detect_games():
    detected = set()
    local = os.environ.get("LOCALAPPDATA",
            os.path.join(os.path.expanduser("~"), "AppData", "Local"))
    pf86  = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    pf    = os.environ.get("PROGRAMFILES",      r"C:\Program Files")
    steam86 = os.path.join(pf86, "Steam", "steamapps", "common")
    steam   = os.path.join(pf,   "Steam", "steamapps", "common")
    ov = load_game_paths()
    checks = {
        "Roblox": [
            os.path.join(local, "Roblox", "Versions"),
            os.path.join(local, "Roblox"),
        ],
        "FiveM": [
            os.path.join(local, "FiveM", "FiveM.app"),
            os.path.join(local, "FiveM"),
        ],
        "Valorant": [
            os.path.join(pf,  "Riot Games", "VALORANT"),
            os.path.join(pf86,"Riot Games", "VALORANT"),
            os.path.join(local, "VALORANT"),
        ],
        "CS2": [
            os.path.join(steam86, "Counter-Strike Global Offensive",
                         "game", "bin", "win64", "cs2.exe"),
            os.path.join(steam,   "Counter-Strike Global Offensive",
                         "game", "bin", "win64", "cs2.exe"),
        ],
        "Minecraft": [
            os.path.join(local, ".minecraft"),
            os.path.join(AD, ".minecraft"),
        ],
        "Fortnite": [
            os.path.join(local, "FortniteGame"),
            os.path.join(pf, "Epic Games", "Fortnite"),
            os.path.join(pf86, "Epic Games", "Fortnite"),
        ],
        "Apex": [
            os.path.join(steam86, "Apex Legends"),
            os.path.join(steam, "Apex Legends"),
            os.path.join(pf, "Origin Games", "Apex Legends"),
            os.path.join(pf86, "Origin Games", "Apex Legends"),
        ],
    }
    for game, paths in checks.items():
        custom = (ov.get(game, "") or "").strip()
        cand = list(paths)
        if custom:
            cand.insert(0, custom)
            if game == "CS2":
                cand.insert(1, os.path.join(custom, "game", "bin", "win64", "cs2.exe"))
        if any(os.path.exists(p) for p in cand):
            detected.add(game)
    return detected

# default game paths (fallback)
LA=os.getenv("LOCALAPPDATA",""); AD=os.getenv("APPDATA",""); ST="C:\\Program Files (x86)\\Steam\\steamapps\\common"
FM=os.path.join(LA,"FiveM","FiveM.app"); FN_CFG=os.path.join(LA,"FortniteGame","Saved","Config","WindowsClient")
VAL=os.path.join(LA,"VALORANT"); MC=os.path.join(AD,".minecraft")
CS2_CFG=os.path.join(ST,"Counter-Strike Global Offensive","game","csgo","cfg")
APEX_CFG=os.path.join(ST,"Apex Legends","cfg")
MC_OPT=os.path.join(MC,"options.txt")
MC_SRV=os.path.join(MC,"servers.dat")
FN_INI=os.path.join(FN_CFG,"GameUserSettings.ini")
def mc_set(key,val):
    return f'ps:$f="{MC_OPT}";if(Test-Path $f){{$c=Get-Content $f;$n=$c -replace "^{key}:.*","{key}:{val}";if($c -eq $n){{$n+="{key}:{val}"}};$n|Set-Content $f}}'
def fn_set(key,val):
    return f'ps:$f="{FN_INI}";if(Test-Path $f){{(Get-Content $f) -replace "{key}=.*","{key}={val}" | Set-Content $f}}'

# overrides from folders
def resolved_roblox_root():
    p = (load_game_paths().get("Roblox", "") or "").strip()
    if p and os.path.isdir(p):
        return os.path.normpath(p)
    return os.path.join(LA, "Roblox")

def resolved_fivem_root():
    p = (load_game_paths().get("FiveM", "") or "").strip()
    if p and os.path.isdir(p):
        return os.path.normpath(p)
    return os.path.join(LA, "FiveM")

def resolved_minecraft_dir():
    p = (load_game_paths().get("Minecraft", "") or "").strip()
    if p and os.path.isdir(p):
        return os.path.normpath(p)
    if os.path.isdir(MC):
        return MC
    alt = os.path.join(LA, ".minecraft")
    return alt if os.path.isdir(alt) else MC

def resolved_fortnite_gameroot():
    p = (load_game_paths().get("Fortnite", "") or "").strip()
    if p and os.path.isdir(p):
        p = os.path.normpath(p)
        if os.path.isdir(os.path.join(p, "Saved", "Config", "WindowsClient")):
            return p
        fg = os.path.join(p, "FortniteGame")
        if os.path.isdir(os.path.join(fg, "Saved", "Config", "WindowsClient")):
            return fg
        return p
    return os.path.join(LA, "FortniteGame")

def resolved_apex_root():
    p = (load_game_paths().get("Apex", "") or "").strip()
    if p and os.path.isdir(p):
        return os.path.normpath(p)
    return os.path.join(ST, "Apex Legends")

def mc_set_dyn(key, val):
    f = os.path.join(resolved_minecraft_dir(), "options.txt")
    return f'ps:$f="{f}";if(Test-Path $f){{$c=Get-Content $f;$n=$c -replace "^{key}:.*","{key}:{val}";if($c -eq $n){{$n+="{key}:{val}"}};$n|Set-Content $f}}'

def fn_set_dyn(key, val):
    fi = os.path.join(resolved_fortnite_gameroot(), "Saved", "Config", "WindowsClient", "GameUserSettings.ini")
    return f'ps:$f="{fi}";if(Test-Path $f){{(Get-Content $f) -replace "{key}=.*","{key}={val}" | Set-Content $f}}'

# tweaks
def _category_roblox():
    rr = resolved_roblox_root()
    return [
        ("Clear Cache",      [f'cmd /c "rd /s /q "{rr}\\logs" 2>nul"', 'cmd /c "del /f /s /q "%temp%\\Roblox*" 2>nul"'],
         "clears roblox cache and logs"),
        ("High Priority",    ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options\\RobloxPlayerBeta.exe\\PerfOptions" /v CpuPriorityClass /t REG_DWORD /d 3 /f'],
         "roblox gets high cpu priority"),
        ("GPU Priority MAX", ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile\\Tasks\\Games" /v "GPU Priority" /t REG_DWORD /d 8 /f'],
         "max gpu priority"),
        ("FSO OFF",          ['reg add "HKCU\\System\\GameConfigStore" /v GameDVR_FSEBehaviorMode /t REG_DWORD /d 2 /f', 'reg add "HKCU\\System\\GameConfigStore" /v GameDVR_HonorUserFSEBehaviorMode /t REG_DWORD /d 1 /f'],
         "turns off fullscreen optimization less input lag"),
        ("Game Bar OFF",     ['reg add "HKCU\\System\\GameConfigStore" /v GameDVR_Enabled /t REG_DWORD /d 0 /f'],
         "kills xbox overlay"),
        ("Nagle OFF",        ['reg add "HKLM\\SOFTWARE\\Microsoft\\MSMQ\\Parameters" /v TCPNoDelay /t REG_DWORD /d 1 /f'],
         "packets sent instantly"),
        ("DNS Flush",        ["ipconfig /flushdns"],
         "clears dns"),
        ("Shader Cache",     ['cmd /c "del /f /s /q "%LOCALAPPDATA%\\D3DSCache\\*" 2>nul"'],
         "clears shader cache"),
        ("Mouse Accel OFF",  ['reg add "HKCU\\Control Panel\\Mouse" /v MouseSpeed /t REG_SZ /d "0" /f', 'reg add "HKCU\\Control Panel\\Mouse" /v MouseThreshold1 /t REG_SZ /d "0" /f', 'reg add "HKCU\\Control Panel\\Mouse" /v MouseThreshold2 /t REG_SZ /d "0" /f'],
         "raw mouse input"),
    ]

def _category_fivem():
    fm = resolved_fivem_root()
    app = os.path.join(fm, "FiveM.app")
    return [
        ("Clear FiveM Cache", [f'cmd /c "rd /s /q "{app}\\cache" 2>nul"', f'cmd /c "rd /s /q "{app}\\data\\cache" 2>nul"'],
         "clears fivem cache fixes crashes and black screens"),
        ("Clear GTA Cache",  ['cmd /c "rd /s /q "%localappdata%\\Rockstar Games\\GTA V\\cache" 2>nul"'],
         "clears gta cache fixes stutters"),
        ("FiveM Priority",   ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options\\FiveM.exe\\PerfOptions" /v CpuPriorityClass /t REG_DWORD /d 3 /f'],
         "fivem gets high cpu priority"),
        ("GTA5 Priority",    ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options\\GTA5.exe\\PerfOptions" /v CpuPriorityClass /t REG_DWORD /d 3 /f'],
         "gta5 gets high cpu priority"),
        ("GPU Priority MAX", ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile\\Tasks\\Games" /v "GPU Priority" /t REG_DWORD /d 8 /f'],
         "max gpu priority"),
        ("Nagle OFF",        ['reg add "HKLM\\SOFTWARE\\Microsoft\\MSMQ\\Parameters" /v TCPNoDelay /t REG_DWORD /d 1 /f'],
         "packets sent instantly big for ping"),
        ("Net Throttle OFF", ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile" /v NetworkThrottlingIndex /t REG_DWORD /d 0xffffffff /f'],
         "no network throttling"),
        ("DNS Flush",        ["ipconfig /flushdns"],
         "flushes dns"),
        ("FSO OFF",          ['reg add "HKCU\\System\\GameConfigStore" /v GameDVR_FSEBehaviorMode /t REG_DWORD /d 2 /f', 'reg add "HKCU\\System\\GameConfigStore" /v GameDVR_HonorUserFSEBehaviorMode /t REG_DWORD /d 1 /f'],
         "turns off fullscreen optimization less input lag"),
        ("Game DVR OFF",     ['reg add "HKCU\\System\\GameConfigStore" /v GameDVR_Enabled /t REG_DWORD /d 0 /f', 'reg add "HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows\\GameDVR" /v AllowGameDVR /t REG_DWORD /d 0 /f'],
         "kills xbox dvr"),
        ("Shader Cache",     ['cmd /c "del /f /s /q "%LOCALAPPDATA%\\D3DSCache\\*" 2>nul"', 'cmd /c "del /f /s /q "%LOCALAPPDATA%\\NVIDIA\\DXCache\\*" 2>nul"'],
         "clears shader cache"),
        ("Mouse Accel OFF",  ['reg add "HKCU\\Control Panel\\Mouse" /v MouseSpeed /t REG_SZ /d "0" /f', 'reg add "HKCU\\Control Panel\\Mouse" /v MouseThreshold1 /t REG_SZ /d "0" /f', 'reg add "HKCU\\Control Panel\\Mouse" /v MouseThreshold2 /t REG_SZ /d "0" /f'],
         "raw mouse input"),
        ("WASD Input Tweak", ["powercfg -setactive SCHEME_MIN",
                              'reg add "HKCU\\Control Panel\\Keyboard" /v KeyboardSpeed /t REG_SZ /d 31 /f',
                              'reg add "HKCU\\Control Panel\\Keyboard" /v KeyboardDelay /t REG_SZ /d 0 /f',
                              'reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\VisualEffects" /v VisualFXSetting /t REG_DWORD /d 2 /f',
                              'reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Serialize" /v StartupDelayInMSec /t REG_DWORD /d 0 /f',
                              "netsh interface tcp set global autotuninglevel=normal"],
         "wicky wasd preset: max keyboard repeat, zero keyboard delay, best-performance visuals, no startup delay, and high performance plan"),
        ("Timer 0.5ms",      ['reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\kernel" /v GlobalTimerResolutionRequests /t REG_DWORD /d 1 /f'],
         "better response"),
        ("mmcss priority",   ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile" /v SystemResponsiveness /t REG_DWORD /d 0 /f', 'reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile" /v NoLazyMode /t REG_DWORD /d 1 /f'],
         "games get full cpu scheduling"),
        ("Power Plan",       ["powercfg -duplicatescheme e9a42b02-d5df-448d-aa00-03f14749eb61", "powercfg -setactive e9a42b02-d5df-448d-aa00-03f14749eb61"],
         "Ultimate performance power plan.\nWarning: gets hot, not for laptops on battery."),
    ]

def _category_minecraft():
    md = resolved_minecraft_dir()
    return [
        ("Fullbright (Gamma 100)", [mc_set_dyn("gamma", "100.0")], "Gamma auf Maximum.\nAlles hell wie mit Nachtsicht.\nWarning: Manche Server bannen das."),
        ("Max FPS Unlimited", [mc_set_dyn("maxFps", "260")], "FPS-Limit auf 260 setzen."),
        ("Render Distance -> 8", [mc_set_dyn("renderDistance", "8")], "8 Chunks = guter Kompromiss\nzwischen FPS und Sichtweite."),
        ("Simulation Distance -> 5", [mc_set_dyn("simulationDistance", "5")], "Weniger Simulation = mehr FPS.\nRedstone/Mobs nur 5 Chunks."),
        ("Smooth Lighting OFF", [mc_set_dyn("ao", "false")], "Ambient Occlusion aus.\nDeutlich mehr FPS."),
        ("VSync OFF", [mc_set_dyn("enableVsync", "false")], "VSync aus.\nWeniger Input-Lag."),
        ("View Bobbing OFF", [mc_set_dyn("bobView", "false")], "Kamera-Wackeln aus.\nBesser fuer PvP."),
        ("Clouds OFF", [mc_set_dyn("renderClouds", "false")], "Wolken aus.\nSpart GPU."),
        ("Particles Minimal", [mc_set_dyn("particles", "2")], "Minimale Partikel.\nMehr FPS in Fights."),
        ("Entity Shadows OFF", [mc_set_dyn("entityShadows", "false")], "Entity Schatten aus."),
        ("Biome Blend OFF", [mc_set_dyn("biomeBlendRadius", "0")], "Biome Blending aus.\nFixt Stutter an Biome-Grenzen."),
        ("Mipmap -> 0", [mc_set_dyn("mipmapLevels", "0")], "Mipmap aus.\nSchaerfere Texturen nah."),
        ("Attack Indicator OFF", [mc_set_dyn("attackIndicator", "0")], "Attack-Indikator aus.\nCleaner HUD."),
        ("Fullscreen ON", [mc_set_dyn("fullscreen", "true")], "Fullscreen Modus."),
        ("GUI Scale -> 3", [mc_set_dyn("guiScale", "3")], "GUI Scale auf 3.\nMehr Sicht im PvP."),
        ("FOV -> 90", [mc_set_dyn("fov", "0.5")], "FOV auf ~90 deg.\n0.0=70 deg, 0.5=90 deg, 1.0=110 deg."),
        ("MC Logs leeren", [f'cmd /c "del /f /s /q "{md}\\crash-reports\\*" 2>nul"', f'cmd /c "del /f /s /q "{md}\\logs\\*" 2>nul"'], "Logs loeschen."),
        ("Shader Cache", [f'cmd /c "del /f /s /q "{md}\\shadercache\\*" 2>nul"', 'cmd /c "del /f /s /q "%LOCALAPPDATA%\\D3DSCache\\*" 2>nul"'], "Shader loeschen."),
        ("Java Priority", ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options\\javaw.exe\\PerfOptions" /v CpuPriorityClass /t REG_DWORD /d 3 /f'], "Java hohe Prio."),
        ("Nagle OFF", ['reg add "HKLM\\SOFTWARE\\Microsoft\\MSMQ\\Parameters" /v TCPNoDelay /t REG_DWORD /d 1 /f'], "Server-Latenz."),
        ("DNS Cloudflare", ['netsh interface ip set dns "Ethernet" static 1.1.1.1 primary', 'netsh interface ip add dns "Ethernet" 1.0.0.1 index=2'], "Schnelleres DNS."),
        ("Timer 0.5ms", ['reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\kernel" /v GlobalTimerResolutionRequests /t REG_DWORD /d 1 /f'], "Timing."),
        ("MC Defender", [f'ps:Add-MpPreference -ExclusionPath "{md}" -ErrorAction SilentlyContinue'], "Defender Exclusion."),
        ("Game DVR OFF", ['reg add "HKCU\\System\\GameConfigStore" /v GameDVR_Enabled /t REG_DWORD /d 0 /f'], "Xbox aus."),
    ]

def _category_fortnite():
    fg = resolved_fortnite_gameroot()
    saved = os.path.join(fg, "Saved")
    return [
        ("VSync OFF", [fn_set_dyn("bUseVSync", "False")], "VSync in Config aus."),
        ("FPS Uncap", [fn_set_dyn("FrameRateLimit", "0.000000")], "FPS-Limit entfernen."),
        ("Fullscreen", [fn_set_dyn("PreferredFullscreenMode", "0"), fn_set_dyn("LastConfirmedFullscreenMode", "0")], "Exclusive Fullscreen.\nBeste Performance."),
        ("Shadows OFF", [fn_set_dyn("sg.ShadowQuality", "0")], "Schatten komplett aus.\nMassive FPS Boost."),
        ("Effects Low", [fn_set_dyn("sg.EffectsQuality", "0")], "Effekte auf Minimum."),
        ("Post Process OFF", [fn_set_dyn("sg.PostProcessQuality", "0")], "Post Processing aus.\nWeniger Input-Lag."),
        ("Textures Medium", [fn_set_dyn("sg.TextureQuality", "1")], "Texturen auf Medium.\nGuter Kompromiss."),
        ("View Distance Far", [fn_set_dyn("sg.ViewDistanceQuality", "2")], "Sichtweite Far.\nWichtig fuer Loot."),
        ("Motion Blur OFF", [fn_set_dyn("bMotionBlurEnabled", "False")], "Motion Blur aus."),
        ("Show FPS", [fn_set_dyn("bShowFPS", "True")], "FPS-Counter anzeigen."),
        ("Mouse Smoothing OFF", [fn_set_dyn("bViewAccelerationEnabled", "False")], "Maus-Smoothing aus."),
        ("Cache leeren", [f'cmd /c "rd /s /q "{saved}\\webcache" 2>nul"', f'cmd /c "del /f /s /q "{saved}\\Logs\\*" 2>nul"'], "Cache+Logs."),
        ("Shader Cache", ['cmd /c "del /f /s /q "%LOCALAPPDATA%\\D3DSCache\\*" 2>nul"', 'cmd /c "del /f /s /q "%LOCALAPPDATA%\\NVIDIA\\DXCache\\*" 2>nul"'], "Shader loeschen."),
        ("Fortnite Priority", ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options\\FortniteClient-Win64-Shipping.exe\\PerfOptions" /v CpuPriorityClass /t REG_DWORD /d 3 /f'], "Hohe Prio."),
        ("Mouse Accel OFF", ['reg add "HKCU\\Control Panel\\Mouse" /v MouseSpeed /t REG_SZ /d "0" /f', 'reg add "HKCU\\Control Panel\\Mouse" /v MouseThreshold1 /t REG_SZ /d "0" /f', 'reg add "HKCU\\Control Panel\\Mouse" /v MouseThreshold2 /t REG_SZ /d "0" /f'], "1:1 Maus."),
        ("HPET OFF", ["bcdedit /deletevalue useplatformclock"], "Weniger Latenz."),
        ("Nagle OFF", ['reg add "HKLM\\SOFTWARE\\Microsoft\\MSMQ\\Parameters" /v TCPNoDelay /t REG_DWORD /d 1 /f'], "Netzwerk."),
        ("GPU Scheduling", ['reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\GraphicsDrivers" /v HwSchMode /t REG_DWORD /d 2 /f'], "HW GPU Scheduling."),
        ("Fortnite Defender", [f'ps:Add-MpPreference -ExclusionPath "{fg}" -ErrorAction SilentlyContinue'], "Defender."),
        ("Game DVR OFF", ['reg add "HKCU\\System\\GameConfigStore" /v GameDVR_Enabled /t REG_DWORD /d 0 /f'], "Xbox aus."),
    ]

def _category_apex():
    ar = resolved_apex_root()
    cfg = os.path.join(ar, "cfg")
    return [
        ("Apex: FPS Cap 190", [cfg_append(os.path.join(cfg, "autoexec.cfg"), "fps_max 190")], "FPS-Limit auf 190 begrenzen.\nStabilere Frametimes."),
        ("Apex: Fog OFF", [cfg_append(os.path.join(cfg, "autoexec.cfg"), "fog_enable 0")], "Nebel aus. Bessere Sicht."),
        ("Apex: Ragdoll OFF", [cfg_append(os.path.join(cfg, "autoexec.cfg"), "cl_ragdoll_collide 0")], "Ragdolls vereinfacht.\nWeniger CPU-Last."),
        ("Apex: Shadow Detail Low", [cfg_append(os.path.join(cfg, "autoexec.cfg"), "shadow_enable 0")], "Schatten aus."),
        ("Apex: Particle OFF", [cfg_append(os.path.join(cfg, "autoexec.cfg"), "mat_particle_fallback 3")], "Partikel auf Minimum."),
        ("Shader Cache", ['cmd /c "del /f /s /q "%LOCALAPPDATA%\\D3DSCache\\*" 2>nul"', 'cmd /c "del /f /s /q "%LOCALAPPDATA%\\NVIDIA\\DXCache\\*" 2>nul"'], "Shader loeschen."),
        ("Apex Priority", ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options\\r5apex.exe\\PerfOptions" /v CpuPriorityClass /t REG_DWORD /d 3 /f'], "Hohe Prio."),
        ("Mouse Accel OFF", ['reg add "HKCU\\Control Panel\\Mouse" /v MouseSpeed /t REG_SZ /d "0" /f', 'reg add "HKCU\\Control Panel\\Mouse" /v MouseThreshold1 /t REG_SZ /d "0" /f', 'reg add "HKCU\\Control Panel\\Mouse" /v MouseThreshold2 /t REG_SZ /d "0" /f'], "1:1 Maus."),
        ("Nagle OFF", ['reg add "HKLM\\SOFTWARE\\Microsoft\\MSMQ\\Parameters" /v TCPNoDelay /t REG_DWORD /d 1 /f'], "Netzwerk."),
        ("Net Throttle OFF", ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile" /v NetworkThrottlingIndex /t REG_DWORD /d 0xffffffff /f'], "Bandbreite."),
        ("GPU Scheduling", ['reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\GraphicsDrivers" /v HwSchMode /t REG_DWORD /d 2 /f'], "HW GPU Scheduling."),
        ("HPET OFF", ["bcdedit /deletevalue useplatformclock"], "Latenz."),
        ("Game DVR OFF", ['reg add "HKCU\\System\\GameConfigStore" /v GameDVR_Enabled /t REG_DWORD /d 0 /f'], "Xbox aus."),
        ("Apex Defender", [f'ps:Add-MpPreference -ExclusionPath "{ar}" -ErrorAction SilentlyContinue'], "Defender."),
    ]

def cfg_append(path,line):
    return f'ps:$f="{path}";if(!(Test-Path $f)){{New-Item -Path $f -ItemType File -Force|Out-Null}};if(!(Select-String -Path $f -Pattern "{line}" -Quiet)){{Add-Content $f "{line}"}}'

# static cats + valo/cs2 (rest via category_items)
CATEGORIES = {
    "Network": [
        ("DNS Flush",        ["ipconfig /flushdns"],
         "clears dns cache"),
        ("Winsock Reset",    ["netsh winsock reset"],
         "resets network socket layer"),
        ("TCP/IP Reset",     ["netsh int ip reset"],
         "Resets the whole TCP/IP stack. If internet is bad try this."),
        ("Nagle OFF",        ['reg add "HKLM\\SOFTWARE\\Microsoft\\MSMQ\\Parameters" /v TCPNoDelay /t REG_DWORD /d 1 /f'],
         "sends packets instantly lowers ping"),
        ("Net Throttle OFF", ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile" /v NetworkThrottlingIndex /t REG_DWORD /d 0xffffffff /f'],
         "stops windows throttling ur network"),
        ("DNS Cloudflare",   ['netsh interface ip set dns "Ethernet" static 1.1.1.1 primary', 'netsh interface ip add dns "Ethernet" 1.0.0.1 index=2'],
         "sets dns to cloudflare usually faster"),
        ("TCP Chimney OFF",  ["netsh int tcp set global chimney=disabled"],
         "turns off tcp offloading"),
        ("RSS ON",           ["netsh int tcp set global rss=enabled"],
         "lets multiple cores handle network traffic"),
        ("ECN OFF",          ["netsh int tcp set global ecncapability=disabled"],
         "turns off ecn"),
        ("IPv6 OFF",         ['reg add "HKLM\\SYSTEM\\CurrentControlSet\\Services\\Tcpip6\\Parameters" /v DisabledComponents /t REG_DWORD /d 0xff /f'],
         "turns off ipv6"),
    ],
    "GPU": [
        ("HAGS ON",          ['reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\GraphicsDrivers" /v HwSchMode /t REG_DWORD /d 2 /f'],
         "enables hardware accelerated gpu scheduling"),
        ("HW Scheduling",    ['reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\GraphicsDrivers" /v HwSchMode /t REG_DWORD /d 2 /f'],
         "lets gpu manage its own workload"),
        ("GPU Priority 8",   ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile\\Tasks\\Games" /v "GPU Priority" /t REG_DWORD /d 8 /f'],
         "gives games max gpu priority"),
        ("FSO OFF",          ['reg add "HKCU\\System\\GameConfigStore" /v GameDVR_FSEBehaviorMode /t REG_DWORD /d 2 /f', 'reg add "HKCU\\System\\GameConfigStore" /v GameDVR_HonorUserFSEBehaviorMode /t REG_DWORD /d 1 /f'],
         "turns off fullscreen optimization less input lag"),
        ("Game DVR OFF",     ['reg add "HKCU\\System\\GameConfigStore" /v GameDVR_Enabled /t REG_DWORD /d 0 /f', 'reg add "HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows\\GameDVR" /v AllowGameDVR /t REG_DWORD /d 0 /f'],
         "kills xbox dvr recording"),
        ("TDR 10s",          ['reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\GraphicsDrivers" /v TdrDelay /t REG_DWORD /d 10 /f'],
         "gives gpu more time before driver timeout"),
        ("TDR DDI Delay 20", ['reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\GraphicsDrivers" /v TdrDdiDelay /t REG_DWORD /d 20 /f'],
         "gives the display driver more time before timing out"),
        ("MPO OFF",          ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows\\Dwm" /v OverlayTestMode /t REG_DWORD /d 5 /f'],
         "fixes black screen flashes"),
        ("Preemption OFF",   ['reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\GraphicsDrivers\\Scheduler" /v EnablePreemption /t REG_DWORD /d 0 /f'],
         "stops windows interrupting gpu mid task"),
        ("DX Shader Cleanup", ['ps:Remove-Item -Force -Recurse -ErrorAction SilentlyContinue "$env:LOCALAPPDATA\\D3DSCache\\*"',
                               'ps:Remove-Item -Force -Recurse -ErrorAction SilentlyContinue "$env:LOCALAPPDATA\\NVIDIA\\DXCache\\*"',
                               'ps:Remove-Item -Force -Recurse -ErrorAction SilentlyContinue "$env:LOCALAPPDATA\\AMD\\DxCache\\*"',
                               'ps:Remove-Item -Force -Recurse -ErrorAction SilentlyContinue "$env:LOCALAPPDATA\\Intel\\ShaderCache\\*"'],
         "clears dx shader caches"),
        ("NVIDIA GL Cache Cleanup", ['ps:Remove-Item -Force -Recurse -ErrorAction SilentlyContinue "$env:LOCALAPPDATA\\NVIDIA\\GLCache\\*"'],
         "clears nvidia gl cache"),
        ("AMD GL Cache Cleanup", ['ps:Remove-Item -Force -Recurse -ErrorAction SilentlyContinue "$env:LOCALAPPDATA\\AMD\\GLCache\\*"'],
         "clears amd gl cache"),
        ("Shader Cache Clean", ['cmd /c "del /f /s /q "%LOCALAPPDATA%\\D3DSCache\\*" 2>nul"', 'cmd /c "del /f /s /q "%LOCALAPPDATA%\\NVIDIA\\DXCache\\*" 2>nul"'],
         "clears shader cache"),
    ],
    "CPU": [
        ("High Performance Plan", ["powercfg -setactive SCHEME_MIN"],
         "switches to the high performance power plan"),
        ("Core Parking OFF", ["ps:powercfg -setacvalueindex SCHEME_CURRENT SUB_PROCESSOR CPMINCORES 100", "ps:powercfg -setactive SCHEME_CURRENT"],
         "keeps all cpu cores awake"),
        ("Min CPU 100%",     ["ps:powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEMIN 100", "ps:powercfg /setactive SCHEME_CURRENT"],
         "forces the cpu minimum state to 100%"),
        ("Max CPU 100%",     ["ps:powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEMAX 100", "ps:powercfg /setactive SCHEME_CURRENT"],
         "forces the cpu maximum state to 100%"),
        ("Responsiveness 0", ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile" /v SystemResponsiveness /t REG_DWORD /d 0 /f'],
         "gives games full cpu priority"),
        ("Latency Priority",  ['reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\PriorityControl" /v Win32PrioritySeparation /t REG_DWORD /d 38 /f'],
         "gives foreground apps more responsive scheduling"),
        ("System Responsiveness 0", ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile" /v SystemResponsiveness /t REG_DWORD /d 0 /f'],
         "gives games full cpu priority"),
        ("Power Throttle OFF", ['reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Power\\PowerThrottling" /v PowerThrottlingOff /t REG_DWORD /d 1 /f'],
         "stops windows throttling apps"),
        ("Idle Disable",     ["ps:powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR IDLEDISABLE 1", "ps:powercfg /setactive SCHEME_CURRENT"],
         "disables cpu idle parking"),
        ("Core Parking Min 100%", ["ps:powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR CPMINCORES 100", "ps:powercfg /setactive SCHEME_CURRENT"],
         "keeps the minimum core parking at 100%"),
        ("Core Parking Max 100%", ["ps:powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR CPMAXCORES 100", "ps:powercfg /setactive SCHEME_CURRENT"],
         "keeps the maximum core parking at 100%"),
        ("Boost Mode Aggressive", ["ps:powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR PERFBOOSTMODE 2", "ps:powercfg /setactive SCHEME_CURRENT"],
         "uses the most aggressive cpu boost behavior"),
        ("Timer 0.5ms",      ['reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\kernel" /v GlobalTimerResolutionRequests /t REG_DWORD /d 1 /f'],
         "sets system timer to 0.5ms"),
        ("Dynamic Tick OFF", ["bcdedit /set disabledynamictick yes"],
         "fixed cpu tick rate"),
        ("HPET OFF",         ["bcdedit /deletevalue useplatformclock"],
         "turns off hpet"),
        ("Hibernate OFF",    ["powercfg -h off"],
         "turns off hibernate frees disk space"),
        ("CSRSS Realtime",   ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options\\csrss.exe\\PerfOptions" /v CpuPriorityClass /t REG_DWORD /d 4 /f'],
         "realtime priority for windows input"),
        ("Paging Exec OFF",  ['reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Memory Management" /v DisablePagingExecutive /t REG_DWORD /d 1 /f'],
         "Keeps system code in RAM instead of paging it.\nWarning: Needs 8GB+ RAM to not backfire."),
    ],
    "RAM": [
        ("RAM Cleaner",      ["py:clean_ram()"],
         "purges standby memory and trims working sets"),
        ("Superfetch OFF",   ["sc config SysMain start= disabled", "sc stop SysMain"],
         "stops windows preloading apps into ram"),
        ("Memory Compression OFF", ["ps:Disable-MMAgent -MemoryCompression"],
         "turns off ram compression"),
        ("Prefetch OFF",     ['reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Memory Management\\PrefetchParameters" /v EnablePrefetcher /t REG_DWORD /d 0 /f'],
         "stops windows prefetching files"),
        ("Page Combine OFF", ["ps:Disable-MMAgent -PageCombining"],
         "turns off page combining"),
        ("SysMain OFF",      ["sc config SysMain start= disabled", "sc stop SysMain"],
         "stops the sysmain service"),
        ("Mem Compress OFF", ["ps:Disable-MMAgent -MemoryCompression"],
         "turns off ram compression"),
        ("NDU OFF",          ['reg add "HKLM\\SYSTEM\\CurrentControlSet\\Services\\Ndu" /v Start /t REG_DWORD /d 4 /f'],
         "kills a service that causes ram leaks"),
        ("Paging Executive ON", ['reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Memory Management" /v DisablePagingExecutive /t REG_DWORD /d 0 /f'],
         "lets windows page kernel code normally"),
        ("Pagefile Clear OFF", ['reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Memory Management" /v ClearPageFileAtShutdown /t REG_DWORD /d 0 /f'],
         "stops pagefile clearing at shutdown"),
        ("Large System Cache OFF", ['reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Memory Management" /v LargeSystemCache /t REG_DWORD /d 0 /f'],
         "keeps normal desktop memory behavior"),
        ("App Prefetch OFF", ['reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Memory Management\\PrefetchParameters" /v EnablePrefetcher /t REG_DWORD /d 0 /f'],
         "turns off app prefetching"),
        ("Standby Clean",    ['ps:[System.GC]::Collect(); [System.GC]::WaitForPendingFinalizers()'],
         "flushes standby ram"),
    ],
    "Input": [
        ("Mouse Accel OFF",  ['reg add "HKCU\\Control Panel\\Mouse" /v MouseSpeed /t REG_SZ /d "0" /f', 'reg add "HKCU\\Control Panel\\Mouse" /v MouseThreshold1 /t REG_SZ /d "0" /f', 'reg add "HKCU\\Control Panel\\Mouse" /v MouseThreshold2 /t REG_SZ /d "0" /f'],
         "turns off mouse acceleration"),
        ("KB Speed MAX",     ['reg add "HKCU\\Control Panel\\Keyboard" /v KeyboardDelay /t REG_SZ /d "0" /f', 'reg add "HKCU\\Control Panel\\Keyboard" /v KeyboardSpeed /t REG_SZ /d "31" /f'],
         "max keyboard speed no delay"),
        ("Sticky Keys OFF",  ['reg add "HKCU\\Control Panel\\Accessibility\\StickyKeys" /v Flags /t REG_SZ /d "506" /f'],
         "turns off sticky keys popup"),
        ("Filter Keys OFF",  ['reg add "HKCU\\Control Panel\\Accessibility\\Keyboard Response" /v Flags /t REG_SZ /d "122" /f'],
         "every keypress registers instantly"),
        ("Menu Delay 0",     ['reg add "HKCU\\Control Panel\\Desktop" /v MenuShowDelay /t REG_SZ /d "0" /f'],
         "right click menus are instant"),
    ],
    "FPS Boost": [
        ("Game Bar OFF",     ['reg add "HKCU\\System\\GameConfigStore" /v GameDVR_Enabled /t REG_DWORD /d 0 /f', 'reg add "HKCU\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\GameDVR" /v AppCaptureEnabled /t REG_DWORD /d 0 /f'],
         "kills xbox game bar"),
        ("Game Mode ON",     ['reg add "HKCU\\Software\\Microsoft\\GameBar" /v AllowAutoGameMode /t REG_DWORD /d 1 /f', 'reg add "HKCU\\Software\\Microsoft\\GameBar" /v AutoGameModeEnabled /t REG_DWORD /d 1 /f'],
         "windows prioritizes ur game"),
        ("BG Apps OFF",      ['reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\BackgroundAccessApplications" /v GlobalUserDisabled /t REG_DWORD /d 1 /f'],
         "stops store apps running in background"),
        ("Search OFF",       ["sc config WSearch start= disabled", "sc stop WSearch"],
         "Stops the indexer randomly spiking disk and CPU.\nWarning: Win+S still works, just indexes slower."),
        ("Notifs OFF",       ['reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\PushNotifications" /v ToastEnabled /t REG_DWORD /d 0 /f'],
         "turns off notification popups"),
        ("MMCSS Priority",   ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile" /v SystemResponsiveness /t REG_DWORD /d 0 /f', 'reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile" /v NoLazyMode /t REG_DWORD /d 1 /f'],
         "games get full cpu scheduling"),
        ("Fast Shutdown",    ['reg add "HKCU\\Control Panel\\Desktop" /v WaitToKillAppTimeout /t REG_SZ /d "2000" /f', 'reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control" /v WaitToKillServiceTimeout /t REG_SZ /d "2000" /f'],
         "pc shuts down faster"),
    ],
    "Debloat": [
        *[(f"rm {a.split('.')[-1]}", [f"appx:{a}"], f"removes {a.split('.')[-1]}") for a in [
            "Microsoft.BingWeather", "Microsoft.GetHelp", "Microsoft.MicrosoftSolitaireCollection",
            "Microsoft.People", "Microsoft.WindowsFeedbackHub", "Microsoft.WindowsMaps",
            "Microsoft.Xbox.TCUI", "Microsoft.XboxApp", "Microsoft.XboxGameOverlay",
            "Microsoft.XboxGamingOverlay", "Microsoft.YourPhone", "Microsoft.ZuneMusic",
            "Microsoft.ZuneVideo", "MicrosoftTeams", "Clipchamp.Clipchamp",
        ]],
        ("Cortana OFF",  ['reg add "HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows\\Windows Search" /v AllowCortana /t REG_DWORD /d 0 /f'],
         "removes cortana"),
        ("Copilot OFF",  ['reg add "HKCU\\Software\\Policies\\Microsoft\\Windows\\WindowsCopilot" /v TurnOffWindowsCopilot /t REG_DWORD /d 1 /f'],
         "removes copilot"),
        ("Ads OFF",      ['reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\ContentDeliveryManager" /v SilentInstalledAppsEnabled /t REG_DWORD /d 0 /f', 'reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\ContentDeliveryManager" /v ContentDeliveryAllowed /t REG_DWORD /d 0 /f'],
         "stops windows silently installing sponsored apps idk why it does that"),
    ],
    "Privacy": [
        ("Telemetry 0",  ['reg add "HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows\\DataCollection" /v AllowTelemetry /t REG_DWORD /d 0 /f'],
         "sets telemetry to minimum"),
        ("DiagTrack OFF", ["sc config DiagTrack start= disabled", "sc stop DiagTrack"],
         "kills the diagnostic tracking service"),
        ("Ad ID OFF",    ['reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\AdvertisingInfo" /v Enabled /t REG_DWORD /d 0 /f'],
         "removes windows advertising id"),
        ("Location OFF", ['reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager\\ConsentStore\\location" /v Value /t REG_SZ /d "Deny" /f'],
         "blocks apps from seeing ur location"),
        ("Clipboard OFF", ['reg add "HKCU\\Software\\Microsoft\\Clipboard" /v EnableClipboardHistory /t REG_DWORD /d 0 /f'],
         "stops clipboard syncing to cloud"),
    ],
    "Power": [
        ("Ultimate Perf", ["powercfg -duplicatescheme e9a42b02-d5df-448d-aa00-03f14749eb61", "powercfg -setactive e9a42b02-d5df-448d-aa00-03f14749eb61"],
         "Enables the hidden Ultimate Performance plan.\nWarning: PC becomes hotter, not for laptops on battery."),
        ("High Performance Plan", ["powercfg -setactive SCHEME_MIN"],
         "switches to the high performance power plan"),
        ("Sleep Timeout OFF", ["ps:powercfg /change standby-timeout-ac 0"],
         "keeps the pc from sleeping on ac power"),
        ("Fast Startup OFF", ['reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Power" /v HiberbootEnabled /t REG_DWORD /d 0 /f'],
         "turns off fast startup"),
        ("Disk Timeout OFF", ["ps:powercfg /change disk-timeout-ac 0"],
         "stops the disk from timing out"),
        ("Monitor Timeout OFF", ["ps:powercfg /change monitor-timeout-ac 0"],
         "keeps the monitor timeout disabled on ac"),
        ("Hibernate Timeout OFF", ["ps:powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP HIBERNATEIDLE 0", "ps:powercfg /setactive SCHEME_CURRENT"],
         "disables hibernate timeout"),
        ("Idle Disable", ["ps:powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR IDLEDISABLE 1", "ps:powercfg /setactive SCHEME_CURRENT"],
         "disables cpu idle parking"),
        ("CPU 100%",      ["powercfg -setacvalueindex SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEMIN 100", "powercfg -setacvalueindex SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEMAX 100"],
         "Forces CPU to always run at 100%. No throttling.\nWarning: Hot. Make sure your cooling is fine."),
        ("USB Suspend OFF", ["powercfg -setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0"],
         "stops usb ports losing power"),
        ("Sleep OFF",     ["powercfg -setacvalueindex SCHEME_CURRENT SUB_SLEEP STANDBYIDLE 0"],
         "disables sleep"),
        ("Apply Plan",    ["powercfg -setactive SCHEME_CURRENT"],
         "applies power plan"),
    ],
    "Cleanup": [
        ("Temp Cleanup",     ['ps:Remove-Item -Force -Recurse -ErrorAction SilentlyContinue "$env:TEMP\\*"', 'ps:Remove-Item -Force -Recurse -ErrorAction SilentlyContinue "$env:WINDIR\\Temp\\*"'],
         "clears temp folders"),
        ("Win Temp",      ['cmd /c "del /f /s /q "%TEMP%\\*" 2>nul"'],
         "clears temp folder"),
        ("Sys Temp",      ['cmd /c "del /f /s /q "%SYSTEMROOT%\\Temp\\*" 2>nul"'],
         "clears system temp folder"),
        ("Prefetch Cleanup", ['ps:Remove-Item -Force -Recurse -ErrorAction SilentlyContinue "$env:SYSTEMROOT\\Prefetch\\*"'],
         "clears prefetch files"),
        ("Prefetch Files", ['cmd /c "del /f /s /q "%SYSTEMROOT%\\Prefetch\\*" 2>nul"'],
         "clears prefetch files"),
        ("Thumbnail Cache Cleanup", ['ps:Remove-Item -Force -Recurse -ErrorAction SilentlyContinue "$env:LOCALAPPDATA\\Microsoft\\Windows\\Explorer\\thumbcache_*"'],
         "clears thumbnail cache"),
        ("Recent Files Cleanup", ['ps:Remove-Item -Force -Recurse -ErrorAction SilentlyContinue "$env:APPDATA\\Microsoft\\Windows\\Recent\\*"'],
         "clears recent file history"),
        ("Recycle Bin",   ['cmd /c "rd /s /q %SYSTEMDRIVE%\\$Recycle.Bin 2>nul"'],
         "empties recycle bin"),
        ("Recycle Bin Cleanup", ['cmd /c "rd /s /q %SYSTEMDRIVE%\\$Recycle.Bin 2>nul"'],
         "empties recycle bin"),
        ("Crash Dumps Cleanup", ['ps:Remove-Item -Force -Recurse -ErrorAction SilentlyContinue "$env:LOCALAPPDATA\\CrashDumps\\*"', 'ps:Remove-Item -Force -Recurse -ErrorAction SilentlyContinue "$env:ProgramData\\Microsoft\\Windows\\WER\\*"'],
         "clears crash dumps and error reports"),
        ("Delivery Cache Cleanup", ['ps:Remove-Item -Force -Recurse -ErrorAction SilentlyContinue "$env:ProgramData\\Microsoft\\Windows\\DeliveryOptimization\\Cache\\*"'],
         "clears delivery optimization cache"),
        ("Icon Cache Cleanup", ['ps:Remove-Item -Force -Recurse -ErrorAction SilentlyContinue "$env:LOCALAPPDATA\\Microsoft\\Windows\\Explorer\\iconcache_*"'],
         "clears icon cache"),
        ("Shader Cache",  ['cmd /c "del /f /s /q "%LOCALAPPDATA%\\D3DSCache\\*" 2>nul"', 'cmd /c "del /f /s /q "%LOCALAPPDATA%\\NVIDIA\\DXCache\\*" 2>nul"'],
         "clears shader cache"),
        ("Logs Cleanup", ['ps:Remove-Item -Force -Recurse -ErrorAction SilentlyContinue "$env:WINDIR\\Logs\\*"', 'ps:Remove-Item -Force -Recurse -ErrorAction SilentlyContinue "$env:LOCALAPPDATA\\*.log"', 'ps:Remove-Item -Force -Recurse -ErrorAction SilentlyContinue "$env:TEMP\\*.log"'],
         "clears common log files"),
        ("DNS Flush",     ["ipconfig /flushdns"],
         "flushes dns"),
    ],
    "Visual": [
        ("Animations OFF", ['reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced" /v TaskbarAnimations /t REG_DWORD /d 0 /f'],
         "turns off shell animations"),
        ("Transparency OFF", ['reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize" /v EnableTransparency /t REG_DWORD /d 0 /f'],
         "turns off transparency effects"),
        ("VisualFX Best Performance", ['reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\VisualEffects" /v VisualFXSetting /t REG_DWORD /d 2 /f'],
         "uses best performance visual effects"),
        ("Wallpaper Compression OFF", ['reg add "HKCU\\Control Panel\\Desktop" /v JPEGImportQuality /t REG_DWORD /d 100 /f'],
         "keeps wallpaper quality high"),
        ("Snap Assist OFF", ['reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced" /v SnapAssist /t REG_DWORD /d 0 /f'],
         "turns off snap assist"),
        ("Show File Extensions", ['reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced" /v HideFileExt /t REG_DWORD /d 0 /f'],
         "shows file extensions"),
        ("Show Hidden Files", ['reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced" /v Hidden /t REG_DWORD /d 1 /f'],
         "shows hidden files"),
        ("Taskbar Left", ['reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced" /v TaskbarAl /t REG_DWORD /d 0 /f'],
         "moves taskbar to the left"),
        ("Taskbar Anim OFF", ['reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced" /v TaskbarAnimations /t REG_DWORD /d 0 /f'],
         "removes taskbar animations"),
        ("Menu Delay 0",  ['reg add "HKCU\\Control Panel\\Desktop" /v MenuShowDelay /t REG_SZ /d "0" /f'],
         "menus are instant"),
        ("Aero Peek OFF", ['reg add "HKCU\\Software\\Microsoft\\Windows\\DWM" /v EnableAeroPeek /t REG_DWORD /d 0 /f'],
         "removes desktop preview on taskbar hover"),
    ],
    "Services": [
        (f"kill {s}", [f"sc config {s} start= disabled", f"sc stop {s}"], f"disables {s}")
        for s in ["DiagTrack", "dmwappushservice", "SysMain", "WSearch", "WerSvc", "Fax",
                  "RemoteRegistry", "MapsBroker", "WMPNetworkSvc", "RetailDemo", "PcaSvc", "XblAuthManager",
                  "XblGameSave", "XboxNetApiSvc", "AJRouter", "TrkWks", "PhoneSvc"]
    ],
    "Valorant": [
        ("Valo Priority",    ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options\\VALORANT-Win64-Shipping.exe\\PerfOptions" /v CpuPriorityClass /t REG_DWORD /d 3 /f'],
         "valorant gets high cpu priority"),
        ("GPU Priority MAX", ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile\\Tasks\\Games" /v "GPU Priority" /t REG_DWORD /d 8 /f'],
         "max gpu priority"),
        ("FSO OFF",          ['reg add "HKCU\\System\\GameConfigStore" /v GameDVR_FSEBehaviorMode /t REG_DWORD /d 2 /f', 'reg add "HKCU\\System\\GameConfigStore" /v GameDVR_HonorUserFSEBehaviorMode /t REG_DWORD /d 1 /f'],
         "turns off fullscreen optimization less input lag"),
        ("Game DVR OFF",     ['reg add "HKCU\\System\\GameConfigStore" /v GameDVR_Enabled /t REG_DWORD /d 0 /f'],
         "kills game bar"),
        ("Mouse Accel OFF",  ['reg add "HKCU\\Control Panel\\Mouse" /v MouseSpeed /t REG_SZ /d "0" /f', 'reg add "HKCU\\Control Panel\\Mouse" /v MouseThreshold1 /t REG_SZ /d "0" /f', 'reg add "HKCU\\Control Panel\\Mouse" /v MouseThreshold2 /t REG_SZ /d "0" /f'],
         "raw mouse input"),
        ("Nagle OFF",        ['reg add "HKLM\\SOFTWARE\\Microsoft\\MSMQ\\Parameters" /v TCPNoDelay /t REG_DWORD /d 1 /f'],
         "packets sent instantly"),
        ("Net Throttle OFF", ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile" /v NetworkThrottlingIndex /t REG_DWORD /d 0xffffffff /f'],
         "no network throttling"),
        ("DNS Flush",        ["ipconfig /flushdns"],
         "flushes dns"),
        ("Shader Cache",     ['cmd /c "del /f /s /q "%LOCALAPPDATA%\\D3DSCache\\*" 2>nul"', 'cmd /c "del /f /s /q "%LOCALAPPDATA%\\NVIDIA\\DXCache\\*" 2>nul"'],
         "clears shader cache"),
        ("Timer 0.5ms",      ['reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\kernel" /v GlobalTimerResolutionRequests /t REG_DWORD /d 1 /f'],
         "more precise frame timing"),
        ("MMCSS Priority",   ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile" /v SystemResponsiveness /t REG_DWORD /d 0 /f', 'reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile" /v NoLazyMode /t REG_DWORD /d 1 /f'],
         "games get full cpu priority"),
        ("Notifs OFF",       ['reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\PushNotifications" /v ToastEnabled /t REG_DWORD /d 0 /f'],
         "turns off notification popups"),
    ],
    "CS2": [
        ("CS2 Priority",     ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options\\cs2.exe\\PerfOptions" /v CpuPriorityClass /t REG_DWORD /d 3 /f'],
         "cs2 gets high cpu priority"),
        ("GPU Priority MAX", ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile\\Tasks\\Games" /v "GPU Priority" /t REG_DWORD /d 8 /f'],
         "max gpu priority"),
        ("FSO OFF",          ['reg add "HKCU\\System\\GameConfigStore" /v GameDVR_FSEBehaviorMode /t REG_DWORD /d 2 /f', 'reg add "HKCU\\System\\GameConfigStore" /v GameDVR_HonorUserFSEBehaviorMode /t REG_DWORD /d 1 /f'],
         "turns off fullscreen optimization"),
        ("Game DVR OFF",     ['reg add "HKCU\\System\\GameConfigStore" /v GameDVR_Enabled /t REG_DWORD /d 0 /f'],
         "kills game bar"),
        ("Mouse Accel OFF",  ['reg add "HKCU\\Control Panel\\Mouse" /v MouseSpeed /t REG_SZ /d "0" /f', 'reg add "HKCU\\Control Panel\\Mouse" /v MouseThreshold1 /t REG_SZ /d "0" /f', 'reg add "HKCU\\Control Panel\\Mouse" /v MouseThreshold2 /t REG_SZ /d "0" /f'],
         "raw mouse input"),
        ("Nagle OFF",        ['reg add "HKLM\\SOFTWARE\\Microsoft\\MSMQ\\Parameters" /v TCPNoDelay /t REG_DWORD /d 1 /f'],
         "packets sent instantly"),
        ("Net Throttle OFF", ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile" /v NetworkThrottlingIndex /t REG_DWORD /d 0xffffffff /f'],
         "no network throttling"),
        ("Shader Cache",     ['cmd /c "del /f /s /q "%LOCALAPPDATA%\\D3DSCache\\*" 2>nul"', 'cmd /c "del /f /s /q "%LOCALAPPDATA%\\NVIDIA\\DXCache\\*" 2>nul"'],
         "clears shader cache"),
        ("Timer 0.5ms",      ['reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\kernel" /v GlobalTimerResolutionRequests /t REG_DWORD /d 1 /f'],
         "more precise frame timing"),
        ("DNS Flush",        ["ipconfig /flushdns"],
         "flushes dns"),
        ("MMCSS Priority",   ['reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile" /v SystemResponsiveness /t REG_DWORD /d 0 /f', 'reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile" /v NoLazyMode /t REG_DWORD /d 1 /f'],
         "cs2 gets full cpu priority"),
        ("KB Speed MAX",     ['reg add "HKCU\\Control Panel\\Keyboard" /v KeyboardDelay /t REG_SZ /d "0" /f', 'reg add "HKCU\\Control Panel\\Keyboard" /v KeyboardSpeed /t REG_SZ /d "31" /f'],
         "max keyboard speed"),
        ("Sticky Keys OFF",  ['reg add "HKCU\\Control Panel\\Accessibility\\StickyKeys" /v Flags /t REG_SZ /d "506" /f'],
         "turns off sticky keys popup"),
    ],
}

def _extra_tweak_paths():
    candidates = []
    env_path = os.environ.get("HEXTRA_EXTRA_TWEAKS", "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())
    try:
        base_dir = PROJECT_ROOT
        candidates.extend([base_dir / "tweak_actions.py", base_dir / "extras" / "tweak_actions.py"])
    except Exception:
        pass
    try:
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend([exe_dir / "tweak_actions.py", exe_dir / "extras" / "tweak_actions.py"])
    except Exception:
        pass
    unique = []
    for candidate in candidates:
        if candidate and candidate not in unique and candidate.is_file():
            unique.append(candidate)
    return unique

def _load_defy_extras():
    extras = {}
    extra_paths = _extra_tweak_paths()
    if not extra_paths:
        return extras
    catalog = None
    for path in extra_paths:
        try:
            spec = importlib.util.spec_from_file_location("_hextra_defy_tweaks", path)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            catalog = mod.build_catalog()
            break
        except Exception:
            continue
    if catalog is None:
        return extras

    cat_map = {
        "network": "Network",
        "gpu": "GPU",
        "cpu": "CPU",
        "ram": "RAM",
        "input": "Input",
        "fps_boost": "FPS Boost",
        "debloat": "Debloat",
        "privacy": "Privacy",
        "power": "Power",
        "cleanup": "Cleanup",
        "visual": "Visual",
        "services": "Services",
        "roblox": "Roblox",
        "minecraft": "Minecraft",
        "fivem": "FiveM",
        "valorant": "Valorant",
        "cs2": "CS2",
    }
    for defy_cat, hextra_cat in cat_map.items():
        items = catalog.get(defy_cat, [])
        if not items:
            continue
        existing = {name for name, *_ in CATEGORIES.get(hextra_cat, [])}
        merged = []
        for tweak in items:
            if tweak.name in existing:
                continue
            merged.append((tweak.name, [tweak.executor], tweak.description))
        if merged:
            extras[hextra_cat] = merged
    return extras

EXTRA_TWEAKS = _load_defy_extras()

# sidebar order
CATEGORY_ORDER = (
    "Network", "GPU", "CPU", "RAM", "Input", "FPS Boost", "Debloat", "Privacy",
    "Power", "Cleanup", "Visual", "Services",
    "Roblox", "FiveM", "Valorant", "CS2", "Minecraft", "Fortnite", "Apex",
)

def category_items(cat):
    if cat == "Roblox":
        items = list(_category_roblox())
    elif cat == "FiveM":
        items = list(_category_fivem())
    elif cat == "Minecraft":
        items = list(_category_minecraft())
    elif cat == "Fortnite":
        items = list(_category_fortnite())
    elif cat == "Apex":
        items = list(_category_apex())
    else:
        items = list(CATEGORIES[cat])
    for name, cmds, desc in EXTRA_TWEAKS.get(cat, []):
        if name not in {t[0] for t in items}:
            items.append((name, cmds, desc))
    return items

def tweak_key(category, name):
    return f"{category}::{name}".strip().casefold()

def _strip_quotes(text):
    text = str(text).strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1]
    return text

def tweak_restart_hint(category, name, cmds):
    joined = " ".join(str(c) for c in cmds if not callable(c)).lower()
    if any(token in joined for token in ["bcdedit", "hwschmode", "hiberbootenabled", "disablepagingexecutive", "disabledcomponents"]):
        return "restart"
    if any(token in joined for token in ["explorer\\advanced", "themes\\personalize", "pushnotifications"]):
        return "sign out"
    if category in {"Roblox", "FiveM", "Valorant", "CS2", "Minecraft", "Fortnite", "Apex"}:
        return "relaunch game"
    if any(token in joined for token in ["gameusersettings.ini", "options.txt", ".cfg", "gameconfigstore"]):
        return "relaunch app"
    return ""

def tweak_entry(category, item):
    name, cmds, desc = item
    return {
        "id": tweak_key(category, name),
        "category": category,
        "name": name,
        "cmds": list(cmds),
        "desc": desc or "",
        "restart": tweak_restart_hint(category, name, cmds),
    }

def category_entries(cat):
    return [tweak_entry(cat, item) for item in category_items(cat)]

def all_tweak_entries():
    entries = []
    for cat in CATEGORY_ORDER:
        entries.extend(category_entries(cat))
    return entries

def find_tweak_entry(tweak_id):
    for entry in all_tweak_entries():
        if entry["id"] == tweak_id:
            return entry
    return None

def recommended_tweak_entries():
    entries = {entry["id"]: entry for entry in all_tweak_entries()}
    mem = psutil.virtual_memory()
    battery = None
    try:
        battery = psutil.sensors_battery()
    except Exception:
        battery = None
    ids = []
    if mem.total < 16 * 1024**3:
        ids.extend([
            tweak_key("FPS Boost", "Game Bar OFF"),
            tweak_key("RAM", "Superfetch OFF"),
            tweak_key("Cleanup", "Temp Cleanup"),
        ])
    else:
        ids.extend([
            tweak_key("CPU", "Paging Exec OFF"),
            tweak_key("FPS Boost", "Game Mode ON"),
        ])
    if psutil.cpu_count() and psutil.cpu_count() <= 8:
        ids.extend([
            tweak_key("FPS Boost", "BG Apps OFF"),
            tweak_key("FPS Boost", "Search OFF"),
        ])
    if battery is not None:
        ids.extend([
            tweak_key("FPS Boost", "Game Mode ON"),
            tweak_key("Visual", "Animations OFF"),
        ])
    seen = set()
    picked = []
    for tweak_id in ids:
        entry = entries.get(tweak_id)
        if entry and tweak_id not in seen:
            seen.add(tweak_id)
            picked.append(entry)
    return picked

def hardware_recommendations():
    mem = psutil.virtual_memory()
    total_gb = mem.total / 1073741824
    battery = None
    try:
        battery = psutil.sensors_battery()
    except Exception:
        battery = None
    recs = []
    if total_gb < 16:
        recs.append("Low-memory setup detected: prioritize Game Bar OFF, Superfetch OFF, and cleanup tools.")
    else:
        recs.append("16 GB+ RAM detected: aggressive CPU and latency tweaks are safer here.")
    if psutil.cpu_count() and psutil.cpu_count() <= 8:
        recs.append("Lower core-count CPU detected: background app and search indexing tweaks are recommended.")
    if battery is not None:
        recs.append("Battery-capable system detected: avoid Ultimate Performance and constant 100% CPU on battery.")
    if not is_admin():
        recs.append("Run as administrator for full tweak coverage and more accurate status checks.")
    return recs


BUILTIN_PRESET_SPECS = [
    {
        "id": "safe-fps",
        "title": "Safe FPS Pack",
        "desc": "A balanced gaming preset with low-risk FPS and frametime wins.",
        "refs": [
            ("FPS Boost", "Game Bar OFF"),
            ("FPS Boost", "Game Mode ON"),
            ("FPS Boost", "BG Apps OFF"),
            ("FPS Boost", "Search OFF"),
            ("FPS Boost", "Notifs OFF"),
            ("FPS Boost", "MMCSS Priority"),
            ("GPU", "HAGS ON"),
            ("GPU", "GPU Priority 8"),
            ("GPU", "FSO OFF"),
            ("Visual", "Animations OFF"),
        ],
    },
    {
        "id": "low-latency",
        "title": "Low Latency Pack",
        "desc": "Focus on mouse, keyboard, scheduler, and network responsiveness.",
        "refs": [
            ("Input", "Mouse Accel OFF"),
            ("Input", "KB Speed MAX"),
            ("Input", "Sticky Keys OFF"),
            ("Input", "Filter Keys OFF"),
            ("Input", "Menu Delay 0"),
            ("CPU", "Timer 0.5ms"),
            ("CPU", "Dynamic Tick OFF"),
            ("CPU", "Latency Priority"),
            ("CPU", "Power Throttle OFF"),
            ("Network", "Nagle OFF"),
            ("Network", "Net Throttle OFF"),
            ("FPS Boost", "MMCSS Priority"),
        ],
    },
    {
        "id": "background-cleanup",
        "title": "Background Cleanup Pack",
        "desc": "Reduce background noise, RAM pressure, and Windows clutter.",
        "refs": [
            ("FPS Boost", "BG Apps OFF"),
            ("FPS Boost", "Search OFF"),
            ("FPS Boost", "Notifs OFF"),
            ("FPS Boost", "Game Bar OFF"),
            ("RAM", "Superfetch OFF"),
            ("Cleanup", "Temp Cleanup"),
            ("Visual", "Animations OFF"),
            ("Visual", "Transparency OFF"),
            ("Visual", "VisualFX Best Performance"),
            ("Visual", "Aero Peek OFF"),
        ],
    },
    {
        "id": "competitive",
        "title": "Competitive Pack",
        "desc": "A stronger competitive preset for latency, focus, and game priority.",
        "refs": [
            ("FPS Boost", "Game Mode ON"),
            ("FPS Boost", "BG Apps OFF"),
            ("FPS Boost", "Search OFF"),
            ("FPS Boost", "Notifs OFF"),
            ("FPS Boost", "MMCSS Priority"),
            ("GPU", "HAGS ON"),
            ("GPU", "GPU Priority 8"),
            ("GPU", "FSO OFF"),
            ("CPU", "Timer 0.5ms"),
            ("CPU", "Dynamic Tick OFF"),
            ("CPU", "Latency Priority"),
            ("CPU", "Power Throttle OFF"),
            ("Input", "Mouse Accel OFF"),
            ("Input", "KB Speed MAX"),
            ("Network", "Nagle OFF"),
            ("Network", "Net Throttle OFF"),
        ],
    },
    {
        "id": "laptop-safe",
        "title": "Laptop Safe Pack",
        "desc": "A lighter preset that avoids hotter power-plan tweaks.",
        "refs": [
            ("FPS Boost", "Game Mode ON"),
            ("FPS Boost", "BG Apps OFF"),
            ("FPS Boost", "Search OFF"),
            ("FPS Boost", "Notifs OFF"),
            ("RAM", "Superfetch OFF"),
            ("Visual", "Animations OFF"),
            ("Visual", "Transparency OFF"),
            ("Visual", "Aero Peek OFF"),
            ("Cleanup", "Temp Cleanup"),
        ],
    },
]


def builtin_presets():
    entries = {entry["id"]: entry for entry in all_tweak_entries()}
    presets = []
    for spec in BUILTIN_PRESET_SPECS:
        tweak_ids = []
        categories = []
        seen = set()
        for category, name in spec.get("refs", []):
            tweak_id = tweak_key(category, name)
            entry = entries.get(tweak_id)
            if not entry or tweak_id in seen:
                continue
            seen.add(tweak_id)
            tweak_ids.append(tweak_id)
            categories.append(entry["category"])
        if not tweak_ids:
            continue
        item = dict(spec)
        item["tweak_ids"] = tweak_ids
        item["count"] = len(tweak_ids)
        item["categories"] = sorted(set(categories))
        presets.append(item)
    return presets


def builtin_preset(preset_id):
    for preset in builtin_presets():
        if preset["id"] == preset_id:
            return preset
    return None


def load_builtin_preset(preset_id):
    preset = builtin_preset(preset_id)
    if not preset:
        return False, "Preset not available.", None
    set_selected_tweaks(set(preset["tweak_ids"]))
    return True, f"Loaded preset '{preset['title']}' with {preset['count']} tweaks.", preset

def _reg_hive_parts(path):
    import winreg
    path = _strip_quotes(path)
    if "\\" not in path:
        return None, ""
    root, subkey = path.split("\\", 1)
    mapping = {
        "HKLM": winreg.HKEY_LOCAL_MACHINE,
        "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
        "HKCU": winreg.HKEY_CURRENT_USER,
        "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
        "HKCR": winreg.HKEY_CLASSES_ROOT,
        "HKEY_CLASSES_ROOT": winreg.HKEY_CLASSES_ROOT,
        "HKU": winreg.HKEY_USERS,
        "HKEY_USERS": winreg.HKEY_USERS,
    }
    return mapping.get(root.upper()), subkey

def _query_registry_value(path, value_name):
    if os.name != "nt":
        return False, None, None
    try:
        import winreg
        hive, subkey = _reg_hive_parts(path)
        if hive is None:
            return False, None, None
        key = winreg.OpenKey(hive, subkey)
        try:
            value, typ = winreg.QueryValueEx(key, _strip_quotes(value_name))
            return True, value, typ
        finally:
            winreg.CloseKey(key)
    except Exception:
        return False, None, None

def _registry_type_name(reg_type):
    if os.name != "nt":
        return None
    try:
        import winreg
        mapping = {
            winreg.REG_DWORD: "REG_DWORD",
            winreg.REG_SZ: "REG_SZ",
            winreg.REG_QWORD: "REG_QWORD",
        }
        return mapping.get(reg_type)
    except Exception:
        return None

def _registry_render_data(value, reg_type):
    type_name = _registry_type_name(reg_type)
    if type_name in {"REG_DWORD", "REG_QWORD"}:
        return str(int(value))
    return str(value)

def _parse_reg_add(cmd):
    match = re.search(r'reg add "([^"]+)" /v ("[^"]+"|\S+) /t (\S+) /d ("[^"]+"|\S+) /f', cmd, re.IGNORECASE)
    if not match:
        return None
    return {
        "path": match.group(1),
        "name": _strip_quotes(match.group(2)),
        "type": match.group(3).upper(),
        "data": _strip_quotes(match.group(4)),
    }

def _parse_reg_delete(cmd):
    match = re.search(r'reg delete "([^"]+)" /v ("[^"]+"|\S+) /f', cmd, re.IGNORECASE)
    if not match:
        return None
    return {
        "path": match.group(1),
        "name": _strip_quotes(match.group(2)),
    }

def _parse_service_config(cmd):
    match = re.search(r'sc config (\S+) start=\s*(\S+)', cmd, re.IGNORECASE)
    if not match:
        return None
    return match.group(1), match.group(2).lower()

def _parse_service_state(cmd):
    match = re.search(r'sc (start|stop) (\S+)', cmd, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).lower(), match.group(2)

def _query_service_start(service_name):
    if os.name != "nt":
        return None
    exists, value, _ = _query_registry_value(f"HKLM\\SYSTEM\\CurrentControlSet\\Services\\{service_name}", "Start")
    if not exists:
        return None
    mapping = {2: "auto", 3: "demand", 4: "disabled"}
    return mapping.get(int(value), str(value))

def _query_service_running(service_name):
    out = _run_process(["sc", "query", service_name], return_output=True, timeout=8)
    if not out:
        return None
    upper = out.upper()
    if "RUNNING" in upper:
        return True
    if "STOPPED" in upper:
        return False
    return None

def _active_power_scheme():
    out = _run_process(["powercfg", "/getactivescheme"], return_output=True, timeout=8)
    return out.lower() if out else ""

def _bcdedit_flag(name):
    out = _run_process(["bcdedit", "/enum"], return_output=True, timeout=10)
    if not out:
        return None
    match = re.search(rf"^{re.escape(name)}\s+(\S+)", out, re.IGNORECASE | re.MULTILINE)
    return match.group(1).lower() if match else None

def command_status(cmd):
    if callable(cmd):
        return None
    cmd = str(cmd).strip()
    if not cmd:
        return None
    parsed = _parse_reg_add(cmd)
    if parsed:
        exists, value, reg_type = _query_registry_value(parsed["path"], parsed["name"])
        if parsed["type"] in {"REG_DWORD", "REG_QWORD"}:
            try:
                target = int(parsed["data"], 0)
            except Exception:
                target = 0
            if not exists:
                # key absent = Windows default of 0, treat as applied when target is 0
                return target == 0
            try:
                return int(value) == target
            except Exception:
                return False
        if not exists:
            return False
        return str(value).strip() == parsed["data"]
    parsed = _parse_reg_delete(cmd)
    if parsed:
        exists, _, _ = _query_registry_value(parsed["path"], parsed["name"])
        return not exists
    parsed = _parse_service_config(cmd)
    if parsed:
        service, target = parsed
        return _query_service_start(service) == target
    parsed = _parse_service_state(cmd)
    if parsed:
        action, service = parsed
        state = _query_service_running(service)
        if state is None:
            return None
        return state if action == "start" else (state is False)
    if cmd.lower().startswith("appx:"):
        package = cmd.split(":", 1)[1].strip()
        script = f'if(Get-AppxPackage -AllUsers "{package}"){{"FOUND"}}else{{"MISSING"}}'
        result = _run_process(["powershell", "-NoProfile", "-Command", script], return_output=True, timeout=10)
        if not result:
            return None
        return result.strip().upper() == "MISSING"
    if "powercfg -setactive scheme_min" in cmd.lower():
        return "high performance" in _active_power_scheme()
    if "powercfg -setactive e9a42b02-d5df-448d-aa00-03f14749eb61" in cmd.lower():
        return "e9a42b02-d5df-448d-aa00-03f14749eb61" in _active_power_scheme()
    if "bcdedit /set disabledynamictick yes" in cmd.lower():
        return _bcdedit_flag("disabledynamictick") == "yes"
    if "bcdedit /deletevalue useplatformclock" in cmd.lower():
        return _bcdedit_flag("useplatformclock") is None
    return None

def tweak_status(entry):
    statuses = [command_status(cmd) for cmd in entry["cmds"]]
    known = [status for status in statuses if status is not None]
    if not known:
        return "Unknown", "#6b7280"
    if all(known):
        return "Applied", "#f5f7fa"
    if any(known):
        return "Partial", "#a1a1aa"
    return "Not Applied", "#6b7280"

def _restore_cmd_for_registry_snapshot(path, name, exists, value=None, reg_type=None):
    if not exists:
        return f'reg delete "{path}" /v {name} /f'
    type_name = _registry_type_name(reg_type)
    if not type_name:
        return None
    data = _registry_render_data(value, reg_type)
    quoted_name = f'"{name}"' if " " in name else name
    quoted_data = f'"{data}"' if " " in data else data
    return f'reg add "{path}" /v {quoted_name} /t {type_name} /d {quoted_data} /f'

def snapshot_restore_commands(cmd):
    if callable(cmd):
        return []
    cmd = str(cmd).strip()
    parsed = _parse_reg_add(cmd)
    if parsed:
        exists, value, reg_type = _query_registry_value(parsed["path"], parsed["name"])
        restore = _restore_cmd_for_registry_snapshot(parsed["path"], parsed["name"], exists, value, reg_type)
        return [restore] if restore else []
    parsed = _parse_reg_delete(cmd)
    if parsed:
        exists, value, reg_type = _query_registry_value(parsed["path"], parsed["name"])
        restore = _restore_cmd_for_registry_snapshot(parsed["path"], parsed["name"], exists, value, reg_type)
        return [restore] if restore else []
    parsed = _parse_service_config(cmd)
    if parsed:
        service, _ = parsed
        current = _query_service_start(service)
        if current:
            return [f"sc config {service} start= {current}"]
    parsed = _parse_service_state(cmd)
    if parsed:
        _, service = parsed
        running = _query_service_running(service)
        if running is True:
            return [f"sc start {service}"]
        if running is False:
            return [f"sc stop {service}"]
    return []

def save_tweak_snapshot(entry, restore_cmds):
    if not restore_cmds:
        return
    save_snapshot(entry["id"], {
        "name": entry["name"],
        "category": entry["category"],
        "restore_cmds": list(dict.fromkeys(restore_cmds)),
        "updated_at": time.time(),
    })

def snapshot_entries():
    entries = []
    for tweak_id, snap in load_snapshots().items():
        cmds = list(dict.fromkeys(snap.get("restore_cmds", [])))
        if cmds:
            entries.append({
                "id": tweak_id,
                "category": snap.get("category", "Snapshots"),
                "name": f"restore {snap.get('name', tweak_id)}",
                "cmds": cmds,
                "desc": "Restore previously captured values.",
                "restart": "",
            })
    return entries

def _command_key(cmd):
    if callable(cmd):
        return ("callable", getattr(cmd, "__module__", ""), getattr(cmd, "__qualname__", ""), repr(cmd))
    return ("text", str(cmd).strip())

def _dedupe_tweaks(tweaks):
    seen = set()
    deduped = []
    for tweak in tweaks:
        if isinstance(tweak, dict):
            name = tweak.get("name", "")
            cmds = list(tweak.get("cmds", []))
            desc = tweak.get("desc", "")
        else:
            name, cmds, desc = tweak
        unique_cmds = []
        for cmd in cmds:
            key = _command_key(cmd)
            if key in seen:
                continue
            seen.add(key)
            unique_cmds.append(cmd)
        if unique_cmds:
            if isinstance(tweak, dict):
                copy = dict(tweak)
                copy["cmds"] = unique_cmds
                deduped.append(copy)
            else:
                deduped.append((name, unique_cmds, desc))
    return deduped

# revert commands
REVERT_CMDS = [
    'reg delete "HKLM\\SOFTWARE\\Microsoft\\MSMQ\\Parameters" /v TCPNoDelay /f',
    'reg delete "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile" /v NetworkThrottlingIndex /f',
    'reg delete "HKLM\\SYSTEM\\CurrentControlSet\\Control\\GraphicsDrivers" /v HwSchMode /f',
    'reg delete "HKCU\\System\\GameConfigStore" /v GameDVR_Enabled /f',
    'reg delete "HKCU\\System\\GameConfigStore" /v GameDVR_FSEBehaviorMode /f',
    'reg delete "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile" /v SystemResponsiveness /f',
    'reg delete "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Power\\PowerThrottling" /v PowerThrottlingOff /f',
    'reg delete "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\kernel" /v GlobalTimerResolutionRequests /f',
    'bcdedit /deletevalue disabledynamictick',
    'sc config SysMain start= auto', 'sc start SysMain',
    'sc config WSearch start= auto', 'sc start WSearch',
    'sc config DiagTrack start= auto',
    'reg delete "HKCU\\Control Panel\\Mouse" /v MouseSpeed /f',
    'reg delete "HKCU\\Control Panel\\Mouse" /v MouseThreshold1 /f',
    'reg delete "HKCU\\Control Panel\\Mouse" /v MouseThreshold2 /f',
    'reg add "HKCU\\Control Panel\\Keyboard" /v KeyboardSpeed /t REG_SZ /d 31 /f',
    'reg add "HKCU\\Control Panel\\Keyboard" /v KeyboardDelay /t REG_SZ /d 1 /f',
    'reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\VisualEffects" /v VisualFXSetting /t REG_DWORD /d 1 /f',
    'reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Serialize" /v StartupDelayInMSec /t REG_DWORD /d 1200 /f',
    'netsh interface tcp set global autotuninglevel=normal',
    'powercfg -setactive SCHEME_BALANCED',
    'reg delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\PushNotifications" /v ToastEnabled /f',
    'reg delete "HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows\\DataCollection" /v AllowTelemetry /f',
    'reg delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\AdvertisingInfo" /v Enabled /f',
    'reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced" /v TaskbarAnimations /t REG_DWORD /d 1 /f',
    'reg delete "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options\\RobloxPlayerBeta.exe\\PerfOptions" /v CpuPriorityClass /f',
    'reg delete "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options\\FiveM.exe\\PerfOptions" /v CpuPriorityClass /f',
    'reg delete "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options\\GTA5.exe\\PerfOptions" /v CpuPriorityClass /f',
    'reg delete "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options\\VALORANT-Win64-Shipping.exe\\PerfOptions" /v CpuPriorityClass /f',
    'reg delete "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options\\cs2.exe\\PerfOptions" /v CpuPriorityClass /f',
]

# threads
def _coerce_worker_entry(entry):
    if isinstance(entry, dict):
        return entry
    if isinstance(entry, (list, tuple)) and len(entry) >= 3:
        name, cmds, desc = entry[:3]
        return {
            "id": tweak_key("adhoc", name),
            "category": "adhoc",
            "name": name,
            "cmds": list(cmds),
            "desc": desc or "",
            "restart": "",
        }
    return {
        "id": "unknown",
        "category": "adhoc",
        "name": "unknown",
        "cmds": [],
        "desc": "",
        "restart": "",
    }

class TweakWorker(QThread):
    progress = pyqtSignal(int, int, str)
    detail   = pyqtSignal(str)
    done     = pyqtSignal()
    def __init__(self, tweaks): super().__init__(); self.tweaks = tweaks
    def run(self):
        n = len(self.tweaks)
        for i, raw in enumerate(self.tweaks, 1):
            entry = _coerce_worker_entry(raw)
            name = entry["name"]
            cmds = entry["cmds"]
            self.progress.emit(i, n, name)
            restore_cmds = []
            had_error = False
            for c in cmds:
                restore_cmds.extend(snapshot_restore_commands(c))
                out = run_cmd(c)
                status = "error" if _command_failed(out) else "ok"
                had_error = had_error or status == "error"
                append_activity("command", name, str(out or ""), status, tweak_key=entry["id"], category=entry["category"], extra={"command": str(c)})
                if isinstance(out, str) and out.strip():
                    self.detail.emit(out.strip())
            save_tweak_snapshot(entry, restore_cmds)
            detail = entry.get("restart", "")
            if had_error:
                detail = ((detail + " ") if detail else "") + "One or more commands failed."
            append_activity("tweak", name, detail, "error" if had_error else "ok", tweak_key=entry["id"], category=entry["category"])
        self.done.emit()

class StatusWorker(QThread):
    result = pyqtSignal(list)  # list of (text, color) per row
    def __init__(self, entries):
        super().__init__()
        self.entries = entries
    def run(self):
        results = [tweak_status(e) for e in self.entries]
        self.result.emit(results)

class AccountLoginWorker(QThread):
    result = pyqtSignal(bool, str, dict)
    def __init__(self, username, password, remember=False, register=False, email=""):
        super().__init__()
        self.username = username
        self.password = password
        self.remember = remember
        self.register = register
        self.email = email
    def run(self):
        if self.register:
            ok, msg, resp = client_register(self.username, self.email, self.password, self.remember)
        else:
            ok, msg, resp = client_login(self.username, self.password, self.remember)
        self.result.emit(ok, msg, resp if isinstance(resp, dict) else {})

class AccountStatusWorker(QThread):
    result = pyqtSignal(str, str, dict)
    def __init__(self, auth):
        super().__init__()
        self.auth = dict(auth or {})
    def run(self):
        resp = client_status(self.auth)
        self.result.emit(*_account_days_left_text(resp), resp if isinstance(resp, dict) else {})

class RedeemWorker(QThread):
    result = pyqtSignal(bool, str, dict)
    def __init__(self, auth, key):
        super().__init__()
        self.auth = dict(auth or {})
        self.key = key
    def run(self):
        ok, msg, resp = client_redeem(self.auth, self.key)
        self.result.emit(ok, msg, resp if isinstance(resp, dict) else {})

class _MotdPollWorker(QThread):
    result = pyqtSignal(str)
    def run(self):
        resp = _fetch_json("/motd", timeout=6)
        if isinstance(resp, dict):
            self.result.emit(resp.get("motd", ""))
            return
        self.result.emit("")

class UpdateCheckWorker(QThread):
    result = pyqtSignal(dict)

    def __init__(self, current_version, auth=None):
        super().__init__()
        self.current_version = current_version or "0.0.0"
        self.auth = dict(auth or {})

    def run(self):
        if not _can_self_update():
            self.result.emit({})
            return
        try:
            from urllib.parse import quote
            resp = _fetch_json(f"/update/check?v={quote(self.current_version)}", timeout=8, auth=self.auth)
        except Exception:
            resp = None
        self.result.emit(resp if isinstance(resp, dict) else {})

class UpdateDownloadWorker(QThread):
    progress = pyqtSignal(int, int, str)
    result = pyqtSignal(bool, str, dict)

    def __init__(self, meta, auth=None):
        super().__init__()
        self.meta = dict(meta or {})
        self.auth = dict(auth or {})

    def run(self):
        published_size = 0
        try:
            published_size = int(self.meta.get("size") or 0)
        except Exception:
            published_size = 0

        def _emit_progress(done, total):
            self.progress.emit(int(done or 0), int(total or published_size or 0), "Downloading update...")

        _emit_progress(0, published_size)
        payload = _prepare_update_install(self.meta, progress=_emit_progress, auth=self.auth)
        if payload.get("success"):
            final_size = int(payload.get("size") or published_size or 0)
            self.progress.emit(final_size, final_size, "Preparing installer...")
            self.result.emit(True, "Update downloaded. Hextra will restart to finish installing.", payload)
            return
        self.result.emit(False, payload.get("message", "Could not prepare the update."), payload)

class Toggle(QAbstractButton):
    def __init__(self, accent, parent=None):
        super().__init__(parent)
        self._accent = QColor(accent); self._on = False; self._t = 0.0; self._hov = False
        self._anim = QPropertyAnimation(self, b"toggleProgress", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setCheckable(True); self.setFixedSize(32, 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._flip)

    def set_accent(self, ac): self._accent = QColor(ac); self.update()
    def setChecked(self, v):
        self._on = bool(v)
        target = 1.0 if self._on else 0.0
        if self._anim.state() == QAbstractAnimation.State.Running:
            self._anim.stop()
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(target)
        self._anim.start()
    def isChecked(self):      return self._on
    def _flip(self):
        self.setChecked(not self._on)

    def getToggleProgress(self):
        return self._t

    def setToggleProgress(self, value):
        self._t = max(0.0, min(1.0, float(value)))
        self.update()

    toggleProgress = pyqtProperty(float, fget=getToggleProgress, fset=setToggleProgress)

    def enterEvent(self, _):
        self._hov = True
        self.update()

    def leaveEvent(self, _):
        self._hov = False
        self.update()

    def tick(self):
        pass

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height(); t = self._t
        off = QColor(PANEL if self._hov else BG)
        on = QColor(self._accent.name())
        r = int(off.red()   + (on.red()   - off.red())   * t)
        g = int(off.green() + (on.green() - off.green()) * t)
        b = int(off.blue()  + (on.blue()  - off.blue())  * t)
        p.setBrush(QBrush(QColor(r, g, b)))
        p.setPen(QPen(QColor(LINE), 1))
        track = QRectF(0.5, 0.5, max(0.0, w - 1.0), max(0.0, h - 1.0))
        p.drawRoundedRect(track, track.height() / 2.0, track.height() / 2.0)
        dia = max(8.0, min(10.0, h - 8.0))
        y = max(0.0, (h - dia) / 2.0)
        margin = y
        x = margin + (w - (margin * 2.0) - dia) * t
        knob = QColor("#ffffff") if self._on else QColor(DIM)
        p.setBrush(QBrush(knob))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(x, y, dia, dia))
        p.end()

class SnowCanvas(QWidget):
    _F = [
        (0.05,0.0,0.0018,4,0.0,0.6),(0.12,0.15,0.0012,3,1.2,0.4),(0.22,0.42,0.0021,5,0.7,0.8),
        (0.31,0.08,0.0015,3,2.1,0.5),(0.38,0.65,0.0009,2,0.3,0.3),(0.45,0.30,0.0017,4,1.8,0.7),
        (0.52,0.80,0.0011,3,2.5,0.4),(0.60,0.10,0.0020,5,0.9,0.9),(0.67,0.55,0.0014,3,1.5,0.6),
        (0.74,0.35,0.0022,4,2.8,0.5),(0.80,0.72,0.0010,2,0.5,0.3),(0.88,0.20,0.0016,3,1.1,0.7),
        (0.93,0.48,0.0019,4,2.2,0.8),(0.18,0.88,0.0013,3,1.7,0.4),(0.55,0.92,0.0018,5,0.2,0.6),
        (0.70,0.05,0.0011,2,2.9,0.3),(0.28,0.60,0.0020,4,1.4,0.9),(0.42,0.78,0.0015,3,0.8,0.5),
        (0.85,0.90,0.0012,2,2.0,0.4),(0.08,0.50,0.0017,3,1.6,0.7),(0.95,0.70,0.0021,4,0.1,0.8),
        (0.35,0.22,0.0009,2,2.6,0.3),(0.62,0.42,0.0016,3,1.3,0.6),(0.78,0.85,0.0014,4,0.6,0.5),
        (0.15,0.75,0.0019,3,2.3,0.7),(0.50,0.05,0.0022,5,1.0,0.9),(0.90,0.38,0.0010,2,1.9,0.3),
    ]
    def __init__(self, accent, parent=None, *, opacity_scale=0.55, size_scale=1.0):
        super().__init__(parent); self._accent = QColor(accent); self._t = 0.0
        self._opacity_scale = max(0.0, float(opacity_scale))
        self._size_scale = max(0.35, float(size_scale))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

    def set_accent(self, ac): self._accent = QColor(ac)
    def tick(self): self._t += 1.0; self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        if not W or not H: p.end(); return
        for bx, by, speed, size, phase, drift in self._F:
            yf = (by + self._t * speed) % 1.0
            xf = bx + math.sin(self._t * 0.02 + phase) * 0.015 * drift
            cx, cy = int(xf * W), int(yf * H)
            fade = 0.15 + 0.55 * math.sin(yf * math.pi)
            draw_size = size * self._size_scale
            col = QColor("#f5f7fa"); col.setAlphaF(fade * self._opacity_scale)
            p.setPen(QPen(col, max(1, int(draw_size // 3)), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for arm in range(6):
                a = math.radians(arm * 60)
                r = draw_size * 2
                ex, ey = cx + r*math.cos(a), cy + r*math.sin(a)
                p.drawLine(QPointF(cx, cy), QPointF(ex, ey))
                for b in [-1, 1]:
                    ba = a + b*math.radians(50); bl = r*0.4; mid = 0.55
                    mx, my = cx + r*mid*math.cos(a), cy + r*mid*math.sin(a)
                    p.drawLine(QPointF(mx, my), QPointF(mx + bl*math.cos(ba), my + bl*math.sin(ba)))
        p.end()

def _solid(ac):
    return (
        "QPushButton{"
        f"background:{ac};"
        "color:#ffffff;"
        f"border:1px solid {ac};border-radius:4px;"
        f"font:500 11px '{UI_FONT}';padding:0 16px;"
        "}"
        "QPushButton:hover{"
        f"background:{QColor(ac).darker(108).name()};"
        f"border-color:{QColor(ac).darker(108).name()};"
        "}"
        "QPushButton:pressed{"
        f"background:{QColor(ac).darker(112).name()};"
        f"border-color:{QColor(ac).darker(112).name()};"
        "}"
        "QPushButton:disabled{"
        f"background:{SURFACE2};"
        f"color:{DIM};border-color:{LINE};"
        "}"
    )

def _ghost(ac):
    return (
        "QPushButton{"
        "background:transparent;"
        f"color:{DIM};border:1px solid {LINE};"
        f"border-radius:4px;font:400 11px '{UI_FONT}';padding:0 12px;"
        "}"
        "QPushButton:hover{"
        f"color:{MAIN};border-color:{_rgba('#ffffff', 32)};background:transparent;"
        "}"
    )

def _ghost_sized(ac, px):
    return (
        "QPushButton{"
        "background:transparent;"
        f"color:{DIM};border:1px solid {LINE};"
        f"border-radius:4px;font:400 {px}px '{UI_FONT}';padding:0 12px;"
        "}"
        "QPushButton:hover{"
        f"color:{MAIN};border-color:{_rgba('#ffffff', 32)};background:transparent;"
        "}"
    )

def _danger():
    return (
        "QPushButton{"
        "background:transparent;"
        f"color:{MAIN};border:1px solid {ACCENT_LIVE};border-radius:4px;"
        f"font:500 11px '{UI_FONT}';padding:0 16px;"
        "}"
        "QPushButton:hover{"
        f"background:rgba(230,0,0,24);"
        f"border-color:{ACCENT_LIVE};"
        "}"
    )

def replica_nav_button_style(ac, on=False):
    if on:
        return (
            "QPushButton{"
            f"background:{PANEL};"
            f"color:{MAIN};border:none;border-left:2px solid {ac};border-radius:0px;"
            f"font:600 13px '{UI_FONT}';text-align:left;padding:0 18px;"
            "}"
        )
    return (
        "QPushButton{"
        "background:transparent;"
        f"color:{MID};border:none;border-left:2px solid transparent;border-radius:0px;"
        f"font:600 13px '{UI_FONT}';text-align:left;padding:0 18px;"
        "}"
        "QPushButton:hover{"
        f"color:{MAIN};background:{PANEL};"
        "}"
    )

_DOT_ICON_CACHE = {}

def _status_dot_icon(color):
    key = str(color).lower()
    cached = _DOT_ICON_CACHE.get(key)
    if cached is not None:
        return cached
    pix = QPixmap(10, 10)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    col = QColor(color)
    p.setPen(QPen(col.darker(145), 1))
    p.setBrush(QBrush(col))
    p.drawEllipse(1, 1, 8, 8)
    p.end()
    icon = QIcon(pix)
    _DOT_ICON_CACHE[key] = icon
    return icon

def _pretty_label(text):
    return str(text).replace("_", " ")

def _sidebar_label(key, fallback=""):
    labels = {
        "home": "Dashboard",
        "account": "Account",
        "tweak:FPS Boost": "FPS & Rendering",
        "tweak:CPU": "CPU & Priority",
        "tweak:GPU": "GPU & Graphics",
        "tweak:RAM": "Gaming Performance",
        "tweak:Input": "Input & Latency",
        "tweak:Privacy": "Privacy & Telemetry",
        "tweak:Debloat": "Windows Debloater",
        "tweak:Visual": "Visual Effects",
        "tweak:Network": "Network & Latency",
        "tweak:Power": "Power & Startup",
        "tweak:Services": "Service Manager",
        "tweak:Cleanup": "System Cleaner",
        "profiles": "Presets",
        "activity": "Activity Log",
        "settings": "Settings",
        "restore": "Restore & Recovery",
    }
    if key in labels:
        return labels[key]
    if key.startswith("tweak:"):
        return _pretty_label(key[6:])
    return _pretty_label(fallback or key)

def _button_display_text(text):
    return str(text).replace("&", "&&")

GAME_TAB_KEYS = {
    "tweak:Roblox": "Roblox",
    "tweak:FiveM": "FiveM",
    "tweak:Valorant": "Valorant",
    "tweak:CS2": "CS2",
    "tweak:Minecraft": "Minecraft",
    "tweak:Fortnite": "Fortnite",
    "tweak:Apex": "Apex",
}

# the sidebar
class Sidebar(QFrame):
    page_selected = pyqtSignal(str)

    def __init__(self, accent, parent=None):
        super().__init__(parent)
        self._accent = accent
        self._active = "home"
        self._nav_btns = {}
        self._game_detection = {game: False for game in GAME_TAB_KEYS.values()}
        self.setFixedWidth(250)
        self.setStyleSheet(f"QFrame{{background:{BG};border:none;border-right:1px solid {LINE};}}")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 20, 0, 0)
        root.setSpacing(0)

        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet(
            f"QScrollArea{{background:transparent;border:none;}}"
            f"QScrollBar:vertical{{background:transparent;width:4px;border:none;margin:6px 0 6px 0;}}"
            f"QScrollBar::handle:vertical{{background:{LINE};border:none;border-radius:2px;min-height:24px;}}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
        )
        content = QWidget()
        content.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        sections = [
            ("Overview", [("home", "Home")]),
            ("Performance", [
                ("tweak:FPS Boost", "FPS Boost"),
                ("tweak:CPU", "CPU"),
                ("tweak:GPU", "GPU"),
                ("tweak:RAM", "RAM"),
                ("tweak:Input", "Input"),
                ("tweak:Network", "Network"),
            ]),
            ("System", [
                ("tweak:Privacy", "Privacy"),
                ("tweak:Debloat", "Debloat"),
                ("tweak:Services", "Services"),
            ]),
            ("Games", [
                ("tweak:Roblox", "Roblox"),
                ("tweak:FiveM", "FiveM"),
                ("tweak:Valorant", "Valorant"),
                ("tweak:CS2", "CS2"),
                ("tweak:Minecraft", "Minecraft"),
                ("tweak:Fortnite", "Fortnite"),
                ("tweak:Apex", "Apex"),
            ]),
            ("Tools", [
                ("profiles", "Presets"),
                ("quick", "Quick Tools"),
                ("restore", "Restore"),
                ("settings", "Settings"),
            ]),
        ]

        for section, items in sections:
            sep = QLabel(section)
            sep.setStyleSheet(f"color:{DIM};font:700 12px '{MONO_FONT}';letter-spacing:1.35px;padding:0 18px 10px 18px;border:none;")
            lay.addWidget(sep)
            for key, label in items:
                btn = QPushButton(label)
                btn.setFixedHeight(33)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(lambda _=False, k=key: self._select(k))
                lay.addWidget(btn)
                self._nav_btns[key] = btn
            lay.addSpacing(18)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        self._footer = QPushButton()
        self._footer.setCursor(Qt.CursorShape.PointingHandCursor)
        self._footer.clicked.connect(lambda: self.page_selected.emit("account"))
        root.addWidget(self._footer)
        self.set_account_summary("guest", "No account", False)
        self.refresh_game_detection()

    def _display_label(self, key):
        button = self._nav_btns.get(key)
        return button.text() if button else _sidebar_label(key)

    def _refresh_button_styles(self):
        for key, button in self._nav_btns.items():
            button.setStyleSheet(replica_nav_button_style(self._accent, key == self._active))
            button.setIconSize(QSize(8, 8))
            button.setIcon(_status_dot_icon(self._nav_icon_color(key)))

    def _nav_icon_color(self, key):
        game = GAME_TAB_KEYS.get(key)
        if game:
            return "#28c840" if self._game_detection.get(game, False) else "#ff5f57"
        return self._accent if key == self._active else MID

    def _select(self, key):
        self._active = key
        self._refresh_button_styles()
        self.page_selected.emit(key)

    def set_accent(self, ac):
        self._accent = ac
        self._refresh_button_styles()

    def refresh_game_detection(self):
        detected = detect_games()
        self._game_detection = {game: game in detected for game in GAME_TAB_KEYS.values()}
        for key, game in GAME_TAB_KEYS.items():
            button = self._nav_btns.get(key)
            if not button:
                continue
            if self._game_detection.get(game, False):
                button.setToolTip(f"{game} was detected on this PC.")
            else:
                button.setToolTip(f"{game} was not detected on this PC.")
        self._refresh_button_styles()

    def set_account_summary(self, name, plan, online=True):
        self._footer.setText(f"{name or 'guest'}\n{plan or 'No account'}")
        self._footer.setIcon(_status_dot_icon("#28c840" if online else MID))
        self._footer.setIconSize(QSize(9, 9))
        self._footer.setStyleSheet(
            "QPushButton{"
            f"background:transparent;color:{MID};border:none;border-top:1px solid {LINE};"
            f"padding:16px;text-align:left;font:13px '{UI_FONT}';"
            "}"
            f"QPushButton:hover{{background:{PANEL};color:{MAIN};}}"
        )

class RestoreWarnDialog(QWidget):
    confirmed  = pyqtSignal()
    go_restore = pyqtSignal()

    def __init__(self, accent, parent=None):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedWidth(360)
        self.setStyleSheet(f"QWidget{{background:{CARD};border:1px solid {REPLICA['line_soft']};border-radius:12px;}}")
        lay = QVBoxLayout(self); lay.setContentsMargins(24,22,24,22); lay.setSpacing(14)

        icon = QLabel("!"); icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("font:28px;border:none;background:transparent;")
        lay.addWidget(icon)

        title = QLabel("No Restore Point")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color:{MAIN};font:700 15pt '{TITLE_FONT}';border:none;background:transparent;")
        lay.addWidget(title)

        msg = QLabel("You have not created a restore point yet.\nIf something goes wrong, undoing changes will be harder.\n\nCreate one first or continue anyway.")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter); msg.setWordWrap(True)
        msg.setStyleSheet(f"color:{MAIN};font:500 10pt '{UI_FONT}';border:none;background:transparent;")
        lay.addWidget(msg)

        row = QHBoxLayout(); row.setSpacing(8)
        a = QPushButton("Create One First"); a.setFixedHeight(34); a.setCursor(Qt.CursorShape.PointingHandCursor)
        a.setStyleSheet(_solid(accent))
        a.clicked.connect(lambda: (self.go_restore.emit(), self.close()))

        b = QPushButton("Continue Anyway"); b.setFixedHeight(34); b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(_ghost(accent))
        b.clicked.connect(lambda: (self.confirmed.emit(), self.close()))

        row.addWidget(a, 1); row.addWidget(b, 1); lay.addLayout(row)
        self.adjustSize()

    def show_centered(self, parent_widget):
        if parent_widget:
            gp = parent_widget.mapToGlobal(QPointF(0,0)).toPoint()
            self.move(gp.x() + parent_widget.width()//2 - self.width()//2,
                      gp.y() + parent_widget.height()//2 - self.height()//2)
        self.show()

class RestorePage(QWidget):
    def __init__(self, get_ac, parent=None):
        super().__init__(parent); self._get_ac = get_ac
        self.setStyleSheet(f"background:{BG};")
        lay = QVBoxLayout(self); lay.setContentsMargins(22,18,22,18); lay.setSpacing(16)

        ac = self._get_ac()
        title = QLabel("Restore & Recovery")
        title.setStyleSheet(replica_title_style())
        lay.addWidget(title)
        lay.addWidget(_lbl("Keep a safe rollback path ready before you make deeper system changes.", MID, size=10))

        self._card = QFrame()
        self._card.setStyleSheet(replica_hero_style(ac))
        cl = QVBoxLayout(self._card); cl.setContentsMargins(18,16,18,16); cl.setSpacing(10)
        self._icon = QLabel(); self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter); self._icon.setStyleSheet("border:none;background:transparent;")
        self._head = QLabel(); self._head.setAlignment(Qt.AlignmentFlag.AlignCenter); self._head.setStyleSheet(f"color:{MAIN};font:700 11pt '{UI_FONT}';border:none;background:transparent;")
        self._sub  = QLabel(); self._sub.setWordWrap(True); self._sub.setAlignment(Qt.AlignmentFlag.AlignCenter); self._sub.setStyleSheet(f"color:{MID};font:500 9pt '{UI_FONT}';border:none;background:transparent;")
        cl.addWidget(self._icon); cl.addWidget(self._head); cl.addWidget(self._sub)
        lay.addWidget(self._card)

        self._make_btn = QPushButton("Create Restore Point")
        self._make_btn.setFixedHeight(40); self._make_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._make_btn.setStyleSheet(_solid(ac)); self._make_btn.clicked.connect(self._make_rp)
        lay.addWidget(self._make_btn)

        self._open_btn = QPushButton("Open System Restore")
        self._open_btn.setFixedHeight(36); self._open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_btn.setStyleSheet(_ghost(ac)); self._open_btn.clicked.connect(lambda: run_cmd("rstrui.exe"))
        lay.addWidget(self._open_btn)

        self._prog = _prog_bar(ac); self._prog.setVisible(False)
        self._stat = QLabel(""); self._stat.setStyleSheet(f"color:{MID};font:600 9pt '{UI_FONT}';border:none;")
        self._rp_result = None
        self._rp_poll = QTimer(self)
        self._rp_poll.timeout.connect(self._poll_restore_result)
        lay.addWidget(self._prog); lay.addWidget(self._stat)
        lay.addStretch(); self._refresh()

    def _refresh_legacy(self):
        if has_restore_point():
            self._icon.setText("READY"); self._icon.setStyleSheet(replica_badge_style("green"))
            self._head.setText("Restore Point Ready")
            self._sub.setText("A restore point is ready. If anything goes wrong, open System Restore and roll back.")
            self._card.setStyleSheet(replica_card_style("#16401a", radius=14, alt=True))
        else:
            self._icon.setText("NONE"); self._icon.setStyleSheet(replica_badge_style("gray"))
            self._head.setText("No Restore Point Yet")
            self._sub.setText("Create one before applying tweaks so you can undo changes if something goes wrong.")
            self._card.setStyleSheet(replica_card_style(REPLICA["line_soft"], radius=14, alt=True))

    def _refresh(self):
        self._card.setStyleSheet(replica_hero_style(self._get_ac()))
        if has_restore_point():
            self._icon.setText("READY")
            self._icon.setStyleSheet(replica_badge_style("green"))
            self._head.setText("Restore Point Ready")
            self._sub.setText("A restore point is ready. If anything goes wrong, open System Restore and roll back.")
        else:
            self._icon.setText("ALERT")
            self._icon.setStyleSheet(replica_badge_style("amber"))
            self._head.setText("No Restore Point Yet")
            self._sub.setText("Create one before applying tweaks so you can undo changes if something goes wrong.")

    def _make_rp(self):
        self._make_btn.setEnabled(False); self._make_btn.setText("Creating Restore Point...")
        self._prog.setVisible(True); self._prog.setValue(0); self._stat.setText("Creating restore point...")
        self._stat.setStyleSheet(f"color:{MID};font:600 9pt '{UI_FONT}';border:none;")
        self._rp_result = None
        import threading
        def _do():
            self._rp_result = create_restore_point()
        threading.Thread(target=_do, daemon=True).start()
        self._rp_poll.start(200)

    def _poll_restore_result(self):
        if self._rp_result is None:
            return
        self._rp_poll.stop()
        ok, message = self._rp_result
        self._prog.setValue(100 if ok else 0)
        self._stat.setText(message)
        self._stat.setStyleSheet((f"color:{MAIN}" if ok else f"color:{MID}") + f";font:600 9pt '{UI_FONT}';border:none;")
        self._make_btn.setEnabled(True); self._make_btn.setText("Create Restore Point"); self._refresh()
        QTimer.singleShot(5000, lambda: (self._prog.setVisible(False), self._stat.setText(""),
            self._stat.setStyleSheet(f"color:{MID};font:600 9pt '{UI_FONT}';border:none;")))

    def update_accent(self, color):
        self._card.setStyleSheet(replica_hero_style(color))
        apply_glass_shadow(self._card, color, blur=46, y=16, alpha=70)
        self._make_btn.setStyleSheet(_solid(color))
        self._open_btn.setStyleSheet(_ghost(color))
        self._prog.setStyleSheet(
            f"QProgressBar{{background:{REPLICA['surface_alt']};border:none;border-radius:3px;}}"
            "QProgressBar::chunk{background:#f5f7fa;border-radius:3px;}"
        )

# tweaks
class TweakPage(QWidget):
    tweaks_applied = pyqtSignal()
    catalog_changed = pyqtSignal()

    def __init__(self, cat, get_ac, parent=None, provider=None, title=None):
        super().__init__(parent); self._cat = cat; self._get_ac = get_ac
        self._provider = provider or (lambda cat=cat: category_entries(cat))
        self._title = title or cat
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._worker = None
        self._plan_active = False
        self._rows = []
        root = QVBoxLayout(self); root.setContentsMargins(32,28,32,0); root.setSpacing(0)

        hdr = QFrame()
        self._hdr = hdr
        hdr.setStyleSheet("QFrame{background:transparent;border:none;}")
        hl = QVBoxLayout(hdr); hl.setContentsMargins(0,0,0,0); hl.setSpacing(4)
        ac = self._get_ac()
        top = QHBoxLayout(); top.setContentsMargins(0,0,0,0); top.setSpacing(8)
        title_col = QVBoxLayout(); title_col.setContentsMargins(0,0,0,0); title_col.setSpacing(2)
        self._section_lbl = QLabel(self._section_caption())
        self._section_lbl.setStyleSheet(f"color:{MID};font:11px '{MONO_FONT}';border:none;")
        self._cat_lbl = QLabel(self._title)
        self._cat_lbl.setStyleSheet(replica_title_style())
        title_col.addWidget(self._cat_lbl)
        title_col.addWidget(self._section_lbl)
        top.addLayout(title_col)
        top.addStretch()
        self._count_lbl = QLabel("0 tweaks")
        self._count_lbl.setStyleSheet(replica_badge_style("cyan"))
        self._count_lbl.hide()
        hl.addLayout(top)
        self._summary = _lbl("", MID, size=10)
        self._summary.hide()
        self._search = QLineEdit()
        self._search.setPlaceholderText("filter tweaks...")
        self._search.setFixedHeight(34)
        self._search.setClearButtonEnabled(True)
        self._search.setStyleSheet(replica_input_style(ac))
        self._search.textChanged.connect(self._apply_filters)
        tools = QHBoxLayout(); tools.setContentsMargins(0,20,0,0); tools.setSpacing(8)
        tools.addWidget(self._search, 1)
        for label, fn, attr in [("all", lambda: self._set_all(True), "_all_btn"),
                                ("none", lambda: self._set_all(False), "_none_btn"),
                                ("check", self._refresh_statuses, "_status_btn")]:
            width = 68 if label == "check" else 62
            b = QPushButton(label); b.setFixedSize(width, 34); b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_ghost(ac)); b.clicked.connect(fn); tools.addWidget(b)
            setattr(self, attr, b)
        hl.addLayout(tools)
        root.addWidget(hdr)

        sc = SmoothScrollArea(); sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        sc.setStyleSheet(f"QScrollArea{{border:none;background:{BG};}}"
                         f"QScrollBar:vertical{{background:transparent;width:7px;border:none;margin:6px 0 6px 0;}}"
                         f"QScrollBar::handle:vertical{{background:{_rgba('#dfe7f6', 60)};border:1px solid {_rgba('#ffffff', 36)};border-radius:3px;min-height:26px;}}"
                         f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}")
        self._sc = sc
        cw = QWidget(); cw.setStyleSheet(f"background:transparent;")
        cw.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        self._cw = cw
        lst = QVBoxLayout(cw); lst.setContentsMargins(0,20,0,0); lst.setSpacing(1)
        self._lst = lst
        sc.setWidget(cw); root.addWidget(sc, 1)

        bot = QWidget(); bot.setFixedHeight(66)
        bot.setStyleSheet("background:transparent;border:none;")
        bl = QHBoxLayout(bot); bl.setContentsMargins(0,18,0,0); bl.setSpacing(10)
        self._stat = QLabel("0 selected"); self._stat.setStyleSheet(f"color:{DIM};font:11px '{MONO_FONT}';border:none;")
        self._prog = _prog_bar(ac); self._prog.setFixedWidth(120); self._prog.setVisible(True)
        self._abtn = QPushButton("apply selected"); self._abtn.setFixedSize(126, 34)
        self._abtn.setCursor(Qt.CursorShape.PointingHandCursor); self._abtn.setStyleSheet(_solid(ac))
        self._abtn.clicked.connect(self._apply)
        bl.addWidget(self._stat)
        bl.addWidget(self._prog)
        bl.addStretch()
        bl.addWidget(self._abtn)
        root.addWidget(bot)
        self._build_entries()
        self._status_worker = None
        self._status_active = False
        self._status_poll = QTimer(self)
        self._status_poll.setInterval(4000)
        self._status_poll.timeout.connect(self._start_status_worker)

    def _section_caption(self):
        mapping = {
            "FPS Boost": "PERFORMANCE TUNING",
            "CPU": "PERFORMANCE TUNING",
            "GPU": "PERFORMANCE TUNING",
            "RAM": "PERFORMANCE TUNING",
            "Input": "PERFORMANCE TUNING",
            "Network": "NETWORK",
            "Power": "NETWORK",
            "Privacy": "PRIVACY & SECURITY",
            "Debloat": "PRIVACY & SECURITY",
            "Visual": "PRIVACY & SECURITY",
            "Services": "MAINTENANCE",
            "Cleanup": "MAINTENANCE",
        }
        return mapping.get(self._cat, "TWEAKS")

    def _clear_rows(self):
        while self._lst.count():
            item = self._lst.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _status_style(self, color):
        return f"background:{color};border:none;border-radius:3px;"

    def _restart_style(self):
        return f"color:{MID};font:10px '{MONO_FONT}';border:1px solid {LINE};border-radius:2px;padding:2px 6px;background:transparent;"

    def _make_tip_button(self, tip, color):
        is_warn = "[!]" in tip or "warning:" in str(tip).lower()
        qb = QPushButton("!" if is_warn else "?")
        qb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        qb.setFixedSize(24, 24); qb.setCursor(Qt.CursorShape.PointingHandCursor); qb.setToolTip(tip)
        bc = MAIN if is_warn else MID
        bbo = REPLICA["line_soft"]
        hover = MAIN
        qb.setStyleSheet(f"QPushButton{{background:transparent;color:{bc};border:1px solid {bbo};border-radius:12px;font:700 8pt '{UI_FONT}';}}"
                         f"QPushButton:hover{{color:{hover};border-color:{hover};}}")
        return qb

    def _build_entries(self):
        self._clear_rows()
        self._rows = []
        self._sw = []
        ac = self._get_ac()
        selected = load_selected_tweaks()
        entries = list(self._provider())
        self._entries = entries
        for i, entry in enumerate(entries):
            row = QFrame(); row.setFixedHeight(72)
            row.setStyleSheet(
                f"QFrame{{background:{PANEL};border:none;border-radius:3px;}}"
                f"QFrame:hover{{background:{SURFACE2};}}"
            )
            rl = QHBoxLayout(row); rl.setContentsMargins(14,10,14,10); rl.setSpacing(12)

            sw = Toggle(ac); sw.setChecked(entry["id"] in selected); rl.addWidget(sw)

            name_lbl = FitLabel(entry["name"], color=MAIN, size=13)
            desc_lbl = QLabel(f"{str(entry.get('category', '')).lower()} / {str(entry.get('desc', '')).lower()}")
            desc_lbl.setWordWrap(True)
            desc_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            desc_lbl.setMinimumHeight(26)
            desc_lbl.setStyleSheet(f"color:{DIM};font:12px '{MONO_FONT}';border:none;background:transparent;")
            text_col = QVBoxLayout()
            text_col.setContentsMargins(0,0,0,0)
            text_col.setSpacing(2)
            text_col.addWidget(name_lbl)
            text_col.addWidget(desc_lbl)
            rl.addLayout(text_col, 1)

            status_lbl = QLabel("")
            status_lbl.setFixedSize(6, 6)
            status_lbl.setToolTip("Unknown")
            status_lbl.setStyleSheet(self._status_style("#4b5563"))

            restart_lbl = None
            if entry.get("restart"):
                restart_lbl = QLabel(str(entry["restart"]).lower())
                restart_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                restart_lbl.setToolTip(entry["restart"])
                restart_lbl.setStyleSheet(self._restart_style())
                rl.addWidget(restart_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

            rl.addWidget(status_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

            sw.clicked.connect(lambda _=False, key=entry["id"], toggle=sw: self._on_toggle(key, toggle.isChecked()))
            self._lst.addWidget(row)
            self._rows.append({
                "entry": entry,
                "row": row,
                "toggle": sw,
                "status": status_lbl,
                "restart": restart_lbl,
                "name": name_lbl,
                "desc": desc_lbl,
            })
            self._sw.append(sw)
        self._apply_filters()
        self._update_apply_info()

    def _set_all(self, enabled):
        changed = set(load_selected_tweaks())
        for row in self._rows:
            row["toggle"].setChecked(enabled)
            if enabled:
                changed.add(row["entry"]["id"])
            else:
                changed.discard(row["entry"]["id"])
        set_selected_tweaks(changed)
        self._update_apply_info()
        self.catalog_changed.emit()

    def _on_toggle(self, tweak_id, enabled):
        set_tweak_selected(tweak_id, enabled)
        self._update_apply_info()
        self.catalog_changed.emit()

    def _apply_filters(self):
        query = self._search.text().strip().lower()
        shown = 0
        for row in self._rows:
            entry = row["entry"]
            hay = f'{entry["name"]} {entry["desc"]} {entry["category"]}'.lower()
            visible = not query or query in hay
            row["row"].setVisible(visible)
            shown += 1 if visible else 0
        total = len(self._rows)
        self._section_lbl.setText(f"{self._section_caption().lower()} / {total} tweaks")
        self._count_lbl.setText(f"{shown}/{len(self._rows)} tweaks" if query else f"{len(self._rows)} tweaks")

    def _update_apply_info(self):
        selected = len(self._selected_entries())
        total = max(1, len(self._rows))
        self._stat.setText(f"{selected} selected")
        self._prog.setValue(int((selected / total) * 100))

    def activate(self):
        self._status_active = True
        if not self._status_poll.isActive():
            self._status_poll.start()
        self._build_entries()
        self._refresh_statuses()

    def deactivate(self):
        self._status_active = False
        self._status_poll.stop()

    def _selected_entries(self):
        return [row["entry"] for row in self._rows if row["toggle"].isChecked()]

    def _start_status_worker(self):
        # skip if a worker is already running or no rows exist
        if not self._status_active:
            return
        if self._status_worker and self._status_worker.isRunning():
            return
        if not self._rows:
            return
        entries = [row["entry"] for row in self._rows]
        self._status_worker = StatusWorker(entries)
        self._status_worker.result.connect(self._apply_statuses)
        self._status_worker.start()

    def _apply_statuses(self, results):
        for row, (text, color) in zip(self._rows, results):
            row["status"].setToolTip(text)
            row["status"].setStyleSheet(self._status_style(color))

    def _refresh_statuses(self):
        self._start_status_worker()


    def _apply(self):
        sel = self._selected_entries()
        if not sel: return
        if not self._plan_active:
            self._stat.setText("redeem a key to unlock tweaks.")
            self._stat.setStyleSheet(f"color:{MID};font:11px '{MONO_FONT}';border:none;")
            return
        if not has_restore_point():
            dlg = RestoreWarnDialog(self._get_ac(), self)
            dlg.confirmed.connect(self._run); dlg.go_restore.connect(self._go_restore)
            dlg.show_centered(self)
        else: self._run()

    def _go_restore(self):
        p = self.parent()
        while p and not isinstance(p, Dashboard): p = p.parent()
        if p: p._sidebar._select("restore")

    def _run(self):
        sel = self._selected_entries()
        if not sel: return
        if not self._plan_active:
            self._stat.setText("no active plan.")
            return
        ac = self._get_ac()
        self._abtn.setEnabled(False); self._abtn.setText("working...")
        self._prog.setVisible(True); self._prog.setValue(0)
        self._prog.setStyleSheet(_prog_bar(ac).styleSheet())
        self._worker = TweakWorker(sel)
        self._worker.progress.connect(lambda i,n,nm: (self._prog.setValue(int(i/n*100)), self._stat.setText(str(nm).lower())))
        self._worker.detail.connect(lambda txt: self._stat.setText(str(txt).lower()))
        self._worker.done.connect(self._done); self._worker.start()

    def _done(self):
        hints = {entry.get("restart", "") for entry in self._selected_entries() if entry.get("restart")}
        msg = "Done."
        if "restart" in hints:
            msg = "Done. Restart your PC."
        elif "sign out" in hints:
            msg = "Done. Sign out to fully apply."
        elif "relaunch game" in hints or "relaunch app" in hints:
            msg = "Done. Restart the game or app."
        self._prog.setValue(100); self._stat.setText(msg)
        self._stat.setStyleSheet(f"color:{MID};font:11px '{MONO_FONT}';border:none;")
        self._abtn.setEnabled(True); self._abtn.setText("apply selected")
        self._refresh_statuses()
        self.tweaks_applied.emit()
        self.catalog_changed.emit()
        QTimer.singleShot(2000, self._update_apply_info)

    def update_accent(self, color):
        self._hdr.setStyleSheet("QFrame{background:transparent;border:none;}")
        self._section_lbl.setStyleSheet(f"color:{MID};font:11px '{MONO_FONT}';border:none;")
        self._cat_lbl.setStyleSheet(replica_title_style())
        self._count_lbl.setStyleSheet(replica_badge_style("cyan"))
        self._search.setStyleSheet(replica_input_style(color))
        for btn_name in ["_all_btn", "_none_btn", "_status_btn"]:
            getattr(self, btn_name).setStyleSheet(_ghost(color))
        self._abtn.setStyleSheet(_solid(color))
        self._prog.setStyleSheet(_prog_bar(color).styleSheet())
        for idx, row in enumerate(self._rows):
            row["toggle"].set_accent(color)
            row["row"].setStyleSheet(
                f"QFrame{{background:{PANEL};border:none;border-radius:3px;}}"
                f"QFrame:hover{{background:{SURFACE2};}}"
            )

    def set_plan_active(self, active):
        self._plan_active = bool(active)
        self._abtn.setEnabled(bool(active))
        if not active:
            self._abtn.setText("locked")
            self._stat.setText("redeem a key to unlock this page.")
        else:
            self._abtn.setText("apply selected")
            self._update_apply_info()

class MiniStat(QFrame):
    action_clicked = pyqtSignal()

    # animated float property for the progress bar
    def _get_anim_val(self): return self._anim_val
    def _set_anim_val(self, v):
        self._anim_val = v
        self._bar.setValue(int(v))
        self._val.setText(f"{v:.0f}%")
    _anim_prop = pyqtProperty(float, fget=_get_anim_val, fset=_set_anim_val)

    def __init__(self, label, _accent, action_text=None):
        super().__init__()
        self._anim_val = 0.0
        self._accent   = _accent
        self._action_text = action_text or ""
        self._action_btn = None
        self.setMinimumHeight(108)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(f"QFrame{{background:{PANEL};border:1px solid {LINE};border-radius:4px;}}")

        lay = QVBoxLayout(self); lay.setContentsMargins(16,14,16,14); lay.setSpacing(8)

        row = QHBoxLayout()
        self._val = QLabel("--")
        self._val.setStyleSheet(f"color:{MAIN};font:700 24px '{TITLE_FONT}';border:none;")
        row.addWidget(self._val); row.addStretch()

        self._bar = _prog_bar(_accent)
        self._lbl = QLabel(str(label).upper())
        bottom = QHBoxLayout(); bottom.setContentsMargins(0,0,0,0); bottom.setSpacing(6)
        bottom.addWidget(self._lbl)
        bottom.addStretch()
        if action_text:
            self._action_btn = QPushButton(action_text)
            self._action_btn.setFixedSize(72, 22)
            self._action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._action_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._action_btn.setAutoDefault(False)
            self._action_btn.setDefault(False)
            self._action_btn.clicked.connect(self.action_clicked.emit)
            bottom.addWidget(self._action_btn)

        lay.addLayout(row); lay.addWidget(self._bar); lay.addLayout(bottom)

        self._anim = QPropertyAnimation(self, b"_anim_prop", self)
        self._anim.setDuration(600)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.update_accent(_accent)

    def update_val(self, v):
        if self._anim.state() == QAbstractAnimation.State.Running:
            self._anim.stop()
        self._anim.setStartValue(self._anim_val)
        self._anim.setEndValue(float(v))
        self._anim.start()

    def update_accent(self, color):
        self._accent = color
        self._bar.setStyleSheet(
            f"QProgressBar{{background:{REPLICA['surface_alt']};border:none;border-radius:3px;}}"
            "QProgressBar::chunk{background:#f5f7fa;border-radius:3px;}")
        self._lbl.setStyleSheet(f"color:{MID};font:700 8pt '{UI_FONT}';border:none;letter-spacing:0.9px;")
        if self._action_btn:
            self._action_btn.setStyleSheet(
                f"QPushButton{{background:{_rgba('#ffffff', 6)};color:{MAIN};border:1px solid {_rgba('#ffffff', 42)};"
                f"border-radius:9px;font:700 7.5pt '{UI_FONT}';padding:0 8px;}}"
                f"QPushButton:hover{{background:{_rgba('#ffffff', 12)};border-color:{_rgba('#ffffff', 70)};}}"
                f"QPushButton:disabled{{color:{MID};border-color:{LINE};background:transparent;}}")

    def set_action_state(self, enabled, text=None):
        if not self._action_btn:
            return
        self._action_btn.setEnabled(enabled)
        self._action_btn.setText(text or self._action_text)

def _lbl(text, color=MAIN, bold=False, size=12, spacing=0):
    l = QLabel(text)
    weight = "600 " if bold else "400 "
    l.setStyleSheet(f"color:{color};font:{weight}{size}px '{UI_FONT}';"
                    + (f"letter-spacing:{spacing}px;" if spacing else "") + "border:none;background:transparent;")
    return l

class FitLabel(QLabel):
    def __init__(self, text="", color=MAIN, size=8, bold=True, parent=None):
        super().__init__(parent)
        self._full_text = ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        weight = "700 " if bold else "500 "
        self.setStyleSheet(f"color:{color};font:{weight}{size}px '{UI_FONT}';border:none;background:transparent;")
        self.setText(text)

    def setText(self, text):
        self._full_text = "" if text is None else str(text)
        self._update_elide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_elide()

    def _update_elide(self):
        fm = QFontMetrics(self.font())
        available = max(0, self.contentsRect().width())
        txt = self._full_text
        if available <= 0:
            super().setText(txt)
            return
        if fm.horizontalAdvance(txt) <= available:
            super().setText(txt)
            return
        shortened = txt
        while shortened and fm.horizontalAdvance(shortened + "...") > available:
            shortened = shortened[:-1]
        super().setText((shortened + "...") if shortened else "...")

def _prog_bar(ac):
    b = QProgressBar(); b.setRange(0,100); b.setValue(0); b.setFixedHeight(2); b.setTextVisible(False)
    b.setStyleSheet(
        f"QProgressBar{{background:{LINE};border:none;border-radius:1px;}}"
        f"QProgressBar::chunk{{background:{ac};border-radius:1px;}}"
    )
    return b

def _irow(label, value, *, label_width=96, row_height=40, label_px=9, value_size=11):
    w = QFrame(); w.setStyleSheet(f"QFrame{{background:{PANEL};border:1px solid {LINE};border-radius:4px;}}"); w.setFixedHeight(row_height)
    w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    lay = QHBoxLayout(w); lay.setContentsMargins(12,0,12,0); lay.setSpacing(10)
    kl = QLabel(label); kl.setFixedWidth(label_width); kl.setStyleSheet(f"color:{DIM};font:500 {label_px}px '{MONO_FONT}';border:none;letter-spacing:1px;")
    vl = FitLabel(value, color=MAIN, size=value_size)
    lay.addWidget(kl); lay.addWidget(vl, 1); return w, vl

# -- PC Optimization Score --------------------------------------------------
def _score_checks():
    """Returns score checks using only real tweak names that exist in the Tweaker."""
    if os.name != "nt":
        return [("windows only", False)]

    checks = []
    active_scheme = (_active_power_scheme() or "").lower()

    def add(label, passed):
        checks.append((label, bool(passed)))

    def entry_for(category, name):
        return find_tweak_entry(tweak_key(category, name))

    def add_tweak(category, name, passed=None):
        entry = entry_for(category, name)
        if not entry:
            return
        if passed is None:
            statuses = [command_status(cmd) for cmd in entry["cmds"]]
            known = [status for status in statuses if status is not None]
            if not known:
                return
            passed = all(known)
        add(entry["name"], passed)

    def add_service_tweak(category, name, service_name):
        entry = entry_for(category, name)
        state = _query_service_start(service_name)
        if not entry or state is None:
            return
        add(entry["name"], state == "disabled")

    perf_plan_active = any(token in active_scheme for token in (
        "high performance",
        "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
        "e9a42b02-d5df-448d-aa00-03f14749eb61",
    ))

    for category, name in [
        ("Network", "Nagle OFF"),
        ("Network", "Net Throttle OFF"),
        ("GPU", "HAGS ON"),
        ("GPU", "GPU Priority 8"),
        ("GPU", "FSO OFF"),
        ("GPU", "TDR 10s"),
        ("GPU", "TDR DDI Delay 20"),
        ("GPU", "MPO OFF"),
        ("GPU", "Preemption OFF"),
        ("CPU", "Latency Priority"),
        ("CPU", "Power Throttle OFF"),
        ("CPU", "Timer 0.5ms"),
        ("CPU", "Dynamic Tick OFF"),
        ("CPU", "HPET OFF"),
        ("CPU", "Paging Exec OFF"),
        ("RAM", "Prefetch OFF"),
        ("RAM", "NDU OFF"),
        ("RAM", "Pagefile Clear OFF"),
        ("RAM", "Large System Cache OFF"),
        ("Input", "Mouse Accel OFF"),
        ("Input", "KB Speed MAX"),
        ("Input", "Sticky Keys OFF"),
        ("Input", "Filter Keys OFF"),
        ("Input", "Menu Delay 0"),
        ("FPS Boost", "Game Bar OFF"),
        ("FPS Boost", "Game Mode ON"),
        ("FPS Boost", "BG Apps OFF"),
        ("FPS Boost", "Notifs OFF"),
        ("FPS Boost", "MMCSS Priority"),
        ("Power", "Fast Startup OFF"),
        ("Visual", "Animations OFF"),
        ("Visual", "Transparency OFF"),
        ("Visual", "VisualFX Best Performance"),
        ("Visual", "Aero Peek OFF"),
    ]:
        add_tweak(category, name)

    add_tweak("CPU", "High Performance Plan", perf_plan_active)
    add_service_tweak("RAM", "Superfetch OFF", "SysMain")
    add_service_tweak("FPS Boost", "Search OFF", "WSearch")

    return checks


def _score_missing_labels(checks):
    return [label for label, ok in checks if not ok]


def _score_missing_summary(checks, limit=3):
    missing = _score_missing_labels(checks)
    if not missing:
        return "all tracked optimizations applied"
    preview = ", ".join(missing[:limit])
    if len(missing) > limit:
        preview += " ..."
    return f"missing: {preview}"


def _score_missing_tooltip(checks):
    missing = _score_missing_labels(checks)
    if not missing:
        return "No missing optimizations detected."
    return "\n".join(["Missing optimizations:"] + [f"[ ] {label}" for label in missing])


_BENEFIT_CACHE = {"ts": 0.0, "profile": None}


def _clamp_pct(value):
    try:
        return int(max(0, min(100, round(float(value)))))
    except Exception:
        return 0


def _benefit_gap(check_map, labels):
    states = [check_map[label] for label in labels if label in check_map]
    if not states:
        return 0
    missing = sum(1 for ok in states if not ok)
    return _clamp_pct((missing / len(states)) * 100)


def _benefit_score(gap_pct, support_pct):
    gap_pct = max(0.0, min(100.0, float(gap_pct)))
    support_pct = max(0.0, min(100.0, float(support_pct)))
    return _clamp_pct(gap_pct * (0.65 + 0.45 * (support_pct / 100.0)))


def _benefit_support_profile():
    mem = psutil.virtual_memory()
    total_gb = mem.total / 1073741824
    ram_used_pct = float(mem.percent)
    process_count = len(psutil.pids())
    cpu_load = stable_cpu_percent()
    physical_cores = psutil.cpu_count(logical=False) or psutil.cpu_count() or 4
    detected_games = sorted(game for game in detect_games() if game in GAME_TAB_KEYS.values())
    competitive_titles = {"Valorant", "FiveM", "CS2", "Fortnite", "Apex", "Roblox"}
    competitive_count = sum(1 for game in detected_games if game in competitive_titles)

    if total_gb <= 8:
        ram_sensitivity = 100
    elif total_gb <= 12:
        ram_sensitivity = 90
    elif total_gb <= 16:
        ram_sensitivity = 76
    elif total_gb <= 24:
        ram_sensitivity = 60
    elif total_gb <= 32:
        ram_sensitivity = 46
    else:
        ram_sensitivity = 32

    if physical_cores <= 4:
        cpu_sensitivity = 100
    elif physical_cores <= 6:
        cpu_sensitivity = 88
    elif physical_cores <= 8:
        cpu_sensitivity = 76
    elif physical_cores <= 12:
        cpu_sensitivity = 60
    else:
        cpu_sensitivity = 42

    if ram_used_pct >= 85:
        ram_pressure = 100
    elif ram_used_pct >= 75:
        ram_pressure = 88
    elif ram_used_pct >= 65:
        ram_pressure = 74
    elif ram_used_pct >= 55:
        ram_pressure = 58
    else:
        ram_pressure = 36

    if process_count >= 240:
        process_pressure = 100
    elif process_count >= 200:
        process_pressure = 88
    elif process_count >= 160:
        process_pressure = 74
    elif process_count >= 125:
        process_pressure = 58
    else:
        process_pressure = 36

    if cpu_load >= 55:
        cpu_pressure = 100
    elif cpu_load >= 35:
        cpu_pressure = 82
    elif cpu_load >= 20:
        cpu_pressure = 62
    else:
        cpu_pressure = 36

    if len(detected_games) >= 4:
        game_relevance = 95
    elif len(detected_games) == 3:
        game_relevance = 82
    elif len(detected_games) == 2:
        game_relevance = 68
    elif len(detected_games) == 1:
        game_relevance = 54
    else:
        game_relevance = 18

    if competitive_count >= 4:
        competitive_factor = 100
    elif competitive_count == 3:
        competitive_factor = 86
    elif competitive_count == 2:
        competitive_factor = 74
    elif competitive_count == 1:
        competitive_factor = 58
    else:
        competitive_factor = 18

    return {
        "mem_total_gb": total_gb,
        "mem_percent": ram_used_pct,
        "process_count": process_count,
        "cpu_load": cpu_load,
        "physical_cores": physical_cores,
        "detected_games": detected_games,
        "competitive_count": competitive_count,
        "ram_sensitivity": ram_sensitivity,
        "cpu_sensitivity": cpu_sensitivity,
        "ram_pressure": ram_pressure,
        "process_pressure": process_pressure,
        "cpu_pressure": cpu_pressure,
        "game_relevance": game_relevance,
        "competitive_factor": competitive_factor,
    }


def _benefit_summary(profile):
    top = sorted(profile["areas"].items(), key=lambda item: item[1], reverse=True)[:3]
    if not top:
        return "already fairly optimized for this pc"
    joined = ", ".join(f"{label.lower()} {pct}%" for label, pct in top)
    return f"best gains: {joined}"


def _benefit_tooltip(profile):
    lines = [
        "Hextra Benefit (estimated for this PC)",
        f"Overall: {profile['overall']}%",
        "",
        "Breakdown:",
    ]
    for label, pct in profile["areas"].items():
        lines.append(f"- {label}: {pct}%")
    lines.append("")
    lines.append("Why:")
    for reason in profile["reasons"]:
        lines.append(f"- {reason}")
    return "\n".join(lines)


def _benefit_profile(force=False):
    global _BENEFIT_CACHE
    now = time.time()
    cached = _BENEFIT_CACHE.get("profile")
    if not force and cached and (now - float(_BENEFIT_CACHE.get("ts") or 0.0)) < 8.0:
        return cached

    checks = _score_checks()
    check_map = {label: ok for label, ok in checks}
    missing_count = sum(1 for _, ok in checks if not ok)
    total_checks = max(1, len(checks))
    support = _benefit_support_profile()

    area_labels = {
        "Latency": [
            "Mouse Accel OFF", "KB Speed MAX", "Sticky Keys OFF", "Filter Keys OFF", "Menu Delay 0",
            "Nagle OFF", "Net Throttle OFF", "Latency Priority", "Power Throttle OFF",
            "Timer 0.5ms", "Dynamic Tick OFF", "MMCSS Priority", "Game Mode ON",
        ],
        "FPS Stability": [
            "Game Bar OFF", "Game Mode ON", "BG Apps OFF", "Notifs OFF", "MMCSS Priority",
            "HAGS ON", "GPU Priority 8", "FSO OFF", "MPO OFF", "Preemption OFF",
            "VisualFX Best Performance", "Animations OFF", "Transparency OFF", "Aero Peek OFF",
        ],
        "Background Load": [
            "BG Apps OFF", "Search OFF", "Superfetch OFF", "Notifs OFF", "Game Bar OFF",
            "Animations OFF", "Transparency OFF", "VisualFX Best Performance",
            "Pagefile Clear OFF", "NDU OFF", "Large System Cache OFF",
        ],
        "Game Tweaks": [
            "HAGS ON", "GPU Priority 8", "FSO OFF", "Game Mode ON", "MMCSS Priority",
            "Nagle OFF", "Mouse Accel OFF", "Timer 0.5ms", "Latency Priority",
        ],
    }

    area_support = {
        "Latency": (
            support["cpu_sensitivity"] * 0.32
            + support["competitive_factor"] * 0.22
            + support["cpu_pressure"] * 0.18
            + support["process_pressure"] * 0.16
            + support["ram_pressure"] * 0.12
        ),
        "FPS Stability": (
            support["cpu_sensitivity"] * 0.24
            + support["ram_sensitivity"] * 0.18
            + support["ram_pressure"] * 0.18
            + support["process_pressure"] * 0.16
            + support["competitive_factor"] * 0.24
        ),
        "Background Load": (
            support["process_pressure"] * 0.40
            + support["ram_pressure"] * 0.30
            + support["cpu_pressure"] * 0.18
            + support["ram_sensitivity"] * 0.12
        ),
        "Game Tweaks": (
            support["game_relevance"] * 0.42
            + support["competitive_factor"] * 0.24
            + support["cpu_sensitivity"] * 0.18
            + support["ram_sensitivity"] * 0.16
        ),
    }

    areas = {}
    for label, labels in area_labels.items():
        gap = _benefit_gap(check_map, labels)
        areas[label] = _benefit_score(gap, area_support[label])

    overall_gap = _clamp_pct((missing_count / total_checks) * 100)
    overall_support = (sum(area_support.values()) / max(1, len(area_support)))
    overall = _benefit_score(overall_gap, overall_support)

    reasons = []
    reasons.append(f"{missing_count} of {total_checks} tracked tweaks are still missing")
    reasons.append(f"{support['physical_cores']} physical CPU cores and {support['mem_total_gb']:.0f} GB RAM detected")
    if support["process_count"] >= 125:
        reasons.append(f"{support['process_count']} background processes are running right now")
    else:
        reasons.append("background process count is already fairly low")
    if support["detected_games"]:
        shown = ", ".join(support["detected_games"][:3])
        if len(support["detected_games"]) > 3:
            shown += " ..."
        reasons.append(f"detected supported games: {shown}")
    else:
        reasons.append("no supported games were detected automatically")
    if overall <= 20:
        reasons.append("this system already looks fairly optimized, so gains may stay small")

    profile = {
        "overall": overall,
        "areas": areas,
        "summary": _benefit_summary({"areas": areas}),
        "tooltip": "",
        "reasons": reasons[:5],
    }
    profile["tooltip"] = _benefit_tooltip(profile)
    _BENEFIT_CACHE = {"ts": now, "profile": profile}
    return profile


class ScoreWidget(QFrame):
    def _get_anim_val(self): return self._anim_val
    def _set_anim_val(self, v):
        self._anim_val = v
        self._score_lbl.setText(f"{v:.0f}%")
    _anim_prop = pyqtProperty(float, fget=_get_anim_val, fset=_set_anim_val)

    def __init__(self, accent, parent=None):
        super().__init__(parent)
        self._accent   = accent
        self._anim_val = 0.0
        self._locked   = False
        self.setMinimumHeight(96)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(replica_card_style(REPLICA["line_soft"], radius=14, alt=True))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("click to refresh benefit estimate")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)

        row = QHBoxLayout()
        self._score_lbl = QLabel("--")
        self._score_lbl.setStyleSheet(f"color:{MAIN};font:700 24px '{TITLE_FONT}';border:none;")
        self._grade_lbl = QLabel("")
        self._grade_lbl.setStyleSheet(f"color:{MID};font:700 9pt '{UI_FONT}';border:none;")
        row.addWidget(self._score_lbl)
        row.addStretch()
        row.addWidget(self._grade_lbl)

        self._bar = _prog_bar(accent)
        self._tag = QLabel("HEXTRA BENEFIT")
        self._tag.setStyleSheet(f"color:{MID};font:700 8pt '{UI_FONT}';border:none;letter-spacing:0.9px;")

        lay.addLayout(row)
        lay.addWidget(self._bar)
        lay.addWidget(self._tag)

        self._anim = QPropertyAnimation(self, b"_anim_prop", self)
        self._anim.setDuration(700)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.refresh()

    def refresh(self, force=False):
        if self._locked:
            return
        profile = _benefit_profile(force=force)
        pct = int(profile["overall"])

        if pct >= 70:
            grade, gcol = "HIGH VALUE", "#f5f7fa"
        elif pct >= 45:
            grade, gcol = "GOOD FIT", "#d4d4d8"
        elif pct >= 25:
            grade, gcol = "SOME VALUE", "#a1a1aa"
        else:
            grade, gcol = "LOW GAIN", "#71717a"

        self._grade_lbl.setText(grade)
        self._grade_lbl.setStyleSheet(f"color:{gcol};font:700 9pt '{UI_FONT}';border:none;")

        if self._anim.state() == QAbstractAnimation.State.Running:
            self._anim.stop()
        self._anim.setStartValue(self._anim_val)
        self._anim.setEndValue(float(pct))
        self._anim.start()
        self.setToolTip(profile["tooltip"])

    def update_accent(self, color):
        self._accent = color
        self._bar.setStyleSheet(
            f"QProgressBar{{background:{REPLICA['surface_alt']};border:none;border-radius:2px;}}"
            "QProgressBar::chunk{background:#f5f7fa;border-radius:2px;}")
        self._tag.setStyleSheet(f"color:{MID};font:700 8pt '{UI_FONT}';border:none;letter-spacing:0.9px;")

    def mousePressEvent(self, e):
        if self._locked:
            return
        self.refresh(force=True)
        super().mousePressEvent(e)

    def set_plan_active(self, active):
        self._locked = False
        self._tag.setText("HEXTRA BENEFIT")
        self.refresh(force=True)


class OverviewScoreCard(QFrame):
    def _get_anim_val(self): return self._anim_val
    def _set_anim_val(self, v):
        self._anim_val = v
        self._score_lbl.setText(f"{v:.0f}%")
    _anim_prop = pyqtProperty(float, fget=_get_anim_val, fset=_set_anim_val)

    def __init__(self, accent, parent=None):
        super().__init__(parent)
        self._accent = accent
        self._anim_val = 0.0
        self._locked = False
        self.setMinimumHeight(108)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(f"QFrame{{background:{PANEL};border:1px solid {LINE};border-radius:4px;}}")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("click to refresh benefit estimate")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(4)

        self._tag = QLabel("Hextra Benefit")
        self._tag.setStyleSheet(f"color:{DIM};font:500 9px '{MONO_FONT}';letter-spacing:1.2px;border:none;background:transparent;")
        self._score_lbl = QLabel("--")
        self._score_lbl.setStyleSheet(f"color:{MAIN};font:300 22pt '{UI_FONT}';border:none;background:transparent;")
        self._sub_lbl = QLabel("estimated value for this pc")
        self._sub_lbl.setStyleSheet(f"color:{MID};font:10px '{UI_FONT}';border:none;background:transparent;")

        lay.addWidget(self._tag)
        lay.addWidget(self._score_lbl)
        lay.addWidget(self._sub_lbl)

        self._anim = QPropertyAnimation(self, b"_anim_prop", self)
        self._anim.setDuration(700)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.refresh()

    def refresh(self, force=False):
        if self._locked:
            return
        profile = _benefit_profile(force=force)
        pct = int(profile["overall"])
        self._sub_lbl.setText(profile["summary"])

        if self._anim.state() == QAbstractAnimation.State.Running:
            self._anim.stop()
        self._anim.setStartValue(self._anim_val)
        self._anim.setEndValue(float(pct))
        self._anim.start()
        self.setToolTip(profile["tooltip"])

    def update_accent(self, color):
        self._accent = color
        self._tag.setStyleSheet(f"color:{DIM};font:500 9px '{MONO_FONT}';letter-spacing:1.2px;border:none;background:transparent;")
        self._score_lbl.setStyleSheet(f"color:{MAIN};font:300 22pt '{UI_FONT}';border:none;background:transparent;")
        self._sub_lbl.setStyleSheet(f"color:{MID};font:10px '{UI_FONT}';border:none;background:transparent;")

    def mousePressEvent(self, e):
        if self._locked:
            return
        self.refresh(force=True)
        super().mousePressEvent(e)

    def set_plan_active(self, active):
        self._locked = False
        self._tag.setText("Hextra Benefit")
        self.refresh(force=True)


# home stuff
class HomePage(QWidget):
    tweaks_applied = pyqtSignal()
    open_restore = pyqtSignal()

    def __init__(self, get_ac, parent=None):
        super().__init__(parent); self._get_ac = get_ac; self._worker = None; self._ram_cleaner = None; self._saved_scroll = None; self._after_worker = None; self._plan_active = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(f"background:{BG};"); self._net_prev = psutil.net_io_counters(); self._net_prev_ts = time.time()
        self._accent_labels = []
        self._accent_btns   = []

        wrap = SmoothScrollArea(); wrap.setWidgetResizable(True)
        wrap.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        wrap.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        wrap.setStyleSheet(f"QScrollArea{{border:none;background:{BG};}}"
                           f"QScrollBar:horizontal{{height:0px;border:none;background:transparent;}}"
                           f"QScrollBar:vertical{{background:transparent;width:7px;border:none;margin:6px 0 6px 0;}}"
                           f"QScrollBar::handle:vertical{{background:{_rgba('#dfe7f6', 60)};border:1px solid {_rgba('#ffffff', 36)};border-radius:3px;min-height:26px;}}"
                           f"QScrollBar::handle:vertical:hover{{background:{_rgba(self._get_ac(), 98)};border-color:{_rgba(self._get_ac(), 72)};}}"
                           f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}")
        self._wrap = wrap
        inner = QWidget(); inner.setStyleSheet(f"background:{BG};")
        inner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        self._inner = inner
        lay = QVBoxLayout(inner); lay.setContentsMargins(16,14,16,14); lay.setSpacing(10)
        self._lay = lay
        ac = self._get_ac()

        hero = QFrame()
        hero.setStyleSheet(replica_hero_style(ac))
        self._hero_card = hero
        hl = QVBoxLayout(hero); hl.setContentsMargins(18, 18, 18, 16); hl.setSpacing(10)
        self._hero_eyebrow = QLabel("SYSTEM OVERVIEW")
        self._hero_eyebrow.setStyleSheet(replica_section_caption(ac))
        hero_title = QLabel("Clean control over performance and recovery")
        hero_title.setStyleSheet(f"color:{MAIN};font:700 19pt '{TITLE_FONT}';border:none;background:transparent;")
        hero_sub = QLabel("Monitor live stats, apply safe actions, and keep your restore path visible before you change anything.")
        hero_sub.setWordWrap(True)
        hero_sub.setStyleSheet(f"color:{MID};font:500 10pt '{UI_FONT}';border:none;background:transparent;")
        badge_row = QHBoxLayout(); badge_row.setContentsMargins(0, 0, 0, 0); badge_row.setSpacing(8)
        self._hero_plan_badge = QLabel("")
        self._hero_restore_badge = QLabel("")
        self._hero_profile_badge = QLabel("")
        badge_row.addWidget(self._hero_plan_badge)
        badge_row.addWidget(self._hero_restore_badge)
        badge_row.addWidget(self._hero_profile_badge)
        badge_row.addStretch()
        hl.addWidget(self._hero_eyebrow)
        hl.addWidget(hero_title)
        hl.addWidget(hero_sub)
        hl.addLayout(badge_row)
        lay.addWidget(hero)

        sr = QGridLayout(); sr.setHorizontalSpacing(10); sr.setVerticalSpacing(10)
        self._cpu_s = MiniStat("cpu", ac)
        self._ram_s = MiniStat("ram", ac, action_text="Boost")
        self._ram_s.action_clicked.connect(self._run_ram_boost)
        self._gpu_s = MiniStat("gpu", ac)
        self._score_w = ScoreWidget(ac)
        top_stats = [self._cpu_s, self._ram_s, self._gpu_s, self._score_w]
        for idx, s in enumerate(top_stats):
            sr.addWidget(s, idx // 2, idx % 2)
        sr.setColumnStretch(0, 1)
        sr.setColumnStretch(1, 1)
        lay.addLayout(sr)

        self._summary_cards = []
        summary = QGridLayout(); summary.setHorizontalSpacing(10); summary.setVerticalSpacing(10)
        for _ in range(3):
            card = QFrame()
            card.setStyleSheet(replica_card_style(REPLICA["line_soft"], radius=14, alt=True))
            cl = QVBoxLayout(card); cl.setContentsMargins(12, 10, 12, 10); cl.setSpacing(6)
            value = QLabel("0 / 0")
            value.setStyleSheet(f"color:{MAIN};font:700 14pt '{TITLE_FONT}';border:none;")
            label = QLabel("summary")
            label.setStyleSheet(f"color:{MID};font:700 8pt '{UI_FONT}';border:none;letter-spacing:0.8px;")
            bar = _prog_bar(ac)
            cl.addWidget(value)
            cl.addWidget(bar)
            cl.addWidget(label)
            idx = len(self._summary_cards)
            summary.addWidget(card, 0, idx)
            self._summary_cards.append((card, value, label, bar))
        summary.setColumnStretch(0, 1)
        summary.setColumnStretch(1, 1)
        summary.setColumnStretch(2, 1)
        lay.addLayout(summary)

        def box(title):
            f = QFrame(); f.setStyleSheet(replica_card_style(REPLICA["line_soft"], radius=14, alt=True))
            f.setMinimumWidth(0)
            f.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            outer = QVBoxLayout(f); outer.setContentsMargins(14,12,14,12); outer.setSpacing(10)
            cap = QLabel(title.upper())
            cap.setStyleSheet(replica_section_caption(ac))
            self._accent_labels.append(cap)
            outer.addWidget(cap)
            inner = QVBoxLayout(); inner.setContentsMargins(0,0,0,0); inner.setSpacing(6)
            outer.addLayout(inner, 1)
            return f, inner

        info_stack = QVBoxLayout()
        info_stack.setContentsMargins(0, 0, 0, 0)
        info_stack.setSpacing(10)

        b, bv = box("System Snapshot")
        for k, v in [("computer", platform.node()), ("OS", f"{platform.system()} {platform.release()}"),
                     ("user", os.environ.get("USERNAME", os.environ.get("USER", "N/A"))),
                     ("root", os.environ.get("SystemRoot", os.environ.get("SYSTEMROOT", "N/A")))]:
            r, _ = _irow(k, v); bv.addWidget(r)

        b2, bv2 = box("Hardware")
        if os.name == "nt":
            cpu_name = ps_q("(Get-CimInstance Win32_Processor).Name")
        else:
            cpu_name = platform.processor() or "N/A"
        if not cpu_name or cpu_name == "N/A":
            cpu_name = platform.processor() or "N/A"
        for k, v in [("cpu", cpu_name[:48]),
                     ("cores", f"{psutil.cpu_count(logical=False)} physical / {psutil.cpu_count()} logical"),
                     ("ram", f"{psutil.virtual_memory().total/1073741824:.1f} GB"),
                     ("disk c", f"{psutil.disk_usage('C:\\' if os.name == 'nt' else '/').total/1073741824:.0f} GB")]:
            r, _ = _irow(k, v); bv2.addWidget(r)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(10)
        top_row.addWidget(b, 1)
        top_row.addWidget(b2, 1)
        info_stack.addLayout(top_row)

        b3, bv3 = box("Live Telemetry"); self._live = {}
        for k in ["cpu", "ram", "gpu", "uptime", "last boot", "processes", "net up", "net down"]:
            r, vl = _irow(k, "--"); bv3.addWidget(r); self._live[k] = vl

        quick_card, ql = box("Quick Actions")
        qa = QGridLayout(); qa.setHorizontalSpacing(8); qa.setVerticalSpacing(8)
        self._abtn = QPushButton("Apply All Tweaks"); self._abtn.setFixedHeight(36)
        self._abtn.setCursor(Qt.CursorShape.PointingHandCursor); self._abtn.setStyleSheet(_solid(ac))
        self._abtn.clicked.connect(self._apply_all)

        self._rbtn = QPushButton("Revert Common Tweaks"); self._rbtn.setFixedHeight(36)
        self._rbtn.setCursor(Qt.CursorShape.PointingHandCursor); self._rbtn.setStyleSheet(_danger())
        self._rbtn.clicked.connect(self._revert_all)

        self._restore_btn = QPushButton("Open Restore & Recovery"); self._restore_btn.setFixedHeight(36)
        self._restore_btn.setCursor(Qt.CursorShape.PointingHandCursor); self._restore_btn.setStyleSheet(_ghost(ac))
        self._restore_btn.clicked.connect(self._open_restore)
        qa.addWidget(self._abtn, 0, 0)
        qa.addWidget(self._rbtn, 0, 1)
        qa.addWidget(self._restore_btn, 1, 0, 1, 2)
        qa.setColumnStretch(0, 1)
        qa.setColumnStretch(1, 1)
        ql.addLayout(qa)

        self._rec_box, self._rec_box_lay = box("Hardware Recommendations")
        self._rec_texts = []
        self._rec_apply = QPushButton("Select Recommended"); self._rec_apply.setFixedHeight(34); self._rec_apply.setCursor(Qt.CursorShape.PointingHandCursor); self._rec_apply.setStyleSheet(_ghost(ac)); self._rec_apply.clicked.connect(self._select_recommended)
        self._rec_box_lay.addWidget(self._rec_apply)

        self._preset_box, self._preset_box_lay = box("Preset Packs")
        self._preset_btns = []
        for preset in builtin_presets():
            row = QFrame()
            row.setStyleSheet(
                f"QFrame{{background:{PANEL};border:1px solid {LINE};border-radius:4px;}}"
            )
            rl = QHBoxLayout(row)
            rl.setContentsMargins(12, 10, 12, 10)
            rl.setSpacing(10)

            copy = QVBoxLayout()
            copy.setContentsMargins(0, 0, 0, 0)
            copy.setSpacing(3)
            title = QLabel(preset["title"])
            title.setStyleSheet(f"color:{MAIN};font:700 11pt '{UI_FONT}';border:none;background:transparent;")
            desc = QLabel(f"{preset['desc']}  [{preset['count']} tweaks]")
            desc.setWordWrap(True)
            desc.setStyleSheet(f"color:{MID};font:10px '{UI_FONT}';border:none;background:transparent;")
            copy.addWidget(title)
            copy.addWidget(desc)

            btn = QPushButton("Load")
            btn.setFixedHeight(30)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(_ghost(ac))
            btn.clicked.connect(lambda _=False, pid=preset["id"]: self._load_preset_pack(pid))
            self._preset_btns.append(btn)
            self._accent_btns.append(btn)

            rl.addLayout(copy, 1)
            rl.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)
            self._preset_box_lay.addWidget(row)

        mid_row = QHBoxLayout()
        mid_row.setContentsMargins(0, 0, 0, 0)
        mid_row.setSpacing(10)
        mid_row.addWidget(b3, 1)
        right_stack = QVBoxLayout()
        right_stack.setContentsMargins(0, 0, 0, 0)
        right_stack.setSpacing(10)
        right_stack.addWidget(self._rec_box)
        right_stack.addWidget(self._preset_box)
        mid_row.addLayout(right_stack, 1)
        info_stack.addLayout(mid_row)

        info_stack.addWidget(quick_card)

        qtb, qtl = box("Safe Quick Tools")
        qgrid = QGridLayout(); qgrid.setSpacing(6)
        self._quick_btns = []
        for i, entry in enumerate(quick_tool_entries()):
            btn = QPushButton(entry["name"]); btn.setFixedHeight(36); btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(_ghost(ac))
            btn.clicked.connect(lambda _=False, e=entry: self._run_quick_tool(e))
            qgrid.addWidget(btn, i // 2, i % 2)
            self._quick_btns.append(btn)
        qtl.addLayout(qgrid)
        info_stack.addWidget(qtb)
        lay.addLayout(info_stack)

        self._prog = _prog_bar(ac); self._prog.setVisible(False)
        self._stat = QLabel(""); self._stat.setStyleSheet(f"color:{MID};font:600 9pt '{UI_FONT}';")
        pr = QHBoxLayout(); pr.addWidget(self._prog, 1); pr.addWidget(self._stat)
        lay.addLayout(pr); lay.addStretch()

        start_gpu_sampler()
        wrap.setWidget(inner); ol = QVBoxLayout(self); ol.setContentsMargins(0,0,0,0); ol.addWidget(wrap)
        self._refresh_recommendations()
        self._refresh_summary_cards()
        t = QTimer(self); t.timeout.connect(self._tick); t.start(1500); self._tick()

    def _refresh_live_stats(self):
        self._score_w.refresh()
        self._refresh_summary_cards()

    def _refresh_overview_badges(self):
        restore_ready = has_restore_point()
        profiles = len(load_profiles())
        self._hero_plan_badge.setText("Plan Active" if self._plan_active else "Plan Locked")
        self._hero_plan_badge.setStyleSheet(replica_badge_style("cyan" if self._plan_active else "red"))
        self._hero_restore_badge.setText("Restore Ready" if restore_ready else "No Restore Point")
        self._hero_restore_badge.setStyleSheet(replica_badge_style("green" if restore_ready else "amber"))
        self._hero_profile_badge.setText(f"{profiles} Profile{'s' if profiles != 1 else ''}")
        self._hero_profile_badge.setStyleSheet(replica_badge_style("gold" if profiles else "amber"))

    def _refresh_summary_cards(self):
        total = max(1, len(all_tweak_entries()))
        selected_count = len(load_selected_tweaks()) if self._plan_active else 0
        restore_ready = has_restore_point()
        profiles = len(load_profiles())
        metrics = [
            (f"{selected_count} / {total}", "Selected Tweaks", int(min(100, (selected_count / total) * 100))),
            ("Ready" if restore_ready else "Missing", "Restore Safety", 100 if restore_ready else 22),
            (str(profiles), "Saved Profiles", min(100, profiles * 25)),
        ]
        for (_card, value_lbl, label_lbl, bar), (value, label, pct) in zip(self._summary_cards, metrics):
            value_lbl.setText(value)
            label_lbl.setText(label)
            bar.setValue(pct)
        self._refresh_overview_badges()

    def _refresh_recommendations(self):
        while self._rec_box_lay.count() > 1:
            item = self._rec_box_lay.takeAt(1)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for text in hardware_recommendations():
            lbl = _lbl(text, MID, size=10)
            lbl.setWordWrap(True)
            lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self._rec_box_lay.addWidget(lbl)

    def _select_recommended(self):
        selected = load_selected_tweaks()
        selected.update(entry["id"] for entry in recommended_tweak_entries())
        set_selected_tweaks(selected)
        self._show_status("Recommended tweaks selected.", self._get_ac())
        append_activity("recommendation", "Selected recommended tweaks", "", "ok")
        self._refresh_summary_cards()
        self.refresh_score()

    def _load_preset_pack(self, preset_id):
        if not self._plan_active:
            self._show_status("Redeem a key to unlock presets.")
            return
        ok, msg, preset = load_builtin_preset(preset_id)
        self._show_status(msg, self._get_ac() if ok else MID)
        if not ok:
            return
        append_activity("preset", "Loaded preset", preset["title"], "ok", extra={"preset_id": preset["id"], "count": preset["count"]})
        self._refresh_summary_cards()
        self.refresh_score()

    def _run_quick_tool(self, entry):
        self._run_worker([entry], f"{entry['name']} finished.")
        append_activity("quick-tool", entry["name"], entry.get("desc", ""), "ok")

    def _remember_scroll(self):
        try:
            self._saved_scroll = self._wrap.verticalScrollBar().value()
        except Exception:
            self._saved_scroll = None

    def _restore_scroll(self):
        if self._saved_scroll is None:
            return
        try:
            bar = self._wrap.verticalScrollBar()
            bar.setValue(max(0, min(self._saved_scroll, bar.maximum())))
        except Exception:
            pass

    def _restore_scroll_later(self):
        QTimer.singleShot(0, self._restore_scroll)
        QTimer.singleShot(80, self._restore_scroll)

    def _clear_status_if_idle(self):
        if self._worker and self._worker.isRunning():
            return
        self._stat.setText("")
        self._stat.setStyleSheet(f"color:{MID};font:600 9pt '{UI_FONT}';")

    def _show_status(self, text, color=None, timeout_ms=5000):
        self._stat.setText(text)
        status_color = MID if color == MID else (MAIN if color else MID)
        weight = "bold " if color else ""
        self._stat.setStyleSheet(f"color:{status_color};font:{weight}9pt '{UI_FONT}';")
        if timeout_ms:
            QTimer.singleShot(timeout_ms, self._clear_status_if_idle)

    def _run_ram_boost(self):
        if not self._plan_active:
            self._show_status("Redeem a key to unlock tweaks.")
            return
        if self._ram_cleaner and self._ram_cleaner.isRunning():
            return
        self._remember_scroll()
        self._ram_s.set_action_state(False, "...")
        self._show_status("Running RAM cleaner...", self._get_ac(), timeout_ms=0)
        self._restore_scroll_later()
        self._ram_cleaner = _RamCleanerWorker(self)
        self._ram_cleaner.result.connect(self._finish_ram_boost)
        self._ram_cleaner.finished.connect(lambda: setattr(self, "_ram_cleaner", None))
        self._ram_cleaner.start()

    def _finish_ram_boost(self, result):
        self._ram_s.set_action_state(True)
        self._tick()
        is_error = _command_failed(result)
        self._show_status(result, MAIN if not is_error else MID)
        self._restore_scroll_later()

    def _tick(self):
        cpu = stable_cpu_percent(); mem = psutil.virtual_memory()
        gpu = gpu_percent()
        try: dsk = psutil.disk_usage("C:\\" if os.name == "nt" else "/")
        except: dsk = None
        up = int(time.time() - psutil.boot_time()); h, m = divmod(up//60, 60); d, h = divmod(h, 24)
        nio = psutil.net_io_counters()
        now = time.time()
        elapsed = max(0.001, now - self._net_prev_ts)
        sent = f"{(nio.bytes_sent - self._net_prev.bytes_sent)/1024/elapsed:.0f} KB/s"
        recv = f"{(nio.bytes_recv - self._net_prev.bytes_recv)/1024/elapsed:.0f} KB/s"
        self._net_prev = nio
        self._net_prev_ts = now
        self._cpu_s.update_val(cpu); self._ram_s.update_val(mem.percent)
        self._gpu_s.update_val(gpu)
        sv = lambda k,v: self._live[k].setText(str(v))
        sv("cpu", f"{cpu:.1f}%  ({psutil.cpu_count()} cores)")
        sv("ram", f"{mem.percent:.1f}%  ({mem.used/1073741824:.1f}/{mem.total/1073741824:.1f} GB)")
        sv("gpu", f"{gpu:.1f}%")
        sv("uptime", f"{d}d {h}h {m}m")
        sv("last boot", datetime.fromtimestamp(psutil.boot_time()).strftime("%Y-%m-%d %H:%M"))
        sv("processes", str(len(psutil.pids())))
        sv("net up", sent); sv("net down", recv)

    def _run_worker(self, tweaks, msg, after_done=None):
        if not self._plan_active:
            self._show_status("Redeem a key to unlock tweaks.")
            return
        self._after_worker = after_done
        self._abtn.setEnabled(False); self._rbtn.setEnabled(False)
        self._prog.setVisible(True); self._prog.setValue(0)
        self._prog.setStyleSheet(f"QProgressBar{{background:{REPLICA['surface_alt']};border:none;border-radius:3px;}}QProgressBar::chunk{{background:#f5f7fa;border-radius:3px;}}")
        self._worker = TweakWorker(tweaks)
        self._worker.progress.connect(lambda i,n,nm: (self._prog.setValue(int(i/n*100)), self._stat.setText(nm)))
        self._worker.detail.connect(lambda txt: self._stat.setText(txt))
        def _done():
            self._prog.setValue(100)
            self._abtn.setEnabled(True); self._rbtn.setEnabled(True)
            self.refresh_score()
            self._refresh_recommendations()
            self.tweaks_applied.emit()
            self._prog.setVisible(False)
            self._show_status(msg, self._get_ac())
            if callable(self._after_worker):
                self._after_worker()
            self._after_worker = None
        self._worker.done.connect(_done); self._worker.start()

    def _apply_all(self):
        tweaks = [entry for c in CATEGORY_ORDER for entry in category_entries(c)]
        self._run_worker(_dedupe_tweaks(tweaks), "All tweaks finished. Restart your PC.")

    def _revert_all(self):
        exact = snapshot_entries()
        revert_tweaks = [{"id": f"revert::{i}", "category": "Restore", "name": f"undo {i+1}", "cmds": [c], "desc": "", "restart": "restart"} for i, c in enumerate(REVERT_CMDS)]
        all_reverts = exact + revert_tweaks
        msg = "Snapshots restored. Restart your PC." if exact else "Common defaults restored. Restart your PC."
        self._run_worker(_dedupe_tweaks(all_reverts), msg, after_done=lambda: _save_named_block(SNAPSHOTS_KEY, {}))

    def _open_restore(self):
        self.open_restore.emit()

    def set_plan_active(self, active):
        self._plan_active = bool(active)
        self._abtn.setEnabled(bool(active))
        self._rbtn.setEnabled(bool(active))
        self._rec_apply.setEnabled(bool(active))
        self._ram_s.set_action_state(bool(active), "Boost" if active else "Locked")
        self._score_w.set_plan_active(bool(active))
        for btn in getattr(self, "_quick_btns", []):
            btn.setEnabled(bool(active))
        for btn in getattr(self, "_preset_btns", []):
            btn.setEnabled(bool(active))
        if not active:
            set_selected_tweaks([])
            self._refresh_summary_cards()
            self._show_status("No active plan. Redeem a key in Account.")
        else:
            self._refresh_summary_cards()
            self._stat.setText("")
            self._stat.setStyleSheet(f"color:{MID};font:600 9pt '{UI_FONT}';")

    def update_accent(self, color):
        self._hero_card.setStyleSheet(replica_hero_style(color))
        apply_glass_shadow(self._hero_card, color, blur=48, y=18, alpha=76)
        self._hero_eyebrow.setStyleSheet(replica_section_caption(color))
        for lbl in self._accent_labels:
            lbl.setStyleSheet(replica_section_caption(color))
        for btn in self._accent_btns:
            btn.setStyleSheet(_ghost(color))
        self._restore_btn.setStyleSheet(_ghost(color))
        self._rec_apply.setStyleSheet(_ghost(color))
        for btn in getattr(self, "_quick_btns", []):
            btn.setStyleSheet(_ghost(color))
        self._abtn.setStyleSheet(_solid(color))
        self._prog.setStyleSheet(f"QProgressBar{{background:{REPLICA['surface_alt']};border:none;border-radius:3px;}}QProgressBar::chunk{{background:#f5f7fa;border-radius:3px;}}")
        for card, value_lbl, label_lbl, bar in getattr(self, "_summary_cards", []):
            apply_glass_shadow(card, color, blur=28, y=10, alpha=34)
            label_lbl.setStyleSheet(f"color:{MID};font:700 8pt '{UI_FONT}';border:none;")
            bar.setStyleSheet(f"QProgressBar{{background:{REPLICA['surface_alt']};border:none;border-radius:2px;}}QProgressBar::chunk{{background:#f5f7fa;border-radius:2px;}}")
        self._cpu_s.update_accent(color)
        self._ram_s.update_accent(color)
        self._gpu_s.update_accent(color)
        self._score_w.update_accent(color)

class ProfilesPage(QWidget):
    selection_changed = pyqtSignal()

    def __init__(self, get_ac, parent=None):
        super().__init__(parent); self._get_ac = get_ac; self._worker = None; self._plan_active = False
        self.setStyleSheet(f"background:{BG};")
        root = QVBoxLayout(self); root.setContentsMargins(18,16,18,16); root.setSpacing(12)
        self._title = QLabel("Presets")
        self._title.setStyleSheet(replica_title_style())
        self._summary = _lbl("", MID, size=10)
        root.addWidget(self._title)
        root.addWidget(self._summary)

        top = QFrame(); self._hero_card = top; top.setStyleSheet(replica_hero_style(self._get_ac()))
        tl = QVBoxLayout(top); tl.setContentsMargins(18,18,18,18); tl.setSpacing(12)
        cap = QLabel("PRESET LIBRARY")
        cap.setStyleSheet(replica_section_caption(self._get_ac()))
        tl.addWidget(cap)
        tl.addWidget(_lbl("Load built-in packs or save your own reusable tweak selection.", MID, size=10))
        row = QHBoxLayout(); row.setSpacing(8)
        self._name = QLineEdit(); self._name.setPlaceholderText("Profile name"); self._name.setFixedHeight(38)
        self._name.setStyleSheet(replica_input_style(self._get_ac()))
        self._save_btn = QPushButton("Save Selection"); self._save_btn.setFixedHeight(38); self._save_btn.setStyleSheet(_solid(self._get_ac())); self._save_btn.clicked.connect(self._save_current)
        row.addWidget(self._name, 1); row.addWidget(self._save_btn)
        tl.addLayout(row)
        root.addWidget(top)

        self._scroll = SmoothScrollArea(); self._scroll.setWidgetResizable(True); self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(f"QScrollArea{{border:none;background:{BG};}}")
        holder = QWidget(); holder.setStyleSheet(f"background:{BG};")
        self._list = QVBoxLayout(holder); self._list.setContentsMargins(0,0,0,0); self._list.setSpacing(6)
        self._scroll.setWidget(holder)
        root.addWidget(self._scroll, 1)

        self._prog = _prog_bar(self._get_ac()); self._prog.setVisible(False)
        self._stat = QLabel(""); self._stat.setStyleSheet(f"color:{MID};font:600 9pt '{UI_FONT}';")
        root.addWidget(self._prog); root.addWidget(self._stat)
        self._refresh()

    def _clear_rows(self):
        while self._list.count():
            item = self._list.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _profile_entries(self, name):
        profiles = load_profiles()
        info = profiles.get(name, {})
        tweak_ids = set(info.get("tweaks", []))
        return [entry for entry in all_tweak_entries() if entry["id"] in tweak_ids]

    def _save_current(self):
        ok, msg = save_profile(self._name.text(), load_selected_tweaks())
        self._stat.setText(msg)
        self._stat.setStyleSheet(f"color:{MAIN if ok else MID};font:600 9pt '{UI_FONT}';")
        if ok:
            append_activity("profile", "Saved profile", msg, "ok")
            self._name.clear()
            self._refresh()

    def _load_profile(self, name):
        entries = self._profile_entries(name)
        set_selected_tweaks([entry["id"] for entry in entries])
        append_activity("profile", "Loaded profile", name, "ok")
        self._stat.setText(f"Loaded profile '{name}'.")
        self._stat.setStyleSheet(f"color:{MAIN};font:600 9pt '{UI_FONT}';")
        self.selection_changed.emit()
        self._refresh()

    def _load_builtin_preset(self, preset_id):
        if not self._plan_active:
            self._stat.setText("Redeem a key to unlock preset packs.")
            self._stat.setStyleSheet(f"color:{MID};font:600 9pt '{UI_FONT}';")
            return
        ok, msg, preset = load_builtin_preset(preset_id)
        self._stat.setText(msg)
        self._stat.setStyleSheet(f"color:{MAIN if ok else MID};font:600 9pt '{UI_FONT}';")
        if not ok or not preset:
            return
        append_activity("preset", "Loaded preset", preset["title"], "ok", extra={"preset_id": preset["id"], "count": preset["count"]})
        self.selection_changed.emit()
        self._refresh()

    def _delete_profile(self, name):
        delete_profile(name)
        append_activity("profile", "Deleted profile", name, "ok")
        self._stat.setText(f"Deleted profile '{name}'.")
        self._stat.setStyleSheet(f"color:{MID};font:600 9pt '{UI_FONT}';")
        self._refresh()

    def _apply_profile(self, name):
        if not self._plan_active:
            self._stat.setText("Redeem a key to unlock tweaks.")
            self._stat.setStyleSheet(f"color:{MAIN};font:600 9pt '{UI_FONT}';")
            return
        entries = self._profile_entries(name)
        if not entries:
            self._stat.setText("This profile has no valid tweaks.")
            self._stat.setStyleSheet(f"color:{MID};font:600 9pt '{UI_FONT}';")
            return
        if not has_restore_point():
            dlg = RestoreWarnDialog(self._get_ac(), self)
            dlg.confirmed.connect(lambda: self._run_entries(name, entries))
            dlg.go_restore.connect(self._go_restore)
            dlg.show_centered(self)
        else:
            self._run_entries(name, entries)

    def _go_restore(self):
        p = self.parent()
        while p and not isinstance(p, Dashboard): p = p.parent()
        if p: p._sidebar._select("restore")

    def _run_entries(self, name, entries):
        if not self._plan_active:
            self._stat.setText("Redeem a key to unlock tweaks.")
            self._stat.setStyleSheet(f"color:{MAIN};font:600 9pt '{UI_FONT}';")
            return
        self._prog.setVisible(True); self._prog.setValue(0)
        self._worker = TweakWorker(entries)
        self._worker.progress.connect(lambda i, n, nm: (self._prog.setValue(int(i / max(1, n) * 100)), self._stat.setText(nm)))
        self._worker.detail.connect(lambda txt: self._stat.setText(txt))
        self._worker.done.connect(lambda: self._finish_run(name))
        self._worker.start()

    def _finish_run(self, name):
        append_activity("profile", "Applied profile", name, "ok")
        self._prog.setValue(100)
        self._stat.setText(f"Applied profile '{name}'.")
        self._stat.setStyleSheet(f"color:{MAIN};font:600 9pt '{UI_FONT}';")
        QTimer.singleShot(4000, lambda: self._prog.setVisible(False))

    def _refresh_legacy(self):
        self._clear_rows()
        profiles = load_profiles()
        self._summary.setText(f"{len(load_selected_tweaks())} selected tweaks / {len(profiles)} saved profiles")
        if not profiles:
            self._list.addWidget(_lbl("No profiles saved yet.", MID, size=11))
            self._list.addStretch()
            return
        for name, info in sorted(profiles.items()):
            row = QFrame(); row.setStyleSheet(replica_card_style(REPLICA["line_soft"], radius=12, alt=True))
            rl = QHBoxLayout(row); rl.setContentsMargins(12,10,12,10); rl.setSpacing(8)
            title = QVBoxLayout(); title.setContentsMargins(0,0,0,0); title.setSpacing(4)
            title.addWidget(_lbl(name, MAIN, bold=True, size=11))
            title.addWidget(_lbl(f"{len(info.get('tweaks', []))} tweaks", MID, size=10))
            rl.addLayout(title, 1)
            for label, fn, style in [
                ("load", lambda _=False, n=name: self._load_profile(n), _ghost(self._get_ac())),
                ("apply", lambda _=False, n=name: self._apply_profile(n), _solid(self._get_ac())),
                ("delete", lambda _=False, n=name: self._delete_profile(n), _danger()),
            ]:
                btn = QPushButton(label); btn.setFixedHeight(30); btn.setCursor(Qt.CursorShape.PointingHandCursor); btn.setStyleSheet(style); btn.clicked.connect(fn)
                rl.addWidget(btn)
            self._list.addWidget(row)
        self._list.addStretch()

    def activate(self):
        self._refresh()

    def _refresh(self):
        self._clear_rows()
        profiles = load_profiles()
        presets = builtin_presets()
        self._summary.setText(f"{len(load_selected_tweaks())} selected tweaks | {len(profiles)} saved profiles | {len(presets)} preset packs")

        preset_cap = QLabel("Preset Packs")
        preset_cap.setStyleSheet(f"color:{DIM};font:500 9px '{MONO_FONT}';letter-spacing:1.5px;border:none;background:transparent;")
        self._list.addWidget(preset_cap)
        self._list.addSpacing(6)

        for preset in presets:
            row = QFrame(); row.setStyleSheet(replica_card_style(REPLICA["line_soft"], radius=14, alt=True))
            rl = QHBoxLayout(row); rl.setContentsMargins(14,12,14,12); rl.setSpacing(10)
            title = QVBoxLayout(); title.setContentsMargins(0,0,0,0); title.setSpacing(4)
            title.addWidget(_lbl(preset["title"], MAIN, bold=True, size=11))
            title.addWidget(_lbl(f"{preset['count']} tweaks | {preset['desc']}", MID, size=10))
            rl.addLayout(title, 1)
            btn = QPushButton("Load")
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(_ghost(self._get_ac()))
            btn.setEnabled(self._plan_active)
            btn.setToolTip("" if self._plan_active else "Active plan required.")
            btn.clicked.connect(lambda _=False, pid=preset["id"]: self._load_builtin_preset(pid))
            rl.addWidget(btn)
            self._list.addWidget(row)

        self._list.addSpacing(10)
        saved_cap = QLabel("Saved Profiles")
        saved_cap.setStyleSheet(f"color:{DIM};font:500 9px '{MONO_FONT}';letter-spacing:1.5px;border:none;background:transparent;")
        self._list.addWidget(saved_cap)
        self._list.addSpacing(6)

        if not profiles:
            empty = QFrame(); empty.setStyleSheet(replica_card_style(REPLICA["line_soft"], radius=14, alt=True))
            el = QVBoxLayout(empty); el.setContentsMargins(16,16,16,16); el.setSpacing(6)
            el.addWidget(_lbl("No profiles saved yet.", MAIN, bold=True, size=11))
            el.addWidget(_lbl("Save a selection above to build a reusable loadout for different games or workflows.", MID, size=10))
            self._list.addWidget(empty)
            self._list.addStretch()
            return
        for name, info in sorted(profiles.items()):
            row = QFrame(); row.setStyleSheet(replica_card_style(REPLICA["line_soft"], radius=14, alt=True))
            rl = QHBoxLayout(row); rl.setContentsMargins(14,12,14,12); rl.setSpacing(10)
            title = QVBoxLayout(); title.setContentsMargins(0,0,0,0); title.setSpacing(4)
            title.addWidget(_lbl(name, MAIN, bold=True, size=11))
            title.addWidget(_lbl(f"{len(info.get('tweaks', []))} tweaks saved", MID, size=10))
            rl.addLayout(title, 1)
            for label, fn, style in [
                ("Load", lambda _=False, n=name: self._load_profile(n), _ghost(self._get_ac())),
                ("Apply", lambda _=False, n=name: self._apply_profile(n), _solid(self._get_ac())),
                ("Delete", lambda _=False, n=name: self._delete_profile(n), _danger()),
            ]:
                btn = QPushButton(label); btn.setFixedHeight(32); btn.setCursor(Qt.CursorShape.PointingHandCursor); btn.setStyleSheet(style); btn.clicked.connect(fn)
                rl.addWidget(btn)
            self._list.addWidget(row)
        self._list.addStretch()

    def update_accent(self, color):
        self._title.setStyleSheet(replica_title_style())
        self._save_btn.setStyleSheet(_solid(color))
        self._name.setStyleSheet(replica_input_style(color))
        self._prog.setStyleSheet(f"QProgressBar{{background:{REPLICA['surface_alt']};border:none;border-radius:2px;}}QProgressBar::chunk{{background:{color};border-radius:2px;}}")
        self._refresh()

    def set_plan_active(self, active):
        self._plan_active = bool(active)
        if active:
            self._stat.setText("")
            self._stat.setStyleSheet(f"color:{MID};font:600 9pt '{UI_FONT}';")
        self._refresh()

class ActivityLogPage(QWidget):
    def __init__(self, get_ac, parent=None):
        super().__init__(parent); self._get_ac = get_ac
        self.setStyleSheet(f"background:{BG};")
        root = QVBoxLayout(self); root.setContentsMargins(18,16,18,16); root.setSpacing(12)
        self._title = QLabel("Activity Log")
        self._title.setStyleSheet(replica_title_style())
        self._summary = _lbl("Recent changes, tweaks, imports, and account activity all in one place.", MID, size=10)
        root.addWidget(self._title)
        root.addWidget(self._summary)

        controls = QHBoxLayout(); controls.setSpacing(8)
        self._refresh_btn = QPushButton("Refresh"); self._refresh_btn.setFixedHeight(32); self._refresh_btn.setStyleSheet(_ghost(self._get_ac())); self._refresh_btn.clicked.connect(self._refresh)
        self._clear_btn = QPushButton("Clear History"); self._clear_btn.setFixedHeight(32); self._clear_btn.setStyleSheet(_danger()); self._clear_btn.clicked.connect(lambda: (clear_activity_log(), self._refresh()))
        controls.addWidget(self._refresh_btn); controls.addWidget(self._clear_btn); controls.addStretch()
        root.addLayout(controls)

        self._scroll = SmoothScrollArea(); self._scroll.setWidgetResizable(True); self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(f"QScrollArea{{border:none;background:{BG};}}")
        holder = QWidget(); holder.setStyleSheet(f"background:{BG};")
        self._list = QVBoxLayout(holder); self._list.setContentsMargins(0,0,0,0); self._list.setSpacing(6)
        self._scroll.setWidget(holder)
        root.addWidget(self._scroll, 1)
        self._refresh()

    def _clear_rows(self):
        while self._list.count():
            item = self._list.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _refresh(self):
        self._clear_rows()
        entries = list(reversed(load_activity_log()))
        self._summary.setText(f"{len(entries)} recent events")
        if not entries:
            empty = QFrame(); empty.setStyleSheet(replica_card_style(REPLICA["line_soft"], radius=14, alt=True))
            el = QVBoxLayout(empty); el.setContentsMargins(16,16,16,16); el.setSpacing(6)
            el.addWidget(_lbl("No activity yet.", MAIN, bold=True, size=11))
            el.addWidget(_lbl("Applied tweaks, profile changes, and account actions will show up here.", MID, size=10))
            self._list.addWidget(empty)
            self._list.addStretch()
            return
        for item in entries:
            row = QFrame(); row.setStyleSheet(replica_card_style(REPLICA["line_soft"], radius=14, alt=True))
            rl = QVBoxLayout(row); rl.setContentsMargins(14,12,14,12); rl.setSpacing(8)
            ts = datetime.fromtimestamp(item.get("ts", time.time())).strftime("%Y-%m-%d %H:%M:%S")
            top = QHBoxLayout(); top.setContentsMargins(0,0,0,0); top.setSpacing(8)
            title = _lbl(item.get("title", "") or "Activity", MAIN, bold=True, size=10)
            status = str(item.get("status", "info")).lower()
            tone = "green" if status == "ok" else "red" if status == "error" else "amber"
            badge = QLabel(status.upper())
            badge.setStyleSheet(replica_badge_style(tone))
            meta = _lbl(f"{ts} | {item.get('kind', 'activity').title()}", MID, size=9)
            detail = _lbl(item.get("detail", "") or item.get("kind", ""), MID, size=10)
            top.addWidget(title, 1)
            top.addWidget(badge, 0, Qt.AlignmentFlag.AlignRight)
            rl.addLayout(top); rl.addWidget(meta); rl.addWidget(detail)
            self._list.addWidget(row)
        self._list.addStretch()

    def activate(self):
        self._refresh()

    def update_accent(self, color):
        self._title.setStyleSheet(replica_title_style())
        self._hero_card.setStyleSheet(replica_hero_style(color))
        apply_glass_shadow(self._hero_card, color, blur=44, y=16, alpha=70)
        self._refresh_btn.setStyleSheet(_ghost(color))

class SettingsPage(QWidget):
    accent_changed = pyqtSignal(str)
    game_paths_changed = pyqtSignal()
    data_imported = pyqtSignal()

    def __init__(self, get_ac, parent=None):
        super().__init__(parent); self._get_ac = get_ac
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(f"background:{BG};")
        self._accent_labels = []
        self._accent_btns = []
        self._game_edits = {}

        wrap = SmoothScrollArea(); wrap.setWidgetResizable(True)
        wrap.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        wrap.setStyleSheet(f"QScrollArea{{border:none;background:{BG};}}"
                           f"QScrollBar:vertical{{background:transparent;width:7px;border:none;margin:6px 0 6px 0;}}"
                           f"QScrollBar::handle:vertical{{background:{_rgba('#dfe7f6', 60)};border:1px solid {_rgba('#ffffff', 36)};border-radius:3px;min-height:26px;}}"
                           f"QScrollBar::handle:vertical:hover{{background:{_rgba(self._get_ac(), 98)};border-color:{_rgba(self._get_ac(), 72)};}}"
                           f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}")
        self._wrap = wrap
        inner = QWidget(); inner.setStyleSheet(f"background:{BG};")
        inner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        self._inner = inner
        lay = QVBoxLayout(inner); lay.setContentsMargins(18,16,18,16); lay.setSpacing(14)
        self._lay = lay
        h1 = QLabel("Settings")
        h1.setStyleSheet(replica_title_style())
        self._title = h1
        lay.addWidget(h1)
        self._summary = _lbl("Tune the app theme, manage folders, and move your setup between systems.", MID, size=10)
        lay.addWidget(self._summary)

        pc = QFrame(); self._preset_card = pc; pc.setStyleSheet(replica_card_style(REPLICA["line_soft"], radius=16, alt=True))
        pl = QVBoxLayout(pc); pl.setContentsMargins(18,16,18,16); pl.setSpacing(10)
        p1 = QLabel("Preset Themes")
        p1.setStyleSheet(replica_section_caption(self._get_ac()))
        self._accent_labels.append(p1)
        pl.addWidget(p1)
        p2 = _lbl("Choose a ready-made accent for the full app.", MID, size=10)
        pl.addWidget(p2)

        grid = QGridLayout(); grid.setSpacing(8)
        for i, (name, color) in enumerate(THEMES.items()):
            b = self._theme_btn(name, color)
            grid.addWidget(b, i // 4, i % 4)
        pl.addLayout(grid)
        lay.addWidget(pc)
        pc.hide()

        mc = QFrame(); self._manual_card = mc; mc.setStyleSheet(replica_card_style(REPLICA["line_soft"], radius=16, alt=True))
        ml = QVBoxLayout(mc); ml.setContentsMargins(18,16,18,16); ml.setSpacing(10)
        p3 = QLabel("Pick Any Color")
        p3.setStyleSheet(replica_section_caption(self._get_ac()))
        self._accent_labels.append(p3)
        ml.addWidget(p3)
        p4 = _lbl("Open the color picker and dial in a custom accent instantly.", MID, size=10)
        ml.addWidget(p4)

        pr = QHBoxLayout(); pr.setSpacing(10)
        self._swatch = QFrame(); self._swatch.setFixedSize(42, 42)
        self._swatch.setStyleSheet(f"QFrame{{background:{self._get_ac()};border-radius:12px;border:1px solid {_rgba(self._get_ac(), 90)};}}")
        self._pick_btn = QPushButton("Open Color Picker")
        self._pick_btn.setFixedHeight(40); self._pick_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pick_btn.setStyleSheet(_ghost(self._get_ac())); self._pick_btn.clicked.connect(self._pick_manual)
        self._accent_btns.append(self._pick_btn)
        pr.addWidget(self._swatch); pr.addWidget(self._pick_btn, 1); ml.addLayout(pr)
        lay.addWidget(mc)
        mc.hide()

        cc = QFrame(); self._custom_card = cc; cc.setStyleSheet(replica_card_style(REPLICA["line_soft"], radius=16, alt=True))
        cl = QVBoxLayout(cc); cl.setContentsMargins(18,16,18,16); cl.setSpacing(10)
        p5 = QLabel("Create Custom Theme")
        p5.setStyleSheet(replica_section_caption(self._get_ac()))
        self._accent_labels.append(p5)
        cl.addWidget(p5)
        p6 = _lbl("Name a color you like and keep it as a reusable accent.", MID, size=10)
        cl.addWidget(p6)

        row1 = QHBoxLayout(); row1.setSpacing(8)
        self._name_inp = QLineEdit(); self._name_inp.setPlaceholderText("Theme name"); self._name_inp.setFixedHeight(38)
        self._name_inp.setStyleSheet(replica_input_style(self._get_ac()))
        self._picked = self._get_ac()
        self._color_dot = QPushButton(); self._color_dot.setFixedSize(38, 38)
        self._color_dot.setStyleSheet(f"QPushButton{{background:{self._picked};border:1px solid {_rgba(self._picked, 90)};border-radius:12px;}}QPushButton:hover{{border-color:{QColor(self._picked).lighter(120).name()};}}")
        self._color_dot.setCursor(Qt.CursorShape.PointingHandCursor); self._color_dot.setToolTip("click to pick color")
        self._color_dot.clicked.connect(self._pick_custom)
        row1.addWidget(self._name_inp, 1); row1.addWidget(self._color_dot)
        cl.addLayout(row1)

        self._save_b = QPushButton("Save Theme"); self._save_b.setFixedHeight(36)
        self._save_b.setCursor(Qt.CursorShape.PointingHandCursor); self._save_b.setStyleSheet(_solid(self._get_ac()))
        self._save_b.clicked.connect(self._save_custom); cl.addWidget(self._save_b)

        self._custom_list = QVBoxLayout(); self._custom_list.setSpacing(4)
        cl.addLayout(self._custom_list)
        lay.addWidget(cc)
        cc.hide()

        gfc = QFrame(); self._folders_card = gfc; gfc.setStyleSheet(replica_card_style(REPLICA["line_soft"], radius=16, alt=True))
        gf = QVBoxLayout(gfc); gf.setContentsMargins(18,16,18,16); gf.setSpacing(10)
        d_lbl = QLabel("Folders")
        d_lbl.setStyleSheet(replica_section_caption(self._get_ac()))
        self._accent_labels.append(d_lbl)
        gf.addWidget(d_lbl)
        gf.addWidget(_lbl("If a game was not detected automatically, point Hextra to it here.", MID, size=10))
        self._game_path_btns = []
        gp = load_game_paths()
        for gkey, gshort in [
            ("Roblox", "roblox"),
            ("FiveM", "fivem"),
            ("Valorant", "valorant"),
            ("CS2", "cs2"),
            ("Minecraft", "minecraft"),
            ("Fortnite", "fortnite"),
            ("Apex", "apex"),
        ]:
            row = QHBoxLayout(); row.setSpacing(8)
            row.addWidget(_lbl(gkey, MAIN, bold=True, size=10))
            ed = QLineEdit(); ed.setFixedHeight(38)
            ed.setPlaceholderText("optional - leave empty for default paths")
            ed.setStyleSheet(replica_input_style(self._get_ac()))
            ed.setText(gp.get(gkey, ""))
            self._game_edits[gkey] = ed
            bb = QPushButton("Browse"); bb.setFixedHeight(38); bb.setCursor(Qt.CursorShape.PointingHandCursor)
            bb.setStyleSheet(_ghost_sized(self._get_ac(), 13)); bb.clicked.connect(lambda _c, k=gkey, e=ed: self._browse_game_folder(k, e))
            self._game_path_btns.append(bb)
            cb = QPushButton("Clear"); cb.setFixedHeight(38); cb.setCursor(Qt.CursorShape.PointingHandCursor)
            cb.setStyleSheet(_ghost_sized(self._get_ac(), 13)); cb.clicked.connect(lambda _c, k=gkey, e=ed: self._clear_game_folder(k, e))
            self._game_path_btns.append(cb)
            row.addWidget(ed, 1); row.addWidget(bb); row.addWidget(cb)
            rw = QWidget(); rw.setLayout(row); gf.addWidget(rw)
        lay.addWidget(gfc)

        ioc = QFrame(); self._io_card = ioc; ioc.setStyleSheet(replica_card_style(REPLICA["line_soft"], radius=16, alt=True))
        iol = QVBoxLayout(ioc); iol.setContentsMargins(18,16,18,16); iol.setSpacing(10)
        io_lbl = QLabel("Import / Export")
        io_lbl.setStyleSheet(replica_section_caption(self._get_ac()))
        self._accent_labels.append(io_lbl)
        iol.addWidget(io_lbl)
        iol.addWidget(_lbl("Back up your setup or restore it on another system.", MID, size=10))
        io_row = QHBoxLayout(); io_row.setSpacing(8)
        self._export_btn = QPushButton("Export Settings"); self._export_btn.setFixedHeight(36); self._export_btn.setCursor(Qt.CursorShape.PointingHandCursor); self._export_btn.setStyleSheet(_ghost(self._get_ac())); self._export_btn.clicked.connect(self._export_settings)
        self._import_btn = QPushButton("Import Settings"); self._import_btn.setFixedHeight(36); self._import_btn.setCursor(Qt.CursorShape.PointingHandCursor); self._import_btn.setStyleSheet(_solid(self._get_ac())); self._import_btn.clicked.connect(self._import_settings)
        io_row.addWidget(self._export_btn); io_row.addWidget(self._import_btn); io_row.addStretch()
        iol.addLayout(io_row)
        lay.addWidget(ioc)

        lay.addStretch()

        wrap.setWidget(inner); ol = QVBoxLayout(self); ol.setContentsMargins(0,0,0,0); ol.addWidget(wrap)
        self._refresh_customs()

    def _browse_game_folder(self, game_key, edit):
        start = edit.text().strip() or os.path.expanduser("~")
        d = QFileDialog.getExistingDirectory(self, f"select folder - {game_key}", start)
        if d:
            edit.setText(d)
            save_game_path(game_key, d)
            self.game_paths_changed.emit()

    def _clear_game_folder(self, game_key, edit):
        edit.clear()
        save_game_path(game_key, "")
        self.game_paths_changed.emit()

    def _theme_btn(self, name, color):
        b = QPushButton(name.title()); b.setFixedHeight(36); b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(
            f"QPushButton{{background:{_rgba('#ffffff', 6)};color:{MAIN};border:1px solid {_rgba('#ffffff', 42)};"
            "border-radius:12px;font:700 9pt 'Segoe UI';padding:0 12px;}"
            f"QPushButton:hover{{background:{_rgba('#ffffff', 12)};border-color:{_rgba('#ffffff', 76)};}}"
        )
        b.clicked.connect(lambda _, c=color: self.accent_changed.emit(c))
        return b

    def _pick_custom(self):
        c = QColorDialog.getColor(QColor(self._picked), self, "pick a color for your theme")
        if c.isValid():
            self._picked = c.name()
            self._color_dot.setStyleSheet(f"QPushButton{{background:{c.name()};border:1px solid {_rgba(c.name(), 90)};border-radius:12px;}}QPushButton:hover{{border-color:{QColor(c.name()).lighter(120).name()};}}")

    def _save_custom(self):
        name = self._name_inp.text().strip()
        if not name: return
        save_custom_theme(name, self._picked); self._name_inp.clear()
        self._refresh_customs(); self.accent_changed.emit(self._picked)

    def _refresh_customs_legacy(self):
        while self._custom_list.count():
            w = self._custom_list.takeAt(0).widget()
            if w: w.deleteLater()
        for name, color in load_custom_themes().items():
            row = QHBoxLayout(); row.setSpacing(6)
            sw = QFrame(); sw.setFixedSize(14, 14); sw.setStyleSheet(f"QFrame{{background:{color};border-radius:4px;border:none;}}")
            btn = QPushButton(name); btn.setFixedHeight(30); btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton{{background:{_rgba(color, 34)};color:{color};border:1px solid {_rgba(color, 68)};"
                f"border-radius:5px;font:11px 'Press Start 2P';text-align:left;padding-left:6px;}}"
                f"QPushButton:hover{{background:{_rgba(color, 68)};}}")
            btn.clicked.connect(lambda _, c=color: self.accent_changed.emit(c))
            del_b = QPushButton("X"); del_b.setFixedSize(26, 26); del_b.setCursor(Qt.CursorShape.PointingHandCursor)
            del_b.setStyleSheet(f"QPushButton{{background:transparent;color:{MID};border:none;font:13px;}}QPushButton:hover{{color:{MAIN};}}")
            n = name; del_b.clicked.connect(lambda _, nm=n: (delete_custom_theme(nm), self._refresh_customs()))
            row.addWidget(sw); row.addWidget(btn, 1); row.addWidget(del_b)
            w = QWidget(); w.setLayout(row); self._custom_list.addWidget(w)

    def _pick_manual(self):
        c = QColorDialog.getColor(QColor(self._get_ac()), self, "pick any color")
        if c.isValid():
            self._swatch.setStyleSheet(f"QFrame{{background:{c.name()};border-radius:12px;border:1px solid {_rgba(c.name(), 90)};}}")
            self._pick_btn.setStyleSheet(
                f"QPushButton{{background:{_rgba(c.name(), 34)};color:{c.name()};border:1px solid {_rgba(c.name(), 85)};"
                f"border-radius:12px;font:600 9pt '{UI_FONT}';padding:0 14px;}}"
                f"QPushButton:hover{{background:{_rgba(c.name(), 68)};border-color:{c.name()};}}")
            self.accent_changed.emit(c.name())

    def _refresh_customs(self):
        while self._custom_list.count():
            w = self._custom_list.takeAt(0).widget()
            if w:
                w.deleteLater()
        for name, color in load_custom_themes().items():
            row = QHBoxLayout(); row.setSpacing(8)
            sw = QFrame(); sw.setFixedSize(14, 14); sw.setStyleSheet(f"QFrame{{background:{color};border-radius:5px;border:none;}}")
            btn = QPushButton(name.title()); btn.setFixedHeight(32); btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton{{background:{_rgba(color, 34)};color:{color};border:1px solid {_rgba(color, 68)};"
                f"border-radius:12px;font:600 9pt '{UI_FONT}';text-align:left;padding-left:10px;}}"
                f"QPushButton:hover{{background:{_rgba(color, 68)};}}")
            btn.clicked.connect(lambda _, c=color: self.accent_changed.emit(c))
            del_b = QPushButton("Remove"); del_b.setFixedHeight(32); del_b.setCursor(Qt.CursorShape.PointingHandCursor)
            del_b.setStyleSheet(_ghost_sized("#ffffff", 13))
            n = name; del_b.clicked.connect(lambda _, nm=n: (delete_custom_theme(nm), self._refresh_customs()))
            row.addWidget(sw); row.addWidget(btn, 1); row.addWidget(del_b)
            w = QWidget(); w.setLayout(row); self._custom_list.addWidget(w)

    def _export_settings(self):
        path, _ = QFileDialog.getSaveFileName(self, "export settings", os.path.expanduser("~\\hextra_settings.json"), "JSON (*.json)")
        if not path:
            return
        ok = export_settings_file(path)
        append_activity("settings", "Export settings", path, "ok" if ok else "error")

    def _import_settings(self):
        path, _ = QFileDialog.getOpenFileName(self, "import settings", os.path.expanduser("~"), "JSON (*.json)")
        if not path:
            return
        ok, _ = import_settings_file(path)
        append_activity("settings", "Import settings", path, "ok" if ok else "error")
        if ok:
            self.reload_from_storage()
            self.game_paths_changed.emit()
            self.data_imported.emit()

    def reload_from_storage(self):
        gp = load_game_paths()
        for key, edit in self._game_edits.items():
            edit.setText(gp.get(key, ""))
        current = load_data().get("color", self._get_ac())
        if current != "rainbow":
            self._picked = current
            self._swatch.setStyleSheet(f"QFrame{{background:{current};border-radius:12px;border:1px solid {_rgba(current, 90)};}}")
            self._color_dot.setStyleSheet(f"QPushButton{{background:{current};border:1px solid {_rgba(current, 90)};border-radius:12px;}}QPushButton:hover{{border-color:{QColor(current).lighter(120).name()};}}")
        self._refresh_customs()

    def update_accent(self, color):
        if color == "rainbow": return
        self._title.setStyleSheet(replica_title_style())
        for panel in (self._preset_card, self._manual_card, self._custom_card, self._folders_card, self._io_card):
            panel.setStyleSheet(replica_card_style(REPLICA["line_soft"], radius=16, alt=True))
            apply_glass_shadow(panel, color, blur=30, y=10, alpha=36)
        for lbl in self._accent_labels:
            lbl.setStyleSheet(replica_section_caption(color))
        self._swatch.setStyleSheet(f"QFrame{{background:{color};border-radius:12px;border:1px solid {_rgba(color, 90)};}}")
        self._save_b.setStyleSheet(_solid(color))
        self._pick_btn.setStyleSheet(_ghost(color))
        self._export_btn.setStyleSheet(_ghost(color))
        self._import_btn.setStyleSheet(_solid(color))
        for b in self._game_path_btns:
            b.setStyleSheet(_ghost_sized(color, 13))
        self._name_inp.setStyleSheet(replica_input_style(color))
        for edit in self._game_edits.values():
            edit.setStyleSheet(replica_input_style(color))

    def update_accent_rainbow(self, color):
        self.update_accent(color)

class AccountPage(QWidget):
    account_updated = pyqtSignal(dict)

    def __init__(self, get_ac, parent=None):
        super().__init__(parent)
        self._get_ac = get_ac
        self._auth = {}
        self._status = {}
        self._status_worker = None
        self._redeem_worker = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"QScrollArea{{background:transparent;border:none;}}"
                             f"QScrollBar:vertical{{width:7px;border:none;background:transparent;margin:6px 0 6px 0;}}"
                             f"QScrollBar::handle:vertical{{background:{_rgba('#dfe7f6', 60)};border:1px solid {_rgba('#ffffff', 36)};border-radius:3px;min-height:26px;}}"
                             f"QScrollBar::handle:vertical:hover{{background:{_rgba(self._get_ac(), 98)};border-color:{_rgba(self._get_ac(), 72)};}}"
                             f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}")
        host = QWidget()
        host.setStyleSheet("background:transparent;border:none;")
        lay = QVBoxLayout(host)
        lay.setContentsMargins(4, 4, 4, 12)
        lay.setSpacing(12)

        self._hero = QFrame()
        self._hero.setObjectName("accountHeroCard")
        hero_l = QVBoxLayout(self._hero)
        hero_l.setContentsMargins(20, 18, 20, 18)
        hero_l.setSpacing(10)
        self._hero_eyebrow = QLabel("ACCOUNT OVERVIEW")
        self._hero_title = QLabel("Your account at a glance")
        self._hero_title.setStyleSheet(f"color:{MAIN};font:700 18pt '{TITLE_FONT}';border:none;background:transparent;")
        self._hero_sub = QLabel("Track membership status, redeem keys, and keep your account details close to the tweaks they unlock.")
        self._hero_sub.setWordWrap(True)
        self._hero_sub.setStyleSheet(f"color:{MID};font:500 10pt '{UI_FONT}';border:none;background:transparent;")
        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 0, 0, 0)
        badge_row.setSpacing(8)
        self._hero_status_badge = QLabel("No account")
        self._hero_plan_badge = QLabel("No active plan")
        self._hero_member_badge = QLabel("Member since -")
        badge_row.addWidget(self._hero_status_badge)
        badge_row.addWidget(self._hero_plan_badge)
        badge_row.addWidget(self._hero_member_badge)
        badge_row.addStretch()
        hero_l.addWidget(self._hero_eyebrow)
        hero_l.addWidget(self._hero_title)
        hero_l.addWidget(self._hero_sub)
        hero_l.addLayout(badge_row)
        lay.addWidget(self._hero)

        self._profile = QFrame()
        self._profile.setObjectName("accountProfileCard")
        pl = QVBoxLayout(self._profile)
        pl.setContentsMargins(20, 20, 20, 20)
        pl.setSpacing(12)
        cap = QLabel("PROFILE DETAILS")
        title = QLabel("Profile Information")
        cap.setStyleSheet(replica_section_caption(self._get_ac()))
        title.setStyleSheet(f"color:{MAIN};font:700 16pt '{TITLE_FONT}';border:none;background:transparent;")
        pl.addWidget(cap)
        pl.addWidget(title)
        subtitle = QLabel("Your account details and membership information")
        subtitle.setStyleSheet(f"color:{MID};font:11px '{UI_FONT}';border:none;")
        pl.addWidget(subtitle)
        self._username_row, self._username_val = _irow("username", "-", label_width=120, row_height=48, label_px=11, value_size=13)
        self._email_row, self._email_val = _irow("email", "-", label_width=120, row_height=48, label_px=11, value_size=13)
        self._created_row, self._created_val = _irow("member since", "-", label_width=120, row_height=48, label_px=11, value_size=13)
        self._status_row, self._status_val = _irow("status", "No active plan", label_width=120, row_height=48, label_px=11, value_size=13)
        self._expires_row, self._expires_val = _irow("active until", "-", label_width=120, row_height=48, label_px=11, value_size=13)
        for row in (self._username_row, self._email_row, self._created_row, self._status_row, self._expires_row):
            pl.addWidget(row)
        lay.addWidget(self._profile)

        self._licenses = QFrame()
        self._licenses.setObjectName("accountLicenseCard")
        ll = QVBoxLayout(self._licenses)
        ll.setContentsMargins(20, 20, 20, 20)
        ll.setSpacing(12)
        lic_cap = QLabel("LICENSES")
        lic_title = QLabel("Licenses")
        lic_cap.setStyleSheet(replica_section_caption(self._get_ac()))
        lic_title.setStyleSheet(f"color:{MAIN};font:700 15pt '{TITLE_FONT}';border:none;background:transparent;")
        ll.addWidget(lic_cap)
        ll.addWidget(lic_title)
        lic_sub = QLabel("Manage your license keys and view active licenses")
        lic_sub.setStyleSheet(f"color:{MID};font:11px '{UI_FONT}';border:none;")
        ll.addWidget(lic_sub)
        self._redeem_shell = QFrame()
        self._redeem_shell.setObjectName("accountRedeemShell")
        shell_l = QHBoxLayout(self._redeem_shell)
        shell_l.setContentsMargins(12, 12, 12, 12)
        shell_l.setSpacing(8)
        redeem_row = QHBoxLayout()
        redeem_row.setSpacing(8)
        self._redeem_input = QLineEdit()
        self._redeem_input.setPlaceholderText("Enter license key")
        self._redeem_input.setFixedHeight(38)
        self._redeem_btn = QPushButton("Redeem")
        self._redeem_btn.setFixedHeight(38)
        self._redeem_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._redeem_btn.clicked.connect(self._redeem)
        redeem_row.addWidget(self._redeem_input, 1)
        redeem_row.addWidget(self._redeem_btn)
        shell_l.addLayout(redeem_row)
        ll.addWidget(self._redeem_shell)
        self._redeem_msg = QLabel("")
        self._redeem_msg.setWordWrap(True)
        self._redeem_msg.setStyleSheet(f"color:{MID};font:11px '{UI_FONT}';border:none;")
        ll.addWidget(self._redeem_msg)
        self._history_host = QWidget()
        self._history_host.setStyleSheet("background:transparent;border:none;")
        self._history_lay = QVBoxLayout(self._history_host)
        self._history_lay.setContentsMargins(0, 0, 0, 0)
        self._history_lay.setSpacing(8)
        ll.addWidget(self._history_host)
        lay.addWidget(self._licenses)
        lay.addStretch(1)

        scroll.setWidget(host)
        root.addWidget(scroll)
        self._redeem_input.returnPressed.connect(self._redeem)
        self.update_accent(self._get_ac())

    def update_accent(self, color):
        soft_edge = _rgba(color, 38)
        card_edge = _rgba("#ffffff", 52)
        shell_edge = _rgba("#ffffff", 44)
        self._hero.setStyleSheet(replica_hero_style(color))
        apply_glass_shadow(self._hero, color, blur=50, y=18, alpha=78)
        self._hero_eyebrow.setStyleSheet(replica_section_caption(color))
        self._profile.setStyleSheet(f"QFrame#accountProfileCard{{background:{CARD};border:1px solid {card_edge};border-radius:22px;}}")
        self._licenses.setStyleSheet(f"QFrame#accountLicenseCard{{background:{CARD};border:1px solid {card_edge};border-radius:22px;}}")
        self._redeem_shell.setStyleSheet(f"QFrame#accountRedeemShell{{background:{REPLICA['surface_alt']};border:1px solid {shell_edge};border-radius:16px;}}")
        apply_glass_shadow(self._profile, color, blur=32, y=12, alpha=42)
        apply_glass_shadow(self._licenses, color, blur=32, y=12, alpha=42)
        apply_glass_shadow(self._redeem_shell, color, blur=24, y=8, alpha=28)
        for row in (self._username_row, self._email_row, self._created_row, self._status_row, self._expires_row):
            row.setStyleSheet(f"QFrame{{background:{REPLICA['surface_alt']};border:1px solid {REPLICA['line_soft']};border-radius:12px;}}")
        self._redeem_input.setStyleSheet(f"QLineEdit{{background:{BG};color:{MAIN};border:1px solid {_rgba('#ffffff', 52)};border-radius:14px;padding:0 12px;font:600 10pt '{UI_FONT}';}}QLineEdit:focus{{border-color:{_rgba('#ffffff', 92)};background:{CARD};}}")
        self._redeem_btn.setStyleSheet(_solid(color))

    def set_session(self, auth, status=None):
        self._auth = dict(auth or {})
        if status:
            self._status = dict(status)
        self._render()

    def activate(self):
        if self._auth.get("username") and self._auth.get("session_token"):
            self._refresh_status()

    def _refresh_status(self):
        if self._status_worker is not None:
            return
        self._status_worker = AccountStatusWorker(self._auth)
        self._status_worker.result.connect(self._on_status)
        self._status_worker.finished.connect(lambda: setattr(self, "_status_worker", None))
        self._status_worker.start()

    def _on_status(self, text, tone, resp):
        if isinstance(resp, dict) and resp.get("success", True) is not False:
            self._status = dict(resp)
            self._render()
            payload = dict(resp)
            payload["auth"] = dict(self._auth)
            self.account_updated.emit(payload)

    def _render(self):
        auth = self._auth or {}
        status = self._status or {}
        username = status.get('username') or auth.get('username') or '-'
        email = status.get('email') or auth.get('email') or '-'
        created = str(status.get('created') or '-').replace('T', ' ')[:10]
        self._username_val.setText(username)
        self._email_val.setText(email)
        self._created_val.setText(created)
        self._hero_title.setText(f"{username}'s account" if username and username != "-" else "Your account at a glance")
        self._hero_status_badge.setText("Signed In" if username and username != "-" else "No Account")
        self._hero_status_badge.setStyleSheet(
            replica_badge_style("cyan" if username and username != "-" else "amber", font_px=10, padding_v=4, padding_h=10, radius=3, letter_spacing=0.8)
        )
        self._hero_member_badge.setText(f"Member since {created}" if created != "-" else "Member since -")
        self._hero_member_badge.setStyleSheet(replica_badge_style("gold", font_px=10, padding_v=4, padding_h=10, radius=3, letter_spacing=0.8))
        if status.get("licensed"):
            self._status_val.setText(f"Active ({status.get('days_left', 0)} days left)")
            self._status_val.setStyleSheet(f"color:{MAIN};font:700 13px '{UI_FONT}';border:none;")
            self._hero_plan_badge.setText(f"{status.get('days_left', 0)} days left")
            self._hero_plan_badge.setStyleSheet(replica_badge_style("green", font_px=10, padding_v=4, padding_h=10, radius=3, letter_spacing=0.8))
        else:
            self._status_val.setText("No active plan")
            self._status_val.setStyleSheet(f"color:{MID};font:700 13px '{UI_FONT}';border:none;")
            self._hero_plan_badge.setText("No active plan")
            self._hero_plan_badge.setStyleSheet(replica_badge_style("amber", font_px=10, padding_v=4, padding_h=10, radius=3, letter_spacing=0.8))
        self._expires_val.setText(str(status.get('license_expires') or '-').replace('T', ' ')[:16])
        self._render_history(status.get("licenses") or [])

    def _render_history(self, licenses):
        while self._history_lay.count():
            item = self._history_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not licenses:
            empty = QFrame()
            empty.setStyleSheet(f"QFrame{{background:{REPLICA['surface_alt']};border:1px solid {REPLICA['line_soft']};border-radius:14px;}}")
            el = QVBoxLayout(empty)
            el.setContentsMargins(14, 12, 14, 12)
            title = QLabel("No redeemed keys yet.")
            title.setStyleSheet(f"color:{MAIN};font:700 10pt '{UI_FONT}';border:none;")
            label = QLabel("Redeemed plans will appear here with activation dates and expiry details.")
            label.setWordWrap(True)
            label.setStyleSheet(f"color:{MID};font:10px '{UI_FONT}';border:none;")
            el.addWidget(title)
            el.addWidget(label)
            self._history_lay.addWidget(empty)
            return
        for item in licenses:
            card = QFrame()
            card.setStyleSheet(f"QFrame{{background:{REPLICA['surface_alt']};border:1px solid {REPLICA['line_soft']};border-radius:14px;}}")
            row = QVBoxLayout(card)
            row.setContentsMargins(14, 12, 14, 12)
            row.setSpacing(6)
            top = QHBoxLayout()
            top.setContentsMargins(0, 0, 0, 0)
            top.setSpacing(8)
            key_lbl = QLabel(str(item.get("key", "")))
            key_lbl.setStyleSheet(f"color:{MAIN};font:bold 12px '{UI_FONT}';border:none;")
            badge = QLabel(f"{item.get('days', 0)} days")
            badge.setStyleSheet(replica_badge_style("gold", font_px=10, padding_v=4, padding_h=10, radius=3, letter_spacing=0.8))
            meta = QLabel(f"{item.get('days', 0)} days | redeemed {str(item.get('redeemed_at', '')).replace('T', ' ')[:16]}")
            meta.setStyleSheet(f"color:{MID};font:11px '{UI_FONT}';border:none;")
            expiry = QLabel(f"Active until {str(item.get('active_until', '')).replace('T', ' ')[:16]}")
            expiry.setStyleSheet(f"color:{DIM};font:11px '{UI_FONT}';border:none;")
            top.addWidget(key_lbl, 1)
            top.addWidget(badge, 0, Qt.AlignmentFlag.AlignRight)
            row.addLayout(top)
            row.addWidget(meta)
            row.addWidget(expiry)
            self._history_lay.addWidget(card)

    def _redeem(self):
        key = self._redeem_input.text().strip()
        if not key:
            self._redeem_msg.setText("Enter a key first.")
            return
        if not self._auth.get("username") or not self._auth.get("session_token"):
            self._redeem_msg.setText("Login again before redeeming.")
            return
        self._redeem_btn.setEnabled(False)
        self._redeem_btn.setText("Redeeming...")
        self._redeem_msg.setText("Redeeming key...")
        self._redeem_worker = RedeemWorker(self._auth, key)
        self._redeem_worker.result.connect(self._on_redeem)
        self._redeem_worker.finished.connect(lambda: setattr(self, "_redeem_worker", None))
        self._redeem_worker.start()

    def _on_redeem(self, ok, msg, resp):
        self._redeem_btn.setEnabled(True)
        self._redeem_btn.setText("Redeem")
        self._redeem_msg.setText(msg)
        if ok:
            self._redeem_input.clear()
            self._status = dict(resp or {})
            self._render()
            payload = dict(resp or {})
            payload["auth"] = dict(self._auth)
            self.account_updated.emit(payload)

class OverviewPage(QWidget):
    tweaks_applied = pyqtSignal()
    open_restore = pyqtSignal()

    def __init__(self, get_ac, parent=None):
        super().__init__(parent)
        self._get_ac = get_ac
        self._plan_active = False
        self._ram_cleaner = None
        self._ram_boost_btn = None
        self._recent_signature = None
        self._score_w = None
        self.setStyleSheet(f"background:{BG};")
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 28)
        root.setSpacing(0)

        self._title = QLabel("Overview")
        self._title.setStyleSheet(
            f"color:{MAIN};font:300 18pt '{UI_FONT}';letter-spacing:0.2px;border:none;background:transparent;"
        )
        self._subtitle = QLabel("")
        self._subtitle.setStyleSheet(
            f"color:{MID};font:11px '{MONO_FONT}';letter-spacing:0.3px;border:none;background:transparent;"
        )
        root.addWidget(self._title)
        root.addWidget(self._subtitle)
        root.addSpacing(28)

        stats = QGridLayout()
        stats.setContentsMargins(0, 0, 0, 0)
        stats.setHorizontalSpacing(12)
        stats.setVerticalSpacing(12)
        self._stats = []
        for index in range(4):
            card = QFrame()
            card.setStyleSheet(f"QFrame{{background:{PANEL};border:1px solid {LINE};border-radius:4px;}}")
            lay = QVBoxLayout(card)
            lay.setContentsMargins(16, 16, 16, 16)
            lay.setSpacing(4)
            label = QLabel("")
            label.setStyleSheet(f"color:{DIM};font:500 9px '{MONO_FONT}';letter-spacing:1.2px;border:none;background:transparent;")
            value = QLabel("")
            value.setStyleSheet(f"color:{MAIN};font:300 22pt '{UI_FONT}';border:none;background:transparent;")
            sub = QLabel("")
            sub.setStyleSheet(f"color:{MID};font:10px '{UI_FONT}';border:none;background:transparent;")
            lay.addWidget(label)
            lay.addWidget(value)
            lay.addWidget(sub)
            if index == 1:
                self._ram_boost_btn = QPushButton("Boost")
                self._ram_boost_btn.setFixedHeight(24)
                self._ram_boost_btn.setFixedWidth(68)
                self._ram_boost_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                self._ram_boost_btn.clicked.connect(self._run_ram_boost)
                self._ram_boost_btn.setStyleSheet(_ghost(self._get_ac()))
                lay.addSpacing(6)
                lay.addWidget(self._ram_boost_btn, 0, Qt.AlignmentFlag.AlignLeft)
            stats.addWidget(card, 0, index)
            self._stats.append((label, value, sub))
        root.addLayout(stats)
        root.addSpacing(12)

        self._score_w = OverviewScoreCard(self._get_ac())
        root.addWidget(self._score_w)

        root.addSpacing(16)
        root.addSpacing(18)
        self._section = QLabel("Recently Applied")
        self._section.setStyleSheet(f"color:{DIM};font:500 9px '{MONO_FONT}';letter-spacing:1.5px;border:none;background:transparent;")
        root.addWidget(self._section)
        root.addSpacing(10)

        self._recent_lay = QVBoxLayout()
        self._recent_lay.setContentsMargins(0, 0, 0, 0)
        self._recent_lay.setSpacing(3)
        root.addLayout(self._recent_lay)
        root.addStretch(1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_live_stats)
        self._timer.start(1500)
        self._score_timer = QTimer(self)
        self._score_timer.timeout.connect(self._refresh_score_card)
        self._score_timer.start(10000)
        self.refresh_score()

    def _clear_recent(self):
        while self._recent_lay.count():
            item = self._recent_lay.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _recent_items(self):
        selected = sorted(load_selected_tweaks())[:4]
        rows = []
        for tweak_id in selected:
            entry = find_tweak_entry(tweak_id)
            if entry:
                rows.append(entry)
        if rows:
            return rows
        return all_tweak_entries()[:4]

    def _make_recent_row(self, entry):
        row = QFrame()
        row.setFixedHeight(58)
        row.setStyleSheet(
            f"QFrame{{background:{PANEL};border:none;border-radius:3px;}}"
            f"QFrame:hover{{background:{SURFACE2};}}"
        )
        lay = QHBoxLayout(row)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(12)
        status = QLabel("")
        status.setFixedSize(6, 6)
        status.setStyleSheet("QLabel{background:#28c840;border-radius:3px;border:none;}")
        status_wrap = QWidget()
        status_wrap.setFixedWidth(6)
        status_wrap.setStyleSheet("background:transparent;border:none;")
        status_lay = QVBoxLayout(status_wrap)
        status_lay.setContentsMargins(0, 7, 0, 0)
        status_lay.setSpacing(0)
        status_lay.addWidget(status, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        status_lay.addStretch(1)
        lay.addWidget(status_wrap)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        name = QLabel(entry.get("name", "Tweak"))
        name.setFixedHeight(19)
        name.setStyleSheet(f"color:{MAIN};font:600 12px '{UI_FONT}';border:none;background:transparent;")
        desc = QLabel(f"{str(entry.get('category', '')).lower()} / {str(entry.get('desc', '')).lower()}")
        desc.setFixedHeight(18)
        desc.setWordWrap(False)
        desc.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        desc.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        desc.setToolTip(desc.text())
        desc.setStyleSheet(f"color:{MID};font:11px '{UI_FONT}';border:none;background:transparent;")
        text_col.addWidget(name)
        text_col.addWidget(desc)
        lay.addLayout(text_col, 1)

        if entry.get("restart"):
            badge = QLabel(str(entry.get("restart")).lower())
            badge.setStyleSheet(
                f"QLabel{{color:{MID};border:1px solid {LINE};border-radius:2px;padding:2px 6px;font:9px '{MONO_FONT}';}}"
            )
            lay.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    def _refresh_recent_rows(self):
        entries = self._recent_items()
        signature = tuple(entry.get("id") or entry.get("name", "") for entry in entries)
        if signature == self._recent_signature:
            return
        self._recent_signature = signature
        self._clear_recent()
        for entry in entries:
            self._recent_lay.addWidget(self._make_recent_row(entry))

    def _refresh_live_stats(self):
        mem = psutil.virtual_memory()
        cpu = stable_cpu_percent()
        uptime_days = max(0, int((time.time() - psutil.boot_time()) // 86400))
        subtitle = f"system / {platform.system()} {platform.release()} / {'admin' if is_admin() else 'user'}"
        self._subtitle.setText(subtitle.lower())
        stats = [
            ("Applied", str(len(load_selected_tweaks())), "tweaks active"),
            ("RAM Usage", f"{mem.percent:.0f}%", f"{mem.used/1073741824:.1f} of {mem.total/1073741824:.0f} gb used"),
            ("CPU", f"{cpu:.0f}%", "avg utilization"),
            ("Uptime", f"{uptime_days}d", "since last boot"),
        ]
        for (label, value, sub), (lbl, val, desc) in zip(self._stats, stats):
            label.setText(lbl)
            value.setText(val)
            sub.setText(desc)

        self._refresh_recent_rows()

    def _refresh_score_card(self):
        if self._score_w:
            self._score_w.refresh()

    def refresh_score(self):
        self._refresh_live_stats()
        if self._score_w:
            self._score_w.refresh(force=True)

    def activate(self):
        if not self._timer.isActive():
            self._timer.start(1500)
        if not self._score_timer.isActive():
            self._score_timer.start(10000)
        self._refresh_live_stats()
        self._refresh_score_card()

    def deactivate(self):
        self._timer.stop()
        self._score_timer.stop()

    def update_accent(self, color):
        if self._ram_boost_btn:
            self._ram_boost_btn.setStyleSheet(_ghost(color))
        if self._score_w:
            self._score_w.update_accent(color)
        self._refresh_live_stats()

    def set_plan_active(self, active):
        self._plan_active = bool(active)
        if self._ram_boost_btn:
            self._ram_boost_btn.setEnabled(self._plan_active)
            self._ram_boost_btn.setToolTip("" if self._plan_active else "Active plan required.")
        if self._score_w:
            self._score_w.set_plan_active(self._plan_active)

    def _set_ram_boost_state(self, enabled=True, text="Boost"):
        if not self._ram_boost_btn:
            return
        self._ram_boost_btn.setEnabled(enabled and self._plan_active)
        self._ram_boost_btn.setText(text)

    def _run_ram_boost(self):
        if not self._plan_active:
            if self._ram_boost_btn:
                self._ram_boost_btn.setToolTip("Active plan required.")
            return
        if self._ram_cleaner and self._ram_cleaner.isRunning():
            return
        self._set_ram_boost_state(False, "...")
        self._ram_cleaner = _RamCleanerWorker(self)
        self._ram_cleaner.result.connect(self._finish_ram_boost)
        self._ram_cleaner.finished.connect(lambda: setattr(self, "_ram_cleaner", None))
        self._ram_cleaner.start()

    def _finish_ram_boost(self, result):
        self._set_ram_boost_state(True, "Boost")
        if self._ram_boost_btn:
            self._ram_boost_btn.setToolTip(result or "")
        self._refresh_live_stats()


class HtmlActivityPage(QWidget):
    def __init__(self, get_ac, parent=None):
        super().__init__(parent)
        self._get_ac = get_ac
        self.setStyleSheet(f"background:{BG};")
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 28)
        root.setSpacing(0)
        self._title = QLabel("Activity")
        self._title.setStyleSheet(replica_title_style())
        self._subtitle = QLabel("log / last 30 entries")
        self._subtitle.setStyleSheet(f"color:{MID};font:11px '{MONO_FONT}';border:none;background:transparent;")
        root.addWidget(self._title)
        root.addWidget(self._subtitle)
        root.addSpacing(24)

        self._scroll = SmoothScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(f"QScrollArea{{background:transparent;border:none;}}")
        host = QWidget()
        host.setStyleSheet("background:transparent;border:none;")
        self._list = QVBoxLayout(host)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(0)
        self._scroll.setWidget(host)
        root.addWidget(self._scroll, 1)
        self._refresh()

    def _clear(self):
        while self._list.count():
            item = self._list.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _entry_row(self, item):
        row = QFrame()
        row.setStyleSheet(f"QFrame{{background:transparent;border:none;border-bottom:1px solid {LINE};}}")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 10, 0, 10)
        lay.setSpacing(14)
        stamp = datetime.fromtimestamp(item.get("ts", time.time())).strftime("%H:%M:%S")
        ts = QLabel(stamp)
        ts.setMinimumWidth(64)
        ts.setStyleSheet(f"color:{DIM};font:10px '{MONO_FONT}';border:none;")
        lay.addWidget(ts)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        title = QLabel(item.get("title") or "Activity")
        title.setStyleSheet(f"color:{MAIN};font:12px '{UI_FONT}';border:none;")
        detail = QLabel(item.get("detail") or item.get("kind", "activity"))
        detail.setStyleSheet(f"color:{MID};font:10px '{MONO_FONT}';border:none;")
        col.addWidget(title)
        col.addWidget(detail)
        lay.addLayout(col, 1)
        status = str(item.get("status", "info")).lower()
        badge = QLabel(status)
        badge.setStyleSheet(replica_badge_style("ok" if status == "ok" else "warn" if status == "error" else "info"))
        lay.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        return row

    def _refresh(self):
        self._clear()
        entries = list(reversed(load_activity_log()))[:30]
        for item in entries:
            self._list.addWidget(self._entry_row(item))
        self._list.addStretch(1)

    def activate(self):
        self._refresh()

    def update_accent(self, color):
        self._refresh()


class QuickToolsPage(QWidget):
    def __init__(self, get_ac, parent=None):
        super().__init__(parent)
        self._get_ac = get_ac
        self._plan_active = False
        self._worker = None
        self._buttons = []
        self.setStyleSheet(f"background:{BG};")
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 28)
        root.setSpacing(0)
        self._title = QLabel("Quick Tools")
        self._title.setStyleSheet(replica_title_style())
        self._subtitle = QLabel("one-click system utilities")
        self._subtitle.setStyleSheet(f"color:{MID};font:11px '{MONO_FONT}';border:none;")
        root.addWidget(self._title)
        root.addWidget(self._subtitle)
        root.addSpacing(24)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        for index, entry in enumerate(quick_tool_entries()):
            card = QFrame()
            card.setStyleSheet(
                f"QFrame{{background:{PANEL};border:1px solid {LINE};border-radius:4px;}}"
                f"QFrame:hover{{background:{SURFACE2};border-color:{_rgba('#ffffff', 32)};}}"
            )
            lay = QVBoxLayout(card)
            lay.setContentsMargins(14, 14, 14, 14)
            lay.setSpacing(10)
            name = QLabel(entry["name"])
            name.setStyleSheet(f"color:{MAIN};font:500 12px '{UI_FONT}';border:none;background:transparent;")
            desc = QLabel(entry.get("desc", ""))
            desc.setWordWrap(True)
            desc.setStyleSheet(f"color:{MID};font:10px '{MONO_FONT}';border:none;line-height:1.5;background:transparent;")
            btn = QPushButton("run")
            btn.setFixedHeight(28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(_ghost(self._get_ac()))
            btn.clicked.connect(lambda _=False, item=entry: self._run_tool(item))
            lay.addWidget(name)
            lay.addWidget(desc)
            lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignLeft)
            grid.addWidget(card, index // 3, index % 3)
            self._buttons.append(btn)
        root.addLayout(grid)
        root.addSpacing(16)
        self._status = QLabel("")
        self._status.setStyleSheet(f"color:{MID};font:10px '{MONO_FONT}';border:none;background:transparent;")
        root.addWidget(self._status)
        root.addStretch(1)

    def _run_tool(self, entry):
        if not self._plan_active:
            self._status.setText("No active plan.")
            return
        if self._worker and self._worker.isRunning():
            return
        self._status.setText(f"running {entry['name'].lower()}...")
        for btn in self._buttons:
            btn.setEnabled(False)
        self._worker = TweakWorker([entry])
        self._worker.done.connect(lambda: self._finish_tool(entry))
        self._worker.start()

    def _finish_tool(self, entry):
        for btn in self._buttons:
            btn.setEnabled(True)
        self._status.setText(f"{entry['name']} finished.")
        self._worker = None

    def activate(self):
        return

    def update_accent(self, color):
        for btn in self._buttons:
            btn.setStyleSheet(_ghost(color))

    def set_plan_active(self, active):
        self._plan_active = bool(active)


class HtmlRestorePage(QWidget):
    def __init__(self, get_ac, parent=None):
        super().__init__(parent)
        self._get_ac = get_ac
        self._rp_result = None
        self._rp_poll = QTimer(self)
        self._rp_poll.timeout.connect(self._poll_restore_result)
        self.setStyleSheet(f"background:{BG};")
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 28)
        root.setSpacing(0)
        self._title = QLabel("Restore")
        self._title.setStyleSheet(replica_title_style())
        self._subtitle = QLabel("system protection / windows restore")
        self._subtitle.setStyleSheet(f"color:{MID};font:11px '{MONO_FONT}';border:none;")
        root.addWidget(self._title)
        root.addWidget(self._subtitle)
        root.addSpacing(24)

        self._card = QFrame()
        self._card.setStyleSheet(f"QFrame{{background:{PANEL};border:1px solid {LINE};border-radius:4px;}}")
        card_l = QHBoxLayout(self._card)
        card_l.setContentsMargins(24, 20, 24, 20)
        card_l.setSpacing(16)
        self._icon = QLabel("READY")
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setFixedWidth(52)
        self._icon.setStyleSheet(replica_badge_style("green"))
        self._restore_title = QLabel("")
        self._restore_title.setStyleSheet(f"color:{MAIN};font:500 13px '{UI_FONT}';border:none;")
        self._restore_sub = QLabel("")
        self._restore_sub.setStyleSheet(f"color:{MID};font:10px '{MONO_FONT}';border:none;")
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(3)
        text_col.addWidget(self._restore_title)
        text_col.addWidget(self._restore_sub)
        self._revert_btn = QPushButton("revert")
        self._revert_btn.setFixedHeight(34)
        self._revert_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._revert_btn.setStyleSheet(_ghost(self._get_ac()))
        self._revert_btn.clicked.connect(lambda: run_cmd("rstrui.exe"))
        card_l.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        card_l.addLayout(text_col, 1)
        card_l.addWidget(self._revert_btn)
        root.addWidget(self._card)

        root.addSpacing(24)
        self._create_lbl = QLabel("Create New")
        self._create_lbl.setStyleSheet(f"color:{DIM};font:500 9px '{MONO_FONT}';letter-spacing:1.5px;border:none;")
        self._create_desc = QLabel("Creates a Windows System Restore checkpoint before applying changes.\nRequires admin privileges.")
        self._create_desc.setStyleSheet(f"color:{MID};font:12px '{MONO_FONT}';border:none;line-height:1.6;")
        self._create_btn = QPushButton("create restore point")
        self._create_btn.setFixedHeight(36)
        self._create_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._create_btn.setStyleSheet(_solid(self._get_ac()))
        self._create_btn.clicked.connect(self._make_rp)
        self._prog = _prog_bar(self._get_ac())
        self._prog.setVisible(False)
        self._status = QLabel("")
        self._status.setStyleSheet(f"color:{MID};font:10px '{MONO_FONT}';border:none;")
        root.addWidget(self._create_lbl)
        root.addSpacing(8)
        root.addWidget(self._create_desc)
        root.addSpacing(16)
        root.addWidget(self._create_btn, 0, Qt.AlignmentFlag.AlignLeft)
        root.addSpacing(12)
        root.addWidget(self._prog)
        root.addSpacing(8)
        root.addWidget(self._status)
        root.addStretch(1)
        self._refresh()

    def _refresh(self):
        if has_restore_point():
            self._icon.show()
            self._icon.setText("READY")
            self._icon.setStyleSheet(replica_badge_style("green"))
            self._restore_title.setText("Restore Point Created")
            self._restore_sub.setText("hextra tweaker checkpoint available")
            self._revert_btn.setEnabled(True)
        else:
            self._icon.hide()
            self._restore_title.setText("No Restore Point")
            self._restore_sub.setText("create a checkpoint before applying changes")
            self._revert_btn.setEnabled(False)

    def _make_rp(self):
        self._create_btn.setEnabled(False)
        self._create_btn.setText("creating...")
        self._prog.setVisible(True)
        self._prog.setValue(12)
        self._status.setText("creating restore point...")
        self._rp_result = None
        def _do():
            self._rp_result = create_restore_point()
        threading.Thread(target=_do, daemon=True).start()
        self._rp_poll.start(200)

    def _poll_restore_result(self):
        if self._rp_result is None:
            return
        self._rp_poll.stop()
        ok, msg = self._rp_result
        self._prog.setValue(100 if ok else 0)
        self._status.setText(msg)
        self._create_btn.setEnabled(True)
        self._create_btn.setText("create restore point")
        self._refresh()

    def activate(self):
        self._refresh()

    def update_accent(self, color):
        self._create_btn.setStyleSheet(_solid(color))
        self._revert_btn.setStyleSheet(_ghost(color))
        self._prog.setStyleSheet(_prog_bar(color).styleSheet())


class HtmlSettingsPage(QWidget):
    accent_changed = pyqtSignal(str)
    snow_changed = pyqtSignal(bool)
    game_paths_changed = pyqtSignal()
    data_imported = pyqtSignal()
    check_updates_requested = pyqtSignal()

    def __init__(self, get_ac, parent=None):
        super().__init__(parent)
        self._get_ac = get_ac
        self._swatches = []
        self._game_edits = {}
        self._action_buttons = []
        self.setStyleSheet(f"background:{BG};")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"QScrollArea{{background:{BG};border:none;}}")
        host = QWidget()
        host.setStyleSheet(f"background:{BG};")
        root = QVBoxLayout(host)
        root.setContentsMargins(32, 28, 32, 28)
        root.setSpacing(0)
        self._title = QLabel("Settings")
        self._title.setStyleSheet(replica_title_style())
        self._subtitle = QLabel("preferences / appearance")
        self._subtitle.setStyleSheet(f"color:{MID};font:11px '{MONO_FONT}';border:none;")
        root.addWidget(self._title)
        root.addWidget(self._subtitle)
        root.addSpacing(24)

        appearance = QLabel("Appearance")
        appearance.setStyleSheet(f"color:{DIM};font:500 9px '{MONO_FONT}';letter-spacing:1.5px;border:none;")
        root.addWidget(appearance)
        root.addSpacing(12)

        accent_row = QFrame()
        accent_row.setStyleSheet(f"QFrame{{background:transparent;border:none;border-bottom:1px solid {LINE};}}")
        ar = QHBoxLayout(accent_row)
        ar.setContentsMargins(0, 12, 0, 12)
        ar.setSpacing(12)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        text.addWidget(_lbl("Accent Color", MAIN, size=12))
        text.addWidget(_lbl("highlight and interactive elements", MID, size=10))
        swatch_row = QHBoxLayout()
        swatch_row.setContentsMargins(0, 12, 0, 0)
        swatch_row.setSpacing(8)
        for name, color in THEMES.items():
            btn = QPushButton()
            btn.setFixedSize(20, 20)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(name.title())
            btn.clicked.connect(lambda _=False, c=color: self.accent_changed.emit(c))
            self._swatches.append((btn, color))
            swatch_row.addWidget(btn)
        text.addLayout(swatch_row)
        ar.addLayout(text, 1)
        root.addWidget(accent_row)

        snow_row = QFrame()
        snow_row.setStyleSheet(f"QFrame{{background:transparent;border:none;border-bottom:1px solid {LINE};}}")
        sr = QHBoxLayout(snow_row)
        sr.setContentsMargins(0, 12, 0, 12)
        sr.setSpacing(12)
        snow_text = QVBoxLayout()
        snow_text.setContentsMargins(0, 0, 0, 0)
        snow_text.setSpacing(2)
        snow_text.addWidget(_lbl("Snow Effect", MAIN, size=12))
        snow_text.addWidget(_lbl("animated particles in background", MID, size=10))
        self._snow = Toggle(self._get_ac())
        self._snow.setChecked(is_snow_on())
        self._snow.clicked.connect(self._toggle_snow_effect)
        sr.addLayout(snow_text, 1)
        sr.addWidget(self._snow, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(snow_row)

        root.addSpacing(24)
        updates = QLabel("Updates")
        updates.setStyleSheet(f"color:{DIM};font:500 9px '{MONO_FONT}';letter-spacing:1.5px;border:none;")
        root.addWidget(updates)
        root.addSpacing(12)

        update_row = QFrame()
        update_row.setStyleSheet(f"QFrame{{background:transparent;border:none;border-bottom:1px solid {LINE};}}")
        ur = QHBoxLayout(update_row)
        ur.setContentsMargins(0, 12, 0, 12)
        ur.setSpacing(12)
        update_text = QVBoxLayout()
        update_text.setContentsMargins(0, 0, 0, 0)
        update_text.setSpacing(2)
        update_text.addWidget(_lbl("Check for Updates", MAIN, size=12))
        update_text.addWidget(_lbl("look for a newer Hextra build on your server", MID, size=10))
        self._update_status = _lbl(f"Current build {VERSION}", MID, size=10)
        self._update_status.setWordWrap(True)
        update_text.addWidget(self._update_status)
        self._check_updates_btn = QPushButton("check")
        self._check_updates_btn.setFixedHeight(28)
        self._check_updates_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._check_updates_btn.setStyleSheet(_ghost(self._get_ac()))
        self._check_updates_btn.clicked.connect(self._request_update_check)
        self._action_buttons.append(self._check_updates_btn)
        ur.addLayout(update_text, 1)
        ur.addWidget(self._check_updates_btn)
        root.addWidget(update_row)

        root.addSpacing(24)
        account = QLabel("Data")
        account.setStyleSheet(f"color:{DIM};font:500 9px '{MONO_FONT}';letter-spacing:1.5px;border:none;")
        root.addWidget(account)
        root.addSpacing(12)
        for label, desc, action in [
            ("Export Settings", "save config to file", self._export_settings),
            ("Import Settings", "load config from file", self._import_settings),
        ]:
            row = QFrame()
            row.setStyleSheet(f"QFrame{{background:transparent;border:none;border-bottom:1px solid {LINE};}}")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 12, 0, 12)
            rl.setSpacing(12)
            text_col = QVBoxLayout()
            text_col.setContentsMargins(0, 0, 0, 0)
            text_col.setSpacing(2)
            text_col.addWidget(_lbl(label, MAIN, size=12))
            text_col.addWidget(_lbl(desc, MID, size=10))
            btn = QPushButton(label.split()[0].lower())
            btn.setFixedHeight(28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(_ghost(self._get_ac()))
            btn.clicked.connect(action)
            self._action_buttons.append(btn)
            rl.addLayout(text_col, 1)
            rl.addWidget(btn)
            root.addWidget(row)

        root.addSpacing(24)
        folders = QLabel("Folders")
        folders.setStyleSheet(f"color:{DIM};font:500 9px '{MONO_FONT}';letter-spacing:1.5px;border:none;")
        root.addWidget(folders)
        root.addSpacing(12)
        gp = load_game_paths()
        for game in ["Roblox", "FiveM", "Valorant", "CS2", "Minecraft", "Fortnite", "Apex"]:
            row = QFrame()
            row.setStyleSheet(f"QFrame{{background:transparent;border:none;border-bottom:1px solid {LINE};}}")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 12, 0, 12)
            rl.setSpacing(8)
            label = QLabel(game)
            label.setFixedWidth(90)
            label.setStyleSheet(f"color:{MAIN};font:12px '{UI_FONT}';border:none;")
            edit = QLineEdit()
            edit.setFixedHeight(34)
            edit.setText(gp.get(game, ""))
            edit.setPlaceholderText("optional")
            edit.setStyleSheet(replica_input_style(self._get_ac()))
            self._game_edits[game] = edit
            browse = QPushButton("browse")
            browse.setFixedHeight(28)
            browse.setStyleSheet(_ghost(self._get_ac()))
            browse.clicked.connect(lambda _=False, g=game, e=edit: self._browse_game_folder(g, e))
            self._action_buttons.append(browse)
            clear = QPushButton("clear")
            clear.setFixedHeight(28)
            clear.setStyleSheet(_ghost(self._get_ac()))
            clear.clicked.connect(lambda _=False, g=game, e=edit: self._clear_game_folder(g, e))
            self._action_buttons.append(clear)
            rl.addWidget(label)
            rl.addWidget(edit, 1)
            rl.addWidget(browse)
            rl.addWidget(clear)
            root.addWidget(row)
        root.addStretch(1)
        scroll.setWidget(host)
        outer.addWidget(scroll)
        self.update_accent(self._get_ac())

    def _browse_game_folder(self, game_key, edit):
        start = edit.text().strip() or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, f"select folder - {game_key}", start)
        if folder:
            edit.setText(folder)
            save_game_path(game_key, folder)
            self.game_paths_changed.emit()

    def _clear_game_folder(self, game_key, edit):
        edit.clear()
        save_game_path(game_key, "")
        self.game_paths_changed.emit()

    def _export_settings(self):
        path, _ = QFileDialog.getSaveFileName(self, "export settings", os.path.expanduser("~\\hextra-tweaker-settings.json"), "JSON (*.json)")
        if path:
            export_settings_file(path)

    def _import_settings(self):
        path, _ = QFileDialog.getOpenFileName(self, "import settings", os.path.expanduser("~"), "JSON (*.json)")
        if not path:
            return
        ok, _msg = import_settings_file(path)
        if ok:
            self.reload_from_storage()
            self.data_imported.emit()

    def _toggle_snow_effect(self):
        enabled = self._snow.isChecked()
        set_snow(enabled)
        self.snow_changed.emit(enabled)

    def _request_update_check(self):
        self.set_update_status("Checking for updates...", MID)
        self.check_updates_requested.emit()

    def reload_from_storage(self):
        gp = load_game_paths()
        for key, edit in self._game_edits.items():
            edit.setText(gp.get(key, ""))
        self.update_accent(load_data().get("color", self._get_ac()))
        self._snow.setChecked(is_snow_on())

    def activate(self):
        self.reload_from_storage()

    def _swatch_style(self, swatch, selected):
        border = MAIN if selected else "transparent"
        hover = _rgba("#ffffff", 84)
        if swatch == "rainbow":
            return (
                "QPushButton{"
                "background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                "stop:0 #ff004d,stop:0.20 #ff7a00,stop:0.40 #ffe600,"
                "stop:0.60 #00d084,stop:0.80 #3b82f6,stop:1 #8b5cf6);"
                f"border:2px solid {border};border-radius:10px;"
                "}"
                f"QPushButton:hover{{border-color:{MAIN if selected else hover};}}"
            )
        return (
            "QPushButton{"
            f"background:{swatch};border:2px solid {border};border-radius:10px;"
            "}"
            f"QPushButton:hover{{border-color:{MAIN if selected else hover};}}"
        )

    def update_accent(self, color):
        selected_theme = load_data().get("color", color)
        live_color = self._get_ac() if selected_theme == "rainbow" else (self._get_ac() if color == "rainbow" else color)
        for btn, swatch in self._swatches:
            btn.setStyleSheet(self._swatch_style(swatch, swatch == selected_theme))
        self._snow.set_accent(live_color)
        for btn in self._action_buttons:
            btn.setStyleSheet(_ghost(live_color))
        for edit in self._game_edits.values():
            edit.setStyleSheet(replica_input_style(live_color))

    def update_accent_rainbow(self, color):
        self.update_accent(color)

    def set_update_status(self, text, color=MID):
        self._update_status.setText(text or f"Current build {VERSION}")
        self._update_status.setStyleSheet(f"color:{color};font:400 10px '{UI_FONT}';border:none;background:transparent;")


class Dashboard(QWidget):
    def __init__(self, get_ac, set_ac, win, parent=None):
        super().__init__(parent)
        self._get_ac = get_ac
        self._set_ac = set_ac
        self._win = win
        self._auth = load_auth()
        self._plan_active = False
        self._license_status_worker = None
        self._license_refresh_timer = QTimer(self)
        self._license_refresh_timer.setInterval(5 * 60 * 1000)
        self._license_refresh_timer.timeout.connect(self._refresh_license_badge)
        self.setStyleSheet(f"background:{BG};")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        titlebar = QFrame()
        titlebar.setFixedHeight(44)
        titlebar.setStyleSheet(f"QFrame{{background:{BG};border:none;border-bottom:1px solid {LINE};}}")
        tl = QHBoxLayout(titlebar)
        tl.setContentsMargins(16, 0, 16, 0)
        tl.setSpacing(8)
        self._logo = QLabel()
        self._logo.setTextFormat(Qt.TextFormat.RichText)
        self._logo.setStyleSheet(f"font:11px '{MONO_FONT}';letter-spacing:1.2px;border:none;")
        tl.addWidget(self._logo)
        tl.addStretch(1)
        if not getattr(win, "_native_frame", False):
            for color, handler in [
                ("#28c840", lambda: win.showNormal() if win.isMaximized() else win.showMaximized()),
                ("#febc2e", win.showMinimized),
                ("#ff5f57", win.close),
            ]:
                btn = QPushButton()
                btn.setFixedSize(12, 12)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(handler)
                btn.setStyleSheet(
                    "QPushButton{"
                    f"background:{color};border:none;border-radius:6px;"
                    "}"
                )
                tl.addWidget(btn)
        root.addWidget(titlebar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self._sidebar = Sidebar(self._get_ac())
        self._sidebar.page_selected.connect(self._switch)
        body.addWidget(self._sidebar)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("QStackedWidget{background:transparent;border:none;}")
        self._home = OverviewPage(self._get_ac)
        self._stack.addWidget(self._home)
        self._activity = HtmlActivityPage(self._get_ac)
        self._stack.addWidget(self._activity)
        self._account = AccountPage(self._get_ac)
        self._account.account_updated.connect(self._on_account_updated)
        self._stack.addWidget(self._account)
        self._quick = QuickToolsPage(self._get_ac)
        self._stack.addWidget(self._quick)
        self._profiles = ProfilesPage(self._get_ac)
        self._profiles.selection_changed.connect(self._refresh_score)
        self._stack.addWidget(self._profiles)
        self._tpages = {}
        for cat in CATEGORY_ORDER:
            pg = TweakPage(cat, self._get_ac)
            pg.tweaks_applied.connect(self._refresh_score)
            self._stack.addWidget(pg)
            self._tpages[cat] = pg
            pg.catalog_changed.connect(self._refresh_dynamic_pages)
        self._settings = HtmlSettingsPage(self._get_ac)
        self._settings.accent_changed.connect(self._on_accent)
        self._settings.snow_changed.connect(self.set_snow_enabled)
        self._settings.game_paths_changed.connect(self._sidebar.refresh_game_detection)
        self._settings.data_imported.connect(self._reload_from_data)
        self._settings.check_updates_requested.connect(self._request_update_check)
        self._stack.addWidget(self._settings)
        self._restore = HtmlRestorePage(self._get_ac)
        self._stack.addWidget(self._restore)

        body.addWidget(self._stack, 1)
        root.addLayout(body, 1)
        self._snow = SnowCanvas(self._get_ac(), self, opacity_scale=0.22, size_scale=0.72)
        self._snow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._snow_on = is_snow_on()
        self._snow.setVisible(self._snow_on)
        self._snow.raise_()
        self._update_logo(self._get_ac())
        self._account.set_session(self._auth)
        self._set_plan_access(False)
        QTimer.singleShot(0, self._refresh_license_badge)
        self._license_refresh_timer.start()

    def _switch(self, key):
        if key == "home":
            target = self._home
        elif key == "activity":
            target = self._activity
        elif key == "account":
            target = self._account
        elif key == "quick":
            target = self._quick
        elif key == "profiles":
            target = self._profiles
        elif key == "settings":
            target = self._settings
        elif key == "restore":
            target = self._restore
        elif key.startswith("tweak:"):
            target = self._tpages.get(key[6:])
        else:
            target = None
        if target is None or target is self._stack.currentWidget():
            return
        current = self._stack.currentWidget()
        if current is not None and hasattr(current, "deactivate"):
            current.deactivate()
        self._stack.setCurrentWidget(target)
        if hasattr(target, "update_accent"):
            target.update_accent(self._get_ac())
        if hasattr(target, "activate"):
            target.activate()

    def _refresh_score(self):
        self._home.refresh_score()

    def _refresh_dynamic_pages(self):
        pass

    def _request_update_check(self):
        if hasattr(self._win, "trigger_update_check"):
            self._win.trigger_update_check(manual=True)

    def _reload_from_data(self):
        stored = load_data().get("color", self._get_ac())
        self._set_ac(stored)
        self._settings.reload_from_storage()
        self.set_snow_enabled(is_snow_on())
        self._sidebar.refresh_game_detection()
        self._refresh_dynamic_pages()

    def sync_accent(self, color):
        self._sidebar.set_accent(color)
        self._update_logo(color)
        self._snow.set_accent(color)
        self._home.update_accent(color)
        self._activity.update_accent(color)
        self._account.update_accent(color)
        self._quick.update_accent(color)
        self._profiles.update_accent(color)
        self._settings.update_accent(color)
        self._restore.update_accent(color)
        for pg in self._tpages.values():
            pg.update_accent(color)
            for sw in pg._sw:
                sw.set_accent(color)

    def _on_accent(self, color):
        self._set_ac(color)
        self.sync_accent(self._get_ac())

    def set_title_color(self, color):
        self._update_logo(color)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_snow") and self._snow:
            self._snow.setGeometry(0, 0, self.width(), self.height())
            self._snow.raise_()

    def tick(self):
        if self._snow_on:
            self._snow.tick()

    def set_snow_enabled(self, enabled):
        self._snow_on = bool(enabled)
        if hasattr(self, "_snow") and self._snow:
            self._snow.setVisible(self._snow_on)
            if self._snow_on:
                self._snow.raise_()
                self._snow.update()

    def _update_logo(self, color):
        self._logo.setText(
            f"<span style='color:{MID};'>Hex</span><span style='color:{color};'>tra</span>"
        )

    def _refresh_license_badge(self):
        if self._license_status_worker is not None:
            return
        auth = self._auth if isinstance(getattr(self, "_auth", None), dict) and self._auth.get("session_token") else load_auth()
        if auth.get("mode") != "account" or not auth.get("username") or not auth.get("session_token"):
            self._sidebar.set_account_summary("guest", "No account", False)
            self._set_plan_access(False)
            return
        self._auth = auth
        self._account.set_session(auth)
        self._sidebar.set_account_summary(auth.get("username", "guest"), "Checking plan...", True)
        self._license_status_worker = AccountStatusWorker(auth)
        self._license_status_worker.result.connect(self._apply_license_badge)
        self._license_status_worker.finished.connect(lambda: setattr(self, "_license_status_worker", None))
        self._license_status_worker.start()

    def _apply_license_badge(self, text, tone, resp):
        if isinstance(resp, dict) and resp.get("locked"):
            self._auth = {}
            clear_auth()
        active = bool(isinstance(resp, dict) and resp.get("success", True) is not False and resp.get("licensed"))
        self._set_plan_access(active)
        if isinstance(resp, dict) and resp.get("success", True) is not False:
            self._account.set_session(self._auth, resp)
        self._sidebar.set_account_summary(self._auth.get("username", "guest"), text, True)

    def _on_account_updated(self, payload):
        if not isinstance(payload, dict):
            return
        auth = payload.get("auth")
        if isinstance(auth, dict):
            self._auth = auth
        self._apply_license_badge(*_account_days_left_text(payload), payload)

    def _set_plan_access(self, active):
        previous = self._plan_active
        self._plan_active = bool(active)
        if previous and not self._plan_active:
            set_selected_tweaks([])
        if hasattr(self, "_home") and self._home:
            self._home.set_plan_active(self._plan_active)
        if hasattr(self, "_quick") and self._quick:
            self._quick.set_plan_active(self._plan_active)
        if hasattr(self, "_profiles") and self._profiles:
            self._profiles.set_plan_active(self._plan_active)
        for pg in getattr(self, "_tpages", {}).values():
            if hasattr(pg, "set_plan_active"):
                pg.set_plan_active(self._plan_active)
                if not self._plan_active and hasattr(pg, "_build_entries"):
                    pg._build_entries()

# motd
class MotdOverlay(QWidget):
    done = pyqtSignal()

    def __init__(self, motd, accent, parent):
        super().__init__(parent)
        self._accent = QColor(accent)
        self._secs   = 5

        self.setGeometry(0, 0, parent.width(), parent.height())

        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.setContentsMargins(0, 0, 0, 0)

        card = QFrame(self)
        card.setFixedWidth(480)
        card.setStyleSheet(replica_hero_style(accent))
        cl = QVBoxLayout(card)
        cl.setContentsMargins(30, 26, 30, 24)
        cl.setSpacing(16)

        hrow = QHBoxLayout()
        icon = QLabel("NEWS")
        icon.setStyleSheet("font-size:22px; border:none; background:transparent;")
        title = QLabel("Message of the Day")
        title.setStyleSheet(f"color:{MAIN};font:700 15pt '{TITLE_FONT}';border:none;background:transparent;")
        icon.setText("NEWS")
        icon.setStyleSheet(replica_badge_style("cyan"))
        hrow.addWidget(icon); hrow.addSpacing(8); hrow.addWidget(title); hrow.addStretch()
        cl.addLayout(hrow)

        line = QFrame(); line.setFixedHeight(1)
        line.setStyleSheet(f"background:{_rgba(accent, 85)}; border:none;")
        cl.addWidget(line)

        msg = QLabel(motd.strip())
        msg.setWordWrap(True)
        msg.setStyleSheet(f"color:#e0e2f0;font:500 10pt '{UI_FONT}';border:none;background:transparent;")
        msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cl.addWidget(msg)

        self._btn = QPushButton(f"Continue ({self._secs}s)")
        self._btn.setFixedHeight(42)
        self._btn.setEnabled(False)
        self._btn.setCursor(Qt.CursorShape.ForbiddenCursor)
        self._btn.setStyleSheet(f"""
            QPushButton {{
                background: #1a1b22;
                color: #484a5a;
                border: 1px solid #2a2b38;
                border-radius: 12px;
                font: 600 10pt 'Segoe UI';
            }}
        """)
        self._btn.clicked.connect(self._close)
        cl.addWidget(self._btn)

        self._accent_hex = accent
        root.addWidget(card)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 180))
        p.end()

    def _tick(self):
        self._secs -= 1
        if self._secs > 0:
            self._btn.setText(f"Continue ({self._secs}s)")
        else:
            self._timer.stop()
            self._btn.setEnabled(True)
            self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._btn.setText("Continue")
            self._btn.setStyleSheet(f"""
                QPushButton {{
                    background: {self._accent_hex};
                    color: white;
                    border: none;
                    border-radius: 12px;
                    font: 600 10pt 'Segoe UI';
                }}
                QPushButton:hover {{
                    background: {QColor(self._accent_hex).lighter(120).name()};
                }}
            """)

    def _close(self):
        self._timer.stop()
        self.hide()
        self.deleteLater()
        self.done.emit()

# login
class LoginPage(QWidget):
    success       = pyqtSignal()
    logged_in     = pyqtSignal(dict)
    theme_changed = pyqtSignal(str)
    motd_received = pyqtSignal(str)

    def __init__(self, accent, parent=None):
        super().__init__(parent); self._accent = accent; self._worker = None; self._mode = "login"
        self._selected_theme = load_data().get("color", accent)
        self._hextra_anim_refs = []
        self.setStyleSheet(f"background:{BG};")

        self._snow = SnowCanvas(accent, self)
        self._snow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._snow_on = is_snow_on()
        self._snow.setVisible(self._snow_on)

        root = QVBoxLayout(self); root.setAlignment(Qt.AlignmentFlag.AlignCenter); root.setSpacing(0); root.setContentsMargins(0,0,0,0)

        self._logo = QLabel("HEXTRA"); self._logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._logo.setStyleSheet(f"font-weight:900;font-size:84px;color:{MAIN};letter-spacing:3px;border:none;background:transparent;")
        root.addWidget(self._logo)

        self._sub = QLabel("Performance tuning for Windows"); self._sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub.setStyleSheet(f"color:{MID};font:600 10pt '{UI_FONT}';letter-spacing:0.4px;border:none;background:transparent;")
        root.addWidget(self._sub)
        root.addSpacing(34)

        self._card = QFrame(); self._card.setFixedWidth(380)
        self._card.setStyleSheet(replica_hero_style(accent))
        cl = QVBoxLayout(self._card); cl.setContentsMargins(28,26,28,24); cl.setSpacing(14)

        self._eyebrow = QLabel("ACCOUNT ACCESS"); self._eyebrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._eyebrow.setStyleSheet(replica_section_caption(accent))
        cl.addWidget(self._eyebrow)

        self._card_title = QLabel("Sign in to continue"); self._card_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._card_title.setStyleSheet(f"color:{MAIN};font:700 16pt '{TITLE_FONT}';border:none;background:transparent;")
        cl.addWidget(self._card_title)

        self._card_sub = QLabel("Sync your plan, theme, and restore-aware workflow across sessions.")
        self._card_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._card_sub.setWordWrap(True)
        self._card_sub.setStyleSheet(f"color:{MID};font:500 9pt '{UI_FONT}';border:none;background:transparent;")
        cl.addWidget(self._card_sub)

        self._user = QLineEdit(); self._user.setPlaceholderText("Username"); self._user.setFixedHeight(44)
        self._user.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        cl.addWidget(self._user)

        self._email = QLineEdit(); self._email.setPlaceholderText("Email"); self._email.setFixedHeight(44)
        self._email.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        cl.addWidget(self._email)

        self._pw = QLineEdit(); self._pw.setPlaceholderText("Password"); self._pw.setFixedHeight(46)
        self._pw.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter); self._pw.setEchoMode(QLineEdit.EchoMode.Password)
        cl.addWidget(self._pw)

        rem_row = QHBoxLayout(); rem_row.setContentsMargins(2,0,2,0)
        self._rem = _Check("Keep me signed in", accent)
        saved = load_auth()
        if saved.get("mode") == "account":
            self._user.setText(saved.get("username", ""))
            self._email.setText(saved.get("email", ""))
            self._rem.setChecked(True)
        rem_row.addWidget(self._rem); rem_row.addStretch(); cl.addLayout(rem_row)

        self._msg = QLabel(""); self._msg.setAlignment(Qt.AlignmentFlag.AlignCenter); self._msg.setFixedHeight(36)
        self._msg.setWordWrap(True)
        self._msg.setStyleSheet(f"color:{MID};font:600 9pt '{UI_FONT}';border:none;background:transparent;")
        cl.addWidget(self._msg)

        self._btn = QPushButton("Sign In"); self._btn.setFixedHeight(46)
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor); self._btn.setStyleSheet(_solid(accent))
        self._btn.clicked.connect(self._login)
        cl.addWidget(self._btn)

        self._switch_btn = QPushButton("Create Account")
        self._switch_btn.setFixedHeight(38)
        self._switch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._switch_btn.clicked.connect(self._toggle_mode)
        cl.addWidget(self._switch_btn)
        root.addWidget(self._card, alignment=Qt.AlignmentFlag.AlignCenter)
        root.addSpacing(18)

        self._theme_buttons = []
        self._theme_wrap = QWidget()
        theme_wrap_lay = QVBoxLayout(self._theme_wrap); theme_wrap_lay.setContentsMargins(0,0,0,0); theme_wrap_lay.setSpacing(8)
        theme_row = QHBoxLayout(); theme_row.setSpacing(8); theme_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for name, color in list(THEMES.items())[:11]:
            b = QPushButton(); b.setFixedSize(22, 22); b.setCursor(Qt.CursorShape.PointingHandCursor); b.setToolTip(name if color != "rainbow" else "rainbow")
            b.clicked.connect(lambda _, c=color: self._apply_theme(c))
            theme_row.addWidget(b)
            self._theme_buttons.append((b, color))
        theme_wrap_lay.addLayout(theme_row)
        self._theme_hint = QLabel("Choose your accent"); self._theme_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._theme_hint.setStyleSheet(f"color:{MID};font:600 9pt '{UI_FONT}';border:none;background:transparent;")
        theme_wrap_lay.addWidget(self._theme_hint)
        root.addWidget(self._theme_wrap)
        self._theme_wrap.hide()
        root.addSpacing(10)
        self._update_theme_buttons(self._selected_theme)

        self._snow_btn = QPushButton()
        self._snow_btn.setFixedHeight(26)
        self._snow_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_snow_btn()
        self._snow_btn.clicked.connect(self._toggle_snow)
        root.addWidget(self._snow_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        for field in (self._user, self._email, self._pw):
            field.returnPressed.connect(self._login)
        self._apply_input_styles(accent)
        self._update_mode_ui()
        QTimer.singleShot(800, self._fetch_login_motd)

    def _apply_theme(self, color):
        self.set_theme_state(color)
        self.theme_changed.emit(color)
 
    def set_theme_state(self, selected_color, display_color=None):
        self._selected_theme = selected_color
        if display_color is None:
            display_color = "#e60000" if selected_color == "rainbow" else selected_color
        self._accent = display_color
        self._update_theme_buttons(selected_color)
        self.set_logo_color(display_color)

    def _fetch_login_motd(self):
        w = _MotdPollWorker(); w.result.connect(self._on_login_motd); w.start()
        self._motd_poll_worker = w

    def _on_login_motd(self, motd):
        if motd and motd.strip():
            self.motd_received.emit(motd.strip())

    def resizeEvent(self, e): super().resizeEvent(e); self._snow.setGeometry(0,0,self.width(),self.height())
    def tick(self):
        if self._snow_on: self._snow.tick()

    def _toggle_snow(self):
        self._snow_on = not self._snow_on
        set_snow(self._snow_on)
        self._snow.setVisible(self._snow_on)
        self._update_snow_btn()

    def _update_snow_btn(self):
        if self._snow_on:
            self._snow_btn.setText("Snow Effects On")
            self._snow_btn.setStyleSheet(f"QPushButton{{background:transparent;color:{MAIN};border:none;border-radius:10px;font:600 9pt '{UI_FONT}';padding:0 12px;}}QPushButton:hover{{color:{MAIN};background:transparent;}}")
        else:
            self._snow_btn.setText("Snow Effects Off")
            self._snow_btn.setStyleSheet(f"QPushButton{{background:transparent;color:{DIM};border:none;border-radius:10px;font:600 9pt '{UI_FONT}';padding:0 12px;}}QPushButton:hover{{color:{MID};background:transparent;}}")

    def _update_theme_buttons(self, selected_color):
        for btn, color in getattr(self, "_theme_buttons", []):
            selected = color == selected_color
            bg = "#f5f7fa" if selected else "#111318"
            edge = "#f5f7fa" if selected else _rgba("#ffffff", 42)
            btn.setStyleSheet(
                f"QPushButton{{background:{bg};border:1px solid {edge};border-radius:11px;}}"
                f"QPushButton:hover{{border-color:{_rgba('#ffffff', 84)};}}"
            )

    def set_logo_color(self, color):
        self._logo.setStyleSheet(f"font-weight:900;font-size:84px;color:{MAIN};letter-spacing:3px;border:none;background:transparent;")
        self._snow.set_accent(QColor("#ffffff"))
        self._update_login_colors(color)

    def _update_login_colors(self, color):
        if color != "rainbow":
            self._accent = color
        self._btn.setStyleSheet(_solid(color))
        self._card.setStyleSheet(replica_hero_style(color))
        apply_glass_shadow(self._card, color, blur=54, y=20, alpha=82)
        self._eyebrow.setStyleSheet(replica_section_caption(color))
        self._apply_input_styles(color)
        self._update_theme_buttons(self._selected_theme)
        if self._snow_on:
            self._snow_btn.setStyleSheet(f"QPushButton{{background:transparent;color:{MAIN};border:none;border-radius:10px;font:600 9pt '{UI_FONT}';padding:0 12px;}}QPushButton:hover{{color:{MAIN};background:transparent;}}")

    def _apply_input_styles(self, color):
        field_style = replica_input_style(color).replace(f"font:10pt '{UI_FONT}'", f"font:600 10pt '{UI_FONT}'")
        for field in (self._user, self._email, self._pw):
            field.setStyleSheet(field_style)
        self._switch_btn.setStyleSheet(
            f"QPushButton{{background:{REPLICA['surface_alt']};color:{MAIN};border:1px solid {REPLICA['line_soft']};border-radius:14px;font:600 9pt '{UI_FONT}';padding:0 12px;}}"
            f"QPushButton:hover{{border-color:{REPLICA['line']};color:{MAIN};background:{CARD};}}"
        )

    def _toggle_mode(self):
        self._mode = "register" if self._mode == "login" else "login"
        self._update_mode_ui()

    def _update_mode_ui(self):
        registering = self._mode == "register"
        self._card_title.setText("Create your account" if registering else "Sign in to continue")
        self._card_sub.setText("Set up your account to sync access and manage your plan from one place." if registering else "Sync your plan, theme, and restore-aware workflow across sessions.")
        self._email.setVisible(registering)
        self._btn.setText("Create Account" if registering else "Sign In")
        self._switch_btn.setText("Back to Sign In" if registering else "Create Account")

    def _login(self):
        username = self._user.text().strip()
        email = self._email.text().strip()
        password = self._pw.text()
        if not username:
            self._set_msg("Enter your username.", MID); return
        if self._mode == "register" and not email:
            self._set_msg("Enter your email.", MID); return
        if not password:
            self._set_msg("Enter your password.", MID); return
        self._btn.setEnabled(False); self._switch_btn.setEnabled(False)
        self._btn.setText("Working...")
        self._set_msg("Logging in...", MID)
        self._worker = AccountLoginWorker(username, password, self._rem.isChecked(), self._mode == "register", email)
        self._worker.result.connect(self._on_result)
        self._worker.start()

    def _on_result(self, ok, msg, resp):
        self._btn.setEnabled(True); self._switch_btn.setEnabled(True)
        self._btn.setText("Create Account" if self._mode == "register" else "Sign In")
        if ok:
            auth_info = _account_payload(
                self._user.text().strip(),
                resp.get("session_token", ""),
                resp.get("email", "") or self._email.text().strip(),
                resp.get("session_expires", ""),
            )
            if self._rem.isChecked():
                save_auth(auth_info)
            else:
                clear_auth()
            self._set_msg("Logged in.", MAIN)
            self._btn.setText("Success")
            payload = dict(resp or {})
            payload["auth"] = auth_info
            QTimer.singleShot(600, lambda p=payload: self.logged_in.emit(p))
        else:
            self._set_msg(msg or "Login failed", MID); self._shake()

    def _set_msg(self, t, c):
        tone = MID if c == MID else MAIN
        self._msg.setText(t); self._msg.setStyleSheet(f"color:{tone};font:600 9pt '{UI_FONT}';border:none;background:transparent;")

    def _shake(self):
        ox = self._pw.pos().x()
        for i, dx in enumerate([8,-8,6,-6,4,-4,0]):
            QTimer.singleShot(i*38, lambda d=dx, o=ox: self._pw.move(o+d, self._pw.pos().y()))

class AdminRequiredDialog(QDialog):
    elevate_requested = pyqtSignal()
    continue_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setObjectName("adminCard")
        card.setStyleSheet(replica_hero_style("#ffffff"))
        root.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(28, 26, 28, 24)
        lay.setSpacing(16)

        title = QLabel("Administrator Required")
        title.setStyleSheet(f"color:{MAIN};font:700 18pt '{TITLE_FONT}';border:none;background:transparent;")
        body = QLabel(
            "Hextra needs administrator privileges to apply system-level tweaks.\n\n"
            "Would you like to restart with elevated permissions?"
        )
        body.setWordWrap(True)
        body.setStyleSheet(f"color:{DIM};font:500 10pt '{UI_FONT}';line-height:20px;border:none;background:transparent;")

        btn_row = QHBoxLayout()
        btn_row.setSpacing(14)
        elevate = QPushButton("Elevate")
        cont = QPushButton("Continue")
        for btn in (elevate, cont):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(40)
            btn.setStyleSheet(
                f"QPushButton{{background:{BG};color:{MAIN};border:1px solid {LINE};border-radius:14px;padding:0 20px;font:600 9pt '{UI_FONT}';}}"
                f"QPushButton:hover{{border-color:{MAIN};}}"
            )
        elevate.clicked.connect(self.elevate_requested.emit)
        cont.clicked.connect(self.continue_requested.emit)
        elevate.clicked.connect(self.accept)
        cont.clicked.connect(self.reject)

        btn_row.addStretch(1)
        btn_row.addWidget(elevate)
        btn_row.addWidget(cont)
        btn_row.addStretch(1)

        lay.addWidget(title)
        lay.addWidget(body)
        lay.addStretch(1)
        lay.addLayout(btn_row)

class UpdateAvailableDialog(QDialog):
    install_requested = pyqtSignal()

    def __init__(self, meta, current_version, parent=None):
        super().__init__(parent)
        self._meta = dict(meta or {})
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        version = str(self._meta.get("version", "") or "new")
        changelog = str(self._meta.get("changelog", "") or "").strip() or "No changelog provided for this build."
        info_bits = [f"Current build: {current_version}", f"New build: {version}"]
        if self._meta.get("size"):
            info_bits.append(f"Download: {_format_bytes(self._meta.get('size'))}")
        if self._meta.get("published"):
            info_bits.append(f"Published: {self._meta.get('published')}")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setStyleSheet(replica_hero_style("#ffffff"))
        root.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(28, 26, 28, 24)
        lay.setSpacing(14)

        title = QLabel("Update Available")
        title.setStyleSheet(f"color:{MAIN};font:700 18pt '{TITLE_FONT}';border:none;background:transparent;")
        subtitle = QLabel("A newer Hextra build is available on your update server.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color:{DIM};font:500 10pt '{UI_FONT}';border:none;background:transparent;")

        info = QLabel("  |  ".join(info_bits))
        info.setWordWrap(True)
        info.setStyleSheet(f"color:{MID};font:500 9pt '{UI_FONT}';border:none;background:transparent;")

        notes_title = QLabel("Changelog")
        notes_title.setStyleSheet(replica_section_caption("#ffffff"))
        notes = QLabel(changelog)
        notes.setWordWrap(True)
        notes.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        notes.setStyleSheet(f"color:{MAIN};font:500 9.5pt '{UI_FONT}';border:none;background:{PANEL};padding:12px;border-radius:10px;")

        btn_row = QHBoxLayout()
        btn_row.setSpacing(14)
        later = QPushButton("Later")
        install = QPushButton("Update Now")
        later.setCursor(Qt.CursorShape.PointingHandCursor)
        install.setCursor(Qt.CursorShape.PointingHandCursor)
        later.setMinimumHeight(40)
        install.setMinimumHeight(40)
        later.setStyleSheet(
            f"QPushButton{{background:{BG};color:{MAIN};border:1px solid {LINE};border-radius:14px;padding:0 20px;font:600 9pt '{UI_FONT}';}}"
            f"QPushButton:hover{{border-color:{MAIN};}}"
        )
        install.setStyleSheet(
            f"QPushButton{{background:{MAIN};color:{BG};border:1px solid {MAIN};border-radius:14px;padding:0 20px;font:700 9pt '{UI_FONT}';}}"
            "QPushButton:hover{background:#ffffff;}"
        )
        later.clicked.connect(self.reject)
        install.clicked.connect(self._accept_update)

        btn_row.addStretch(1)
        btn_row.addWidget(later)
        btn_row.addWidget(install)

        lay.addWidget(title)
        lay.addWidget(subtitle)
        lay.addWidget(info)
        lay.addSpacing(4)
        lay.addWidget(notes_title)
        lay.addWidget(notes)
        lay.addSpacing(6)
        lay.addLayout(btn_row)

    def _accept_update(self):
        self.install_requested.emit()
        self.accept()

class UpdateProgressDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setStyleSheet(replica_hero_style("#ffffff"))
        root.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(28, 26, 28, 24)
        lay.setSpacing(14)

        title = QLabel("Installing Update")
        title.setStyleSheet(f"color:{MAIN};font:700 18pt '{TITLE_FONT}';border:none;background:transparent;")
        self._status = QLabel("Downloading update...")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color:{DIM};font:500 10pt '{UI_FONT}';border:none;background:transparent;")

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(10)
        self._bar.setStyleSheet(
            f"QProgressBar{{background:{PANEL};border:1px solid {LINE};border-radius:5px;}}"
            f"QProgressBar::chunk{{background:{MAIN};border-radius:5px;}}"
        )

        self._detail = QLabel("Hextra will restart once the new build is ready.")
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet(f"color:{MID};font:500 9pt '{UI_FONT}';border:none;background:transparent;")

        self._close_btn = QPushButton("Close")
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setMinimumHeight(40)
        self._close_btn.setVisible(False)
        self._close_btn.setStyleSheet(
            f"QPushButton{{background:{BG};color:{MAIN};border:1px solid {LINE};border-radius:14px;padding:0 20px;font:600 9pt '{UI_FONT}';}}"
            f"QPushButton:hover{{border-color:{MAIN};}}"
        )
        self._close_btn.clicked.connect(self.reject)

        lay.addWidget(title)
        lay.addWidget(self._status)
        lay.addWidget(self._bar)
        lay.addWidget(self._detail)
        lay.addSpacing(4)
        lay.addWidget(self._close_btn, 0, Qt.AlignmentFlag.AlignRight)

    def set_progress(self, done, total, text):
        self._status.setText(text or "Downloading update...")
        if total and total > 0:
            self._bar.setRange(0, int(total))
            self._bar.setValue(min(int(done or 0), int(total)))
            self._detail.setText(f"{_format_bytes(done)} of {_format_bytes(total)}")
        else:
            self._bar.setRange(0, 0)
            self._detail.setText("Preparing the update package...")

    def set_error(self, message):
        self._status.setText("Update failed")
        self._detail.setText(message or "Could not prepare the update.")
        self._bar.setRange(0, 1)
        self._bar.setValue(0)
        self._close_btn.setVisible(True)

    def set_ready(self, message):
        self._status.setText("Restarting Hextra...")
        self._detail.setText(message or "The new build is ready to install.")
        self._bar.setRange(0, 1)
        self._bar.setValue(1)

class _Check(QWidget):
    def __init__(self, label, accent, parent=None):
        super().__init__(parent); lay = QHBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(8)
        self._box = _Dot(accent); self._box.clicked.connect(lambda: None)
        lay.addWidget(self._box); lay.addWidget(_lbl(label, MID, size=11))
    def setChecked(self, v): self._box.setChecked(v)
    def isChecked(self): return self._box.isChecked()

class _Dot(QAbstractButton):
    def __init__(self, accent, parent=None):
        super().__init__(parent); self._ac = QColor(accent); self._on = False; self._hov = False
        self.setFixedSize(16,16); self.setCursor(Qt.CursorShape.PointingHandCursor); self.clicked.connect(self._flip)
    def setChecked(self, v): self._on = v; self.update()
    def isChecked(self): return self._on
    def _flip(self): self._on = not self._on; self.update()
    def enterEvent(self, _): self._hov = True; self.update()
    def leaveEvent(self, _): self._hov = False; self.update()
    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1,1,14,14)
        if self._on:
            p.setBrush(QBrush(QColor("#f5f7fa"))); p.setPen(QPen(QColor("#f5f7fa"), 1.0)); p.drawRoundedRect(r,3,3)
            p.setPen(QPen(QColor("#0d1117"), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath(); path.moveTo(3.5,8.0); path.lineTo(6.5,11.5); path.lineTo(12.5,4.5)
            p.drawPath(path)
        else:
            p.setBrush(QBrush(QColor(BG))); p.setPen(QPen(QColor(REPLICA["line"] if self._hov else LINE), 1.2)); p.drawRoundedRect(r,3,3)
        p.end()

class CornerGrip(QWidget):
    def __init__(self, get_accent, parent=None):
        super().__init__(parent)
        self._get_accent = get_accent
        self.setFixedSize(18, 18)
        self.setStyleSheet("background:transparent;")
        self.setToolTip("resize")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = QColor(self._get_accent()) if callable(self._get_accent) else QColor(MID)
        muted = QColor(LINE).lighter(135)
        for i, offset in enumerate((0, 4, 8)):
            color = accent if i == 0 else muted
            p.setPen(QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(self.width() - 3 - offset, self.height() - 9, self.width() - 9, self.height() - 3 - offset)
        p.end()

# main window
class Hextra(QWidget):
    EDGE = 5; MW, MH = 820, 560

    def __init__(self):
        super().__init__()
        d = load_data()
        self._accent = d.get("color", "#e60000")
        self._rainbow = (self._accent == "rainbow")
        self._hue = 0.58 if self._rainbow else 0.0; self._move_drag = None; self._resize = None
        self._dash_ready = False
        self._native_frame = _use_native_window_frame()
        self._update_check_worker = None
        self._update_download_worker = None
        self._update_prompt = None
        self._update_progress = None
        self._ignored_update_version = ""
        self._manual_update_feedback = False
        self._force_update_prompt = False
        self._saved_auth_worker = None

        if self._native_frame:
            self.setWindowFlags(Qt.WindowType.Window)
            self.setWindowTitle("Hextra")
        else:
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
            self.setMouseTracking(True)
        self.setStyleSheet(f"background:{BG};")
        self.resize(980, 640); self.setMinimumSize(self.MW, self.MH)

        self._stack = QStackedWidget(self)
        self._login = LoginPage(self._accent if not self._rainbow else "#e60000")
        self._login.logged_in.connect(self._on_logged_in)
        self._login.success.connect(self._go_dash)
        self._login.theme_changed.connect(self._set_accent)
        self._login.motd_received.connect(self._show_motd_on_window)
        self._stack.addWidget(self._login)
        self._dash = None
        self._corner_grip = CornerGrip(self._get_display_color, self)
        self._corner_grip.setVisible(not self._native_frame)
        self._corner_grip.raise_()
        self._update_stack_geo()
        self._pending_motd = ""
        self._pending_login_payload = {}

        self._timer = QTimer(); self._timer.timeout.connect(self._tick); self._timer.start(60)

        self._last_motd = ""
        self._motd_poll = QTimer(); self._motd_poll.timeout.connect(self._poll_motd); self._motd_poll.start(60000)
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._check_for_updates)
        self._update_timer.start(UPDATE_CHECK_INTERVAL_MS)
        QTimer.singleShot(0, self._preload_dash)
        QTimer.singleShot(0, self._apply_window_corners)
        QTimer.singleShot(250, self._try_saved_session)
        QTimer.singleShot(3500, self._check_for_updates)

    def _current_accent(self):
        return self._get_display_color() if self._rainbow else self._accent

    def showEvent(self, e):
        super().showEvent(e)
        self._apply_window_corners()

    def _update_stack_geo(self):
        if self._native_frame:
            self._stack.setGeometry(0, 0, self.width(), self.height())
            self._corner_grip.hide()
            return
        E = self.EDGE
        self._stack.setGeometry(E, E, self.width()-E*2, self.height()-E*2)
        g = 18
        self._corner_grip.setGeometry(self.width() - g - 3, self.height() - g - 3, g, g)
        self._corner_grip.show()
        self._corner_grip.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_stack_geo()
        self._apply_window_corners()

    def _apply_window_corners(self):
        if os.name != "nt" or self._native_frame:
            return
        try:
            hwnd = int(self.winId())
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            DWMWCP_ROUND = 2
            value = ctypes.c_int(DWMWCP_ROUND)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
        except Exception:
            pass

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(BG))
        p.end()

    def _get_display_color(self):
        if self._rainbow: return QColor.fromHslF(self._hue, 0.85, 0.58).name()
        return self._accent

    def _edge_flags(self, pos):
        x, y, w, h = pos.x(), pos.y(), self.width(), self.height(); e = self.EDGE
        return x < e, x > w-e, y < e, y > h-e

    def _cursor_for(self, L, R, T, B):
        if (L and T) or (R and B): return Qt.CursorShape.SizeFDiagCursor
        if (R and T) or (L and B): return Qt.CursorShape.SizeBDiagCursor
        if L or R: return Qt.CursorShape.SizeHorCursor
        if T or B: return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def mousePressEvent(self, e):
        if self._native_frame:
            return super().mousePressEvent(e)
        if e.button() != Qt.MouseButton.LeftButton: return
        pos = e.position().toPoint(); flags = self._edge_flags(pos)
        if any(flags): self._resize = (flags, e.globalPosition().toPoint(), self.geometry()); self._move_drag = None; self.setCursor(self._cursor_for(*flags))
        elif pos.y() < 52: self._move_drag = e.globalPosition().toPoint(); self._resize = None

    def mouseMoveEvent(self, e):
        if self._native_frame:
            return super().mouseMoveEvent(e)
        pos = e.position().toPoint()
        if self._resize is None and self._move_drag is None:
            return
        if self._move_drag is not None and e.buttons() == Qt.MouseButton.LeftButton:
            d = e.globalPosition().toPoint() - self._move_drag
            self.move(self.x()+d.x(), self.y()+d.y()); self._move_drag = e.globalPosition().toPoint()
            return
        if self._resize is not None and e.buttons() == Qt.MouseButton.LeftButton:
            (L,R,T,B), start, g = self._resize
            dx = int(e.globalPosition().x()-start.x()); dy = int(e.globalPosition().y()-start.y())
            x,y,w,h = g.x(),g.y(),g.width(),g.height()
            if L: nw=max(self.MW,w-dx); x+=w-nw; w=nw
            if R: w=max(self.MW,w+dx)
            if T: nh=max(self.MH,h-dy); y+=h-nh; h=nh
            if B: h=max(self.MH,h+dy)
            self.setGeometry(x,y,w,h)

    def mouseReleaseEvent(self, e):
        if self._native_frame:
            return super().mouseReleaseEvent(e)
        self._resize=None; self._move_drag=None; self.unsetCursor()

    def _go_dash(self):
        self._preload_dash()
        if self._stack.currentWidget() is self._dash:
            return
        self.setWindowOpacity(1.0)
        self._stack.setCurrentWidget(self._dash)

    def _preload_dash(self):
        if self._dash_ready:
            return
        if self._dash is None:
            self._dash = Dashboard(self._current_accent, self._set_accent, self)
            self._stack.addWidget(self._dash)
        self._dash_ready = True

    def _on_logged_in(self, payload):
        payload = dict(payload or {})
        self._pending_login_payload = payload
        self._pending_motd = payload.get("motd", "") or ""
        self._continue_after_login()

    def _try_saved_session(self):
        if self._stack.currentWidget() is not self._login:
            return
        auth = load_auth()
        if auth.get("mode") != "account" or not auth.get("username") or not auth.get("session_token"):
            return
        self._login._set_msg("Checking saved session...", MID)
        self._saved_auth_worker = AccountStatusWorker(auth)
        self._saved_auth_worker.result.connect(lambda _text, _tone, resp, a=dict(auth): self._on_saved_session_result(a, resp))
        self._saved_auth_worker.finished.connect(lambda: setattr(self, "_saved_auth_worker", None))
        self._saved_auth_worker.start()

    def _on_saved_session_result(self, auth, resp):
        if isinstance(resp, dict) and resp.get("success"):
            payload = dict(resp)
            payload["auth"] = dict(auth)
            self._on_logged_in(payload)
            return
        clear_auth()
        message = "Saved session expired. Sign in again."
        if isinstance(resp, dict) and resp.get("message"):
            message = str(resp.get("message"))
        self._login._set_msg(message, MID)

    def _continue_after_login(self):
        self._go_dash()
        if self._dash:
            auth = self._pending_login_payload.get("auth")
            if isinstance(auth, dict):
                self._dash._auth = dict(auth)
            self._dash._account.set_session(self._dash._auth if self._dash else {}, self._pending_login_payload)
            self._dash._refresh_license_badge()
        self._last_motd = self._pending_motd
        if self._pending_motd and self._pending_motd.strip():
            QTimer.singleShot(1200, lambda: self._show_motd_on_window(self._pending_motd.strip()))
        self._pending_motd = ""
        self._pending_login_payload = {}

    def _show_centered_dialog(self, dialog):
        if dialog is None:
            return
        dialog.adjustSize()
        base = self.frameGeometry() if self.isVisible() else self.geometry()
        target = base.center() - dialog.rect().center()
        dialog.move(target)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _set_settings_update_status(self, text, color=MID):
        try:
            if self._dash and getattr(self._dash, "_settings", None):
                self._dash._settings.set_update_status(text, color)
        except Exception:
            pass

    def trigger_update_check(self, manual=False):
        if manual:
            if not _can_self_update():
                self._set_settings_update_status("Auto update only works inside the built Windows EXE.", MID)
                return
            if self._update_progress is not None and self._update_progress.isVisible():
                self._set_settings_update_status("An update is already being installed.", MID)
                self._show_centered_dialog(self._update_progress)
                return
            if self._update_prompt is not None and self._update_prompt.isVisible():
                self._set_settings_update_status("An update prompt is already open.", MID)
                self._show_centered_dialog(self._update_prompt)
                return
            if self._update_check_worker is not None:
                self._set_settings_update_status("Already checking for updates...", MID)
                return
            self._ignored_update_version = ""
            self._manual_update_feedback = True
            self._check_for_updates(force=True)
            return
        self._check_for_updates(force=False)

    def _check_for_updates(self, force=False):
        if not _can_self_update():
            return
        if self._update_check_worker is not None or self._update_download_worker is not None:
            return
        self._force_update_prompt = bool(force)
        auth = dict(getattr(self._dash, "_auth", {}) or load_auth())
        worker = UpdateCheckWorker(_current_version(), auth)
        worker.result.connect(self._on_update_check_result)
        worker.finished.connect(self._on_update_check_finished)
        self._update_check_worker = worker
        worker.start()

    def _on_update_check_finished(self):
        self._update_check_worker = None

    def _on_update_check_result(self, resp):
        manual = self._manual_update_feedback
        force = self._force_update_prompt
        self._manual_update_feedback = False
        self._force_update_prompt = False
        if not isinstance(resp, dict) or not resp:
            if manual:
                self._set_settings_update_status("Could not reach the update server right now.", MID)
            return
        if not resp.get("update"):
            if manual:
                self._set_settings_update_status(f"You're already on the latest build ({_current_version()}).", MID)
            return
        version = str(resp.get("version", "") or "").strip()
        if not version or _version_tuple(version) <= _version_tuple(_current_version()):
            if manual:
                self._set_settings_update_status(f"You're already on the latest build ({_current_version()}).", MID)
            return
        if not force and version == self._ignored_update_version:
            return
        if self._update_prompt and self._update_prompt.isVisible():
            return
        if self._update_progress and self._update_progress.isVisible():
            return
        if manual:
            self._set_settings_update_status(f"Update {version} is available.", MAIN)
        dlg = UpdateAvailableDialog(resp, _current_version(), self)
        dlg.rejected.connect(lambda v=version: setattr(self, "_ignored_update_version", v))
        dlg.install_requested.connect(lambda meta=dict(resp): self._start_update_download(meta))
        dlg.finished.connect(lambda _=0: self._on_update_prompt_closed())
        self._update_prompt = dlg
        self._show_centered_dialog(dlg)

    def _on_update_prompt_closed(self):
        self._update_prompt = None

    def _start_update_download(self, meta):
        if self._update_download_worker is not None:
            return
        if self._update_prompt is not None:
            try:
                self._update_prompt.close()
            except Exception:
                pass
            self._update_prompt = None
        progress = UpdateProgressDialog(self)
        progress.finished.connect(lambda _=0: setattr(self, "_update_progress", None))
        self._update_progress = progress
        self._set_settings_update_status("Downloading update...", MID)
        self._show_centered_dialog(progress)
        auth = dict(getattr(self._dash, "_auth", {}) or load_auth())
        worker = UpdateDownloadWorker(meta, auth)
        worker.progress.connect(self._on_update_download_progress)
        worker.result.connect(self._on_update_download_result)
        worker.finished.connect(self._on_update_download_finished)
        self._update_download_worker = worker
        worker.start()

    def _on_update_download_progress(self, done, total, text):
        if self._update_progress is not None:
            self._update_progress.set_progress(done, total, text)
        if total and total > 0:
            self._set_settings_update_status(f"Downloading update... {_format_bytes(done)} / {_format_bytes(total)}", MID)
        else:
            self._set_settings_update_status(text or "Downloading update...", MID)

    def _on_update_download_result(self, ok, message, payload):
        if self._update_progress is None:
            return
        if not ok:
            self._update_progress.set_error(message)
            self._set_settings_update_status(message or "Could not prepare the update.", MID)
            return
        helper_path = payload.get("helper_path", "")
        launch_ok, launch_msg = _launch_update_helper(helper_path)
        if not launch_ok:
            _delete_path_quietly(helper_path)
            _delete_path_quietly(payload.get("download_path", ""))
            self._update_progress.set_error(launch_msg)
            self._set_settings_update_status(launch_msg, MID)
            return
        self._update_progress.set_ready(launch_msg)
        self._set_settings_update_status("Restarting into the new build...", MAIN)
        QTimer.singleShot(500, QApplication.instance().quit)

    def _on_update_download_finished(self):
        self._update_download_worker = None

    def _restart_elevated(self):
        if _launch_elevated_instance():
            self.close()

    def _poll_motd(self):
        if not self._dash or self._stack.currentWidget() is not self._dash:
            return
        worker = _MotdPollWorker()
        worker.result.connect(self._on_poll_result)
        worker.start()
        self._poll_worker = worker

    def _on_poll_result(self, motd):
        if not motd or not motd.strip():
            return
        if motd.strip() != self._last_motd.strip():
            self._last_motd = motd.strip()
            self._show_motd_on_window(motd.strip())

    def _show_motd_on_window(self, motd):
        ac = self._accent if not self._rainbow else "#ffffff"
        self._motd_overlay = MotdOverlay(motd, ac, self)
        self._motd_overlay.setGeometry(0, 0, self.width(), self.height())
        self._motd_overlay.show()
        self._motd_overlay.raise_()

    def _set_accent(self, color):
        self._rainbow = (color == "rainbow")
        if self._rainbow:
            self._hue = 0.58
        else:
            self._accent = color
        d = load_data(); d["color"] = color; save_data(d)
        live = self._current_accent()
        if hasattr(self, "_login") and self._login:
            self._login.set_theme_state(color, live)
        if hasattr(self, "_dash") and self._dash:
            self._dash.sync_accent(live)

    def _tick(self):
        self._hue = (self._hue + 0.006) % 1.0
        self._corner_grip.update()
        cur = self._stack.currentWidget()

        if self._rainbow:
            color = QColor.fromHslF(self._hue, 0.85, 0.58).name()
            if cur is self._login:
                self._login.set_logo_color(color)
                self._login.tick()
            elif self._dash and cur is self._dash:
                self._dash.set_title_color(color)
                self._dash._sidebar.set_accent(color)
                visible = self._dash._stack.currentWidget()
                if hasattr(visible, "update_accent"):
                    visible.update_accent(color)
                if hasattr(visible, '_sw'):
                    for sw in visible._sw: sw.set_accent(color); sw.tick()
                if visible is self._dash._settings:
                    self._dash._settings.update_accent_rainbow(color)
                if visible is self._dash._home:
                    self._dash._home.update_accent(color)
                self._dash.tick()
        else:
            if cur is self._login:
                self._login.tick()
            elif self._dash and cur is self._dash:
                visible = self._dash._stack.currentWidget()
                if hasattr(visible, '_sw'):
                    for sw in visible._sw: sw.tick()
                self._dash.tick()

def main():
    if "--smoke-test" in sys.argv and "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    _start_update_cleanup_thread(_PENDING_UPDATE_CLEANUP)
    app = QApplication(sys.argv); app.setStyle("Fusion")
    pixel_font_family = _load_pixel_font()
    pixel_font = QFont(pixel_font_family)
    pixel_font.setPointSize(10)
    app.setFont(pixel_font)
    app.setStyleSheet("QToolTip{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 rgba(255,255,255,24),stop:0.18 rgba(28,33,44,236),stop:1 rgba(20,24,34,246));color:#eef4ff;border:1px solid rgba(185,205,240,72);border-radius:10px;padding:7px 11px;font:600 10px 'Segoe UI';}")
    app_icon = _load_app_icon()
    app.setWindowIcon(app_icon)
    window = Hextra(); window.setWindowIcon(app_icon); window.show()
    _apply_native_window_icon(window, app_icon)
    QTimer.singleShot(0, lambda: _apply_native_window_icon(window, app_icon))
    return app.exec()

if __name__ == "__main__":
    try:
        _boot()
        _ensure_elevated_start()
        sys.exit(main())
    except Exception:
        traceback.print_exc(); input("\nThe application crashed. Press Enter to exit.")
