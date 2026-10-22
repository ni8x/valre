"""The duo check: one of my deaths, read from my partner's side.

The question this answers is not a scoreboard one - it is "when I died, where was my duo, and did he
ever come up" - so every number here is anchored to a single death: the distance between the two of us
at that instant, whether he was in front of me or behind me in my own facing, whether he was moving up
or backing off in the seconds before, how close he ever got afterwards, and whether he dealt any damage
in that round at all.

Rules are the ones the project already publishes, reused rather than reinvented:

* the trade window is ``stats.TRADE_WINDOW_MS`` (5000 ms) and a death is traded when the same round
  holds a later death of the killer within that window;
* the trade range is ``stats.ISO_DISTANCE_CM`` (1200 cm) - the "no living teammate within 12 m" rule the
  death audit states;
* distance is the same 3D Euclidean cm metric ``stats._dist`` uses, at the nearest ~128 Hz sample, with
  hidden/parked actors dropped exactly like the position engine does.

Nothing is cached and nothing is versioned: a payload is computed on demand from the cached ``stats.json``
plus ``movement.parquet``, so it can never be stale, and it reports its own seconds. A missing position is
``None`` and lands in the ``unknown`` band as a **count**, never a zero. Untraded is not blame: the module
reports the geometry and leaves the argument to the reader.
"""
from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

import analysis
import players as players_mod
import regions as regions_mod
import settings as settings_mod
import stats as stats_mod

TRADE_WINDOW_MS = stats_mod.TRADE_WINDOW_MS
ISO_DISTANCE_CM = stats_mod.ISO_DISTANCE_CM

# under this the partner counts as standing still in the 3 s before the death (~0.3 s of full speed)
STILL_CM = 200.0
TRACK_MS = (4000, 3000, 2000, 1000, 0)
AFTER_CAP_MS = 30000  # if a round carries no end time, stop looking for him 30 s after the death
MOV_COLUMNS = ("time_ms", "character_net_guid", "pos_x", "pos_y", "pos_z", "yaw")

BANDS = ("<=12m", "12-25m", ">25m", "unknown")

NOTES = (
    "A pair row is one of my deaths, read at that instant: how far my partner was, whether he was alive, "
    "which side of my facing he was on, and how close he ever came afterwards.",
    "Traded by him means he killed my killer in the same round within the published 5 s window; the "
    "payload's own traded flag is shown beside it, so a trade by a third teammate is never counted as his.",
    "Distance is straight-line 3D at the nearest ~128 Hz sample, and the 12 m band is the same rule the "
    "death audit publishes (no living teammate within 1200 cm).",
    "A death with no position for either of us is counted in the unknown band and never read as a zero.",
    "A round the export does not give a winner is counted and kept out of the win rate, never read as a loss.",
)

LIMITS = (
    "The export holds no comms, so \"even after giving calls\" cannot be graded; the closest honest "
    "reading of a death is that you moved in and he did not.",
    "The place names come from the game's own callout list (valorant-api.com), placed with the same "
    "projection the minimap uses. A name is the nearest callout centre, so it says \"closest named "
    "place\" - the offset to that centre is printed beside it, and it is 2D (no floors) with no "
    "knowledge of walls. Ahead/behind stays in your own facing, not against the objective.",
    "Distance is geometry, not possibility: it does not know about walls, smoke, line of sight, or whether "
    "a trade was even available to him.",
    "An untraded death is not automatically a mistake - it can still have been the right play.",
    "The trio is not in the uploaded data as one unit (no match holds all three of the repeating subjects), "
    "so a trio reads as the pairs inside it.",
)


# ---- tiny helpers ----------------------------------------------------


def _pct(part: Any, whole: Any, digits: int = 1) -> float | None:
    if part is None or not whole:
        return None
    return round(100.0 * float(part) / float(whole), digits)


def _div(part: Any, whole: Any, digits: int = 2) -> float | None:
    if part is None or not whole:
        return None
    return round(float(part) / float(whole), digits)


