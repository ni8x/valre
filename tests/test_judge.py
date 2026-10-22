"""The judge: the blame maths, the counter-case and the drills.

Every payload here is built by hand so a test can name one fact and check that
exactly that fact produced the number. The last test runs the real reference
replay through the same code and ties its verdict back to the stats engine.
"""

import pytest

import judge


def _p(subject, team, stats=None, per_round=None):
    return {
        "subject": subject,
        "team": team,
        "agent_uuid": "agent-" + subject,
        "stats": stats or {},
        "per_round": per_round or [],
    }


def _d(t, victim, victim_team, killer=None, killer_team=None, first_blood=False,
       traded=True, isolated=False, suicide=False):
    return {
        "t": t,
        "victim": victim,
        "victim_team": victim_team,
        "killer": killer,
        "killer_team": killer_team,
        "first_blood": first_blood,
        "traded": traded,
        "isolated": isolated,
        "suicide": suicide,
    }


def _r(rn, winner, deaths=(), blue_buy="full", red_buy="full", overtime=False):
    return {
        "round": rn,
        "winner": winner,
        "overtime": overtime,
        "sides": {"attacker": "Blue", "defender": "Red"},
        "deaths": list(deaths),
        "economy": {"Blue": {"buy": blue_buy}, "Red": {"buy": red_buy}},
    }


def _players(stats=None, rows=None):
    """A 2v2: Blue b1, b2 and Red r1, r2."""
    return [
        _p("b1", "Blue", (stats or {}).get("b1"), (rows or {}).get("b1")),
        _p("b2", "Blue", (stats or {}).get("b2"), (rows or {}).get("b2")),
        _p("r1", "Red", (stats or {}).get("r1"), (rows or {}).get("r1")),
        _p("r2", "Red", (stats or {}).get("r2"), (rows or {}).get("r2")),
    ]


def _payload(rounds, players=None, stats=None, rows=None):
    return {
        "rounds": rounds,
        "players": players if players is not None else _players(stats, rows),
    }


def _by_subject(rep, subject):
    return next(p for p in rep["players"] if p["subject"] == subject)


def _faults(block):
    return {f["key"]: f["n"] for f in block["faults"]}


def _round(rep, rn):
    return next(lr for lr in rep["lost_rounds"] if lr["round"] == rn)


# --- the fault list -----------------------------------------------------------


def test_faults_are_only_counted_in_rounds_the_team_lost():
    # Blue wins, so the only death (a Red player) is the losing side's fault and
    # nothing at all is charged to Blue.
    rounds = [_r(1, "Blue", [_d(1000, "r1", "Red", "b1", "Blue", first_blood=True, traded=False)])]
    rep = judge.summarize(_payload(rounds))
    assert _faults(_by_subject(rep, "r1")) == {"opening_death": 1, "untraded_death": 1}
    assert _by_subject(rep, "r1")["points"] == 5
    assert _by_subject(rep, "b1")["points"] == 0
    assert _faults(_by_subject(rep, "b1")) == {}


def test_one_death_can_carry_several_faults_with_the_published_weights():
    rounds = [
        _r(
            1,
            "Blue",
            [_d(1000, "r1", "Red", "b1", "Blue", first_blood=True, traded=False, isolated=True)],
        )
    ]
    rows = {"r1": [{"round": 1, "damage_opponents": 0.0}]}
    rep = judge.summarize(_payload(rounds, rows=rows))
    block = _by_subject(rep, "r1")
    assert _faults(block) == {
        "opening_death": 1,
        "untraded_death": 1,
        "no_damage": 1,
        "isolated_death": 1,
    }
    # 3 + 2 + 2 + 1
    assert block["points"] == 8
    assert block["faults"][0]["key"] == "opening_death"
    assert block["case_against"][0] == "died first in the round in 1 round"


def test_damage_that_is_missing_is_not_a_fault():
    # No per_round row at all: we do not know the damage, so nothing is charged.
    rounds = [_r(1, "Blue", [_d(1000, "r1", "Red", "b1", "Blue")])]
    rep = judge.summarize(_payload(rounds))
    assert "no_damage" not in _faults(_by_subject(rep, "r1"))


def test_an_ultimate_is_only_charged_when_the_charge_was_full_at_that_moment():
    rounds = [
        _r(1, "Blue", [_d(700, "r1", "Red", "b1", "Blue")]),
        _r(2, "Blue", [_d(1000, "r1", "Red", "b1", "Blue")]),
    ]
    ult = {"r1": {"cost": 7, "points": [[500, 3], [900, 7]], "casts": [950], "active": []}}
    rep = judge.summarize(_payload(rounds), ult=ult)
    block = _by_subject(rep, "r1")
    # only the death at 1000, where the charge had reached the cost
    assert _faults(block)["ult_charged"] == 1
    assert rep["ult_available"] is True


