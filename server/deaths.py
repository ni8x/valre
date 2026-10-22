"""The death audit: what the export shows around each death.

Everything here is derived on demand from the cached stats payload plus two small
reads of the export: the ultimate-point timeline on the player state (one row per
point change) and the ultimate-cast events. Nothing is cached or versioned, so a
new field can never make an older payload look fresh, and every number we do not
have stays ``None``.

The audit never calls a death a mistake. It reports what the export holds - whether
the player was traded, whether he was isolated, whether he had dealt damage, whether
his ultimate was charged - and leaves the judgement to the reader.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

import stats as stats_mod

# Reads aimed at the player state (one row per point change) and the cast events.
ULT_GROUP_REGEX = "BombPlayerState"
POINTS_FIELD = "NumUltimatePoints"
ACTIVE_FIELD = "bUltimateActive"
CAST_EVENT = "characterUltimateUsed"

NOTES = [
    "A death is untraded when the killer is not killed in the same round within "
    "5 seconds: the window the public stat pages use.",
    "Isolated means no living teammate within 12 m at the moment of death.",
    "An ultimate's cost is read from the charge just before a cast, never from the "
    "highest charge seen, because the export re-sends a zero at the half-time "
    "boundary. A player who never cast one has no known cost and his charge is "
    "left as a dash.",
    "This is a record of what the export shows, not a verdict: an untraded death "
    "can still have been the right play.",
]

# Every class is plain evidence, and each one is counted from the same death list.
CLASSES = (
    ("untraded", "untraded"),
    ("isolated", "isolated"),
    ("no_damage", "no damage dealt"),
    ("ult_ready", "ultimate charged"),
    ("opening", "opening death"),
    ("self_inflicted", "self-inflicted"),
)


def _passive_cast(points: list, t: int):
    """Charge at time ``t``: the last point value at or before it."""
    before = [v for (ts, v) in points if ts <= t]
    return before[-1] if before else None


def _ult_cost(points: list, casts: list) -> int | None:
    """Cost of an ultimate, taken from the charge just before a cast.

    A cast can only happen at full charge and the charge never passes the cost, so
    the largest value seen before a cast is the cost. Without a cast we do not know
    it, and saying nothing beats guessing.
    """
    seen = []
    for c in casts:
        v = _passive_cast(points, c)
        if v is not None:
            seen.append(v)
    return max(seen) if seen else None


def _active_at(active: list, t: int):
    before = [v for (ts, v) in active if ts <= t]
    return before[-1] if before else None


def _ultimates(export_dir: Path, players: list[dict]) -> dict[str, dict]:
    """Ultimate charge per subject: cost, casts, point timeline, active windows."""
    by_state: dict[int, str] = {}
    by_char: dict[int, str] = {}
    for p in players:
        subj = p.get("subject")
        st = p.get("player_state_guid")
        ch = p.get("character_net_guid")
        if subj and st not in (None, ""):
            by_state[int(st)] = subj
        if subj and ch not in (None, ""):
            by_char[int(ch)] = subj

    points: dict[str, list] = {}
    active: dict[str, list] = {}
    rows = stats_mod._read_fields(
        export_dir,
        ULT_GROUP_REGEX,
        ["group_path", "time_ms", "actor_net_guid", "field_name", "value_i64", "value_bool"],
    )
    for r in rows:
        actor = r.get("actor_net_guid")
        t = r.get("time_ms")
        if actor is None or t is None:
            continue
        subj = by_state.get(int(actor))
        if subj is None:
            continue
        if r.get("field_name") == POINTS_FIELD and r.get("value_i64") is not None:
            points.setdefault(subj, []).append((int(t), int(r["value_i64"])))
        elif r.get("field_name") == ACTIVE_FIELD and r.get("value_bool") is not None:
            active.setdefault(subj, []).append((int(t), bool(r["value_bool"])))

    casts: dict[str, list] = {}
    ev = export_dir / "events.parquet"
    if ev.exists():
        tbl = pq.read_table(ev, columns=["group", "time1", "word0"])
        g = tbl.column("group")
        if pa.types.is_dictionary(g.type):
            tbl = tbl.set_column(tbl.schema.get_field_index("group"), "group", g.cast(pa.string()))
        for r in tbl.to_pylist():
            if r.get("group") != CAST_EVENT:
                continue
            who = r.get("word0")
            t = r.get("time1")
            if who is None or t is None:
                continue
            subj = by_char.get(int(who))
            if subj is not None:
                casts.setdefault(subj, []).append(int(t))

    out: dict[str, dict] = {}
    for subj in set(points) | set(active) | set(casts):
        line = sorted(points.get(subj, []))
        cast_times = sorted(casts.get(subj, []))
        out[subj] = {
            "cost": _ult_cost(line, cast_times),
            "casts": cast_times,
            "points": line,
            "active": sorted(active.get(subj, [])),
        }
    return out


def _flag(value) -> bool | None:
    return None if value is None else bool(value)


def summarize(stats_payload: dict, ult: dict[str, dict] | None = None) -> dict:
    """Build the audit from a stats payload and (optionally) the ultimate timeline."""
    ult = ult or {}
    rounds = {int(r["round"]): r for r in stats_payload.get("rounds") or [] if r.get("round") is not None}
    players = stats_payload.get("players") or []
    agent_of = {p.get("subject"): p.get("agent_uuid") for p in players}

    per_round: dict[str, dict] = {}
    for p in players:
        per_round[p.get("subject")] = {
            int(row["round"]): row
            for row in (p.get("per_round") or [])
            if row.get("round") is not None
        }

    blocks = []
    totals = {key: 0 for key, _ in CLASSES}
    total_deaths = 0

    for p in players:
        subj = p.get("subject")
        events = []
        for rn in sorted(rounds):
            rnd = rounds[rn]
            for d in rnd.get("deaths") or []:
                if d.get("victim") != subj:
                    continue
                t = d.get("t")
                u = ult.get(subj) or {}
                pts = _passive_cast(u.get("points") or [], int(t)) if t is not None else None
                active = _active_at(u.get("active") or [], int(t)) if t is not None else None
                cost = u.get("cost")
                killer = d.get("killer")
                start = rnd.get("start_ms")
                dmg = (per_round.get(subj) or {}).get(rn, {}).get("damage_opponents")
                same_round = rnd.get("deaths") or []
                winner = rnd.get("winner")
                events.append(
                    {
                        "round": rn,
                        "t": t,
                        "round_ms": (int(t) - int(start)) if (t is not None and start is not None) else None,
                        "round_won": (winner == d.get("victim_team")) if winner else None,
                        "killer": killer,
                        "killer_team": d.get("killer_team"),
                        "killer_agent": agent_of.get(killer),
                        "killer_survived": None
                        if killer is None
                        else not any(x.get("victim") == killer for x in same_round),
                        "damage_round": dmg,
                        "dealt_no_damage": (dmg == 0) if isinstance(dmg, (int, float)) else None,
                        "first_blood": _flag(d.get("first_blood")),
                        "traded": _flag(d.get("traded")),
                        "isolated": _flag(d.get("isolated")),
                        "bait": _flag(d.get("bait")),
                        "suicide": _flag(d.get("suicide")),
                        "ult_cost": cost,
                        "ult_points": pts,
                        "ult_ready": (pts >= cost) if (pts is not None and cost is not None) else None,
                        "ult_active": active,
                    }
                )

        events.sort(key=lambda e: (e["t"] is None, e["t"]))
        n = len(events)
        total_deaths += n
        counts = {
            "untraded": sum(1 for e in events if e["traded"] is False),
            "isolated": sum(1 for e in events if e["isolated"] is True),
            "no_damage": sum(1 for e in events if e["dealt_no_damage"] is True),
            "ult_ready": sum(1 for e in events if e["ult_ready"] is True),
            "opening": sum(1 for e in events if e["first_blood"] is True),
            "self_inflicted": sum(1 for e in events if e["suicide"] is True),
        }
        for key, _ in CLASSES:
            totals[key] += counts[key]

        texts = {
            "untraded": f"{counts['untraded']} of {n} deaths were not traded",
            "isolated": f"{counts['isolated']} of {n} deaths came with no teammate within 12 m",
            "no_damage": f"{counts['no_damage']} of {n} deaths were in a round with no damage dealt to opponents",
            "ult_ready": f"{counts['ult_ready']} of {n} deaths happened with an ultimate charged",
            "opening": f"{counts['opening']} of {n} deaths were the round's first death",
            "self_inflicted": f"{counts['self_inflicted']} of {n} deaths were self-inflicted",
        }
        classes = [
            {"key": key, "label": label, "n": counts[key], "text": texts[key]}
            for key, label in CLASSES
            if counts[key] > 0
        ]
        blocks.append(
            {
                "subject": subj,
                "team": p.get("team"),
                "agent_uuid": p.get("agent_uuid"),
                "deaths": n,
                "classes": classes,
                "events": events,
            }
        )

    return {
        "n_rounds": len(rounds),
        "ult_available": bool(ult),
        "players": blocks,
        "totals": dict(totals, deaths=total_deaths),
        "classes": [{"key": key, "label": label} for key, label in CLASSES],
        "notes": NOTES,
    }


def build(store, replay_id: str) -> dict:
    """The audit for one replay, computed now and not kept."""
    payload = stats_mod.build(store, replay_id)
    ult: dict[str, dict] = {}
    try:
        ult = _ultimates(store.export_dir(replay_id), payload.get("players") or [])
    except Exception:
        # A replay whose player state cannot be read still gets the audit, with
        # every ultimate number left as a dash.
        ult = {}
    rep: dict[str, Any] = summarize(payload, ult)
    rep["replay_id"] = replay_id
    rep["generated_at"] = int(time.time() * 1000)
    return rep