def band(distance_cm: float | None) -> str:
    """The published-distance bands. A missing distance is `unknown`, and unknown is only ever a count."""
    if distance_cm is None:
        return "unknown"
    if distance_cm <= ISO_DISTANCE_CM:
        return "<=12m"
    if distance_cm <= 2500.0:
        return "12-25m"
    return ">25m"


def died_before(deaths: list[dict], subject: str, t_ms: float) -> bool:
    """Was this subject already dead when the clock read t_ms? (the rule stats.py uses)"""
    return any(d.get("victim") == subject and (d.get("t") or 0) <= t_ms for d in deaths)


def traded_by(deaths: list[dict], death: dict, trader: str, window_ms: int = TRADE_WINDOW_MS) -> dict | None:
    """The row in which `trader` killed this death's killer inside the window, or None.

    This is the exact attribution: it has to be the killer of *my* killer, inside 5000 ms, in the same
    round. A later death by anyone else does not count.
    """
    killer = death.get("killer")
    if not killer:
        return None
    t = death.get("t") or 0
    for row in deaths:
        if row.get("victim") != killer or row.get("killer") != trader:
            continue
        rt = row.get("t") or 0
        if rt < t:
            continue
        if rt - t <= window_ms:
            return row
    return None


def facing_split(target: Any, me: Any, yaw_deg: float | None) -> tuple[float | None, float | None]:
    """The delta from me to target in my facing frame: (forward_m, left_m).

    The export's yaw is degrees and its world direction on the XY plane is (cos yaw, sin yaw).
    """
    if target is None or me is None or yaw_deg is None:
        return None, None
    dx = float(target[0]) - float(me[0])
    dy = float(target[1]) - float(me[1])
    r = math.radians(float(yaw_deg))
    fwd = dx * math.cos(r) + dy * math.sin(r)
    left = -dx * math.sin(r) + dy * math.cos(r)
    return round(fwd / 100.0, 1), round(left / 100.0, 1)


def _movement(export_dir: Path, guids: list[str]) -> dict[int, dict[str, Any]]:
    """Per-pawn time-sorted tracks for the pair's pawns only, from movement.parquet."""
    path = export_dir / "movement.parquet"
    table = pq.read_table(path, columns=list(MOV_COLUMNS))
    gcol = table.column("character_net_guid")
    gtype = table.schema.field("character_net_guid").type
    if pa.types.is_string(gtype) or pa.types.is_large_string(gtype):
        wanted: list[Any] = [str(g) for g in guids]
    else:
        wanted = []
        for g in guids:
            try:
                wanted.append(int(g))
            except (TypeError, ValueError):
                continue
    if wanted:
        table = table.filter(pc.is_in(gcol, value_set=pa.array(wanted, type=gtype)))
    t = table.column("time_ms").to_numpy(zero_copy_only=False).astype(np.float64)
    g = table.column("character_net_guid").to_numpy(zero_copy_only=False)
    x = table.column("pos_x").to_numpy(zero_copy_only=False).astype(np.float64)
    y = table.column("pos_y").to_numpy(zero_copy_only=False).astype(np.float64)
    z = table.column("pos_z").to_numpy(zero_copy_only=False).astype(np.float64)
    yaw = table.column("yaw").to_numpy(zero_copy_only=False).astype(np.float64)
    # the same hidden/parked filter the position engine applies
    keep = np.isfinite(x) & np.isfinite(y) & (x >= -20000.0) & (y >= -20000.0)
    t, g, x, y, z, yaw = t[keep], g[keep], x[keep], y[keep], z[keep], yaw[keep]
    yaw = np.where(yaw > 180.0, yaw - 360.0, yaw)
    order = np.argsort(t, kind="stable")
    t, g, x, y, z, yaw = t[order], g[order], x[order], y[order], z[order], yaw[order]
    out: dict[int, dict[str, Any]] = {}
    for guid in np.unique(g):
        m = g == guid
        try:
            key = int(guid)
        except (TypeError, ValueError):
            continue
        out[key] = {"t": t[m], "x": x[m], "y": y[m], "z": z[m], "yaw": yaw[m]}
    return out


