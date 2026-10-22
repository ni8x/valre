"""Shot / hit metrics for a replay (Phase A of the OP.GG-parity plan).

Two independent streams, both recovered from the vrfkit export:

  shots  - gun magazines decrement: every negative diff of
           AmmoComponent.AuthResourceAmount on the magazine object of a gun actor
           is one bullet fired (128 Hz sampling).  The shooter is resolved from
           the AresInventory equip timeline (gun actor -> owning pawn).
  hits   - MulticastNotifyDamage_{Point,Base} invocations, parsed with the exact
           rules of vrfkit's tools/extract_damage_events.py: a run of consecutive
           parameter rows sharing (packet_id, channel_index, actor_net_guid,
           object_net_guid, function); a repeated parameter starts the next
           invocation.  The victim is the row's actor_net_guid (the damaged
           component owner), never the Character parameter.

Nothing else is invented: sprays are a cadence heuristic, and every derived
number is labelled as such in NOTES.

build(store, replay_id, fresh=False) -> dict   (cached as <replay>/metrics.json)
"""
from __future__ import annotations

import json
import re
import time
from bisect import bisect_right
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

import analysis
import engagement
import stats as stats_mod

METRICS_VERSION = 5

AMMO_GROUP = "/Script/ShooterGame.AmmoComponent"
INVENTORY_GROUP = "/Script/ShooterGame.AresInventory"
GUNS_PREFIX = "/Game/Equippables/Guns/"
DAMAGE_PREFIX = "MulticastNotifyDamage_"
DAMAGE_FUNCTIONS = ("MulticastNotifyDamage_Point", "MulticastNotifyDamage_Base")
F32_MAX_NEG = -3.4028234663852886e38  # float sentinel for "no timestamp"
KILL_FEED_SLACK_MS = 100
SPRAY_GAP_MS = 600  # cadence heuristic: shots further apart than this start a new spray

# Display names from vrfkit tools/equippable_table.py (fixed reference set).
WEAPON_NAMES = {
    "Equippable_Unarmed": "Unarmed",
    "Ability_Melee_Base": "Melee",
    "BombEquippable": "Spike",
    "BasePistol": "Classic",
    "SawedOffShotgun": "Shorty",
    "AutomaticPistol": "Frenzy",
    "LugerPistol": "Ghost",
    "CompactPistol": "Compact Pistol",
    "RevolverPistol": "Sheriff",
    "Vector": "Stinger",
    "SubMachineGun_MP5": "Spectre",
    "PumpShotgun": "Bucky",
    "AutomaticShotgun": "Judge",
    "AssaultRifle_Burst": "Bulldog",
    "BattleRifle": "Warden",
    "DMR": "Guardian",
    "AssaultRifle_ACR": "Phantom",
    "AssaultRifle_AK": "Vandal",
    "LeverSniperRifle": "Marshal",
    "BoltSniper": "Operator",
    "DS_Gun": "Outlaw",
    "LightMachineGun": "Ares",
    "HeavyMachineGun": "Odin",
    "Gun_Deadeye_Q_Pistol": "Headhunter",
    "Gun_Deadeye_X_Giantslayer_Prototype_FireRatePrototype": "Tour de Force",
}

