"""The duo check: pure pair rules on synthetic shapes, plus a real read of the shipped caches.

Nothing here fabricates replay data. The pure tests feed the exact shapes the engine consumes
(death rows, movement tracks, per-round rows) and assert the documented rules; the real tests read the
caches the engines already wrote, never build or rewrite one, and skip themselves when the probed
subjects are not on this machine.
"""

import numpy as np
import pytest

import duo

ME = "e703e25c-bdf8-5484-9449-a78d85996d46"
MATE = "0ce13fa3-fd35-5d8c-8f83-a4d8cd262041"
THIRD = "f24904f7-b04e-500f-82b0-2e3a096d8403"


def _death(**kw):
    row = {"t": 1000, "victim": "victim", "killer": "killer", "first_blood": False, "traded": False}
    row.update(kw)
    return row


def _track(rows):
    """rows: list of (t, x, y, z, yaw) -> the dict the engine reads."""
    arr = np.array(rows, dtype=float)
    return {"t": arr[:, 0], "x": arr[:, 1], "y": arr[:, 2], "z": arr[:, 3], "yaw": arr[:, 4]}


def _row(**kw):
    base = {
        "partner_killed_my_killer": False,
        "traded": False,
        "mate_alive": True,
        "distance_cm": 3000,
        "partner_arrived_after_ms": None,
    }
    base.update(kw)
    return base


# ---- the bands -------------------------------------------------------------


def test_band_boundaries():
    assert duo.band(0) == "<=12m"
    assert duo.band(1200) == "<=12m"
    assert duo.band(1200.4) == "12-25m"
    assert duo.band(2500) == "12-25m"
    assert duo.band(2501) == ">25m"
    assert duo.band(None) == "unknown"


# ---- alive at the death ----------------------------------------------------


def test_died_before():
    deaths = [_death(victim="mate", t=900)]
    assert duo.died_before(deaths, "mate", 1000) is True
    assert duo.died_before(deaths, "mate", 900) is True
    assert duo.died_before(deaths, "mate", 899) is False
    assert duo.died_before(deaths, "someone", 2000) is False
    assert duo.died_before([_death(victim="mate", t=None)], "mate", 10) is True


# ---- trade attribution -----------------------------------------------------


def test_traded_by_is_exact():
    death = _death(t=1000, victim="me", killer="them")
    traded = _death(t=4000, victim="them", killer="mate")
    assert duo.traded_by([death, traded], death, "mate") is not None


def test_trade_window_boundary_is_inclusive():
    death = _death(t=1000, victim="me", killer="them")
    at_5s = _death(t=6000, victim="them", killer="mate")
    at_5001 = _death(t=6001, victim="them", killer="mate")
    assert duo.traded_by([death, at_5s], death, "mate") is not None
    assert duo.traded_by([death, at_5001], death, "mate") is None


def test_trade_by_a_third_teammate_is_not_his():
    death = _death(t=1000, victim="me", killer="them")
    by_third = _death(t=3000, victim="them", killer="third")
    assert duo.traded_by([death, by_third], death, "mate") is None
    assert duo.traded_by([death, by_third], death, "third") is not None


def test_trade_before_the_death_does_not_count():
    death = _death(t=1000, victim="me", killer="them")
    early = _death(t=500, victim="them", killer="other")
    assert duo.traded_by([death, early], death, "other") is None


def test_trade_needs_the_killer_of_my_killer():
    death = _death(t=1000, victim="me", killer="them")
    wrong_victim = _death(t=2000, victim="someone_else", killer="mate")
    assert duo.traded_by([death, wrong_victim], death, "mate") is None


def test_trade_without_a_killer_is_none():
    death = _death(t=1000, victim="me", killer=None, suicide=True)
    assert duo.traded_by([death], death, "mate") is None


# ---- facing ----------------------------------------------------------------


def test_facing_split_uses_the_world_direction():
    me = (0.0, 0.0, 0.0)
    # yaw 0 -> world direction (1, 0): a target on +x is straight ahead
    assert duo.facing_split((100.0, 0.0, 0.0), me, 0.0) == (1.0, 0.0)
    # yaw 90 -> world direction (0, 1): the same +y target is 2 m ahead
    assert duo.facing_split((0.0, 200.0, 0.0), me, 90.0) == (2.0, 0.0)
    # yaw 90 and a target on +x is 1 m to my left
    assert duo.facing_split((100.0, 0.0, 0.0), me, 90.0) == (0.0, -1.0)
    assert duo.facing_split(None, me, 0.0) == (None, None)
    assert duo.facing_split((1.0, 1.0, 0.0), None, 0.0) == (None, None)
    assert duo.facing_split((1.0, 1.0, 0.0), me, None) == (None, None)


