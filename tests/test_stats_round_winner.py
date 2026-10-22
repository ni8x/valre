"""Task 3 — round winners must follow spike detonation / defusal."""
import pytest

import stats


def _players(*spec):
    return {name: {"team": team} for name, team in spec}


def test_defusal_wins_for_the_defending_team_despite_a_later_wipe():
    """m1 R1: Red died 5 times but defused first; the wipe rule must not win."""
    players = _players(("b1", "Blue"), ("b2", "Blue"), ("r1", "Red"), ("r2", "Red"))
    deaths = [{"victim": "r1"}, {"victim": "r2"}]
    spikes = [
        {"t": 100, "kind": "planted"},
        {"t": 200, "kind": "defused", "defuser_team": "Red"},
    ]
    winner, source = stats._round_winner(
        {"start_ms": 0, "end_ms": 300}, deaths, players, spikes,
        {"attacker": "Blue", "defender": "Red"},
    )
    assert winner == "Red"
    assert source == "spike"


def test_explosion_wins_for_the_attacking_team_without_any_wipe():
    """m1 R17 shape: no team is wiped, so ONLY the exploded branch can return Red."""
    players = _players(("b1", "Blue"), ("b2", "Blue"), ("r1", "Red"), ("r2", "Red"))
    deaths = [{"victim": "b1"}]
    spikes = [{"t": 100, "kind": "planted"}, {"t": 900, "kind": "exploded"}]
    winner, source = stats._round_winner(
        {"start_ms": 0, "end_ms": 1000}, deaths, players, spikes,
        {"attacker": "Red", "defender": "Blue"},
    )
    assert winner == "Red"
    assert source == "spike"


def test_mutual_wipe_does_not_fabricate_a_winner():
    """Both teams wiped with no spike and no side evidence: the round is unknown."""
    players = _players(("b1", "Blue"), ("b2", "Blue"), ("r1", "Red"), ("r2", "Red"))
    deaths = [{"victim": "b1"}, {"victim": "b2"}, {"victim": "r1"}, {"victim": "r2"}]
    spikes = [{"t": 100, "kind": "planted"}]
    assert stats._round_winner(
        {"start_ms": 0, "end_ms": 1000}, deaths, players, spikes, None
    ) == (None, None)


def test_explosion_does_not_guess_without_known_sides():
    players = _players(("b1", "Blue"), ("b2", "Blue"), ("r1", "Red"), ("r2", "Red"))
    deaths = [{"victim": "b1"}]
    spikes = [{"t": 100, "kind": "planted"}, {"t": 900, "kind": "exploded"}]
    assert stats._round_winner({"start_ms": 0, "end_ms": 1000}, deaths, players, spikes, None) == (None, None)


def test_duplicate_death_rows_do_not_change_the_wipe_verdict():
    """A revived player can die twice in a round; alive counts must stay sane."""
    players = _players(("b1", "Blue"), ("b2", "Blue"), ("r1", "Red"), ("r2", "Red"))
    deaths = [{"victim": "r1"}, {"victim": "r2"}, {"victim": "r2"}]
    winner, source = stats._round_winner({"start_ms": 0, "end_ms": 1000}, deaths, players, [], None)
    assert winner == "Blue"
    assert source == "elimination"


def test_round_sides_come_from_defusal_evidence_and_swap_after_the_half():
    rounds = {n: {"round": n, "start_ms": n * 1000, "end_ms": n * 1000 + 900, "switch_teams_ms": 3000}
              for n in (1, 2, 3, 4)}
    players = _players(("b1", "Blue"), ("r1", "Red"))
    spikes = [{"t": 2100, "kind": "defused", "defuser_team": "Red"}]
    sides = stats._round_sides([1, 2, 3, 4], rounds, 3000, spikes)
    assert sides[1] == {"attacker": "Blue", "defender": "Red"}
    assert sides[2] == {"attacker": "Blue", "defender": "Red"}
    assert sides[3] == {"attacker": "Red", "defender": "Blue"}
    assert sides[4] == {"attacker": "Red", "defender": "Blue"}


@pytest.mark.slow
def test_m1_every_round_winner_matches_independent_evidence(m1_stats):
    """Winners must agree with spike outcomes and wipe evidence, for every round."""
    from collections import Counter
    checked = Counter()
    for r in m1_stats["rounds"]:
        kinds = [s["kind"] for s in (r.get("spikes") or [])]
        sides = r.get("sides")
        winner = r["winner"]
        assert winner in ("Blue", "Red"), f"round {r['round']} has no winner"
        if "exploded" in kinds and sides:
            assert winner == sides["attacker"], f"round {r['round']} exploded"
            checked["spike"] += 1
        elif "defused" in kinds and sides:
            assert winner == sides["defender"], f"round {r['round']} defused"
            checked["spike"] += 1
        elif not kinds:
            dead = {d["victim"] for d in (r.get("deaths") or []) if d.get("victim")}
            alive = Counter()
            for s, p in ((p["subject"], p) for p in m1_stats["players"]):
                if s not in dead:
                    alive[p["team"]] += 1
            wiped = [t for t in ("Blue", "Red") if alive[t] == 0]
            if len(wiped) == 1:
                assert winner == ("Red" if wiped[0] == "Blue" else "Blue")
                checked["wipe"] += 1
    assert checked["spike"] >= 5, f"expected spike-decided rounds, saw {checked}"
    assert checked["wipe"] >= 5, f"expected wipe-decided rounds, saw {checked}"


def test_defuser_matched_within_the_same_round_not_by_time_proximity():
    """A defusal attempt can start seconds before the defused event (m1 R12: 8.6 s)."""
    rounds = {1: {"start_ms": 0, "end_ms": 100_000}}
    defusers = [(20_000, 111), (90_000, 222)]
    assert stats._defuser_at(defusers, rounds, 95_000) == 222


def test_defuser_ignores_attempts_from_another_round():
    rounds = {1: {"start_ms": 0, "end_ms": 100_000},
              2: {"start_ms": 100_000, "end_ms": 200_000}}
    defusers = [(90_000, 111)]
    assert stats._defuser_at(defusers, rounds, 150_000) is None


def test_defuser_ignores_attempts_starting_after_the_completion():
    rounds = {1: {"start_ms": 0, "end_ms": 100_000}}
    defusers = [(90_000, 222), (98_000, 111)]
    assert stats._defuser_at(defusers, rounds, 95_000) == 222
