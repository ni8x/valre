"""How much of a match the export actually holds.

The reader's question here is never "was this a good match" but "did we capture
it, and where are the holes". Every number is derived rather than debated: the
manifest's decode quality, the per-table row counts, and the two engine caches
(stats.json always; metrics.json once the aim engine has run). Nothing in this
module is cached or versioned, so it can never make a stale payload look fresh.

A number we do not have stays ``None`` and its check line is simply not written.
The report never guesses a hole into existence, and never guesses one away.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import analysis
import metrics as metrics_mod
import stats as stats_mod
import tables as tables_mod

TEAMS = ("Blue", "Red")

# Plain-language limits, kept next to the numbers they qualify.
NOTES = [
    "Coverage describes what the export holds, not what the match server saw: a "
    "round or an event the export lost entirely leaves no trace here.",
    "Round and damage numbers come from the stats cache; the shot numbers come from "
    "the aim engine, which is computed on demand the first time it is asked for.",
]


def _team(value: Any) -> str | None:
    return value if value in TEAMS else None


def _pct(part: Any, whole: Any) -> float | None:
    if part is None or not whole:
        return None
    return round(100.0 * part / whole, 1)


def _n(value: Any) -> str:
    """A readable number for a sentence: no trailing '.0', no exponent."""
    if isinstance(value, float):
        return f"{value:,.1f}".rstrip("0").rstrip(".")
    return f"{value:,}"


def _flag(value: Any) -> bool | None:
    """Manifest flags arrive as 0/1; the report speaks yes/no."""
    return None if value is None else bool(value)


def _sources(manifest: dict, summary: dict) -> dict[str, Any]:
    source_file = manifest.get("source_file")
    return {
        "friendly_name": summary.get("friendly_name"),
        "build": summary.get("build"),
        "replay_version": summary.get("replay_version"),
        "changelist": summary.get("changelist"),
        "duration_ms": summary.get("duration_ms"),
        "elapsed_ms": manifest.get("elapsed_ms"),
        "platform": summary.get("platform"),
        "level_name": summary.get("level_name"),
        "timestamp_ticks": summary.get("timestamp_ticks"),
        # The basename only: the export records the path the file was opened from.
        "source_file": Path(source_file).name if source_file else None,
        "source_size_bytes": manifest.get("source_size_bytes"),
        "is_live": _flag(manifest.get("is_live")),
        "compressed": _flag(manifest.get("compressed")),
        "encrypted": _flag(manifest.get("encrypted")),
    }


def _decode(summary: dict, counts: dict) -> dict[str, Any]:
    quality = summary.get("quality") or {}
    return {
        "content_blocks_lost": quality.get("content_blocks_lost"),
        "malformed_content_blocks": quality.get("malformed_content_blocks"),
        "unknown_chunks": quality.get("unknown_chunks"),
        "typed_pct": quality.get("typed_pct"),
        "net_guid_rows": quality.get("net_guid_rows"),
        "event_rows": quality.get("event_rows"),
        "movement_rows": quality.get("movement_rows"),
        "partials_rows": counts.get("partials"),
    }


def _match_group(rounds: list, stats_payload: dict) -> dict[str, Any]:
    overview = (stats_payload or {}).get("overview") or {}
    validation = (stats_payload or {}).get("validation") or {}
    players = (stats_payload or {}).get("players") or []

    with_winner = sum(1 for r in rounds if _team(r.get("winner")))
    with_sides = sum(
        1 for r in rounds if _team((r.get("sides") or {}).get("defender"))
    )
    overtime = sum(1 for r in rounds if r.get("overtime"))
    spikes = {"planted": 0, "defused": 0, "exploded": 0}
    for r in rounds:
        for s in r.get("spikes") or []:
            kind = s.get("kind")
            if kind in spikes:
                spikes[kind] += 1
    self_deaths = 0
    for p in players:
        self_deaths += int((p.get("stats") or {}).get("self_deaths") or 0)

    return {
        "rounds": len(rounds),
        "rounds_with_winner": with_winner,
        "rounds_without_winner": len(rounds) - with_winner,
        "rounds_with_sides": with_sides,
        "rounds_without_sides": len(rounds) - with_sides,
        "overtime_rounds": overtime,
        "players": len(players),
        "kill_events": validation.get("combat_report_kills"),
        "death_events": overview.get("total_kills_events"),
        "self_deaths": self_deaths,
        "spike_plants": spikes["planted"],
        "spike_defuses": spikes["defused"],
        "spike_explosions": spikes["exploded"],
    }


def _damage_group(stats_payload: dict) -> dict[str, Any]:
    validation = (stats_payload or {}).get("validation") or {}
    total = validation.get("damage_region_total")
    self_damage = validation.get("damage_self_total")
    unattributed = validation.get("damage_unattributed_total")
    opponents = None
    if None not in (total, self_damage, unattributed):
        opponents = round(total - self_damage - unattributed, 1)
    return {
        "region_total": total,
        "to_opponents": opponents,
        "self": self_damage,
        "unattributed": unattributed,
        "unattributed_pct": _pct(unattributed, total),
        "self_pct": _pct(self_damage, total),
    }


def _shots_group(metrics_validation: dict | None) -> dict[str, Any] | None:
    if metrics_validation is None:
        return None
    mv = metrics_validation
    return {
        "shots_all_objects": mv.get("shots_all_objects"),
        "shots_real_guns": mv.get("shots_real_guns"),
        "unresolved_shots": mv.get("unresolved_shots"),
        "unresolved_hits": mv.get("unresolved_hits"),
        "unresolved_pct": _pct(mv.get("unresolved_shots"), mv.get("shots_all_objects")),
        "death_events": mv.get("character_deaths"),
        "kill_feed_pairs": mv.get("kill_feed_pairs"),
        "kill_feed_unpaired": mv.get("kill_feed_unpaired"),
        "engagements": mv.get("engagements_total"),
    }


def _checks(match: dict, decode: dict, damage: dict, shots: dict | None) -> list[dict]:
    """Plain sentences about what the numbers above mean, worst first is not
    attempted: the order is rounds -> decode -> damage -> shots, so the report
    reads the same way every time."""
    checks: list[dict] = []

    def add(level: str, text: str) -> None:
        checks.append({"level": level, "text": text})

    rounds = match.get("rounds") or 0
    if not rounds:
        add("warn", "No rounds were captured in this export.")
    else:
        add("ok", f"{rounds} rounds captured.")
        no_winner = match.get("rounds_without_winner") or 0
        if no_winner:
            add("warn", f"{no_winner} of {rounds} rounds have no winner in the export.")
        else:
            add("ok", f"All {rounds} rounds have a winner.")
        no_sides = match.get("rounds_without_sides") or 0
        overtime = match.get("overtime_rounds") or 0
        if no_sides:
            if overtime:
                add(
                    "info",
                    f"{overtime} overtime round{'' if overtime == 1 else 's'} carry no "
                    "sides, so they stay out of the attack/defence split.",
                )
            other = no_sides - overtime
            if other:
                add(
                    "warn",
                    f"{other} round{'' if other == 1 else 's'} carry no sides (no "
                    "defusal evidence to derive them from).",
                )
        else:
            add("ok", "Every round has its sides.")

    lost = decode.get("content_blocks_lost")
    if lost is not None:
        if lost:
            add("warn", f"{lost} content block{'' if lost == 1 else 's'} were lost "
                        "during export; whatever they held is not in this replay.")
        else:
            add("ok", "No content blocks were lost during export.")
    malformed = decode.get("malformed_content_blocks")
    if malformed is not None:
        if malformed:
            add("warn", f"{malformed} malformed content block"
                        f"{'' if malformed == 1 else 's'} were skipped.")
        else:
            add("ok", "No malformed content blocks.")
    partials = decode.get("partials_rows")
    if partials is not None:
        if partials:
            add("info", f"{partials} fragmented bunch{'es' if partials != 1 else ''} "
                        "were rejected and kept for diagnostics.")
        else:
            add("ok", "No fragmented bunches (partials) were rejected.")
    unknown = decode.get("unknown_chunks")
    if unknown:
        add("info", f"{unknown} chunk{'' if unknown == 1 else 's'} were not recognised, "
                    "so any field they carried is not read.")

    unattributed = damage.get("unattributed")
    if unattributed is not None:
        if unattributed:
            pct = damage.get("unattributed_pct")
            share = f" ({pct}% of all region damage)" if pct is not None else ""
            add("info", f"{_n(unattributed)} damage has no attributed shooter"
                        f"{share}; it is excluded from ADR.")
        else:
            add("ok", "Every point of damage is attributed to a shooter.")
    self_damage = damage.get("self")
    if self_damage:
        add("info", f"{_n(self_damage)} damage was self-inflicted and is excluded from ADR.")

    if shots is None:
        add("info", "The aim engine could not be read for this replay, so its shot "
                    "numbers are not part of this report.")
    else:
        parts = []
        us = shots.get("unresolved_shots")
        uh = shots.get("unresolved_hits")
        if us is not None:
            parts.append(f"{_n(us)} shot{'' if us == 1 else 's'}")
        if uh is not None:
            parts.append(f"{_n(uh)} hit record{'' if uh == 1 else 's'}")
        if parts:
            pct = shots.get("unresolved_pct")
            share = f" ({pct}% of all shot records)" if pct is not None else ""
            add("info", "The aim engine could not tie " + " and ".join(parts) +
                        f" to a shooter{share}.")
        unpaired = shots.get("kill_feed_unpaired")
        if unpaired:
            add("info", f"{_n(unpaired)} kill-feed entr{'y' if unpaired == 1 else 'ies'} "
                        "could not be paired with a death event.")
    return checks


def summarize(
    manifest: dict,
    summary: dict,
    counts: dict,
    stats_payload: dict,
    metrics_validation: dict | None = None,
) -> dict[str, Any]:
    """Assemble the coverage report from already-loaded inputs (pure)."""
    rounds = (stats_payload or {}).get("rounds") or []
    sources = _sources(manifest, summary)
    decode = _decode(summary, counts)
    match = _match_group(rounds, stats_payload)
    damage = _damage_group(stats_payload)
    shots = _shots_group(metrics_validation)
    return {
        "sources": sources,
        "streams": {name: counts.get(name) for name in tables_mod.ALL_TABLES},
        "decode": decode,
        "match": match,
        "damage": damage,
        "shots": shots,
        "checks": _checks(match, decode, damage, shots),
        "notes": list(NOTES),
    }


def build(store, replay_id: str) -> dict[str, Any]:
    """The coverage report for one replay, computed on demand (never cached)."""
    manifest = analysis.load_manifest(store.manifest_path(replay_id))
    summary = analysis.summarize(manifest, replay_id)
    counts = analysis.table_row_counts(store.export_dir(replay_id), tables_mod.ALL_TABLES)
    stats_payload = stats_mod.build(store, replay_id)
    metrics_validation = None
    try:
        metrics_validation = metrics_mod.build(store, replay_id).get("validation")
    except Exception:
        # A dead aim engine must not take the coverage report down with it.
        metrics_validation = None
    payload = summarize(manifest, summary, counts, stats_payload, metrics_validation)
    payload["replay_id"] = replay_id
    payload["generated_at"] = int(time.time() * 1000)
    return payload
