"""Phase 13: named map regions, taken from the game's own callouts.

Every point gets the name a player would say out loud - "A Main", "Mid Top",
"B Site". The names come from valorant-api.com (cached in data/game/maps.json,
exposed by ``valorant_api.maps()``); nothing is hand-drawn.

Callout world coordinates are projected with the exact transform ``spatial``
already uses for tracks:

    u = y * xMultiplier + xScalarToAdd
    v = x * yMultiplier + yScalarToAdd

The label for a point is the *nearest callout centre*. That is an estimate, not
a boundary: it is 2D (no floors - a Rafters/Window callout sits above the site
below it) and it knows no walls (nearest centre in straight-line image space).
The offset to that centre travels with the name so a reader can see how far off
the label is. ``regions_for_replay`` also returns each callout's Voronoi cell -
the exact set of pixels whose nearest centre is that callout - which is what
"populate the map with regions" means.
"""
from __future__ import annotations

import re
import threading
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

import spatial
import valorant_api

# unit square the Voronoi cells are clipped to
_UNIT = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]

# the export stores the plant location as a "(x, y, z)" string
_VEC_RX = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
_SITE_LETTERS = "ABC"

_regions_lock = threading.RLock()
_regions_cache: dict[tuple, list[dict[str, Any]]] = {}


def _label(name: str, super_: str) -> str:
    if super_ and name and super_.lower() != name.lower():
        return f"{super_} {name}"
    return name or super_ or ""


def _clip(poly: list[tuple[float, float]], ax, ay, bx, by):
    """Keep the part of ``poly`` closer to (ax,ay) than to (bx,by).

    Half-plane cut against the perpendicular bisector; Sutherland-Hodgman.
    """
    nx = bx - ax
    ny = by - ay
    cx = (ax + bx) / 2.0
    cy = (ay + by) / 2.0

    def f(px, py):
        return nx * (px - cx) + ny * (py - cy)

    out: list[tuple[float, float]] = []
    n = len(poly)
    for i in range(n):
        px, py = poly[i]
        qx, qy = poly[(i + 1) % n]
        fp = f(px, py)
        fq = f(qx, qy)
        if fp <= 0:
            out.append((px, py))
        if (fp <= 0) != (fq <= 0):
            t = fp / (fp - fq)
            out.append((px + t * (qx - px), py + t * (qy - py)))
    return out


def _area(poly) -> float:
    n = len(poly)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return abs(s) / 2.0


def _voronoi(regions: list[dict[str, Any]]) -> None:
    """Attach each region's Voronoi cell (clipped to the unit square).

    Callouts sharing a centre collapse to a zero-area cell for all but the
    first - stated, not silently overlapping. Nothing is dropped from the
    label list either way.
    """
    seen: set[tuple[float, float]] = set()
    for r in regions:
        key = (round(r["u"], 6), round(r["v"], 6))
        if key in seen:
            r["poly"] = []
            continue
        seen.add(key)
        poly = list(_UNIT)
        for o in regions:
            if o is r:
                continue
            poly = _clip(poly, r["u"], r["v"], o["u"], o["v"])
            if len(poly) < 3:
                break
        r["poly"] = [[round(u, 5), round(v, 5)] for u, v in poly]


def _regions_for(uuid: str, proj: dict[str, float]) -> list[dict[str, Any]]:
    key = (uuid, proj["xm"], proj["ym"], proj["xsa"], proj["ysa"])
    with _regions_lock:
        hit = _regions_cache.get(key)
        if hit is not None:
            return hit

    m = next((mm for mm in valorant_api.maps() if mm.get("uuid") == uuid), None)
    out: list[dict[str, Any]] = []
    for c in (m or {}).get("callouts") or []:
        loc = c.get("location") or {}
        x = loc.get("x")
        y = loc.get("y")
        if x is None or y is None:
            continue
        name = c.get("regionName") or ""
        sup = c.get("superRegionName") or c.get("superRegion") or ""
        u = y * proj["xm"] + proj["xsa"]
        v = x * proj["ym"] + proj["ysa"]
        out.append(
            {
                "name": name,
                "super": sup,
                "label": _label(name, sup),
                "u": round(u, 5),
                "v": round(v, 5),
                "has_box": bool(c.get("scale3D")),
            }
        )
    if out:
        _voronoi(out)
    with _regions_lock:
        _regions_cache[key] = out
        if len(_regions_cache) > 8:
            _regions_cache.pop(next(iter(_regions_cache)))
    return out


def for_map(uuid: str, proj: dict[str, float]) -> list[dict[str, Any]]:
    """Callouts + Voronoi cells for a resolved map uuid and projection."""
    return _regions_for(uuid, proj)


def regions_for_replay(store, replay_id: str) -> dict[str, Any]:
    """Callouts (with Voronoi cells) for the replay's map, or the empty shape."""
    info, proj = spatial.map_ctx(store, replay_id)
    if info is None or proj is None:
        return {"map": None, "proj": None, "regions": []}
    return {"map": info, "proj": proj, "regions": _regions_for(info["uuid"], proj)}


def cell_area(regions: list[dict[str, Any]]) -> float:
    """Total area covered by the Voronoi cells (1.0 when they tile the square)."""
    return sum(_area([tuple(p) for p in r.get("poly") or []]) for r in regions)