# ---- sampling --------------------------------------------------------------


def test_sample_picks_the_nearest_in_time():
    track = _track([(0, 0, 0, 0, 0), (100, 10, 0, 0, 0), (200, 20, 0, 0, 0)])
    pos, dt, yaw = duo._sample(track, 140)
    assert pos == (10.0, 0.0, 0.0)
    assert dt == -40.0
    assert yaw == 0.0


def test_sample_of_an_empty_track_is_none():
    assert duo._sample(None, 5) == (None, None, None)
    assert duo._sample({"t": np.array([]), "x": np.array([]), "y": np.array([]),
                        "z": np.array([]), "yaw": np.array([])}, 5) == (None, None, None)


def test_subject_sample_prefers_the_pawn_nearest_in_time():
    near = _track([(1000, 100, 0, 0, 0)])
    far = _track([(900, 900, 0, 0, 0)])
    pos, dt, _ = duo.subject_sample({1: near, 2: far}, ["1", "2"], 990)
    assert pos == (100.0, 0.0, 0.0)  # the swap pawn was 10 ms away, the parked one 90 ms
    assert dt == 10.0


def test_subject_sample_without_pawns_is_none():
    assert duo.subject_sample({}, ["1"], 10) == (None, None, None)


# ---- did he ever come up ---------------------------------------------------


def test_closest_after_measures_the_window():
    target = (0.0, 0.0, 0.0)
    track = _track([(0, 5000, 0, 0, 0), (1000, 3000, 0, 0, 0), (2000, 1000, 0, 0, 0),
                    (3000, 500, 0, 0, 0), (4000, 9000, 0, 0, 0)])
    closest_cm, at_ms, arrived = duo.closest_after({1: track}, ["1"], target, 0, 5000)
    assert closest_cm == 500
    assert at_ms == 3000
    assert arrived == 2000  # first sample inside 1200 cm, 2000 ms after the death


def test_closest_after_with_no_sample_is_all_none():
    assert duo.closest_after({}, ["1"], (0.0, 0.0, 0.0), 0, 1000) == (None, None, None)


def test_closest_after_without_a_target_is_all_none():
    track = _track([(500, 10, 0, 0, 0)])
    assert duo.closest_after({1: track}, ["1"], None, 0, 1000) == (None, None, None)


# ---- the trade counts ------------------------------------------------------


def test_counts_split_alive_dead_range_and_never_came():
    rows = [
        _row(traded=True, partner_killed_my_killer=True),
        _row(mate_alive=False, distance_cm=500),
        _row(distance_cm=500, partner_arrived_after_ms=1200),
        _row(distance_cm=3000),
        _row(distance_cm=None),
    ]
    counts = duo._counts(rows)
    assert counts["total"] == 5
    assert counts["traded_by_mate"] == 1
    assert counts["traded_by_anyone"] == 1
    assert counts["untraded_mate_alive"] == 3
    assert counts["untraded_mate_alive_in_range"] == 1
    assert counts["untraded_mate_alive_far"] == 1
    assert counts["untraded_mate_alive_unknown"] == 1
    assert counts["untraded_mate_dead"] == 1
    assert counts["untraded_mate_never_came"] == 2


# ---- contacts --------------------------------------------------------------


def test_contacts_attribute_the_first_death():
    contacts = duo._blank_contacts()
    first = _death(t=100, victim="mate", killer="enemy", traded=True)
    deaths = [first, _death(t=200, victim="enemy", killer="me")]
    duo._bump_contacts(contacts, first, deaths, "me", "mate")
    assert contacts["rounds"] == 1
    assert contacts["mine_first_blood"] == 0
    assert contacts["his_first_blood"] == 0
    assert contacts["his_first_death"] == 1
    assert contacts["first_traded_by_me"] == 1
    assert contacts["first_traded_by_him"] == 0


def test_contacts_count_the_other_trade_direction():
    contacts = duo._blank_contacts()
    first = _death(t=100, victim="me", killer="enemy", traded=True)
    deaths = [first, _death(t=200, victim="enemy", killer="mate")]
    duo._bump_contacts(contacts, first, deaths, "me", "mate")
    assert contacts["mine_first_death"] == 1
    assert contacts["first_traded_by_him"] == 1
    assert contacts["first_traded_by_me"] == 0


def test_contacts_count_his_first_death():
    contacts = duo._blank_contacts()
    first = _death(t=100, victim="mate", killer="enemy")
    duo._bump_contacts(contacts, first, [first], "me", "mate")
    assert contacts["his_first_death"] == 1
    assert contacts["mine_first_death"] == 0
    assert contacts["first_traded_by_me"] == 0
    assert contacts["first_traded_by_him"] == 0


# ---- the combined block ----------------------------------------------------


