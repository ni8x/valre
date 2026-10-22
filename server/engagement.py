"""Phase B — engagement model + aim / movement / duel metrics (all PROXIES).

Everything in this module is an approximation derived from
``movement.parquet`` (position, yaw, pitch, velocity at ~128 Hz) plus the
Phase A shots/hits/sprays. Replays carry no visibility data (the
``VisibleCharacterOverrides`` blob is undecoded), so "enemy in view" is
approximated as:

    enemy alive AND within NEAR_DIST_CM AND within FRONT_ANGLE_DEG of aim

Constants are documented below; every metric produced here is marked ``~``
(approximation) in the UI.

Definitions (mirroring OP.GG where possible):
- engagement: contiguous window where an enemy pawn is "in view"; windows for
  the same enemy with gaps < MERGE_GAP_MS are merged (proxy for one fight).
- pre_aim_deg: angle between aim direction and the vector to the enemy HEAD
  100 ms before the first shot (engagements with a shot only; median).
- pre_aim_complete: share of those with pre_aim_deg <= 3 deg.
- head_level_pct: share of in-view samples whose VERTICAL aim error to the
  enemy head <= 3 deg (crosshair height; yaw ignored).
- reaction_ms: imminent-window start -> first shot fired (median).
- ttd_ms: imminence -> first damage dealt to that enemy (median).
- counter_strafe_pct: share of shots fired at speed <= STOP_SPEED_CMS.
- first_eng_acc: of engagements with a first shot, share that landed a hit
  within FIRST_HIT_MS of that shot.
- spray geometry: avg_radius_cm (impacts vs spray centroid), spread_deg
  (deviation of impact-origin vectors from the mean vector, first 4 hits),
  spray_ttk_ms (median kill time - spray start).
- hs_kill_pct: kills whose final blow was a headshot / all gun kills.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

HIDDEN = -20000.0
NEAR_DIST_CM = 3000.0
FRONT_ANGLE_DEG = 25.0
HEAD_LEVEL_DEG = 1.2
HEAD_DIST_CM = 2000.0
IMMINENT_DIST_CM = 1100.0
IMMINENT_ANGLE_DEG = 16.0
MERGE_GAP_MS = 1000
PRE_AIM_OK_DEG = 3.0
PRE_SHOT_MS = 100
STOP_SPEED_CMS = 100.0
FIRST_HIT_MS = 200
WINDOW_PAD_MS = 500
EYE_HEIGHT_CM = 65.0
SPRAY_PAD_MS = 300

_MOV_COLUMNS = [
    "time_ms",
    "character_net_guid",
    "pos_x",
    "pos_y",
    "pos_z",
    "yaw",
    "pitch",
    "vel_x",
    "vel_y",
    "vel_z",
]


def _median(vals: list[float]) -> float | None:
    clean = [v for v in vals if v is not None and math.isfinite(v)]
    if not clean:
        return None
    return round(float(np.median(np.asarray(clean, dtype=np.float64))), 2)


def _ratio(part: int, total: int) -> float | None:
    if not total:
        return None
    return round(part / total, 4)


def _load_movement(export_dir: Path) -> dict[int, dict[str, np.ndarray]]:
    """movement.parquet -> {pawn_guid: {t, x, y, z, yaw, pitch, speed}}.

    Hidden / parked actors (x or y <= HIDDEN) are dropped. Pitch is wrapped to
    [-180, 180]; speed is the horizontal velocity magnitude in cm/s.
    """
    tbl = pq.read_table(export_dir / "movement.parquet", columns=_MOV_COLUMNS)
    t = tbl.column("time_ms").to_numpy(zero_copy_only=False).astype(np.int64)
    g = tbl.column("character_net_guid").to_numpy(zero_copy_only=False).astype(np.int64)
    x = tbl.column("pos_x").to_numpy(zero_copy_only=False).astype(np.float64)
    y = tbl.column("pos_y").to_numpy(zero_copy_only=False).astype(np.float64)
    z = tbl.column("pos_z").to_numpy(zero_copy_only=False).astype(np.float64)
    yaw = tbl.column("yaw").to_numpy(zero_copy_only=False).astype(np.float64)
    pitch = tbl.column("pitch").to_numpy(zero_copy_only=False).astype(np.float64)
    vx = tbl.column("vel_x").to_numpy(zero_copy_only=False).astype(np.float64)
    vy = tbl.column("vel_y").to_numpy(zero_copy_only=False).astype(np.float64)
    keep = (
        np.isfinite(x)
        & np.isfinite(y)
        & np.isfinite(z)
        & (x > HIDDEN)
        & (y > HIDDEN)
        & (g > 0)
    )
    t, g, x, y, z, yaw, pitch, vx, vy = (
        t[keep],
        g[keep],
        x[keep],
        y[keep],
        z[keep],
        yaw[keep],
        pitch[keep],
        vx[keep],
        vy[keep],
    )
    pitch = np.where(pitch > 180.0, pitch - 360.0, pitch)
    speed = np.hypot(vx, vy)
    order = np.lexsort((t, g))
    t, g, x, y, z, yaw, pitch, speed = (
        t[order],
        g[order],
        x[order],
        y[order],
        z[order],
        yaw[order],
        pitch[order],
        speed[order],
    )
    bounds = np.flatnonzero(np.diff(g)) + 1
    starts = np.concatenate(([0], bounds))
    ends = np.concatenate((bounds, [len(g)]))
    out: dict[int, dict[str, np.ndarray]] = {}
    for a, b in zip(starts, ends):
        if b - a < 2:
            continue
        out[int(g[a])] = {
            "t": t[a:b],
            "x": x[a:b],
            "y": y[a:b],
            "z": z[a:b],
            "yaw": yaw[a:b],
            "pitch": pitch[a:b],
            "speed": speed[a:b],
        }
    return out


def _round_bounds(rounds: dict[int, dict], t: Any) -> tuple[int | None, int | None]:
    if t is None:
        return None, None
    for rn in sorted(rounds):
        info = rounds[rn]
        s = info.get("start_ms")
        e = info.get("end_ms")
        if s is None or t < s:
            continue
        if e is None or t <= e:
            return s, e
    return None, None


def _dead_windows(pawn: int, deaths: list[dict], rounds: dict[int, dict]) -> list[tuple[int, int]]:
    """[(death_ms, alive_again_ms)] — a player stays dead until round end."""
    out: list[tuple[int, int]] = []
    for d in deaths:
        if d.get("victim_char") != pawn:
            continue
        t = d.get("t")
        if t is None:
            continue
        s, e = _round_bounds(rounds, t)
        if e is None:
            e = int(t) + 30000
        out.append((int(t), int(e)))
    out.sort()
    return out


def _dead_mask(times: np.ndarray, windows: list[tuple[int, int]]) -> np.ndarray:
    mask = np.zeros(len(times), dtype=bool)
    if not windows:
        return mask
    starts = np.asarray([w[0] for w in windows], dtype=np.int64)
    ends = np.asarray([w[1] for w in windows], dtype=np.int64)
    k = np.searchsorted(starts, times, side="right") - 1
    valid = k >= 0
    idx = np.clip(k, 0, len(starts) - 1)
    mask[valid] = times[valid] <= ends[idx[valid]]
    return mask


def _state_at(a: dict[str, np.ndarray], t: float) -> dict[str, float] | None:
    ta = a["t"]
    if not len(ta):
        return None
    i = int(np.searchsorted(ta, t, side="right")) - 1
    if i < 0:
        return None
    return {
        "x": float(a["x"][i]),
        "y": float(a["y"][i]),
        "z": float(a["z"][i]),
        "yaw": float(a["yaw"][i]),
        "pitch": float(a["pitch"][i]),
        "speed": float(a["speed"][i]),
    }


def _angle_to(px: float, py: float, pz: float, yaw: float, pitch: float,
              tx: float, ty: float, tz: float) -> float | None:
    dx, dy, dz = tx - px, ty - py, tz - pz
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    if dist <= 1e-6:
        return None
    yr = math.radians(yaw)
    pr = math.radians(pitch)
    cp = math.cos(pr)
    ax, ay, az = cp * math.cos(yr), cp * math.sin(yr), math.sin(pr)
    dot = (dx * ax + dy * ay + dz * az) / dist
    return math.degrees(math.acos(max(-1.0, min(1.0, dot))))


def _first_in(lst: list[dict], lo: float, hi: float) -> dict | None:
    keys = [x["t"] for x in lst]
    i = int(np.searchsorted(np.asarray(keys, dtype=np.int64), lo, side="left"))
    if i < len(lst) and lst[i]["t"] <= hi:
        return lst[i]
    return None


def _spray_geometry(subject: str, spray: dict, hit_list: list[dict]) -> dict[str, float] | None:
    """Impacts of ``hit_list`` inside the spray window -> centroid metrics."""
    lo = (spray.get("start") or 0) - 150
    hi = (spray.get("end") or 0) + SPRAY_PAD_MS
    pts: list[tuple[float, float, float]] = []
    vecs: list[tuple[float, float, float]] = []
    kill_t: float | None = None
    for h in hit_list:
        t = h.get("t")
        if t is None or t < lo or t > hi:
            continue
        imp = h.get("impact")
        if isinstance(imp, (list, tuple)) and len(imp) == 3 and all(v is not None for v in imp):
            pts.append((float(imp[0]), float(imp[1]), float(imp[2])))
        org = h.get("origin")
        if (
            isinstance(org, (list, tuple))
            and len(org) == 3
            and all(v is not None for v in org)
            and isinstance(imp, (list, tuple))
            and len(imp) == 3
            and all(v is not None for v in imp)
        ):
            vecs.append(
                (float(imp[0]) - float(org[0]), float(imp[1]) - float(org[1]), float(imp[2]) - float(org[2]))
            )
        if h.get("killed") and kill_t is None:
            kill_t = float(t)
    if len(pts) < 2:
        return None
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    cz = sum(p[2] for p in pts) / len(pts)
    radii = [math.dist(p, (cx, cy, cz)) for p in pts]
    radius_rms = float(np.sqrt(np.mean(np.square(radii))))
    spread = None
    if len(vecs) >= 2:
        v0 = vecs[0]
        l0 = math.sqrt(v0[0] ** 2 + v0[1] ** 2 + v0[2] ** 2) or 1.0
        devs = []
        for v in vecs[1:4]:
            vlen = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2) or 1.0
            dot = (v[0] * v0[0] + v[1] * v0[1] + v[2] * v0[2]) / (vlen * l0)
            devs.append(math.degrees(math.acos(max(-1.0, min(1.0, dot)))))
        spread = float(np.mean(devs))
    ttk = None
    if kill_t is not None and spray.get("start") is not None:
        ttk = kill_t - float(spray["start"])
    return {
        "radius": radius_rms,
        "spread": spread,
        "ttk": ttk,
    }


def analyze(
    export_dir: Path,
    players: list[dict],
    char2subj: dict[int, str],
    teams: dict[str, str | None],
    rounds: dict[int, dict],
    deaths: list[dict],
    hits: list[dict],
    shots: list[dict],
    sprays: list[dict],
) -> tuple[dict[str, dict], dict[str, Any]]:
    """Return ({subject: metric_updates}, meta)."""
    try:
        mov = _load_movement(export_dir)
    except Exception:
        return {}, {"error": "movement load failed"}

    pawn_of: dict[str, int] = {}
    for p in players:
        pawn = p.get("character_net_guid")
        if pawn:
            pawn_of[p["subject"]] = int(pawn)
    subj_of_pawn: dict[int, str] = {}
    for pawn, subj in char2subj.items():
        subj_of_pawn.setdefault(int(pawn), subj)

    shots_by: dict[str, list[dict]] = {}
    for s in shots:
        if s.get("p") and s.get("t") is not None:
            shots_by.setdefault(s["p"], []).append(s)
    for lst in shots_by.values():
        lst.sort(key=lambda s: s["t"])

    hits_by: dict[str, list[dict]] = {}
    for h in hits:
        if h.get("shooter") and h.get("gun") and h.get("t") is not None:
            hits_by.setdefault(h["shooter"], []).append(h)
    for lst in hits_by.values():
        lst.sort(key=lambda h: h["t"])

    # eye-height validation: head-hit impact z minus victim position z
    eye_offsets: list[float] = []
    head_hits = 0
    for h in hits:
        if str(h.get("bone") or "").lower() != "head":
            continue
        head_hits += 1
        imp = h.get("impact")
        victim = h.get("victim")
        if not isinstance(imp, (list, tuple)) or len(imp) != 3 or imp[2] is None:
            continue
        b = mov.get(int(victim)) if victim else None
        if not b:
            continue
        st = _state_at(b, float(h["t"]))
        if st:
            eye_offsets.append(float(imp[2]) - st["z"])

    out: dict[str, dict] = {}
    total_engagements = 0

    for subject, pawn in pawn_of.items():
        a = mov.get(pawn)
        if not a:
            continue
        team = teams.get(subject)
        a_dead = _dead_mask(a["t"], _dead_windows(pawn, deaths, rounds))
        my_shots = shots_by.get(subject, [])
        my_hits = hits_by.get(subject, [])

        # ---------------- engagements ----------------
        engagements: list[tuple[int, int, int]] = []  # start, end, enemy_pawn
        im_all: list[tuple[int, int, int]] = []
        near_total = 0
        near_head_ok = 0
        for other_pawn, b in mov.items():
            if other_pawn == pawn:
                continue
            other_subject = subj_of_pawn.get(other_pawn)
            if other_subject is None:
                continue
            if team and teams.get(other_subject) == team:
                continue
            tb = b["t"]
            idx = np.searchsorted(a["t"], tb, side="right") - 1
            ok = idx >= 0
            if not ok.any():
                continue
            aidx = np.clip(idx, 0, len(a["t"]) - 1)
            ax, ay, az = a["x"][aidx], a["y"][aidx], a["z"][aidx]
            ayaw, apitch = a["yaw"][aidx], a["pitch"][aidx]
            dx, dy = b["x"] - ax, b["y"] - ay
            dz, dz_head = b["z"] - az, (b["z"] + EYE_HEIGHT_CM) - az
            dist = np.sqrt(dx * dx + dy * dy + dz * dz)
            dist = np.maximum(dist, 1e-6)
            yr = np.radians(ayaw)
            pr = np.radians(apitch)
            cp = np.cos(pr)
            ux, uy, uz = cp * np.cos(yr), cp * np.sin(yr), np.sin(pr)
            dot = (dx * ux + dy * uy + dz * uz) / dist
            ang = np.degrees(np.arccos(np.clip(dot, -1.0, 1.0)))
            dist_xy = np.maximum(np.sqrt(dx * dx + dy * dy), 1e-6)
            head_elev_diff = np.abs(np.degrees(np.arctan2(dz_head, dist_xy)) - apitch)
            b_dead = _dead_mask(tb, _dead_windows(other_pawn, deaths, rounds))
            alive = (~a_dead[aidx]) & (~b_dead) & ok
            near = alive & (dist <= NEAR_DIST_CM) & (ang <= FRONT_ANGLE_DEG)
            view = alive & (dist <= HEAD_DIST_CM) & (ang <= FRONT_ANGLE_DEG)
            near_total += int(view.sum())
            near_head_ok += int((view & (head_elev_diff <= HEAD_LEVEL_DEG)).sum())
            short = alive & (dist <= IMMINENT_DIST_CM) & (ang <= IMMINENT_ANGLE_DEG)
            if short.any():
                f2 = short.astype(np.int8)
                d2 = np.diff(f2)
                s2 = list(np.flatnonzero(d2 == 1) + 1)
                e2 = list(np.flatnonzero(d2 == -1))
                if f2[0]:
                    s2.insert(0, 0)
                if f2[-1]:
                    e2.append(len(f2) - 1)
                ms: list[int] = []
                me: list[int] = []
                for si, ei in zip(s2, e2):
                    x0, x1 = int(tb[si]), int(tb[ei])
                    if me and x0 - me[-1] < MERGE_GAP_MS:
                        me[-1] = x1
                    else:
                        ms.append(x0)
                        me.append(x1)
                im_all.extend((x0, x1, other_pawn) for x0, x1 in zip(ms, me))
            if not near.any():
                continue
            flags = near.astype(np.int8)
            diff = np.diff(flags)
            s_idx = list(np.flatnonzero(diff == 1) + 1)
            e_idx = list(np.flatnonzero(diff == -1))
            if flags[0]:
                s_idx.insert(0, 0)
            if flags[-1]:
                e_idx.append(len(flags) - 1)
            merged_s: list[int] = []
            merged_e: list[int] = []
            for si, ei in zip(s_idx, e_idx):
                t0, t1 = int(tb[si]), int(tb[ei])
                if merged_e and t0 - merged_e[-1] < MERGE_GAP_MS:
                    merged_e[-1] = t1
                else:
                    merged_s.append(t0)
                    merged_e.append(t1)
            engagements.extend((t0, t1, other_pawn) for t0, t1 in zip(merged_s, merged_e))

        engagements.sort()
        total_engagements += len(engagements)
        im_by_pawn: dict[int, list[tuple[int, int]]] = {}
        for x0, x1, ep in sorted(im_all):
            im_by_pawn.setdefault(ep, []).append((x0, x1))

        # ---------------- per-engagement metrics ----------------
        pre_aims: list[float] = []
        pre_aim_ok = 0
        reaction: list[float] = []
        ttd: list[float] = []
        first_shot_n = 0
        first_hit_n = 0
        for (t0, t1, ep) in engagements:
            b = mov.get(ep)
            enemy_subject = subj_of_pawn.get(ep)
            shot = _first_in(my_shots, t0, t1 + WINDOW_PAD_MS)
            if b is not None and shot is not None:
                aim_t = max(t0, int(shot["t"]) - PRE_SHOT_MS)
                bst = _state_at(b, aim_t)
                ast = _state_at(a, aim_t)
                if bst and ast:
                    deg = _angle_to(
                        ast["x"], ast["y"], ast["z"], ast["yaw"], ast["pitch"],
                        bst["x"], bst["y"], bst["z"] + EYE_HEIGHT_CM,
                    )
                    if deg is not None:
                        pre_aims.append(deg)
                        if deg <= PRE_AIM_OK_DEG:
                            pre_aim_ok += 1
            if shot:
                first_shot_n += 1
                t_shot = float(shot["t"])
                for (x0, x1) in im_by_pawn.get(ep, []):
                    if x0 <= t_shot <= x1 + WINDOW_PAD_MS:
                        reaction.append(t_shot - x0)
                        break
                hit = None
                for h in my_hits:
                    if h["t"] < t_shot:
                        continue
                    if h["t"] > t_shot + FIRST_HIT_MS:
                        break
                    if h.get("victim_subject") == enemy_subject:
                        hit = h
                        break
                if hit:
                    first_hit_n += 1
            if enemy_subject:
                for h in my_hits:
                    if h["t"] < t0:
                        continue
                    if h["t"] > t1 + WINDOW_PAD_MS:
                        break
                    if h.get("victim_subject") == enemy_subject:
                        for (x0, x1) in im_by_pawn.get(ep, []):
                            if x0 <= h["t"] <= x1 + WINDOW_PAD_MS:
                                ttd.append(float(h["t"]) - max(x0, t0))
                                break
                        break

        # ---------------- counter-strafe ----------------
        stopped = 0
        classified = 0
        for s in my_shots:
            st = _state_at(a, float(s["t"]))
            if not st:
                continue
            classified += 1
            if st["speed"] <= STOP_SPEED_CMS:
                stopped += 1

        # ---------------- spray geometry ----------------
        radii: list[float] = []
        spreads: list[float] = []
        ttks: list[float] = []
        for sp in sprays:
            if sp.get("p") != subject:
                continue
            g = _spray_geometry(subject, sp, my_hits)
            if not g:
                continue
            radii.append(g["radius"])
            if g["spread"] is not None:
                spreads.append(g["spread"])
            if g["ttk"] is not None:
                ttks.append(g["ttk"])

        # ---------------- headshot kills ----------------
        gun_kills = 0
        hs_kills = 0
        for h in my_hits:
            if h.get("killed") and h.get("victim_subject"):
                gun_kills += 1
                if str(h.get("bone") or "").lower() == "head":
                    hs_kills += 1

        out[subject] = {
            "engagements": len(engagements),
            "pre_aim_deg": _median(pre_aims),
            "pre_aim_n": len(pre_aims),
            "pre_aim_complete": _ratio(pre_aim_ok, len(pre_aims)),
            "head_level_pct": _ratio(near_head_ok, near_total),
            "near_samples": near_total,
            "reaction_ms": _median(reaction),
            "reaction_n": len(reaction),
            "ttd_ms": _median(ttd),
            "ttd_n": len(ttd),
            "counter_strafe_pct": _ratio(stopped, classified),
            "shots_classified": classified,
            "first_eng_n": first_shot_n,
            "first_eng_acc": _ratio(first_hit_n, first_shot_n),
            "spray_radius_cm": _median(radii),
            "spray_spread_deg": _median(spreads),
            "spray_ttk_ms": _median(ttks),
            "hs_kill_pct": _ratio(hs_kills, gun_kills),
        }

    meta = {
        "eye_offset_cm": _median(eye_offsets),
        "head_hits_sampled": head_hits,
        "engagements_total": total_engagements,
    }
    return out, meta
