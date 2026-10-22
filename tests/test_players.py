"""The cross-match player layer: pure pooling rules, local settings, and a real read.

Nothing here fabricates replay data. The real tests read the caches the engines
already wrote and never build or rewrite one; the settings tests redirect the
settings file at a temporary path so a test run cannot touch this machine's own
identity.
"""

import json
from pathlib import Path

import pytest

import players
import settings


class _FakeStore:
    """Just enough of the store for the match list: an order and a lookup."""

    def __init__(self, records):
        self._records = records

    def ids(self):
        return [r.get("id") for r in self._records]

    def get(self, replay_id):
        for r in self._records:
            if r.get("id") == replay_id:
                return r
        return None


def _ready(replay_id, match_id, **extra):
    return {
        "id": replay_id,
        "status": "ready",
        "summary": {"friendly_name": match_id},
        **extra,
    }


# ---- pure helpers ----------------------------------------------------------


def test_a_ratio_without_a_denominator_is_missing_not_zero():
    assert players._pct(3, 0) is None
    assert players._div(3, 0) is None
    assert players._pct(3, 6) == 50.0
    assert players._div(3, 6) == 0.5
    assert players._r(None) is None
    assert players._int("7") == 7
    assert players._int(None) is None


def test_a_mean_ignores_missing_values():
    assert players._mean([]) is None
    assert players._mean([None, None]) is None
    assert players._mean([1.0, None, 3.0]) == 2.0


def test_pooling_weights_each_value_by_its_own_sample_size():
    value, n = players._weighted([(10.0, 4), (None, 8), (20.0, 6)])
    assert value == 16.0 and n == 10
    value, n = players._weighted([(None, 5)])
    assert value is None and n == 0
    value, n = players._weighted([])
    assert value is None and n == 0


def test_a_bucket_derives_its_rates_and_nulls_an_empty_one():
    bucket = players._finish_bucket(
        {"rounds": 2, "kills": 3, "deaths": 1, "damage": 300.0, "kast_rounds": 1, "hs_kills": 2}
    )
    assert bucket["kd"] == 3.0
    assert bucket["adr"] == 150.0
    assert bucket["kast_pct"] == 50.0
    assert bucket["hs_pct"] == 66.7
    empty = players._finish_bucket({"rounds": 0, "kills": 0, "deaths": 0, "damage": 0.0})
    assert empty["kd"] is None and empty["adr"] is None
    assert empty["kast_pct"] is None and empty["hs_pct"] is None


def test_buckets_add_up():
    into = {}
    players._add_bucket(into, {"rounds": 2, "kills": 3, "deaths": 1, "damage": 100.0})
    players._add_bucket(into, {"rounds": 3, "kills": 1, "deaths": 4, "damage": 50.0})
    assert into["rounds"] == 5 and into["kills"] == 4 and into["deaths"] == 5
    assert into["damage"] == 150.0


def test_a_grade_uses_the_published_bands():
    assert players._grade(None) is None
    assert players._grade(5.0) == "A"
    assert players._grade(12.0) == "A"
    assert players._grade(16.0) == "B"
    assert players._grade(20.0) == "C"
    assert players._grade(25.0) == "D"
    assert players._grade(40.0) == "E"


# ---- the match list -------------------------------------------------------


def test_matches_dedupe_by_match_id_and_keep_the_first_replay():
    store = _FakeStore(
        [
            _ready("r-b", "match-1"),
            _ready("r-a", "match-1"),
            _ready("r-c", "match-2"),
        ]
    )
    matches = players._matches(store)
    assert [m["match_id"] for m in matches] == ["match-1", "match-2"]
    assert matches[0]["replay_id"] == "r-b"
    assert matches[0]["duplicates"] == ["r-a"]
    assert matches[1]["duplicates"] == []


def test_matches_skip_a_replay_that_is_not_ready_or_has_no_summary():
    store = _FakeStore(
        [
            {"id": "r1", "status": "pending", "summary": {"friendly_name": "m"}},
            {"id": "r2", "status": "ready", "summary": {}},
            _ready("r3", "m"),
        ]
    )
    matches = players._matches(store)
    assert len(matches) == 1 and matches[0]["replay_id"] == "r3"


