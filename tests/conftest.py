"""Shared fixtures for engine tests.

The tests drive the real server modules against the real exports in
``data/replays/<id>/export``. Nothing here fabricates replay data: where a test
needs a synthetic input it builds the documented event shape a pure function
consumes, and the assertions against real replays always use real engine output.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

REFERENCE = "m1"  # Sunset, the match that has an OP.GG report for parity


@pytest.fixture(scope="session")
def store():
    import store as store_mod

    return store_mod.store


def ingested_ids():
    """Replay ids that have a finished export (manifest.json present)."""
    import store as store_mod

    out = []
    for rec in store_mod.store.list_all():
        rid = rec["id"]
        try:
            if store_mod.store.manifest_path(rid).exists():
                out.append(rid)
        except Exception:
            continue
    return out


@pytest.fixture(scope="session")
def all_replays():
    ids = ingested_ids()
    if not ids:
        pytest.skip("no ingested replays with an export")
    return ids


@pytest.fixture(scope="session")
def m1_export(store):
    if not store.manifest_path(REFERENCE).exists():
        pytest.skip(f"{REFERENCE} export not available")
    return REFERENCE


@pytest.fixture(scope="session")
def m1_metrics(store):
    import metrics

    if not store.manifest_path(REFERENCE).exists():
        pytest.skip(f"{REFERENCE} export not available")
    # `_compute` and not `build`: the cache path would let a session assert against
    # a stats.json/metrics.json written by an older build (or by the currently
    # shipped version) instead of the code under test, and would rewrite the
    # shipped cache as a side effect of running the tests.
    return metrics._compute(store, REFERENCE)


@pytest.fixture(scope="session")
def m1_stats(store):
    import stats

    if not store.manifest_path(REFERENCE).exists():
        pytest.skip(f"{REFERENCE} export not available")
    return stats._compute(store, REFERENCE)
