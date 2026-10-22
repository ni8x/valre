"""Cached table readers must not survive a re-export.

`app._READERS` holds open `TableReader` (pyarrow ParquetFile) handles per
`(replay_id, table)`. A re-export replaces the parquet files under the same
replay id, so any handle cached before the export refers to a deleted file.
These tests run at import time without a live server.
"""

from pathlib import Path

import app as app_mod


def test_drop_readers_removes_only_that_replay():
    app_mod._READERS.clear()
    app_mod._READERS[("a", "fields")] = object()
    app_mod._READERS[("a", "events")] = object()
    app_mod._READERS[("b", "fields")] = object()

    dropped = app_mod._drop_readers("a")

    assert dropped == 2
    assert ("a", "fields") not in app_mod._READERS
    assert ("a", "events") not in app_mod._READERS
    assert ("b", "fields") in app_mod._READERS
    app_mod._READERS.clear()


def test_run_export_drops_cached_readers_for_the_replay(monkeypatch):
    app_mod._READERS.clear()
    app_mod._READERS[("x", "fields")] = object()
    app_mod._READERS[("y", "fields")] = object()

    def boom(*_args, **_kwargs):
        raise RuntimeError("export failed on purpose")

    monkeypatch.setattr(app_mod.vrfkit, "export", boom)

    app_mod._run_export("x", Path("does-not-exist.vrf"))

    assert ("x", "fields") not in app_mod._READERS
    assert ("y", "fields") in app_mod._READERS
    app_mod.JOBS.pop("x", None)
    app_mod._READERS.clear()

class _FakeStore:
    """Minimal store stub: dir()/export_dir()/update() only."""

    def __init__(self, root: Path):
        self.root = root
        self.updates: list[tuple[str, dict]] = []

    def dir(self, replay_id: str) -> Path:
        return self.root / replay_id

    def export_dir(self, replay_id: str) -> Path:
        return self.root / replay_id / "export"

    def update(self, replay_id: str, **fields):
        self.updates.append((replay_id, fields))
        return None


def test_run_export_deletes_stale_json_caches_for_that_replay(monkeypatch, tmp_path):
    fake = _FakeStore(tmp_path)
    for rid in ("x", "y"):
        d = fake.dir(rid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "stats.json").write_text("{}", encoding="utf-8")
        (d / "metrics.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(app_mod, "store", fake)

    def boom(*_args, **_kwargs):
        raise RuntimeError("export failed on purpose")

    monkeypatch.setattr(app_mod.vrfkit, "export", boom)

    app_mod._run_export("x", Path("does-not-exist.vrf"))

    assert not (fake.dir("x") / "stats.json").exists()
    assert not (fake.dir("x") / "metrics.json").exists()
    assert (fake.dir("y") / "stats.json").exists()
    assert (fake.dir("y") / "metrics.json").exists()
    app_mod.JOBS.pop("x", None)
