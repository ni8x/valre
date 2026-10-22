"""Manifest parsing + high level game summary."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _loadouts(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """game_specific_data is a list of JSON blobs; find the playerLoadouts one."""
    found: dict[str, dict[str, Any]] = {}
    for blob in manifest.get("game_specific_data") or []:
        if not isinstance(blob, str) or "playerLoadouts" not in blob:
            continue
        try:
            data = json.loads(blob)
        except Exception:
            continue
        for entry in data.get("playerLoadouts") or []:
            subj = entry.get("subject")
            if subj:
                found[subj] = entry
    return found


def player_table(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    loadouts = _loadouts(manifest)
    players = []
    for p in manifest.get("players") or []:
        subj = p.get("subject")
        lo = loadouts.get(subj) or {}
        players.append(
            {
                "subject": subj,
                "actor_net_guid": p.get("actor_net_guid"),
                "character_net_guid": p.get("character_net_guid"),
                "all_character_net_guids": p.get("character_net_guids"),
                "agent_uuid": (lo.get("characterId") or "").lower(),
                "loadout": lo.get("loadout"),
            }
        )
    return players


def summarize(manifest: dict[str, Any], replay_id: str) -> dict[str, Any]:
    levels = manifest.get("level_names_and_times") or []
    level_name = levels[0].get("name") if levels else None
    quality = manifest.get("quality") or {}
    counts = manifest.get("counts") or {}
    return {
        "id": replay_id,
        "friendly_name": manifest.get("friendly_name"),
        "title": manifest.get("friendly_name"),
        "build": manifest.get("replay_build"),
        "replay_version": manifest.get("replay_version"),
        "changelist": manifest.get("replay_changelist"),
        "duration_ms": manifest.get("duration_ms"),
        "platform": manifest.get("platform"),
        "level_name": level_name,
        "timestamp_ticks": manifest.get("timestamp_ticks"),
        "stats": manifest.get("stats"),
        "counts": counts,
        "quality": {
            "content_blocks_lost": quality.get("content_blocks_lost"),
            "unknown_chunks": quality.get("unknown_chunks"),
            "malformed_content_blocks": (quality.get("net") or {}).get("malformed_content_blocks"),
            "typed_pct": quality.get("typed_pct"),
            "movement_rows": quality.get("movement_rows"),
            "net_guid_rows": quality.get("net_guid_rows"),
            "event_rows": quality.get("event_rows"),
        },
        "players": player_table(manifest),
    }


def table_row_counts(export_dir: Path, names: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for n in names:
        p = export_dir / f"{n}.parquet"
        if p.exists():
            try:
                out[n] = pq.ParquetFile(p).metadata.num_rows
            except Exception:
                out[n] = 0
    return out


def rounds_from_events(events_path: Path) -> list[dict[str, Any]]:
    if not events_path.exists():
        return []
    t = pq.read_table(events_path)
    rows = t.to_pylist()
    rounds: dict[int, dict[str, Any]] = {}
    for r in rows:
        grp = r.get("group")
        w0 = r.get("word0")
        tms = r.get("time1")
        if grp == "roundStarted":
            n = int(w0) + 1 if w0 is not None else len(rounds) + 1
            rounds.setdefault(n, {"round": n, "start_ms": tms})
        elif grp == "switchTeams":
            for rd in rounds.values():
                rd.setdefault("switch_teams_ms", tms)
    return [rounds[k] for k in sorted(rounds)]
