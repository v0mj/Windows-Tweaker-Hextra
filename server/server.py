#!/usr/bin/env python3
"""
HEXTRA License Server
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Key validation, admin panel, reseller system, crash reporting, auto-updates.
Unified login system: admins and resellers share one /login page.
All data stored as JSON files under /opt/suno/.
"""

import collections
import datetime
import hmac
import hashlib
import json
import mimetypes
import os
import re
import secrets
import shutil
import string
import tempfile
import threading
import time
import urllib.request

from flask import Flask, Response, request, jsonify, redirect, session
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)

BASE_DIR   = os.environ.get("HEXTRA_DATA_DIR", "/opt/suno")
COMPAT_BASE_DIRS = (BASE_DIR, "/opt/suno", "/opt/hextra", "/opt/defy")
LEGACY_BASE_DIR = "/opt/defy"
DB         = os.path.join(BASE_DIR, "keys.json")
CFG_FILE   = os.path.join(BASE_DIR, "config.json")
SESSION_SECRET_FILE = os.path.join(BASE_DIR, ".secret_key")
ACCOUNTS_FILE = os.path.join(BASE_DIR, "accounts.json")
LAUNCH_LOG = os.path.join(BASE_DIR, "launches.json")
CRASH_DIR  = os.path.join(BASE_DIR, "crashes")
AUDIT_LOG  = os.path.join(BASE_DIR, "audit.json")
UPDATE_DIR = os.path.join(BASE_DIR, "updates")
FILE_STORAGE_DIR = os.path.join(BASE_DIR, "shared_files")
FILE_MANAGER_ROOT = BASE_DIR
DEFAULT_USER_CLOUD_ROOT = "cloud"
RATE_LIMIT = 10
AUTH_RATE_LIMIT = 10
AUTH_RATE_WINDOW = 300
CLIENT_SESSION_TTL_SECONDS = max(300, int(os.environ.get("HEXTRA_CLIENT_SESSION_TTL_SECONDS", "21600") or "21600"))
CRASH_RATE_LIMIT = 8
CRASH_RATE_WINDOW = 300
GLOBAL_CRASH_RATE_LIMIT = 50
GLOBAL_CRASH_RATE_WINDOW = 60
MAX_CRASH_TRACEBACK = 8000
MAX_CRASH_ERROR = 400
MAX_UPDATE_SIZE_MB = max(32, int(os.environ.get("HEXTRA_MAX_UPDATE_SIZE_MB", "512") or "512"))
MAX_UPDATE_SIZE = MAX_UPDATE_SIZE_MB * 1024 * 1024
MAX_SHARED_FILE_SIZE_MB = max(32, int(os.environ.get("HEXTRA_MAX_SHARED_FILE_SIZE_MB", str(MAX_UPDATE_SIZE_MB)) or str(MAX_UPDATE_SIZE_MB)))
MAX_SHARED_FILE_SIZE = MAX_SHARED_FILE_SIZE_MB * 1024 * 1024
MAX_TEXT_EDITOR_SIZE_MB = max(1, int(os.environ.get("HEXTRA_MAX_TEXT_EDITOR_SIZE_MB", "2") or "2"))
MAX_TEXT_EDITOR_SIZE = MAX_TEXT_EDITOR_SIZE_MB * 1024 * 1024
HTML_PREVIEW_TTL_SECONDS = max(30, int(os.environ.get("HEXTRA_HTML_PREVIEW_TTL_SECONDS", "300") or "300"))
HWID_REBIND_LIMIT = 2
ALLOWED_UPDATE_EXTENSIONS = {".py", ".exe", ".zip", ".7z", ".msi"}
EDITABLE_TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".json", ".yaml", ".yml", ".html", ".htm", ".css", ".js", ".mjs", ".cjs",
    ".ts", ".tsx", ".jsx", ".xml", ".csv", ".ini", ".cfg", ".conf", ".env", ".toml", ".py", ".sh", ".bat",
    ".ps1", ".log", ".sql", ".php", ".rb", ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".go", ".rs",
}
app.config["MAX_CONTENT_LENGTH"] = MAX_UPDATE_SIZE

DEFAULT_ADMIN_USER = "admin"
PASSWORD_HASH_PREFIXES = ("pbkdf2:", "scrypt:")
SESSION_KEY = "suno_auth"
SESSION_KEY_ALIASES = ("suno_auth", "hextra_auth", "defy_auth")
TRUSTED_PROXIES = {
    ip.strip()
    for ip in os.environ.get("SUNO_TRUSTED_PROXIES", "127.0.0.1,::1").split(",")
    if ip.strip()
}
SECRET_ENV_MAP = {
    "discord_webhook": "SUNO_DISCORD_WEBHOOK",
    "groq_api_key": "SUNO_GROQ_API_KEY",
    "gemini_api_key": "SUNO_GEMINI_API_KEY",
}
_json_locks = collections.defaultdict(threading.Lock)
_html_preview_tokens = {}
_html_preview_lock = threading.Lock()
_file_manager_presence = {}
_file_manager_presence_lock = threading.Lock()
FILE_MANAGER_PRESENCE_TTL_SECONDS = max(20, int(os.environ.get("HEXTRA_FILE_MANAGER_PRESENCE_TTL_SECONDS", "45") or "45"))


def _ensure_dir(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)

def _candidate_data_paths(path):
    normalized = os.path.normpath(path)
    rel = None
    for root in COMPAT_BASE_DIRS:
        root_norm = os.path.normpath(root)
        prefix = root_norm + os.sep
        if normalized == root_norm:
            rel = ""
            break
        if normalized.startswith(prefix):
            rel = normalized[len(prefix):]
            break
    if rel is None:
        return [path]
    candidates = []
    for root in COMPAT_BASE_DIRS:
        candidate = os.path.join(root, rel) if rel else root
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates

def _resolve_existing_data_path(path):
    for candidate in _candidate_data_paths(path):
        if os.path.exists(candidate):
            return candidate
    return path

def _load_json(path, default=None):
    for candidate in _candidate_data_paths(path):
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as _e:
            app.logger.warning("Suppressed error in _load_json: %s", _e)
    return default if default is not None else {}

def _save_json(path, data):
    path = _resolve_existing_data_path(path)
    _ensure_dir(path)
    lock = _json_locks[path]
    tmp_path = None
    with lock:
        fd, tmp_path = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
            tmp_path = None
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)

def load_keys():   return _load_json(DB, {})
def save_keys(k):  _save_json(DB, k)
def load_cfg():    return _load_json(CFG_FILE, {})
def save_cfg(c):   _save_json(CFG_FILE, c)
def load_accounts(): return _load_json(ACCOUNTS_FILE, {})
def save_accounts(a): _save_json(ACCOUNTS_FILE, a)

@app.errorhandler(RequestEntityTooLarge)
def handle_request_too_large(_err):
    return jsonify(
        success=False,
        message=f"Uploaded file too large. Server limit is {MAX_UPDATE_SIZE_MB} MB.",
        limit_mb=MAX_UPDATE_SIZE_MB,
    ), 413

@app.after_request
def add_security_headers(response):
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://fonts.gstatic.com; "
        "font-src https://fonts.googleapis.com https://fonts.gstatic.com"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


def _is_password_hash(value):
    return isinstance(value, str) and value.startswith(PASSWORD_HASH_PREFIXES)

def _hash_password(password: str) -> str:
    return generate_password_hash(password)

def _password_matches(stored: str, candidate: str) -> bool:
    if not isinstance(stored, str) or not isinstance(candidate, str) or not candidate:
        return False
    if _is_password_hash(stored):
        try:
            return check_password_hash(stored, candidate)
        except Exception as _e:
            app.logger.warning("Suppressed error in _password_matches: %s", _e)
            return False
    return secrets.compare_digest(stored, candidate)

def _migrate_passwords(entries):
    changed = False
    for entry in entries.values():
        password = entry.get("password", "")
        if password and not _is_password_hash(password):
            entry["password"] = _hash_password(password)
            changed = True
    return changed

def _read_secret_file():
    for candidate in _candidate_data_paths(SESSION_SECRET_FILE):
        try:
            if os.path.isfile(candidate):
                with open(candidate, "r", encoding="utf-8") as f:
                    secret = f.read().strip()
                if secret:
                    return secret
        except Exception as _e:
            app.logger.warning("Suppressed error in _read_secret_file: %s", _e)
    return ""

def _write_secret_file(secret):
    path = _resolve_existing_data_path(SESSION_SECRET_FILE)
    _ensure_dir(path)
    tmp_path = None
    fd, tmp_path = tempfile.mkstemp(prefix=".secret_key.", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(secret)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        tmp_path = None
        try:
            os.chmod(path, 0o600)
        except Exception as _e:
            app.logger.warning("Suppressed error in _write_secret_file: %s", _e)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

def _get_session_secret():
    env_secret = os.getenv("SUNO_SESSION_SECRET", "").strip()
    if env_secret:
        return env_secret
    file_secret = _read_secret_file()
    if file_secret:
        return file_secret
    cfg = load_cfg()
    cfg_secret = cfg.get("session_secret", "").strip()
    if cfg_secret:
        if not _read_secret_file():
            _write_secret_file(cfg_secret)
        return cfg_secret
    session_secret = secrets.token_urlsafe(48)
    config_exists = any(os.path.isfile(candidate) for candidate in _candidate_data_paths(CFG_FILE))
    if config_exists:
        cfg["session_secret"] = session_secret
        save_cfg(cfg)
    else:
        _write_secret_file(session_secret)
    return session_secret

def _configure_sessions():
    lifetime_hours = max(1, int(os.getenv("HEXTRA_WEB_SESSION_HOURS", "12") or "12"))
    app.secret_key = _get_session_secret()
    app.config.update(
        SESSION_COOKIE_NAME="suno_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=os.getenv("SUNO_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"},
        PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=lifetime_hours),
    )

def _session_auth():
    for key in SESSION_KEY_ALIASES:
        auth = session.get(key)
        if isinstance(auth, dict):
            if key != SESSION_KEY:
                session[SESSION_KEY] = auth
                session.modified = True
            return auth
    return {}

def _set_session_auth(role: str, username: str, name: str = "", remember: bool = False, **extra):
    session.clear()
    session.permanent = bool(remember)
    auth = {"role": role, "username": username, "name": name or username}
    auth.update({k: v for k, v in extra.items() if v not in (None, "")})
    for key in SESSION_KEY_ALIASES:
        session[key] = dict(auth)
    session.modified = True
    return auth

def _clear_session_auth():
    session.clear()

def _secret_env_name(key: str) -> str:
    return SECRET_ENV_MAP.get(key, f"SUNO_{key.upper()}")

def _has_env_secret(key: str) -> bool:
    return bool(os.getenv(_secret_env_name(key), "").strip())

def _cfg_secret(key: str, default: str = "") -> str:
    env_value = os.getenv(_secret_env_name(key), "").strip()
    if env_value:
        return env_value
    return load_cfg().get(key, default)

def _safe_update_filename(filename: str) -> str:
    sanitized = secure_filename((filename or "").replace("\\", "/"))
    if not sanitized:
        raise ValueError("Invalid filename")
    ext = os.path.splitext(sanitized)[1].lower()
    if ext not in ALLOWED_UPDATE_EXTENSIONS:
        raise ValueError("Unsupported update file type")
    return sanitized

def _safe_join(base_dir: str, filename: str) -> str:
    final_path = os.path.realpath(os.path.join(base_dir, filename))
    real_base = os.path.realpath(base_dir)
    if not (final_path == real_base or final_path.startswith(real_base + os.sep)):
        raise ValueError("Invalid path")
    return final_path

def _safe_storage_filename(filename: str) -> str:
    raw_name = os.path.basename((filename or "").replace("\\", "/")).strip()
    sanitized = secure_filename(raw_name)
    if not sanitized:
        raise ValueError("Invalid filename")
    return sanitized

def _safe_manager_name(name: str) -> str:
    raw_name = os.path.basename((name or "").replace("\\", "/")).strip()
    sanitized = secure_filename(raw_name)
    if not sanitized:
        raise ValueError("Invalid name")
    return sanitized

def _normalize_manager_relpath(rel_path: str) -> str:
    rel_path = (rel_path or "").replace("\\", "/").strip().strip("/")
    if not rel_path:
        return ""
    normalized = os.path.normpath(rel_path).replace("\\", "/")
    if normalized in {".", ""}:
        return ""
    if normalized.startswith("../") or normalized == "..":
        raise ValueError("Invalid path")
    return normalized

def _normalize_user_file_access_paths(value):
    paths = value
    if isinstance(paths, str):
        raw_parts = re.split(r"[\r\n,;]+", paths)
    elif isinstance(paths, list):
        raw_parts = paths
    else:
        raw_parts = []
    normalized = []
    for item in raw_parts:
        try:
            path = _normalize_manager_relpath(str(item or ""))
        except ValueError:
            continue
        if not path:
            continue
        if path not in normalized:
            normalized.append(path)
    normalized.sort()
    return normalized

def _user_cloud_folder_name(username: str, fallback: str = "user") -> str:
    candidate = secure_filename((username or "").strip().lower()).replace("-", "_")
    candidate = re.sub(r"_+", "_", candidate).strip("._")
    return candidate or fallback

def _normalize_user_cloud_path(value, username: str = "", fallback: str = "") -> str:
    raw = (value or "").strip()
    if not raw:
        base = _normalize_manager_relpath(DEFAULT_USER_CLOUD_ROOT)
        slug = _user_cloud_folder_name(username, fallback or "user")
        raw = f"{base}/{slug}" if base else slug
    return _normalize_manager_relpath(raw)

def _manager_path(rel_path: str = ""):
    normalized = _normalize_manager_relpath(rel_path)
    absolute = _safe_join(FILE_MANAGER_ROOT, normalized)
    return normalized, absolute

def _ensure_manager_folder(rel_path: str) -> str:
    normalized, absolute = _manager_path(rel_path)
    os.makedirs(absolute, exist_ok=True)
    return normalized

def _uploaded_file_size(file_storage) -> int:
    stream = file_storage.stream
    current = stream.tell()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(current, os.SEEK_SET)
    return size

def _list_shared_files():
    os.makedirs(FILE_STORAGE_DIR, exist_ok=True)
    items = []
    for entry in os.scandir(FILE_STORAGE_DIR):
        if not entry.is_file():
            continue
        stat = entry.stat()
        items.append({
            "name": entry.name,
            "size": stat.st_size,
            "modified": datetime.datetime.fromtimestamp(stat.st_mtime, datetime.timezone.utc).isoformat(),
            "download_url": f"/files/download/{entry.name}",
        })
    items.sort(key=lambda item: item["modified"], reverse=True)
    return items

def _manager_breadcrumbs(rel_path: str):
    crumbs = [{"name": "root", "path": ""}]
    if not rel_path:
        return crumbs
    current = []
    for part in rel_path.split("/"):
        current.append(part)
        crumbs.append({"name": part, "path": "/".join(current)})
    return crumbs

def _dir_size_bytes(path: str) -> int:
    total = 0
    try:
        for root, dirs, files in os.walk(path):
            for name in files:
                file_path = os.path.join(root, name)
                try:
                    total += os.path.getsize(file_path)
                except OSError:
                    continue
    except OSError:
        return 0
    return total

def _list_manager_entries(rel_path: str = ""):
    normalized, absolute = _manager_path(rel_path)
    os.makedirs(FILE_MANAGER_ROOT, exist_ok=True)
    if not os.path.isdir(absolute):
        raise FileNotFoundError("Folder not found")
    entries = []
    for entry in os.scandir(absolute):
        stat = entry.stat()
        entry_rel = f"{normalized}/{entry.name}" if normalized else entry.name
        is_dir = entry.is_dir()
        entries.append({
            "name": entry.name,
            "path": entry_rel,
            "type": "dir" if is_dir else "file",
            "size": _dir_size_bytes(entry.path) if is_dir else stat.st_size,
            "modified": datetime.datetime.fromtimestamp(stat.st_mtime, datetime.timezone.utc).isoformat(),
            "download_url": f"/files/download/{entry_rel}" if not is_dir else "",
        })
    entries.sort(key=lambda item: (0 if item["type"] == "dir" else 1, item["name"].lower()))
    return {
        "path": normalized,
        "root": FILE_MANAGER_ROOT,
        "breadcrumbs": _manager_breadcrumbs(normalized),
        "entries": entries,
    }

def _editable_text_extension(rel_path: str) -> str:
    return os.path.splitext((rel_path or "").lower())[1]

def _is_editable_text_file(rel_path: str) -> bool:
    return _editable_text_extension(rel_path) in EDITABLE_TEXT_EXTENSIONS

def _is_html_preview_file(rel_path: str) -> bool:
    return _editable_text_extension(rel_path) in {".html", ".htm"}

def _user_file_access_paths(user):
    return _normalize_user_file_access_paths((user or {}).get("file_access_paths", []))

def _user_file_home_path(user) -> str:
    home = (user or {}).get("file_home_path", "")
    try:
        return _normalize_manager_relpath(home)
    except ValueError:
        return ""

def _user_default_file_path(user):
    home = _user_file_home_path(user)
    if home:
        return home
    allowed = _user_file_access_paths(user)
    return allowed[0] if allowed else ""

def _user_can_access_manager_path(user, rel_path: str = "") -> bool:
    allowed = _user_file_access_paths(user)
    if not allowed:
        return True
    try:
        normalized = _normalize_manager_relpath(rel_path)
    except ValueError:
        return False
    if not normalized:
        return False
    for base in allowed:
        if normalized == base or normalized.startswith(base + "/"):
            return True
    return False

def _cleanup_html_preview_tokens():
    now = datetime.datetime.now(datetime.timezone.utc)
    with _html_preview_lock:
        expired = [token for token, meta in _html_preview_tokens.items() if meta.get("expires_at", now) <= now]
        for token in expired:
            _html_preview_tokens.pop(token, None)

def _create_html_preview_token(rel_path: str) -> tuple[str, int]:
    _cleanup_html_preview_tokens()
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=HTML_PREVIEW_TTL_SECONDS)
    token = secrets.token_urlsafe(24)
    with _html_preview_lock:
        _html_preview_tokens[token] = {"path": rel_path, "expires_at": expires_at}
    return token, HTML_PREVIEW_TTL_SECONDS

def _consume_html_preview_token(token: str, revoke: bool = False):
    if not token:
        return None
    _cleanup_html_preview_tokens()
    now = datetime.datetime.now(datetime.timezone.utc)
    with _html_preview_lock:
        meta = _html_preview_tokens.get(token)
        if not meta:
            return None
        if meta.get("expires_at", now) <= now:
            _html_preview_tokens.pop(token, None)
            return None
        if revoke:
            _html_preview_tokens.pop(token, None)
        return dict(meta)

def _presence_key(actor: dict) -> str:
    return f"{actor.get('role','unknown')}:{actor.get('id','')}"

def _cleanup_file_manager_presence():
    now = datetime.datetime.now(datetime.timezone.utc)
    with _file_manager_presence_lock:
        expired = [key for key, meta in _file_manager_presence.items() if meta.get("expires_at", now) <= now]
        for key in expired:
            _file_manager_presence.pop(key, None)

def _update_file_manager_presence(actor: dict, current_path: str = "", editor_path: str = "", editing: bool = False):
    _cleanup_file_manager_presence()
    now = datetime.datetime.now(datetime.timezone.utc)
    expires_at = now + datetime.timedelta(seconds=FILE_MANAGER_PRESENCE_TTL_SECONDS)
    try:
        normalized_current = _normalize_manager_relpath(current_path)
    except ValueError:
        normalized_current = ""
    try:
        normalized_editor = _normalize_manager_relpath(editor_path)
    except ValueError:
        normalized_editor = ""
    key = _presence_key(actor)
    with _file_manager_presence_lock:
        _file_manager_presence[key] = {
            "role": actor.get("role", ""),
            "id": actor.get("id", ""),
            "name": actor.get("name", ""),
            "current_path": normalized_current,
            "editor_path": normalized_editor,
            "editing": bool(editing and normalized_editor),
            "updated_at": now,
            "expires_at": expires_at,
        }

def _remove_file_manager_presence(actor: dict):
    key = _presence_key(actor)
    with _file_manager_presence_lock:
        _file_manager_presence.pop(key, None)

def _file_manager_presence_snapshot():
    _cleanup_file_manager_presence()
    with _file_manager_presence_lock:
        items = []
        for meta in _file_manager_presence.values():
            items.append({
                "role": meta.get("role", ""),
                "id": meta.get("id", ""),
                "name": meta.get("name", ""),
                "current_path": meta.get("current_path", ""),
                "editor_path": meta.get("editor_path", ""),
                "editing": bool(meta.get("editing", False)),
                "updated_at": meta.get("updated_at").isoformat() if isinstance(meta.get("updated_at"), datetime.datetime) else "",
            })
    items.sort(key=lambda item: ((item.get("name") or "").lower(), item.get("role", "")))
    return items

def _normalize_hwid(hwid: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (hwid or "").lower())

def _remember_hwid(entry, hwid: str):
    raw_hwid = (hwid or "").strip()
    if not raw_hwid:
        return
    history = entry.setdefault("hwid_history", [])
    if raw_hwid not in history:
        history.append(raw_hwid)
        entry["hwid_history"] = history[-5:]
    normalized = _normalize_hwid(raw_hwid)
    if normalized:
        aliases = entry.setdefault("hwid_aliases", [])
        if normalized not in aliases:
            aliases.append(normalized)
            entry["hwid_aliases"] = aliases[-8:]

def _remember_ip(entry, ip: str):
    if not ip:
        return
    history = entry.setdefault("ip_history", [])
    if ip not in history:
        history.append(ip)
        entry["ip_history"] = history[-5:]
    entry["last_ip"] = ip

