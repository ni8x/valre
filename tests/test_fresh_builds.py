"""Proves the version-gated cache contract of the two whole-replay engines.

Both engines cache their payload at ``<replay>/<engine>.json`` and reuse it only
when the payload's ``version`` equals the engine's version constant; ``fresh=True``
must always recompute. These tests replace ``_compute`` with a recording stub and
redirect ``store.dir`` to a tmp directory, so neither a real parquet scan nor a real
cache file is involved — the shipped caches under ``data/replays`` are never written.
"""

import json

import pytest

import metrics
import stats
import store as sm


@pytest.mark.slow
@pytest.mark.parametrize(
    ("module", "version_attr", "cache_name"),
    [
        (stats, "STATS_VERSION", "stats.json"),
        (metrics, "METRICS_VERSION", "metrics.json"),
    ],
    ids=["stats", "metrics"],
)
def test_version_gate_reuses_and_fresh_recomputes(
    monkeypatch, tmp_path, module, version_attr, cache_name
):
    version = getattr(module, version_attr)
    calls = []

    def fake_compute(store, replay_id):
        calls.append(replay_id)
        return {"version": version, "replay_id": replay_id, "stub": len(calls)}

    monkeypatch.setattr(sm.store, "dir", lambda replay_id: tmp_path)
    monkeypatch.setattr(module, "_compute", fake_compute)

    first = module.build(sm.store, "m1")
    assert calls == ["m1"], "the first build must compute"
    assert first["version"] == version
    assert (tmp_path / cache_name).exists(), "the payload must be cached"

    cached = module.build(sm.store, "m1")
    assert calls == ["m1"], "a matching cache version must not recompute"
    assert cached == first

    fresh = module.build(sm.store, "m1", fresh=True)
    assert calls == ["m1", "m1"], "fresh=True must bypass a matching cache"
    assert fresh["stub"] == 2

    assert json.loads((tmp_path / cache_name).read_text(encoding="utf-8")) == fresh


@pytest.mark.slow
@pytest.mark.parametrize(
    ("module", "version_attr", "cache_name"),
    [
        (stats, "STATS_VERSION", "stats.json"),
        (metrics, "METRICS_VERSION", "metrics.json"),
    ],
    ids=["stats", "metrics"],
)
def test_a_payload_from_an_older_version_is_rebuilt(
    monkeypatch, tmp_path, module, version_attr, cache_name
):
    version = getattr(module, version_attr)
    calls = []

    def fake_compute(store, replay_id):
        calls.append(replay_id)
        return {"version": version, "replay_id": replay_id, "stub": len(calls)}

    monkeypatch.setattr(sm.store, "dir", lambda replay_id: tmp_path)
    monkeypatch.setattr(module, "_compute", fake_compute)
    (tmp_path / cache_name).write_text(
        json.dumps({"version": version - 1, "note": "stale"}), encoding="utf-8"
    )

    payload = module.build(sm.store, "m1")
    assert calls == ["m1"], "a stale cache version must be rebuilt"
    assert payload["version"] == version
    assert "note" not in payload, "the stale body must be discarded"
