import hashlib
import json
import os
import queue
import ssl
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from tkinter import Tk, StringVar, BooleanVar, filedialog, messagebox
import tkinter as tk
from tkinter import ttk
import urllib.error
import urllib.request


APP_NAME = "Hextra Bootstrapper"
API_BASE_URL = "https://oltrski.de"
DOWNLOAD_CHUNK_SIZE = 1024 * 256
LOGIN_TIMEOUT = 12
CHECK_TIMEOUT = 12
DOWNLOAD_TIMEOUT = 90


def _current_hwid():
    return str(uuid.getnode())


def _safe_update_filename(name):
    raw = Path(str(name or "")).name.strip()
    if not raw:
        raw = "Hextra.exe"
    safe = []
    for char in raw:
        if char.isalnum() or char in "._ -":
            safe.append(char)
        else:
            safe.append("_")
    raw = "".join(safe).strip()
    if not raw:
        raw = "Hextra.exe"
    if not Path(raw).suffix:
        raw += ".exe"
    return raw


def _safe_version_tag(version):
    raw = str(version or "").strip()
    if not raw:
        return "latest"
    safe = []
    for char in raw:
        if char.isalnum() or char in "._-":
            safe.append(char)
        else:
            safe.append("-")
    cleaned = "".join(safe).strip("-._ ")
    return cleaned or "latest"


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(DOWNLOAD_CHUNK_SIZE), b""):
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _format_bytes(num_bytes):
    size = float(num_bytes or 0)
    units = ["B", "KB", "MB", "GB"]
    idx = 0
    while size >= 1024.0 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(size)} {units[idx]}"
    return f"{size:.1f} {units[idx]}"


def _preferred_output_dir():
    exe_dir = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    try:
        exe_dir.mkdir(parents=True, exist_ok=True)
        probe = exe_dir / ".hextra_bootstrapper_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return exe_dir
    except Exception:
        pass
    downloads = Path.home() / "Downloads" / "Hextra"
    downloads.mkdir(parents=True, exist_ok=True)
    return downloads


def _api_headers(username="", session_token=""):
    headers = {
        "Accept": "application/json",
    }
    username = (username or "").strip()
    session_token = (session_token or "").strip()
    if username and session_token:
        headers["Authorization"] = f"Bearer {session_token}"
        headers["X-Hextra-User"] = username
        headers["X-Hextra-HWID"] = _current_hwid()
    return headers


def _json_request(path, *, method="GET", payload=None, timeout=10, username="", session_token=""):
    url = f"{API_BASE_URL}{path}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    for key, value in _api_headers(username=username, session_token=session_token).items():
        request.add_header(key, value)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ctx) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:
            return {"success": False, "message": f"Server returned HTTP {exc.code}"}
    except Exception as exc:
        return {"success": False, "message": f"Server not reachable: {exc}"}


def _build_output_path(download_dir, filename, version):
    target_dir = Path(download_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_update_filename(filename)
    stem = Path(safe_name).stem
    suffix = Path(safe_name).suffix or ".exe"
    version_tag = _safe_version_tag(version)
    final_name = f"{stem}-{version_tag}{suffix}"
    return target_dir / final_name


def _download_latest_build(username, password, download_dir, *, progress=None):
    username = (username or "").strip()
    password = password or ""
    if not username or not password:
        return {"success": False, "message": "Bitte Benutzername und Passwort eingeben."}

    login = _json_request(
        "/client/login",
        method="POST",
        timeout=LOGIN_TIMEOUT,
        payload={
            "username": username,
            "password": password,
            "remember": False,
            "hwid": _current_hwid(),
        },
    )
    if not login.get("success") or not login.get("session_token"):
        return {"success": False, "message": login.get("message", "Login fehlgeschlagen.")}

    session_token = login.get("session_token", "")
    meta = _json_request(
        "/update/check?v=0.0.0",
        method="GET",
        timeout=CHECK_TIMEOUT,
        username=username,
        session_token=session_token,
    )
    if not isinstance(meta, dict):
        return {"success": False, "message": "Update-Antwort war ungueltig."}
    if meta.get("success") is False and meta.get("message"):
        return {"success": False, "message": meta.get("message", "Update-Check fehlgeschlagen.")}
    if not meta.get("filename"):
        return {"success": False, "message": "Auf dem Server ist kein Build hinterlegt."}

    version = str(meta.get("version") or "latest").strip() or "latest"
    output_path = _build_output_path(download_dir, meta.get("filename"), version)
    temp_path = output_path.with_suffix(output_path.suffix + ".part")
    if temp_path.exists():
        temp_path.unlink(missing_ok=True)

    request = urllib.request.Request(f"{API_BASE_URL}/update/download", method="GET")
    for key, value in _api_headers(username=username, session_token=session_token).items():
        request.add_header(key, value)

    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT, context=ctx) as response:
            total = 0
            try:
                total = int(response.headers.get("Content-Length", "0") or 0)
            except Exception:
                total = 0
            transferred = 0
            with open(temp_path, "wb") as handle:
                while True:
                    chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    transferred += len(chunk)
                    if progress:
                        progress(transferred, total)
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
            return {"success": False, "message": body.get("message", f"Download fehlgeschlagen (HTTP {exc.code}).")}
        except Exception:
            return {"success": False, "message": f"Download fehlgeschlagen (HTTP {exc.code})."}
    except Exception as exc:
        return {"success": False, "message": f"Download fehlgeschlagen: {exc}"}

    expected_size = 0
    try:
        expected_size = int(meta.get("size") or 0)
    except Exception:
        expected_size = 0
    actual_size = temp_path.stat().st_size if temp_path.exists() else 0
    if expected_size and actual_size and expected_size != actual_size:
        temp_path.unlink(missing_ok=True)
        return {"success": False, "message": "Dateigroesse stimmt nicht mit dem Server-Build ueberein."}

    expected_checksum = str(meta.get("checksum") or "").strip().lower()
    if expected_checksum:
        actual_checksum = _sha256_file(temp_path).lower()
        if actual_checksum != expected_checksum:
            temp_path.unlink(missing_ok=True)
            return {"success": False, "message": "Checksum-Pruefung fehlgeschlagen."}

    if output_path.exists():
        output_path.unlink(missing_ok=True)
    temp_path.replace(output_path)

    return {
        "success": True,
        "message": f"Version {version} wurde heruntergeladen.",
        "path": str(output_path),
        "folder": str(output_path.parent),
        "filename": output_path.name,
        "version": version,
        "size": actual_size,
    }


class BootstrapperApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("560x350")
        self.root.minsize(520, 320)
        self.root.configure(bg="#0f1115")

        self.username_var = StringVar()
        self.password_var = StringVar()
        self.download_dir_var = StringVar(value=str(_preferred_output_dir()))
        self.auto_launch_var = BooleanVar(value=True)
        self.status_var = StringVar(value="Bereit. Mit Login wird immer die neueste Version direkt vom Server geholt.")
        self.progress_text_var = StringVar(value="Noch kein Download gestartet.")
        self.last_download_path = ""
        self.worker = None
        self.events = queue.Queue()

        self._build_ui()
        self.root.after(120, self._poll_events)

    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Hextra.TFrame", background="#0f1115")
        style.configure("Card.TFrame", background="#171a21")
        style.configure("Hextra.TLabel", background="#0f1115", foreground="#f2f4f8", font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background="#0f1115", foreground="#9da4b5", font=("Segoe UI", 9))
        style.configure("CardTitle.TLabel", background="#171a21", foreground="#f7f8fb", font=("Segoe UI Semibold", 18))
        style.configure("Hextra.TButton", font=("Segoe UI", 10))
        style.configure("Hextra.Horizontal.TProgressbar", troughcolor="#1a1e27", background="#f2f4f8", bordercolor="#1a1e27", lightcolor="#f2f4f8", darkcolor="#f2f4f8")

        outer = ttk.Frame(self.root, style="Hextra.TFrame", padding=18)
        outer.pack(fill="both", expand=True)

        card = ttk.Frame(outer, style="Card.TFrame", padding=18)
        card.pack(fill="both", expand=True)

        ttk.Label(card, text="Hextra Bootstrapper", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(card, text="Loggt sich normal am Backend ein, zieht die neueste EXE und prueft sie vor dem Start.", style="Muted.TLabel").pack(anchor="w", pady=(4, 14))

        grid = ttk.Frame(card, style="Card.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)

        ttk.Label(grid, text="Benutzername", style="Hextra.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.username_entry = ttk.Entry(grid, textvariable=self.username_var)
        self.username_entry.grid(row=0, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(grid, text="Passwort", style="Hextra.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 8))
        self.password_entry = ttk.Entry(grid, textvariable=self.password_var, show="*")
        self.password_entry.grid(row=1, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(grid, text="Zielordner", style="Hextra.TLabel").grid(row=2, column=0, sticky="w", pady=(0, 8))
        self.folder_entry = ttk.Entry(grid, textvariable=self.download_dir_var)
        self.folder_entry.grid(row=2, column=1, sticky="ew", pady=(0, 8))
        browse_btn = ttk.Button(grid, text="Ordner...", command=self._browse_folder, style="Hextra.TButton")
        browse_btn.grid(row=2, column=2, padx=(8, 0), pady=(0, 8))

        auto_launch = ttk.Checkbutton(card, text="Nach erfolgreichem Download direkt starten", variable=self.auto_launch_var)
        auto_launch.pack(anchor="w", pady=(10, 12))

        btn_row = ttk.Frame(card, style="Card.TFrame")
        btn_row.pack(fill="x")
        self.download_btn = ttk.Button(btn_row, text="Neueste Version holen", command=self._start_download, style="Hextra.TButton")
        self.download_btn.pack(side="left")
        self.open_btn = ttk.Button(btn_row, text="Ordner oeffnen", command=self._open_folder, style="Hextra.TButton")
        self.open_btn.pack(side="left", padx=(8, 0))
        self.launch_btn = ttk.Button(btn_row, text="Datei starten", command=self._launch_download, style="Hextra.TButton")
        self.launch_btn.pack(side="left", padx=(8, 0))
        self.open_btn.state(["disabled"])
        self.launch_btn.state(["disabled"])

        self.progress = ttk.Progressbar(card, style="Hextra.Horizontal.TProgressbar", mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(16, 8))
        ttk.Label(card, textvariable=self.progress_text_var, style="Muted.TLabel").pack(anchor="w")
        ttk.Label(card, textvariable=self.status_var, style="Hextra.TLabel", wraplength=500, justify="left").pack(anchor="w", pady=(14, 0))

        self.username_entry.focus_set()

    def _browse_folder(self):
        selected = filedialog.askdirectory(initialdir=self.download_dir_var.get() or str(_preferred_output_dir()))
        if selected:
            self.download_dir_var.set(selected)

    def _set_busy(self, busy):
        if busy:
            self.download_btn.state(["disabled"])
        else:
            self.download_btn.state(["!disabled"])

    def _start_download(self):
        if self.worker and self.worker.is_alive():
            return
        username = self.username_var.get().strip()
        password = self.password_var.get()
        download_dir = self.download_dir_var.get().strip()
        if not username or not password:
            messagebox.showerror(APP_NAME, "Bitte Benutzername und Passwort eingeben.")
            return
        if not download_dir:
            messagebox.showerror(APP_NAME, "Bitte einen Zielordner auswaehlen.")
            return

        self.progress["value"] = 0
        self.progress_text_var.set("Verbinde mit dem Server...")
        self.status_var.set("Anmeldung laeuft...")
        self.last_download_path = ""
        self.open_btn.state(["disabled"])
        self.launch_btn.state(["disabled"])
        self._set_busy(True)

        def worker():
            def on_progress(done, total):
                self.events.put(("progress", done, total))

            result = _download_latest_build(username, password, download_dir, progress=on_progress)
            self.events.put(("done", result))

        self.worker = threading.Thread(target=worker, name="hextra-bootstrapper-download", daemon=True)
        self.worker.start()

    def _poll_events(self):
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "progress":
                done, total = event[1], event[2]
                if total > 0:
                    percent = max(0, min(100, int((done / total) * 100)))
                    self.progress["value"] = percent
                    self.progress_text_var.set(f"Download: {_format_bytes(done)} / {_format_bytes(total)} ({percent}%)")
                else:
                    self.progress.step(2)
                    self.progress_text_var.set(f"Download: {_format_bytes(done)}")
            elif kind == "done":
                result = event[1]
                self._set_busy(False)
                if result.get("success"):
                    self.progress["value"] = 100
                    self.last_download_path = result.get("path", "")
                    self.status_var.set(
                        f"{result.get('message', 'Download erfolgreich.')} Datei: {result.get('filename', '')}"
                    )
                    self.progress_text_var.set(
                        f"Gespeichert nach {result.get('folder', '')} ({_format_bytes(result.get('size', 0))})"
                    )
                    self.open_btn.state(["!disabled"])
                    self.launch_btn.state(["!disabled"])
                    if self.auto_launch_var.get():
                        self._launch_download()
                else:
                    self.progress["value"] = 0
                    self.status_var.set(result.get("message", "Download fehlgeschlagen."))
                    self.progress_text_var.set("Kein gueltiger Build geladen.")
                    messagebox.showerror(APP_NAME, result.get("message", "Download fehlgeschlagen."))
        self.root.after(120, self._poll_events)

    def _open_folder(self):
        if not self.last_download_path:
            return
        folder = Path(self.last_download_path).parent
        try:
            os.startfile(str(folder))
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Ordner konnte nicht geoeffnet werden: {exc}")

    def _launch_download(self):
        if not self.last_download_path:
            return
        path = Path(self.last_download_path)
        if not path.exists():
            messagebox.showerror(APP_NAME, "Die heruntergeladene Datei wurde nicht gefunden.")
            return
        try:
            os.startfile(str(path))
        except AttributeError:
            subprocess.Popen([str(path)], close_fds=True)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Datei konnte nicht gestartet werden: {exc}")


def main():
    root = Tk()
    app = BootstrapperApp(root)
    root.mainloop()
    return app


if __name__ == "__main__":
    main()