def test_a_missed_trade_is_charged_to_teammates_alive_in_trade_range():
    # r1 dies untraded with a teammate inside trade range, so r2 could have
    # traded and did not; r3 is not on the team, so it is never charged.
    rounds = [
        _r(
            1,
            "Blue",
            [
                _d(1000, "r1", "Red", "b1", "Blue", traded=False, isolated=False),
                _d(4000, "b1", "Blue", "r2", "Red"),
                _d(5000, "r2", "Red", "b2", "Blue"),
                _d(6000, "b2", "Blue", "r2", "Red"),
            ],
        )
    ]
    rep = judge.summarize(_payload(rounds))
    assert _by_subject(rep, "r2")["missed_trades"] == 1
    assert _faults(_by_subject(rep, "r2"))["missed_trade"] == 1
    assert _by_subject(rep, "b1")["missed_trades"] == 0


def test_an_isolated_death_is_nobody_elses_missed_trade():
    rounds = [
        _r(1, "Blue", [_d(1000, "r1", "Red", "b1", "Blue", traded=False, isolated=True)])
    ]
    rep = judge.summarize(_payload(rounds))
    assert _by_subject(rep, "r2")["missed_trades"] == 0


# --- the round: buy, lead, plant ---------------------------------------------


def test_changeable_reads_the_buy_classes():
    rounds = [
        _r(1, "Blue", [_d(1000, "r1", "Red", "b1", "Blue")], blue_buy="full", red_buy="eco"),
        _r(2, "Blue", [_d(2000, "r1", "Red", "b1", "Blue")], blue_buy="full", red_buy="force"),
        _r(3, "Blue", [_d(3000, "r1", "Red", "b1", "Blue")], blue_buy="pistol", red_buy="pistol"),
        _r(4, "Blue", [_d(4000, "r1", "Red", "b1", "Blue")], blue_buy="full", red_buy="full"),
    ]
    rep = judge.summarize(_payload(rounds))
    assert _round(rep, 1)["changeable"] is False
    assert _round(rep, 2)["changeable"] is True
    assert _round(rep, 3)["changeable"] is True
    assert _round(rep, 4)["changeable"] is True
    assert _round(rep, 1)["buy"] == "eco"
    assert _round(rep, 1)["opponent_buy"] == "full"


def test_losing_a_lead_is_named_only_when_the_lead_was_two():
    # Blue lose two first, so Red hold a two player lead, then Red lose the round.
    rounds = [
        _r(
            1,
            "Blue",
            [
                _d(1000, "b1", "Blue", "r1", "Red"),
                _d(2000, "b2", "Blue", "r1", "Red"),
                _d(3000, "r1", "Red", "b1", "Blue"),
                _d(4000, "r2", "Red", "b2", "Blue", first_blood=False),
            ],
        )
    ]
    rep = judge.summarize(_payload(rounds))
    assert _round(rep, 1)["max_lead_lost"] == 2
    assert "lost_a_lead" in {c["key"] for c in _round(rep, 1)["classes"]}


def test_the_plant_classes_tell_who_planted():
    rounds = [
        _r(1, "Blue", [_d(1000, "r1", "Red", "b1", "Blue")]),
        _r(2, "Blue", [_d(2000, "r1", "Red", "b1", "Blue")]),
        _r(3, "Blue", [_d(3000, "r1", "Red", "b1", "Blue")]),
    ]
    bombs = {1: {"planter": "r1"}, 2: {"planter": "b1"}}
    rep = judge.summarize(_payload(rounds), bombs=bombs)
    assert {c["key"] for c in _round(rep, 1)["classes"]} == {"lost_after_plant"}
    assert {c["key"] for c in _round(rep, 2)["classes"]} == {"retake_failed"}
    assert {c["key"] for c in _round(rep, 3)["classes"]} == {"no_plant"}
    assert rep["plant_available"] is True


def test_a_round_without_a_winner_is_never_judged():
    rounds = [
        _r(1, None, [_d(1000, "r1", "Red", "b1", "Blue")]),
        _r(2, "Blue", [_d(2000, "r1", "Red", "b1", "Blue")]),
    ]
    rep = judge.summarize(_payload(rounds))
    assert [lr["round"] for lr in rep["lost_rounds"]] == [2]
    assert rep["n_rounds"] == 2


