"""Unit tests for the insight record generator.

``stats._insights`` is a pure function over the engine's documented shapes
(``players_out``, ``rounds``, ``order``, ``h2h``, ``overview``), so these tests
build those shapes directly. The end-to-end assertions on the reference replay
live in ``tests/test_engines_m1.py``.
"""
import stats


ALLOWED_KINDS = set(stats.INSIGHT_KINDS)


def _player(subject, *, kills=0, deaths=0, kd=0.0, adr=0.0, hs_pct=None,
            headshot_kills=0, entry_kills=0, isolated_pct=None, isolated_deaths=0,
            bait_pct=None, clutch_wins=0, clutch_attempts=0, per_round=None,
            rating=None, team="Blue"):
    """One ``players_out`` row, carrying only the fields ``_insights`` reads."""
    return {
        "subject": subject,
        "team": team,
        "stats": {
            "kills": kills, "deaths": deaths, "kd": kd, "adr": adr,
            "hs_pct": hs_pct, "headshot_kills": headshot_kills,
            "entry_kills": entry_kills, "isolated_pct": isolated_pct,
            "isolated_deaths": isolated_deaths, "bait_pct": bait_pct,
            "clutch_wins": clutch_wins, "clutch_attempts": clutch_attempts,
        },
        "per_round": per_round if per_round is not None else [],
        "rating_by_round": rating or {"n": 0, "mean": None, "stdev": None},
    }


def _check(rec):
    """Assert one record is well formed and never rates below the sample floor."""
    assert isinstance(rec, dict), rec
    assert set(rec) == {"text", "detail", "n", "kind"}, rec
    assert isinstance(rec["text"], str) and rec["text"].strip(), rec
    assert rec["detail"] is None or (isinstance(rec["detail"], str) and rec["detail"].strip())
    assert rec["n"] is None or (isinstance(rec["n"], int) and rec["n"] >= 1), rec
    assert rec["kind"] in ALLOWED_KINDS, rec
    is_rate = (rec["kind"] in stats.RATE_INSIGHT_KINDS
               or "%" in rec["text"] or "%" in (rec["detail"] or ""))
    if is_rate:
        assert rec["n"] is not None and rec["n"] >= stats.MIN_RATE_SAMPLE, rec


def _context_rounds():
    """Six decided rounds, three per half, so both sides of both teams exist."""
    def cell(buy, alv):
        return {"alv": alv, "loadout_value": int(alv * 5), "buy": buy}
    out = {}
    for n in (1, 2, 3):
        out[n] = {"round": n, "sides": {"attacker": "Blue", "defender": "Red"},
                  "winner": "Blue",
                  "economy": {"Blue": cell("full", 6000.0), "Red": cell("eco", 1000.0)}}
    for n in (4, 5, 6):
        out[n] = {"round": n, "sides": {"attacker": "Red", "defender": "Blue"},
                  "winner": "Red",
                  "economy": {"Blue": cell("full", 6000.0), "Red": cell("eco", 1000.0)}}
    return out


def _two_rated_players():
    return [
        _player("aaaaaaaa-1", kills=10, deaths=8, kd=1.25, adr=150.0, hs_pct=50.0,
                headshot_kills=5, entry_kills=4, isolated_pct=25.0, isolated_deaths=2,
                per_round=[{"deaths": 1} for _ in range(8)],
                rating={"n": 8, "mean": 5.0, "stdev": 2.0}),
        _player("bbbbbbbb-1", kills=6, deaths=10, kd=0.6, adr=90.0, hs_pct=20.0,
                headshot_kills=1, entry_kills=2, isolated_pct=60.0, isolated_deaths=6,
                per_round=[{"deaths": 1} for _ in range(10)],
                rating={"n": 10, "mean": 3.0, "stdev": 4.0}),
    ]


def test_records_are_dicts_with_the_four_keys_and_an_allowed_kind():
    rounds = _context_rounds()
    order = sorted(rounds)
    overview = {"economy": stats._economy_overview(rounds, order)}
    out = stats._insights(_two_rated_players(), rounds, order, {}, overview)
    assert out
    for rec in out:
        _check(rec)


def test_kills_record_counts_both_kills_and_deaths_as_its_sample():
    p = _player("cccccccc-1", kills=3, deaths=1, kd=3.0, adr=80.0)
    out = stats._insights([p], {}, [], {}, {})
    kills = [r for r in out if r["kind"] == "kills"]
    assert len(kills) == 1
    assert kills[0]["n"] == 4  # 3 kills + 1 death: the K/D sample
    assert "3 kills" in kills[0]["text"]


def test_rate_claims_are_suppressed_below_three_samples():
    # one kill, one death: a 100% headshot rate and a 100% isolated-death rate
    # both rest on a single event and must not be emitted.
    p = _player("dddddddd-1", kills=1, deaths=1, kd=1.0, adr=20.0, hs_pct=100.0,
                headshot_kills=1, isolated_pct=100.0, isolated_deaths=1, bait_pct=100.0,
                per_round=[{"deaths": 1}])
    out = stats._insights([p], {}, [], {}, {})
    kinds = {r["kind"] for r in out}
    assert "aim" not in kinds
    assert "survival" not in kinds
    assert "kills" in kinds and all(r["n"] >= 1 for r in out)


def test_empty_players_and_rounds_do_not_raise():
    assert stats._insights([], {}, [], {}, {}) == []


def test_side_economy_and_consistency_lines_appear_when_the_data_exists():
    rounds = _context_rounds()
    order = sorted(rounds)
    overview = {"economy": stats._economy_overview(rounds, order)}
    out = stats._insights(_two_rated_players(), rounds, order, {}, overview)
    kinds = {r["kind"] for r in out}
    assert {"side", "economy", "consistency"} <= kinds
    for rec in out:
        _check(rec)
    econ = next(r for r in out if r["kind"] == "economy")
    assert "full-buy" in econ["text"]
    side = next(r for r in out if r["kind"] == "side")
    assert "attack" in side["text"] and "defence" in side["text"]


def test_context_lines_are_absent_without_the_data():
    rounds = {1: {"round": 1}, 2: {"round": 2}}  # no sides, no economy, no winner
    order = [1, 2]
    overview = {"economy": stats._economy_overview(rounds, order)}
    p = _player("eeeeeeee-1", kills=2, deaths=2, kd=1.0, adr=40.0)
    out = stats._insights([p], rounds, order, {}, overview)
    kinds = {r["kind"] for r in out}
    assert "side" not in kinds
    assert "economy" not in kinds
    assert "consistency" not in kinds
    for rec in out:
        _check(rec)
