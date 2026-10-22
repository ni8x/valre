"""The death audit: pure classification plus one real-export check."""

import pytest

import deaths


def _death(
    t,
    victim,
    killer,
    victim_team="Blue",
    killer_team="Red",
    first_blood=False,
    traded=False,
    isolated=True,
    bait=False,
    suicide=False,
):
    return {
        "t": t,
        "victim": victim,
        "killer": killer,
        "victim_team": victim_team,
        "killer_team": killer_team,
        "first_blood": first_blood,
        "traded": traded,
        "isolated": isolated,
        "bait": bait,
        "suicide": suicide,
    }


def _player(subject, team, per_round, agent_uuid="agent-1"):
    return {"subject": subject, "team": team, "agent_uuid": agent_uuid, "per_round": per_round}


def _payload(rounds, players):
    return {"rounds": rounds, "players": players}


def _classes(block):
    return {c["key"]: c["n"] for c in block["classes"]}


def test_ult_cost_uses_the_charge_before_a_cast():
    # The half-time re-send of a zero must not lower the cost: a cast is the proof.
    points = [(100, 1), (200, 8), (300, 0), (400, 3)]
    assert deaths._ult_cost(points, [250, 450]) == 8
    assert deaths._ult_cost(points, [150]) == 1


def test_ult_cost_is_unknown_without_a_cast():
    assert deaths._ult_cost([(100, 4)], []) is None
    assert deaths._ult_cost([], [100]) is None
    assert deaths._ult_cost([], []) is None


def test_a_death_carries_its_round_context():
    rounds = [
        {
            "round": 1,
            "start_ms": 10_000,
            "end_ms": 40_000,
            "winner": "Red",
            "deaths": [_death(22_000, "P1", "P2", first_blood=True, traded=False, isolated=True)],
        }
    ]
    players = [
        _player("P1", "Blue", [{"round": 1, "damage_opponents": 0.0}]),
        _player("P2", "Red", [{"round": 1, "damage_opponents": 150.0}]),
    ]
    rep = deaths.summarize(_payload(rounds, players))
    (block,) = [p for p in rep["players"] if p["subject"] == "P1"]
    (event,) = block["events"]
    assert event["round"] == 1
    assert event["t"] == 22_000
    assert event["round_ms"] == 12_000
    assert event["round_won"] is False
    assert event["killer"] == "P2"
    assert event["killer_team"] == "Red"
    assert event["killer_agent"] == "agent-1"
    assert event["killer_survived"] is True
    assert event["damage_round"] == 0.0
    assert event["dealt_no_damage"] is True
    assert event["ult_points"] is None
    assert event["ult_ready"] is None
    assert _classes(block) == {"untraded": 1, "isolated": 1, "no_damage": 1, "opening": 1}
    assert block["deaths"] == 1
    assert rep["totals"]["deaths"] == 1
    assert rep["n_rounds"] == 1


def test_a_killer_who_dies_later_did_not_survive():
    rounds = [
        {
            "round": 1,
            "start_ms": 0,
            "winner": "Blue",
            "deaths": [
                _death(1_000, "P1", "P2"),
                _death(3_000, "P2", "P1"),
            ],
        }
    ]
    players = [_player("P1", "Blue", []), _player("P2", "Red", [])]
    rep = deaths.summarize(_payload(rounds, players))
    by_subject = {p["subject"]: p["events"] for p in rep["players"]}
    assert by_subject["P1"][0]["killer_survived"] is False
    assert by_subject["P2"][0]["killer_survived"] is False


def test_a_self_inflicted_death_is_kept_and_never_a_survivor():
    rounds = [
        {
            "round": 24,
            "start_ms": 0,
            "winner": "Red",
            "deaths": [_death(5_000, "P1", "P1", suicide=True, traded=False, isolated=True)],
        }
    ]
    players = [_player("P1", "Blue", [{"round": 24, "damage_opponents": 12.0}])]
    rep = deaths.summarize(_payload(rounds, players))
    (block,) = rep["players"]
    (event,) = block["events"]
    assert event["suicide"] is True
    assert event["killer_survived"] is False
    assert _classes(block) == {"untraded": 1, "isolated": 1, "self_inflicted": 1}
    assert rep["totals"]["self_inflicted"] == 1


def test_an_ultimate_is_only_called_charged_when_the_cost_is_known():
    rounds = [
        {
            "round": 1,
            "start_ms": 0,
            "winner": "Red",
            "deaths": [_death(20_000, "P1", "P2")],
        }
    ]
    players = [_player("P1", "Blue", [{"round": 1, "damage_opponents": 40.0}])]
    charged = {"P1": {"cost": 7, "casts": [19_000], "points": [(19_000, 7)], "active": [(19_000, True)]}}
    rep = deaths.summarize(_payload(rounds, players), charged)
    (block,) = rep["players"]
    (event,) = block["events"]
    assert event["ult_cost"] == 7
    assert event["ult_points"] == 7
    assert event["ult_ready"] is True
    assert event["ult_active"] is True
    assert _classes(block)["ult_ready"] == 1

    # Same charge, no known cost (this player never cast one): no claim either way.
    unknown = {"P1": {"cost": None, "casts": [], "points": [(19_000, 7)], "active": []}}
    rep = deaths.summarize(_payload(rounds, players), unknown)
    (block,) = rep["players"]
    (event,) = block["events"]
    assert event["ult_points"] == 7
    assert event["ult_ready"] is None
    assert "ult_ready" not in _classes(block)


def test_damage_that_is_missing_stays_missing():
    rounds = [
        {
            "round": 1,
            "start_ms": 0,
            "winner": None,
            "deaths": [_death(1_000, "P1", "P2")],
        }
    ]
    players = [_player("P1", "Blue", [{"round": 1, "damage_opponents": None}])]
    rep = deaths.summarize(_payload(rounds, players))
    (block,) = rep["players"]
    (event,) = block["events"]
    assert event["damage_round"] is None
    assert event["dealt_no_damage"] is None
    assert event["round_won"] is None
    assert "no_damage" not in _classes(block)


def test_deaths_are_sorted_by_time_and_strangers_are_ignored():
    rounds = [
        {
            "round": 2,
            "start_ms": 30_000,
            "winner": "Blue",
            "deaths": [_death(50_000, "P1", "P2"), _death(20_000, "GHOST", "P2")],
        }
    ]
    players = [_player("P1", "Blue", [{"round": 2, "damage_opponents": 10.0}])]
    rep = deaths.summarize(_payload(rounds, players))
    (block,) = rep["players"]
    assert block["deaths"] == 1
    assert rep["totals"]["deaths"] == 1


@pytest.mark.slow
def test_a_real_export_matches_the_stats_engine(store, m1_stats):
    ult = deaths._ultimates(store.export_dir("m1"), m1_stats["players"])
    rep = deaths.summarize(m1_stats, ult)
    assert rep["ult_available"] is True
    assert rep["n_rounds"] == 24
    assert len(rep["players"]) == 10
    total = sum(p["deaths"] for p in rep["players"])
    assert rep["totals"]["deaths"] == total
    assert total == m1_stats["overview"]["total_kills_events"]
    assert all(p["deaths"] > 0 for p in rep["players"])
    # One first death per round, and every m1 player cast an ultimate.
    assert rep["totals"]["opening"] == 24
    costs = {e["ult_cost"] for p in rep["players"] for e in p["events"]}
    assert costs == {7, 8}
    assert rep["totals"]["self_inflicted"] == 1