# --- grades and the counter-case ---------------------------------------------


def test_grades_are_bands_of_the_team_share():
    assert judge._grade(5.0) == "A"
    assert judge._grade(12.0) == "A"
    assert judge._grade(20.0) == "C"
    assert judge._grade(28.0) == "D"
    assert judge._grade(40.0) == "E"


def test_the_share_is_of_the_players_own_team():
    # r1 owns every fault on Red; b1 owns the one Blue fault, so each is 100%
    # of his own team's points.
    rounds = [
        _r(1, "Blue", [_d(1000, "r1", "Red", "b1", "Blue", first_blood=True)]),
        _r(2, "Red", [_d(2000, "b1", "Blue", "r1", "Red", first_blood=True)]),
    ]
    rep = judge.summarize(_payload(rounds))
    assert _by_subject(rep, "r1")["share_pct"] == 100.0
    assert _by_subject(rep, "r1")["grade"] == "E"
    assert _by_subject(rep, "b1")["share_pct"] == 100.0
    assert rep["teams"]["Blue"]["points"] == _by_subject(rep, "b1")["points"]
    assert rep["teams"]["Blue"]["lost"] == 1


def test_the_counter_case_names_the_facts_it_has():
    rounds = [_r(1, "Blue", [_d(1000, "r1", "Red", "b1", "Blue", first_blood=True)])]
    stats = {
        "r1": {
            "deaths": 5,
            "traded_deaths": 3,
            "entry_kills": 4,
            "entry_deaths": 5,
            "multikills": {"2": 2, "3": 1},
        }
    }
    rep = judge.summarize(_payload(rounds, stats=stats))
    case_for = _by_subject(rep, "r1")["case_for"]
    assert "3 of his deaths were traded by a teammate" in case_for
    assert "he won 4 of the 9 opening duels he took" in case_for
    assert "3 multi-kill rounds, one of them a 3k" in case_for


def test_a_player_with_no_facts_gets_a_plain_counter_case():
    rounds = [_r(1, "Blue", [_d(1000, "r1", "Red", "b1", "Blue")])]
    rep = judge.summarize(_payload(rounds))
    assert _by_subject(rep, "r2")["case_for"] == ["no mitigating fact stood out in this match"]


def test_entry_order_is_the_rank_of_the_first_damage():
    rounds = [
        _r(
            1,
            "Blue",
            [
                _d(4500, "r2", "Red", "b1", "Blue"),
                _d(4800, "r2", "Red", "b1", "Blue"),
                _d(6000, "r1", "Red", "b1", "Blue"),
                _d(7000, "b1", "Blue", "r2", "Red"),
            ],
        )
    ]
    contacts = {1: {"r1": {"first": 5000, "n": 4}, "b1": {"first": 6000, "n": 2}}}
    rep = judge.summarize(_payload(rounds), contacts=contacts)
    assert _by_subject(rep, "r1")["entry_first"] == 1
    assert _by_subject(rep, "r1")["entry_avg_order"] == 1.0
    assert _by_subject(rep, "b1")["entry_first"] == 0
    assert rep["contact_available"] is True


def test_entering_only_after_two_teammates_died_is_counted_late():
    # r2 dies at 1000 and again would be absurd, so use two different teammates:
    # r1 is the one who arrives late, after two own deaths.
    rounds = [
        _r(
            1,
            "Blue",
            [
                _d(1000, "r2", "Red", "b1", "Blue"),
                _d(2000, "r2", "Red", "b2", "Blue"),
                _d(9000, "r2", "Red", "b1", "Blue"),
                _d(9500, "b1", "Blue", "r1", "Red"),
                _d(9600, "b2", "Blue", "r1", "Red"),
            ],
        )
    ]
    contacts = {1: {"r1": {"first": 8000, "n": 1}}}
    rep = judge.summarize(_payload(rounds), contacts=contacts)
    assert _by_subject(rep, "r1")["entry_late"] == 1


def test_a_team_that_lost_nothing_is_reported_as_such():
    rounds = [_r(1, "Blue", [_d(1000, "r1", "Red", "b1", "Blue")])]
    rep = judge.summarize(_payload(rounds))
    assert rep["teams"]["Blue"]["lost"] == 0
    assert rep["teams"]["Blue"]["ranked"][0]["points"] == 0
    assert "lost 0 rounds" in rep["teams"]["Blue"]["summary"]


