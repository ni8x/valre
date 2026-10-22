"""Named map regions: the game's own callout names, and the rules they obey.

Nothing here fabricates replay data. The pure tests feed callout/plant shapes and assert the
documented rules; the real tests read the exports and the payloads the engines already build, never
write one, and skip themselves when a replay or a probed subject is not on this machine.

The anchor is the plant-site regression: the export records the planted site index on many rounds and
the nearest callout must agree with it (`0=A, 1=B, 2=C`). That check is what caught the projection.
"""

import pytest

import regions

# the same pair the duo tests probed
ME = "e703e25c-bdf8-5484-9449-a78d85996d46"
MATE = "0ce13fa3-fd35-5d8c-8f83-a4d8cd262041"

MIN_CALLOUTS = 11


def _world(proj, u, v):
    """Invert the projection: unit space back to world centimetres."""
    return (v - proj["ysa"]) / proj["ym"], (u - proj["xsa"]) / proj["xm"]


def _inside(poly, u, v):
    """Ray-cast point-in-polygon over a cell that may be empty."""
    n = len(poly)
    if n < 3:
        return False
    hit = False
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if (y0 > v) != (y1 > v):
            if u < (x1 - x0) * (v - y0) / (y1 - y0) + x0:
                hit = not hit
    return hit


def _maps_with_callouts(store, all_replays):
    out = {}
    for rid in all_replays:
        geo = regions.regions_for_replay(store, rid)
        if geo["map"] is None or not geo["regions"]:
            continue
        out[geo["map"]["uuid"]] = geo
    return out


# ---- the names come free, and land on the picture ---------------------------


def test_every_map_resolves_callouts_inside_the_picture(store, all_replays):
    got = _maps_with_callouts(store, all_replays)
    assert got, "no replay resolved a map with callouts"
    for uuid, geo in got.items():
        assert len(geo["regions"]) >= MIN_CALLOUTS, uuid
        for r in geo["regions"]:
            assert 0.0 <= r["u"] <= 1.0, (uuid, r)
            assert 0.0 <= r["v"] <= 1.0, (uuid, r)
            assert r["label"], (uuid, r)
            assert r["name"] in r["label"] or r["super"] in r["label"]


def test_cells_tile_the_unit_square(store, all_replays):
    got = _maps_with_callouts(store, all_replays)
    assert got
    for uuid, geo in got.items():
        regs = geo["regions"]
        assert regions.cell_area(regs) == pytest.approx(1.0, abs=1e-4), uuid
        # a grid point is in exactly one cell - no gaps, no overlaps
        for i in range(1, 20):
            for j in range(1, 20):
                u = i / 20.0
                v = j / 20.0
                n = sum(1 for r in regs if _inside(r["poly"], u, v))
                assert n == 1, (uuid, u, v, n)


def test_region_at_returns_the_callouts_own_centre(store, all_replays):
    got = _maps_with_callouts(store, all_replays)
    assert got
    for uuid, geo in got.items():
        proj = geo["proj"]
        seen = set()
        bad = 0
        for r in geo["regions"]:
            key = (r["u"], r["v"])
            if key in seen:  # duplicate centre: both keep the label, one keeps the cell
                continue
            seen.add(key)
            x, y = _world(proj, r["u"], r["v"])
            hit = regions.region_at(proj, geo["regions"], x, y)
            if not hit or hit["label"] != r["label"] or hit["cm"] > 0.5:
                bad += 1
        assert bad == 0, (uuid, bad)


# ---- the anchor: the export's own site index --------------------------------


@pytest.mark.slow
def test_plant_site_agrees_with_the_nearest_callout(store, all_replays):
    import judge

    checked = 0
    wrong = []
    for rid in all_replays:
        geo = regions.regions_for_replay(store, rid)
        if geo["map"] is None or not geo["regions"]:
            continue
        payload = judge.build(store, rid)
        assert "plants" in payload
        for rn, plant in (payload.get("plants") or {}).items():
            if not isinstance(plant, dict):
                continue
            site = plant.get("site")
            if not isinstance(site, int) or site not in (0, 1, 2):
                continue
            checked += 1
            letter = regions.site_letter(geo["proj"], geo["regions"], plant)
            if letter != "ABC"[site]:
                wrong.append((rid, rn, site, letter, plant.get("location")))
    assert checked >= 40, f"only {checked} plants carried a site index"
    assert wrong == [], wrong


def test_site_letter_prefers_the_index_and_never_guesses():
    assert regions.site_letter(None, [], {"site": 0}) == "A"
    assert regions.site_letter(None, [], {"site": 1}) == "B"
    assert regions.site_letter(None, [], {"site": 2}) == "C"
    assert regions.site_letter(None, [], {"site": 7}) is None
    assert regions.site_letter(None, [], {"site": None, "location": "(1,2,3)"}) is None
    assert regions.site_letter(None, [], {"site": None, "location": "nonsense"}) is None
    assert regions.site_letter(None, [], {}) is None
    assert regions.site_letter(None, [], None) is None


# ---- unknown stays unknown --------------------------------------------------


def test_unknown_point_and_map_stay_empty(store):
    empty = regions.regions_for_replay(store, "no-such-replay")
    assert empty == {"map": None, "proj": None, "regions": []}
    assert regions.region_at(None, [], 1.0, 2.0) is None
    assert regions.region_at({"xm": 1, "ym": 1, "xsa": 0, "ysa": 0}, [], 1.0, 2.0) is None
    assert regions.region_at_pos(None, [], None) is None
    assert regions.region_at_pos({"xm": 1, "ym": 1, "xsa": 0, "ysa": 0}, [], ()) is None


# ---- the payloads carry the words -------------------------------------------


def test_tracks_and_heatmap_carry_regions(store):
    import spatial

    if not store.manifest_path("m1").exists():
        pytest.skip("m1 export not available")
    t = spatial.tracks(store, "m1", 1)
    assert t["map"] is not None
    assert t["regions"] and all("poly" in r for r in t["regions"])
    h = spatial.heatmap(store, "m1")
    assert h["regions"] == t["regions"]


def test_pair_rows_carry_where_and_the_old_limit_is_gone(store):
    import duo
    import players

    subjects = {p["subject"] for p in players.catalog(store)["players"]}
    if ME not in subjects or MATE not in subjects:
        pytest.skip("the probed pair is not on this machine")
    payload = duo.pair(store, ME, MATE)
    rows = payload["deaths"] + payload["his_deaths"]
    if not rows:
        pytest.skip("the probed pair shares no round here")
    for row in rows:
        assert "my_region" in row and "mate_region" in row
        for key in ("my_region", "mate_region"):
            got = row[key]
            assert got is None or set(got) == {"name", "super", "label", "cm"}
    assert any(r["my_region"] for r in rows), "no death resolved a place name"
    joined = " ".join(payload["limits"])
    assert "no callouts" not in joined
    assert "nearest callout centre" in joined


@pytest.mark.slow
def test_ai_dossier_carries_region_and_site(store):
    import ai

    if not store.manifest_path("m1").exists():
        pytest.skip("m1 export not available")
    ctx = ai.build_context(store, "m1")
    players_ = ctx["players"]
    assert players_ and all("rz" in p for p in players_)
    assert any(p["rz"] for p in players_), "no player resolved a top region"
    assert ctx["rounds"] and all("site" in r for r in ctx["rounds"])