def region_at(proj: dict[str, float] | None, regions, x, y, z=None):
    """Nearest named region for a world point, or None when it is unknown.

    ``x``/``y`` are world centimetres - the same space as the callout locations
    and as the samples ``duo`` reads - so a track sample can be passed straight
    in. Returns ``{name, super, label, cm}``; ``cm`` is how far the point is
    from the callout centre (the label is the closest named place, not a box).
    """
    if not proj or not regions or x is None or y is None:
        return None
    u = y * proj["xm"] + proj["xsa"]
    v = x * proj["ym"] + proj["ysa"]
    best = None
    best_d2 = None
    for r in regions:
        d2 = (r["u"] - u) ** 2 + (r["v"] - v) ** 2
        if best_d2 is None or d2 < best_d2:
            best_d2 = d2
            best = r
    if best is None:
        return None
    dx = (v - best["v"]) / proj["ym"] if proj.get("ym") else 0.0
    dy = (u - best["u"]) / proj["xm"] if proj.get("xm") else 0.0
    cm = (dx * dx + dy * dy) ** 0.5
    return {
        "name": best["name"],
        "super": best["super"],
        "label": best["label"],
        "cm": round(cm, 1),
    }


def region_at_pos(proj, regions, pos):
    """``region_at`` for a ``(x, y, z)`` sample (``None`` sample -> ``None``)."""
    if not pos:
        return None
    return region_at(proj, regions, pos[0], pos[1], pos[2] if len(pos) > 2 else None)


def region_vec(text) -> list[float]:
    """Parse the export's `"(x, y, z)"` vector string; `[]` when unreadable."""
    if not text:
        return []
    return [float(v) for v in _VEC_RX.findall(str(text))]


def site_letter(proj, regions, plant) -> str | None:
    """The A/B/C of a plant: the export's own index, else the callout nearest it.

    `BombPlantedRPC.PlantSite` is left out of many rounds, so the fallback reads
    the plant location - real data, same nearest-callout rule as every other name
    in this module. `None` when neither is readable: never a guessed site.
    """
    if not plant:
        return None
    site = plant.get("site")
    if isinstance(site, int) and 0 <= site < len(_SITE_LETTERS):
        return _SITE_LETTERS[site]
    nums = region_vec(plant.get("location"))
    if len(nums) < 2:
        return None
    got = region_at(proj, regions, nums[0], nums[1])
    letter = ((got or {}).get("super") or "").strip()[:1].upper()
    return letter if letter and letter in _SITE_LETTERS else None


def pawns_by_subject(manifest) -> dict[str, list]:
    """Every pawn guid a subject ever held (a half-time agent swap gives two)."""
    import analysis

    out: dict[str, list] = {}
    for p in analysis.player_table(manifest):
        guids = list(p.get("all_character_net_guids") or [])
        single = p.get("character_net_guid")
        if single and single not in guids:
            guids.append(single)
        if guids:
            out[p["subject"]] = guids
    return out


def time_in_regions(export_dir, proj, regions, guids, step_ms: int = 1000,
                    top: int = 2, skip: tuple[str, ...] = ("spawn",)) -> list[dict[str, Any]]:
    """How long a player spent in each named region, one sample per ``step_ms``.

    Reads the same ``movement.parquet`` table the minimap uses (hidden/parked
    samples already dropped). Returns ``[{label, name, super, s, pct}]`` longest
    first, or ``[]`` when the map or the player is unknown. Time is sample count
    x step: an estimate at one point per second, not a frame-accurate stopwatch.
    ``pct`` is always the share of *all* sampled time; ``skip`` only keeps spawn
    regions (where everyone starts) out of the ranking, so the top names say
    where a player actually played.
    """
    if not proj or not regions or not guids:
        return []
    table = spatial._movement_table(export_dir)
    if table is None or table.num_rows == 0:
        return []
    want = [str(g) for g in guids]
    try:
        mask = pc.is_in(table.column("character_net_guid"), value_set=pa.array(want))
        sub = table.filter(mask)
    except Exception:  # noqa: BLE001
        return []
    if sub.num_rows == 0:
        return []
    t = sub.column("time_ms").to_numpy()
    x = sub.column("pos_x").to_numpy()
    y = sub.column("pos_y").to_numpy()
    g = sub.column("character_net_guid").to_numpy(zero_copy_only=False)
    step = max(int(step_ms), 1)
    bucket = (t // step).astype("int64")
    codes = {w: i for i, w in enumerate(want)}
    gi = np.fromiter((codes.get(str(v), 0) for v in g), dtype="int64", count=sub.num_rows)
    key = bucket * (len(codes) + 1) + gi
    _, idx = np.unique(key, return_index=True)
    u = y[idx] * proj["xm"] + proj["xsa"]
    v = x[idx] * proj["ym"] + proj["ysa"]
    ru = np.array([r["u"] for r in regions])
    rv = np.array([r["v"] for r in regions])
    d2 = (ru[:, None] - u[None, :]) ** 2 + (rv[:, None] - v[None, :]) ** 2
    pick = d2.argmin(axis=0)
    n = int(pick.size)
    out: list[dict[str, Any]] = []
    for i, r in enumerate(regions):
        label = r["label"] or ""
        if any(s in label.lower() for s in skip):
            continue
        c = int((pick == i).sum())
        if c:
            out.append(
                {
                    "label": r["label"],
                    "name": r["name"],
                    "super": r["super"],
                    "s": round(c * step / 1000.0, 1),
                    "pct": round(100.0 * c / n, 1),
                }
            )
    out.sort(key=lambda r: -r["s"])
    return out[:top] if top else out