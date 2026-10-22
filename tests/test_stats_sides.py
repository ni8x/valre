"""Unit tests for the attack/defence split.

``stats._side_split`` is a pure function over the documented per-round row shape,
so these tests build that shape directly. The end-to-end assertions against real
replay data live in ``test_engines_m1.py``.
"""
import stats


def _sides(attacker):
    return {"attacker": attacker, "defender": "Red" if attacker == "Blue" else "Blue"}


def _rounds(*attackers):
    return {i + 1: {"round": i + 1, "sides": _sides(a)} for i, a in enumerate(attackers)}


def _row(round_no, *, kills=0, deaths=0, assists=0, damage=0.0, kast=False,
         hs_kills=0, first_blood=False, first_death=False):
    # `damage` is damage dealt to opponents (the ADR basis the split aggregates).
    return {"round": round_no, "kills": kills, "deaths": deaths, "assists": assists,
            "damage": damage, "damage_opponents": damage, "kast": kast, "hs_kills": hs_kills,
            "first_blood": first_blood, "first_death": first_death}


def test_side_split_counts_only_rounds_that_player_attacked():
    rounds = _rounds("Blue", "Red", "Blue")
    rows = [_row(1, kills=2, damage=200.0, kast=True, first_blood=True),
            _row(2, kills=1, damage=100.0, kast=True),
            _row(3, kills=3, damage=300.0, deaths=1, hs_kills=2)]
    out = stats._side_split(rows, "Blue", rounds)
    assert out["attack"]["rounds"] == 2
    assert out["attack"]["kills"] == 5
    assert out["attack"]["deaths"] == 1
    assert out["attack"]["damage"] == 500.0
    assert out["attack"]["adr"] == 250.0
    assert out["attack"]["kast_rounds"] == 1
    assert out["attack"]["kast_pct"] == 50.0
    assert out["attack"]["entry_kills"] == 1
    assert out["attack"]["entry_deaths"] == 0
    assert out["attack"]["hs_kills"] == 2
    assert out["attack"]["hs_pct"] == 40.0
    assert out["defend"]["rounds"] == 1
    assert out["defend"]["kills"] == 1
    assert out["defend"]["deaths"] == 0
    assert out["defend"]["kd"] == 1.0


def test_side_split_skips_rounds_without_sides():
    rounds = _rounds("Blue")
    rounds[1]["sides"] = None
    out = stats._side_split([_row(1, kills=4)], "Blue", rounds)
    assert out["attack"]["rounds"] == 0
    assert out["attack"]["kills"] == 0
    assert out["attack"]["adr"] is None


def test_side_split_without_a_team_is_empty_not_wrong():
    out = stats._side_split([_row(1, kills=4)], None, _rounds("Blue"))
    assert out["attack"]["rounds"] == 0
    assert out["attack"]["kills"] == 0
    assert out["defend"]["rounds"] == 0


def test_side_split_rates_are_none_when_no_rounds_were_played():
    out = stats._side_split([], "Blue", {})
    assert out["attack"]["rounds"] == 0
    assert out["attack"]["kast_pct"] is None
    assert out["attack"]["hs_pct"] is None
    assert out["attack"]["kd"] is None
    assert out["attack"]["adr"] is None


def test_side_split_ignores_a_round_the_team_was_not_in():
    rounds = {1: {"round": 1, "sides": {"attacker": "Blue", "defender": "Red"}}}
    out = stats._side_split([_row(1, kills=3)], "Green", rounds)
    assert out["attack"]["rounds"] == 0
    assert out["defend"]["rounds"] == 0