def test_the_weights_and_bands_are_published():
    rounds = [_r(1, "Blue", [])]
    rep = judge.summarize(_payload(rounds))
    assert rep["weights"]["opening_death"] == 3
    assert rep["weights"]["missed_trade"] == 1
    assert rep["grade_bands"][0] == [12.0, "A"]
    assert rep["notes"] == judge.NOTES


# --- the drills -----------------------------------------------------------------


def test_only_the_bad_side_of_a_threshold_becomes_a_drill():
    bad = judge.training({"pre_aim_deg": 14.77, "pre_aim_n": 53}, {}, 24)
    assert [w["key"] for w in bad] == ["crosshair_placement"]
    assert bad[0]["severity"] == 1.0
    assert bad[0]["threshold"] == 8.0
    good = judge.training({"pre_aim_deg": 5.5, "pre_aim_n": 53}, {}, 24)
    assert good == []


def test_a_thin_sample_is_never_a_weakness():
    assert judge.training({"pre_aim_deg": 14.77, "pre_aim_n": 5}, {}, 24) == []


def test_severity_is_bounded_and_the_threshold_reads_a_half():
    assert judge._severity(3.0, 3.0, 8.0) == 0.0
    assert judge._severity(8.0, 3.0, 8.0) == 0.5
    assert judge._severity(13.0, 3.0, 8.0) == 1.0
    assert judge._severity(90.0, 3.0, 8.0) == 1.0


def test_a_low_trade_rate_is_a_weakness_and_a_high_one_is_not():
    low = judge.training({}, {"traded_pct": 15.0, "deaths": 20}, 24)
    assert [w["key"] for w in low] == ["trades"]
    high = judge.training({}, {"traded_pct": 72.0, "deaths": 20}, 24)
    assert [w["key"] for w in high] == []


def test_the_ultimate_rate_uses_the_cast_events():
    low = judge.training({}, {"ults_used": 2}, 24)
    assert [w["key"] for w in low] == ["ultimate"]
    assert low[0]["value"] == 0.08
    assert low[0]["n"] == 24
    high = judge.training({}, {"ults_used": 12}, 24)
    assert [w["key"] for w in high] == []


def test_no_data_is_no_drill_and_no_ability_cast_drill_exists():
    assert judge.training(None, None, 0) == []
    assert judge.training({}, {}, 24) == []
    assert "utility" not in {spec["key"] for spec in judge.TRAINING}


def test_drills_are_ordered_by_how_far_past_the_threshold_they_are():
    out = judge.training(
        {"pre_aim_deg": 14.77, "pre_aim_n": 53, "spray_spread_deg": 2.47,
         "shots_classified": 413},
        {"traded_pct": 15.0, "deaths": 20},
        24,
    )
    assert [w["key"] for w in out] == ["crosshair_placement", "spray_control", "trades"]
    assert out[0]["severity"] >= out[1]["severity"] >= out[2]["severity"]


# --- the real replay ------------------------------------------------------------


@pytest.mark.slow
def test_a_real_replay_is_judged_from_its_own_facts(store, m1_stats):
    rep = judge.build(store, "m1")

    assert rep["replay_id"] == "m1"
    assert rep["n_rounds"] == 24
    assert rep["plant_available"] and rep["contact_available"] and rep["ult_available"]

    # every round has a winner, so every round has exactly one losing team
    winners = {int(r["round"]): r.get("winner") for r in m1_stats["rounds"] if r.get("winner")}
    assert len(rep["lost_rounds"]) == len(winners) == 24
    for lr in rep["lost_rounds"]:
        assert lr["loser"] != winners[lr["round"]]
        assert lr["winner"] == winners[lr["round"]]

    lost = {
        t: sum(1 for lr in rep["lost_rounds"] if lr["loser"] == t) for t in ("Blue", "Red")
    }
    assert lost == {"Blue": 13, "Red": 11}
    assert rep["teams"]["Blue"]["lost"] == 13
    assert rep["teams"]["Red"]["lost"] == 11
    # the reference match had a plant in 16 rounds, all of them lost rounds
    assert sum(1 for lr in rep["lost_rounds"] if lr["plant"]) == 16

    total = sum(p["points"] for p in rep["players"])
    assert total == rep["teams"]["Blue"]["points"] + rep["teams"]["Red"]["points"]
    for p in rep["players"]:
        assert p["grade"] in ("A", "B", "C", "D", "E")
        assert p["share_pct"] is not None
        assert p["case_for"]
        for w in rep["training"][p["subject"]]:
            assert w["n"] >= 1 and 0.0 < w["severity"] <= 1.0
            assert w["drill"]