NOTES = [
    "Shots are counted from gun magazine decrements (128 Hz), not from shot RPCs - "
    "the export does not carry per-shot vectors.",
    "Shotgun blasts count as one shot but can produce several hits; spray accuracy "
    "therefore reads low on shotguns.",
    "Because a shotgun blast is one shot with up to one hit per pellet, a per-weapon "
    "accuracy above 1.0 is real for shotguns (the Judge is the only such row in the "
    "reference set) - it is hits per trigger pull, not hit probability.",
    "Sprays are a cadence heuristic (shots >600 ms apart start a new spray).",
    "Hit bone/impact coordinates come straight from MulticastNotifyDamage_Point.",
    "Ammo decrements on ability 'guns' (charges) are excluded; only real guns count.",
    "ADR here sums damage-dealt of gun hits only; CombatReport damage in the Stats tab "
    "is the superset (adds utility/falloff extras) - small differences are expected.",
    "Aim/duel metrics marked ~ are proxies: the replay carries no visibility data. An "
    "enemy counts as 'in view' when alive, within 3000 cm and within 25 degrees of aim; "
    "pre-aim/reaction/TTD measure angles and first shot/damage from engagement start.",
    "Per-weapon rows carry their own sample (shots, hits); acc/hs_rate/dmg_per_shot/"
    "shots_per_kill are null when the denominator is zero, and a weapon only appears "
    "once the player has fired or hit with it. The weapon is the shooter's equipped gun "
    "at hit time, so a same-class pickup is attributed to the gun actually used.",
]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _stem(class_path: str | None) -> str | None:
    if not class_path:
        return None
    leaf = class_path.rstrip("/").split("/")[-1]
    leaf = leaf.split(".")[-1]  # Package.Class_C -> Class_C
    leaf = re.sub(r"^Default__", "", leaf)
    leaf = re.sub(r"_C$", "", leaf)
    return leaf or None


def weapon_name(class_path: str | None) -> str | None:
    stem = _stem(class_path)
    if stem is None:
        return None
    if stem in WEAPON_NAMES:
        return WEAPON_NAMES[stem]
    if "Deadeye_Q_Pistol" in stem:
        return "Headhunter"
    if "Deadeye_X" in stem:
        return "Tour de Force"
    return None


def is_gun_actor(class_path: str | None) -> bool:
    if not class_path:
        return False
    stem = _stem(class_path) or ""
    if stem.startswith("Gun_Deadeye"):
        return True
    return GUNS_PREFIX in class_path


def _is_gun_path(class_path: str | None) -> bool:
    return is_gun_actor(class_path)


def _vec(s) -> list[float] | None:
    if not s:
        return None
    s = s.strip()
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
    parts = s.split(",")
    if len(parts) != 3:
        return None
    try:
        return [float(p) for p in parts]
    except ValueError:
        return None


def _read_actors(export_dir: Path) -> tuple[dict[int, str | None], dict[int, str]]:
    """actor guid -> class_path (None when the guid opened as two classes)."""
    p = export_dir / "actors.parquet"
    if not p.exists():
        return {}, {}
    t = pq.read_table(p, columns=["actor_net_guid", "event", "class_path"])
    classes: dict[int, set] = {}
    for g, cls in zip(t.column("actor_net_guid").to_pylist(),
                      t.column("class_path").to_pylist()):
        if g is None or not cls:
            continue
        classes.setdefault(int(g), set()).add(cls)
    single = {g: next(iter(c)) for g, c in classes.items() if len(c) == 1}
    conflicted: dict[int, str] = {g: "|".join(sorted(classes[g])) for g in classes if len(classes[g]) > 1}
    out: dict[int, str | None] = {g: c for g, c in single.items()}
    for g in conflicted:
        out[g] = None
    return out, conflicted


# --------------------------------------------------------------------------- #
# export scan
# --------------------------------------------------------------------------- #
SCAN_COLUMNS = ["time_ms", "packet_id", "channel_index", "actor_net_guid",
                "object_net_guid", "group_path", "field_name",
                "value_i64", "value_f64", "value_bool", "value_str"]


def _scan_fields(export_dir: Path) -> tuple[list[dict], list[dict], list[dict]]:
    """One streaming pass, keeping only ammo / inventory / damage rows (in file order)."""
    p = export_dir / "fields.parquet"
    ammo: list[dict] = []
    inv: list[dict] = []
    dmg: list[dict] = []
    if not p.exists():
        return ammo, inv, dmg
    pf = pq.ParquetFile(p)
    for batch in pf.iter_batches(batch_size=200_000, columns=SCAN_COLUMNS):
        gp = batch.column("group_path")
        if pa.types.is_dictionary(gp.type):
            gp = gp.cast(pa.string())
        fn = batch.column("field_name")
        if pa.types.is_dictionary(fn.type):
            fn = fn.cast(pa.string())
        mask = pc.or_(
            pc.or_(pc.equal(gp, AMMO_GROUP), pc.equal(gp, INVENTORY_GROUP)),
            pc.starts_with(fn, DAMAGE_PREFIX),
        )
        for r in batch.filter(mask).to_pylist():
            g = r.get("group_path") or ""
            if g == AMMO_GROUP:
                ammo.append(r)
            elif g == INVENTORY_GROUP:
                inv.append(r)
            else:
                dmg.append(r)
    return ammo, inv, dmg