def _hwid_matches(entry, hwid: str) -> bool:
    raw_hwid = (hwid or "").strip()
    stored_hwid = (entry.get("hwid", "") or "").strip()
    if not raw_hwid or not stored_hwid:
        return False
    if stored_hwid == raw_hwid:
        return True
    normalized = _normalize_hwid(raw_hwid)
    if not normalized:
        return False
    aliases = set(entry.get("hwid_aliases", []))
    aliases.add(_normalize_hwid(stored_hwid))
    return normalized in aliases

def _can_auto_rebind(entry, hwid: str, ip: str) -> bool:
    raw_hwid = (hwid or "").strip()
    if not raw_hwid:
        return False
    if entry.get("rebind_count", 0) >= HWID_REBIND_LIMIT:
        return False
    if raw_hwid in entry.get("hwid_history", []):
        return False
    last_ip = entry.get("last_ip", "")
    ip_history = entry.get("ip_history", [])
    if ip and (ip == last_ip or ip in ip_history):
        return True
    return False

def _current_admin():
    auth = _session_auth()
    if auth.get("role") != "admin":
        return None, None
    username = auth.get("username", "").lower()
    admin_id = auth.get("admin_id", "")
    admins = _get_admins()
    if admin_id in admins:
        admin = admins[admin_id]
        if admin.get("username", "").lower() == username:
            return admin_id, admin
    for aid, admin in admins.items():
        if admin.get("username", "").lower() == username:
            return aid, admin
    return None, None

def _current_reseller():
    auth = _session_auth()
    if auth.get("role") != "reseller":
        return None, None
    rid = (auth.get("reseller_id", "") or "").strip().lower()
    username = auth.get("username", "").lower()
    resellers = _get_resellers()
    reseller = resellers.get(rid)
    if not reseller or reseller.get("disabled"):
        return None, None
    if reseller.get("username", "").lower() != username:
        return None, None
    return rid, reseller

def _current_user():
    auth = _session_auth()
    if auth.get("role") != "user":
        return None, None
    user_id = (auth.get("user_id", "") or "").strip().lower()
    username = auth.get("username", "").lower()
    users = _get_users()
    user = users.get(user_id)
    if not user or user.get("disabled") or user.get("banned"):
        return None, None
    if user.get("username", "").lower() != username:
        return None, None
    return user_id, user

def _admin_payload(admin_id, admin):
    return {
        "role": "admin",
        "name": admin.get("name", "Admin"),
        "username": admin.get("username", ""),
        "admin_id": admin_id,
        "file_manager_access": bool(admin.get("file_manager_access", True)),
    }

def _reseller_payload(rid, reseller):
    return {
        "role": "reseller",
        "name": reseller.get("name", ""),
        "username": reseller.get("username", ""),
        "reseller_id": rid,
        "credits": reseller.get("credits", 0),
    }

def _user_active_until(user):
    return (user or {}).get("license_expires", "") or ""

def _user_has_active_license(user):
    expires = _user_active_until(user)
    if not expires:
        return False
    try:
        return _parse_datetime(expires) > _now()
    except Exception as _e:
        app.logger.warning("Suppressed error in _user_has_active_license: %s", _e)
        return False