def _sample(track: dict[str, Any] | None, t_ms: float) -> tuple[Any, float | None, float | None]:
    """The nearest sample of one pawn in time: (xyz, dt_ms, yaw)."""
    if not track or not len(track["t"]):
        return None, None, None
    ts = track["t"]
    i = int(np.searchsorted(ts, t_ms))
    cands = [j for j in (i, i - 1) if 0 <= j < len(ts)]
    if not cands:
        return None, None, None
    j = min(cands, key=lambda k: abs(float(ts[k]) - t_ms))
    pos = (float(track["x"][j]), float(track["y"][j]), float(track["z"][j]))
    return pos, float(ts[j]) - t_ms, float(track["yaw"][j])


def subject_sample(tracks: dict[int, dict[str, Any]], pawns: list[Any], t_ms: float) -> tuple[Any, float | None, float | None]:
    """The nearest sample for a subject: an agent swap means trying every pawn he played, nearest wins."""
    best = None
    for guid in pawns:
        try:
            key = int(guid)
        except (TypeError, ValueError):
            continue
        pos, dt, yaw = _sample(tracks.get(key), t_ms)
        if pos is None or dt is None:
            continue
        if best is None or abs(dt) < abs(best[1]):
            best = (pos, dt, yaw)
    if best is None:
        return None, None, None
    return best


def _window(tracks: dict[int, dict[str, Any]], pawns: list[Any], t_from: float, t_to: float):
    """Every sample of a subject strictly after t_from up to t_to, merged in time order."""
    ts, xs, ys, zs = [], [], [], []
    for guid in pawns:
        try:
            track = tracks.get(int(guid))
        except (TypeError, ValueError):
            continue
        if not track or not len(track["t"]):
            continue
        a = int(np.searchsorted(track["t"], t_from, side="right"))
        b = int(np.searchsorted(track["t"], t_to, side="right"))
        if b <= a:
            continue
        ts.append(track["t"][a:b])
        xs.append(track["x"][a:b])
        ys.append(track["y"][a:b])
        zs.append(track["z"][a:b])
    if not ts:
        return None
    t = np.concatenate(ts)
    x = np.concatenate(xs)
    y = np.concatenate(ys)
    z = np.concatenate(zs)
    order = np.argsort(t, kind="stable")
    return t[order], x[order], y[order], z[order]


def closest_after(tracks: dict[int, dict[str, Any]], pawns: list[Any], target: Any,
                  t_from: float, t_to: float) -> tuple[int | None, int | None, int | None]:
    """(closest cm, ms after the death when it happened, ms until he first came within 12 m)."""
    got = _window(tracks, pawns, t_from, t_to)
    if got is None or target is None:
        return None, None, None
    t, x, y, z = got
    dx = x - float(target[0])
    dy = y - float(target[1])
    dz = z - float(target[2])
    d = np.sqrt(dx * dx + dy * dy + dz * dz)
    k = int(np.argmin(d))
    within = np.nonzero(d <= ISO_DISTANCE_CM)[0]
    arrived = int(round(float(t[within[0]]) - t_from)) if len(within) else None
    return int(round(float(d[k]))), int(round(float(t[k]) - t_from)), arrived


def _pawns_by_subject(manifest) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    for p in analysis.player_table(manifest):
        guids = list(p.get("all_character_net_guids") or [])
        single = p.get("character_net_guid")
        if single and single not in guids:
            guids.append(single)
        if guids:
            out[p["subject"]] = guids
    return out


