"""Phase C: spatial layer — 2D tracks, heatmaps, kill markers.

Projection (valorant-api map constants, normalized 0..1 of the displayIcon):
    u = pos_y * xMultiplier + xScalarToAdd
    v = pos_x * yMultiplier + yScalarToAdd

Facing in image space: du ~ sin(yaw) * xMultiplier, dv ~ cos(yaw) * yMultiplier
(yaw degrees; world dir = (cos(yaw), sin(yaw)) on the XY plane).
"""
from __future__ import annotations

import threading
from typing import Any

import pyarrow.compute as pc
import pyarrow.parquet as pq

import analysis
import regions
import stats as stats_mod
import valorant_api

HIDDEN = -20000.0
DOWNSAMPLE_MS = 160
GRID = 64  # heatmap resolution in normalized space

_move_lock = threading.RLock()
_move_cache: dict[str, tuple[float, Any]] = {}


def _movement_table(export_dir):
    key = str(export_dir)
    path = export_dir / "movement.parquet"
    mtime = path.stat().st_mtime
    with _move_lock:
        hit = _move_cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
    t = pq.read_table(
        path,
        columns=[
            "time_ms",
            "character_net_guid",
            "pos_x",
            "pos_y",
            "yaw",
            "vel_x",
            "vel_y",
            "vel_z",
        ],
    )
    x = t.column("pos_x")
    y = t.column("pos_y")
    mask = pc.and_(
        pc.and_(pc.is_valid(x), pc.is_valid(y)),
        pc.and_(pc.greater(x, HIDDEN), pc.greater(y, HIDDEN)),
    )
    t = t.filter(mask)
    with _move_lock:
        _move_cache[key] = (mtime, t)
        if len(_move_cache) > 4:
            _move_cache.pop(next(iter(_move_cache)))
    return t


def map_ctx(store, replay_id: str):
    """(map_info, projection_dict) for a replay, or (None, None)."""
    rec = store.get(replay_id) or {}
    summary = rec.get("summary") or {}
    level = summary.get("level_name")
    if not level:
        return None, None
    m = valorant_api.map_for_level(level)
    if not m or m.get("xMultiplier") is None:
        return None, None
    proj = {
        "xm": m["xMultiplier"],
        "ym": m["yMultiplier"],
        "xsa": m["xScalarToAdd"],
        "ysa": m["yScalarToAdd"],
    }
    info = {
        "uuid": m["uuid"],
        "name": m["name"],
        "image": f"/api/assets/map/{m['uuid']}_display.png",
    }
    return info, proj


def _players(store, replay_id: str):
    """[(subject, pawn, team)] from stats."""
    st = stats_mod.build(store, replay_id)
    out = []
    for p in st.get("players", []):
        subj = p.get("subject")
        pawn = p.get("character_net_guid")
        if subj and pawn:
            out.append((subj, int(pawn), p.get("team")))
    return out


def _window(st, round_no):
    for r in st.get("rounds", []):
        if r.get("round") == round_no:
            start = r.get("start_ms")
            end = r.get("end_ms")
            if end is None:
                end = start + 240_000
            return start, end
    return None, None