# --------------------------------------------------------------------------- #
# shots (ammo decrements)
# --------------------------------------------------------------------------- #
def _count_negative_diffs(rows: list[dict]) -> int:
    prev = None
    n = 0
    for r in rows:
        v = r.get("value_i64")
        if v is None:
            continue
        if prev is not None and v < prev:
            n += 1
        prev = v
    return n


def shots_from_ammo(ammo_rows: list[dict],
                    classes: dict[int, str | None]) -> tuple[list[dict], dict]:
    """Per gun actor pick the magazine object (most negative diffs) and turn its
    decrements into shots. Returns (shots, stats)."""
    by_actor: dict[int, dict[int, list[dict]]] = {}
    for r in ammo_rows:
        fname = (r.get("field_name") or "").rsplit(".", 1)[-1]
        if fname != "AuthResourceAmount":
            continue
        a = r.get("actor_net_guid")
        o = r.get("object_net_guid")
        if a is None or o is None:
            continue
        by_actor.setdefault(int(a), {}).setdefault(int(o), []).append(r)

    shots: list[dict] = []
    stats = {"gun_actors": 0, "skipped_actors": 0, "mag_objects": 0, "reserve_drops": 0,
             "negative_diffs_all": 0}
    for actor, objs in by_actor.items():
        cls = classes.get(actor)
        if not _is_gun_path(cls):
            stats["skipped_actors"] += 1
            continue
        stats["gun_actors"] += 1
        best_obj, best_rows, best_neg = None, None, -1
        for obj, rows in objs.items():
            rows.sort(key=lambda r: (r.get("time_ms") or 0, r.get("packet_id") or 0))
            neg = _count_negative_diffs(rows)
            stats["negative_diffs_all"] += neg
            if neg > best_neg or (neg == best_neg and best_rows is not None and len(rows) > len(best_rows)):
                best_obj, best_rows, best_neg = obj, rows, neg
        if best_rows is None or best_neg <= 0:
            continue
        stats["mag_objects"] += 1
        for obj, rows in objs.items():
            if obj == best_obj:
                continue
            for i in range(1, len(rows)):
                a_, b_ = rows[i - 1].get("value_i64"), rows[i].get("value_i64")
                if a_ is not None and b_ is not None and b_ < a_:
                    stats["reserve_drops"] += 1
        prev = None
        for r in best_rows:
            v = r.get("value_i64")
            if v is None:
                continue
            if prev is not None and v < prev:
                shots.append({"t": r.get("time_ms"), "gun": actor, "mag": v,
                              "n": prev - v})
            prev = v
    shots.sort(key=lambda s: (s.get("t") or 0))
    return shots, stats


# --------------------------------------------------------------------------- #
# equip timeline (gun actor -> owning pawn)
# --------------------------------------------------------------------------- #
def equip_timeline(inv_rows: list[dict]) -> dict[int, list[tuple[int, int, int]]]:
    """gun actor guid -> sorted [(time_ms, packet_id, pawn_guid)]."""
    events: dict[int, list[tuple[int, int, int]]] = {}
    for r in inv_rows:
        fname = (r.get("field_name") or "").rsplit(".", 1)[-1]
        if fname not in ("NewCurrentEquippable", "CurrentEquippable"):
            continue
        pawn = r.get("actor_net_guid")
        gun = r.get("value_i64")
        if pawn is None or gun is None or gun == 0:
            continue
        events.setdefault(int(gun), []).append(
            (int(r.get("time_ms") or 0), int(r.get("packet_id") or 0), int(pawn)))
    for k in events:
        events[k].sort()
    return events


def owner_at(timeline: dict[int, list[tuple[int, int, int]]], gun: int,
             t: int | None) -> int | None:
    evs = timeline.get(gun)
    if not evs:
        return None
    times = [e[0] for e in evs]
    i = bisect_right(times, t or 0) - 1
    return evs[i][2] if i >= 0 else None


