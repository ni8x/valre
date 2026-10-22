"""Local settings for this machine: who you are, and the labels you give people.

Kept in ``data/settings.json``, inside the git-ignored ``data/`` directory. Nothing here is a Riot ID:
the export carries none (probed 2026-10-18 - the account-identity structure is deliberately
obfuscated), so an identity is the ``subject`` GUID the export itself uses, and a label is a nickname
this machine keeps. A missing or corrupt file reads as empty settings rather than raising, because a
settings file must never be able to break a page.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SETTINGS_PATH = DATA_DIR / "settings.json"
SETTINGS_VERSION = 1

_lock = threading.RLock()


def _blank() -> dict[str, Any]:
    return {"version": SETTINGS_VERSION, "me": None, "names": {}}


def load() -> dict[str, Any]:
    """The current settings, always a well-formed dict."""
    with _lock:
        try:
            raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return _blank()
        if not isinstance(raw, dict):
            return _blank()
        names = raw.get("names")
        me = raw.get("me")
        return {
            "version": SETTINGS_VERSION,
            "me": me if isinstance(me, str) and me else None,
            "names": {str(k): str(v) for k, v in names.items()} if isinstance(names, dict) else {},
        }


def save(settings: dict[str, Any]) -> dict[str, Any]:
    """Write settings atomically and return the cleaned copy that was written."""
    with _lock:
        names = settings.get("names") if isinstance(settings.get("names"), dict) else {}
        me = settings.get("me")
        clean = {
            "version": SETTINGS_VERSION,
            "me": me if isinstance(me, str) and me else None,
            "names": {str(k): str(v) for k, v in names.items() if str(v).strip()},
        }
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(clean, indent=2), encoding="utf-8")
        tmp.replace(SETTINGS_PATH)
        return clean


def me() -> str | None:
    return load()["me"]


def set_me(subject: str | None) -> dict[str, Any]:
    s = load()
    s["me"] = subject or None
    return save(s)


def names() -> dict[str, str]:
    return load()["names"]


def set_name(subject: str, label: str) -> dict[str, Any]:
    s = load()
    s["names"][subject] = label
    return save(s)