def _user_days_left(user):
    expires = _user_active_until(user)
    if not expires:
        return 0
    try:
        seconds = max(0, int((_parse_datetime(expires) - _now()).total_seconds()))
        return int((seconds + 86399) // 86400)
    except Exception as _e:
        app.logger.warning("Suppressed error in _user_days_left: %s", _e)
        return 0

def _user_payload(user_id, user):
    licenses = []
    for item in user.get("licenses", []):
        if not isinstance(item, dict):
            continue
        licenses.append({
            "key": item.get("key", ""),
            "days": item.get("days", 0),
            "redeemed_at": item.get("redeemed_at", ""),
            "active_from": item.get("active_from", ""),
            "active_until": item.get("active_until", ""),
            "type": item.get("type", "standard"),
        })
    return {
        "role": "user",
        "user_id": user_id,
        "username": user.get("username", ""),
        "email": user.get("email", ""),
        "created": user.get("created", ""),
        "disabled": bool(user.get("disabled")),
        "banned": bool(user.get("banned")),
        "domain_access": bool(user.get("access_files", user.get("domain_access", False))),
        "access_files": bool(user.get("access_files", user.get("domain_access", False))),
        "access_admin": bool(user.get("access_admin", False)),
        "access_reseller": bool(user.get("access_reseller", False)),
        "reseller_id": (user.get("reseller_id", "") or "").strip().lower(),
        "file_home_path": _user_file_home_path(user),
        "file_access_paths": _user_file_access_paths(user),
        "file_access_default_path": _user_default_file_path(user),
        "license_expires": user.get("license_expires", ""),
        "licensed": _user_has_active_license(user),
        "days_left": _user_days_left(user),
        "licenses": licenses[::-1],
    }

def _admin_user_summary(user_id, user):
    licenses = user.get("licenses", [])
    last_license = licenses[-1] if licenses else {}
    return {
        "id": user_id,
        "username": user.get("username", ""),
        "email": user.get("email", ""),
        "created": user.get("created", ""),
        "disabled": bool(user.get("disabled")),
        "banned": bool(user.get("banned")),
        "domain_access": bool(user.get("access_files", user.get("domain_access", False))),
        "access_files": bool(user.get("access_files", user.get("domain_access", False))),
        "access_admin": bool(user.get("access_admin", False)),
        "access_reseller": bool(user.get("access_reseller", False)),
        "reseller_id": (user.get("reseller_id", "") or "").strip().lower(),
        "file_home_path": _user_file_home_path(user),
        "file_access_paths": _user_file_access_paths(user),
        "file_access_default_path": _user_default_file_path(user),
        "hwid": user.get("hwid", ""),
        "license_expires": user.get("license_expires", ""),
        "licensed": _user_has_active_license(user),
        "days_left": _user_days_left(user),
        "license_count": len(licenses),
        "last_redeemed_at": user.get("last_redeemed_at", ""),
        "last_key": last_license.get("key", ""),
    }


def _admin_redeem_logs():
    users = _get_users()
    logs = []
    for uid, user in users.items():
        if not isinstance(user, dict):
            continue
        for item in user.get("licenses", []):
            if not isinstance(item, dict):
                continue
            key_id = item.get("key", "")
            if not key_id or key_id == "ADMIN-ADJUST":
                continue
            logs.append({
                "user_id": uid,
                "username": user.get("username", ""),
                "email": user.get("email", ""),
                "key": key_id,
                "days": int(item.get("days") or 0),
                "type": item.get("type", "standard"),
                "redeemed_at": item.get("redeemed_at", ""),
                "active_until": item.get("active_until", ""),
            })
    logs.sort(key=lambda item: item.get("redeemed_at", ""), reverse=True)
    return logs

_configure_sessions()


_rate_buckets = collections.defaultdict(list)
_rate_bucket_windows = {}
_rate_request_count = 0
_rate_lock = threading.Lock()
_crash_global_events = []
_crash_global_lock = threading.Lock()

def _rate_ok_bucket(bucket_key, limit, window_seconds=60):
    global _rate_request_count
    now_ts = time.time()
    with _rate_lock:
        _rate_request_count += 1
        window = [t for t in _rate_buckets.get(bucket_key, []) if now_ts - t < window_seconds]
        _rate_bucket_windows[bucket_key] = window_seconds
        if _rate_request_count % 500 == 0:
            _cleanup_rate_buckets(now_ts)
        if len(_rate_buckets) > 10000:
            oldest = sorted(
                _rate_buckets.keys(),
                key=lambda k: min(_rate_buckets.get(k) or [0])
            )[:1000]
            for key in oldest:
                _rate_buckets.pop(key, None)
                _rate_bucket_windows.pop(key, None)
        if len(window) >= limit:
            _rate_buckets[bucket_key] = window
            return False
        window.append(now_ts)
        _rate_buckets[bucket_key] = window
        return True

def _cleanup_rate_buckets(now_ts=None):
    now_ts = now_ts or time.time()
    for key in list(_rate_buckets.keys()):
        bucket_window = _rate_bucket_windows.get(key, 60)
        window = [t for t in _rate_buckets.get(key, []) if now_ts - t < bucket_window]
        if window:
            _rate_buckets[key] = window
        else:
            _rate_buckets.pop(key, None)
            _rate_bucket_windows.pop(key, None)

def _global_crash_rate_ok():
    now_ts = time.time()
    with _crash_global_lock:
        global _crash_global_events
        _crash_global_events = [t for t in _crash_global_events if now_ts - t < GLOBAL_CRASH_RATE_WINDOW]
        if len(_crash_global_events) >= GLOBAL_CRASH_RATE_LIMIT:
            return False
        _crash_global_events.append(now_ts)
        return True

def _rate_ok(ip):
    return _rate_ok_bucket(f"validate:{ip}", RATE_LIMIT, 60)

def _parse_datetime(value):
    if isinstance(value, datetime.datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(text)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)

def _is_expired(entry):
    exp = entry.get("expires", "")
    if not exp: return False
    try: return _now() > _parse_datetime(exp)
    except Exception as _e:
        app.logger.warning("Suppressed error in _is_expired: %s", _e)
        return False

def _generate_key():
    chars = string.ascii_uppercase + string.digits
    seg = lambda: "".join(secrets.choice(chars) for _ in range(4))
    return f"SUNO-{seg()}-{seg()}"

def _now():      return datetime.datetime.now(datetime.timezone.utc)
def _now_iso():  return _now().isoformat()
def _ip_banned(ip): return ip in load_cfg().get("ip_blacklist", [])

def _admin_ip_allowed(ip: str) -> bool:
    """
    Prueft ob eine IP auf der Admin-Whitelist steht.
    Gibt True zurueck wenn:
      - Whitelist deaktiviert ODER
      - Whitelist leer (noch keine Eintraege) ODER
      - IP ist in der Whitelist
    """
    cfg = load_cfg()
    if not cfg.get("admin_whitelist_enabled", False):
        return True
    whitelist = cfg.get("admin_ip_whitelist", [])
    if not whitelist:          # leere Liste = kein Lockout
        return True
    return ip in whitelist

def _definitive_client_ip():
    direct_ip = (request.remote_addr or "").strip()
    if direct_ip in TRUSTED_PROXIES:
        forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        real_ip = request.headers.get("X-Real-IP", "").strip()
        return forwarded or real_ip or direct_ip
    return direct_ip

def _log_launch(key, hwid, ip):
    try:
        _ensure_dir(LAUNCH_LOG)
        logs = _load_json(LAUNCH_LOG, [])
        logs.append({"key": key, "hwid": hwid, "ip": ip, "ts": _now_iso()})
        _save_json(LAUNCH_LOG, logs[-5000:])
    except Exception as _e:
        app.logger.warning("Suppressed error in _log_launch: %s", _e)

def _send_discord(msg):
    webhook = _cfg_secret("discord_webhook", "")
    if not webhook: return
    try:
        payload = json.dumps({"content": msg, "username": "Hextra"}).encode()
        req = urllib.request.Request(webhook, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=5)
    except Exception as _e:
        app.logger.warning("Suppressed error in _send_discord: %s", _e)


def _get_admin_name(body: dict) -> str:
    """Gibt den Admin-Username aus dem Request-Body zurueck."""
    _, admin = _current_admin()
    if admin:
        return admin.get("username", "admin")
    _, user = _current_user()
    if user:
        return user.get("username", "user")
    return body.get("username", "admin").strip() or "admin"

MAX_AUDIT_ENTRIES = 5000

def _audit(admin: str, action: str, target: str = "", details: str = "", ip: str = ""):
    """
    Schreibt einen Eintrag ins Audit-Log.
    admin   = Username des Admins
    action  = was wurde getan (z.B. 'ban_key', 'genkey', 'settings_change')
    target  = worauf (Key-ID, Reseller-ID, IP, ...)
    details = zusaetzliche Infos (Grund, Wert, ...)
    ip      = Client-IP des Admins
    """
    try:
        _ensure_dir(AUDIT_LOG)
        log = _load_json(AUDIT_LOG, [])
        log.append({
            "ts":      _now_iso(),
            "admin":   admin,
            "ip":      ip or request.remote_addr,
            "action":  action,
            "target":  target,
            "details": details
        })
        # Maximale Eintragsanzahl begrenzen (FIFO)
        if len(log) > MAX_AUDIT_ENTRIES:
            log = log[-MAX_AUDIT_ENTRIES:]
        _save_json(AUDIT_LOG, log)
    except Exception as _e:
        app.logger.warning("Suppressed error in _audit: %s", _e)


def _get_body(): return request.json or {}

def _get_key(body):
    keys = load_keys()
    key_id = body.get("key", "").strip().upper()
    if key_id not in keys: return None, key_id, None
    return keys, key_id, keys[key_id]

def _key_summary(key_id, v):
    redeemed = bool(v.get("redeemed") or v.get("redeemed_by") or v.get("account_bound"))
    return {
        "key": key_id, "type": v.get("type", "standard"), "hwid": v.get("hwid", ""),
        "banned": v.get("banned", False), "ban_reason": v.get("ban_reason", ""),
        "paused": v.get("paused", False), "pause_reason": v.get("pause_reason", ""),
        "remaining_seconds": v.get("remaining_seconds", ""),
        "activated": v.get("activated", ""), "created": v.get("created", ""),
        "note": v.get("note", ""), "expires": v.get("expires", ""),
        "days": v.get("days", ""), "expired": _is_expired(v), "tag": v.get("tag", ""),
        "reseller": v.get("reseller", ""), "reseller_name": v.get("reseller_name", ""),
        "last_reset": v.get("last_reset", ""),
        "redeemed": redeemed,
        "redeemed_at": v.get("redeemed_at", ""),
        "redeemed_by": v.get("redeemed_by", ""),
        "redeemed_by_username": v.get("redeemed_by_username", ""),
        "account_bound": bool(v.get("account_bound")),
    }


def _get_admins():
    cfg = load_cfg()
    admins = cfg.get("admins", {})
    if not admins:
        if os.environ.get("HEXTRA_BOOTSTRAP_ADMIN", "").strip().lower() not in {"1", "true", "yes"}:
            return {}
        initial_password = os.environ.get("HEXTRA_BOOTSTRAP_ADMIN_PASSWORD", "").strip() or secrets.token_urlsafe(18)
        if not os.environ.get("HEXTRA_BOOTSTRAP_ADMIN_PASSWORD", "").strip():
            print(
                "\n!!! HEXTRA INITIAL ADMIN PASSWORD GENERATED !!!\n"
                f"Username: {DEFAULT_ADMIN_USER}\n"
                f"Password: {initial_password}\n"
                "Store this password immediately. It will not be shown again.\n",
                flush=True,
            )
        admins = {
            "admin_default": {
                "username": DEFAULT_ADMIN_USER,
                "password": _hash_password(initial_password),
                "name": "Administrator",
                "created": _now_iso(),
            }
        }
        cfg["admins"] = admins
        save_cfg(cfg)
    return admins

def _save_admins(admins):
    cfg = load_cfg(); cfg["admins"] = admins; save_cfg(cfg)

def _admin_has_file_manager_access(admin):
    return bool((admin or {}).get("file_manager_access", True))

def _user_has_domain_access(user):
    return bool((user or {}).get("access_files", (user or {}).get("domain_access", False)))

def _user_has_admin_access(user):
    return bool((user or {}).get("access_admin", False))

def _user_has_reseller_access(user):
    return bool((user or {}).get("access_reseller", False))

def _user_reseller_id(user):
    return _normalize_reseller_id((user or {}).get("reseller_id", ""))

def _check_admin_file_manager():
    aid, admin = _current_admin()
    if not (aid and admin):
        return jsonify(success=False, message="Login required"), 401
    if not _admin_has_file_manager_access(admin):
        return jsonify(success=False, message="File manager access denied"), 403
    return None

def _check_file_manager_access():
    aid, admin = _current_admin()
    if aid and admin:
        if _admin_has_file_manager_access(admin):
            return {"role": "admin", "id": aid, "name": admin.get("username", "")}, None
        return None, (jsonify(success=False, message="File manager access denied"), 403)
    uid, user = _current_user()
    if uid and user:
        if _user_has_domain_access(user):
            return {
                "role": "user",
                "id": uid,
                "name": user.get("username", ""),
                "file_home_path": _user_file_home_path(user),
                "file_access_paths": _user_file_access_paths(user),
                "file_access_default_path": _user_default_file_path(user),
            }, None
        return None, (jsonify(success=False, message="Domain access denied"), 403)
    return None, (jsonify(success=False, message="Login required"), 401)

def _check_admin(body):
    client_ip = _definitive_client_ip()
    if not _admin_ip_allowed(client_ip):
        return jsonify(success=False, message=f"Access denied (IP: {client_ip})"), 403
    uid, user = _current_user()
    if uid and user and _user_has_admin_access(user):
        return None
    aid, admin = _current_admin()
    if aid and admin:
        return None
    return jsonify(success=False, message="Login session required"), 401

def _get_resellers():
    cfg = load_cfg()
    return cfg.get("resellers", {})
def _save_resellers(resellers):
    cfg = load_cfg(); cfg["resellers"] = resellers; save_cfg(cfg)

def _get_users():
    users = load_accounts()
    if not isinstance(users, dict):
        users = {}
    changed = False
    for uid, user in users.items():
        if not isinstance(user, dict):
            continue
        if "licenses" not in user or not isinstance(user.get("licenses"), list):
            user["licenses"] = []
            changed = True
        if "disabled" not in user:
            user["disabled"] = False
            changed = True
        if "banned" not in user:
            user["banned"] = False
            changed = True
        if "access_files" not in user:
            user["access_files"] = bool(user.get("domain_access", False))
            changed = True
        if "access_admin" not in user:
            user["access_admin"] = False
            changed = True
        if "access_reseller" not in user:
            user["access_reseller"] = False
            changed = True
        if "reseller_id" not in user:
            user["reseller_id"] = ""
            changed = True
        normalized_paths = _normalize_user_file_access_paths(user.get("file_access_paths", []))
        if user.get("file_access_paths", []) != normalized_paths:
            user["file_access_paths"] = normalized_paths
            changed = True
        if "domain_access" in user and user.get("domain_access") != user.get("access_files"):
            user["domain_access"] = bool(user.get("access_files"))
            changed = True
        if "email" not in user:
            user["email"] = ""
            changed = True
        if "username" in user and user.get("username", "").strip() != user.get("username", ""):
            user["username"] = user.get("username", "").strip()
            changed = True
        if "email" in user and user.get("email", "").strip().lower() != user.get("email", ""):
            user["email"] = user.get("email", "").strip().lower()
            changed = True
        normalized_home = _user_file_home_path(user)
        if user.get("file_home_path", "") != normalized_home:
            user["file_home_path"] = normalized_home
            changed = True
    if changed:
        save_accounts(users)
    return users

def _save_users(users):
    save_accounts(users)

def _run_startup_migrations():
    cfg = load_cfg()
    admins = cfg.get("admins", {})
    if not admins:
        admins = _get_admins()
        cfg = load_cfg()
    resellers = cfg.get("resellers", {})
    cfg_changed = False
    if isinstance(admins, dict) and _migrate_passwords(admins):
        cfg["admins"] = admins
        cfg_changed = True
    if isinstance(resellers, dict) and _migrate_passwords(resellers):
        cfg["resellers"] = resellers
        cfg_changed = True
    if cfg_changed:
        save_cfg(cfg)
    users = load_accounts()
    if isinstance(users, dict) and _migrate_passwords(users):
        save_accounts(users)

_run_startup_migrations()

def _normalize_reseller_id(value: str) -> str:
    return (value or "").strip().lower()

def _find_user_by_username(username: str):
    target = (username or "").strip().lower()
    if not target:
        return None, None
    users = _get_users()
    for uid, user in users.items():
        if user.get("username", "").lower() == target:
            return uid, user
    return None, None

def _find_user_by_email(email: str):
    target = (email or "").strip().lower()
    if not target:
        return None, None
    users = _get_users()
    for uid, user in users.items():
        if user.get("email", "").lower() == target:
            return uid, user
    return None, None

def _authenticate_user(body):
    uid, user = _current_user()
    if uid and user:
        return uid, user, None
    username = body.get("username", "").strip()
    password = body.get("password", "")
    if not username or not password:
        return None, None, jsonify(success=False, message="Login required")
    uid, user = _find_user_by_username(username)
    if not uid:
        return None, None, jsonify(success=False, message="Account not found")
    if user.get("banned"):
        return None, None, jsonify(success=False, message="Account banned")
    if user.get("disabled"):
        return None, None, jsonify(success=False, message="Account disabled")
    if not _password_matches(user.get("password", ""), password):
        return None, None, jsonify(success=False, message="Wrong password")
    return uid, user, None

def _bind_user_hwid(user, hwid: str, ip: str):
    current_hwid = (user.get("hwid", "") or "").strip()
    if not current_hwid:
        user["hwid"] = (hwid or "").strip()
        _remember_hwid(user, hwid)
        _remember_ip(user, ip)
        return True, "Device linked"
    if _hwid_matches(user, hwid):
        if current_hwid != (hwid or "").strip():
            user["hwid"] = (hwid or "").strip()
        _remember_hwid(user, hwid)
        _remember_ip(user, ip)
        return True, "Welcome back"
    if _can_auto_rebind(user, hwid, ip):
        _remember_hwid(user, current_hwid)
        _remember_hwid(user, hwid)
        _remember_ip(user, ip)
        user["hwid"] = (hwid or "").strip()
        normalized = _normalize_hwid(hwid)
        user["hwid_aliases"] = [normalized] if normalized else []
        user["rebind_count"] = user.get("rebind_count", 0) + 1
        return True, "Device updated"
    return False, "HWID mismatch"

def _client_token_digest(token: str) -> str:
    secret = (app.secret_key or _get_session_secret() or "").encode("utf-8")
    return hmac.new(secret, (token or "").encode("utf-8"), hashlib.sha256).hexdigest()

def _issue_client_session(user, hwid: str, ip: str):
    token = secrets.token_urlsafe(48)
    expires_at = _now() + datetime.timedelta(seconds=CLIENT_SESSION_TTL_SECONDS)
    user["client_session"] = {
        "token_hash": _client_token_digest(token),
        "created_at": _now_iso(),
        "expires_at": expires_at.isoformat(),
        "hwid": (hwid or "").strip(),
        "hwid_alias": _normalize_hwid(hwid),
        "ip": ip or "",
        "last_ip": ip or "",
        "last_seen": _now_iso(),
    }
    return token, expires_at.isoformat()

def _client_auth_token(body):
    header = (request.headers.get("Authorization") or "").strip()
    if header.lower().startswith("bearer "):
        return header.split(" ", 1)[1].strip()
    return (body.get("session_token") or body.get("token") or "").strip()

def _client_auth_username(body):
    return (
        (body.get("username") or "")
        or (request.headers.get("X-Hextra-User") or "")
        or (request.args.get("u") or "")
    ).strip()

def _client_auth_error(message="Client session required", status=401):
    return jsonify(success=False, message=message, locked=True), status

def _authenticate_client_session(body):
    username = _client_auth_username(body)
    token = _client_auth_token(body)
    hwid = (body.get("hwid") or request.headers.get("X-Hextra-HWID") or "").strip()
    if not username or not token or not _normalize_hwid(hwid):
        return None, None, _client_auth_error("Valid client session required")

    uid, user = _find_user_by_username(username)
    if not uid:
        return None, None, _client_auth_error("Account not found")
    if user.get("banned"):
        return None, None, _client_auth_error("Account banned", 403)
    if user.get("disabled"):
        return None, None, _client_auth_error("Account disabled", 403)
    if not _hwid_matches(user, hwid):
        return None, None, _client_auth_error("Device mismatch", 403)

    client_session = user.get("client_session") or {}
    expected_hash = client_session.get("token_hash", "")
    if not expected_hash or not hmac.compare_digest(expected_hash, _client_token_digest(token)):
        return None, None, _client_auth_error("Invalid or expired client session")
    session_hwid = client_session.get("hwid_alias") or _normalize_hwid(client_session.get("hwid", ""))
    if session_hwid and session_hwid != _normalize_hwid(hwid):
        return None, None, _client_auth_error("Session device mismatch", 403)
    try:
        if _now() > _parse_datetime(client_session.get("expires_at", "")):
            user.pop("client_session", None)
            users = _get_users()
            users[uid] = user
            _save_users(users)
            return None, None, _client_auth_error("Client session expired")
    except Exception:
        return None, None, _client_auth_error("Client session expired")

    client_session["last_seen"] = _now_iso()
    client_session["last_ip"] = request.remote_addr or "unknown"
    user["client_session"] = client_session
    _remember_hwid(user, hwid)
    _remember_ip(user, request.remote_addr or "unknown")
    users = _get_users()
    users[uid] = user
    _save_users(users)
    return uid, user, None

def _check_client_update_access():
    aid, admin = _current_admin()
    if aid and admin:
        return None
    uid, user = _current_user()
    if uid and user and _user_has_active_license(user):
        return None
    uid, user, err = _authenticate_client_session({})
    if err:
        return err
    if not _user_has_active_license(user):
        return jsonify(success=False, message="Active plan required", locked=True), 403
    return None

def _redeem_key_to_user(user_id, user, key_id, entry):
    now = _now()
    days = int(entry.get("days") or 0)
    current_expiry = None
    if user.get("license_expires"):
        try:
            current_expiry = _parse_datetime(user["license_expires"])
        except Exception as _e:
            app.logger.warning("Suppressed error in _redeem_key_to_user: %s", _e)
            current_expiry = None
    start_at = current_expiry if current_expiry and current_expiry > now else now
    new_expiry = start_at + datetime.timedelta(days=days) if days > 0 else start_at
    record = {
        "key": key_id,
        "days": days,
        "redeemed_at": now.isoformat(),
        "active_from": start_at.isoformat(),
        "active_until": new_expiry.isoformat(),
        "type": entry.get("type", "standard"),
    }
    licenses = user.setdefault("licenses", [])
    licenses.append(record)
    user["license_expires"] = new_expiry.isoformat()
    if not user.get("license_started"):
        user["license_started"] = now.isoformat()
    user["last_redeemed_at"] = now.isoformat()
    entry["redeemed"] = True
    entry["redeemed_at"] = now.isoformat()
    entry["redeemed_by"] = user_id
    entry["redeemed_by_username"] = user.get("username", "")
    entry["account_bound"] = True
    entry["hwid"] = ""
    entry["activated"] = ""

def _get_credit_prices():
    return load_cfg().get("credit_prices", {"1": 1, "3": 1, "7": 2, "14": 3, "30": 5, "90": 12, "365": 40})

def _check_reseller(body):
    current_rid, current_reseller = _current_reseller()
    if current_rid and current_reseller:
        requested_rid = body.get("reseller_id", "").strip().lower()
        if requested_rid and requested_rid != current_rid:
            return None, jsonify(success=False, message="Ungueltiger Account")
        return current_rid, current_reseller
    uid, user = _current_user()
    if uid and user and _user_has_reseller_access(user):
        rid = _user_reseller_id(user)
        resellers = _get_resellers()
        requested_rid = body.get("reseller_id", "").strip().lower()
        if not rid:
            return None, jsonify(success=False, message="Kein Reseller zugewiesen")
        if requested_rid and requested_rid != rid:
            return None, jsonify(success=False, message="Ungueltiger Account")
        reseller = resellers.get(rid)
        if not reseller:
            return None, jsonify(success=False, message="Reseller nicht gefunden")
        if reseller.get("disabled"):
            return None, jsonify(success=False, message="Reseller deaktiviert")
        return rid, reseller
    return None, jsonify(success=False, message="Login session required"), 401


@app.route("/auth/login", methods=["POST"])
def auth_login():
    ip = request.remote_addr or "unknown"
    if not _rate_ok_bucket(f"auth:{ip}", AUTH_RATE_LIMIT, AUTH_RATE_WINDOW):
        return jsonify(success=False, message="Too many login attempts. Try again later."), 429
    body = _get_body()
    username = body.get("username", "").strip()
    password = body.get("password", "")
    remember = bool(body.get("remember"))
    if not username or not password:
        return jsonify(success=False, message="Username und Passwort eingeben")
    admins = _get_admins()
    for aid, a in admins.items():
        if a.get("username", "").lower() == username.lower() and _password_matches(a.get("password", ""), password):
            _set_session_auth("admin", a["username"], a.get("name", "Admin"), remember=remember, admin_id=aid)
            _audit(a["username"], "admin_login", "", f"ip={request.remote_addr}")
            return jsonify(success=True, **_admin_payload(aid, a))
    resellers = _get_resellers()
    for rid, r in resellers.items():
        if r.get("username", "").lower() == username.lower():
            if not _password_matches(r.get("password", ""), password): return jsonify(success=False, message="Falsches Passwort")
            if r.get("disabled"): return jsonify(success=False, message="Account deaktiviert")
            _set_session_auth("reseller", r["username"], r.get("name", ""), remember=remember, reseller_id=rid)
            return jsonify(success=True, **_reseller_payload(rid, r))
    uid, user = _find_user_by_username(username)
    if uid:
        if user.get("banned"):
            return jsonify(success=False, message="Account banned")
        if user.get("disabled"):
            return jsonify(success=False, message="Account disabled")
        if not _password_matches(user.get("password", ""), password):
            return jsonify(success=False, message="Falsches Passwort")
        if not (_user_has_domain_access(user) or _user_has_admin_access(user) or _user_has_reseller_access(user)):
            return jsonify(success=False, message="No website access enabled")
        _set_session_auth("user", user.get("username", ""), user.get("username", ""), remember=remember, user_id=uid)
        return jsonify(success=True, **_user_payload(uid, user))
    return jsonify(success=False, message="Benutzer nicht gefunden")


@app.route("/auth/me", methods=["GET"])
def auth_me():
    aid, admin = _current_admin()
    if aid and admin:
        return jsonify(success=True, **_admin_payload(aid, admin))
    rid, reseller = _current_reseller()
    if rid and reseller:
        return jsonify(success=True, **_reseller_payload(rid, reseller))
    uid, user = _current_user()
    if uid and user:
        return jsonify(success=True, **_user_payload(uid, user))
    _clear_session_auth()
    return jsonify(success=False, message="Not authenticated"), 401


@app.route("/auth/logout", methods=["POST"])
def auth_logout():
    auth = _session_auth()
    if auth.get("role") == "admin" and auth.get("username"):
        _audit(auth.get("username"), "admin_logout", "", f"ip={request.remote_addr}")
    _clear_session_auth()
    return jsonify(success=True)


@app.route("/admin/accounts", methods=["POST"])
def admin_accounts():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    admins = _get_admins()
    return jsonify(success=True, admins=[{"id": aid, "username": a.get("username", ""), "name": a.get("name", ""), "created": a.get("created", ""), "file_manager_access": bool(a.get("file_manager_access", True))} for aid, a in admins.items()])

@app.route("/admin/account/create", methods=["POST"])
def admin_account_create():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    uname = body.get("new_username", "").strip()
    pw = body.get("new_password", "").strip()
    name = body.get("new_name", "").strip() or uname
    file_manager_access = bool(body.get("file_manager_access", True))
    if not uname or not pw: return jsonify(success=False, message="Username und Passwort erforderlich")
    admins = _get_admins()
    for a in admins.values():
        if a.get("username", "").lower() == uname.lower(): return jsonify(success=False, message="Username bereits vergeben")
    for r in _get_resellers().values():
        if r.get("username", "").lower() == uname.lower(): return jsonify(success=False, message="Username bereits vergeben (Reseller)")
    aid = "a_" + "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
    admins[aid] = {"username": uname, "password": _hash_password(pw), "name": name, "created": _now_iso(), "file_manager_access": file_manager_access}
    _save_admins(admins)
    _audit(_get_admin_name(body), "create_admin", uname)
    return jsonify(success=True, id=aid)

@app.route("/admin/account/delete", methods=["POST"])
def admin_account_delete():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    aid = body.get("id", "").strip()
    admins = _get_admins()
    if aid not in admins: return jsonify(success=False, message="Account nicht gefunden")
    if len(admins) <= 1: return jsonify(success=False, message="Letzter Admin kann nicht geloescht werden")
    del admins[aid]; _save_admins(admins)
    _audit(_get_admin_name(body), "delete_admin", aid)
    return jsonify(success=True)


@app.route("/admin/users", methods=["POST"])
def admin_users():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    users = _get_users()
    out = []
    for uid, user in users.items():
        if not isinstance(user, dict):
            continue
        out.append(_admin_user_summary(uid, user))
    out.sort(key=lambda item: ((item.get("username") or "").lower(), item.get("created", "")))
    return jsonify(success=True, users=out)

@app.route("/admin/user/create", methods=["POST"])
def admin_user_create():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    username = body.get("username", "").strip()
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")
    access_files = bool(body.get("access_files", body.get("domain_access", False)))
    access_admin = bool(body.get("access_admin", False))
    access_reseller = bool(body.get("access_reseller", False))
    reseller_id = _normalize_reseller_id(body.get("reseller_id", ""))
    file_access_paths = _normalize_user_file_access_paths(body.get("file_access_paths", []))
    create_cloud_home = bool(body.get("create_cloud_home", False))
    if not username or not password:
        return jsonify(success=False, message="Username und Passwort erforderlich")
    if _find_user_by_username(username)[0]:
        return jsonify(success=False, message="Username bereits vergeben")
    if email and _find_user_by_email(email)[0]:
        return jsonify(success=False, message="E-Mail bereits vergeben")
    for admin in _get_admins().values():
        if admin.get("username", "").lower() == username.lower():
            return jsonify(success=False, message="Username bereits vergeben")
    for reseller in _get_resellers().values():
        if reseller.get("username", "").lower() == username.lower():
            return jsonify(success=False, message="Username bereits vergeben")
    if access_reseller:
        if not reseller_id:
            return jsonify(success=False, message="Reseller-ID erforderlich")
        if reseller_id not in _get_resellers():
            return jsonify(success=False, message="Reseller-ID nicht gefunden")
    user_id = "u_" + "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
    file_home_path = ""
    if create_cloud_home:
        try:
            file_home_path = _normalize_user_cloud_path(body.get("file_home_path", ""), username, user_id)
        except ValueError:
            return jsonify(success=False, message="Cloud-Ordner ist ungueltig")
        _ensure_manager_folder(file_home_path)
        access_files = True
        file_access_paths = [file_home_path]
    users = _get_users()
    users[user_id] = {
        "username": username,
        "email": email,
        "password": _hash_password(password),
        "created": _now_iso(),
        "licenses": [],
        "disabled": False,
        "banned": False,
        "domain_access": access_files,
        "access_files": access_files,
        "access_admin": access_admin,
        "access_reseller": access_reseller,
        "reseller_id": reseller_id,
        "file_home_path": file_home_path,
        "file_access_paths": file_access_paths,
        "hwid": "",
    }
    _save_users(users)
    _audit(_get_admin_name(body), "create_user", username, f"files={access_files};admin={access_admin};reseller={access_reseller};reseller_id={reseller_id};home={file_home_path or '-'};paths={','.join(file_access_paths) if file_access_paths else '*'}")
    return jsonify(success=True, id=user_id, user=_admin_user_summary(user_id, users[user_id]))

@app.route("/admin/user/delete", methods=["POST"])
def admin_user_delete():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    user_id = body.get("id", "").strip()
    users = _get_users()
    if user_id not in users:
        return jsonify(success=False, message="User account not found")
    username = users[user_id].get("username", user_id)
    del users[user_id]
    _save_users(users)
    _audit(_get_admin_name(body), "delete_user", username)
    return jsonify(success=True)


@app.route("/admin/redeem-logs", methods=["POST"])
def admin_redeem_logs():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    return jsonify(success=True, logs=_admin_redeem_logs())


@app.route("/admin/user/change-password", methods=["POST"])
def admin_user_change_password():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    user_id = body.get("id", "").strip()
    new_pw = body.get("new_password", "").strip()
    if not user_id or not new_pw:
        return jsonify(success=False, message="ID und neues Passwort erforderlich")
    users = _get_users()
    if user_id not in users:
        return jsonify(success=False, message="User account not found")
    users[user_id]["password"] = _hash_password(new_pw)
    _save_users(users)
    _audit(_get_admin_name(body), "change_user_password", users[user_id].get("username", user_id))
    return jsonify(success=True)


@app.route("/admin/user/toggle-disabled", methods=["POST"])
def admin_user_toggle_disabled():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    user_id = body.get("id", "").strip()
    users = _get_users()
    if user_id not in users:
        return jsonify(success=False, message="User account not found")
    users[user_id]["disabled"] = bool(body.get("disabled"))
    _save_users(users)
    action = "disable_user" if users[user_id]["disabled"] else "enable_user"
    _audit(_get_admin_name(body), action, users[user_id].get("username", user_id))
    return jsonify(success=True, disabled=users[user_id]["disabled"])


@app.route("/admin/user/toggle-banned", methods=["POST"])
def admin_user_toggle_banned():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    user_id = body.get("id", "").strip()
    users = _get_users()
    if user_id not in users:
        return jsonify(success=False, message="User account not found")
    users[user_id]["banned"] = bool(body.get("banned"))
    _save_users(users)
    action = "ban_user" if users[user_id]["banned"] else "unban_user"
    _audit(_get_admin_name(body), action, users[user_id].get("username", user_id))
    return jsonify(success=True, banned=users[user_id]["banned"])


@app.route("/admin/user/reset-hwid", methods=["POST"])
def admin_user_reset_hwid():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    user_id = body.get("id", "").strip()
    users = _get_users()
    if user_id not in users:
        return jsonify(success=False, message="User account not found")
    user = users[user_id]
    user["hwid"] = ""
    user["rebind_count"] = 0
    user["last_ip"] = ""
    _save_users(users)
    _audit(_get_admin_name(body), "reset_user_hwid", user.get("username", user_id))
    return jsonify(success=True)


@app.route("/admin/user/adjust-days", methods=["POST"])
def admin_user_adjust_days():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    user_id = body.get("id", "").strip()
    delta = int(body.get("days", 0) or 0)
    reason = body.get("reason", "").strip()
    if not user_id or delta == 0:
        return jsonify(success=False, message="User ID und Tage erforderlich")
    users = _get_users()
    if user_id not in users:
        return jsonify(success=False, message="User account not found")
    user = users[user_id]
    now = _now()
    current_expiry = None
    if user.get("license_expires"):
        try:
            current_expiry = _parse_datetime(user["license_expires"])
        except Exception as _e:
            app.logger.warning("Suppressed error in admin_user_adjust_days: %s", _e)
            current_expiry = None
    start_at = current_expiry if current_expiry and current_expiry > now else now
    new_expiry = start_at + datetime.timedelta(days=delta)
    user["license_expires"] = new_expiry.isoformat() if new_expiry > now else ""
    record = {
        "key": "ADMIN-ADJUST",
        "days": delta,
        "redeemed_at": now.isoformat(),
        "active_from": start_at.isoformat(),
        "active_until": user.get("license_expires", ""),
        "type": "manual",
        "reason": reason,
        "admin": _get_admin_name(body),
    }
    user.setdefault("licenses", []).append(record)
    user["last_redeemed_at"] = now.isoformat()
    users[user_id] = user
    _save_users(users)
    _audit(_get_admin_name(body), "adjust_user_days", user.get("username", user_id), f"days={delta}; reason={reason}")
    return jsonify(success=True, user=_admin_user_summary(user_id, user))

@app.route("/admin/user/access", methods=["POST"])
def admin_user_access():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    user_id = body.get("id", "").strip()
    users = _get_users()
    if user_id not in users:
        return jsonify(success=False, message="User account not found")
    access_files = bool(body.get("access_files", users[user_id].get("access_files", users[user_id].get("domain_access", False))))
    access_admin = bool(body.get("access_admin", users[user_id].get("access_admin", False)))
    access_reseller = bool(body.get("access_reseller", users[user_id].get("access_reseller", False)))
    reseller_id = _normalize_reseller_id(body.get("reseller_id", users[user_id].get("reseller_id", "")))
    file_access_paths = _normalize_user_file_access_paths(body.get("file_access_paths", users[user_id].get("file_access_paths", [])))
    if access_reseller:
        if not reseller_id:
            return jsonify(success=False, message="Reseller-ID erforderlich")
        if reseller_id not in _get_resellers():
            return jsonify(success=False, message="Reseller-ID nicht gefunden")
    else:
        reseller_id = ""
    users[user_id]["domain_access"] = access_files
    users[user_id]["access_files"] = access_files
    users[user_id]["access_admin"] = access_admin
    users[user_id]["access_reseller"] = access_reseller
    users[user_id]["reseller_id"] = reseller_id
    users[user_id]["file_access_paths"] = file_access_paths
    _save_users(users)
    _audit(_get_admin_name(body), "user_access", users[user_id].get("username", user_id), f"files={access_files};admin={access_admin};reseller={access_reseller};reseller_id={reseller_id};home={users[user_id].get('file_home_path', '') or '-'};paths={','.join(file_access_paths) if file_access_paths else '*'}")
    return jsonify(success=True, user=_admin_user_summary(user_id, users[user_id]))

@app.route("/admin/user/cloud-home", methods=["POST"])
def admin_user_cloud_home():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    user_id = body.get("id", "").strip()
    users = _get_users()
    if user_id not in users:
        return jsonify(success=False, message="User account not found")
    user = users[user_id]
    try:
        home_path = _normalize_user_cloud_path(body.get("file_home_path", ""), user.get("username", ""), user_id)
    except ValueError:
        return jsonify(success=False, message="Cloud-Ordner ist ungueltig")
    home_path = _ensure_manager_folder(home_path)
    user["domain_access"] = True
    user["access_files"] = True
    user["file_home_path"] = home_path
    user["file_access_paths"] = [home_path]
    users[user_id] = user
    _save_users(users)
    _audit(_get_admin_name(body), "user_cloud_home", user.get("username", user_id), home_path)
    return jsonify(success=True, user=_admin_user_summary(user_id, user))


@app.route("/validate", methods=["POST"])
def validate():
    if os.environ.get("HEXTRA_ENABLE_LEGACY_VALIDATE", "").strip().lower() not in {"1", "true", "yes"}:
        return jsonify(success=False, message="Legacy key validation disabled"), 410
    ip = request.remote_addr
    if _ip_banned(ip): return jsonify(success=False, message="Access denied")
    if not _rate_ok(ip): return jsonify(success=False, message="Too many attempts. Try again later.")
    cfg = load_cfg()
    if cfg.get("maintenance"): return jsonify(success=False, message=cfg.get("maintenance_msg", "Server in Wartung. Bitte warte."))
    body = _get_body()
    key_id = body.get("key", "").strip().upper()
    hwid = body.get("hwid", "")
    keys = load_keys()
    if key_id not in keys: return jsonify(success=False, message="Invalid key")
    entry = keys[key_id]
    if entry.get("redeemed") or entry.get("redeemed_by"):
        return jsonify(success=False, message="Key already redeemed to an account")
    if entry.get("banned"):
        r = f": {entry['ban_reason']}" if entry.get("ban_reason") else ""
        return jsonify(success=False, message=f"Key is banned{r}")
    if entry.get("paused"):
        r = f": {entry['pause_reason']}" if entry.get("pause_reason") else ""
        return jsonify(success=False, message=f"Key is paused{r}")
    if _is_expired(entry): return jsonify(success=False, message="Key expired")
    motd = cfg.get("motd", "")
    if not entry.get("hwid"):
        now = _now(); entry["hwid"] = hwid; entry["activated"] = now.isoformat()
        _remember_hwid(entry, hwid)
        _remember_ip(entry, ip)
        if entry.get("days") and not entry.get("expires"):
            try: entry["expires"] = (now + datetime.timedelta(days=int(entry["days"]))).isoformat()
            except Exception as _e:
                app.logger.warning("Suppressed error in validate: %s", _e)
        save_keys(keys); _log_launch(key_id, hwid, ip)
        _send_discord(f"ðŸ”‘ **Neue Aktivierung!**\nKey: `{key_id}`\nHWID: `{hwid}`\nIP: `{ip}`\nTag: `{entry.get('tag', '-')}`")
        return jsonify(success=True, message="Key activated", type=entry.get("type", "standard"), expires=entry.get("expires", ""), motd=motd)
    if _hwid_matches(entry, hwid):
        _remember_hwid(entry, hwid)
        _remember_ip(entry, ip)
        if entry.get("hwid") != hwid:
            entry["hwid"] = hwid
            save_keys(keys)
        _log_launch(key_id, hwid, ip)
        return jsonify(success=True, message="Welcome back", type=entry.get("type", "standard"), expires=entry.get("expires", ""), motd=motd)
    if _can_auto_rebind(entry, hwid, ip):
        _remember_hwid(entry, entry.get("hwid", ""))
        _remember_hwid(entry, hwid)
        _remember_ip(entry, ip)
        entry["hwid"] = hwid
        normalized = _normalize_hwid(hwid)
        entry["hwid_aliases"] = [normalized] if normalized else []
        entry["rebind_count"] = entry.get("rebind_count", 0) + 1
        save_keys(keys)
        _log_launch(key_id, hwid, ip)
        return jsonify(success=True, message="HWID updated", type=entry.get("type", "standard"), expires=entry.get("expires", ""), motd=motd, hwid_rebound=True)
    return jsonify(success=False, message="HWID mismatch")


@app.route("/client/register", methods=["POST"])
def client_register():
    ip = request.remote_addr or "unknown"
    if not _rate_ok_bucket(f"client-register:{ip}", AUTH_RATE_LIMIT, AUTH_RATE_WINDOW):
        return jsonify(success=False, message="Too many attempts. Try again later."), 429
    body = _get_body()
    username = body.get("username", "").strip()
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")
    hwid = body.get("hwid", "")
    remember = bool(body.get("remember"))
    if not _normalize_hwid(hwid):
        return jsonify(success=False, message="Device identity required")
    if len(username) < 3 or not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        return jsonify(success=False, message="Username must be 3-32 characters")
    if not email or "@" not in email or len(email) > 120:
        return jsonify(success=False, message="Valid email required")
    if not password or len(password) < 4:
        return jsonify(success=False, message="Password too short")
    if _find_user_by_username(username)[0]:
        return jsonify(success=False, message="Username already taken")
    if _find_user_by_email(email)[0]:
        return jsonify(success=False, message="Email already in use")
    user_id = "u_" + "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(10))
    user = {
        "username": username,
        "email": email,
        "password": _hash_password(password),
        "created": _now_iso(),
        "disabled": False,
        "banned": False,
        "licenses": [],
        "license_expires": "",
        "hwid": "",
        "hwid_history": [],
        "hwid_aliases": [],
        "ip_history": [],
        "rebind_count": 0,
    }
    ok, message = _bind_user_hwid(user, hwid, ip)
    if not ok:
        return jsonify(success=False, message=message)
    users = _get_users()
    users[user_id] = user
    session_token, session_expires = _issue_client_session(user, hwid, ip)
    users[user_id] = user
    _save_users(users)
    _set_session_auth("user", username, username, remember=remember, user_id=user_id)
    return jsonify(success=True, message="Account created", **_user_payload(user_id, user), session_token=session_token, session_expires=session_expires, motd=load_cfg().get("motd", ""))


@app.route("/client/login", methods=["POST"])
def client_login():
    ip = request.remote_addr or "unknown"
    if not _rate_ok_bucket(f"client-login:{ip}", AUTH_RATE_LIMIT, AUTH_RATE_WINDOW):
        return jsonify(success=False, message="Too many attempts. Try again later."), 429
    body = _get_body()
    uid, user, err = _authenticate_user(body)
    if err:
        return err
    hwid = body.get("hwid", "")
    if not _normalize_hwid(hwid):
        return jsonify(success=False, message="Device identity required")
    ok, message = _bind_user_hwid(user, hwid, ip)
    if not ok:
        return jsonify(success=False, message=message)
    users = _get_users()
    session_token, session_expires = _issue_client_session(user, hwid, ip)
    users[uid] = user
    _save_users(users)
    _set_session_auth("user", user.get("username", ""), user.get("username", ""), remember=bool(body.get("remember")), user_id=uid)
    return jsonify(success=True, message=message, **_user_payload(uid, user), session_token=session_token, session_expires=session_expires, motd=load_cfg().get("motd", ""))


@app.route("/client/status", methods=["POST"])
def client_status():
    body = _get_body()
    uid, user, err = _authenticate_client_session(body)
    if err:
        return err
    return jsonify(success=True, message="Session valid", **_user_payload(uid, user), motd=load_cfg().get("motd", ""))


@app.route("/client/redeem", methods=["POST"])
def client_redeem():
    body = _get_body()
    uid, user, err = _authenticate_client_session(body)
    if err:
        return err
    key_id = body.get("key", "").strip().upper()
    if not key_id:
        return jsonify(success=False, message="Key required")
    keys = load_keys()
    if key_id not in keys:
        return jsonify(success=False, message="Invalid key")
    entry = keys[key_id]
    if entry.get("redeemed") or entry.get("redeemed_by"):
        return jsonify(success=False, message="Key already redeemed")
    if entry.get("hwid") or entry.get("activated"):
        return jsonify(success=False, message="Key already used")
    if entry.get("banned"):
        return jsonify(success=False, message="Key is banned")
    if entry.get("paused"):
        return jsonify(success=False, message="Key is paused")
    if _is_expired(entry):
        return jsonify(success=False, message="Key expired")
    redeem_days = entry.get("days", 0)
    _redeem_key_to_user(uid, user, key_id, entry)
    users = _get_users()
    users[uid] = user
    _save_users(users)
    keys.pop(key_id, None)
    save_keys(keys)
    _audit(user.get("username", ""), "redeem_key", key_id, f"days={redeem_days}", request.remote_addr)
    return jsonify(success=True, message="Key redeemed", **_user_payload(uid, user))


@app.route("/keys", methods=["POST"])
def list_keys():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    keys = load_keys()
    out = [_key_summary(k, v) for k, v in keys.items()]
    stats = {"total": len(keys), "free": sum(1 for v in keys.values() if not v.get("hwid")), "bound": sum(1 for v in keys.values() if v.get("hwid")), "banned": sum(1 for v in keys.values() if v.get("banned"))}
    return jsonify(success=True, keys=out, stats=stats)

@app.route("/genkey", methods=["POST"])
def gen_key():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    keys = load_keys(); count = min(int(body.get("count", 1)), 50)
    reseller_id = _normalize_reseller_id(body.get("reseller", ""))
    reseller_name = ""
    if reseller_id:
        resellers = _get_resellers()
        if reseller_id not in resellers:
            return jsonify(success=False, message="Reseller not found")
        reseller_name = resellers[reseller_id].get("name", reseller_id)
    days_val = ""
    if body.get("days"):
        try: days_val = int(body["days"])
        except Exception as _e:
            app.logger.warning("Suppressed error in gen_key: %s", _e)
    new_keys = []
    for _ in range(count):
        k = _generate_key()
        while k in keys: k = _generate_key()
        entry = {"type": body.get("type", "standard"), "hwid": "", "created": _now_iso(), "note": body.get("note", ""), "banned": False, "expires": "", "days": days_val, "tag": body.get("tag", "")}
        if reseller_id:
            entry["reseller"] = reseller_id
            entry["reseller_name"] = reseller_name
        keys[k] = entry
        new_keys.append(k)
    save_keys(keys)
    _audit(_get_admin_name(body), "genkey", ", ".join(new_keys), f"count={count} type={body.get('type','standard')} days={days_val} reseller={reseller_id} tag={body.get('tag','')} note={body.get('note','')}")
    return jsonify(success=True, keys=new_keys)

@app.route("/delkey", methods=["POST"])
def del_key():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    keys, key_id, entry = _get_key(body)
    if not entry: return jsonify(success=False, message="Key not found")
    del keys[key_id]; save_keys(keys)
    _audit(_get_admin_name(body), "delete_key", key_id)
    return jsonify(success=True)

@app.route("/resetkey", methods=["POST"])
def reset_key():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    keys, key_id, entry = _get_key(body)
    if not entry: return jsonify(success=False, message="Key not found")
    entry["hwid"] = ""; entry["activated"] = ""; save_keys(keys)
    _audit(_get_admin_name(body), "reset_hwid", key_id)
    return jsonify(success=True)

@app.route("/bankey", methods=["POST"])
def ban_key():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    keys, key_id, entry = _get_key(body)
    if not entry: return jsonify(success=False, message=f"Key not found: [{key_id}]")
    entry["banned"] = not entry.get("banned", False)
    if entry["banned"]:
        entry["ban_reason"] = body.get("reason", "")
        if body.get("freeze"):
            if entry.get("expires"):
                try:
                    exp = _parse_datetime(entry["expires"])
                    secs = max(0, int((exp - _now()).total_seconds()))
                    entry["frozen_expires"] = f"seconds:{secs}"
                    entry["expires"] = ""
                    entry["remaining_seconds"] = str(secs)  # FIX: behalte countdown sichtbar
                except Exception as _e:
                    app.logger.warning("Suppressed error in ban_key: %s", _e)
            elif entry.get("remaining_seconds"):
                entry["frozen_expires"] = f"seconds:{entry['remaining_seconds']}"
                # FIX: remaining_seconds NICHT loeschen, damit countdown sichtbar bleibt
    else:
        entry["ban_reason"] = ""
        entry["remaining_seconds"] = ""
        fe = entry.get("frozen_expires", "")
        if fe:
            if fe.startswith("seconds:") or fe.startswith("paused:"):
                secs = int(fe.split(":")[1])
                entry["expires"] = (_now() + datetime.timedelta(seconds=secs)).isoformat()
                entry["remaining_seconds"] = ""
            else:
                try:
                    exp = _parse_datetime(fe)
                    secs = max(0, int((exp - _now()).total_seconds()))
                    entry["expires"] = (_now() + datetime.timedelta(seconds=secs)).isoformat()
                except Exception as _e:
                    app.logger.warning("Suppressed error in ban_key: %s", _e)
                    entry["expires"] = fe
            entry["frozen_expires"] = ""
    save_keys(keys)
    _audit(_get_admin_name(body), "ban_key" if entry["banned"] else "unban_key", key_id, f"reason={body.get('reason','')}")
    return jsonify(success=True, banned=entry["banned"], expires=entry.get("expires", ""), remaining_seconds=entry.get("remaining_seconds", ""))

@app.route("/pausekey", methods=["POST"])
def pause_key():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    keys, key_id, entry = _get_key(body)
    if not entry: return jsonify(success=False, message=f"Key not found: [{key_id}]")
    entry["paused"] = not entry.get("paused", False)
    if entry["paused"]:
        entry["pause_reason"] = body.get("reason", "")
        if entry.get("expires"):
            try:
                exp = _parse_datetime(entry["expires"])
                entry["remaining_seconds"] = max(0, int((exp - _now()).total_seconds()))
                entry["frozen_expires_pause"] = entry["expires"]; entry["expires"] = ""
            except Exception as _e:
                app.logger.warning("Suppressed error in pause_key: %s", _e)
    else:
        entry["pause_reason"] = ""
        if entry.get("remaining_seconds"):
            try:
                entry["expires"] = (_now() + datetime.timedelta(seconds=int(entry["remaining_seconds"]))).isoformat()
                entry["remaining_seconds"] = ""; entry["frozen_expires_pause"] = ""
            except Exception as _e:
                app.logger.warning("Suppressed error in pause_key: %s", _e)
        else:
            entry["frozen_expires_pause"] = ""
    save_keys(keys)
    _audit(_get_admin_name(body), "pause_key" if entry["paused"] else "unpause_key", key_id, f"reason={body.get('reason','')}")
    return jsonify(success=True, paused=entry["paused"], remaining_seconds=entry.get("remaining_seconds", ""), expires=entry.get("expires", ""))

@app.route("/editkey", methods=["POST"])
def edit_key():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    keys, key_id, entry = _get_key(body)
    if not entry: return jsonify(success=False, message="Key not found")
    for field in ("note", "type", "tag"):
        if field in body: entry[field] = body[field]
    if "days" in body:
        try:
            days = int(body["days"])
            if days == 0: entry["expires"] = ""; entry["days"] = ""
            else:
                entry["days"] = days
                if entry.get("activated"):
                    base = _parse_datetime(entry["activated"])
                    entry["expires"] = (base + datetime.timedelta(days=days)).isoformat()
                else: entry["expires"] = ""
        except Exception as _e:
            app.logger.warning("Suppressed error in edit_key: %s", _e)
    if "expires" in body: entry["expires"] = body["expires"]
    if "reseller" in body:
        rid = body["reseller"].strip()
        if rid == "":
            entry["reseller"] = ""; entry["reseller_name"] = ""
        else:
            _res = _get_resellers()
            if rid in _res:
                entry["reseller"] = rid
                entry["reseller_name"] = _res[rid].get("name", rid)
    save_keys(keys)
    changed = [f for f in ("note","type","tag","days","expires","reseller") if f in body]
    _audit(_get_admin_name(body), "edit_key", key_id, f"changed={','.join(changed)}")
    return jsonify(success=True)

@app.route("/export/csv", methods=["POST"])
def export_csv():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    def esc(val): return '"' + str(val).replace('"', "'") + '"'
    keys = load_keys()
    rows = ["Key,Type,Tag,HWID,Activated,Created,Expires,Banned,Note,Reseller"]
    for k, v in keys.items():
        rows.append(",".join([esc(k), esc(v.get("type","")), esc(v.get("tag","")), esc(v.get("hwid","")), esc(v.get("activated","")), esc(v.get("created","")), esc(v.get("expires","")), esc(v.get("banned",False)), esc(v.get("note","")), esc(v.get("reseller_name",""))]))
    return Response("\n".join(rows), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=hextra_keys.csv"})


@app.route("/motd", methods=["GET"])
def get_motd():
    cfg = load_cfg(); return jsonify(motd=cfg.get("motd", ""), maintenance=cfg.get("maintenance", False))

@app.route("/settings/save", methods=["POST"])
def settings_save():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    cfg = load_cfg()
    for field in ("motd", "maintenance_msg"):
        if field in body: cfg[field] = body[field]
    if "discord_webhook" in body and not _has_env_secret("discord_webhook"):
        cfg["discord_webhook"] = body["discord_webhook"]
    if "maintenance" in body: cfg["maintenance"] = bool(body["maintenance"])
    save_cfg(cfg)
    changed = [f for f in ("motd","maintenance","maintenance_msg") if f in body]
    if "discord_webhook" in body:
        changed.append("discord_webhook_env_locked" if _has_env_secret("discord_webhook") else "discord_webhook")
    _audit(_get_admin_name(body), "settings_change", "", f"fields={','.join(changed)}")
    return jsonify(success=True)

@app.route("/settings/get", methods=["POST"])
def settings_get():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    cfg = load_cfg()
    return jsonify(success=True, motd=cfg.get("motd", ""), maintenance=cfg.get("maintenance", False), maintenance_msg=cfg.get("maintenance_msg", ""), discord_webhook="" if _has_env_secret("discord_webhook") else cfg.get("discord_webhook", ""), discord_webhook_managed=_has_env_secret("discord_webhook"), ip_blacklist=cfg.get("ip_blacklist", []), admin_whitelist_enabled=cfg.get("admin_whitelist_enabled", False), admin_ip_whitelist=cfg.get("admin_ip_whitelist", []))

@app.route("/ip/ban", methods=["POST"])
def ip_ban():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    cfg = load_cfg(); bl = cfg.get("ip_blacklist", []); ip = body.get("ip", "").strip()
    if not ip: return jsonify(success=False, message="No IP provided")
    if ip not in bl: bl.append(ip)
    cfg["ip_blacklist"] = bl; save_cfg(cfg)
    _audit(_get_admin_name(body), "ip_ban", ip)
    return jsonify(success=True, blacklist=bl)

@app.route("/ip/unban", methods=["POST"])
def ip_unban():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    cfg = load_cfg(); ip = body.get("ip", "").strip()
    cfg["ip_blacklist"] = [x for x in cfg.get("ip_blacklist", []) if x != ip]
    save_cfg(cfg)
    _audit(_get_admin_name(body), "ip_unban", body.get("ip","").strip())
    return jsonify(success=True, blacklist=cfg["ip_blacklist"])


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  ADMIN IP WHITELIST
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@app.route("/admin/whitelist/get", methods=["POST"])
def admin_whitelist_get():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    cfg = load_cfg()
    return jsonify(
        success=True,
        enabled=cfg.get("admin_whitelist_enabled", False),
        whitelist=cfg.get("admin_ip_whitelist", []),
        your_ip=request.remote_addr
    )

@app.route("/admin/whitelist/toggle", methods=["POST"])
def admin_whitelist_toggle():
    """Aktiviert oder deaktiviert die Whitelist.
    Sicherheit: Aktivierung nur moeglich wenn eigene IP bereits in der Liste steht."""
    body = _get_body(); err = _check_admin(body)
    if err: return err
    cfg = load_cfg()
    enable = bool(body.get("enabled", False))
    if enable:
        whitelist = cfg.get("admin_ip_whitelist", [])
        my_ip = request.remote_addr
        if not whitelist:
            return jsonify(success=False,
                message="Whitelist ist leer. Fuege zuerst deine IP hinzu, sonst sperrst du dich aus.")
        if my_ip not in whitelist:
            return jsonify(success=False,
                message=f"Deine IP ({my_ip}) ist nicht in der Whitelist. Fuege sie zuerst hinzu.")
    cfg["admin_whitelist_enabled"] = enable
    save_cfg(cfg)
    _audit(_get_admin_name(body), "whitelist_toggle", "", f"enabled={enable}")
    return jsonify(success=True, enabled=enable)

@app.route("/admin/whitelist/add", methods=["POST"])
def admin_whitelist_add():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    cfg  = load_cfg()
    ip   = body.get("ip", "").strip()
    # Wenn kein IP angegeben, eigene IP hinzufuegen
    if not ip:
        ip = request.remote_addr
    if not ip:
        return jsonify(success=False, message="Keine IP angegeben")
    wl = cfg.get("admin_ip_whitelist", [])
    if ip not in wl:
        wl.append(ip)
    cfg["admin_ip_whitelist"] = wl
    save_cfg(cfg)
    _audit(_get_admin_name(body), "whitelist_add", ip)
    return jsonify(success=True, whitelist=wl, added=ip)

@app.route("/admin/whitelist/remove", methods=["POST"])
def admin_whitelist_remove():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    cfg = load_cfg()
    ip  = body.get("ip", "").strip()
    if not ip:
        return jsonify(success=False, message="Keine IP angegeben")
    # Schutz: eigene IP nicht entfernen wenn Whitelist aktiv ist
    if cfg.get("admin_whitelist_enabled", False) and ip == request.remote_addr:
        return jsonify(success=False,
            message="Du kannst deine eigene IP nicht entfernen waehrend die Whitelist aktiv ist.")
    wl = [x for x in cfg.get("admin_ip_whitelist", []) if x != ip]
    cfg["admin_ip_whitelist"] = wl
    save_cfg(cfg)
    _audit(_get_admin_name(body), "whitelist_remove", ip)
    return jsonify(success=True, whitelist=wl, removed=ip)

@app.route("/admin/whitelist/myip", methods=["POST"])
def admin_whitelist_myip():
    """Gibt die eigene IP zurueck â€” kein Auth noetig, hilfreich fuer Setup."""
    return jsonify(success=True, ip=request.remote_addr)



@app.route("/admin/resellers", methods=["POST"])
def admin_resellers():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    resellers = _get_resellers(); keys = load_keys(); out = []
    for rid, r in resellers.items():
        kc = sum(1 for v in keys.values() if v.get("reseller") == rid)
        ac = sum(1 for v in keys.values() if v.get("reseller") == rid and v.get("hwid"))
        out.append({"id": rid, "name": r.get("name",""), "username": r.get("username",""), "credits": r.get("credits",0), "total_generated": r.get("total_generated",0), "keys_active": ac, "keys_total": kc, "created": r.get("created",""), "disabled": r.get("disabled",False)})
    return jsonify(success=True, resellers=out, credit_prices=_get_credit_prices())

@app.route("/admin/reseller/create", methods=["POST"])
def admin_reseller_create():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    rid = body.get("id","").strip().lower()
    name = body.get("name","").strip()
    username = body.get("r_username","").strip()
    password = body.get("r_password","").strip()
    if not rid or not name or not password or not username:
        return jsonify(success=False, message="ID, Name, Username und Passwort erforderlich")
    if not re.fullmatch(r"[a-z0-9_-]{3,32}", rid):
        return jsonify(success=False, message="ID nur mit a-z, 0-9, - und _ (3-32 Zeichen)")
    resellers = _get_resellers(); admins = _get_admins()
    if rid in resellers:
        return jsonify(success=False, message="Reseller-ID bereits vergeben")
    for r in resellers.values():
        if r.get("username","").lower() == username.lower():
            return jsonify(success=False, message="Username bereits vergeben")
    for a in admins.values():
        if a.get("username","").lower() == username.lower():
            return jsonify(success=False, message="Username bereits vergeben (Admin)")
    resellers[rid] = {
        "id": rid,
        "name": name,
        "username": username,
        "password": _hash_password(password),
        "credits": int(body.get("credits",0)),
        "total_generated": 0,
        "created": _now_iso(),
        "disabled": False
    }
    _save_resellers(resellers)
    _send_discord(f"ðŸª **Neuer Reseller!**\nName: `{name}`\nUsername: `{username}`\nID: `{rid}`\nShop: `/shop/{rid}`")
    return jsonify(success=True, id=rid, shop_path=f"/shop/{rid}")

@app.route("/admin/reseller/edit", methods=["POST"])
def admin_reseller_edit():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    rid = body.get("id","").strip(); resellers = _get_resellers()
    if rid not in resellers: return jsonify(success=False, message="Reseller not found")
    r = resellers[rid]
    if "name" in body: r["name"] = body["name"]
    if "r_username" in body and body["r_username"]:
        nu = body["r_username"].strip()
        for orid, orr in resellers.items():
            if orid != rid and orr.get("username","").lower() == nu.lower(): return jsonify(success=False, message="Username bereits vergeben")
        for a in _get_admins().values():
            if a.get("username","").lower() == nu.lower(): return jsonify(success=False, message="Username bereits vergeben (Admin)")
        r["username"] = nu
    if "r_password" in body and body["r_password"]: r["password"] = _hash_password(body["r_password"])
    if "disabled" in body: r["disabled"] = bool(body["disabled"])
    _save_resellers(resellers); return jsonify(success=True)

@app.route("/admin/reseller/credits", methods=["POST"])
def admin_reseller_credits():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    rid = body.get("id","").strip(); resellers = _get_resellers()
    if rid not in resellers: return jsonify(success=False, message="Reseller not found")
    amount = int(body.get("amount",0)); mode = body.get("mode","add")
    if mode == "set": resellers[rid]["credits"] = max(0, amount)
    else: resellers[rid]["credits"] = max(0, resellers[rid].get("credits",0) + amount)
    _save_resellers(resellers); return jsonify(success=True, credits=resellers[rid]["credits"])

@app.route("/admin/reseller/delete", methods=["POST"])
def admin_reseller_delete():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    rid = body.get("id","").strip(); resellers = _get_resellers()
    if rid not in resellers: return jsonify(success=False, message="Reseller not found")
    del resellers[rid]; _save_resellers(resellers); return jsonify(success=True)

@app.route("/admin/reseller/prices", methods=["POST"])
def admin_reseller_prices():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    prices = body.get("prices",{}); cfg = load_cfg()
    cfg["credit_prices"] = {str(k): int(v) for k, v in prices.items()}
    save_cfg(cfg); return jsonify(success=True)


@app.route("/reseller/login", methods=["POST"])
def reseller_login():
    ip = request.remote_addr or "unknown"
    if not _rate_ok_bucket(f"reseller-login:{ip}", AUTH_RATE_LIMIT, AUTH_RATE_WINDOW):
        return jsonify(success=False, message="Zu viele Login-Versuche. Bitte spaeter erneut versuchen."), 429
    body = _get_body(); rid, result = _check_reseller(body)
    if rid is None: return result
    if not _current_reseller()[0]:
        _set_session_auth("reseller", result.get("username", ""), result.get("name", ""), remember=bool(body.get("remember")), reseller_id=rid)
    return jsonify(success=True, id=rid, name=result["name"], username=result.get("username",""), credits=result.get("credits",0))

@app.route("/reseller/info", methods=["POST"])
def reseller_info():
    body = _get_body(); rid, result = _check_reseller(body)
    if rid is None: return result
    keys = load_keys(); my_keys = {k: v for k, v in keys.items() if v.get("reseller") == rid}
    active = sum(1 for v in my_keys.values() if v.get("hwid")); free = sum(1 for v in my_keys.values() if not v.get("hwid"))
    return jsonify(success=True, name=result["name"], username=result.get("username",""), credits=result.get("credits",0), total_generated=result.get("total_generated",0), keys_total=len(my_keys), keys_active=active, keys_free=free, credit_prices=_get_credit_prices())

@app.route("/reseller/keys", methods=["POST"])
def reseller_keys():
    body = _get_body(); rid, result = _check_reseller(body)
    if rid is None: return result
    keys = load_keys()
    return jsonify(success=True, keys=[_key_summary(k, v) for k, v in keys.items() if v.get("reseller") == rid])

@app.route("/reseller/genkey", methods=["POST"])
def reseller_genkey():
    body = _get_body(); rid, rdata = _check_reseller(body)
    if rid is None: return rdata
    count = min(int(body.get("count",1)), 20); days = int(body.get("days",7))
    prices = _get_credit_prices(); cost_per_key = prices.get(str(days), None)
    if cost_per_key is None: return jsonify(success=False, message=f"Laufzeit {days} Tage nicht verfuegbar")
    total_cost = cost_per_key * count
    if rdata.get("credits",0) < total_cost: return jsonify(success=False, message=f"Nicht genug Credits ({rdata.get('credits',0)} vorhanden, {total_cost} benoetigt)")
    keys = load_keys(); new_keys = []
    for _ in range(count):
        k = _generate_key()
        while k in keys: k = _generate_key()
        keys[k] = {"type": body.get("type","standard"), "hwid": "", "created": _now_iso(), "note": body.get("note",""), "banned": False, "expires": "", "days": days, "tag": f"reseller:{rdata['name']}", "reseller": rid, "reseller_name": rdata["name"]}
        new_keys.append(k)
    save_keys(keys)
    resellers = _get_resellers()
    resellers[rid]["credits"] = resellers[rid].get("credits",0) - total_cost
    resellers[rid]["total_generated"] = resellers[rid].get("total_generated",0) + count
    _save_resellers(resellers)
    _send_discord(f"ðŸª **Reseller Key-Gen**\nReseller: `{rdata['name']}`\nKeys: {count}x {days}d\nCredits: -{total_cost} (Rest: {resellers[rid]['credits']})")
    return jsonify(success=True, keys=new_keys, credits_used=total_cost, credits_remaining=resellers[rid]["credits"])

@app.route("/reseller/resetkey", methods=["POST"])
def reseller_resetkey():
    body = _get_body(); rid, rdata = _check_reseller(body)
    if rid is None: return rdata
    keys = load_keys(); key_id = body.get("key","").strip().upper()
    if key_id not in keys: return jsonify(success=False, message="Key not found")
    if keys[key_id].get("reseller") != rid: return jsonify(success=False, message="Kein Zugriff auf diesen Key")
    # 24h cooldown check
    last_reset = keys[key_id].get("last_reset", "")
    if last_reset:
        try:
            since = (_now() - _parse_datetime(last_reset)).total_seconds()
            remaining = int(86400 - since)
            if remaining > 0:
                h, m = divmod(remaining // 60, 60)
                s = remaining % 60
                return jsonify(success=False, message=f"Reset erst in {h:02d}:{m:02d}:{s:02d} moeglich", cooldown=remaining)
        except Exception as _e:
            app.logger.warning("Suppressed error in reseller_resetkey: %s", _e)
    keys[key_id]["hwid"] = ""
    keys[key_id]["activated"] = ""
    keys[key_id]["last_reset"] = _now_iso()
    save_keys(keys)
    return jsonify(success=True, last_reset=keys[key_id]["last_reset"])


@app.route("/crash", methods=["POST"])
def crash_report():
    ip = request.remote_addr or "unknown"
    if not _rate_ok_bucket(f"crash:{ip}", CRASH_RATE_LIMIT, CRASH_RATE_WINDOW):
        return jsonify(success=False, message="Too many crash reports. Try again later."), 429
    if not _global_crash_rate_ok():
        return jsonify(success=False, message="Global crash rate limit exceeded"), 429
    body = _get_body(); os.makedirs(CRASH_DIR, exist_ok=True)
    ts = _now().strftime("%Y%m%d_%H%M%S")
    report = {"ts": _now_iso(), "key": str(body.get("key",""))[:64], "hwid": str(body.get("hwid",""))[:128], "ip": ip, "version": str(body.get("version",""))[:32], "error": str(body.get("error",""))[:MAX_CRASH_ERROR], "traceback": str(body.get("traceback",""))[:MAX_CRASH_TRACEBACK]}
    with open(os.path.join(CRASH_DIR, f"crash_{ts}.json"), "w", encoding="utf-8") as f: json.dump(report, f, indent=2)
    _send_discord(f"ðŸ’¥ **Crash Report!**\nKey: `{body.get('key','?')}`\nVersion: `{body.get('version','?')}`\nError: `{str(body.get('error','?'))[:200]}`")
    return jsonify(success=True)

@app.route("/crashes", methods=["POST"])
def list_crashes():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    os.makedirs(CRASH_DIR, exist_ok=True); reports = []
    for fn in sorted(os.listdir(CRASH_DIR), reverse=True)[:50]:
        try:
            with open(os.path.join(CRASH_DIR, fn)) as f: reports.append(json.load(f))
        except Exception as _e:
            app.logger.warning("Suppressed error in list_crashes: %s", _e)
    return jsonify(success=True, crashes=reports)

@app.route("/stats", methods=["POST"])
def stats():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    logs = _load_json(LAUNCH_LOG, []); now = _now()
    def since(hours):
        cutoff = (now - datetime.timedelta(hours=hours)).isoformat()
        return [l for l in logs if l.get("ts","") >= cutoff]
    def unique(entries): return len(set(e["hwid"] for e in entries if e.get("hwid")))
    l24, l7d, l30d = since(24), since(168), since(720)
    top = collections.Counter(e["key"] for e in l7d).most_common(10)
    return jsonify(success=True, stats={"launches_24h": len(l24), "unique_24h": unique(l24), "launches_7d": len(l7d), "unique_7d": unique(l7d), "launches_30d": len(l30d), "unique_30d": unique(l30d), "top_keys_7d": [{"key": k, "count": c} for k, c in top], "total_logs": len(logs), "recent": logs[-20:][::-1]})


@app.route("/stats/chart", methods=["POST"])
def stats_chart():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    logs  = _load_json(LAUNCH_LOG, [])
    keys  = load_keys()
    now   = _now()

    # --- Launches per day (letzte 30 Tage) ---
    daily = {}
    for i in range(30):
        d = (now - datetime.timedelta(days=29 - i)).strftime("%Y-%m-%d")
        daily[d] = 0
    for entry in logs:
        ts = entry.get("ts", "")[:10]
        if ts in daily:
            daily[ts] += 1
    daily_labels = list(daily.keys())
    daily_values = list(daily.values())

    # --- Unique HWIDs per day (letzte 30 Tage) ---
    daily_hwid: dict = {d: set() for d in daily}
    for entry in logs:
        ts = entry.get("ts", "")[:10]
        if ts in daily_hwid and entry.get("hwid"):
            daily_hwid[ts].add(entry["hwid"])
    daily_unique = [len(daily_hwid[d]) for d in daily_labels]

    # --- Key-Typ Verteilung ---
    type_counts: dict = {}
    for v in keys.values():
        t = v.get("type", "standard")
        type_counts[t] = type_counts.get(t, 0) + 1

    # --- Status Verteilung ---
    free    = sum(1 for v in keys.values() if not v.get("hwid") and not v.get("banned"))
    active  = sum(1 for v in keys.values() if v.get("hwid")  and not v.get("banned") and not _is_expired(v))
    banned  = sum(1 for v in keys.values() if v.get("banned"))
    expired = sum(1 for v in keys.values() if _is_expired(v) and not v.get("banned"))
    paused  = sum(1 for v in keys.values() if v.get("paused") and not v.get("banned"))

    # --- Aktivierungen pro Wochentag (kumuliert) ---
    weekday_counts = [0] * 7
    for entry in logs:
        try:
            wd = _parse_datetime(entry["ts"]).weekday()
            weekday_counts[wd] += 1
        except Exception as _e:
            app.logger.warning("Suppressed error in stats_chart: %s", _e)

    return jsonify(
        success=True,
        daily_labels=daily_labels,
        daily_values=daily_values,
        daily_unique=daily_unique,
        type_counts=type_counts,
        status_counts={"Frei": free, "Aktiv": active, "Gebannt": banned, "Abgelaufen": expired, "Pausiert": paused},
        weekday_counts=weekday_counts,
    )


@app.route("/admin/account/change-password", methods=["POST"])
def admin_account_change_password():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    target_id  = body.get("id",       "").strip()
    new_pw     = body.get("new_password", "").strip()
    if not target_id or not new_pw:
        return jsonify(success=False, message="ID und neues Passwort erforderlich")
    admins = _get_admins()
    if target_id not in admins:
        return jsonify(success=False, message="Admin-Account nicht gefunden")
    admins[target_id]["password"] = _hash_password(new_pw)
    _save_admins(admins)
    _audit(_get_admin_name(body), "change_password", admins[target_id].get("username", target_id))
    return jsonify(success=True)

@app.route("/admin/account/file-manager-access", methods=["POST"])
def admin_account_file_manager_access():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    target_id = body.get("id", "").strip()
    admins = _get_admins()
    if target_id not in admins:
        return jsonify(success=False, message="Admin-Account nicht gefunden")
    enabled = bool(body.get("enabled", False))
    admins[target_id]["file_manager_access"] = enabled
    _save_admins(admins)
    _audit(_get_admin_name(body), "file_manager_access", admins[target_id].get("username", target_id), f"enabled={enabled}")
    return jsonify(success=True, enabled=enabled)


def _version_tuple(v):
    try: return tuple(int(x) for x in v.split("."))
    except Exception as _e:
        app.logger.warning("Suppressed error in _version_tuple: %s", _e)
        return (0, 0, 0)

@app.route("/update/check", methods=["GET"])
def update_check():
    err = _check_client_update_access()
    if err: return err
    client_ver = request.args.get("v", "0.0.0")
    meta = _load_json(os.path.join(UPDATE_DIR, "meta.json"), None)
    if not meta: return jsonify(update=False, message="No updates available")
    server_ver = meta.get("version", "0.0.0")
    if _version_tuple(server_ver) > _version_tuple(client_ver):
        return jsonify(update=True, version=server_ver, changelog=meta.get("changelog",""), filename=meta.get("filename","hextra.py"), checksum=meta.get("checksum",""), size=meta.get("size",0), published=meta.get("published",""))
    return jsonify(update=False, version=server_ver, checksum=meta.get("checksum",""), filename=meta.get("filename",""), published=meta.get("published",""))

@app.route("/update/download", methods=["GET"])
def update_download():
    err = _check_client_update_access()
    if err: return err
    meta = _load_json(os.path.join(UPDATE_DIR, "meta.json"), None)
    if not meta: return jsonify(success=False, message="No update"), 404
    try:
        filename = _safe_update_filename(meta.get("filename","hextra.py"))
        filepath = _safe_join(UPDATE_DIR, filename)
    except ValueError as ex:
        return jsonify(success=False, message=str(ex)), 400
    if not os.path.isfile(filepath):
        legacy_filepath = filepath.replace(BASE_DIR, LEGACY_BASE_DIR, 1)
        if os.path.isfile(legacy_filepath):
            filepath = legacy_filepath
    if not os.path.isfile(filepath): return jsonify(success=False, message="File not found"), 404
    with open(filepath, "rb") as f: data = f.read()
    return Response(data, mimetype="application/octet-stream", headers={"Content-Disposition": f"attachment; filename={filename}"})

@app.route("/update/publish", methods=["POST"])
def update_publish():
    os.makedirs(UPDATE_DIR, exist_ok=True)
    if request.content_type and "multipart" in request.content_type:
        body = request.form.to_dict(flat=True)
        err = _check_admin(body)
        if err:
            return err
        f = request.files.get("file")
        if not f or not f.filename: return jsonify(success=False, message="No file uploaded")
        if _uploaded_file_size(f) > MAX_UPDATE_SIZE:
            return jsonify(success=False, message=f"Update file too large. Server limit is {MAX_UPDATE_SIZE_MB} MB.", limit_mb=MAX_UPDATE_SIZE_MB), 400
        version, changelog = request.form.get("version","1.0.0"), request.form.get("changelog","")
        try:
            filename = _safe_update_filename(f.filename)
            filepath = _safe_join(UPDATE_DIR, filename)
        except ValueError as ex:
            return jsonify(success=False, message=str(ex)), 400
        f.save(filepath)
    else:
        body = _get_body(); err = _check_admin(body)
        if err: return err
        try:
            filename = _safe_update_filename(body.get("filename","hextra.py"))
            filepath = _safe_join(UPDATE_DIR, filename)
        except ValueError as ex:
            return jsonify(success=False, message=str(ex)), 400
        if not os.path.isfile(filepath):
            legacy_filepath = filepath.replace(BASE_DIR, LEGACY_BASE_DIR, 1)
            if os.path.isfile(legacy_filepath):
                filepath = legacy_filepath
        version, changelog = body.get("version","1.0.0"), body.get("changelog","")
        if not os.path.isfile(filepath): return jsonify(success=False, message=f"File not in {UPDATE_DIR}")
    injected = False
    if filename.endswith(".py"):
        try:
            with open(filepath, "r", encoding="utf-8") as fh: src = fh.read()
            new_src, n = re.subn(r'VERSION\s*=\s*["\'][\d.]+["\']', f'VERSION = "{version}"', src)
            if n > 0:
                with open(filepath, "w", encoding="utf-8") as fh: fh.write(new_src)
                injected = True
        except Exception as _e:
            app.logger.warning("Suppressed error in update_publish: %s", _e)
    with open(filepath, "rb") as fh: checksum = hashlib.sha256(fh.read()).hexdigest()
    meta = {"version": version, "changelog": changelog, "filename": filename, "checksum": checksum, "size": os.path.getsize(filepath), "published": _now_iso()}
    _save_json(os.path.join(UPDATE_DIR, "meta.json"), meta)
    return jsonify(success=True, meta=meta, version_injected=injected)

@app.route("/update/status", methods=["POST"])
def update_status():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    return jsonify(success=True, meta=_load_json(os.path.join(UPDATE_DIR, "meta.json"), None))

@app.route("/update/delete", methods=["POST"])
def update_delete():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    meta_file = os.path.join(UPDATE_DIR, "meta.json")
    if os.path.isfile(meta_file):
        try:
            meta = _load_json(meta_file); fp = os.path.join(UPDATE_DIR, meta.get("filename",""))
            if os.path.isfile(fp): os.remove(fp)
            os.remove(meta_file)
        except Exception as _e:
            app.logger.warning("Suppressed error in update_delete: %s", _e)
    return jsonify(success=True)

@app.route("/files/list", methods=["POST"])
def files_list():
    body = _get_body()
    actor, access_err = _check_file_manager_access()
    if access_err: return access_err
    path = body.get("path", "")
    if actor.get("role") == "user" and not _user_can_access_manager_path({"file_access_paths": actor.get("file_access_paths", [])}, path):
        return jsonify(success=False, message="Ordnerzugriff verweigert"), 403
    try:
        listing = _list_manager_entries(path)
    except ValueError as ex:
        return jsonify(success=False, message=str(ex)), 400
    except FileNotFoundError:
        return jsonify(success=False, message="Folder not found"), 404
    return jsonify(success=True, limit_mb=MAX_SHARED_FILE_SIZE_MB, **listing)

@app.route("/files/upload", methods=["POST"])
def files_upload():
    if request.content_type and "multipart" in request.content_type:
        body = request.form.to_dict(flat=True)
        actor, access_err = _check_file_manager_access()
        if access_err: return access_err
        if actor.get("role") == "user" and not _user_can_access_manager_path({"file_access_paths": actor.get("file_access_paths", [])}, body.get("path", "")):
            return jsonify(success=False, message="Ordnerzugriff verweigert"), 403
        file_obj = request.files.get("file")
        if not file_obj or not file_obj.filename:
            return jsonify(success=False, message="No file uploaded"), 400
        if _uploaded_file_size(file_obj) > MAX_SHARED_FILE_SIZE:
            return jsonify(success=False, message=f"File too large. Server limit is {MAX_SHARED_FILE_SIZE_MB} MB.", limit_mb=MAX_SHARED_FILE_SIZE_MB), 400
        try:
            rel_path, folder = _manager_path(body.get("path", ""))
            os.makedirs(folder, exist_ok=True)
            filename = _safe_manager_name(body.get("filename", "") or file_obj.filename)
            filepath = _safe_join(folder, filename)
        except ValueError as ex:
            return jsonify(success=False, message=str(ex)), 400
        file_obj.save(filepath)
        file_rel = f"{rel_path}/{filename}" if rel_path else filename
        return jsonify(success=True, message="File uploaded", file={
            "name": filename,
            "size": os.path.getsize(filepath),
            "modified": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "path": file_rel,
            "download_url": f"/files/download/{file_rel}",
        })
    return jsonify(success=False, message="Multipart upload required"), 400

@app.route("/files/download/<path:filename>", methods=["GET"])
def files_download(filename):
    actor, access_err = _check_file_manager_access()
    if access_err:
        if actor is None:
            return redirect("/login")
        return redirect("/login")
    try:
        rel_path, filepath = _manager_path(filename)
    except ValueError as ex:
        return jsonify(success=False, message=str(ex)), 400
    if actor.get("role") == "user" and not _user_can_access_manager_path({"file_access_paths": actor.get("file_access_paths", [])}, rel_path):
        return jsonify(success=False, message="Dateizugriff verweigert"), 403
    if not os.path.isfile(filepath):
        return jsonify(success=False, message="File not found"), 404
    mime_type = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
    with open(filepath, "rb") as f:
        data = f.read()
    download_name = os.path.basename(rel_path) or os.path.basename(filepath)
    return Response(data, mimetype=mime_type, headers={"Content-Disposition": f'attachment; filename="{download_name}"'})

@app.route("/files/create-folder", methods=["POST"])
def files_create_folder():
    body = _get_body()
    actor, access_err = _check_file_manager_access()
    if access_err: return access_err
    if actor.get("role") == "user" and not _user_can_access_manager_path({"file_access_paths": actor.get("file_access_paths", [])}, body.get("path", "")):
        return jsonify(success=False, message="Ordnerzugriff verweigert"), 403
    try:
        _, folder = _manager_path(body.get("path", ""))
        name = _safe_manager_name(body.get("name", ""))
        target = _safe_join(folder, name)
    except ValueError as ex:
        return jsonify(success=False, message=str(ex)), 400
    if os.path.exists(target):
        return jsonify(success=False, message="Folder already exists"), 400
    os.makedirs(target, exist_ok=False)
    return jsonify(success=True, message="Folder created")

@app.route("/files/delete", methods=["POST"])
def files_delete():
    body = _get_body()
    actor, access_err = _check_file_manager_access()
    if access_err: return access_err
    try:
        rel_path, target = _manager_path(body.get("target", ""))
    except ValueError as ex:
        return jsonify(success=False, message=str(ex)), 400
    if actor.get("role") == "user" and not _user_can_access_manager_path({"file_access_paths": actor.get("file_access_paths", [])}, rel_path):
        return jsonify(success=False, message="Ordnerzugriff verweigert"), 403
    if not rel_path:
        return jsonify(success=False, message="Root folder cannot be deleted"), 400
    if not os.path.exists(target):
        return jsonify(success=False, message="Entry not found"), 404
    if os.path.isdir(target):
        shutil.rmtree(target)
    else:
        os.remove(target)
    return jsonify(success=True, message="Deleted")

@app.route("/files/rename", methods=["POST"])
def files_rename():
    body = _get_body()
    actor, access_err = _check_file_manager_access()
    if access_err: return access_err
    try:
        rel_path, source = _manager_path(body.get("target", ""))
        new_name = _safe_manager_name(body.get("name", ""))
    except ValueError as ex:
        return jsonify(success=False, message=str(ex)), 400
    if actor.get("role") == "user" and not _user_can_access_manager_path({"file_access_paths": actor.get("file_access_paths", [])}, rel_path):
        return jsonify(success=False, message="Ordnerzugriff verweigert"), 403
    if not rel_path:
        return jsonify(success=False, message="Root folder cannot be renamed"), 400
    if not os.path.exists(source):
        return jsonify(success=False, message="Entry not found"), 404
    parent_rel = os.path.dirname(rel_path).replace("\\", "/").strip(".")
    parent_rel = "" if parent_rel in {"", "."} else parent_rel
    _, parent_dir = _manager_path(parent_rel)
    target = _safe_join(parent_dir, new_name)
    if os.path.exists(target):
        return jsonify(success=False, message="Target name already exists"), 400
    os.replace(source, target)
    new_rel = f"{parent_rel}/{new_name}" if parent_rel else new_name
    return jsonify(success=True, message="Renamed", path=new_rel)

@app.route("/files/move", methods=["POST"])
def files_move():
    body = _get_body()
    actor, access_err = _check_file_manager_access()
    if access_err: return access_err
    try:
        rel_path, source = _manager_path(body.get("target", ""))
        dest_rel, dest_dir = _manager_path(body.get("destination", ""))
    except ValueError as ex:
        return jsonify(success=False, message=str(ex)), 400
    if actor.get("role") == "user":
        scoped_user = {"file_access_paths": actor.get("file_access_paths", [])}
        if not _user_can_access_manager_path(scoped_user, rel_path) or not _user_can_access_manager_path(scoped_user, dest_rel):
            return jsonify(success=False, message="Ordnerzugriff verweigert"), 403
    if not rel_path:
        return jsonify(success=False, message="Root folder cannot be moved"), 400
    if not os.path.exists(source):
        return jsonify(success=False, message="Entry not found"), 404
    if not os.path.isdir(dest_dir):
        return jsonify(success=False, message="Destination folder not found"), 404

    source_name = os.path.basename(rel_path.rstrip("/\\"))
    target_path = _safe_join(dest_dir, source_name)
    source_real = os.path.realpath(source)
    dest_real = os.path.realpath(dest_dir)
    target_real = os.path.realpath(target_path)

    if source_real == dest_real:
        return jsonify(success=False, message="Entry is already in this folder"), 400
    if target_real == source_real:
        return jsonify(success=False, message="Entry is already in this folder"), 400
    if os.path.exists(target_path):
        return jsonify(success=False, message="Target name already exists in destination"), 400
    if os.path.isdir(source):
        source_prefix = source_real.rstrip("\\/") + os.sep
        if dest_real == source_real or dest_real.startswith(source_prefix):
            return jsonify(success=False, message="Folder cannot be moved into itself"), 400

    shutil.move(source, target_path)
    new_rel = f"{dest_rel}/{source_name}" if dest_rel else source_name
    return jsonify(success=True, message="Moved", path=new_rel)

@app.route("/files/read", methods=["POST"])
def files_read():
    body = _get_body()
    actor, access_err = _check_file_manager_access()
    if access_err: return access_err
    try:
        rel_path, filepath = _manager_path(body.get("target", ""))
    except ValueError as ex:
        return jsonify(success=False, message=str(ex)), 400
    if actor.get("role") == "user" and not _user_can_access_manager_path({"file_access_paths": actor.get("file_access_paths", [])}, rel_path):
        return jsonify(success=False, message="Dateizugriff verweigert"), 403
    if not rel_path:
        return jsonify(success=False, message="Folder cannot be opened in editor"), 400
    if not os.path.isfile(filepath):
        return jsonify(success=False, message="File not found"), 404
    if not _is_editable_text_file(rel_path):
        return jsonify(success=False, message="File type is not supported by the editor"), 400
    size = os.path.getsize(filepath)
    if size > MAX_TEXT_EDITOR_SIZE:
        return jsonify(success=False, message=f"File too large for editor. Limit is {MAX_TEXT_EDITOR_SIZE_MB} MB."), 400
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        try:
            with open(filepath, "r", encoding="utf-8-sig") as f:
                content = f.read()
        except UnicodeDecodeError:
            return jsonify(success=False, message="File is not valid UTF-8 text"), 400
    return jsonify(
        success=True,
        path=rel_path,
        name=os.path.basename(rel_path),
        extension=_editable_text_extension(rel_path),
        size=size,
        limit_mb=MAX_TEXT_EDITOR_SIZE_MB,
        content=content,
    )

@app.route("/files/save", methods=["POST"])
def files_save():
    body = _get_body()
    actor, access_err = _check_file_manager_access()
    if access_err: return access_err
    try:
        rel_path, filepath = _manager_path(body.get("target", ""))
    except ValueError as ex:
        return jsonify(success=False, message=str(ex)), 400
    if actor.get("role") == "user" and not _user_can_access_manager_path({"file_access_paths": actor.get("file_access_paths", [])}, rel_path):
        return jsonify(success=False, message="Dateizugriff verweigert"), 403
    if not rel_path:
        return jsonify(success=False, message="Folder cannot be saved"), 400
    if not os.path.isfile(filepath):
        return jsonify(success=False, message="File not found"), 404
    if not _is_editable_text_file(rel_path):
        return jsonify(success=False, message="File type is not supported by the editor"), 400
    content = body.get("content", "")
    if not isinstance(content, str):
        return jsonify(success=False, message="Invalid content"), 400
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_TEXT_EDITOR_SIZE:
        return jsonify(success=False, message=f"File too large for editor. Limit is {MAX_TEXT_EDITOR_SIZE_MB} MB."), 400
    with open(filepath, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    return jsonify(
        success=True,
        message="Saved",
        path=rel_path,
        size=len(encoded),
        modified=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )

@app.route("/files/preview-token", methods=["POST"])
def files_preview_token():
    body = _get_body()
    actor, access_err = _check_file_manager_access()
    if access_err: return access_err
    try:
        rel_path, filepath = _manager_path(body.get("target", ""))
    except ValueError as ex:
        return jsonify(success=False, message=str(ex)), 400
    if actor.get("role") == "user" and not _user_can_access_manager_path({"file_access_paths": actor.get("file_access_paths", [])}, rel_path):
        return jsonify(success=False, message="Dateizugriff verweigert"), 403
    if not rel_path:
        return jsonify(success=False, message="Folder cannot be previewed"), 400
    if not os.path.isfile(filepath):
        return jsonify(success=False, message="File not found"), 404
    if not _is_html_preview_file(rel_path):
        return jsonify(success=False, message="Preview is only available for HTML files"), 400
    token, ttl = _create_html_preview_token(rel_path)
    return jsonify(success=True, token=token, ttl_seconds=ttl, url=f"/files/preview/{token}")

@app.route("/files/preview-revoke", methods=["POST"])
def files_preview_revoke():
    token = ""
    if request.is_json:
        body = _get_body()
        token = (body.get("token", "") or "").strip()
    else:
        token = (request.form.get("token", "") or request.data.decode("utf-8", errors="ignore") or "").strip()
    if not token:
        return jsonify(success=False, message="Missing token"), 400
    meta = _consume_html_preview_token(token, revoke=True)
    if not meta:
        return jsonify(success=True, revoked=False)
    return jsonify(success=True, revoked=True)

@app.route("/files/preview/<token>", methods=["GET"])
def files_preview(token):
    meta = _consume_html_preview_token(token, revoke=False)
    if not meta:
        return Response("Preview link expired", status=410, mimetype="text/plain")
    rel_path = meta.get("path", "")
    try:
        _, filepath = _manager_path(rel_path)
    except ValueError:
        return Response("Invalid preview path", status=400, mimetype="text/plain")
    if not os.path.isfile(filepath):
        return Response("File not found", status=404, mimetype="text/plain")
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        try:
            with open(filepath, "r", encoding="utf-8-sig") as f:
                content = f.read()
        except UnicodeDecodeError:
            return Response("HTML preview only supports UTF-8 files", status=400, mimetype="text/plain")
    revoke_script = (
        "<script>"
        f"(function(){{var t={json.dumps(token)};"
        "function r(){try{navigator.sendBeacon('/files/preview-revoke',t);}catch(e){}}"
        "window.addEventListener('pagehide',r);window.addEventListener('beforeunload',r);"
        "})();"
        "</script>"
    )
    if "</body>" in content.lower():
        idx = content.lower().rfind("</body>")
        content = content[:idx] + revoke_script + content[idx:]
    else:
        content += revoke_script
    return Response(content, mimetype="text/html")

@app.route("/files/presence", methods=["POST"])
def files_presence():
    actor, access_err = _check_file_manager_access()
    if access_err: return access_err
    body = _get_body() if request.is_json else {}
    if body.get("active") is False:
        _remove_file_manager_presence(actor)
        return jsonify(success=True, active=False)
    scoped_user = {"file_access_paths": actor.get("file_access_paths", [])}
    current_path = body.get("current_path", "")
    editor_path = body.get("editor_path", "")
    if actor.get("role") == "user":
        if current_path and not _user_can_access_manager_path(scoped_user, current_path):
            current_path = actor.get("file_access_default_path", "")
        if editor_path and not _user_can_access_manager_path(scoped_user, editor_path):
            editor_path = ""
    _update_file_manager_presence(actor, current_path=current_path, editor_path=editor_path, editing=bool(body.get("editing")))
    return jsonify(success=True, ttl_seconds=FILE_MANAGER_PRESENCE_TTL_SECONDS)

@app.route("/admin/file-activity", methods=["POST"])
def admin_file_activity():
    body = _get_body(); err = _check_admin(body)
    if err: return err
    return jsonify(success=True, ttl_seconds=FILE_MANAGER_PRESENCE_TTL_SECONDS, sessions=_file_manager_presence_snapshot())


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  AUDIT LOG
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@app.route("/admin/audit", methods=["POST"])
def admin_audit():
    """
    Gibt das Audit-Log zurueck.
    Optionale Filter im Body:
      limit  (int)   - max. Anzahl Eintraege, neueste zuerst (default: 200)
      action (str)   - nur Eintraege mit diesem action-Typ
      admin  (str)   - nur Eintraege dieses Admins
      since  (str)   - ISO-Datum, nur Eintraege nach diesem Zeitpunkt
      q      (str)   - Freitext-Suche in target/details/admin
    """
    body = _get_body(); err = _check_admin(body)
    if err: return err

    log    = _load_json(AUDIT_LOG, [])
    limit  = min(int(body.get("limit", 200)), 1000)
    action = body.get("action", "").strip().lower()
    admin  = body.get("admin",  "").strip().lower()
    since  = body.get("since",  "").strip()
    q      = body.get("q",      "").strip().lower()

    if action: log = [e for e in log if e.get("action","").lower() == action]
    if admin:  log = [e for e in log if e.get("admin","").lower() == admin]
    if since:  log = [e for e in log if e.get("ts","") >= since]
    if q:      log = [e for e in log if q in (e.get("target","") + e.get("details","") + e.get("admin","")).lower()]

    # Neueste zuerst, dann limitieren
    log = list(reversed(log))[:limit]

    # Distinct action types fuer Filter-Dropdown
    all_actions = sorted(set(e.get("action","") for e in _load_json(AUDIT_LOG, [])))

    return jsonify(success=True, entries=log, total=len(_load_json(AUDIT_LOG, [])), actions=all_actions)


@app.route("/admin/audit/clear", methods=["POST"])
def admin_audit_clear():
    """Loescht das gesamte Audit-Log (nur fuer Super-Admins gedacht)."""
    body = _get_body(); err = _check_admin(body)
    if err: return err
    _audit(_get_admin_name(body), "audit_clear", "", "Audit-Log geleert")
    _save_json(AUDIT_LOG, [])
    return jsonify(success=True)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  AI ASSISTANT  (Groq â€” Function Calling + Legacy-Fallback + NLP-Fallback)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

# OpenAI-compatible tool definitions for Groq function calling
_AI_TOOLS = [
    {"type": "function", "function": {
        "name": "gen_key",
        "description": "Generiert neue Lizenz-Keys fuer SUNO",
        "parameters": {"type": "object", "properties": {
            "count": {"type": "string", "description": "Anzahl Keys (1-50)", "default": 1},
            "days":  {"type": "string", "description": "Laufzeit in Tagen (0=unbegrenzt)", "default": 7},
            "type":  {"type": "string",  "description": "Key-Typ z.B. standard, vip", "default": "standard"},
            "note":  {"type": "string",  "description": "Notiz fuer den Key", "default": "AI generated"}
        }, "required": []}}},
    {"type": "function", "function": {
        "name": "ban_key",
        "description": "Bannt einen Lizenz-Key (sperrt die Nutzung dauerhaft)",
        "parameters": {"type": "object", "properties": {
            "key":    {"type": "string", "description": "Key-ID z.B. SUNO-XXXX-YYYY"},
            "reason": {"type": "string", "description": "Grund fuer den Ban", "default": "AI ban"}
        }, "required": ["key"]}}},
    {"type": "function", "function": {
        "name": "unban_key",
        "description": "Entbannt einen Lizenz-Key",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string", "description": "Key-ID"}
        }, "required": ["key"]}}},
    {"type": "function", "function": {
        "name": "pause_key",
        "description": "Pausiert einen Lizenz-Key (friert die verbleibende Laufzeit ein)",
        "parameters": {"type": "object", "properties": {
            "key":    {"type": "string", "description": "Key-ID"},
            "reason": {"type": "string", "description": "Grund fuer die Pause", "default": "AI pause"}
        }, "required": ["key"]}}},
    {"type": "function", "function": {
        "name": "unpause_key",
        "description": "Hebt die Pause eines Lizenz-Keys auf und startet den Countdown neu",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string", "description": "Key-ID"}
        }, "required": ["key"]}}},
    {"type": "function", "function": {
        "name": "pause_all_keys",
        "description": "Pausiert ALLE aktiven (nicht-gebannten) Lizenz-Keys auf einmal",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "unpause_all_keys",
        "description": "Hebt die Pause aller pausierten Lizenz-Keys auf",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "delete_key",
        "description": "Loescht einen Lizenz-Key permanent",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string", "description": "Key-ID"}
        }, "required": ["key"]}}},
    {"type": "function", "function": {
        "name": "reset_key",
        "description": "Setzt die HWID eines Keys zurueck, sodass er neu aktiviert werden kann",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string", "description": "Key-ID"}
        }, "required": ["key"]}}},
    {"type": "function", "function": {
        "name": "add_credits",
        "description": "Fuegt einem Reseller Credits hinzu",
        "parameters": {"type": "object", "properties": {
            "reseller_id": {"type": "string", "description": "Reseller-ID"},
            "amount":      {"type": "string", "description": "Anzahl Credits"}
        }, "required": ["reseller_id", "amount"]}}},
    {"type": "function", "function": {
        "name": "assign_key",
        "description": "Weist einen Key einem Reseller zu",
        "parameters": {"type": "object", "properties": {
            "key":         {"type": "string", "description": "Key-ID"},
            "reseller_id": {"type": "string", "description": "Reseller-ID"}
        }, "required": ["key", "reseller_id"]}}}
]