# --------------------------------------------------------------------------- #
# hits (MulticastNotifyDamage invocations)
# --------------------------------------------------------------------------- #
SCALARS = {
    "DamagerPlayerState": ("damager_state", "value_i64"),
    "KillCreditPlayerState": ("credit_state", "value_i64"),
    "EventInstigatorPawn": ("shooter_pawn", "value_i64"),
    "EquippableUsed": ("weapon_guid", "value_i64"),
    "DamageDealt": ("damage_dealt", "value_f64"),
    "DamageTaken": ("damage_taken", "value_f64"),
    "FalloffMultiplier": ("falloff", "value_f64"),
    "DamagedBone": ("bone", "value_str"),
    "RegionalDamage": ("region", "value_i64"),
    "bDamageKilledTarget": ("killed", "value_bool"),
    "bIsWallPenetration": ("wallbang", "value_bool"),
    "NetTimestamp": ("net_timestamp", "value_f64"),
    "RespawnNumber": ("respawn", "value_i64"),
    "Character": ("character", "value_i64"),
}
VECTORS = {
    "DamageOrigin": "origin",
    "DamageImpactLocation": "impact",
    "DamageDirection": "direction",
    "DamageImpactNormal": "impact_normal",
}


def unresolved_counts(shots: list[dict], hits: list[dict]) -> tuple[int, int]:
    """Match-level count of enriched shots with no resolved shooter (`p`) and
    damage invocations with no resolved shooter (`shooter`).

    Neither can be attributed to a player, so they are reported once in
    `validation` instead of on per-player cells (where they could never be
    anything but zero). The shots are still dropped from per-player aggregates;
    this only makes the drop visible."""
    return (sum(1 for s in shots if s.get("p") is None),
            sum(1 for h in hits if h.get("shooter") is None))


def parse_hits(dmg_rows: list[dict]) -> tuple[list[dict], dict]:
    hits: list[dict] = []
    stats = {"point": 0, "base": 0, "splits": 0, "sentinels": 0,
             "untyped_params": 0, "bad_vectors": 0, "repeated_params": 0}
    cur: dict | None = None
    cur_key = None
    seen: set[str] = set()

    def close():
        nonlocal cur
        if cur is not None:
            hits.append(cur)
            if cur.get("fn") == "MulticastNotifyDamage_Point":
                stats["point"] += 1
            else:
                stats["base"] += 1
            cur = None

    for r in dmg_rows:
        fname = r.get("field_name") or ""
        func, _, param = fname.partition(".")
        if func not in DAMAGE_FUNCTIONS:
            continue
        key = (r.get("packet_id"), r.get("channel_index"),
               r.get("actor_net_guid"), r.get("object_net_guid"), func)
        repeat = False
        if cur is None or key != cur_key:
            new = True
        elif param in seen:
            new = True
            repeat = True
        else:
            new = False
        if new:
            if cur is not None and key == cur_key:
                stats["repeated_params"] += 1
            close()
            cur = {"fn": func, "victim": r.get("actor_net_guid"), "t": r.get("time_ms")}
            cur_key = key
            seen = set()
        seen.add(param)
        if param in SCALARS:
            col, vcol = SCALARS[param]
            v = r.get(vcol)
            if col == "net_timestamp" and v is not None and float(v) <= F32_MAX_NEG + 1.0:
                cur[col] = None
                stats["sentinels"] += 1
            elif col == "respawn" and v == -1:
                cur[col] = None
                stats["sentinels"] += 1
            else:
                cur[col] = v
        elif param in VECTORS:
            vec = _vec(r.get("value_str"))
            if vec is None:
                stats["bad_vectors"] += 1
            else:
                cur[VECTORS[param]] = vec
        else:
            stats["untyped_params"] += 1
    close()
    return hits, stats


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #
def build(store, replay_id: str, fresh: bool = False) -> dict:
    cache = store.dir(replay_id) / "metrics.json"
    if not fresh and cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("version") == METRICS_VERSION:
                return data
        except Exception:
            pass
    payload = _compute(store, replay_id)
    try:
        cache.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass
    return payload


