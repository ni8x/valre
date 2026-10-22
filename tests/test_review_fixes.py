"""Regression tests for the Phase 6 review pass.

Each test here covers a claim the code review made about the server, checked against
the real export for `m1` where a real table is needed. Nothing fabricates replay data.
"""
from __future__ import annotations

import threading

import pytest

import app as app_mod
import stats as stats_mod

REFERENCE = "m1"
TABLE = "events"


@pytest.fixture()
def export_ready(store):
    if not (store.manifest_path(REFERENCE).exists()):
        pytest.skip("m1 has no export")
    path = store.export_dir(REFERENCE) / f"{TABLE}.parquet"
    if not path.exists():
        pytest.skip(f"m1 has no {TABLE} table")
    return path


def test_reader_registry_is_locked(store, export_ready, monkeypatch):
    """The reader cache is built under the registry lock.

    Proving it from the inside: while `TableReader` is being constructed from within
    `_reader`, this thread already holds the lock, so a non-blocking acquire must fail.
    """
    observed: list[bool] = []
    real_reader = app_mod.tables_mod.TableReader

    class _Probe(real_reader):  # type: ignore[misc, valid-type]
        def __init__(self, path):
            observed.append(app_mod._REG_LOCK.acquire(blocking=False))
            if observed[-1]:
                app_mod._REG_LOCK.release()
            super().__init__(path)

    monkeypatch.setattr(app_mod.tables_mod, "TableReader", _Probe)
    app_mod._READERS.pop((REFERENCE, TABLE), None)
    try:
        app_mod._reader(REFERENCE, TABLE)
    finally:
        app_mod._READERS.pop((REFERENCE, TABLE), None)

    assert observed == [False], "TableReader was built outside _REG_LOCK"


def test_concurrent_reader_access_is_safe(store, export_ready):
    """Reading and sweeping the cache from many threads at once must not raise."""
    app_mod._READERS.pop((REFERENCE, TABLE), None)
    errors: list[BaseException] = []
    readers: list[object] = []
    barrier = threading.Barrier(16)

    def work(i: int) -> None:
        barrier.wait()
        try:
            if i % 4 == 3:
                app_mod._drop_readers(REFERENCE)
            else:
                readers.append(app_mod._reader(REFERENCE, TABLE))
        except BaseException as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert readers
    assert all(isinstance(r, app_mod.tables_mod.TableReader) for r in readers)
    # A sweep may evict the reader mid-burst, and the next call rebuilds it; what must
    # never happen is two live readers for one key.
    assert len(app_mod._READERS) <= 1
    app_mod._READERS.pop((REFERENCE, TABLE), None)


def test_null_death_actor_credits_nobody():
    """A death with no actor id must not raise, and must credit nobody.

    A spike detonation kills with no killer actor, so `killer_char` is not guaranteed to
    be a number; the per-player counts used to cast it straight to int.
    """
    char2subj = {11: "killer", 22: "victim"}
    deaths = [
        {"killer_char": 11, "victim_char": 22},    # a normal kill
        {"killer_char": None, "victim_char": 22},  # a detonation: no killer to credit
        {"killer_char": 0, "victim_char": 0},      # id 0 maps to -1, so neither actor counts
        {"killer_char": 22, "victim_char": 22},    # a suicide credits no killer, but does die
    ]
    kills, dths = stats_mod._death_credits(deaths, char2subj)
    assert kills == {"killer": 1}
    assert dths == {"victim": 3}