def _ai_execute_tool(name: str, args: dict) -> str:
    """Fuehrt eine AI-Aktion aus und gibt eine Ergebnismeldung zurueck."""
    admin_name = _session_auth().get("username", "ai")
    def _ai_audit(action, target="", details=""):
        _audit(admin_name or "ai", action, target, details)
    try:
        if name == "gen_key":
            k = load_keys()
            count = min(int(args.get("count", 1)), 50)
            days  = int(args.get("days", 7))
            new   = []
            for _ in range(count):
                nk = _generate_key()
                while nk in k: nk = _generate_key()
                k[nk] = {"type": args.get("type", "standard"), "hwid": "", "created": _now_iso(),
                          "note": args.get("note", "AI generated"), "banned": False,
                          "expires": "", "days": days, "tag": "ai"}
                new.append(nk)
            save_keys(k)
            _ai_audit("gen_key", ",".join(new), f"count={count}; days={days}; type={args.get('type', 'standard')}")
            return f"OK: {count} Key(s) generiert: {', '.join(new)}"

        elif name == "ban_key":
            k = load_keys(); kid = args.get("key", "").strip().upper()
            if kid not in k: return f"FAIL: Key {kid} nicht gefunden"
            k[kid]["banned"] = True; k[kid]["ban_reason"] = args.get("reason", "AI ban")
            save_keys(k)
            _ai_audit("ban_key", kid, f"reason={args.get('reason', 'AI ban')}")
            return f"OK: {kid} gebannt"

        elif name == "unban_key":
            k = load_keys(); kid = args.get("key", "").strip().upper()
            if kid not in k: return f"FAIL: Key {kid} nicht gefunden"
            k[kid]["banned"] = False; k[kid]["ban_reason"] = ""
            k[kid]["frozen_expires"] = ""; k[kid]["remaining_seconds"] = ""
            save_keys(k)
            _ai_audit("unban_key", kid)
            return f"OK: {kid} entbannt"

        elif name == "pause_key":
            k = load_keys(); kid = args.get("key", "").strip().upper()
            if kid not in k: return f"FAIL: Key {kid} nicht gefunden"
            k[kid]["paused"] = True; k[kid]["pause_reason"] = args.get("reason", "AI pause")
            if k[kid].get("expires"):
                try:
                    exp = _parse_datetime(k[kid]["expires"])
                    k[kid]["remaining_seconds"] = max(0, int((exp - _now()).total_seconds()))
                    k[kid]["frozen_expires_pause"] = k[kid]["expires"]; k[kid]["expires"] = ""
                except Exception as _e:
                    app.logger.warning("Suppressed error in _ai_execute_tool: %s", _e)
            save_keys(k)
            _ai_audit("pause_key", kid, f"reason={args.get('reason', 'AI pause')}")
            return f"OK: {kid} pausiert"

        elif name == "unpause_key":
            k = load_keys(); kid = args.get("key", "").strip().upper()
            if kid not in k: return f"FAIL: Key {kid} nicht gefunden"
            k[kid]["paused"] = False; k[kid]["pause_reason"] = ""
            if k[kid].get("remaining_seconds"):
                try:
                    k[kid]["expires"] = (_now() + datetime.timedelta(seconds=int(k[kid]["remaining_seconds"]))).isoformat()
                    k[kid]["remaining_seconds"] = ""
                except Exception as _e:
                    app.logger.warning("Suppressed error in _ai_execute_tool: %s", _e)
            k[kid]["frozen_expires_pause"] = ""
            save_keys(k)
            _ai_audit("unpause_key", kid)
            return f"OK: {kid} entpausiert"

        elif name == "pause_all_keys":
            k = load_keys(); count = 0
            for kid, v in k.items():
                if not v.get("paused") and not v.get("banned"):
                    v["paused"] = True; v["pause_reason"] = "AI bulk pause"
                    if v.get("expires"):
                        try:
                            exp = _parse_datetime(v["expires"])
                            v["remaining_seconds"] = max(0, int((exp - _now()).total_seconds()))
                            v["frozen_expires_pause"] = v["expires"]; v["expires"] = ""
                        except Exception as _e:
                            app.logger.warning("Suppressed error in _ai_execute_tool: %s", _e)
                    count += 1
            save_keys(k)
            _ai_audit("pause_all_keys", "all", f"count={count}")
            return f"OK: {count} Keys pausiert"

        elif name == "unpause_all_keys":
            k = load_keys(); count = 0
            for kid, v in k.items():
                if v.get("paused"):
                    v["paused"] = False; v["pause_reason"] = ""
                    if v.get("remaining_seconds"):
                        try:
                            v["expires"] = (_now() + datetime.timedelta(seconds=int(v["remaining_seconds"]))).isoformat()
                            v["remaining_seconds"] = ""
                        except Exception as _e:
                            app.logger.warning("Suppressed error in _ai_execute_tool: %s", _e)
                    v["frozen_expires_pause"] = ""; count += 1
            save_keys(k)
            _ai_audit("unpause_all_keys", "all", f"count={count}")
            return f"OK: {count} Keys entpausiert"

        elif name == "delete_key":
            k = load_keys(); kid = args.get("key", "").strip().upper()
            if kid not in k: return f"FAIL: Key {kid} nicht gefunden"
            del k[kid]; save_keys(k)
            _ai_audit("delete_key", kid)
            return f"OK: {kid} geloescht"

        elif name == "reset_key":
            k = load_keys(); kid = args.get("key", "").strip().upper()
            if kid not in k: return f"FAIL: Key {kid} nicht gefunden"
            k[kid]["hwid"] = ""; k[kid]["activated"] = ""
            save_keys(k)
            _ai_audit("reset_key", kid)
            return f"OK: HWID von {kid} zurueckgesetzt"

        elif name == "add_credits":
            rs = _get_resellers(); rid = args.get("reseller_id", "")
            if rid not in rs: return f"FAIL: Reseller {rid} nicht gefunden"
            amt = int(args.get("amount", 0))
            rs[rid]["credits"] = rs[rid].get("credits", 0) + amt
            _save_resellers(rs)
            _ai_audit("add_credits", rid, f"amount={amt}")
            return f"OK: {amt} Credits an {rs[rid].get('name', rid)} vergeben"

        elif name == "assign_key":
            k = load_keys(); kid = args.get("key", "").strip().upper()
            rid = args.get("reseller_id", ""); rs = _get_resellers()
            if kid not in k: return f"FAIL: Key {kid} nicht gefunden"
            if rid not in rs: return f"FAIL: Reseller {rid} nicht gefunden"
            k[kid]["reseller"] = rid; k[kid]["reseller_name"] = rs[rid].get("name", "")
            k[kid]["tag"] = f"reseller:{rs[rid].get('name', '')}"
            save_keys(k)
            _ai_audit("assign_key", kid, f"reseller={rid}")
            return f"OK: {kid} an {rs[rid].get('name', rid)} zugewiesen"

        return f"FAIL: Unbekannte Aktion '{name}'"
    except Exception as ex:
        return f"FAIL: {str(ex)}"