def _compute(store, replay_id: str) -> dict:
    export_dir = store.export_dir(replay_id)
    manifest = analysis.load_manifest(store.manifest_path(replay_id))
    players, char2subj, ps2subj, subj2char = stats_mod._identity(manifest)
    subject_of_pawn = dict(char2subj)

    st = stats_mod.build(store, replay_id)
    rounds = {r["round"]: r for r in st.get("rounds", [])}
    n_rounds = len(rounds)

    classes, conflicted = _read_actors(export_dir)
    ammo_rows, inv_rows, dmg_rows = _scan_fields(export_dir)

    raw_shots, shot_stats = shots_from_ammo(ammo_rows, classes)
    timeline = equip_timeline(inv_rows)
    hits, hit_stats = parse_hits(dmg_rows)

    # ---- enrich shots ------------------------------------------------------
    shots: list[dict] = []
    for s in raw_shots:
        gun = s["gun"]
        cls = classes.get(gun)
        pawn = owner_at(timeline, gun, s["t"])
        subject = subject_of_pawn.get(pawn) if pawn is not None else None
        shots.append({
            "t": s["t"],
            "r": stats_mod._round_of(rounds, s["t"]),
            "p": subject,
            "pawn": pawn,
            "w": weapon_name(cls) or _stem(cls) or str(gun),
            "wclass": _stem(cls),
            "n": s["n"],
        })

    # ---- enrich hits --------------------------------------------------------
    for h in hits:
        victim = h.get("victim")
        subj_v = subject_of_pawn.get(victim)
        subj_s = ps2subj.get(h.get("damager_state")) if h.get("damager_state") else None
        if subj_s is None and h.get("credit_state"):
            subj_s = ps2subj.get(h.get("credit_state"))
        if subj_s is None and h.get("shooter_pawn"):
            subj_s = subject_of_pawn.get(h.get("shooter_pawn"))
        cls = classes.get(h.get("weapon_guid")) if h.get("weapon_guid") else None
        h["shooter"] = subj_s
        h["victim_subject"] = subj_v
        h["r"] = stats_mod._round_of(rounds, h.get("t"))
        h["weapon"] = weapon_name(cls) or _stem(cls)
        h["wclass"] = _stem(cls)
        h["gun"] = bool(h.get("weapon_guid")) and _is_gun_path(cls)

    unresolved_shots, unresolved_hits = unresolved_counts(shots, hits)

    # ---- sprays + per-player aggregation ------------------------------------
    per: dict[str, dict] = {}
    for p in players:
        per[p["subject"]] = {
            "subject": p["subject"],
            "shots": 0, "hits": 0, "gun_hits": 0, "head_hits": 0,
            "leg_hits": 0, "util_hits": 0, "dmg": 0.0, "kills": 0, "wallbang_hits": 0,
            "weapons": {}, "sprays": 0, "spray_hits": 0,
        }

    gun_shots: dict[tuple, list[dict]] = {}
    for s in shots:
        p = s.get("p")
        if p is None:
            continue
        cell = per.setdefault(p, _blank_player(p))
        cell["shots"] += s["n"]
        cell["weapons"].setdefault(s["w"], _new_weapon_cell())
        cell["weapons"][s["w"]]["shots"] += s["n"]
        gun_shots.setdefault((p, s["wclass"]), []).append(s)

    hit_attach: dict[tuple, list[dict]] = {}
    for h in hits:
        sp = h.get("shooter")
        if sp is None:
            continue
        cell = per.setdefault(sp, _blank_player(sp))
        if h.get("gun"):
            cell["hits"] += 1
            cell["gun_hits"] += 1
            cell["dmg"] += float(h.get("damage_dealt") or 0.0)
            w = h.get("weapon") or "?"
            cell["weapons"].setdefault(w, _new_weapon_cell())
            cell["weapons"][w]["hits"] += 1
            cell["weapons"][w]["dmg"] += float(h.get("damage_dealt") or 0.0)
            hit_attach.setdefault((sp, h.get("wclass")), []).append(h)
            bone = (h.get("bone") or "").lower()
            if bone == "head":
                cell["head_hits"] += 1
                cell["weapons"][w]["head_hits"] += 1
            elif bone in ("leg", "l_leg", "r_leg", "foot", "l_foot", "r_foot"):
                cell["leg_hits"] += 1
        else:
            cell["util_hits"] += 1
        if h.get("wallbang"):
            cell["wallbang_hits"] += 1
        if h.get("killed") and h.get("victim_subject"):
            cell["kills"] += 1
            if h.get("gun"):
                w = h.get("weapon")
                if w and w in cell["weapons"]:
                    cell["weapons"][w]["kills"] += 1

    sprays: list[dict] = []
    for (p, wclass), slist in gun_shots.items():
        slist.sort(key=lambda s: (s.get("t") or 0))
        cur: list[dict] = []
        for s in slist:
            t = s.get("t") or 0
            if cur and (t - (cur[-1].get("t") or 0) > SPRAY_GAP_MS
                        or s.get("r") != cur[-1].get("r")):
                sprays.append(_finish_spray(cur))
                cur = []
            cur.append(s)
        if cur:
            sprays.append(_finish_spray(cur))

    # attach hits to sprays
    by_key: dict[tuple, list[dict]] = {}
    for sp in sprays:
        by_key.setdefault((sp["p"], sp["wclass"]), []).append(sp)
    for (p, wclass), hlist in hit_attach.items():
        sps = by_key.get((p, wclass))
        if not sps:
            continue
        for h in hlist:
            t = h.get("t") or 0
            match = None
            for sp in sps:
                if sp["start"] - 150 <= t <= sp["end"] + 300:
                    match = sp
                    break
            if match is None:
                continue
            match["hits"] += 1
            match["dmg"] += float(h.get("damage_dealt") or 0.0)
            if h.get("killed"):
                match["kills"] += 1
    for sp in sprays:
        cell = per.setdefault(sp["p"], _blank_player(sp["p"]))
        cell["sprays"] += 1
        cell["spray_hits"] += sp["hits"]
    sprays.sort(key=lambda s: (s.get("start") or 0))

    # ---- kill-feed cross-check ---------------------------------------------
    events_path = export_dir / "events.parquet"
    deaths: list[dict] = []
    if events_path.exists():
        t_ev = pq.read_table(events_path, columns=["group", "time1", "word1"])
        for g, tm, victim in zip(t_ev.column("group").to_pylist(),
                                 t_ev.column("time1").to_pylist(),
                                 t_ev.column("word1").to_pylist()):
            if g == "characterDeath":
                deaths.append({"t": tm, "victim_char": victim})
    killed_blows = [h for h in hits
                    if h.get("killed") and h.get("victim_subject")]
    deaths_sorted = sorted(deaths, key=lambda d: d.get("t") or 0)
    pairs = 0
    used = set()
    for h in sorted(killed_blows, key=lambda h: h.get("t") or 0):
        best = None
        for i, d in enumerate(deaths_sorted):
            if i in used:
                continue
            if d.get("victim_char") != h.get("victim"):
                continue
            dt = abs((d.get("t") or 0) - (h.get("t") or 0))
            if dt <= KILL_FEED_SLACK_MS:
                best = i
                break
        if best is not None:
            used.add(best)
            pairs += 1
    validation = {
        "shots_all_objects": len(raw_shots),
        "shots_real_guns": sum(1 for s in shots if s.get("p")),
        "unresolved_shots": unresolved_shots,
        "unresolved_hits": unresolved_hits,
        "hits_point": hit_stats["point"],
        "hits_base": hit_stats["base"],
        "invocations": len(hits),
        "repeated_param_splits": hit_stats["repeated_params"],
        "sentinels_nulled": hit_stats["sentinels"],
        "untyped_params": hit_stats["untyped_params"],
        "bad_vectors": hit_stats["bad_vectors"],
        "killed_blows_on_players": len(killed_blows),
        "character_deaths": len(deaths),
        "kill_feed_pairs": pairs,
        "kill_feed_unpaired": len(killed_blows) - pairs,
        "gun_actors": shot_stats["gun_actors"],
        "skipped_ammo_actors": shot_stats["skipped_actors"],
    }

    # ---- Phase B: engagement model + aim/movement/duel proxies ----
    teams = {p["subject"]: p.get("team") for p in st.get("players", [])}
    eng_cells, eng_meta = engagement.analyze(
        export_dir, players, char2subj, teams, rounds, deaths, hits, shots, sprays
    )
    for subject, upd in eng_cells.items():
        cell = per.get(subject)
        if cell is not None:
            cell.update(upd)
    validation["engagements_total"] = eng_meta.get("engagements_total") or 0
    validation["eye_offset_cm"] = eng_meta.get("eye_offset_cm")

    players_out = []
    for p in players:
        cell = per.get(p["subject"])
        if cell is None:
            continue
        shots_n = cell["shots"]
        hits_n = cell["hits"]
        cell["accuracy"] = (hits_n / shots_n) if shots_n else None
        cell["hs_pct"] = (cell["head_hits"] / cell["gun_hits"]) if cell["gun_hits"] else None
        cell["adr_gun"] = cell["dmg"] / n_rounds if n_rounds else None
        cell["spray_acc"] = (cell["spray_hits"] / shots_n) if shots_n else None
        cell["weapons"] = _finish_weapons(cell["weapons"])
        players_out.append(cell)

    return {
        "version": METRICS_VERSION,
        "replay_id": replay_id,
        "generated_at": int(time.time() * 1000),
        "n_rounds": n_rounds,
        "players": players_out,
        "hits": hits,
        "shots": shots,
        "sprays": sprays,
        "validation": validation,
        "notes": NOTES,
    }


