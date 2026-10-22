"""Overtime rounds carry no sides: the two-half model stops at regulation."""
import pytest

import stats


def _rounds(n):
    return {i: {"round": i, "start_ms": i * 1000, "end_ms": i * 1000 + 900,
                "switch_teams_ms": 12_500}
            for i in range(1, n + 1)}


def _defused(t, team):
    return {"t": t, "kind": "defused", "defuser_team": team}


def test_regulation_has_no_untrusted_rounds():
    assert stats._untrusted_side_rounds(list(range(1, 25))) == set()


def test_rounds_past_regulation_are_untrusted():
    assert stats._untrusted_side_rounds(list(range(1, 26))) == {25}
    assert stats._untrusted_side_rounds(list(range(1, 28))) == {25, 26, 27}


def test_overtime_is_marked_and_carries_no_side():
    """A defusal in overtime must not flip the regulation second half.

    Round 20 (second half) is defused by Blue, but overtime rounds 25 and 26 are
    defused by Red. If those votes counted, Red would win the second half and
    every regulation round after the swap would get the wrong sides.
    """
    rounds = _rounds(27)
    spikes = [
        _defused(3000, "Red"),    # round 3, first half -> first defenders = Red
        _defused(20_000, "Blue"),  # round 20, second half -> second defenders = Blue
        _defused(25_000, "Red"),   # round 25, overtime -> must be ignored
        _defused(26_000, "Red"),   # round 26, overtime -> must be ignored
    ]
    untrusted = stats._untrusted_side_rounds(list(range(1, 28)))
    sides = stats._round_sides(list(range(1, 28)), rounds, 12_500, spikes, untrusted)

    assert sides[1] == {"attacker": "Blue", "defender": "Red"}
    assert sides[12] == {"attacker": "Blue", "defender": "Red"}
    assert sides[13] == {"attacker": "Red", "defender": "Blue"}
    assert sides[24] == {"attacker": "Red", "defender": "Blue"}
    for n in (25, 26, 27):
        assert n not in sides


def test_overtime_alone_does_not_invent_sides():
    """With defusal evidence only in overtime, there are still no sides at all."""
    rounds = _rounds(26)
    spikes = [_defused(25_500, "Red")]
    untrusted = stats._untrusted_side_rounds(list(range(1, 27)))
    assert stats._round_sides(list(range(1, 27)), rounds, 12_500, spikes, untrusted) == {}


@pytest.mark.slow
def test_regulation_replays_never_mark_overtime(m1_stats):
    rounds = m1_stats["rounds"]
    assert rounds
    for r in rounds:
        assert not r.get("overtime"), f"round {r['round']} falsely marked overtime"
        assert r.get("sides"), f"round {r['round']} lost its sides"