def _combined_side(rows: list[dict]) -> dict[str, Any]:
    """One player's totals over the shared rounds, from his own per-round rows."""
    rounds = len(rows)
    kills = sum(int(r.get("kills") or 0) for r in rows)
    deaths = sum(int(r.get("deaths") or 0) for r in rows)
    assists = sum(int(r.get("assists") or 0) for r in rows)
    damage = round(sum(float(r.get("damage_opponents") or 0.0) for r in rows), 1)
    kast_rounds = sum(1 for r in rows if r.get("kast"))
    return {
        "rounds": rounds or None,
        "kills": kills, "deaths": deaths, "assists": assists,
        "damage_opponents": damage,
        "kast_rounds": kast_rounds,
        "adr": _div(damage, rounds, 1),
        "kd": _div(kills, deaths, 2),
        "kast_pct": _pct(kast_rounds, rounds, 1),
        "kpr": _div(kills, rounds, 2),
    }


ACS_NOTE = ("ACS is not published per round in the cached payload, so the combined block omits it; "
            "each player's match-level ACS stays on his profile and on the match pages.")


def combined_pair(my_rows: list[dict], his_rows: list[dict]) -> dict[str, Any]:
    """The pair's totals over the rounds they actually shared."""
    me = _combined_side(my_rows)
    him = _combined_side(his_rows)
    rounds = max(me["rounds"] or 0, him["rounds"] or 0)
    kills = (me["kills"] or 0) + (him["kills"] or 0)
    deaths = (me["deaths"] or 0) + (him["deaths"] or 0)
    assists = (me["assists"] or 0) + (him["assists"] or 0)
    damage = round((me["damage_opponents"] or 0.0) + (him["damage_opponents"] or 0.0), 1)
    kast_rounds = (me["kast_rounds"] or 0) + (him["kast_rounds"] or 0)
    return {
        "rounds": rounds or None,
        "kills": kills, "deaths": deaths, "assists": assists,
        "damage_opponents": damage,
        "kast_rounds": kast_rounds,
        "adr": _div(damage, rounds, 1),
        "kd": _div(kills, deaths, 2),
        "kast_pct": _pct(kast_rounds, 2 * rounds if rounds else 0, 1),
        "kpr": _div(kills, rounds, 2),
        "me": me,
        "partner": him,
        "n_note": ACS_NOTE,
    }