def tracks(store, replay_id: str, round_no: int | None) -> dict[str, Any]:
    export_dir = store.export_dir(replay_id)
    st = stats_mod.build(store, replay_id)
    info, proj = map_ctx(store, replay_id)
    if info is None:
        return {"map": None, "proj": None, "regions": [], "tracks": [], "kills": [], "note": "map not resolved"}
    table = _movement_table(export_dir)
    tcol = table.column("time_ms").to_numpy()
    gcol = table.column("character_net_guid").to_numpy()
    xcol = table.column("pos_x").to_numpy()
    ycol = table.column("pos_y").to_numpy()
    yawcol = table.column("yaw").to_numpy()
    vx = table.column("vel_x").to_numpy()
    vy = table.column("vel_y").to_numpy()
    vz = table.column("vel_z").to_numpy()

    idx_lo, idx_hi = 0, len(tcol)
    start = end = None
    if round_no:
        start, end = _window(st, round_no)
        if start is None:
            return {
                "map": info,
                "proj": proj,
                "regions": regions.for_map(info["uuid"], proj),
                "tracks": [],
                "kills": [],
                "note": "unknown round",
            }
        import numpy as np

        idx_lo = int(np.searchsorted(tcol, start, "left"))
        idx_hi = int(np.searchsorted(tcol, end, "right"))

    movers = [p for p in _players(store, replay_id)]
    by_pawn: dict[int, list[list[float]]] = {}
    last_kept: dict[int, int] = {}
    rng = range(idx_lo, idx_hi)
    for i in rng:
        pawn = int(gcol[i])
        last = last_kept.get(pawn)
        t = int(tcol[i])
        if last is not None and t - last < DOWNSAMPLE_MS:
            continue
        last_kept[pawn] = t
        x = float(xcol[i])
        y = float(ycol[i])
        u = y * proj["xm"] + proj["xsa"]
        v = x * proj["ym"] + proj["ysa"]
        speed = (float(vx[i]) ** 2 + float(vy[i]) ** 2 + float(vz[i]) ** 2) ** 0.5
        by_pawn.setdefault(pawn, []).append(
            [t, round(u, 5), round(v, 5), round(float(yawcol[i]), 2), round(speed, 1)]
        )

    out_tracks = []
    for subj, pawn, team in movers:
        pts = by_pawn.get(pawn)
        if not pts:
            continue
        out_tracks.append({"subject": subj, "pawn": pawn, "team": team, "points": pts})

    kills: list[dict[str, Any]] = []
    try:
        import metrics as metrics_mod

        md = metrics_mod.build(store, replay_id)
        for h in md.get("hits", []):
            if not h.get("killed"):
                continue
            if round_no and h.get("r") != round_no:
                continue
            impact = h.get("impact") or h.get("origin")
            if not impact:
                continue
            x, y = float(impact[0]), float(impact[1])
            if x < HIDDEN or y < HIDDEN:
                continue
            kills.append(
                {
                    "t": h.get("t"),
                    "r": h.get("r"),
                    "u": round(y * proj["xm"] + proj["xsa"], 5),
                    "v": round(x * proj["ym"] + proj["ysa"], 5),
                    "shooter": h.get("shooter"),
                    "victim": h.get("victim_subject"),
                    "weapon": h.get("weapon"),
                }
            )
    except Exception:
        pass

    return {
        "map": info,
        "proj": proj,
        "regions": regions.for_map(info["uuid"], proj),
        "round": round_no,
        "start_ms": start,
        "end_ms": end,
        "rounds": [
            {"round": r.get("round"), "start_ms": r.get("start_ms"), "end_ms": r.get("end_ms")}
            for r in st.get("rounds", [])
        ],
        "tracks": out_tracks,
        "kills": kills,
    }


def heatmap(
    store,
    replay_id: str,
    player: str | None = None,
    team: str | None = None,
    round_no: int | None = None,
    mode: str = "all",
    speed_threshold: float = 60.0,
) -> dict[str, Any]:
    export_dir = store.export_dir(replay_id)
    st = stats_mod.build(store, replay_id)
    info, proj = map_ctx(store, replay_id)
    if info is None:
        return {"map": None, "proj": None, "regions": [], "cells": []}
    players = _players(store, replay_id)
    want: dict[int, str] = {}
    for subj, pawn, pteam in players:
        if player and subj != player:
            continue
        if team and (pteam or "").lower() != team.lower():
            continue
        want[pawn] = subj
    if not want:
        return {
            "map": info,
            "proj": proj,
            "regions": regions.for_map(info["uuid"], proj),
            "cells": [],
            "note": "no players matched",
        }

    table = _movement_table(export_dir)
    import numpy as np

    tcol = table.column("time_ms").to_numpy()
    gcol = table.column("character_net_guid").to_numpy()
    xcol = table.column("pos_x").to_numpy()
    ycol = table.column("pos_y").to_numpy()
    vx = table.column("vel_x").to_numpy()
    vy = table.column("vel_y").to_numpy()
    vz = table.column("vel_z").to_numpy()

    lo, hi = 0, len(tcol)
    if round_no:
        start, end = _window(st, round_no)
        if start is None:
            return {
                "map": info,
                "proj": proj,
                "regions": regions.for_map(info["uuid"], proj),
                "cells": [],
            }
        lo = int(np.searchsorted(tcol, start, "left"))
        hi = int(np.searchsorted(tcol, end, "right"))

    counts = np.zeros((GRID + 1, GRID + 1), dtype=np.int32)
    total = 0
    for i in range(lo, hi):
        pawn = int(gcol[i])
        if pawn not in want:
            continue
        if mode == "move" or mode == "stay":
            sp = (
                float(vx[i]) ** 2 + float(vy[i]) ** 2 + float(vz[i]) ** 2
            ) ** 0.5
            moving = sp > speed_threshold
            if mode == "move" and not moving:
                continue
            if mode == "stay" and moving:
                continue
        u = float(ycol[i]) * proj["xm"] + proj["xsa"]
        v = float(xcol[i]) * proj["ym"] + proj["ysa"]
        iu = int(u * GRID)
        iv = int(v * GRID)
        if 0 <= iu <= GRID and 0 <= iv <= GRID:
            counts[iv, iu] += 1
            total += 1

    cells = []
    for j in range(GRID + 1):
        for i in range(GRID + 1):
            c = int(counts[j, i])
            if c:
                cells.append([round(i / GRID, 5), round(j / GRID, 5), c])
    return {
        "map": info,
        "proj": proj,
        "regions": regions.for_map(info["uuid"], proj),
        "grid": GRID,
        "cells": cells,
        "total": total,
        "max": int(counts.max()) if total else 0,
    }
