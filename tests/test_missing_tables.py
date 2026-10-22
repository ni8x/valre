"""A manifest-only export (interrupted export) must not crash the engines.

`stats._compute_events` read events.parquet unguarded, so a replay whose
manifest.json exists but whose events.parquet is missing made
GET /api/replays/{id}/stats raise FileNotFoundError (HTTP 500). The other
table reads in stats.py degrade gracefully, and metrics.py already guards
the same file, so the two engines disagreed.
"""

from pathlib import Path

import stats


def test_compute_events_returns_an_empty_skeleton_when_events_parquet_is_missing(tmp_path):
    missing = Path(tmp_path) / "events.parquet"
    order, rounds, deaths, ults, spikes = stats._compute_events(missing)
    assert order == []
    assert rounds == {}
    assert deaths == []
    assert ults == []
    assert spikes == []