def _death_row(death: dict, rnd: dict, victim: str, mate: str, tracks: dict, pawns: dict,
               my_team: str | None, victim_per_round: dict, mate_per_round: dict,
               window_end: float, geo: dict | None = None) -> dict[str, Any]:
    """One death read from the victim's side: where the mate was and what he was doing."""
    t = float(death.get("t") or 0)
    start = float(rnd.get("start_ms") or 0)
    deaths = rnd.get("deaths") or []
    my_pos, my_dt, my_yaw = subject_sample(tracks, pawns.get(victim) or [], t)
    mate_pos, mate_dt, _ = subject_sample(tracks, pawns.get(mate) or [], t)
    distance = stats_mod._dist(my_pos, mate_pos)
    distance_cm = int(round(distance)) if distance is not None else None
    forward_m, lateral_m = facing_split(mate_pos, my_pos, my_yaw)
    proj = (geo or {}).get("proj")
    regs = (geo or {}).get("regions") or []
    my_region = regions_mod.region_at_pos(proj, regs, my_pos)
    mate_region = regions_mod.region_at_pos(proj, regs, mate_pos)

    track = []
    at = {}
    for ms in TRACK_MS:
        pos, _, _ = subject_sample(tracks, pawns.get(mate) or [], t - ms)
        d = stats_mod._dist(my_pos, pos)
        at[ms] = d
        track.append({"ms_before": ms, "distance_m": round(d / 100.0, 1) if d is not None else None})
    closing = None
    if at.get(0) is not None and at.get(3000) is not None:
        closing = int(round(at[0] - at[3000]))
    if closing is None:
        move = None
    elif closing <= -STILL_CM:
        move = "up"
    elif closing >= STILL_CM:
        move = "back"
    else:
        move = "static"

    if mate_pos is not None and my_pos is not None and distance is not None and distance <= ISO_DISTANCE_CM:
        arrived = 0  # he was already inside trade range when I died
    else:
        arrived = None
    closest_cm, closest_ms, came = closest_after(tracks, pawns.get(mate) or [], my_pos, t, window_end)
    if arrived is None:
        arrived = came

    trade_row = traded_by(deaths, death, mate)
    mate_alive = not died_before(deaths, mate, t)
    winner = rnd.get("winner")
    return {
        "match_id": None,  # filled by the caller
        "round": rnd.get("round"),
        "t": int(t),
        "t_into_round_s": round((t - start) / 1000.0, 1) if start else None,
        "first_blood": bool(death.get("first_blood")),
        "round_winner": winner,
        "round_won": None if winner is None else (winner == my_team),
        "distance_cm": distance_cm,
        "distance_m": round(distance / 100.0, 1) if distance is not None else None,
        "distance_band": band(distance_cm),
        "my_region": my_region,
        "mate_region": mate_region,
        "my_sample_dt_ms": None if my_dt is None else int(round(my_dt)),
        "mate_sample_dt_ms": None if mate_dt is None else int(round(mate_dt)),
        "mate_alive": mate_alive,
        "partner_forward_m": forward_m,
        "partner_lateral_m": lateral_m,
        "partner_closing_cm": closing,
        "partner_move": move,
        "partner_track": track,
        "partner_closest_cm": closest_cm,
        "partner_closest_at_ms": closest_ms,
        "partner_arrived_after_ms": arrived,
        "partner_damage_that_round": (mate_per_round or {}).get("damage_opponents"),
        "partner_killed_my_killer": bool(trade_row),
        "partner_killed_my_killer_at_ms": (int((trade_row.get("t") or 0) - t) if trade_row else None),
        "traded": bool(death.get("traded")),
        "isolated": death.get("isolated"),
        "bait": death.get("bait"),
        "suicide": bool(death.get("suicide")),
        "killer": death.get("killer"),
        "damage_dealt_that_round": (victim_per_round or {}).get("damage_opponents"),
    }


def _counts(rows: list[dict]) -> dict[str, int]:
    return {
        "total": len(rows),
        "traded_by_mate": sum(1 for r in rows if r["partner_killed_my_killer"]),
        "traded_by_anyone": sum(1 for r in rows if r["traded"]),
        "untraded_mate_alive": sum(1 for r in rows if not r["traded"] and r["mate_alive"]),
        "untraded_mate_alive_in_range": sum(1 for r in rows if not r["traded"] and r["mate_alive"]
                                           and r["distance_cm"] is not None
                                           and r["distance_cm"] <= ISO_DISTANCE_CM),
        "untraded_mate_alive_far": sum(1 for r in rows if not r["traded"] and r["mate_alive"]
                                       and r["distance_cm"] is not None
                                       and r["distance_cm"] > 2500.0),
        "untraded_mate_alive_unknown": sum(1 for r in rows if not r["traded"] and r["mate_alive"]
                                           and r["distance_cm"] is None),
        "untraded_mate_dead": sum(1 for r in rows if not r["traded"] and not r["mate_alive"]),
        "untraded_mate_never_came": sum(1 for r in rows if not r["traded"] and r["mate_alive"]
                                        and r["partner_arrived_after_ms"] is None),
    }


