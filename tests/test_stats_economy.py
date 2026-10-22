"""Unit tests for the economy aggregates (buy class, buy split, player economy)."""

import stats


def _buy_rounds(*buys):
    """Rounds where Blue's team buy class is given, in round order."""
    return {
        i + 1: {
            "round": i + 1,
            "economy": {
                "Blue": {"alv": 0.0, "loadout_value": 0, "buy": b},
                "Red": {"alv": 1000.0, "loadout_value": 5000, "buy": "eco"},
            },
        }
        for i, b in enumerate(buys)
    }


def _row(round_no, **kw):
    row = {"round": round_no, "kills": 0, "deaths": 0, "assists": 0, "damage": 0.0,
           "damage_opponents": 0.0, "money": None, "kast": False, "hs_kills": 0,
           "first_blood": False, "first_death": False}
    row.update(kw)
    return row


def test_classify_buy_buckets_are_ordered_and_none_is_unknown():
    assert stats.classify_buy(None) == "unknown"
    assert stats.classify_buy(0) == "eco"
    assert stats.classify_buy(stats.ECO_MAX_ALV - 1) == "eco"
    assert stats.classify_buy(stats.ECO_MAX_ALV) == "force"
    assert stats.classify_buy(stats.FORCE_MAX_ALV - 1) == "force"
    assert stats.classify_buy(stats.FORCE_MAX_ALV) == "full"


def test_buy_split_credits_rows_to_their_team_buy_class():
    rounds = _buy_rounds("pistol", "full", "eco")
    rows = [_row(1, kills=1, damage_opponents=100.0, kast=True),
            _row(2, kills=2, deaths=1, damage_opponents=400.0),
            _row(3)]
    out = stats._buy_split(rows, "Blue", rounds)
    assert set(out) == set(stats.BUY_KEYS)
    assert out["pistol"]["rounds"] == 1 and out["pistol"]["kills"] == 1
    assert out["pistol"]["kast_pct"] == 100.0
    assert out["full"]["rounds"] == 1 and out["full"]["damage"] == 400.0
    assert out["full"]["kd"] == 2.0
    assert out["eco"]["rounds"] == 1 and out["eco"]["adr"] == 0.0
    assert out["unknown"]["rounds"] == 0
    assert out["unknown"]["adr"] is None


def test_buy_split_counts_rounds_without_economy_as_unknown():
    rounds = {1: {"round": 1}}
    out = stats._buy_split([_row(1, kills=3)], "Blue", rounds)
    assert out["unknown"]["rounds"] == 1 and out["unknown"]["kills"] == 3
    empty = stats._buy_split([_row(1, kills=3)], None, rounds)
    assert all(empty[k]["rounds"] == 0 for k in stats.BUY_KEYS)


def test_player_economy_averages_money_and_counts_actual_buy_mix():
    rounds = _buy_rounds("pistol", "full", "full")
    rows = [_row(1, money=800), _row(2, money=2000), _row(3, money=None)]
    out = stats._player_economy(rows, "Blue", rounds)
    assert out["rounds"] == 3
    assert out["rounds_with_money"] == 2
    assert out["avg_start_money"] == 1400.0
    assert out["buy_mix"] == {"pistol": 1, "eco": 0, "force": 0, "full": 2, "unknown": 0}
    assert out["rounds_with_loadout_value"] == 3
    assert out["team_avg_loadout_value"] == 0.0


def test_player_economy_without_data_is_none_not_zero():
    out = stats._player_economy([], "Blue", {})
    assert out["rounds_with_money"] == 0
    assert out["avg_start_money"] is None
    assert out["team_avg_loadout_value"] is None
    assert out["buy_mix"]["unknown"] == 0