# Mapping alter Action-Namen auf neue Tool-Namen (Legacy-Kompatibilitaet)
_LEGACY_ACTION_MAP = {
    "genkey": "gen_key", "bankey": "ban_key", "unbankey": "unban_key",
    "pausekey": "pause_key", "unpausekey": "unpause_key",
    "pausekeys": "pause_all_keys", "unpausekeys": "unpause_all_keys",
    "delkey": "delete_key", "resetkey": "reset_key",
    "credits": "add_credits", "assign": "assign_key"
}


def _ai_parse_legacy_blocks(ai_text: str) -> list:
    """Fallback 1: parst alte ```action JSON``` Bloecke aus dem AI-Text."""
    results = []
    action_blocks = re.findall(r'```action\s*\n(.*?)\n```', ai_text, re.DOTALL)
    for block in action_blocks:
        raw = block.strip()
        candidates = []
        try:
            candidates.append(json.loads(raw))
        except Exception as _e:
            app.logger.warning("Suppressed error in _ai_parse_legacy_blocks: %s", _e)
            for _ln in raw.split("\n"):
                _ln = _ln.strip()
                if _ln.startswith("{"):
                    try: candidates.append(json.loads(_ln))
                    except Exception as _e:
                        app.logger.warning("Suppressed error in _ai_parse_legacy_blocks: %s", _e)
            if not candidates:
                _buf = ""; _depth = 0
                for _ch in raw:
                    _buf += _ch
                    if _ch == "{": _depth += 1
                    elif _ch == "}":
                        _depth -= 1
                        if _depth == 0:
                            try: candidates.append(json.loads(_buf.strip()))
                            except Exception as _e:
                                app.logger.warning("Suppressed error in _ai_parse_legacy_blocks: %s", _e)
                            _buf = ""
        for act in candidates:
            try:
                act_type = act.get("action", "")
                tool_name = _LEGACY_ACTION_MAP.get(act_type)
                if tool_name:
                    # Alten 'reseller_id' Key vereinheitlichen
                    if act_type == "credits" and "reseller_id" not in act:
                        act["reseller_id"] = act.get("reseller_id", "")
                    r = _ai_execute_tool(tool_name, act)
                    if r: results.append(r)
            except Exception as ex:
                results.append(f"FAIL {str(ex)}")
    return results