def _match_block(store, match: dict[str, Any], me: str, partner: str) -> dict[str, Any]:
    """One match's rows for the pair, or a block carrying the reason it has none."""
    match_id = match["match_id"]
    blank = {
        "match_id": match_id,
        "replay_id": match["replay_id"],
        "level_name": (match["summary"] or {}).get("level_name"),
        "team": None,
        "rounds_together": 0,
        "rounds_without_winner": 0,
        "wins": 0,
        "losses": 0,
        "win_pct": None,
        "my_deaths": [],
        "his_deaths": [],
        "contacts": _blank_contacts(),
        "combined": combined_pair([], []),
        "_rows": {"me": [], "partner": []},
        "why": None,
    }
    payload = stats_mod.build(store, match["replay_id"])
    listed = payload.get("players") or []
    mine = next((p for p in listed if p.get("subject") == me), None)
    his = next((p for p in listed if p.get("subject") == partner), None)
    if mine is None or his is None:
        blank["why"] = "one of the two is not in this match"
        return blank
    my_team = mine.get("team")
    block = dict(blank)
    block["team"] = my_team
    if his.get("team") != my_team:
        block["why"] = "the two were on opposite teams in this match"
        return block

    manifest = analysis.load_manifest(store.manifest_path(match["replay_id"]))
    pawns = _pawns_by_subject(manifest)
    guids = list(pawns.get(me) or []) + list(pawns.get(partner) or [])
    tracks = _movement(store.export_dir(match["replay_id"]), guids)
    geo = regions_mod.regions_for_replay(store, match["replay_id"])

    my_rounds = {r.get("round"): r for r in (mine.get("per_round") or [])}
    his_rounds = {r.get("round"): r for r in (his.get("per_round") or [])}
    shared = [n for n in my_rounds if n in his_rounds]

    rounds_by_no = {r.get("round"): r for r in (payload.get("rounds") or [])}
    contacts = _blank_contacts()
    my_rows: list[dict] = []
    his_rows: list[dict] = []
    for n in shared:
        rnd = rounds_by_no.get(n) or {"round": n}
        winner = rnd.get("winner")
        if winner is None:
            block["rounds_without_winner"] += 1
        elif winner == my_team:
            block["wins"] += 1
        end = rnd.get("end_ms")
        deaths = rnd.get("deaths") or []
        window_end = float(end) if end is not None else float((deaths[-1].get("t") if deaths else 0) or 0) + AFTER_CAP_MS
        for d in deaths:
            if d.get("victim") == me:
                row = _death_row(d, rnd, me, partner, tracks, pawns, my_team,
                                 my_rounds.get(n) or {}, his_rounds.get(n) or {}, window_end, geo)
                row["match_id"] = match_id
                my_rows.append(row)
            elif d.get("victim") == partner:
                row = _death_row(d, rnd, partner, me, tracks, pawns, my_team,
                                 his_rounds.get(n) or {}, my_rounds.get(n) or {}, window_end, geo)
                row["match_id"] = match_id
                his_rows.append(row)
        if deaths:
            _bump_contacts(contacts, deaths[0], deaths, me, partner)
    block["rounds_together"] = len(shared)
    decided = len(shared) - block["rounds_without_winner"]
    block["losses"] = decided - block["wins"]
    block["win_pct"] = _pct(block["wins"], decided, 1)
    my_shared = [my_rounds[n] for n in shared]
    his_shared = [his_rounds[n] for n in shared]
    block["my_deaths"] = my_rows
    block["his_deaths"] = his_rows
    block["contacts"] = contacts
    block["combined"] = combined_pair(my_shared, his_shared)
    block["_rows"] = {"me": my_shared, "partner": his_shared}
    return block


def _blank_contacts() -> dict[str, int]:
    return {"rounds": 0, "mine_first_blood": 0, "his_first_blood": 0, "mine_first_death": 0,
            "his_first_death": 0, "first_traded_by_me": 0, "first_traded_by_him": 0}


def _bump_contacts(contacts: dict[str, int], first: dict, deaths: list[dict], me: str, partner: str) -> None:
    """Who opened the round: the first death row's killer and victim, each way."""
    contacts["rounds"] += 1
    if first.get("killer") == me:
        contacts["mine_first_blood"] += 1
    if first.get("killer") == partner:
        contacts["his_first_blood"] += 1
    if first.get("victim") == me:
        contacts["mine_first_death"] += 1
    if first.get("victim") == partner:
        contacts["his_first_death"] += 1
    if first.get("traded"):
        if traded_by(deaths, first, me):
            contacts["first_traded_by_me"] += 1
        elif traded_by(deaths, first, partner):
            contacts["first_traded_by_him"] += 1