def _new_weapon_cell() -> dict:
    return {"shots": 0, "hits": 0, "dmg": 0.0, "kills": 0, "head_hits": 0}


def _finish_weapons(weapons: dict) -> dict:
    """Add the derived rates to every ``{weapon: counts}`` cell in place.

    ``acc`` and ``hs_rate`` stay raw ratios (same convention as the player-level
    ``accuracy``/``hs_pct``); ``dmg_per_shot`` and ``shots_per_kill`` are rounded
    for display. Every rate is ``None`` when its denominator is zero - a weapon
    that was never fired at is missing, not a zero percent weapon.
    """
    for w, cell in weapons.items():
        shots = cell.get("shots") or 0
        hits = cell.get("hits") or 0
        kills = cell.get("kills") or 0
        head = cell.get("head_hits") or 0
        cell["acc"] = (hits / shots) if shots else None
        cell["hs_rate"] = (head / hits) if hits else None
        cell["dmg_per_shot"] = round(cell.get("dmg", 0.0) / shots, 1) if shots else None
        cell["shots_per_kill"] = round(shots / kills, 1) if kills else None
    return weapons


def _blank_player(subject: str) -> dict:
    return {
        "subject": subject,
        "shots": 0, "hits": 0, "gun_hits": 0, "head_hits": 0,
        "leg_hits": 0, "util_hits": 0, "dmg": 0.0, "kills": 0, "wallbang_hits": 0,
        "weapons": {}, "sprays": 0, "spray_hits": 0,
        "engagements": 0, "pre_aim_deg": None, "pre_aim_n": 0,
        "pre_aim_complete": None, "head_level_pct": None, "near_samples": 0,
        "reaction_ms": None, "reaction_n": 0, "ttd_ms": None, "ttd_n": 0,
        "counter_strafe_pct": None, "shots_classified": 0,
        "first_eng_n": 0, "first_eng_acc": None,
        "spray_radius_cm": None, "spray_spread_deg": None, "spray_ttk_ms": None,
        "hs_kill_pct": None,
    }


def _finish_spray(shots: list[dict]) -> dict:
    first = shots[0]
    last = shots[-1]
    return {
        "p": first.get("p"),
        "wclass": first.get("wclass"),
        "w": first.get("w"),
        "r": first.get("r"),
        "start": first.get("t"),
        "end": last.get("t"),
        "shots": sum(s.get("n") or 1 for s in shots),
        "hits": 0,
        "dmg": 0.0,
        "kills": 0,
        "ticks": [s.get("t") for s in shots],
    }
