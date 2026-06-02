import sys
import os
import psutil
import platform
import time
from datetime import datetime

from PyQt6.QtCore import Qt, QSize, pyqtSignal, QTimer, QUrl, QThread
from PyQt6.QtGui import QIcon, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QSizePolicy,
)

from qfluentwidgets import (
    FluentWindow, NavigationItemPosition, FluentIcon as FIF,
    TitleLabel, SubtitleLabel, BodyLabel, CaptionLabel,
    CardWidget, ElevatedCardWidget, SwitchButton, ProgressRing,
    PrimaryPushButton, PushButton, InfoBar, InfoBarPosition,
    MessageBox, LineEdit, PasswordLineEdit, ScrollArea,
    SmoothScrollArea, ToggleButton, setThemeColor, Theme, setTheme
)

# Import business logic from legacy
from .legacy import (
    _boot, _ensure_elevated_start, load_data, save_data,
    load_auth, save_auth, client_login, client_status, account_has_active_plan,
    stable_cpu_percent, gpu_percent, hardware_recommendations,
    recommended_tweak_entries, category_entries, tweak_status,
    TweakWorker, set_selected_tweaks, save_tweak_snapshot,
    load_selected_tweaks, all_tweak_entries, snapshot_entries,
    has_restore_point, create_restore_point,
    load_profiles, save_profile, delete_profile, builtin_presets,
    load_builtin_preset, quick_tool_entries, run_cmd,
    append_activity, start_gpu_sampler, detect_games,
    GAME_TAB_KEYS, _RamCleanerWorker, REVERT_CMDS, CATEGORY_ORDER,
    _dedupe_tweaks, _save_named_block, SNAPSHOTS_KEY,
    AccountLoginWorker
)

class HextraLoginWindow(QWidget):
    logged_in = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self.setWindowTitle("Hextra - Login")
        self.resize(560, 520)
        self.setMinimumSize(480, 460)
        self.setObjectName("LoginRoot")

        host = QVBoxLayout(self)
        host.setContentsMargins(28, 28, 28, 28)
        host.setSpacing(18)
        host.setAlignment(Qt.AlignmentFlag.AlignCenter)

        top = QVBoxLayout()
        top.setSpacing(4)
        top.setAlignment(Qt.AlignmentFlag.AlignCenter)
        app_name = TitleLabel("Hextra")
        app_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        app_subtitle = CaptionLabel("Performance tuning for Windows")
        app_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(app_name)
        top.addWidget(app_subtitle)
        host.addLayout(top)

        card = CardWidget(self)
        card.setFixedWidth(430)
        form_ly = QVBoxLayout(card)
        form_ly.setContentsMargins(28, 24, 28, 24)
        form_ly.setSpacing(14)

        h1 = SubtitleLabel("Local mode", card)
        p1 = BodyLabel("Hextra runs locally with all tweak features available offline.")
        p1.setWordWrap(True)
        form_ly.addWidget(h1)
        form_ly.addWidget(p1)

        self.user_inp = LineEdit(card)
        self.user_inp.setPlaceholderText("Username")
        self.pass_inp = PasswordLineEdit(card)
        self.pass_inp.setPlaceholderText("Password")
        form_ly.addWidget(self.user_inp)
        form_ly.addWidget(self.pass_inp)

        self.remember = SwitchButton("Keep me signed in", card)
        self.remember.setChecked(True)
        form_ly.addWidget(self.remember)

        self.btn = PrimaryPushButton("Sign In", card)
        self.btn.setFixedHeight(36)
        self.btn.clicked.connect(self._do_login)
        form_ly.addWidget(self.btn)

        self.err = CaptionLabel("", card)
        self.err.setObjectName("loginError")
        self.err.setWordWrap(True)
        form_ly.addWidget(self.err)
        host.addWidget(card, 0, Qt.AlignmentFlag.AlignCenter)

        self.setStyleSheet(
            """
            #LoginRoot{ background:#202020; }
            #loginError{ color:#cf6679; }
            """
        )

        self.user_inp.returnPressed.connect(self.pass_inp.setFocus)
        self.pass_inp.returnPressed.connect(self._do_login)

    def _set_busy(self, busy: bool):
        self.btn.setEnabled(not busy)
        self.user_inp.setEnabled(not busy)
        self.pass_inp.setEnabled(not busy)
        self.remember.setEnabled(not busy)
        self.btn.setText("Signing in..." if busy else "Sign In")

    def _do_login(self):
        u = self.user_inp.text().strip()
        if not u:
            self.err.setText("Please enter a local display name.")
            return
        self.logged_in.emit(load_auth())

    def _on_login_done(self, ok: bool, msg: str, resp: dict):
        self._set_busy(False)
        if not ok:
            self.err.setText(msg or "Login failed.")
            return
        token = (resp or {}).get("session_token") or ""
        if not token:
            self.err.setText("Login succeeded but no session token was returned.")
            return
        auth = {
            "mode": "account",
            "username": (resp.get("username") or self.user_inp.text().strip()),
            "email": (resp.get("email") or "").strip(),
            "session_token": token,
            "session_expires": (resp.get("session_expires") or "").strip(),
        }
        if not save_auth(auth):
            self.err.setText("Logged in, but could not save session locally.")
            return
        self.logged_in.emit(dict(auth))


