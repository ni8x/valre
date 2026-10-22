"""Regression tests over the real reference replay (m1, Sunset).

These pin what the engines already produce from real data so later work cannot
silently change the numbers. They compute the payloads fresh from the m1 export
(real results, produced by a real export) rather than trusting a shipped cache.
"""
import pytest

import stats


def test_metrics_reference_numbers(m1_metrics):
    assert m1_metrics["version"] == 5
    assert m1_metrics["n_rounds"] == 24

    bullets = sum(p["shots"] for p in m1_metrics["players"])
    gun_hits = sum(p["gun_hits"] for p in m1_metrics["players"])
    assert bullets == 3503
    assert gun_hits == 714


def test_metrics_kill_feed_is_fully_paired(m1_metrics):
    v = m1_metrics["validation"]
    assert v["kill_feed_pairs"] == 186
    assert v["kill_feed_unpaired"] == 0


def test_metrics_phase_b_fields_present_for_reference_player(m1_metrics):
    raze = _player(m1_metrics, "b1859620")
    assert raze["engagements"] > 0
    assert raze["pre_aim_deg"] == pytest.approx(6.6, abs=0.05)
    assert raze["head_level_pct"] == pytest.approx(0.137, abs=0.01)
    assert raze["counter_strafe_pct"] == pytest.approx(0.731, abs=0.01)
    assert raze["spray_radius_cm"] is not None
    assert raze["hs_kill_pct"] is not None


def test_metrics_weapon_rows_carry_rates_and_sample(m1_metrics):
    w = _player(m1_metrics, "b1859620")["weapons"]
    expected = {
        "Classic": (24, 4),
        "Compact Pistol": (17, 5),
        "Vandal": (104, 15),
        "Phantom": (104, 14),
        "Spectre": (9, 3),
        "Guardian": (14, 5),
    }
    assert set(w) == set(expected)
    for name, (shots, hits) in expected.items():
        row = w[name]
        assert row["shots"] == shots
        assert row["hits"] == hits
        assert row["acc"] == pytest.approx(hits / shots)
        assert 0.0 <= row["acc"] <= 1.0
        assert row["dmg_per_shot"] == pytest.approx(row["dmg"] / shots, abs=0.05)
        if row["hits"]:
            assert row["hs_rate"] is not None
            assert 0.0 <= row["hs_rate"] <= 1.0
        if row["kills"] == 0:
            assert row["shots_per_kill"] is None


def test_metrics_weapon_rows_reconcile_with_the_player_totals(m1_metrics):
    for p in m1_metrics["players"]:
        rows = p["weapons"]
        assert sum(r["shots"] for r in rows.values()) == p["shots"], p["subject"]
        assert sum(r["hits"] for r in rows.values()) == p["hits"], p["subject"]
        assert sum(r["head_hits"] for r in rows.values()) == p["head_hits"], p["subject"]
        assert abs(sum(r["dmg"] for r in rows.values()) - p["dmg"]) <= 1.0, p["subject"]
        for name, r in rows.items():
            # acc is hits per shot and may exceed 1.0 for shotguns (pellet hits).
            assert r["acc"] is None or r["acc"] >= 0.0, (p["subject"], name)
            assert r["hs_rate"] is None or 0.0 <= r["hs_rate"] <= 1.0, (p["subject"], name)


def test_metrics_validation_reports_engagement_geometry(m1_metrics):
    v = m1_metrics["validation"]
    assert v["engagements_total"] > 0
    assert v["eye_offset_cm"] == pytest.approx(77.85, abs=0.5)


def test_stats_reference_numbers(m1_stats):
    assert m1_stats["version"] == 19
    assert m1_stats["overview"]["n_rounds"] == 24
    assert len(m1_stats["players"]) == 10
    assert len(m1_stats["rounds"]) == 24


def test_stats_kills_and_deaths_balance(m1_stats):
    """Every death is either somebody's kill or a suicide (killer == victim)."""
    kills = sum(p["stats"]["kills"] for p in m1_stats["players"])
    deaths = sum(p["stats"]["deaths"] for p in m1_stats["players"])
    suicides = sum(
        1 for r in m1_stats["rounds"] for d in r.get("deaths", []) if d.get("suicide")
    )
    assert deaths == kills + suicides
    assert kills > 0 and suicides >= 0


def test_stats_rounds_have_known_winners(m1_stats):
    for r in m1_stats["rounds"]:
        assert r["winner"] in ("Blue", "Red", None), r["round"]


def test_stats_side_split_covers_every_round(m1_stats):
    """attack + defend must reconstruct the match totals, never double count."""
    expected = sum(1 for r in m1_stats["rounds"] if r.get("sides"))
    assert expected == m1_stats["overview"]["n_rounds"]
    by_side = m1_stats["overview"]["side_rounds"]
    for team in ("Blue", "Red"):
        assert by_side[team]["attack"] + by_side[team]["defend"] == expected
    for p in m1_stats["players"]:
        s = p["stats"]
        blocks = p["stats_by_side"]
        assert blocks["attack"]["rounds"] + blocks["defend"]["rounds"] == expected
        assert blocks["attack"]["kills"] + blocks["defend"]["kills"] == s["kills"]
        assert blocks["attack"]["deaths"] + blocks["defend"]["deaths"] == s["deaths"]
        dmg = blocks["attack"]["damage"] + blocks["defend"]["damage"]
        # the side blocks carry damage dealt to opponents (the ADR basis), not the raw
        # region total, so a player who only damaged himself must not match the total
        opp = sum(s["damage_to_opponents"].values())
        assert abs(dmg - opp) <= 1.0, (p["subject"], dmg, opp)


