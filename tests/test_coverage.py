"""Coverage: how much of a match the export actually holds.

Two layers. The pure layer builds the report from hand-made inputs, so every
branch of the check list can be exercised without a replay. The real layer reads
one replay through the same fixtures the other engine tests use and confirms the
report agrees with the engines it summarises. Nothing here writes a cache.
"""
import analysis
import coverage
import pytest


def _round(n, winner, sides=True, overtime=False, spikes=()):
    r = {"round": n, "winner": winner, "spikes": list(spikes)}
    if sides:
        r["sides"] = {"attacker": "Blue", "defender": "Red"}
    if overtime:
        r["overtime"] = True
    return r


def _payload(rounds, **validation):
    v = {
        "damage_region_total": 1000.0,
        "damage_self_total": 100.0,
        "damage_unattributed_total": 50.0,
        "combat_report_kills": 185,
    }
    v.update(validation)
    return {
        "rounds": rounds,
        "overview": {"total_kills_events": 186},
        "validation": v,
        "players": [{"stats": {"self_deaths": 1}}, {"stats": {"self_deaths": 0}}],
    }


MANIFEST = {
    "source_file": "C:/replays/some.vrf",
    "source_size_bytes": 123456,
    "is_live": False,
    "compressed": True,
    "encrypted": True,
    "elapsed_ms": 1234,
}
SUMMARY = {
    "friendly_name": "Ascent",
    "build": "11.02",
    "replay_version": 5,
    "changelist": 123,
    "duration_ms": 1500000,
    "platform": "Windows",
    "level_name": "/Game/Maps/Ascent",
    "timestamp_ticks": 99,
    "quality": {
        "content_blocks_lost": 0,
        "unknown_chunks": 0,
        "malformed_content_blocks": 0,
        "typed_pct": 100.0,
        "movement_rows": 10,
        "net_guid_rows": 20,
        "event_rows": 30,
    },
}
COUNTS = {name: 0 for name in coverage.tables_mod.ALL_TABLES}


def _levels(rep):
    return [c["level"] for c in rep["checks"]]


def _texts(rep):
    return [c["text"] for c in rep["checks"]]


def test_clean_export_reports_no_warnings():
    rounds = [_round(n, "Blue" if n % 2 else "Red") for n in range(1, 25)]
    rep = coverage.summarize(
        MANIFEST,
        SUMMARY,
        COUNTS,
        _payload(rounds, damage_self_total=0.0, damage_unattributed_total=0.0),
        None,
    )
    assert "warn" not in _levels(rep)
    assert "All 24 rounds have a winner." in _texts(rep)
    assert "Every round has its sides." in _texts(rep)
    assert "Every point of damage is attributed to a shooter." in _texts(rep)
    assert rep["match"]["kill_events"] == 185
    assert rep["match"]["death_events"] == 186
    assert rep["match"]["self_deaths"] == 1


def test_gaps_in_rounds_are_named():
    rounds = [_round(n, "Blue") for n in range(1, 24)]
    rounds.append(_round(24, None, sides=False))
    rep = coverage.summarize(
        MANIFEST, SUMMARY, COUNTS, _payload(rounds, damage_self_total=0.0), None
    )
    assert "warn" in _levels(rep)
    assert any("1 of 24 rounds have no winner" in t for t in _texts(rep))
    assert any("carry no sides (no defusal evidence" in t for t in _texts(rep))
    assert rep["match"]["rounds_without_winner"] == 1
    assert rep["match"]["rounds_without_sides"] == 1


def test_overtime_rounds_are_explained_not_warned():
    rounds = [_round(n, "Blue" if n % 2 else "Red") for n in range(1, 25)]
    rounds += [
        _round(25, "Blue", sides=False, overtime=True),
        _round(26, "Red", sides=False, overtime=True),
    ]
    rep = coverage.summarize(MANIFEST, SUMMARY, COUNTS, _payload(rounds), None)
    assert any("2 overtime rounds carry no sides" in t for t in _texts(rep))
    assert not any("no defusal evidence" in t for t in _texts(rep))
    assert rep["match"]["overtime_rounds"] == 2


def test_decode_and_damage_holes_are_reported():
    summary = dict(SUMMARY)
    summary["quality"] = dict(SUMMARY["quality"], content_blocks_lost=3, unknown_chunks=7)
    counts = dict(COUNTS, partials=2)
    rep = coverage.summarize(
        MANIFEST, summary, counts, _payload([_round(1, "Blue")]), None
    )
    assert any("3 content blocks were lost" in t for t in _texts(rep))
    assert any("7 chunks were not recognised" in t for t in _texts(rep))
    assert any("2 fragmented bunches were rejected" in t for t in _texts(rep))
    assert rep["decode"]["partials_rows"] == 2
    assert rep["sources"]["source_file"] == "some.vrf"
    assert rep["sources"]["duration_ms"] == 1500000


def test_damage_unattributed_is_a_share_not_a_verdict():
    rep = coverage.summarize(MANIFEST, SUMMARY, COUNTS, _payload([_round(1, "Blue")]), None)
    assert rep["damage"]["to_opponents"] == 850.0
    assert rep["damage"]["unattributed_pct"] == 5.0
    assert any("50 damage has no attributed shooter (5.0%" in t for t in _texts(rep))
    assert any("100 damage was self-inflicted" in t for t in _texts(rep))


def test_shots_are_only_reported_when_the_engine_has_run():
    rep = coverage.summarize(MANIFEST, SUMMARY, COUNTS, _payload([_round(1, "Blue")]), None)
    assert rep["shots"] is None
    assert any("The aim engine could not be read" in t for t in _texts(rep))

    rep = coverage.summarize(
        MANIFEST,
        SUMMARY,
        COUNTS,
        _payload([_round(1, "Blue")]),
        {"shots_all_objects": 2000, "unresolved_shots": 4, "unresolved_hits": 1189,
         "kill_feed_unpaired": 3, "character_deaths": 186, "engagements_total": 60},
    )
    assert rep["shots"]["unresolved_pct"] == 0.2
    assert any("4 shots and 1,189 hit records" in t for t in _texts(rep))
    assert any("3 kill-feed entries" in t for t in _texts(rep))


@pytest.mark.slow
def test_a_real_export_agrees_with_the_engines(store, m1_stats, m1_metrics):
    manifest = analysis.load_manifest(store.manifest_path("m1"))
    summary = analysis.summarize(manifest, "m1")
    counts = analysis.table_row_counts(store.export_dir("m1"), coverage.tables_mod.ALL_TABLES)
    rep = coverage.summarize(
        manifest, summary, counts, m1_stats, (m1_metrics.get("validation") or None)
    )
    match = rep["match"]
    assert match["rounds"] == len(m1_stats["rounds"]) == 24
    assert match["players"] == len(m1_stats["players"]) == 10
    assert match["rounds_without_winner"] == 0
    assert match["rounds_without_sides"] == 0
    assert match["overtime_rounds"] == 0
    assert match["death_events"] == m1_stats["overview"]["total_kills_events"]
    assert match["kill_events"] == m1_stats["validation"]["combat_report_kills"]
    assert rep["decode"]["partials_rows"] == counts["partials"]
    assert rep["shots"] is not None
    assert rep["sources"]["source_file"]
