"""Unit tests for the opening-duel and round-consistency context helpers.

These cover the pure functions over the engine's own shapes; the end-to-end
assertions on the reference replay live in ``tests/test_engines_m1.py``.
"""

import stats


def _round(deaths):
    return {"deaths": deaths}


def _death(killer, victim, suicide=False):
    return {"killer": killer, "victim": victim, "suicide": suicide}


def test_entry_duels_credits_killer_and_victim():
    rounds = {
        1: _round([_death("a", "b")]),
        2: _round([_death("b", "c")]),
    }
    out = stats._entry_duels(rounds, [1, 2])
    assert out["a"] == {"duels": 1, "wins": 1, "first_deaths": 0, "win_pct": 100.0}
    assert out["b"] == {"duels": 2, "wins": 1, "first_deaths": 1, "win_pct": 50.0}
    assert out["c"] == {"duels": 1, "wins": 0, "first_deaths": 1, "win_pct": 0.0}


def test_entry_duels_ignores_rounds_without_deaths():
    rounds = {1: _round([]), 2: {}}
    assert stats._entry_duels(rounds, [1, 2]) == {}


def test_entry_duels_gives_no_win_for_a_suicide():
    rounds = {1: _round([_death("a", "a", suicide=True)])}
    out = stats._entry_duels(rounds, [1])
    assert out["a"] == {"duels": 1, "wins": 0, "first_deaths": 1, "win_pct": 0.0}


def test_entry_duels_gives_no_win_when_no_killer_is_recorded():
    rounds = {1: _round([_death(None, "a")])}
    out = stats._entry_duels(rounds, [1])
    assert out["a"] == {"duels": 1, "wins": 0, "first_deaths": 1, "win_pct": 0.0}


def test_entry_duels_only_reads_the_first_death_of_a_round():
    rounds = {1: _round([_death("a", "b"), _death("c", "d")])}
    out = stats._entry_duels(rounds, [1])
    assert set(out) == {"a", "b"}


def test_round_consistency_summarises_scores():
    rows = [
        {"kills": 2, "deaths": 0, "damage_opponents": 400.0},
        {"kills": 0, "deaths": 1, "damage_opponents": 200.0},
        {"kills": 1, "deaths": 1, "damage_opponents": None},
    ]
    out = stats._round_consistency(rows)
    assert out == {"mean": 3.0, "stdev": 2.16, "median": 2.0,
                   "best": 6.0, "worst": 1.0, "n": 3}


def test_round_consistency_single_round_has_zero_stdev():
    out = stats._round_consistency([{"kills": 3, "deaths": 1, "damage_opponents": 100.0}])
    assert out["n"] == 1
    assert out["stdev"] == 0.0
    assert out["mean"] == out["median"] == out["best"] == out["worst"] == 6.5


def test_round_consistency_empty_is_none_not_zero():
    out = stats._round_consistency([])
    assert out["n"] == 0
    assert out["mean"] is None and out["stdev"] is None and out["median"] is None
    assert out["best"] is None and out["worst"] is None


def test_clean_rounds_require_a_kill_and_no_death():
    rows = [
        {"kills": 1, "deaths": 0},
        {"kills": 2, "deaths": 1},
        {"kills": 0, "deaths": 0},
        {"kills": 0, "deaths": 1},
    ]
    assert stats._clean_rounds(rows) == 1
