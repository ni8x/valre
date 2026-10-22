"""Phase 14: the offline evidence picker and the Riot sync.

A replay carries no Riot ID, so "which one is me" is always subject + evidence.
These tests drive the real export for the facts a replay does hold (agent, map,
start time, first-half side) and never touch the network: the Riot path is
exercised with a fake transport only.
"""
import datetime as dt
import json

import pytest

import identity
import riot

FRACTURE = "1424bb2e-841e-42cb-86c4-ef5cbd59e206"


@pytest.fixture(scope="module")
def replay(all_replays):
    return FRACTURE if FRACTURE in all_replays else all_replays[0]


@pytest.fixture(scope="module")
def sig(store, replay):
    return identity.signature(store, replay)


@pytest.mark.slow
def test_signature_holds_the_four_facts(sig):
    assert sig["map"]
    assert isinstance(sig["start_ms"], int) and sig["start_ms"] > 0
    assert sig["sides_first"].get("attacker") in ("Blue", "Red")
    assert sig["sides_first"].get("defender") in ("Blue", "Red")
    assert len(sig["players"]) >= 10
    for p in sig["players"]:
        assert p["subject"]
        assert p["team"] in ("Blue", "Red")
        assert p["side_first"] in ("attacker", "defender")
        assert p["agent"] is not None
        assert p["pawns"]


@pytest.mark.slow
def test_full_unique_ask_is_certain(store, replay, sig):
    """One subject answers agent + side + time and nobody else does."""
    picks = {}
    for p in sig["players"]:
        picks.setdefault((p["agent"], p["side_first"]), []).append(p["subject"])
    unique = [p for p in sig["players"] if len(picks[(p["agent"], p["side_first"])]) == 1]
    if not unique:
        pytest.skip("no player with a unique agent and side in this export")
    me = unique[0]
    out = identity.identify(
        store, replay, agent=me["agent"], side=me["side_first"], at=sig["start_local"]
    )
    top = out["candidates"][0]
    assert top["subject"] == me["subject"]
    assert top["score"] == 3
    assert top["certain"] is True
    assert out["asked"]["facts"] == 3


@pytest.mark.slow
def test_nothing_asked_guesses_nothing(store, replay):
    out = identity.identify(store, replay)
    assert not any(c["certain"] for c in out["candidates"])
    assert all(c["score"] == 0 for c in out["candidates"])
    assert "nothing is guessed" in out["note"].lower()


@pytest.mark.slow
def test_a_wrong_agent_never_scores(store, replay):
    out = identity.identify(store, replay, agent="zzz-not-an-agent")
    assert not any(c["certain"] for c in out["candidates"])
    assert all(c["score"] == 0 for c in out["candidates"])


@pytest.mark.slow
def test_time_tolerance_reads_both_iso_and_epoch(store, replay, sig):
    start = dt.datetime.fromtimestamp(sig["start_ms"] / 1000.0, dt.timezone.utc)
    late = (start + dt.timedelta(minutes=30)).isoformat()
    near = identity.identify(store, replay, at=late, tolerance_min=60)
    assert all(c["facts"]["time"] for c in near["candidates"])
    far = identity.identify(store, replay, at=late, tolerance_min=10)
    assert not any(c["facts"]["time"] for c in far["candidates"])
    epoch = identity.identify(store, replay, at=str(sig["start_ms"]))
    assert all(c["facts"]["time"] for c in epoch["candidates"])


def test_parse_at_refuses_junk():
    assert identity._parse_at(None) == (None, None)
    when, why = identity._parse_at("not a date")
    assert when is None and why
    when, why = identity._parse_at("2026-04-19T21:43:00Z")
    assert when is not None and when.tzinfo is not None and why is None
    when, why = identity._parse_at("1776615224674")
    assert when is not None and why is None


@pytest.mark.slow
def test_riot_is_off_without_a_key(store, replay, monkeypatch):
    monkeypatch.delenv("RIOT_API_KEY", raising=False)

    def boom(*_a, **_k):
        raise AssertionError("no network call without a key")

    monkeypatch.setattr(riot, "_get", boom)
    st = riot.status()
    assert st["configured"] is False
    assert "RIOT_API_KEY" not in json.dumps(st.get("note", "")) or "No RIOT_API_KEY" in st["note"]
    out = riot.tag(store, replay, "somebody", "AP")
    assert out["subject"] is None
    assert out["why"]


@pytest.mark.slow
def test_riot_tag_matches_the_export_by_agent_and_team(store, replay, sig, monkeypatch):
    monkeypatch.setenv("RIOT_API_KEY", "fake")
    me = next(p for p in sig["players"] if p["agent"] and p["pawns"])
    tail = sig["level"].rstrip("/").split("/")[-1]
    matchlist = {
        "history": [
            {"matchId": "AP1_1", "gameStartTimeMillis": sig["start_ms"], "mapId": f"/Game/Maps/{tail}/{tail}"}
        ]
    }
    # match-v5 for VALORANT: matchInfo + players (not metadata/info/participants)
    match = {
        "matchInfo": {
            "matchId": "AP1_1",
            "gameStartMillis": sig["start_ms"],
            "mapId": f"/Game/Maps/{tail}/{tail}",
        },
        "players": [
            {"puuid": "p1", "characterId": me["agent_uuid"], "teamId": me["team"]}
        ],
    }

    def fake(url, timeout=12):
        if "accounts/by-riot-id" in url:
            return {"puuid": "p1"}
        if "matchlists" in url:
            return matchlist
        if "matches/AP1_1" in url:
            return match
        raise AssertionError(f"unexpected call: {url}")

    monkeypatch.setattr(riot, "_get", fake)
    out = riot.tag(store, replay, "somebody", "AP")
    assert out["subject"] == me["subject"]
    assert out["why"] is None
    assert out["evidence"]["map"]