def _ai_nlp_fallback(text: str) -> list:
    """
    Fallback 2: NLP Intent-Parsing auf der USER-NACHRICHT.
    Erkennt natuerliche Sprache wie 'Loesche SUNO-XXXX-YYYY' direkt.
    Laeuft NUR auf dem Benutzertext, nie auf dem AI-Antworttext.
    """
    results = []
    lower   = text.lower()
    text_up = text.upper()

    # Globale Aktionen (kein Key-ID noetig)
    if re.search(r'\balle?\s+keys?\s+(pausier|einfrier)', lower):
        results.append(_ai_execute_tool("pause_all_keys", {})); return results
    if re.search(r'\balle?\s+keys?\s+(entpausier|fortsetzen|resume)', lower):
        results.append(_ai_execute_tool("unpause_all_keys", {})); return results

    # Intent-Erkennung: einmal global pruefen (fuer die gesamte Nachricht)
    is_ban      = bool(re.search(r'\b(bann(e|en)?|ban\b|sperr)', lower))
    is_unban    = bool(re.search(r'\b(entbann|unban|entsperr)', lower))
    is_pause    = bool(re.search(r'\b(pausier|pause\b|einfrier|freez)', lower))
    is_unpause  = bool(re.search(r'\b(entpausier|unpause|fortsetzen|resume)', lower))
    is_delete   = bool(re.search(r'\b(l[oÃ¶]sch|delete|entfern|remov)', lower))
    is_reset    = bool(re.search(r'\b(reset|hwid.*(zur[uÃ¼]ck|reset)|zur[uÃ¼]ck.*hwid)', lower))

    # Key-IDs aus der Nachricht extrahieren und Aktion ausfuehren
    for key in re.findall(r'SUNO-[A-Z0-9]{4}-[A-Z0-9]{4}', text_up):
        if is_delete:
            results.append(_ai_execute_tool("delete_key",  {"key": key}))
        elif is_ban:
            results.append(_ai_execute_tool("ban_key",     {"key": key, "reason": "NLP ban"}))
        elif is_unban:
            results.append(_ai_execute_tool("unban_key",   {"key": key}))
        elif is_unpause:
            results.append(_ai_execute_tool("unpause_key", {"key": key}))
        elif is_pause:
            results.append(_ai_execute_tool("pause_key",   {"key": key, "reason": "NLP pause"}))
        elif is_reset:
            results.append(_ai_execute_tool("reset_key",   {"key": key}))

    return results


