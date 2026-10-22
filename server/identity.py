"""Phase 14: tag the user from facts, not from a guess.

A replay carries no name anywhere - only the four facts the owner named: the
agent each player used, the map, the match start time, and the side he started
on (attacker or defender). This module reads those facts out of an export and
ranks the ten subjects by how many of the three questions they answer.

Nothing is weighted and nothing is guessed: the score is a plain count of the
facts that match, and a subject is ``certain`` only when it holds all three and
no other subject does.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import analysis
import spatial
import stats as stats_mod
import valorant_api

# The export stores .NET DateTime ticks since 0001-01-01 (not FILETIME ticks
# since 1601 - that mistake printed a year 3626 in Phase 12).
TICKS_EPOCH = dt.datetime(1, 1, 1, tzinfo=dt.timezone.utc)
TICKS_PER_SECOND = 10_000_000

SIDES = ("attacker", "defender")


def start_of(manifest: dict[str, Any]) -> dt.datetime | None:
    """Match start from the export's ``timestamp_ticks`` (UTC), or None."""
    ticks = manifest.get("timestamp_ticks")
    if not ticks:
        return None
    try:
        return TICKS_EPOCH + dt.timedelta(seconds=float(ticks) / TICKS_PER_SECOND)
    except (TypeError, ValueError, OverflowError):
        return None


def _agents() -> dict[str, str]:
    try:
        return {a["uuid"]: a["name"] for a in valorant_api.agents()}
    except Exception:  # noqa: BLE001 - the metadata cache is a nicety, not a fact
        return {}


def _level(store, replay_id: str) -> str | None:
    rec = store.get(replay_id) or {}
    return ((rec.get("summary") or {}).get("level_name")) or None


def signature(store, replay_id: str) -> dict[str, Any]:
    """Everything a replay says about who was in it - no names, no guesses."""
    manifest = analysis.load_manifest(store.manifest_path(replay_id))
    payload = stats_mod.build(store, replay_id)
    info, _proj = spatial.map_ctx(store, replay_id)
    names = _agents()
    start = start_of(manifest)

    rounds = payload.get("rounds") or []
    first_sides = (rounds[0].get("sides") or {}) if rounds else {}

    table = {row.get("subject"): row for row in analysis.player_table(manifest)}
    players: list[dict[str, Any]] = []
    for p in payload.get("players") or []:
        subject = p.get("subject")
        row = table.get(subject) or {}
        agent_uuid = row.get("agent_uuid")
        team = p.get("team")
        side = None
        for name, side_team in first_sides.items():
            if side_team and team and str(side_team).lower() == str(team).lower():
                side = name
        pawns = sorted(row.get("all_character_net_guids") or [])
        players.append(
            {
                "subject": subject,
                "agent": names.get(agent_uuid),
                "agent_uuid": agent_uuid,
                "team": team,
                "side_first": side if side in SIDES else None,
                "pawns": pawns,
            }
        )

    local = start.astimezone() if start else None
    return {
        "replay_id": replay_id,
        "map": (info or {}).get("name"),
        "level": _level(store, replay_id),
        "start_utc": start.isoformat() if start else None,
        "start_local": local.replace(tzinfo=None).isoformat(timespec="minutes") if local else None,
        "start_ms": int(start.timestamp() * 1000) if start else None,
        "duration_s": round((manifest.get("duration_ms") or 0) / 1000.0, 1),
        "sides_first": {k: v for k, v in first_sides.items() if k in SIDES},
        "players": players,
        "notes": [
            "A replay holds no Riot ID or display name - only the agent each player used, the map, "
            "the match time and the first-half side. The tag is always a subject plus the evidence "
            "that produced it.",
        ],
    }


def _parse_at(at) -> tuple[dt.datetime | None, str | None]:
    """Accept an ISO string or epoch milliseconds; return (aware local, why)."""
    if at is None or at == "":
        return None, None
    if isinstance(at, (int, float)):
        return dt.datetime.fromtimestamp(at / 1000.0, tz=dt.timezone.utc), None
    text = str(at).strip()
    if text.isdigit():
        # a bare epoch-millisecond stamp (the identify route's `at` is a string)
        return dt.datetime.fromtimestamp(int(text) / 1000.0, tz=dt.timezone.utc), None
    try:
        got = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None, f"could not read the time {text!r} - use 2026-10-15T21:30"
    if got.tzinfo is None:
        got = got.astimezone()
    return got, None


def identify(store, replay_id: str, agent: str | None = None, side: str | None = None,
             at=None, tolerance_min: float = 120.0) -> dict[str, Any]:
    """Rank the subjects by how many of the asked facts they answer.

    ``agent`` is matched as a case-insensitive substring of the agent name,
    ``side`` must be attacker/defender, ``at`` must fall within
    ``tolerance_min`` minutes of the match start. Score is the count of the
    asked facts matched; ``certain`` needs all three and a unique top.
    """
    sig = signature(store, replay_id)
    asked_at, why = _parse_at(at)
    agent_q = (agent or "").strip().lower() or None
    side_q = (side or "").strip().lower() or None
    if side_q and side_q not in SIDES:
        why = f"side must be attacker or defender, not {side!r}"
        side_q = None
    asked = [x for x in (agent_q, side_q, asked_at) if x is not None]
    total = len(asked)

    start_ms = sig.get("start_ms")
    candidates: list[dict[str, Any]] = []
    for p in sig["players"]:
        facts = {"agent": None, "side": None, "time": None}
        if agent_q:
            facts["agent"] = bool(p.get("agent") and agent_q in p["agent"].lower())
        if side_q:
            facts["side"] = p.get("side_first") == side_q
        delta_min = None
        if asked_at is not None and start_ms is not None:
            delta_min = round((asked_at.timestamp() * 1000 - start_ms) / 60000.0, 1)
            facts["time"] = abs(delta_min) <= tolerance_min
        score = sum(1 for v in facts.values() if v)
        candidates.append(
            {
                "subject": p.get("subject"),
                "agent": p.get("agent"),
                "team": p.get("team"),
                "side_first": p.get("side_first"),
                "score": score,
                "facts": facts,
                "delta_min": delta_min,
                "certain": False,
            }
        )

    candidates.sort(key=lambda c: (-c["score"], str(c["subject"])))
    if total == 3 and candidates:
        top = candidates[0]
        runner = candidates[1]["score"] if len(candidates) > 1 else -1
        top["certain"] = top["score"] == 3 and runner < 3

    if why:
        note = why
    elif total == 0:
        note = ("Nothing was asked, so every subject scores 0. Give the agent, the side or the time - "
                "nothing is guessed without a fact.")
    elif not any(c["certain"] for c in candidates):
        note = (f"No subject answered all {total} asked facts. The time tolerance is "
                f"{tolerance_min:g} minutes; a wrong answer is reported, never rounded up.")
    else:
        note = "One subject answered every asked fact and no other subject did."

    return {
        "replay_id": replay_id,
        "map": sig.get("map"),
        "start_local": sig.get("start_local"),
        "asked": {
            "agent": agent or None,
            "side": side_q,
            "at": asked_at.isoformat(timespec="minutes") if asked_at else None,
            "tolerance_min": tolerance_min,
            "facts": total,
        },
        "candidates": candidates,
        "note": note,
        "notes": sig.get("notes") or [],
    }