def test_stats_side_split_agrees_with_defused_rounds(m1_stats):
    """A defusal can only be performed by the defending side (game rule), so the
    defuser's team must be the side the derived split assigned to defence."""
    defused = 0
    for r in m1_stats["rounds"]:
        for sp in r.get("spikes") or []:
            if sp.get("kind") != "defused":
                continue
            defused += 1
            assert sp.get("defuser_team") in ("Blue", "Red"), r["round"]
            assert sp["defuser_team"] == r["sides"]["defender"], r["round"]
            assert r["winner"] == sp["defuser_team"], r["round"]
    assert defused > 0, "the reference replay must contain at least one defusal"


def test_stats_round_economy_covers_every_round(m1_stats):
    rounds = m1_stats["rounds"]
    assert rounds
    assert m1_stats["overview"]["economy_thresholds"] == {"eco_max_alv": 3000.0,
                                                          "force_max_alv": 5000.0}
    buys = ("pistol", "eco", "force", "full", "unknown")
    for r in rounds:
        eco = r["economy"]
        assert set(eco) == {"Blue", "Red"}, r["round"]
        for cell in eco.values():
            assert cell["buy"] in buys
            assert cell["alv"] is None or cell["alv"] > 0
            if cell["alv"] is not None:
                assert abs(cell["loadout_value"] - 5 * cell["alv"]) <= 1
    pistols = [r["round"] for r in rounds if r["economy"]["Blue"]["buy"] == "pistol"]
    assert len(pistols) == 2, pistols
    assert rounds[0]["round"] in pistols
    for p in m1_stats["players"]:
        assert sum(b["rounds"] for b in p["stats_by_buy"].values()) == len(rounds)
        assert set(p["stats_by_buy"]) == set(buys)
        eco = p["economy"]
        assert eco["rounds"] == len(rounds)
        assert eco["avg_start_money"] is not None
        assert sum(eco["buy_mix"].values()) == len(rounds)


def test_stats_opening_duels_and_consistency_reconcile(m1_stats):
    rounds = m1_stats["rounds"]
    n = len(rounds)
    with_first_death = sum(1 for r in rounds if (r.get("deaths") or []))
    wins = 0
    for p in m1_stats["players"]:
        e = p["entry"]
        assert e["duels"] >= e["first_deaths"] >= 0
        assert e["wins"] <= e["duels"]
        assert (e["win_pct"] is None) == (e["duels"] == 0)
        if e["win_pct"] is not None:
            assert 0.0 <= e["win_pct"] <= 100.0
            assert abs(e["win_pct"] - 100.0 * e["wins"] / e["duels"]) <= 0.1
        wins += e["wins"]
        r = p["rating_by_round"]
        assert r["n"] == n
        assert r["worst"] <= r["median"] <= r["best"]
        assert abs(r["mean"] - sum(
            2 * row["kills"] + (row["damage_opponents"] or 0.0) / 200.0
            for row in p["per_round"]) / n) <= 0.01
        assert 0 <= p["clean_rounds"] <= n
        assert p["clean_rounds"] == sum(
            1 for row in p["per_round"] if row["kills"] > 0 and row["deaths"] == 0)
    assert wins == with_first_death, (wins, with_first_death)


def test_stats_players_resolve_agents_and_teams(m1_stats):
    for p in m1_stats["players"]:
        assert p["team"] in ("Blue", "Red")
        assert p["agent_uuid"], f"missing agent for {p['subject']}"
        assert len(p["agent_uuid"]) == 36, "agent uuid must be a full uuid"


def test_stats_insights_are_well_formed_records(m1_stats):
    """Every insight is a record with its sample, and no rate claim is built on
    fewer than three events (a single death must not read 100%)."""
    allowed = set(stats.INSIGHT_KINDS)
    assert m1_stats["insights"], "the reference replay should yield insights"
    for rec in m1_stats["insights"]:
        assert set(rec) == {"text", "detail", "n", "kind"}, rec
        assert rec["kind"] in allowed, rec
        assert isinstance(rec["text"], str) and rec["text"].strip(), rec
        assert rec["detail"] is None or rec["detail"].strip(), rec
        assert rec["n"] is None or rec["n"] >= 1, rec
        is_rate = (rec["kind"] in stats.RATE_INSIGHT_KINDS
                   or "%" in rec["text"] or "%" in (rec["detail"] or ""))
        if is_rate:
            assert rec["n"] is not None and rec["n"] >= stats.MIN_RATE_SAMPLE, rec


def test_spatial_tracks_for_round_one(store, m1_export):
    import spatial

    tracks = spatial.tracks(store, "m1", 1)
    assert tracks["round"] == 1
    assert len(tracks["tracks"]) == 10
    assert tracks["end_ms"] > tracks["start_ms"]
    for tr in tracks["tracks"]:
        assert tr["points"], f"no movement points for {tr['subject']}"
        assert all(len(pt) == 5 for pt in tr["points"])


def test_spatial_heatmap_matches_grid(store, m1_export):
    import spatial

    heat = spatial.heatmap(store, "m1", round_no=1)
    assert heat["grid"] > 0
    assert heat["total"] > 0


def test_coach_profiles_every_player(store, m1_export):
    import coach

    prof = coach.build(store, "m1")
    assert prof["version"] == 1
    assert len(prof["players"]) == 10
    for p in prof["players"]:
        assert p["grade"] in ("A", "B", "C", "D", "E")
        assert p["overall"] is None or 0 <= p["overall"] <= 100
    assert set(prof["teams"]) == {"Blue", "Red"}


def _player(metrics_payload, prefix):
    for p in metrics_payload["players"]:
        if p["subject"].startswith(prefix):
            return p
    raise AssertionError(f"player {prefix} not found")