def pair(store, me: str, partner: str, match_id: str | None = None) -> dict[str, Any]:
    """Every death of mine (and of his) read from the other's side, over the pair's shared rounds.

    Never cached: reads the deduped match set, each match's cached payload, and that match's
    ``movement.parquet`` for the two players' pawns. Unknown subjects and pairs with no shared round
    return a payload with ``pair.matches: 0`` and its reason, never an error.
    """
    started = time.time()
    settings = settings_mod.load()
    matches = [m for m in players_mod._matches(store) if not match_id or m["match_id"] == match_id]
    blocks: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for m in matches:
        try:
            block = _match_block(store, m, me, partner)
        except Exception as exc:  # noqa: BLE001 - one unreadable replay must not kill the pair view
            skipped.append({"replay_id": m["replay_id"], "why": str(exc)[:200]})
            continue
        blocks.append(block)

    my_rows = [r for b in blocks for r in b["my_deaths"]]
    his_rows = [r for b in blocks for r in b["his_deaths"]]
    played = [b for b in blocks if b["rounds_together"]]
    rounds_together = sum(b["rounds_together"] for b in played)
    wins = sum(b["wins"] for b in played)
    without = sum(b["rounds_without_winner"] for b in played)
    decided = rounds_together - without
    losses = decided - wins

    contacts = _blank_contacts()
    for b in played:
        for key, value in b["contacts"].items():
            contacts[key] += value

    combined = combined_pair([], [])
    if played:
        combined = combined_pair(
            [r for b in played for r in b["_rows"]["me"]],
            [r for b in played for r in b["_rows"]["partner"]],
        )

    if not played:
        if match_id:
            why = "no shared round for the two in that match"
        elif skipped and not blocks:
            why = "nothing could be read for this pair"
        else:
            why = "the two share no round in the uploaded matches"
    else:
        why = None

    return {
        "generated_at": int(time.time() * 1000),
        "me": {"subject": me, "label": settings["names"].get(me)},
        "partner": {"subject": partner, "label": settings["names"].get(partner)},
        "pair": {
            "matches": len(played),
            "matches_seen": len(blocks),
            "rounds_together": rounds_together or None,
            "wins": wins,
            "losses": losses,
            "win_pct": _pct(wins, decided, 1),
            "rounds_without_winner": without,
            "why": why,
        },
        "matches": [{
            "match_id": b["match_id"],
            "replay_id": b["replay_id"],
            "level_name": b["level_name"],
            "team": b["team"],
            "rounds_together": b["rounds_together"],
            "rounds_without_winner": b["rounds_without_winner"],
            "wins": b["wins"],
            "losses": b["losses"],
            "win_pct": b["win_pct"],
            "my_deaths": len(b["my_deaths"]),
            "his_deaths": len(b["his_deaths"]),
            "why": b["why"],
        } for b in blocks],
        "trades": {
            "mine": _counts(my_rows),
            "his": _counts(his_rows),
            "rule": f"traded by him = he killed my killer in the same round within {TRADE_WINDOW_MS} ms",
            "range_rule": f"12 m = {int(ISO_DISTANCE_CM)} cm, the isolation rule the death audit publishes",
        },
        "deaths": my_rows,
        "his_deaths": his_rows,
        "contacts": contacts,
        "combined": combined,
        "bands": {b: sum(1 for r in my_rows if r["distance_band"] == b) for b in BANDS},
        "skipped": skipped,
        "seconds": round(time.time() - started, 2),
        "notes": list(NOTES),
        "limits": list(LIMITS),
    }