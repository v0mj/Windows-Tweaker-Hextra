"""Authentication, account state, and device identity helpers."""

from .legacy import (
    _account_days_left_text,
    _account_payload,
    _current_hwid,
    _dpapi_transform,
    clear_auth,
    load_auth,
    save_auth,
)

__all__ = [
    "_account_days_left_text",
    "_account_payload",
    "_current_hwid",
    "_dpapi_transform",
    "clear_auth",
    "load_auth",
    "save_auth",
]