class _RingCard(CardWidget):
    """A card wrapping a ProgressRing with a label and value readout."""
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setFixedSize(160, 190)
        ly = QVBoxLayout(self)
        ly.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ly.setSpacing(8)

        self.ring = ProgressRing(self)
        self.ring.setFixedSize(100, 100)
        self.ring.setStrokeWidth(8)
        self.ring.setTextVisible(False)
        ly.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignCenter)

        self.value_label = SubtitleLabel("0%")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ly.addWidget(self.value_label)

        self.title_label = CaptionLabel(title)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ly.addWidget(self.title_label)

    def set_value(self, pct):
        self.ring.setValue(int(pct))
        self.value_label.setText(f"{pct:.1f}%")


class _InfoRow(QWidget):
    """A simple key : value row for info cards."""
    def __init__(self, key, value, parent=None):
        super().__init__(parent)
        ly = QHBoxLayout(self)
        ly.setContentsMargins(0, 2, 0, 2)
        k = CaptionLabel(key)
        k.setFixedWidth(90)
        self.val = BodyLabel(str(value))
        ly.addWidget(k)
        ly.addWidget(self.val, 1)


class OverviewInterface(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("OverviewInterface")
        self.setWidgetResizable(True)

        self.view = QWidget()
        self.setWidget(self.view)

        root = QVBoxLayout(self.view)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(20)

        # ── Header ──
        self.title = TitleLabel("System Overview")
        root.addWidget(self.title)
        self.subtitle = BodyLabel("Monitor live stats and control performance from one place.")
        root.addWidget(self.subtitle)

        # ── Telemetry Rings ──
        ring_row = QHBoxLayout()
        ring_row.setSpacing(16)
        self.cpu_card = _RingCard("CPU")
        self.ram_card = _RingCard("RAM")
        self.gpu_card = _RingCard("GPU")
        ring_row.addWidget(self.cpu_card)
        ring_row.addWidget(self.ram_card)
        ring_row.addWidget(self.gpu_card)
        ring_row.addStretch()
        root.addLayout(ring_row)

        # ── Info Cards Row ──
        info_row = QHBoxLayout()
        info_row.setSpacing(16)

        # System Snapshot
        sys_card = CardWidget(self)
        sys_ly = QVBoxLayout(sys_card)
        sys_ly.setContentsMargins(16, 14, 16, 14)
        sys_ly.addWidget(CaptionLabel("SYSTEM SNAPSHOT"))
        sys_ly.addWidget(_InfoRow("Computer", platform.node()))
        sys_ly.addWidget(_InfoRow("OS", f"{platform.system()} {platform.release()}"))
        sys_ly.addWidget(_InfoRow("User", os.environ.get("USERNAME", "N/A")))
        info_row.addWidget(sys_card, 1)

        # Hardware
        hw_card = CardWidget(self)
        hw_ly = QVBoxLayout(hw_card)
        hw_ly.setContentsMargins(16, 14, 16, 14)
        hw_ly.addWidget(CaptionLabel("HARDWARE"))
        cpu_name = platform.processor() or "N/A"
        hw_ly.addWidget(_InfoRow("CPU", cpu_name[:48]))
        hw_ly.addWidget(_InfoRow("Cores", f"{psutil.cpu_count(logical=False)}P / {psutil.cpu_count()}L"))
        hw_ly.addWidget(_InfoRow("RAM", f"{psutil.virtual_memory().total / 1073741824:.1f} GB"))
        info_row.addWidget(hw_card, 1)

        root.addLayout(info_row)

        # ── Live Telemetry Card ──
        live_card = CardWidget(self)
        live_ly = QVBoxLayout(live_card)
        live_ly.setContentsMargins(16, 14, 16, 14)
        live_ly.addWidget(CaptionLabel("LIVE TELEMETRY"))
        self._live = {}
        for key in ["Uptime", "Processes", "Net Up", "Net Down"]:
            row = _InfoRow(key, "--")
            live_ly.addWidget(row)
            self._live[key] = row.val
        root.addWidget(live_card)

        root.addStretch()

        # ── Timers ──
        self._net_prev = psutil.net_io_counters()
        self._net_prev_ts = time.time()
        start_gpu_sampler()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1500)
        self._tick()

    def _tick(self):
        cpu = stable_cpu_percent()
        mem = psutil.virtual_memory()
        gpu = gpu_percent()

        self.cpu_card.set_value(cpu)
        self.ram_card.set_value(mem.percent)
        self.gpu_card.set_value(gpu)

        up = int(time.time() - psutil.boot_time())
        h, m = divmod(up // 60, 60)
        d, h = divmod(h, 24)
        self._live["Uptime"].setText(f"{d}d {h}h {m}m")
        self._live["Processes"].setText(str(len(psutil.pids())))

        nio = psutil.net_io_counters()
        now = time.time()
        elapsed = max(0.001, now - self._net_prev_ts)
        self._live["Net Up"].setText(f"{(nio.bytes_sent - self._net_prev.bytes_sent) / 1024 / elapsed:.0f} KB/s")
        self._live["Net Down"].setText(f"{(nio.bytes_recv - self._net_prev.bytes_recv) / 1024 / elapsed:.0f} KB/s")
        self._net_prev = nio
        self._net_prev_ts = now

class TweakInterface(ScrollArea):
    def __init__(self, category, parent=None):
        super().__init__(parent=parent)
        self.category = category
        self.setObjectName(f"TweakInterface_{category}")
        self.setWidgetResizable(True)
        
        self.view = QWidget()
        self.setWidget(self.view)
        
        self.layout = QVBoxLayout(self.view)
        self.layout.setContentsMargins(24, 24, 24, 24)
        
        self.title = TitleLabel(category)
        self.layout.addWidget(self.title)
        
        self.card_layout = QVBoxLayout()
        
        self.switches = {}
        entries = category_entries(category)
        selected = load_selected_tweaks()
        
        for entry in entries:
            card = CardWidget(self)
            card_ly = QHBoxLayout(card)
            
            info_ly = QVBoxLayout()
            name_lbl = BodyLabel(entry["name"])
            desc_lbl = CaptionLabel(entry.get("desc", ""))
            
            info_ly.addWidget(name_lbl)
            info_ly.addWidget(desc_lbl)
            
            switch = SwitchButton(self)
            switch.setChecked(entry["id"] in selected)
            switch.checkedChanged.connect(lambda checked, e=entry: self.on_tweak_toggled(e, checked))
            self.switches[entry["id"]] = switch
            
            card_ly.addLayout(info_ly)
            card_ly.addStretch()
            card_ly.addWidget(switch)
            
            self.card_layout.addWidget(card)
            
        self.layout.addLayout(self.card_layout)
        self.layout.addStretch()

    def on_tweak_toggled(self, entry, checked):
        selected = load_selected_tweaks()
        if checked:
            selected.add(entry["id"])
        else:
            selected.discard(entry["id"])
        set_selected_tweaks(selected)

class GamesInterface(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("GamesInterface")
        self.setWidgetResizable(True)
        self._game_names = ["Roblox", "FiveM", "Valorant", "CS2", "Minecraft", "Fortnite", "Apex"]
        self.view = QWidget()
        self.setWidget(self.view)

        self.root = QVBoxLayout(self.view)
        self.root.setContentsMargins(28, 28, 28, 28)
        self.root.setSpacing(16)

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        self.title = TitleLabel("Games")
        self.subtitle = BodyLabel("Choose a game to manage its tweaks.")
        title_col.addWidget(self.title)
        title_col.addWidget(self.subtitle)
        header.addLayout(title_col, 1)
        self.back_btn = PushButton("All Games", self)
        self.back_btn.clicked.connect(self._show_games)
        header.addWidget(self.back_btn)
        self.root.addLayout(header)

        self.content = QVBoxLayout()
        self.content.setSpacing(12)
        self.root.addLayout(self.content)
        self.root.addStretch()
        self._show_games()

    def _clear_content(self):
        while self.content.count():
            item = self.content.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                layout = item.layout()
                while layout.count():
                    child = layout.takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()

    def _show_games(self):
        self.title.setText("Games")
        self.subtitle.setText("Choose a game to manage its tweaks.")
        self.back_btn.hide()
        self._clear_content()

        grid = QGridLayout()
        grid.setSpacing(12)
        for i, game in enumerate(self._game_names):
            entries = category_entries(game)
            card = CardWidget(self)
            cly = QHBoxLayout(card)
            cly.setContentsMargins(16, 14, 16, 14)
            info = QVBoxLayout()
            info.addWidget(BodyLabel(game))
            info.addWidget(CaptionLabel(f"{len(entries)} tweaks"))
            cly.addLayout(info, 1)
            btn = PushButton("Open", self)
            btn.clicked.connect(lambda _=False, g=game: self._show_game(g))
            cly.addWidget(btn)
            grid.addWidget(card, i // 2, i % 2)
        self.content.addLayout(grid)

    def _show_game(self, game):
        self.title.setText(game)
        self.subtitle.setText(f"{game}-specific tweaks.")
        self.back_btn.show()
        self._clear_content()

        top_row = QHBoxLayout()
        back = PushButton("Back to Games", self)
        back.clicked.connect(self._show_games)
        top_row.addWidget(back)
        top_row.addStretch()
        self.content.addLayout(top_row)

        selected = load_selected_tweaks()
        for entry in category_entries(game):
            card = CardWidget(self)
            cly = QHBoxLayout(card)
            cly.setContentsMargins(16, 14, 16, 14)
            info = QVBoxLayout()
            info.addWidget(BodyLabel(entry["name"]))
            info.addWidget(CaptionLabel(entry.get("desc", "")))
            switch = SwitchButton(self)
            switch.setChecked(entry["id"] in selected)
            switch.checkedChanged.connect(lambda checked, e=entry: self._toggle_tweak(e, checked))
            cly.addLayout(info, 1)
            cly.addWidget(switch)
            self.content.addWidget(card)

    def _toggle_tweak(self, entry, checked):
        selected = load_selected_tweaks()
        if checked:
            selected.add(entry["id"])
        else:
            selected.discard(entry["id"])
        set_selected_tweaks(selected)

class PresetsInterface(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("PresetsInterface")
        self.setWidgetResizable(True)
        self._worker = None
        self.view = QWidget()
        self.setWidget(self.view)
        root = QVBoxLayout(self.view)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(16)
        root.addWidget(TitleLabel("Presets"))
        root.addWidget(BodyLabel("Load built-in packs or save your own tweak selection."))
        save_card = CardWidget(self)
        save_ly = QVBoxLayout(save_card)
        save_ly.setContentsMargins(16, 14, 16, 14)
        save_ly.addWidget(CaptionLabel("SAVE CURRENT SELECTION"))
        row = QHBoxLayout()
        self._name_input = LineEdit(self)
        self._name_input.setPlaceholderText("Profile name")
        save_btn = PrimaryPushButton("Save", self)
        save_btn.clicked.connect(self._save_profile)
        row.addWidget(self._name_input, 1)
        row.addWidget(save_btn)
        save_ly.addLayout(row)
        self._save_status = CaptionLabel("")
        save_ly.addWidget(self._save_status)
        root.addWidget(save_card)
        root.addWidget(CaptionLabel("BUILT-IN PRESET PACKS"))
        for preset in builtin_presets():
            card = CardWidget(self)
            cly = QHBoxLayout(card)
            cly.setContentsMargins(16, 12, 16, 12)
            info = QVBoxLayout()
            info.addWidget(BodyLabel(preset["title"]))
            info.addWidget(CaptionLabel(f"{preset['count']} tweaks — {preset['desc']}"))
            btn = PushButton("Load", self)
            btn.clicked.connect(lambda _=False, pid=preset["id"]: self._load_preset(pid))
            cly.addLayout(info, 1)
            cly.addWidget(btn)
            root.addWidget(card)
        self._profiles_label = CaptionLabel("SAVED PROFILES")
        root.addWidget(self._profiles_label)
        self._profiles_layout = QVBoxLayout()
        root.addLayout(self._profiles_layout)
        root.addStretch()
        self._refresh_profiles()

    def _save_profile(self):
        ok, msg = save_profile(self._name_input.text(), load_selected_tweaks())
        self._save_status.setText(msg)
        if ok:
            self._name_input.clear()
            append_activity("profile", "Saved profile", msg, "ok")
            self._refresh_profiles()

    def _load_preset(self, pid):
        ok, msg, preset = load_builtin_preset(pid)
        self._save_status.setText(msg)
        if ok:
            append_activity("preset", "Loaded preset", preset["title"], "ok")

    def _load_profile(self, name):
        profiles = load_profiles()
        info = profiles.get(name, {})
        set_selected_tweaks(info.get("tweaks", []))
        append_activity("profile", "Loaded profile", name, "ok")
        self._save_status.setText(f"Loaded '{name}'.")

    def _delete_profile(self, name):
        delete_profile(name)
        append_activity("profile", "Deleted profile", name, "ok")
        self._refresh_profiles()

    def _refresh_profiles(self):
        while self._profiles_layout.count():
            item = self._profiles_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        profiles = load_profiles()
        if not profiles:
            self._profiles_layout.addWidget(CaptionLabel("No profiles saved yet."))
            return
        for name, info in sorted(profiles.items()):
            card = CardWidget(self)
            cly = QHBoxLayout(card)
            cly.setContentsMargins(16, 12, 16, 12)
            col = QVBoxLayout()
            col.addWidget(BodyLabel(name))
            col.addWidget(CaptionLabel(f"{len(info.get('tweaks', []))} tweaks"))
            cly.addLayout(col, 1)
            load_btn = PushButton("Load", self)
            load_btn.clicked.connect(lambda _=False, n=name: self._load_profile(n))
            del_btn = PushButton("Delete", self)
            del_btn.clicked.connect(lambda _=False, n=name: self._delete_profile(n))
            cly.addWidget(load_btn)
            cly.addWidget(del_btn)
            self._profiles_layout.addWidget(card)


class QuickToolsInterface(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("QuickToolsInterface")
        self.setWidgetResizable(True)
        self._worker = None
        self.view = QWidget()
        self.setWidget(self.view)
        root = QVBoxLayout(self.view)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(16)
        root.addWidget(TitleLabel("Quick Tools"))
        root.addWidget(BodyLabel("One-click system utilities."))
        self._status = CaptionLabel("")
        self._buttons = []
        grid = QGridLayout()
        grid.setSpacing(12)
        for i, entry in enumerate(quick_tool_entries()):
            card = CardWidget(self)
            cly = QVBoxLayout(card)
            cly.setContentsMargins(16, 14, 16, 14)
            cly.setSpacing(8)
            cly.addWidget(BodyLabel(entry["name"]))
            cly.addWidget(CaptionLabel(entry.get("desc", "")))
            btn = PushButton("Run", self)
            btn.clicked.connect(lambda _=False, e=entry: self._run_tool(e))
            cly.addWidget(btn)
            self._buttons.append(btn)
            grid.addWidget(card, i // 3, i % 3)
        root.addLayout(grid)
        root.addWidget(self._status)
        root.addStretch()

    def _run_tool(self, entry):
        if self._worker and self._worker.isRunning():
            return
        self._status.setText(f"Running {entry['name']}...")
        for b in self._buttons:
            b.setEnabled(False)
        self._worker = TweakWorker([entry])
        self._worker.done.connect(lambda: self._finish(entry))
        self._worker.start()

    def _finish(self, entry):
        for b in self._buttons:
            b.setEnabled(True)
        self._status.setText(f"{entry['name']} finished.")
        append_activity("quick-tool", entry["name"], entry.get("desc", ""), "ok")
        self._worker = None


class RestoreInterface(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("RestoreInterface")
        self.setWidgetResizable(True)
        self._rp_result = None
        self._rp_poll = QTimer(self)
        self._rp_poll.timeout.connect(self._poll_result)
        self.view = QWidget()
        self.setWidget(self.view)
        root = QVBoxLayout(self.view)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(16)
        root.addWidget(TitleLabel("Restore"))
        root.addWidget(BodyLabel("System protection and Windows restore points."))
        self._status_card = CardWidget(self)
        sly = QHBoxLayout(self._status_card)
        sly.setContentsMargins(16, 14, 16, 14)
        self._status_label = BodyLabel("Checking...")
        self._revert_btn = PushButton("Open System Restore", self)
        self._revert_btn.clicked.connect(lambda: run_cmd("rstrui.exe"))
        sly.addWidget(self._status_label, 1)
        sly.addWidget(self._revert_btn)
        root.addWidget(self._status_card)
        create_card = CardWidget(self)
        cly = QVBoxLayout(create_card)
        cly.setContentsMargins(16, 14, 16, 14)
        cly.addWidget(CaptionLabel("CREATE NEW RESTORE POINT"))
        cly.addWidget(BodyLabel("Creates a Windows System Restore checkpoint."))
        self._create_btn = PrimaryPushButton("Create Restore Point", self)
        self._create_btn.clicked.connect(self._create)
        cly.addWidget(self._create_btn)
        self._create_status = CaptionLabel("")
        cly.addWidget(self._create_status)
        root.addWidget(create_card)
        root.addStretch()
        self._refresh()

    def _refresh(self):
        if has_restore_point():
            self._status_label.setText("\u2713 Restore Point Ready")
            self._revert_btn.setEnabled(True)
        else:
            self._status_label.setText("\u2717 No Restore Point")
            self._revert_btn.setEnabled(False)

    def _create(self):
        import threading
        self._create_btn.setEnabled(False)
        self._create_btn.setText("Creating...")
        self._create_status.setText("Creating restore point...")
        self._rp_result = None
        def _do():
            self._rp_result = create_restore_point()
        threading.Thread(target=_do, daemon=True).start()
        self._rp_poll.start(200)

    def _poll_result(self):
        if self._rp_result is None:
            return
        self._rp_poll.stop()
        ok, msg = self._rp_result
        self._create_status.setText(msg)
        self._create_btn.setEnabled(True)
        self._create_btn.setText("Create Restore Point")
        self._refresh()


class SettingsInterface(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("SettingsInterface")
        self.setWidgetResizable(True)
        self.view = QWidget()
        self.setWidget(self.view)
        root = QVBoxLayout(self.view)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(16)
        root.addWidget(TitleLabel("Settings"))
        root.addWidget(BodyLabel("Tune the app theme and manage your configuration."))
        color_card = CardWidget(self)
        cly = QVBoxLayout(color_card)
        cly.setContentsMargins(16, 14, 16, 14)
        cly.addWidget(CaptionLabel("ACCENT COLOR"))
        d = load_data()
        current = d.get("color", "#e60000")
        self._color_label = CaptionLabel(f"Current: {current}")
        cly.addWidget(self._color_label)
        root.addWidget(color_card)
        info_card = CardWidget(self)
        ily = QVBoxLayout(info_card)
        ily.setContentsMargins(16, 14, 16, 14)
        ily.addWidget(CaptionLabel("APPLICATION"))
        ily.addWidget(_InfoRow("Version", d.get("version", "2.0")))
        ily.addWidget(_InfoRow("Data Dir", os.path.dirname(os.path.abspath(__file__))))
        root.addWidget(info_card)
        root.addStretch()


class AccountInterface(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("AccountInterface")
        self.setWidgetResizable(True)
        self.view = QWidget()
        self.setWidget(self.view)
        root = QVBoxLayout(self.view)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(16)
        root.addWidget(TitleLabel("Local Edition"))
        root.addWidget(BodyLabel("Hextra is running fully offline. No account key is required."))
        auth = load_auth()
        username = auth.get("username", "")
        status_card = CardWidget(self)
        sly = QVBoxLayout(status_card)
        sly.setContentsMargins(16, 14, 16, 14)
        sly.addWidget(CaptionLabel("ACCOUNT STATUS"))
        self._user_label = BodyLabel(f"Mode: {username or 'local'}")
        sly.addWidget(self._user_label)
        root.addWidget(status_card)
        root.addStretch()

    def _redeem(self):
        return

    def _on_redeem_done(self, result):
        self._redeem_btn.setEnabled(True)
        self._redeem_status.setText(str(result))
        self._key_input.clear()

class HextraFluentWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.initWindow()
        
        # Interfaces
        self.overview_interface = OverviewInterface(self)
        self.addSubInterface(self.overview_interface, FIF.HOME, "Overview")
        
        # Performance
        self.fps_interface = TweakInterface("FPS Boost", self)
        self.cpu_interface = TweakInterface("CPU", self)
        self.gpu_interface = TweakInterface("GPU", self)
        self.ram_interface = TweakInterface("RAM", self)
        self.input_interface = TweakInterface("Input", self)
        self.network_interface = TweakInterface("Network", self)
        
        self.navigationInterface.addSeparator()
        self.addSubInterface(self.fps_interface, FIF.SPEED_OFF, "FPS Boost", position=NavigationItemPosition.SCROLL)
        self.addSubInterface(self.cpu_interface, FIF.DEVELOPER_TOOLS, "CPU", position=NavigationItemPosition.SCROLL)
        self.addSubInterface(self.gpu_interface, FIF.GAME, "GPU", position=NavigationItemPosition.SCROLL)
        self.addSubInterface(self.ram_interface, FIF.APPLICATION, "RAM", position=NavigationItemPosition.SCROLL)
        self.addSubInterface(self.input_interface, FIF.EXPRESSIVE_INPUT_ENTRY, "Input", position=NavigationItemPosition.SCROLL)
        self.addSubInterface(self.network_interface, FIF.WIFI, "Network", position=NavigationItemPosition.SCROLL)
        
        # System
        self.privacy_interface = TweakInterface("Privacy", self)
        self.debloat_interface = TweakInterface("Debloat", self)
        self.services_interface = TweakInterface("Services", self)
        
        self.navigationInterface.addSeparator()
        self.addSubInterface(self.privacy_interface, FIF.CERTIFICATE, "Privacy", position=NavigationItemPosition.SCROLL)
        self.addSubInterface(self.debloat_interface, FIF.DELETE, "Debloat", position=NavigationItemPosition.SCROLL)
        self.addSubInterface(self.services_interface, FIF.SETTING, "Services", position=NavigationItemPosition.SCROLL)

        # Games
        self.games_interface = GamesInterface(self)
        self.navigationInterface.addSeparator()
        self.addSubInterface(self.games_interface, FIF.GAME, "Games", position=NavigationItemPosition.SCROLL)

        # Tools
        self.presets_interface = PresetsInterface(self)
        self.quick_tools_interface = QuickToolsInterface(self)
        self.restore_interface = RestoreInterface(self)
        self.settings_interface = SettingsInterface(self)
        self.account_interface = AccountInterface(self)
        
        self.addSubInterface(self.presets_interface, FIF.FOLDER, "Presets", position=NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.quick_tools_interface, FIF.LAYOUT, "Quick Tools", position=NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.restore_interface, FIF.HISTORY, "Restore", position=NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.settings_interface, FIF.SETTING, "Settings", position=NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.account_interface, FIF.PEOPLE, "Account", position=NavigationItemPosition.BOTTOM)
        
        

    def initWindow(self):
        self.resize(1180, 760)
        self.setMinimumSize(1060, 680)
        self.setWindowTitle('Hextra')
        
        d = load_data()
        accent = d.get("color", "#e60000")
        if accent != "rainbow":
            setThemeColor(accent)
            
        setTheme(Theme.DARK)

def main():
    app = QApplication(sys.argv)
    d = load_data()
    accent = d.get("color", "#e60000")
    if accent != "rainbow":
        setThemeColor(accent)
    setTheme(Theme.DARK)

    w = HextraFluentWindow()
    w.show()
    return app.exec()

if __name__ == "__main__":
    main()
