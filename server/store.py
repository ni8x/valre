"""Replay registry: on-disk layout + JSON index of imported replays.

Layout (all under DATA_DIR):
  replays/<id>/source.vrf      original upload / linked replay
  replays/<id>/export/         vrfkit export output (parquet + manifest.json)
  replays/<id>/meta.json       registry record (see ReplayStore)
  index.json                   {"replays": [id, ...]} ordered newest-first
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REPLAYS_DIR = DATA_DIR / "replays"
INDEX_PATH = DATA_DIR / "index.json"

_lock = threading.RLock()


def _now_ms() -> int:
    return int(time.time() * 1000)


def slugify(text: str, fallback: str = "replay") -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip()).strip("-").lower()
    return s[:40] or fallback


class ReplayStore:
    """Thread-safe registry backed by a JSON index file."""

    def __init__(self) -> None:
        REPLAYS_DIR.mkdir(parents=True, exist_ok=True)
        if not INDEX_PATH.exists():
            INDEX_PATH.write_text(json.dumps({"replays": []}), encoding="utf-8")

    # ---- index -------------------------------------------------------
    def _read_index(self) -> dict[str, Any]:
        try:
            return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {"replays": []}

    def _write_index(self, index: dict[str, Any]) -> None:
        tmp = INDEX_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(index, indent=2), encoding="utf-8")
        tmp.replace(INDEX_PATH)

    def ids(self) -> list[str]:
        with _lock:
            return list(self._read_index().get("replays", []))

    # ---- paths -------------------------------------------------------
    def dir(self, replay_id: str) -> Path:
        return REPLAYS_DIR / replay_id

    def source_path(self, replay_id: str) -> Path:
        return self.dir(replay_id) / "source.vrf"

    def export_dir(self, replay_id: str) -> Path:
        return self.dir(replay_id) / "export"

    def manifest_path(self, replay_id: str) -> Path:
        return self.export_dir(replay_id) / "manifest.json"

    def meta_path(self, replay_id: str) -> Path:
        return self.dir(replay_id) / "meta.json"

    # ---- records -----------------------------------------------------
    def new_id(self, hint: str) -> str:
        with _lock:
            base = slugify(hint)
            existing = set(self.ids())
            candidate = base
            while candidate in existing:
                candidate = f"{base}-{uuid.uuid4().hex[:6]}"
            return candidate

    def create(self, hint: str, original_name: str) -> dict[str, Any]:
        with _lock:
            replay_id = self.new_id(hint)
            self.dir(replay_id).mkdir(parents=True, exist_ok=True)
            record = {
                "id": replay_id,
                "original_name": original_name,
                "title": original_name,
                "status": "pending",
                "error": None,
                "created_at": _now_ms(),
                "updated_at": _now_ms(),
                "summary": None,
            }
            self.save(record, index_new=True)
            return record

    def save(self, record: dict[str, Any], index_new: bool = False) -> None:
        with _lock:
            record["updated_at"] = _now_ms()
            self.meta_path(record["id"]).write_text(
                json.dumps(record, indent=2), encoding="utf-8"
            )
            if index_new:
                index = self._read_index()
                ids = [i for i in index.get("replays", []) if i != record["id"]]
                ids.insert(0, record["id"])
                index["replays"] = ids
                self._write_index(index)

    def get(self, replay_id: str) -> dict[str, Any] | None:
        path = self.meta_path(replay_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def update(self, replay_id: str, **fields: Any) -> dict[str, Any] | None:
        with _lock:
            record = self.get(replay_id)
            if record is None:
                return None
            record.update(fields)
            self.save(record)
            return record

    def delete(self, replay_id: str) -> bool:
        import shutil

        with _lock:
            index = self._read_index()
            if replay_id not in index.get("replays", []):
                return False
            index["replays"] = [i for i in index["replays"] if i != replay_id]
            self._write_index(index)
            d = self.dir(replay_id)
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
            return True

    def list_all(self) -> list[dict[str, Any]]:
        return [r for i in self.ids() if (r := self.get(i)) is not None]


store = ReplayStore()