def test_the_fingerprint_follows_the_match_set_and_the_versions(monkeypatch):
    matches = [{"match_id": "a", "replay_id": "r1", "duplicates": []}]
    first = players._fingerprint(matches)
    assert first == players._fingerprint(list(matches))
    moved = [{"match_id": "a", "replay_id": "r2", "duplicates": []}]
    assert players._fingerprint(moved) != first
    monkeypatch.setattr(players, "DEEP_VERSION", players.DEEP_VERSION + 1)
    assert players._fingerprint(matches) != first


# ---- local settings -------------------------------------------------------


def test_settings_round_trip_on_a_temporary_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)
    assert settings.load() == {"version": settings.SETTINGS_VERSION, "me": None, "names": {}}
    settings.set_me("subject-a")
    settings.set_name("subject-a", "me on Red")
    settings.set_name("subject-b", "duo")
    reloaded = settings.load()
    assert settings.me() == "subject-a"
    assert settings.names()["subject-b"] == "duo"


def test_settings_survive_a_corrupt_file_and_drop_blank_names(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)
    path.write_text("{not json", encoding="utf-8")
    assert settings.me() is None and settings.names() == {}
    settings.set_name("subject-a", "keep")
    settings.set_name("subject-a", "   ")
    assert "subject-a" not in settings.names()


def test_clearing_the_identity_writes_through(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)
    settings.set_me("subject-a")
    settings.set_me(None)
    assert settings.me() is None
    assert json.loads(path.read_text(encoding="utf-8"))["me"] is None


# ---- the real caches -----------------------------------------------------


@pytest.mark.slow
def test_the_catalog_reads_every_distinct_match(store, all_replays):
    if len(all_replays) < 2:
        pytest.skip("needs at least two ingested replays")
    rep = players.catalog(store)
    assert rep["match_count"] == len(rep["matches"])
    assert rep["match_count"] >= 2
    assert rep["skipped"] == []
    assert len({m["match_id"] for m in rep["matches"]}) == rep["match_count"]
    for m in rep["matches"]:
        assert len(m["players"]) >= 2
    assert rep["players"], "no player was aggregated"
    subjects = {p["subject"] for p in rep["players"]}
    assert rep["likely_me"] in subjects
    assert rep["players"][0]["likely_me"] is True
    for p in rep["players"]:
        assert p["matches"] >= 1
        assert len(p["match_ids"]) == p["matches"]
    assert rep["deep"]["matches"] == rep["match_count"]


@pytest.mark.slow
def test_a_profile_pools_the_matches_it_came_from(store, all_replays):
    catalog = players.catalog(store)
    subject = catalog["likely_me"]
    rep = players.profile(store, subject)
    assert rep["subject"] == subject
    assert rep["match_count"] == len(rep["matches"])
    assert rep["match_count"] == catalog["players"][0]["matches"]
    rounds = sum(m["rounds"] for m in rep["matches"])
    assert rep["pooled"]["rounds"] == rounds
    assert rep["pooled"]["kills"] == sum(m["kills"] for m in rep["matches"])
    assert rep["pooled"]["deaths"] == sum(m["deaths"] for m in rep["matches"])
    assert rep["pooled"]["kd"] == players._div(rep["pooled"]["kills"], rep["pooled"]["deaths"])
    assert 0.0 <= rep["pooled"]["kast_pct"] <= 100.0
    assert rep["deep"] is None and rep["deep_state"] is not None
    # pooled and the mean of matches are both published, and they can differ
    assert set(rep["mean_of_matches"]) >= {"adr", "acs", "kd", "kast_pct"}
    assert rep["sides"]["attack"]["rounds"] + rep["sides"]["defend"]["rounds"] <= rounds
    assert [w["shots"] for w in rep["weapons"]] == sorted(
        (w["shots"] for w in rep["weapons"]), reverse=True
    )
    assert len(rep["trend"]) == len(rep["matches"])
    for cell in rep["aim"].values():
        if isinstance(cell, dict):
            assert cell["n"] >= 0


@pytest.mark.slow
def test_an_unknown_subject_is_not_a_player(store):
    with pytest.raises(KeyError):
        players.profile(store, "00000000-0000-0000-0000-000000000000")
