"""KAST must credit assists, and the per-round table must expose them.

The assist numbers come from ``Bomb_CombatReportComponent`` cells
(``per_actor_round[actor][round]["assists"]``), which the aggregate loop already
sums into ``stats.assists``. KAST previously ignored them.
"""
import pytest

import stats


def _cell(**kw):
    base = {
        "damage": 0.0,
        "hits": 0.0,
        "kills": 0,
        "deaths": 0,
        "assists": 0,
        "head_damage": 0.0,
        "body_damage": 0.0,
        "other_damage": 0.0,
        "headshot_kills": 0,
        "wallbang_damage": 0,
    }
    base.update(kw)
    return base


def test_round_assists_counts_only_the_subjects_actors_in_that_round():
    per = {101: {3: _cell(assists=1)}, 202: {3: _cell(assists=2), 4: _cell(assists=5)}}
    mapping = {101: "s1", 202: "s2", 303: "s2"}

    assert stats._round_assists(per, mapping, "s1", 3) == 1
    assert stats._round_assists(per, mapping, "s2", 3) == 2
    assert stats._round_assists(per, mapping, "s2", 4) == 5
    assert stats._round_assists(per, mapping, "s2", 99) == 0
    assert stats._round_assists(per, mapping, "s3", 3) == 0


@pytest.mark.slow
def test_kast_agrees_with_per_round_credit(m1_stats):
    """KAST is re-derived from the per-round table the UI shows.

    Credit requires a kill, an assist, survival, or being traded.
    """
    n_rounds = m1_stats["overview"]["n_rounds"]
    for p in m1_stats["players"]:
        credit = sum(
            1
            for row in p["per_round"]
            if row["kills"] > 0
            or row["assists"] > 0
            or row["deaths"] == 0
            or row["traded"]
        )
        assert p["stats"]["kast_pct"] == pytest.approx(
            round(100.0 * credit / n_rounds, 1)
        ), f"{p['subject']} KAST disagrees with its own per-round table"


@pytest.mark.slow
def test_per_round_assists_sum_to_total_assists(m1_stats):
    for p in m1_stats["players"]:
        assert sum(row["assists"] for row in p["per_round"]) == p["stats"]["assists"]