def _groq_request(groq_key: str, payload_dict: dict) -> dict:
    """Hilfsfunktion fuer Groq API Requests."""
    payload = json.dumps(payload_dict).encode()
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {groq_key}",
                 "User-Agent": "SUNO/1.0"},
        method="POST"
    )
    resp = urllib.request.urlopen(req, timeout=30)
    return json.loads(resp.read().decode())


@app.route("/ai/chat", methods=["POST"])
def ai_chat():
    body = _get_body()
    err = _check_admin(body)
    if err: return err

    message = body.get("message", "").strip()
    if not message:
        return jsonify(success=False, message="Keine Nachricht")

    cfg = load_cfg()
    groq_key = _cfg_secret("groq_api_key", "")
    if not groq_key:
        return jsonify(success=False, message="Groq API Key nicht konfiguriert. Unter Settings speichern.")

    # --- Kontext aufbauen ---
    keys      = load_keys()
    resellers = _get_resellers()

    total_keys   = len(keys)
    free_keys    = sum(1 for v in keys.values() if not v.get("hwid"))
    bound_keys   = sum(1 for v in keys.values() if v.get("hwid"))
    banned_keys  = sum(1 for v in keys.values() if v.get("banned"))
    expired_keys = sum(1 for v in keys.values() if _is_expired(v))

    reseller_info = []
    for rid, r in resellers.items():
        rc = sum(1 for v in keys.values() if v.get("reseller") == rid)
        reseller_info.append(
            f"  - {r.get('name','')} (@{r.get('username','')}, ID:{rid}): "
            f"{r.get('credits',0)} Credits, {rc} Keys, "
            f"{'DEAKTIVIERT' if r.get('disabled') else 'aktiv'}"
        )

    key_details = []
    for kid, v in list(keys.items())[:100]:
        status = ("banned"  if v.get("banned")    else
                  "paused"  if v.get("paused")     else
                  "expired" if _is_expired(v)      else
                  "active"  if v.get("hwid")       else "free")
        key_details.append(
            f"  {kid}: {v.get('type','std')}, {v.get('days','')}d, "
            f"status={status}, hwid={v.get('hwid','')[:12]}, "
            f"note={v.get('note','')}, reseller={v.get('reseller_name','')}"
        )

    system_prompt = f"""Du bist der HEXTRA License Server Admin-Assistent. Antworte auf Deutsch, praeÐ·ise und kurz.

AKTUELLE SERVER-DATEN:
  Keys: {total_keys} gesamt ({free_keys} frei, {bound_keys} aktiv, {banned_keys} gebannt, {expired_keys} abgelaufen)
  Reseller ({len(resellers)}):
{chr(10).join(reseller_info) if reseller_info else "    (keine Reseller)"}

  Keys (max. 100):
{chr(10).join(key_details) if key_details else "    (keine Keys)"}

WICHTIG:
- Nutze fuer JEDE Aktion (Key bannen, pausieren, generieren usw.) ausschliesslich die bereitgestellten Tools.
- Fuehre die Tools direkt aus, ohne vorher zu fragen, ausser bei destruktiven Aktionen (Loeschen).
- Bestatige nach jeder Aktion kurz, was du getan hast."""

    # --- Messages aufbauen ---
    messages = [{"role": "system", "content": system_prompt}]
    for msg in body.get("history", [])[-20:]:
        role = "assistant" if msg.get("role") == "model" else "user"
        messages.append({"role": role, "content": msg.get("text", "")})
    messages.append({"role": "user", "content": message})

    model = cfg.get("groq_model", "meta-llama/llama-4-scout-17b-16e-instruct")

    try:
        # â”€â”€ Primaeransatz: Function Calling â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        result = _groq_request(groq_key, {
            "model": model,
            "messages": messages,
            "tools": _AI_TOOLS,
            "tool_choice": "auto",
            "temperature": 0.3,
            "max_tokens": 2048
        })

        choice       = result["choices"][0]
        resp_message = choice["message"]
        tool_calls   = resp_message.get("tool_calls") or []
        actions_executed = []

        if tool_calls:
            # Tool-Calls ausfuehren
            messages.append(resp_message)
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                try:
                    fn_args = json.loads(tc["function"]["arguments"])
                except Exception as _e:
                    app.logger.warning("Suppressed error in ai_chat: %s", _e)
                    fn_args = {}
                tool_result = _ai_execute_tool(fn_name, fn_args)
                actions_executed.append(tool_result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_result
                })

            # Zweiter Call: natuerlichsprachige Bestaetigung holen
            result2  = _groq_request(groq_key, {
                "model": model,
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": 512
            })
            ai_text = result2["choices"][0]["message"].get("content", "Aktion ausgefuehrt.")

        else:
            # â”€â”€ Fallback 1: Legacy ```action``` Bloecke â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            ai_text = resp_message.get("content", "")
            if not ai_text:
                return jsonify(success=False, message="AI hat keine Antwort geliefert")

            legacy_results = _ai_parse_legacy_blocks(ai_text)
            actions_executed.extend(legacy_results)

            # â”€â”€ Fallback 2: NLP Intent-Parsing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            if not actions_executed:
                nlp_results = _ai_nlp_fallback(message)  # User-Nachricht, nicht AI-Text!
                actions_executed.extend(nlp_results)

            # Action-Bloecke aus dem angezeigten Text entfernen
            ai_text = re.sub(r'```action\s*\n.*?\n```', '', ai_text, flags=re.DOTALL).strip()

        if actions_executed:
            _audit(_get_admin_name(body), "ai_action", "", " | ".join(actions_executed))
        return jsonify(success=True, response=ai_text, actions=actions_executed)

    except urllib.error.HTTPError as e:
        err_body = ""
        try: err_body = e.read().decode()[:300]
        except Exception as _e:
            app.logger.warning("Suppressed error in ai_chat: %s", _e)
        return jsonify(success=False, message=f"Groq API Fehler ({e.code}): {err_body}")
    except Exception as ex:
        return jsonify(success=False, message=f"Fehler: {str(ex)}")



@app.route("/ai/config", methods=["POST"])
def ai_config():
    body = _get_body()
    err = _check_admin(body)
    if err: return err
    cfg = load_cfg()
    if "groq_api_key" in body:
        if _has_env_secret("groq_api_key"):
            return jsonify(success=False, message="groq_api_key is managed via environment")
        cfg["groq_api_key"] = body["groq_api_key"].strip()
        save_cfg(cfg)
        return jsonify(success=True)
    return jsonify(success=True, has_key=bool(_cfg_secret("groq_api_key", "")), env_managed=_has_env_secret("groq_api_key"))


@app.route("/")
def root():
    return redirect("/login")

@app.route("/login")
def login_page():
    aid, admin = _current_admin()
    if aid and admin:
        return redirect("/admin")
    rid, reseller = _current_reseller()
    if rid and reseller:
        return redirect(f"/shop/{rid}")
    return Response(LOGIN_HTML, mimetype="text/html")

@app.route("/admin")
def admin_page():
    aid, admin = _current_admin()
    if not (aid and admin):
        uid, user = _current_user()
        if not (uid and user and _user_has_admin_access(user)):
            return redirect("/login")
    return Response(ADMIN_HTML, mimetype="text/html")

@app.route("/reseller")
def reseller_page():
    return redirect("/login")

@app.route("/shop/<rid>")
def shop_page(rid):
    rid = (rid or "").strip().lower()
    resellers = _get_resellers()
    if rid not in resellers:
        return Response(
            '<!DOCTYPE html><html lang="de"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Shop nicht gefunden</title><style>body{margin:0;background:#0b0b0b;color:#c8c8c8;font-family:monospace;display:flex;align-items:center;justify-content:center;min-height:100vh}div{padding:32px;border:1px solid #1e1e1e;background:#0f0f0f;border-radius:14px;text-align:center}h1{margin:0 0 10px 0;color:#c4a0e8;font-size:22px}p{margin:0;color:#888}</style></head><body><div><h1>Shop nicht gefunden</h1><p>Der angeforderte Reseller existiert nicht.</p></div></body></html>',
            mimetype="text/html",
            status=404,
        )
    current_rid, current_reseller = _current_reseller()
    if current_rid and current_reseller:
        if current_rid != rid:
            return redirect(f"/shop/{current_rid}")
        return Response(RESELLER_HTML, mimetype="text/html")
    uid, user = _current_user()
    if not (uid and user and _user_has_reseller_access(user) and _user_reseller_id(user) == rid):
        return redirect(f"/login?r={rid}")
    return Response(RESELLER_HTML, mimetype="text/html")

@app.route("/admin/update")
def admin_update_page():
    aid, admin = _current_admin()
    if not (aid and admin):
        uid, user = _current_user()
        if not (uid and user and _user_has_admin_access(user)):
            return redirect("/login")
    return Response(UPDATE_HTML, mimetype="text/html")

@app.route("/admin/files")
def admin_files_page():
    aid, admin = _current_admin()
    if not (aid and admin):
        return redirect("/login")
    if not _admin_has_file_manager_access(admin):
        return redirect("/admin")
    return Response(FILES_HTML, mimetype="text/html")

@app.route("/files")
def files_page():
    actor, access_err = _check_file_manager_access()
    if access_err:
        return redirect("/login")
    return Response(FILES_HTML, mimetype="text/html")

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  AUDIT LOG PAGE  (/admin/audit-page)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