def test_combined_pair_adds_both_sides():
    my_rows = [{"kills": 2, "deaths": 1, "assists": 1, "damage_opponents": 300.0, "kast": True},
               {"kills": 0, "deaths": 1, "assists": 0, "damage_opponents": 100.0, "kast": False}]
    his_rows = [{"kills": 1, "deaths": 2, "assists": 2, "damage_opponents": 200.0, "kast": True},
                {"kills": 3, "deaths": 0, "assists": 0, "damage_opponents": 400.0, "kast": True}]
    got = duo.combined_pair(my_rows, his_rows)
    assert got["rounds"] == 2
    assert got["kills"] == 6
    assert got["deaths"] == 4
    assert got["assists"] == 3
    assert got["damage_opponents"] == 1000.0
    assert got["adr"] == 500.0
    assert got["kd"] == 1.5
    assert got["kast_pct"] == 75.0
    assert got["kpr"] == 3.0
    assert got["me"]["adr"] == 200.0
    assert got["partner"]["kd"] == 2.0


def test_combined_pair_empty_is_none_not_zero():
    got = duo.combined_pair([], [])
    assert got["rounds"] is None
    assert got["adr"] is None
    assert got["kd"] is None
    assert got["kast_pct"] is None
    assert got["kpr"] is None


# ---- the real caches -------------------------------------------------------


@pytest.mark.slow
def test_real_teammates_rank_the_probed_pair_first(store):
    try:
        got = players_teammates(store, ME)
    except KeyError:
        pytest.skip("the probed subject is not in this machine's data")
    rows = got["teammates"]
    assert rows, "a subject in 8 matches has teammates"
    top = rows[0]
    assert top["subject"] == MATE
    assert top["matches"] == 5
    assert top["rounds_together"] and top["rounds_together"] > 0
    assert top["wins"] is not None and top["losses"] is not None
    assert top["wins"] + top["losses"] + top["rounds_without_winner"] == top["rounds_together"]
    # deterministic order: matches desc, rounds desc, subject
    keys = [(-(r["matches"] or 0), -(r["rounds_together"] or 0), r["subject"]) for r in rows]
    assert keys == sorted(keys)


@pytest.mark.slow
def test_real_pair_matches_the_teammate_list_and_keeps_its_invariants(store):
    got = duo.pair(store, ME, MATE)
    if got["pair"]["matches"] == 0:
        pytest.skip("the probed pair is not in this machine's data")
    assert got["pair"]["matches"] == 5
    tm = players_teammates(store, ME)["teammates"][0]
    assert got["pair"]["rounds_together"] == tm["rounds_together"]
    assert got["pair"]["wins"] == tm["wins"]
    assert got["pair"]["win_pct"] == tm["win_pct"]

    # the bands cover every one of my deaths exactly once
    assert sum(got["bands"].values()) == len(got["deaths"])
    for row in got["deaths"]:
        assert row["distance_band"] in duo.BANDS
        if row["distance_cm"] is not None:
            assert abs(row["distance_cm"] - row["distance_m"] * 100) <= 50
        # a living teammate inside 12 m contradicts the published isolated flag
        if row["mate_alive"] and row["distance_cm"] is not None and row["distance_cm"] <= duo.ISO_DISTANCE_CM:
            assert row["isolated"] is not True
        # a trade we attribute must be a trade the payload itself calls one
        if row["partner_killed_my_killer"]:
            assert row["traded"] is True
        assert row["partner_move"] in (None, "up", "static", "back")
        assert row["t_into_round_s"] is None or row["t_into_round_s"] >= 0


@pytest.mark.slow
def test_real_trio_never_shares_one_match(store):
    counts = {}
    for other in (MATE, THIRD):
        got = duo.pair(store, ME, other)
        counts[other] = got["pair"]["matches"]
        if got["pair"]["matches"] == 0:
            pytest.skip("the probed subjects are not in this machine's data")
    assert counts == {MATE: 5, THIRD: 2}
    both = set()
    for other in (MATE, THIRD):
        both |= {m["match_id"] for m in duo.pair(store, ME, other)["matches"] if m["rounds_together"]}
    trio = duo.pair(store, MATE, THIRD)
    assert not (both & {m["match_id"] for m in trio["matches"] if m["rounds_together"]}), \
        "no uploaded match holds all three of the repeating subjects"


@pytest.mark.slow
def test_real_unknown_pair_says_why_instead_of_failing(store):
    got = duo.pair(store, "no-such-subject-at-all", MATE)
    assert got["pair"]["matches"] == 0
    assert got["pair"]["why"]
    assert got["deaths"] == []
    assert got["his_deaths"] == []


def players_teammates(store, subject):
    import players

    return players.teammates(store, subject)