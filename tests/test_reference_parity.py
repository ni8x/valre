"""Reference parity: our numbers against the published OP.GG report for m1.

The report at ``docs/... repla.md`` (the OP.GG replay report) publishes ADR and K/D per
player, plus the game's own combat score (ACS) and KAST. These tests pin the published
values so the engine cannot drift away from them silently, and they document the two
places where we knowingly differ (a spike-detonation death the export records as a
self-kill, and one round of KAST).

Nothing here fabricates data: the payload comes from the real m1 export.
"""
from __future__ import annotations

import pytest

REFERENCE = "m1"

# Published ADR for m1, keyed by the first eight characters of the subject id.
PUBLISHED_ADR = {
    "0ce13fa3": 189,  # Cypher
    "b4cbe093": 183,  # Phoenix
    "b1859620": 176,  # Raze
    "cc7b67b1": 173,  # Sova
    "f24904f7": 167,  # Miks
    "11ff18e1": 160,  # Jett
    "f19ff93f": 127,  # Fade
    "13171c1c": 115,  # Clove
    "be3c362f": 115,  # Chamber
    "0dbfdcd7": 115,  # Reyna
}

# Published kills and deaths for m1, same keying.
PUBLISHED_KD = {
    "0ce13fa3": (24, 16),
    "b4cbe093": (22, 21),
    "b1859620": (27, 16),
    "cc7b67b1": (21, 13),
    "f24904f7": (18, 20),
    "11ff18e1": (21, 17),
    "f19ff93f": (12, 18),
    "13171c1c": (13, 23),
    "be3c362f": (13, 21),
    "0dbfdcd7": (14, 20),
}


def by_prefix(payload):
    return {p["subject"][:8]: p for p in payload["players"]}


def test_adr_matches_the_published_report(m1_stats):
    """The published ADR is damage dealt to opponents per round, rounded."""
    players = by_prefix(m1_stats)
    assert set(players) == set(PUBLISHED_ADR)
    for prefix, published in PUBLISHED_ADR.items():
        p = players[prefix]
        assert p["stats"]["adr"] is not None, prefix
        assert round(p["stats"]["adr"]) == published, (prefix, p["stats"]["adr"], published)


def test_kills_match_and_the_one_extra_death_is_a_self_inflicted_one(m1_stats):
    """Kills match the report for all ten players; Cypher carries one extra death.

    That death is the round-24 spike detonation, which the export records as a death
    with the victim's own actor as the killer (`suicide`), so we count it and the report
    does not. No other player in m1 has a self-inflicted death.
    """
    players = by_prefix(m1_stats)
    for prefix, (kills, deaths) in PUBLISHED_KD.items():
        p = players[prefix]
        assert p["stats"]["kills"] == kills, (prefix, p["stats"]["kills"], kills)
        if prefix == "0ce13fa3":
            assert p["stats"]["deaths"] == deaths + 1
            assert p["stats"]["self_deaths"] == 1
        else:
            assert p["stats"]["deaths"] == deaths, (prefix, p["stats"]["deaths"], deaths)
            assert p["stats"]["self_deaths"] == 0, prefix
    assert sum(p["stats"]["self_deaths"] for p in m1_stats["players"]) == 1


def test_damage_bookkeeping_adds_up(m1_stats):
    """Dealt damage splits into opponents, self and unattributed with nothing lost."""
    total_opp = 0.0
    total_taken = 0.0
    for p in m1_stats["players"]:
        s = p["stats"]
        opp = sum(s["damage_to_opponents"].values())
        # Each field is rounded to one decimal, so the split can differ by a rounding step.
        assert abs(s["damage_dealt"] - (opp + s["self_damage"] + s["damage_unattributed"])) < 0.2, (
            p["subject"], s["damage_dealt"], opp, s["self_damage"], s["damage_unattributed"])
        total_opp += opp
        total_taken += s["damage_taken"]
    # Summed over players, both sides round the same 1-dp values, so allow a small drift.
    assert abs(total_opp - total_taken) < 1.0, (total_opp, total_taken)
    validation = m1_stats["overview"]["combat_report_validation"]
    assert abs(total_opp + validation["damage_self_total"]
               + validation["damage_unattributed_total"] - validation["damage_region_total"]) < 0.2


def test_acs_is_the_game_score_per_round(m1_stats):
    """ACS is Riot's own scoreboard score divided by rounds - not our index."""
    n_rounds = m1_stats["overview"]["n_rounds"]
    players = by_prefix(m1_stats)
    for p in m1_stats["players"]:
        score = p["stats"]["score"]
        if score is None:
            assert p["stats"]["acs"] is None
            continue
        assert p["stats"]["acs"] == round(score / n_rounds, 1), p["subject"]
    assert players["b1859620"]["stats"]["acs"] == 277.5  # the report prints 277


def test_raze_kast_and_the_known_one_round_gap(m1_stats):
    """Our KAST is the standard kill/assist/survive/traded share of rounds.

    The report prints 83 % for Raze against our 87.5 % (21 of 24). The only structural
    candidate is round 19, which he survived with no kill, assist or trade, so the two
    are not the same definition; we keep the standard one and say so.
    """
    raze = by_prefix(m1_stats)["b1859620"]
    assert raze["stats"]["kast_pct"] == 87.5
    credited = [r for r in raze["per_round"] if r.get("kast")]
    assert len(credited) == 21
    assert m1_stats["overview"]["n_rounds"] == 24
    assert round(100.0 * 20 / 24, 1) == 83.3  # the report's 83 %, one round fewer


@pytest.mark.slow
def test_official_combat_aggregates_agree_on_kills_and_assists(store, m1_stats):
    """Cross-check against the game's own BasicCombatStatsComponent.

    The component's counters are replicated snapshots, and they can lag the last round -
    Cypher's AggregateDeaths reads 16 there while our 17 includes the round-24
    detonation - so the assertion covers kills and assists, and the death counter is
    only reported.
    """
    import analysis
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    import stats as stats_mod

    export = store.export_dir(REFERENCE) / "fields.parquet"
    if not export.exists():
        pytest.skip("m1 export not available")
    players_by_subject, _char2subj, ps2subj, _subj2char = stats_mod._identity(
        analysis.load_manifest(store.manifest_path(REFERENCE)))
    table = pq.read_table(export, columns=["actor_net_guid", "group_path",
                                           "field_name", "value_i64"])
    group = pc.cast(table["group_path"], pa.string())
    rows = table.filter(pc.match_substring(group, "BasicCombatStatsComponent")).to_pylist()
    official: dict = {}
    for r in rows:
        field = r.get("field_name") or ""
        if field not in ("AggregateKills", "AggregateDeaths", "AggregateAssists"):
            continue
        subject = ps2subj.get(int(r.get("actor_net_guid") or 0))
        value = r.get("value_i64")
        if not subject or value is None:
            continue
        bucket = official.setdefault(subject, {})
        bucket[field] = max(bucket.get(field, 0), value)
    assert official, "no BasicCombatStatsComponent rows found"
    for p in m1_stats["players"]:
        bucket = official.get(p["subject"], {})
        assert "AggregateKills" in bucket, p["subject"]
        assert bucket["AggregateKills"] == p["stats"]["kills"], p["subject"]
        if "AggregateAssists" in bucket:
            assert bucket["AggregateAssists"] == p["stats"]["assists"], p["subject"]
    cypher = [p["subject"] for p in players_by_subject if p["subject"].startswith("0ce13fa3")]
    assert cypher and official[cypher[0]].get("AggregateDeaths") == 16