AUDIT_PAGE_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HEXTRA â€” Audit Log</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&display=swap');
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0a0a0a;--bg2:#0f0f0f;--bg3:#141414;--border:#1e1e1e;--border2:#2a2a2a;--text:#c8c8c8;--text2:#888;--text3:#555;--accent:#c4a0e8;--accent-bg:#c4a0e810;--green:#6a9a6a;--red:#c06060;--yellow:#b8a060;--font:'JetBrains Mono',Consolas,monospace}
html,body{height:100%;background:var(--bg);color:var(--text);font-family:var(--font);font-size:13px}
.topbar{display:flex;align-items:center;justify-content:space-between;padding:14px 28px;background:var(--bg2);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:100}
.topbar-title{font-size:15px;font-weight:700;color:var(--accent);letter-spacing:4px}
.topbar-sub{font-size:10px;color:var(--text3);letter-spacing:2px;margin-top:3px}
.topbar-right{display:flex;gap:10px;align-items:center}
.wrap{padding:28px;max-width:1400px;margin:0 auto}
.filters{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px;align-items:flex-end}
.filter-group{display:flex;flex-direction:column;gap:5px}
.filter-group label{font-size:9px;letter-spacing:2px;color:var(--text3);text-transform:uppercase;font-weight:600}
input,select{background:var(--bg2);border:1px solid var(--border2);border-radius:6px;color:var(--text);font-family:var(--font);font-size:12px;padding:8px 12px;outline:none;transition:border-color .2s}
input:focus,select:focus{border-color:var(--accent)}
input::placeholder{color:var(--text3)}
select option{background:var(--bg2)}
.btn{padding:9px 18px;border:none;border-radius:6px;font-family:var(--font);font-size:11px;font-weight:700;letter-spacing:2px;cursor:pointer;transition:all .15s;white-space:nowrap}
.btn-primary{background:var(--accent);color:#0a0a0a}.btn-primary:hover{background:#d4b0f8}
.btn-danger{background:#c0606020;color:var(--red);border:1px solid var(--red)}.btn-danger:hover{background:#c0606040}
.btn-ghost{background:transparent;color:var(--text2);border:1px solid var(--border2)}.btn-ghost:hover{border-color:var(--accent);color:var(--accent)}
.stats-bar{display:flex;gap:16px;margin-bottom:20px;flex-wrap:wrap}
.stat{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:12px 18px}
.stat-val{font-size:22px;font-weight:700;color:var(--accent)}
.stat-lbl{font-size:9px;color:var(--text3);letter-spacing:2px;margin-top:3px;text-transform:uppercase}
.table-wrap{overflow-x:auto;border-radius:10px;border:1px solid var(--border)}
table{width:100%;border-collapse:collapse}
thead tr{background:var(--bg3);border-bottom:1px solid var(--border2)}
th{padding:11px 14px;text-align:left;font-size:9px;letter-spacing:2px;color:var(--text3);text-transform:uppercase;font-weight:600;white-space:nowrap}
tbody tr{border-bottom:1px solid var(--border);transition:background .1s}
tbody tr:hover{background:var(--accent-bg)}
tbody tr:last-child{border-bottom:none}
td{padding:10px 14px;font-size:12px;vertical-align:middle}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:600;letter-spacing:1px;text-transform:uppercase}
.badge-red{background:#c0606020;color:var(--red);border:1px solid #c0606040}
.badge-green{background:#6a9a6a20;color:var(--green);border:1px solid #6a9a6a40}
.badge-purple{background:#c4a0e820;color:var(--accent);border:1px solid #c4a0e840}
.badge-yellow{background:#b8a06020;color:var(--yellow);border:1px solid #b8a06040}
.badge-gray{background:#44444420;color:var(--text2);border:1px solid #44444440}
.ts{color:var(--text3);font-size:11px;white-space:nowrap}
.target{color:var(--accent);font-size:11px;font-family:var(--font)}
.details{color:var(--text2);font-size:11px;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.empty{text-align:center;padding:60px;color:var(--text3);font-size:13px}
.auto-lbl{font-size:10px;color:var(--text3);display:flex;align-items:center;gap:6px}
.dot{width:7px;height:7px;border-radius:50%;background:var(--green);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.login-wall{display:flex;align-items:center;justify-content:center;min-height:100vh;flex-direction:column;gap:16px}
.login-card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:40px;width:340px}
.login-card h2{color:var(--accent);font-size:18px;letter-spacing:4px;margin-bottom:24px;text-align:center}
.field{margin-bottom:14px}
.field label{display:block;font-size:9px;letter-spacing:2px;color:var(--text3);margin-bottom:6px;text-transform:uppercase;font-weight:600}
.field input{width:100%}
.err{color:var(--red);font-size:11px;min-height:16px;margin-top:8px;text-align:center}
#loginWall{display:flex}.#mainWall{display:none}
</style>
<style id="hextra-replica-audit">
:root{
  --rep-bg:#0a0a0a;
  --rep-panel:#111111;
  --rep-panel-2:#181818;
  --rep-line:#212121;
  --rep-line-strong:#2b2b2b;
  --rep-text:#f0f0f0;
  --rep-dim:#999999;
  --rep-soft:#545454;
  --rep-red:#e60000;
  --rep-green:#25d68c;
  --rep-font:'Segoe UI',system-ui,sans-serif;
  --rep-mono:'Consolas',ui-monospace,monospace;
}
html,body{
  background:var(--rep-bg) !important;
  color:var(--rep-text) !important;
  font-family:var(--rep-font) !important;
}
.topbar,.stat,.table-wrap,.login-card,input,select,.btn{
  background:var(--rep-panel) !important;
  border:1px solid var(--rep-line) !important;
  box-shadow:none !important;
}
input,select,.btn-ghost,.btn-danger{
  background:var(--rep-panel-2) !important;
}
.topbar,.stat,.table-wrap,.login-card,input,select,.btn{
  border-radius:4px !important;
}
.topbar-title,.login-card h2,.stat-val,.target{
  color:var(--rep-text) !important;
  font-family:var(--rep-mono) !important;
}
.topbar-sub,.stat-lbl,.auto-lbl,.details,.field label,.err,.empty,th{
  color:var(--rep-dim) !important;
}
.btn-primary{
  background:var(--rep-red) !important;
  border:1px solid var(--rep-red) !important;
  color:#ffffff !important;
}
.btn-primary:hover,.btn:hover{
  transform:none !important;
}
input:focus,select:focus{
  border-color:var(--rep-red) !important;
  box-shadow:none !important;
}
thead tr{
  background:var(--rep-bg) !important;
  border-bottom-color:var(--rep-line) !important;
}
tbody tr{
  border-bottom-color:var(--rep-line) !important;
}
tbody tr:hover{
  background:var(--rep-panel-2) !important;
}
.badge,.badge-red,.badge-green,.badge-purple,.badge-yellow,.badge-gray{
  background:var(--rep-panel-2) !important;
  color:var(--rep-dim) !important;
  border:1px solid var(--rep-line) !important;
}
.dot{background:var(--rep-green) !important}
</style>
</head>
<body>

<!-- LOGIN WALL -->
<div id="loginWall" class="login-wall">
  <div class="login-card">
    <h2>HEXTRA AUDIT</h2>
    <div class="field"><label>Username</label><input id="lu" placeholder="admin" autocomplete="username" onkeydown="if(event.key==='Enter')document.getElementById('lp').focus()"></div>
    <div class="field"><label>Passwort</label><input type="password" id="lp" placeholder="â€¢â€¢â€¢â€¢â€¢â€¢â€¢â€¢" autocomplete="current-password" onkeydown="if(event.key==='Enter')doLogin()"></div>
    <button class="btn btn-primary" style="width:100%;margin-top:8px" onclick="doLogin()">ANMELDEN</button>
    <div class="err" id="lerr"></div>
  </div>
</div>

<!-- MAIN -->
<div id="mainWall" style="display:none">
  <div class="topbar">
    <div>
      <div class="topbar-title">HEXTRA AUDIT</div>
      <div class="topbar-sub">ADMIN ACTION LOG</div>
    </div>
    <div class="topbar-right">
      <div class="auto-lbl"><div class="dot" id="autoDot"></div><span id="autoLbl">Auto-Refresh: AN</span></div>
      <button class="btn btn-ghost" onclick="toggleAuto()">PAUSE</button>
      <button class="btn btn-ghost" onclick="loadAudit()">â†» REFRESH</button>
      <button class="btn btn-danger" onclick="clearLog()">âœ• LOG LEEREN</button>
      <button class="btn btn-ghost" onclick="logout()">LOGOUT</button>
    </div>
  </div>

  <div class="wrap">
    <div class="stats-bar">
      <div class="stat"><div class="stat-val" id="statTotal">â€“</div><div class="stat-lbl">EintrÃ¤ge gesamt</div></div>
      <div class="stat"><div class="stat-val" id="statShown">â€“</div><div class="stat-lbl">Angezeigt</div></div>
      <div class="stat"><div class="stat-val" id="statLast">â€“</div><div class="stat-lbl">Letzte Aktion</div></div>
    </div>

    <div class="filters">
      <div class="filter-group">
        <label>Aktion</label>
        <select id="fAction" onchange="loadAudit()"><option value="">Alle</option></select>
      </div>
      <div class="filter-group">
        <label>Admin</label>
        <input id="fAdmin" placeholder="Username..." style="width:140px" oninput="debounce()">
      </div>
      <div class="filter-group">
        <label>Suche</label>
        <input id="fQ" placeholder="Key-ID, IP, Details..." style="width:220px" oninput="debounce()">
      </div>
      <div class="filter-group">
        <label>Ab Datum</label>
        <input type="datetime-local" id="fSince" onchange="loadAudit()">
      </div>
      <div class="filter-group">
        <label>Limit</label>
        <select id="fLimit" onchange="loadAudit()">
          <option value="100">100</option>
          <option value="200" selected>200</option>
          <option value="500">500</option>
          <option value="1000">1000</option>
        </select>
      </div>
      <button class="btn btn-ghost" onclick="resetFilters()">Filter zurÃ¼cksetzen</button>
    </div>

    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Zeit</th><th>Admin</th><th>IP</th><th>Aktion</th><th>Ziel</th><th>Details</th>
        </tr></thead>
        <tbody id="tbody">
          <tr><td colspan="6" class="empty">Lade...</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<script>
let _autoTimer=null, _autoOn=true, _debTimer=null;

const ACTION_COLORS = {
  ban_key:'red', unban_key:'green', delete_key:'red', delete_reseller:'red',
  delete_admin:'red', audit_clear:'red', ip_ban:'red',
  genkey:'green', create_admin:'green', create_reseller:'green',
  pause_key:'yellow', unpause_key:'green', reset_hwid:'yellow',
  whitelist_add:'purple', whitelist_remove:'yellow', whitelist_toggle:'purple',
  edit_key:'gray', edit_reseller:'gray', settings_change:'gray',
  reseller_credits:'purple', assign_key:'purple',
  admin_login:'green', ai_action:'purple', ip_unban:'green'
};

function badgeFor(action){
  const col = ACTION_COLORS[action] || 'gray';
  return `<span class="badge badge-${col}">${action.replace(/_/g,' ')}</span>`;
}

function fmtTs(ts){
  if(!ts) return 'â€“';
  return ts.replace('T',' ').substring(0,19);
}

function doLogin(){
  _user = document.getElementById('lu').value.trim();
  _pass = document.getElementById('lp').value.trim();
  if(!_user||!_pass){document.getElementById('lerr').textContent='Bitte ausfÃ¼llen';return;}
  fetch('/admin/audit',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({username:_user,password:_pass,limit:1})})
  .then(r=>r.json()).then(d=>{
    if(d.success){
      document.getElementById('loginWall').style.display='none';
      document.getElementById('mainWall').style.display='block';
      loadAudit(); startAuto();
    } else {
      document.getElementById('lerr').textContent = d.message||'Fehler';
    }
  }).catch(()=>document.getElementById('lerr').textContent='Server nicht erreichbar');
}

function logout(){_user='';_pass='';location.reload();}

function loadAudit(){
  const body={username:_user,password:_pass,
    limit:  parseInt(document.getElementById('fLimit').value)||200,
    action: document.getElementById('fAction').value,
    admin:  document.getElementById('fAdmin').value.trim(),
    q:      document.getElementById('fQ').value.trim()
  };
  const since=document.getElementById('fSince').value;
  if(since) body.since=since.replace('T',' ');
  fetch('/admin/audit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
  .then(r=>r.json()).then(d=>{
    if(!d.success) return;
    // Update stats
    document.getElementById('statTotal').textContent = d.total;
    document.getElementById('statShown').textContent = d.entries.length;
    document.getElementById('statLast').textContent  = d.entries.length ? fmtTs(d.entries[0].ts).split(' ')[1] : 'â€“';
    // Update action dropdown
    const sel=document.getElementById('fAction');
    const cur=sel.value;
    sel.innerHTML='<option value="">Alle</option>';
    (d.actions||[]).forEach(a=>{
      const o=document.createElement('option');
      o.value=a; o.textContent=a.replace(/_/g,' ');
      if(a===cur) o.selected=true;
      sel.appendChild(o);
    });
    // Render table
    const tb=document.getElementById('tbody');
    if(!d.entries.length){tb.innerHTML='<tr><td colspan="6" class="empty">Keine EintrÃ¤ge gefunden</td></tr>';return;}
    tb.innerHTML=d.entries.map(e=>`
      <tr>
        <td class="ts">${fmtTs(e.ts)}</td>
        <td><b style="color:var(--accent)">${e.admin||'â€“'}</b></td>
        <td class="ts">${e.ip||'â€“'}</td>
        <td>${badgeFor(e.action||'')}</td>
        <td class="target">${e.target||'â€“'}</td>
        <td class="details" title="${(e.details||'').replace(/"/g,'&quot;')}">${e.details||'â€“'}</td>
      </tr>`).join('');
  });
}

function clearLog(){
  if(!confirm('Audit-Log wirklich komplett leeren?')) return;
  fetch('/admin/audit/clear',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({username:_user,password:_pass})})
  .then(r=>r.json()).then(d=>{if(d.success)loadAudit();});
}

function resetFilters(){
  document.getElementById('fAction').value='';
  document.getElementById('fAdmin').value='';
  document.getElementById('fQ').value='';
  document.getElementById('fSince').value='';
  document.getElementById('fLimit').value='200';
  loadAudit();
}

function debounce(){clearTimeout(_debTimer);_debTimer=setTimeout(loadAudit,400);}

function startAuto(){
  _autoTimer=setInterval(()=>{if(_autoOn)loadAudit();},10000);
}
function toggleAuto(){
  _autoOn=!_autoOn;
  document.getElementById('autoDot').style.background=_autoOn?'var(--green)':'var(--red)';
  document.getElementById('autoLbl').textContent='Auto-Refresh: '+(_autoOn?'AN':'AUS');
  document.querySelector('.topbar-right .btn-ghost').textContent=_autoOn?'PAUSE':'RESUME';
}

document.getElementById('lp').addEventListener('keydown',e=>{if(e.key==='Enter')doLogin();});
</script>
</body>
</html>"""

AUDIT_PAGE_SESSION_PATCH = r"""
<script>
(function(){
  function authPost(url, body){
    return fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})})
      .then(function(r){return r.json().then(function(d){return {status:r.status,data:d};});});
  }
  window.doLogin = function(){ window.location.href='/login'; };
  window.logout = function(){ fetch('/auth/logout',{method:'POST'}).finally(function(){ window.location.href='/login'; }); };
  window.loadAudit = function(){
    var body={
      limit: parseInt(document.getElementById('fLimit').value)||200,
      action: document.getElementById('fAction').value,
      admin: document.getElementById('fAdmin').value.trim(),
      q: document.getElementById('fQ').value.trim()
    };
    var since=document.getElementById('fSince').value;
    if(since) body.since=since.replace('T',' ');
    authPost('/admin/audit', body).then(function(res){
      var d=res.data||{};
      if(!d.success){
        if(res.status===401 || String(d.message||'').indexOf('Login')!==-1){ window.location.href='/login'; }
        return;
      }
      document.getElementById('statTotal').textContent = d.total;
      document.getElementById('statShown').textContent = d.entries.length;
      document.getElementById('statLast').textContent  = d.entries.length ? fmtTs(d.entries[0].ts).split(' ')[1] : 'â€“';
      var sel=document.getElementById('fAction');
      var cur=sel.value;
      sel.innerHTML='<option value="">Alle</option>';
      (d.actions||[]).forEach(function(a){
        var o=document.createElement('option');
        o.value=a; o.textContent=a.replace(/_/g,' ');
        if(a===cur) o.selected=true;
        sel.appendChild(o);
      });
      var tb=document.getElementById('tbody');
      if(!d.entries.length){tb.innerHTML='<tr><td colspan="6" class="empty">Keine EintrÃ¤ge gefunden</td></tr>';return;}
      tb.innerHTML=d.entries.map(function(e){
        return '<tr>'
          + '<td class="ts">'+fmtTs(e.ts)+'</td>'
          + '<td><b style="color:var(--accent)">'+(e.admin||'â€“')+'</b></td>'
          + '<td class="ts">'+(e.ip||'â€“')+'</td>'
          + '<td>'+badgeFor(e.action||'')+'</td>'
          + '<td class="target">'+(e.target||'â€“')+'</td>'
          + '<td class="details" title="'+String(e.details||'').replace(/"/g,'&quot;')+'">'+(e.details||'â€“')+'</td>'
          + '</tr>';
      }).join('');
    });
  };
  window.clearLog = function(){
    if(!confirm('Audit-Log wirklich komplett leeren?')) return;
    authPost('/admin/audit/clear', {}).then(function(res){
      if(res.data && res.data.success) window.loadAudit();
    });
  };
  fetch('/auth/me',{credentials:'same-origin'})
    .then(function(r){ if(!r.ok) throw 0; return r.json(); })
    .then(function(d){
      if(!d.success || !((d.role==='admin') || (d.role==='user' && d.access_admin))) throw 0;
      document.getElementById('loginWall').style.display='none';
      document.getElementById('mainWall').style.display='block';
      window.loadAudit();
      startAuto();
    })
    .catch(function(){ window.location.href='/login'; });
})();
</script>
"""


@app.route("/admin/audit-page")
def audit_page():
    aid, admin = _current_admin()
    if not (aid and admin):
        uid, user = _current_user()
        if not (uid and user and _user_has_admin_access(user)):
            return redirect("/login")
    return Response(AUDIT_PAGE_HTML.replace("</body>", AUDIT_PAGE_SESSION_PATCH + "\n</body>"), mimetype="text/html")


def _load_html(name, fallback="<h1>HEXTRA</h1><p>HTML template missing</p>"):
    base = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        f"{name}_shop_id_everything_ready.html",
        f"{name}_shop_everything.html",
        f"{name}.html",
    ]
    for filename in candidates:
        path = os.path.join(base, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as _e:
            app.logger.warning("Suppressed error in _load_html: %s", _e)
    return fallback

LOGIN_HTML    = _load_html("login",    r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>HEXTRA - Login</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&display=swap');
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0a0a0a;--bg2:#0f0f0f;--bg3:#141414;--border:#1e1e1e;--border2:#2a2a2a;--text:#c8c8c8;--text2:#888;--text3:#555;--text4:#333;--accent:#c4a0e8;--accent-bg:#c4a0e810;--green:#6a9a6a;--red:#c06060;--font:'JetBrains Mono','Consolas',monospace}
html,body{height:100%;background:var(--bg);color:var(--text);font-family:var(--font);font-size:13px;overflow:hidden}
.bg-grid{position:fixed;inset:0;background-image:linear-gradient(var(--border) 1px,transparent 1px),linear-gradient(90deg,var(--border) 1px,transparent 1px);background-size:60px 60px;opacity:.15;pointer-events:none}
.bg-glow{position:fixed;top:20%;left:50%;transform:translateX(-50%);width:500px;height:500px;background:radial-gradient(circle,#c4a0e808 0%,transparent 70%);pointer-events:none}
.wrap{display:flex;align-items:center;justify-content:center;min-height:100vh;position:relative;z-index:1}
.card{width:400px;padding:48px 44px;background:var(--bg2);border:1px solid var(--border);border-radius:16px;box-shadow:0 0 80px #c4a0e806,0 24px 60px #00000060;animation:fadeIn .4s ease}
@keyframes fadeIn{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}
.brand{text-align:center;margin-bottom:40px}
.brand-name{font-size:36px;font-weight:700;letter-spacing:14px;color:var(--accent);text-shadow:0 0 40px #c4a0e830}
.brand-sub{font-size:9px;letter-spacing:6px;color:var(--text4);margin-top:6px;font-weight:300}
.field{margin-bottom:18px;position:relative}
.field label{display:block;font-size:9px;letter-spacing:2px;color:var(--text3);margin-bottom:8px;font-weight:600;text-transform:uppercase}
.field input{width:100%;padding:13px 44px 13px 16px;background:var(--bg);border:1px solid var(--border2);border-radius:8px;color:var(--text);font-family:var(--font);font-size:13px;outline:none;transition:border-color .2s,box-shadow .2s;letter-spacing:.5px}
.field input:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg)}
.field input::placeholder{color:var(--text4);letter-spacing:1px}
.field .ico{position:absolute;right:14px;bottom:13px;font-size:15px;opacity:.25;pointer-events:none}
.remember{display:flex;align-items:center;gap:10px;margin:20px 0 6px;cursor:pointer;user-select:none}
.remember input{width:15px;height:15px;accent-color:var(--accent);cursor:pointer}
.remember span{font-size:11px;color:var(--text3);letter-spacing:.5px}
.login-btn{width:100%;padding:14px;margin-top:20px;background:var(--accent);color:#0a0a0a;border:none;border-radius:8px;font-family:var(--font);font-weight:700;font-size:13px;letter-spacing:3px;cursor:pointer;transition:all .15s}
.login-btn:hover{background:#d4b0f8;box-shadow:0 0 24px #c4a0e830}
.login-btn:active{transform:scale(.98)}
.login-btn:disabled{background:#1a1a1a;color:var(--text4);cursor:wait;transform:none;box-shadow:none}
.err{min-height:20px;margin-top:14px;font-size:11px;color:var(--red);text-align:center;letter-spacing:.5px}
.footer{margin-top:24px;text-align:center;font-size:9px;color:var(--text4);letter-spacing:1px}
</style>
</head>
<body>
<div class="bg-grid"></div><div class="bg-glow"></div>
<div class="wrap"><div class="card">
  <div class="brand"><div class="brand-name">HEXTRA</div><div class="brand-sub">LICENSE SERVER</div></div>
  <div class="field"><label>Benutzername</label><input id="user" placeholder="Username" autocomplete="username" autofocus onkeydown="if(event.key=='Enter')document.getElementById('pass').focus()"><span class="ico">&#128100;</span></div>
  <div class="field"><label>Passwort</label><input type="password" id="pass" placeholder="Passwort" autocomplete="current-password" onkeydown="if(event.key=='Enter')doLogin()"><span class="ico">&#128274;</span></div>
  <label class="remember" for="rem"><input type="checkbox" id="rem" checked><span>Angemeldet bleiben</span></label>
  <button class="login-btn" id="btn" onclick="doLogin()">ANMELDEN</button>
  <div class="err" id="err"></div>
  <div class="footer">Zugang nur mit Berechtigung</div>
</div></div>
<script>
function delCookie(n){document.cookie=n+'=;expires=Thu, 01 Jan 1970 00:00:00 UTC;path=/;SameSite=Strict'}
function clearLegacyCookies(){['hextra_token','hextra_role','hextra_user','hextra_name','hextra_reseller_id','defy_token','defy_role','defy_user','defy_name','defy_reseller_id'].forEach(delCookie)}
function redirectForRole(d){if(d.role==='admin')window.location.href='/admin';else window.location.href='/shop/'+encodeURIComponent(d.reseller_id)}
function doLogin(){
  var u=document.getElementById('user').value.trim(),p=document.getElementById('pass').value.trim();
  if(!u||!p){document.getElementById('err').textContent='Bitte alle Felder ausfuellen';return}
  var btn=document.getElementById('btn');btn.disabled=true;btn.textContent='Verbinde...';document.getElementById('err').textContent='';
  fetch('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,password:p,remember:document.getElementById('rem').checked})})
    .then(r=>r.json()).then(d=>{
      btn.disabled=false;btn.textContent='ANMELDEN';
      if(d.success){
        clearLegacyCookies();
        redirectForRole(d);
      } else {document.getElementById('err').textContent=d.message||'Login fehlgeschlagen';}
    }).catch(()=>{btn.disabled=false;btn.textContent='ANMELDEN';document.getElementById('err').textContent='Server nicht erreichbar';});
}
(function(){
  clearLegacyCookies();
  fetch('/auth/me',{credentials:'same-origin'})
    .then(function(r){if(!r.ok)return null;return r.json();})
    .then(function(d){if(d&&d.success)redirectForRole(d);})
    .catch(function(){});
})();
</script>
</body></html>""")
ADMIN_HTML    = _load_html("admin",    "<html><body><h1>Admin panel HTML not found</h1><p>Place admin.html next to server.py</p><script>fetch('/auth/me',{credentials:'same-origin'}).then(function(r){if(!r.ok)throw 0;return r.json();}).then(function(d){if(!d.success||d.role!=='admin')window.location.href='/login';}).catch(function(){window.location.href='/login';});</script></body></html>")
RESELLER_HTML = _load_html("reseller", "<html><body><h1>Reseller panel HTML not found</h1><p>Place reseller.html next to server.py</p><script>fetch('/auth/me',{credentials:'same-origin'}).then(function(r){if(!r.ok)throw 0;return r.json();}).then(function(d){if(!d.success||d.role!=='reseller')window.location.href='/login';}).catch(function(){window.location.href='/login';});</script></body></html>")
UPDATE_HTML   = _load_html("update",   "<html><body><h1>Update manager HTML not found</h1><p>Place update.html next to server.py</p><script>fetch('/auth/me',{credentials:'same-origin'}).then(function(r){if(!r.ok)throw 0;return r.json();}).then(function(d){if(!d.success||d.role!=='admin')window.location.href='/login';}).catch(function(){window.location.href='/login';});</script></body></html>")
FILES_HTML    = _load_html("files",    "<html><body><h1>File manager HTML not found</h1><p>Place files.html next to server.py</p><script>fetch('/auth/me',{credentials:'same-origin'}).then(function(r){if(!r.ok)throw 0;return r.json();}).then(function(d){if(!d.success||d.role!=='admin')window.location.href='/login';}).catch(function(){window.location.href='/login';});</script></body></html>")




if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

