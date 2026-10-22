"""Derived stats for a replay.

Everything here is computed from the vrfkit export (parquet + manifest.json).
Sources are validated against the raw replay on m1.vrf; see NOTES for caveats.

build(store, replay_id, fresh=False) -> dict   (cached as <replay>/stats.json)
"""
from __future__ import annotations

import bisect
import json
import math
import re
import time
from pathlib import Path
from statistics import median

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.compute as pc
import pyarrow.parquet as pq

import analysis

STATS_VERSION = 19
# One side of a regulation Valorant match is 12 rounds, so a match past 24 rounds
# is in overtime and the two-half side model no longer describes it.
REGULATION_ROUNDS = 24
TRADE_WINDOW_MS = 5000
ISO_DISTANCE_CM = 1200.0  # ~1.8s of full-speed running (675 cm/s)

NOTES = [
    "Player display names are NOT stored in the replay - only account UUIDs.",
    "Kills/deaths/first-bloods come from the server event timeline (authoritative).",
    "Damage/headshot/assist numbers come from Bomb_CombatReportComponent, which is a "
    "cumulatively replicated struct; per-key MAX is used (never summed).",
    "Headshot% is computed from damage regions (region 1 == head).",
    "Headshot detection via damage value alone is weapon-specific and unreliable, so it is not used.",
    "Trade = killer killed within 5s after the death, same round.",
    "Isolated death = no living teammate within 1200cm at the moment of death.",
    "Bait% is a heuristic: died untraded while a living teammate was within 1200cm of the killer.",
    "Clutch = last player alive on their team in a round their team won.",
    "Round winner is derived from the event timeline (spike / last team standing), not from a decoded scoreboard.",
    "Round winners are confirmed/corrected with BaseTeamState 'Wins' update timing (the value is untyped, only the "
    "timing of one row per round end is informative).",
    "ability_casts only counts the casts still retained in the replicated AbilityCastsThisRound rolling window "
    "(~4-7 most recent casts per player), not every cast in the match.",
    "Attack/defence split uses the derived per-round sides (defusal evidence + the half-time swap); a round whose "
    "side could not be derived is excluded from both blocks, and a block's rates are null when that side was "
    "never played (null = no data, not zero).",
    "Money is read from MoneyManagementComponent (StartOfRoundMoney preferred, Money as the fallback); the "
    "OwnerExclusivePlayerInfo.CopyRoundInfos copy of the same field reads 0 in every ingested build.",
    "Buy classes are this project's own convention, not a published standard: eco below 3000 average loadout "
    "value, force 3000-5000, full buy 5000 and above, plus 'pistol' for the first round of each half. Average "
    "loadout value is the team's gear worth per player on Riot's accounting scale (armour and abilities count), "
    "so it sits well above a naive weapon price sum. No 'credits spent' figure is derived: start money minus end "
    "money mixes grants, rewards, refunds and purchases (see DATA.md).",
    "ADR is damage dealt to opponents per round. Damage is the CombatReport's region damage "
    "(DealtInteractions[].Regions[].Damage), attributed by the interaction's ParticipantSubject, which names the "
    "player who took it. damage_dealt is the raw region total and can exceed the sum of damage_to_opponents by the "
    "damage a player dealt to themselves (their own utility, or the spike detonation the export records as a self "
    "kill); self_damage and damage_unattributed expose that difference. The per-round 'damage' field is the raw "
    "region total and 'damage_opponents' is the ADR input.",
    "A self-inflicted death still counts as a death: the event timeline records it, so kills/deaths/KAST are the "
    "raw count and self_deaths says how many of a player's deaths were their own doing.",
    "Attack/defence is a two-half model, so overtime is kept out of it: once a match runs past the 24-round "
    "regulation the extra rounds are marked overtime and carry no sides, because overtime swaps sides every round "
    "and the single half-time swap no longer describes them.",
]

TEAMS = ("Blue", "Red")


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _load_net_guids(export_dir: Path) -> dict[int, str]:
    p = export_dir / "net_guids.parquet"
    if not p.exists():
        return {}
    t = pq.read_table(p, columns=["net_guid", "path"])
    return {g: (path or "") for g, path in zip(t.column("net_guid").to_pylist(),
                                               t.column("path").to_pylist())}


def _weapon_name(guid, net_guids: dict[int, str]) -> str | None:
    if guid is None:
        return None
    path = net_guids.get(int(guid))
    if not path:
        return None
    leaf = path.rstrip("/").split("/")[-1]
    leaf = re.sub(r"^BP_", "", leaf)
    leaf = re.sub(r"_C$", "", leaf)
    return leaf or None


def _val(row: dict):
    for k in ("value_f64", "value_i64", "value_bool", "value_str"):
        v = row.get(k)
        if v is not None:
            return v
    return None


def _num(row: dict):
    v = row.get("value_f64")
    if v is None:
        v = row.get("value_i64")
    return v


def _read_fields(export_dir: Path, regex: str, columns: list[str]) -> list[dict]:
    """Stream fields.parquet keeping only rows whose group_path matches regex.

    group_path is dictionary-encoded, so the filter is applied in memory after
    casting to plain string (predicate pushdown is not available for it).
    """
    p = export_dir / "fields.parquet"
    if not p.exists():
        return []
    rows: list[dict] = []
    pf = pq.ParquetFile(p)
    for batch in pf.iter_batches(batch_size=200_000, columns=columns):
        gp = batch.column("group_path")
        if pa.types.is_dictionary(gp.type):
            gp = gp.cast(pa.string())
        try:
            mask = pc.match_substring_regex(gp, regex)
        except Exception:
            vals = gp.to_pylist()
            mask = pa.array([bool(re.search(regex, v or "")) for v in vals])
        rows.extend(batch.filter(mask).to_pylist())
    return rows


# --------------------------------------------------------------------------- #
# position index (movement.parquet)
# --------------------------------------------------------------------------- #
class Positions:
    def __init__(self, export_dir: Path, char_guids: set[int]):
        self._t: dict[int, list[int]] = {}
        self._x: dict[int, list[float]] = {}
        self._y: dict[int, list[float]] = {}
        self._z: dict[int, list[float]] = {}
        p = export_dir / "movement.parquet"
        if not p.exists():
            return
        t = pq.read_table(p, columns=["time_ms", "character_net_guid", "pos_x", "pos_y", "pos_z"])
        tms = t.column("time_ms").to_pylist()
        guids = t.column("character_net_guid").to_pylist()
        xs = t.column("pos_x").to_pylist()
        ys = t.column("pos_y").to_pylist()
        zs = t.column("pos_z").to_pylist()
        for i, g in enumerate(guids):
            if g not in char_guids:
                continue
            x, y, z = xs[i], ys[i], zs[i]
            if x is None or y is None or z is None:
                continue
            if x < -20000 or y < -20000:  # hidden/parked actors
                continue
            self._t.setdefault(g, []).append(tms[i])
            self._x.setdefault(g, []).append(x)
            self._y.setdefault(g, []).append(y)
            self._z.setdefault(g, []).append(z)
        for g in self._t:
            order = sorted(range(len(self._t[g])), key=lambda i: self._t[g][i])
            self._t[g] = [self._t[g][i] for i in order]
            self._x[g] = [self._x[g][i] for i in order]
            self._y[g] = [self._y[g][i] for i in order]
            self._z[g] = [self._z[g][i] for i in order]

    def at(self, guid: int, t_ms: int):
        ts = self._t.get(guid)
        if not ts:
            return None
        i = bisect.bisect_left(ts, t_ms)
        if i >= len(ts):
            i = len(ts) - 1
        elif i > 0 and (t_ms - ts[i - 1]) <= (ts[i] - t_ms):
            i -= 1
        return (self._x[guid][i], self._y[guid][i], self._z[guid][i])


def _dist(a, b) -> float | None:
    if a is None or b is None:
        return None
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


# --------------------------------------------------------------------------- #
# manifest
# --------------------------------------------------------------------------- #
def _identity(manifest: dict):
    players = analysis.player_table(manifest)
    char2subj: dict[int, str] = {}
    ps2subj: dict[int, str] = {}
    subj2char: dict[str, int] = {}
    for p in players:
        subj = p["subject"]
        for cg in (p.get("all_character_net_guids") or []):
            if cg:
                char2subj[int(cg)] = subj
        if p.get("character_net_guid"):
            char2subj[int(p["character_net_guid"])] = subj
            subj2char[subj] = int(p["character_net_guid"])
        if p.get("actor_net_guid"):
            ps2subj[int(p["actor_net_guid"])] = subj
    return players, char2subj, ps2subj, subj2char


# --------------------------------------------------------------------------- #
# teams
# --------------------------------------------------------------------------- #
def _teams_from_combat_report(rows: list[dict], ps2subj: dict[int, str]) -> dict[int, str]:
    """CombatReport 'ParticipantTeamName' gives the OWN team of each report actor."""
    votes: dict[int, dict[str, int]] = {}
    for r in rows:
        fn = r.get("field_name") or ""
        if not fn.endswith("ParticipantTeamName"):
            continue
        v = r.get("value_str")
        if v in TEAMS:
            votes.setdefault(int(r["actor_net_guid"]), {})
            votes[int(r["actor_net_guid"])][v] = votes[int(r["actor_net_guid"])].get(v, 0) + 1
    out: dict[int, str] = {}
    for actor, v in votes.items():
        out[actor] = max(v.items(), key=lambda kv: kv[1])[0]
    return out


# --------------------------------------------------------------------------- #
# main compute
# --------------------------------------------------------------------------- #
def _compute_events(events_path: Path):
    """Returns rounds skeleton + deaths + ults + spikes.

    A manifest-only export (interrupted download) has no events.parquet; the
    engine degrades to an empty skeleton instead of raising FileNotFoundError.
    """
    if not events_path.exists():
        return [], {}, [], [], []
    t = pq.read_table(events_path)
    rows = t.to_pylist()
    rounds: dict[int, dict] = {}
    order: list[int] = []
    for r in rows:
        if r.get("group") == "roundStarted":
            w0 = r.get("word0")
            n = int(w0) + 1 if w0 is not None else len(order) + 1
            if n not in rounds:
                rounds[n] = {"round": n, "start_ms": r.get("time1"), "end_ms": None,
                             "switch_teams_ms": None}
                order.append(n)
    order.sort()
    for i, n in enumerate(order):
        rounds[n]["end_ms"] = rounds[order[i + 1]]["start_ms"] if i + 1 < len(order) else None
    switch_times = sorted({r.get("time1") for r in rows
                           if r.get("group") == "switchTeams" and r.get("time1") is not None})
    switch_ms = switch_times[0] if switch_times else None
    if switch_ms is not None:
        for n in order:
            rounds[n]["switch_teams_ms"] = switch_ms
    deaths = []
    ults = []
    spikes = []
    for r in rows:
        g = r.get("group")
        tm = r.get("time1")
        if g == "characterDeath":
            deaths.append({"t": tm, "killer_char": r.get("word0"), "victim_char": r.get("word1")})
        elif g == "characterUltimateUsed":
            ults.append({"t": tm, "who": r.get("word0")})
        elif g in ("spikePlanted", "spikeDefused", "spikeExploded"):
            spikes.append({"t": tm, "kind": g.replace("spike", "").lower()})
    return order, rounds, deaths, ults, spikes


def _round_of(rounds: dict, t) -> int | None:
    if t is None:
        return None
    best = None
    for n in sorted(rounds):
        if rounds[n]["start_ms"] is not None and rounds[n]["start_ms"] <= t:
            best = n
        else:
            break
    return best


def _compute_combat_report(rows: list[dict]) -> tuple[dict, dict, dict, dict]:
    """Per (actor, round) aggregation of the cumulative CombatReport struct.

    Returns (per_actor_round, validation, region_ki, ki_subject) where
    per_actor_round[actor][round] is
    {damage, hits, kills, deaths, assists, regions{0,1,2}, headshot_kills,
     head_damage, body_damage, other_damage, wallbang_damage, opponents{...}},
    region_ki[(actor, round, report, interaction)] is the region damage that
    interaction dealt (the same values summed into per_actor_round) and
    ki_subject[(actor, round, report, interaction)] is the subject who took it,
    so the damage can be attributed to whoever received it.
    """
    # key -> max/anyTrue
    region_agg: dict[tuple, float] = {}
    region_kill: dict[tuple, bool] = {}
    interact_agg: dict[tuple, dict] = {}
    # Region damage and the interaction's subject, keyed per interaction, so damage can
    # be split by who took it (opponent, themselves, nobody).
    region_ki: dict[tuple, float] = {}
    ki_subject: dict[tuple, str] = {}
    re_dr = re.compile(r"Rounds\[(\d+)\]\.Reports\[(\d+)\]\.Interactions\[(\d+)\]"
                       r"\.DealtInteractions\[(\d+)\]\.Regions\[(\d+)\]\."
                       r"(Damage|Hits|bIsKill|bIsWallPen|Region|DestroyedArmor)$")
    re_int = re.compile(r"Rounds\[(\d+)\]\.Reports\[(\d+)\]\.Interactions\[(\d+)\]\."
                        r"(DamageDealt|HitsDealt|bDidKill|bWasKiller|AssistType|"
                        r"ParticipantSubject|ParticipantTeamName|CombatReportIndex)$")
    re_rep = re.compile(r"Rounds\[(\d+)\]\.Reports\[(\d+)\]\.(bDied|RoundNumber)$")

    report_died: dict[tuple, bool] = {}
    for r in rows:
        fn = r.get("field_name") or ""
        actor = int(r.get("actor_net_guid") or 0)
        m = re_dr.match(fn)
        if m:
            n, mi, ki, ji, ri, leaf = (int(m.group(1)) + 1, int(m.group(2)), int(m.group(3)),
                                       int(m.group(4)), int(m.group(5)), m.group(6))
            key = (actor, n, mi, ki, ji, ri)
            if leaf in ("Damage",):
                region_agg[key] = max(region_agg.get(key, 0.0), float(_num(r) or 0))
            elif leaf == "Hits":
                region_agg[key + ("h",)] = max(region_agg.get(key + ("h",), 0.0), float(_num(r) or 0))
            elif leaf == "bIsKill":
                if _val(r):
                    region_kill[key] = True
            elif leaf == "bIsWallPen":
                if _val(r):
                    region_agg[key + ("w",)] = 1.0
            continue
        m = re_int.match(fn)
        if m:
            # CombatReport's Rounds[N] is 0-based; round N+1 matches the event timeline.
            n, mi, ki, leaf = int(m.group(1)) + 1, int(m.group(2)), int(m.group(3)), m.group(4)
            key = (actor, n, mi, ki, leaf)
            if leaf in ("DamageDealt", "HitsDealt"):
                interact_agg.setdefault(key, 0.0)
                interact_agg[key] = max(interact_agg[key], float(_num(r) or 0))
            elif leaf in ("bDidKill", "bWasKiller"):
                if _val(r):
                    interact_agg[(actor, n, mi, ki, leaf)] = True
            elif leaf == "AssistType":
                v = _num(r)
                if v:
                    interact_agg[(actor, n, mi, ki, "AssistType")] = max(
                        interact_agg.get((actor, n, mi, ki, "AssistType"), 0), int(v))
            elif leaf == "ParticipantSubject":
                v = r.get("value_str")
                if v:
                    interact_agg.setdefault((actor, n, mi, ki, "ParticipantSubject"), v)
                    ki_subject[(actor, n, mi, ki)] = v
            continue
        m = re_rep.match(fn)
        if m:
            n, mi, leaf = int(m.group(1)) + 1, int(m.group(2)), m.group(3)
            if leaf == "bDied" and _val(r):
                report_died[(actor, n, mi)] = True

    per: dict[int, dict[int, dict]] = {}
    for key, v in region_agg.items():
        if len(key) == 6:
            actor, n, mi, ki, ji, ri = key
            agg = per.setdefault(actor, {}).setdefault(n, _blank_round())
            agg["damage"] += v
            if ri == 1:
                agg["head_damage"] += v
            elif ri == 0:
                agg["body_damage"] += v
            else:
                agg["other_damage"] += v
            agg["regions"][str(ri)] = agg["regions"].get(str(ri), 0.0) + v
            key_ki = (actor, n, mi, ki)
            region_ki[key_ki] = region_ki.get(key_ki, 0.0) + v
        elif key[-1] == "h":
            actor, n = key[0], key[1]
            agg = per.setdefault(actor, {}).setdefault(n, _blank_round())
            agg["hits"] += v
        elif key[-1] == "w":
            actor, n = key[0], key[1]
            agg = per.setdefault(actor, {}).setdefault(n, _blank_round())
            agg["wallbang_damage"] += 1
    for (actor, n, mi, ki, ji, ri) in region_kill:
        agg = per.setdefault(actor, {}).setdefault(n, _blank_round())
        agg["kills"] += 1
        if ri == 1:
            agg["headshot_kills"] += 1
    for (actor, n, mi, ki, leaf), v in interact_agg.items():
        agg = per.setdefault(actor, {}).setdefault(n, _blank_round())
        if leaf == "bDidKill" and v:
            agg["_did_kill"] = agg.get("_did_kill", 0) + 1
        elif leaf == "bWasKiller" and v:
            agg["_was_killer"] = agg.get("_was_killer", 0) + 1
        elif leaf == "AssistType":
            agg["assists"] += 1
        elif leaf == "ParticipantSubject":
            agg["_opponents"].add(v)
    for (actor, n, mi) in report_died:
        agg = per.setdefault(actor, {}).setdefault(n, _blank_round())
        agg["deaths"] += 1

    # Interaction-level damage total (max over the unstable Reports index), kept as a
    # validation cross-check against the region total; attribution uses region_ki above,
    # which keeps the duplicate report a revive can produce.
    dealt: dict[tuple, float] = {}
    for (actor, n, mi, ki, leaf), v in interact_agg.items():
        if leaf == "DamageDealt":
            key = (actor, n, ki)
            dealt[key] = max(dealt.get(key, 0.0), float(v or 0))

    validation = {
        "combat_report_kills": sum(a.get("kills", 0) for n in per.values() for a in n.values()),
        "combat_report_deaths": sum(a.get("deaths", 0) for n in per.values() for a in n.values()),
        "combat_report_didkill": sum(a.get("_did_kill", 0) for n in per.values() for a in n.values()),
        "damage_interaction_total": round(sum(dealt.values()), 1),
        "damage_region_total": round(sum(a.get("damage", 0.0) for n in per.values() for a in n.values()), 1),
    }
    return per, validation, region_ki, ki_subject


def _blank_round() -> dict:
    return {"damage": 0.0, "hits": 0.0, "kills": 0, "deaths": 0, "assists": 0,
            "head_damage": 0.0, "body_damage": 0.0, "other_damage": 0.0,
            "headshot_kills": 0, "wallbang_damage": 0, "regions": {}, "_opponents": set()}


# --------------------------------------------------------------------------- #
def build(store, replay_id: str, fresh: bool = False) -> dict:
    cache = store.dir(replay_id) / "stats.json"
    if not fresh and cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("version") == STATS_VERSION:
                return data
        except Exception:
            pass
    payload = _compute(store, replay_id)
    try:
        cache.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass
    return payload


def _death_credits(deaths: list[dict], char2subj: dict) -> tuple[dict, dict]:
    """Kills and deaths per subject from the death list.

    A death actor id can be absent - a spike detonation carries no killer - so the
    cast to int is guarded and an absent id credits nobody. An id of 0 maps to -1,
    which is not a character index, so it credits nobody either.
    """
    kills: dict = {}
    dths: dict = {}
    for d in deaths:
        killer, victim = d["killer_char"], d["victim_char"]
        if killer != victim and killer is not None:
            s = char2subj.get(int(killer) or -1)
            if s is not None:
                kills[s] = kills.get(s, 0) + 1
        if victim is not None:
            s = char2subj.get(int(victim) or -1)
            if s is not None:
                dths[s] = dths.get(s, 0) + 1
    return kills, dths


def _compute(store, replay_id: str) -> dict:
    export_dir = store.export_dir(replay_id)
    manifest = analysis.load_manifest(store.manifest_path(replay_id))
    players, char2subj, ps2subj, subj2char = _identity(manifest)
    subjects = [p["subject"] for p in players]
    nets = _load_net_guids(export_dir)

    order, rounds, deaths, ults, spikes = _compute_events(export_dir / "events.parquet")
    n_rounds = len(order)

    fields = _read_fields(
        export_dir,
        "Bomb_CombatReportComponent|DamageableComponent|MoneyManagement|"
        "PlayerScoreComponent|BaseTeamState|Comp_AbilityStatisticsReplicator|TimedBomb",
        ["time_ms", "packet_id", "actor_net_guid", "group_path", "field_name",
         "value_i64", "value_f64", "value_bool", "value_str"],
    )
    cr_rows = [r for r in fields if "Bomb_CombatReportComponent" in (r.get("group_path") or "")]
    per_actor_round, cr_validation, region_ki, ki_subject = _compute_combat_report(cr_rows)

    # actor (CombatReport) -> subject via per-round kill-vector correlation
    actor_team = _teams_from_combat_report(cr_rows, ps2subj)
    events_kills_by_subj_round: dict[str, dict[int, int]] = {}
    for d in deaths:
        s = char2subj.get(int(d["killer_char"]) if d["killer_char"] is not None else -1)
        r = _round_of(rounds, d["t"])
        if s and r:
            events_kills_by_subj_round.setdefault(s, {})
            events_kills_by_subj_round[s][r] = events_kills_by_subj_round[s].get(r, 0) + 1
    actor_to_subject: dict[int, str] = {}
    cr_kills_by_actor_round: dict[int, dict[int, int]] = {}
    for cr_actor, byround in per_actor_round.items():
        cr_kills_by_actor_round[cr_actor] = {n: a.get("kills", 0) for n, a in byround.items()}

    # CombatReport has an unstable per-report 'Report' index, so kills there can
    # double count; use it only as a fallback for actor->subject correlation.
    used = set()
    for cr_actor, byround in cr_kills_by_actor_round.items():
        best, best_score = None, -1
        for s in subjects:
            if s in used:
                continue
            ek = events_kills_by_subj_round.get(s, {})
            score = sum(1 for r, c in byround.items() if ek.get(r, 0) == c and c)
            score -= abs(sum(byround.values()) - sum(ek.values()))
            if score > best_score:
                best, best_score = s, score
        if best:
            actor_to_subject[cr_actor] = best
            used.add(best)
    # fall back to team-name-only actors (no per-player attribution possible)
    player_by_subject = {
        p["subject"]: {
            "subject": p["subject"],
            "agent_uuid": p.get("agent_uuid"),
            "character_net_guid": p.get("character_net_guid"),
            "player_state_guid": p.get("actor_net_guid"),
            "team": None,
        }
        for p in players
    }
    for cr_actor, s in actor_to_subject.items():
        if s in player_by_subject and actor_team.get(cr_actor):
            player_by_subject[s]["team"] = actor_team[cr_actor]

    # teams fallback: spawn clustering on round 1
    _teams_from_spawns(export_dir, player_by_subject, char2subj, rounds, order)

    # Damage attribution. Every CombatReport interaction carries the region damage the
    # actor dealt and the subject who took it, so damage splits into damage dealt to
    # opponents, damage a player took from their own action (their utility, or the spike
    # detonation the export records as a self kill) and damage with no subject at all.
    # ADR is the opponents-only total per round, which is the published reference's
    # definition; summing the raw region damage (the earlier behaviour) counted self
    # damage and overstated ADR for anyone damaged by their own utility or the spike.
    self_damage: dict[str, float] = {s: 0.0 for s in subjects}
    unattributed_damage: dict[str, float] = {s: 0.0 for s in subjects}
    opp_by_actor_round: dict[tuple, float] = {}
    damage_taken: dict[str, float] = {s: 0.0 for s in subjects}
    damage_to_opp: dict[str, dict[str, float]] = {s: {} for s in subjects}
    for cr_actor, by_round in per_actor_round.items():
        for n in by_round:
            opp_by_actor_round.setdefault((cr_actor, n), 0.0)
    for (cr_actor, n, _mi, _ki), v in region_ki.items():
        src = actor_to_subject.get(cr_actor)
        if not src:
            continue
        opp = ki_subject.get((cr_actor, n, _mi, _ki))
        if not opp:
            unattributed_damage[src] += v
        elif opp == src:
            self_damage[src] += v
        else:
            damage_to_opp[src][opp] = damage_to_opp[src].get(opp, 0.0) + v
            opp_by_actor_round[(cr_actor, n)] = opp_by_actor_round.get((cr_actor, n), 0.0) + v
    for src, bucket in damage_to_opp.items():
        for opp, v in bucket.items():
            if opp in damage_taken:
                damage_taken[opp] += v
    cr_validation["damage_self_total"] = round(sum(self_damage.values()), 1)
    cr_validation["damage_unattributed_total"] = round(sum(unattributed_damage.values()), 1)

    # money + score
    _apply_money_score(fields, ps2subj, player_by_subject, rounds)

    # ability casts
    _apply_ability_casts(fields, ps2subj, player_by_subject, char2subj)

    # ults
    for u in ults:
        s = char2subj.get(int(u["who"])) if u["who"] is not None else None
        if s in player_by_subject:
            player_by_subject[s].setdefault("_ults", 0)
            player_by_subject[s]["_ults"] += 1

    # positions
    char_guids = {int(c) for c in char2subj}
    positions = Positions(export_dir, char_guids)

    # ---- per round, resolve deaths ----------------------------------------
    by_round_deaths: dict[int, list[dict]] = {}
    for d in deaths:
        r = _round_of(rounds, d["t"])
        if not r:
            continue
        vs = char2subj.get(int(d["victim_char"])) if d["victim_char"] is not None else None
        ks = char2subj.get(int(d["killer_char"])) if d["killer_char"] is not None else None
        rec = {"t": d["t"], "victim": vs, "killer": ks,
               "victim_char": d["victim_char"], "killer_char": d["killer_char"],
               "suicide": d["victim_char"] == d["killer_char"]}
        by_round_deaths.setdefault(r, []).append(rec)
    for r in by_round_deaths:
        by_round_deaths[r].sort(key=lambda x: x["t"] or 0)

    # first blood / entry + trades + isolation
    for r, ds_ in by_round_deaths.items():
        for i, d in enumerate(ds_):
            d["is_first_blood"] = i == 0
            victim = d["victim"]
            killer = d["killer"]
            # trade: killer dies within window after this death, same round
            d["traded"] = False
            if killer:
                for later in ds_[i + 1:]:
                    if later["victim"] == killer and (later["t"] or 0) - (d["t"] or 0) <= TRADE_WINDOW_MS:
                        d["traded"] = True
                        break
            # isolation: nearest living teammate at death time
            need_pos = positions.at(int(d["victim_char"]), int(d["t"])) if d["victim_char"] else None
            victim_team = player_by_subject.get(victim, {}).get("team")
            nearest = None
            for s in subjects:
                if s == victim:
                    continue
                if player_by_subject[s].get("team") != victim_team:
                    continue
                if _died_before(by_round_deaths.get(r, []), s, d["t"]):
                    continue
                cg = player_by_subject[s].get("character_net_guid")
                if not cg:
                    continue
                dist = _dist(need_pos, positions.at(int(cg), int(d["t"])))
                if dist is not None and (nearest is None or dist < nearest):
                    nearest = dist
            d["nearest_teammate_cm"] = round(nearest) if nearest is not None else None
            d["isolated"] = (nearest is None) or (nearest > ISO_DISTANCE_CM)
            # bait: untraded and a living teammate was near the killer
            d["bait"] = False
            if not d["traded"] and killer and d["killer_char"]:
                kpos = positions.at(int(d["killer_char"]), int(d["t"]))
                for s in subjects:
                    if s == victim or player_by_subject[s].get("team") != victim_team:
                        continue
                    if _died_before(by_round_deaths.get(r, []), s, d["t"]):
                        continue
                    cg = player_by_subject[s].get("character_net_guid")
                    if not cg:
                        continue
                    dd = _dist(kpos, positions.at(int(cg), int(d["t"])))
                    if dd is not None and dd <= ISO_DISTANCE_CM:
                        d["bait"] = True
                        break

    # spike evidence: only defenders defuse, which names the sides for that half
    switch_ms = rounds[order[0]].get("switch_teams_ms") if order else None
    defusers = _spike_defusers(fields)
    for sp in spikes:
        if sp["kind"] != "defused" or sp.get("t") is None:
            continue
        ch = _defuser_at(defusers, rounds, int(sp["t"]))
        team = player_by_subject.get(char2subj.get(ch), {}).get("team") if ch else None
        if team:
            sp["defuser_team"] = team
    excluded = _untrusted_side_rounds(order)
    sides_by_round = _round_sides(order, rounds, switch_ms, spikes, excluded)
    overtime_rounds = excluded if len(order) > REGULATION_ROUNDS else set()

    # round winners
    for r in order:
        ds_ = by_round_deaths.get(r, [])
        rounds[r]["sides"] = sides_by_round.get(r)
        if r in overtime_rounds:
            rounds[r]["overtime"] = True
        winner, source = _round_winner(rounds[r], ds_, player_by_subject, spikes,
                                       sides_by_round.get(r))
        rounds[r]["winner"] = winner
        if source:
            rounds[r]["winner_source"] = source
        rounds[r]["deaths"] = [
            {"t": d["t"], "victim": d["victim"], "killer": d["killer"],
             "victim_team": player_by_subject.get(d["victim"], {}).get("team"),
             "killer_team": player_by_subject.get(d["killer"], {}).get("team"),
             "first_blood": d.get("is_first_blood", False),
             "traded": d.get("traded", False),
             "isolated": d.get("isolated", False),
             "bait": d.get("bait", False),
             "suicide": d.get("suicide", False)}
            for d in ds_
        ]
        rounds[r]["spikes"] = [s for s in spikes
                               if rounds[r]["start_ms"] is not None
                               and s["t"] >= rounds[r]["start_ms"]
                               and (rounds[r]["end_ms"] is None or s["t"] < rounds[r]["end_ms"])]
    team_actors, winner_overrides = _apply_round_winners(rounds, order, fields)
    economy_by_round = _team_economy(fields, team_actors, rounds, order)
    for _n in order:
        rounds[_n]["economy"] = economy_by_round.get(_n) or {}

    # spike detonation / defusal outrank the BaseTeamState timing correlation
    spike_overrides = 0
    for r in order:
        kinds = [s["kind"] for s in rounds[r]["spikes"]]
        sides = sides_by_round.get(r)
        want = None
        if sides and "exploded" in kinds:
            want = sides["attacker"]
        elif "defused" in kinds:
            for sp in rounds[r]["spikes"]:
                if sp["kind"] == "defused" and sp.get("defuser_team"):
                    want = sp["defuser_team"]
                    break
            if want is None and sides:
                want = sides["defender"]
        if want and rounds[r].get("winner") != want:
            rounds[r]["winner"] = want
            rounds[r]["winner_source"] = "spike"
            spike_overrides += 1

    # an undecided round must say so explicitly instead of leaving the source blank
    for r in order:
        if rounds[r].get("winner") is None:
            rounds[r]["winner_source"] = "unknown"

    # ---- per player aggregates -------------------------------------------
    out_players = []
    h2h = {s: {} for s in subjects}
    entry_by_subject = _entry_duels(rounds, order)
    kills_by_subject, deaths_by_subject = _death_credits(deaths, char2subj)
    # Deaths a player dealt to themselves: the export records the spike detonation as a
    # death whose killer and victim are the same actor, so it stays a death like any
    # other (the event timeline says it happened) and is counted separately so the
    # difference from a kill/death aggregate that drops it can be explained. A death
    # with no killer actor is not self-inflicted; no such death exists in the reference
    # set, and the guard keeps the arithmetic total either way.
    self_deaths: dict[str, int] = {}
    for d in deaths:
        kc, vc = d["killer_char"], d["victim_char"]
        if kc is None or vc is None or kc != vc:
            continue
        sd_subject = char2subj.get(int(vc) or -1)
        if sd_subject is not None:
            self_deaths[sd_subject] = self_deaths.get(sd_subject, 0) + 1
    for s in subjects:
        p = player_by_subject[s]
        kills = kills_by_subject.get(s, 0)
        dths = deaths_by_subject.get(s, 0)
        dmg = 0.0
        hits = 0.0
        assists = 0
        head_damage = 0.0
        body_damage = 0.0
        other_damage = 0.0
        headshot_kills = 0
        wallbangs = 0
        for cr_actor, subj in actor_to_subject.items():
            if subj != s:
                continue
            for n, a in per_actor_round.get(cr_actor, {}).items():
                dmg += a["damage"]
                hits += a["hits"]
                assists += a["assists"]
                head_damage += a["head_damage"]
                body_damage += a["body_damage"]
                other_damage += a["other_damage"]
                headshot_kills += a["headshot_kills"]
                wallbangs += int(a.get("wallbang_damage", 0))
        my_deaths = [d for d in deaths if char2subj.get(int(d["victim_char"]) or -1) == s]
        tracked = [d for r in by_round_deaths.values() for d in r if d["victim"] == s]
        traded = sum(1 for d in tracked if d.get("traded"))
        isolated = sum(1 for d in tracked if d.get("isolated"))
        baits = sum(1 for d in tracked if d.get("bait"))
        entry_kills = sum(1 for r in by_round_deaths.values()
                          for d in [r[0]] if d["killer"] == s and not d.get("suicide"))
        entry_deaths = sum(1 for r in by_round_deaths.values()
                           if r and r[0]["victim"] == s)
        # multikills + clutches + KAST
        multi = {2: 0, 3: 0, 4: 0, 5: 0}
        clutch_wins = 0
        clutch_attempts = 0
        kast_rounds = 0
        per_round_rows = []
        for r in order:
            ds_ = by_round_deaths.get(r, [])
            rk = [d for d in ds_ if d["killer"] == s and not d.get("suicide")]
            rd = [d for d in ds_ if d["victim"] == s]
            kc = len(rk)
            if kc >= 2:
                multi[min(kc, 5)] = multi.get(min(kc, 5), 0) + 1
            # clutch
            team = player_by_subject[s].get("team")
            alive_after = _team_alive(by_round_deaths.get(r, []), subjects, player_by_subject,
                                      team, at_time=None, s=s)
            if alive_after == 1 and rounds[r]["winner"] == team:
                clutch_attempts += 1
                if kc > 0:
                    clutch_wins += 1
            elif alive_after == 1:
                clutch_attempts += 1
            # KAST — a kill, an assist, survival, or being traded all count
            assists_in_round = _round_assists(per_actor_round, actor_to_subject, s, r)
            got_kill = kc > 0
            got_assist = assists_in_round > 0
            was_traded = any(d["victim"] == s and d.get("traded") for d in ds_)
            survived = len(rd) == 0
            kast_credited = got_kill or got_assist or survived or was_traded
            if kast_credited:
                kast_rounds += 1
            for d in ds_:
                if d["killer"] == s and d["victim"] and not d.get("suicide"):
                    h2h[s][d["victim"]] = h2h[s].get(d["victim"], 0) + 1
            per_round_rows.append({
                "round": r,
                "kills": kc,
                "deaths": len(rd),
                "assists": assists_in_round,
                "damage": (lambda v: None if v is None else round(v, 1))(
                    _round_damage(per_actor_round, actor_to_subject, s, r)),
                "damage_opponents": (lambda v: None if v is None else round(v, 1))(
                    _round_opp_damage(opp_by_actor_round, actor_to_subject, s, r)),
                "money": player_by_subject[s].get("_money_by_round", {}).get(r),
                "first_blood": any(d.get("is_first_blood") for d in rk),
                "first_death": any(d.get("is_first_blood") for d in rd),
                "traded": any(d.get("traded") for d in rd),
                "clutch": alive_after == 1 and rounds[r]["winner"] == team,
                "kast": kast_credited,
                "hs_kills": _round_hs_kills(per_actor_round, actor_to_subject, s, r),
            })
        score = p.get("_score")
        opp_total = sum(damage_to_opp.get(s, {}).values())
        out_players.append({
            "subject": s,
            "agent_uuid": p.get("agent_uuid"),
            "team": p.get("team"),
            "character_net_guid": p.get("character_net_guid"),
            "player_state_guid": p.get("player_state_guid"),
            "stats": {
                "kills": kills,
                "deaths": dths,
                "assists": assists,
                "kd": round(kills / dths, 2) if dths else float(kills),
                "kast_pct": round(100.0 * kast_rounds / n_rounds, 1) if n_rounds else None,
                "damage_dealt": round(dmg, 1),
                "damage_taken": round(damage_taken.get(s, 0.0), 1),
                "damage_to_opponents": {k: round(v, 1) for k, v in
                                        sorted(damage_to_opp.get(s, {}).items(), key=lambda kv: -kv[1])},
                "adr": round(opp_total / n_rounds, 1) if n_rounds else None,
                "self_damage": round(self_damage.get(s, 0.0), 1),
                "damage_unattributed": round(unattributed_damage.get(s, 0.0), 1),
                "self_deaths": self_deaths.get(s, 0),
                "hits": round(hits, 1),
                "head_damage": round(head_damage, 1),
                "body_damage": round(body_damage, 1),
                "other_damage": round(other_damage, 1),
                "headshot_kills": headshot_kills,
                "hs_pct": round(100.0 * headshot_kills / kills, 1) if kills else None,
                "head_damage_pct": round(100.0 * head_damage / dmg, 1) if dmg else None,
                "wallbangs": wallbangs,
                "entry_kills": entry_kills,
                "entry_deaths": entry_deaths,
                "traded_deaths": traded,
                "traded_pct": round(100.0 * traded / len(tracked), 1) if tracked else None,
                "isolated_deaths": isolated,
                "isolated_pct": round(100.0 * isolated / len(tracked), 1) if tracked else None,
                "bait_pct": round(100.0 * baits / len(tracked), 1) if tracked else None,
                "multikills": {k: v for k, v in multi.items() if v},
                "clutch_attempts": clutch_attempts,
                "clutch_wins": clutch_wins,
                "clutch_pct": round(100.0 * clutch_wins / clutch_attempts, 1) if clutch_attempts else None,
                "ults_used": p.get("_ults", 0),
                "ability_casts": p.get("_ability_casts"),
                "avg_loadout_money": None,
                "score": score,
                "acs": round(score / n_rounds, 1) if (score and n_rounds) else None,
            },
            "per_round": per_round_rows,
            "stats_by_side": _side_split(per_round_rows, p.get("team"), rounds),
            "stats_by_buy": _buy_split(per_round_rows, p.get("team"), rounds),
            "economy": _player_economy(per_round_rows, p.get("team"), rounds),
            "entry": entry_by_subject.get(s) or {**_blank_entry(), "win_pct": None},
            "rating_by_round": _round_consistency(per_round_rows),
            "clean_rounds": _clean_rounds(per_round_rows),
        })

    side_rounds = {t: {"attack": 0, "defend": 0} for t in TEAMS}
    for r in order:
        sd = sides_by_round.get(r)
        if sd:
            side_rounds.setdefault(sd["attacker"], {"attack": 0, "defend": 0})["attack"] += 1
            side_rounds.setdefault(sd["defender"], {"attack": 0, "defend": 0})["defend"] += 1
    overview = _overview(manifest, players, players_out=out_players, n_rounds=n_rounds,
                         rounds=rounds, order=order, cr_validation=cr_validation,
                         deaths=deaths, team_actors=team_actors,
                         winner_overrides=winner_overrides, spike_overrides=spike_overrides,
                         side_rounds=side_rounds)
    insights = _insights(out_players, rounds, order, h2h, overview)
    return {
        "version": STATS_VERSION,
        "replay_id": replay_id,
        "generated_at": int(time.time() * 1000),
        "overview": overview,
        "players": out_players,
        "rounds": [rounds[r] for r in order],
        "head_to_head": h2h,
        "insights": insights,
        "notes": NOTES,
        "validation": cr_validation,
    }


def _round_damage(per, mapping, subject, r) -> float | None:
    """Damage for one player in one round. None when that round has no
    CombatReport entry at all (the struct does not record every round for
    every player, so a missing round must not be shown as 0 damage)."""
    found = False
    total = 0.0
    for cr_actor, subj in mapping.items():
        if subj == subject:
            cell = per.get(cr_actor, {}).get(r)
            if cell is not None:
                found = True
                total += cell.get("damage", 0.0)
    return total if found else None


def _round_opp_damage(opp_by_actor_round: dict, mapping: dict, subject, r) -> float | None:
    """Damage a player dealt to opponents in one round. None when that round has no
    CombatReport entry at all, mirroring _round_damage.

    This is the per-round input of ADR, so it excludes the damage a player dealt to
    themselves; the raw total is the row's 'damage'.
    """
    found = False
    total = 0.0
    for cr_actor, subj in mapping.items():
        if subj == subject:
            key = (cr_actor, r)
            if key in opp_by_actor_round:
                found = True
                total += opp_by_actor_round[key]
    return total if found else None


def _round_assists(per, mapping, subject, r) -> int:
    """Assists credited to one player in one round (0 when the round has no
    CombatReport entry for that player)."""
    total = 0
    for cr_actor, subj in mapping.items():
        if subj == subject:
            cell = per.get(cr_actor, {}).get(r)
            if cell is not None:
                total += int(cell.get("assists", 0))
    return total


ECO_MAX_ALV = 3000.0
FORCE_MAX_ALV = 5000.0
BUY_KEYS = ("pistol", "eco", "force", "full", "unknown")


def classify_buy(alv) -> str:
    """Bucket a team's per-round average loadout value into a buy class.

    Our own convention (no published standard was reachable, see NOTES): a save
    or eco round stays under 3000, a force buy fills 3000-5000, and 5000 or
    more is a full buy. ``None`` means the value is unavailable, not zero.
    """
    if alv is None:
        return "unknown"
    if alv < ECO_MAX_ALV:
        return "eco"
    if alv < FORCE_MAX_ALV:
        return "force"
    return "full"


def _pistol_rounds(order, rounds) -> set:
    """The first round of each half — a pistol round by structure, not by value."""
    out = set()
    if not order:
        return out
    out.add(order[0])
    switch = (rounds.get(order[0]) or {}).get("switch_teams_ms")
    if switch is not None:
        after = [n for n in order if (rounds[n].get("start_ms") or 0) > switch]
        if after:
            out.add(min(after))
    return out


def _team_economy(fields, team_actors, rounds, order) -> dict:
    """Per-round team gear value from the team state ({round: {team: cell}}).

    ``BaseTeamState.LoadoutValue`` is the team total and ``AverageLoadoutValue``
    the per-player value on Riot's accounting scale (armour and abilities
    included); the export writes exactly one pair per team per round at the
    round start. The team state actor is resolved through ``team_actors`` (the
    actor->team map the round-winner pass derives), so a replay without that map
    yields no economy rows rather than a guessed team.
    """
    out = {}
    if not team_actors:
        return out
    rows = fields.to_pylist() if hasattr(fields, "to_pylist") else fields
    for r in rows:
        if "BaseTeamState" not in (r["group_path"] or ""):
            continue
        name = r["field_name"]
        if name not in ("LoadoutValue", "AverageLoadoutValue"):
            continue
        team = team_actors.get(int(r["actor_net_guid"] or 0))
        if not team:
            continue
        n = _round_of(rounds, r["time_ms"])
        if n is None:
            continue
        val = _num(r)
        if val is None:
            continue
        cell = out.setdefault(n, {}).setdefault(team, {})
        if name == "LoadoutValue":
            cell["loadout_value"] = int(val)
        else:
            cell["alv"] = float(val)
    pistols = _pistol_rounds(order, rounds)
    for n, teams in out.items():
        for cell in teams.values():
            cell["alv"] = cell.get("alv")
            cell["loadout_value"] = cell.get("loadout_value")
            cell["buy"] = "pistol" if n in pistols else classify_buy(cell["alv"])
    return out


def _round_hs_kills(per, mapping, subject, r) -> int:
    """Headshot kills credited to one player in one round (0 when the round has
    no CombatReport entry for that player)."""
    total = 0
    for cr_actor, subj in mapping.items():
        if subj == subject:
            cell = per.get(cr_actor, {}).get(r)
            if cell is not None:
                total += int(cell.get("headshot_kills", 0))
    return total


def _blank_side() -> dict:
    return {"rounds": 0, "kills": 0, "deaths": 0, "assists": 0, "damage": 0.0,
            "kast_rounds": 0, "hs_kills": 0, "entry_kills": 0, "entry_deaths": 0}


def _side_split(per_round_rows: list[dict], team: str | None, rounds: dict) -> dict:
    """Per-player aggregates split by the side the player's team played.

    ``attack``/``defend`` count only rounds the player's team actually played on
    that side; a round whose ``sides`` could not be derived is left out of both
    blocks rather than guessed. Percentages follow this module's 0-100
    convention, and rates stay ``None`` when a side was never played, so "no
    data" never renders as a real 0.
    """
    out = {"attack": _blank_side(), "defend": _blank_side()}
    if not team:
        return _finish_sides(out)
    for row in per_round_rows:
        sides = (rounds.get(row.get("round")) or {}).get("sides") or {}
        if sides.get("attacker") == team:
            block = out["attack"]
        elif sides.get("defender") == team:
            block = out["defend"]
        else:
            continue
        _add_row(block, row)
    return _finish_sides(out)


def _finish_sides(out: dict) -> dict:
    for block in out.values():
        n = block["rounds"]
        block["damage"] = round(block["damage"], 1)
        if not n:
            block.update({"kd": None, "adr": None, "kast_pct": None, "hs_pct": None})
            continue
        block["kd"] = (round(block["kills"] / block["deaths"], 2) if block["deaths"]
                       else float(block["kills"]))
        block["adr"] = round(block["damage"] / n, 1)
        block["kast_pct"] = round(100.0 * block["kast_rounds"] / n, 1)
        block["hs_pct"] = (round(100.0 * block["hs_kills"] / block["kills"], 1)
                           if block["kills"] else None)
    return out


def _add_row(block: dict, row: dict) -> None:
    """Accumulate one per-round row into an aggregate block (one credit rule)."""
    block["rounds"] += 1
    block["kills"] += row.get("kills") or 0
    block["deaths"] += row.get("deaths") or 0
    block["assists"] += row.get("assists") or 0
    block["hs_kills"] += row.get("hs_kills") or 0
    if row.get("damage_opponents") is not None:
        block["damage"] += row["damage_opponents"]
    if row.get("kast"):
        block["kast_rounds"] += 1
    if row.get("first_blood"):
        block["entry_kills"] += 1
    if row.get("first_death"):
        block["entry_deaths"] += 1


def _blank_entry() -> dict:
    return {"duels": 0, "wins": 0, "first_deaths": 0}


def _entry_duels(rounds: dict, order: list) -> dict:
    """Opening-duel share taken from each round's first elimination.

    The round's first death is the entry duel. The killer (when one is recorded
    and it is not the victim) wins it; the victim loses it. A first death with no
    killer — a spike explosion or the environment — still counts a lost duel for
    the victim, because being the round's first death is a fact, but credits no
    win to anybody. ``win_pct`` follows this module's 0-100 convention and is
    ``None`` when the player was never in an opening duel.
    """
    out: dict = {}
    for r in order:
        deaths = (rounds.get(r) or {}).get("deaths") or []
        if not deaths:
            continue
        first = deaths[0]
        victim = first.get("victim")
        killer = first.get("killer")
        if victim:
            out.setdefault(victim, _blank_entry())["duels"] += 1
            out[victim]["first_deaths"] += 1
        if killer and killer != victim:
            out.setdefault(killer, _blank_entry())["duels"] += 1
            out[killer]["wins"] += 1
    for block in out.values():
        block["win_pct"] = (round(100.0 * block["wins"] / block["duels"], 1)
                            if block["duels"] else None)
    return out


def _round_score(row: dict) -> float:
    """Transparent per-round index: 2 points per kill + damage / 200.

    Damage is the damage dealt to opponents, the same basis as ADR, so a round the
    player damaged only themselves cannot read as a good round. This is this project's
    own scale, NOT an industry rating, and exists only to compare a player's rounds
    with each other (see ``rating_by_round``).
    """
    return 2.0 * (row.get("kills") or 0) + (row.get("damage_opponents") or 0.0) / 200.0


def _round_consistency(per_round_rows: list) -> dict:
    """Mean and spread of the per-round index across the match.

    Every value is ``None`` when there are no rounds; ``stdev`` is the
    population stdev over the player's own rounds.
    """
    scores = sorted(_round_score(row) for row in per_round_rows)
    n = len(scores)
    if not n:
        return {"mean": None, "stdev": None, "median": None,
                "best": None, "worst": None, "n": 0}
    mean = sum(scores) / n
    var = sum((x - mean) ** 2 for x in scores) / n
    return {"mean": round(mean, 2), "stdev": round(math.sqrt(var), 2),
            "median": round(median(scores), 2),
            "best": round(scores[-1], 2), "worst": round(scores[0], 2), "n": n}


def _clean_rounds(per_round_rows: list) -> int:
    """Rounds where the player got at least one kill and did not die."""
    return sum(1 for row in per_round_rows
               if (row.get("kills") or 0) > 0 and not (row.get("deaths") or 0))


def _buy_split(per_round_rows, team, rounds) -> dict:
    """Per-player aggregates split by the buy class of the player's own team.

    A round whose economy (or team) is unknown lands in the ``unknown`` block
    rather than being dropped, so the block totals always add up to the match.
    """
    out = {k: _blank_side() for k in BUY_KEYS}
    if not team:
        return _finish_sides(out)
    for row in per_round_rows:
        eco = (rounds.get(row.get("round")) or {}).get("economy") or {}
        buy = (eco.get(team) or {}).get("buy") or "unknown"
        _add_row(out.setdefault(buy, _blank_side()), row)
    return _finish_sides(out)


def _player_economy(per_round_rows, team, rounds) -> dict:
    """Start money and the buy mix a player actually played in.

    ``avg_start_money`` averages only rounds with a reported start money (an
    empty set stays ``None``); ``team_avg_loadout_value`` is the average of the
    player's *team* gear value, a team figure rather than this player's own
    equipment.
    """
    money = [row["money"] for row in per_round_rows if row.get("money") is not None]
    mix = {k: 0 for k in BUY_KEYS}
    alv = []
    for row in per_round_rows:
        cell = ((rounds.get(row.get("round")) or {}).get("economy") or {}).get(team) or {}
        mix[cell.get("buy") or "unknown"] += 1
        if cell.get("alv") is not None:
            alv.append(cell["alv"])
    return {
        "rounds": len(per_round_rows),
        "rounds_with_money": len(money),
        "avg_start_money": round(sum(money) / len(money), 1) if money else None,
        "rounds_with_loadout_value": len(alv),
        "team_avg_loadout_value": round(sum(alv) / len(alv), 1) if alv else None,
        "buy_mix": mix,
    }


def _economy_overview(rounds, order) -> dict:
    """Per-team buy mix and round-win rate per buy class."""
    out = {t: {"rounds": 0, "avg_alv": None, "buy_mix": {k: 0 for k in BUY_KEYS},
               "wins_by_buy": {k: 0 for k in BUY_KEYS},
               "win_pct_by_buy": {k: None for k in BUY_KEYS}} for t in TEAMS}
    alv = {t: [] for t in TEAMS}
    for n in order:
        r = rounds.get(n) or {}
        for t in TEAMS:
            cell = (r.get("economy") or {}).get(t)
            if not cell:
                continue
            buy = cell.get("buy") or "unknown"
            out[t]["rounds"] += 1
            out[t]["buy_mix"][buy] += 1
            if r.get("winner") == t:
                out[t]["wins_by_buy"][buy] += 1
            if cell.get("alv") is not None:
                alv[t].append(cell["alv"])
    for t in TEAMS:
        for k in BUY_KEYS:
            n = out[t]["buy_mix"][k]
            out[t]["win_pct_by_buy"][k] = (
                round(100.0 * out[t]["wins_by_buy"][k] / n, 1) if n else None)
        out[t]["avg_alv"] = round(sum(alv[t]) / len(alv[t]), 1) if alv[t] else None
    return out


def _died_before(deaths_in_round, subject, t) -> bool:
    for d in deaths_in_round:
        if d["victim"] == subject and (d["t"] or 0) <= (t or 0):
            return True
    return False


def _team_alive(deaths_in_round, subjects, player_by_subject, team, at_time=None, s=None) -> int:
    if not team:
        return 0
    alive = 0
    for x in subjects:
        if player_by_subject[x].get("team") != team:
            continue
        if at_time is None:
            if any(d["victim"] == x for d in deaths_in_round):
                continue
        else:
            if _died_before(deaths_in_round, x, at_time):
                continue
        alive += 1
    return alive


def _spike_defusers(fields: list[dict]) -> list[tuple[int, int]]:
    """(time_ms, character_net_guid) rows where a player was actively defusing.

    TimedBomb.CurrentDefuser carries the defuser's character guid while the
    defusal runs (cleared with 0 when it stops). Only the defending team can
    defuse, so this names the defenders for that half of the match.
    """
    out: list[tuple[int, int]] = []
    for r in fields:
        gp = r.get("group_path") or ""
        if "TimedBomb" not in gp or r.get("field_name") != "CurrentDefuser":
            continue
        ch = _num(r)
        t = r.get("time_ms")
        if ch and t is not None:
            out.append((int(t), int(ch)))
    return out


def _defuser_at(defusers: list[tuple[int, int]], rounds: dict, t_defuse: int,
                slack_ms: int = 2000) -> int | None:
    """Character guid of the defusal attempt that completed at t_defuse.

    CurrentDefuser is set while the bar is held, which can be seconds before the
    completion event (m1 R12: 8.6 s, m1 R14: 7.0 s), so attempts are matched by
    round rather than by a tight time window: the last attempt in the same round
    at or before the completion (small slack for row/event ordering).
    """
    n = _round_of(rounds, t_defuse)
    if n is None:
        return None
    best_t: int | None = None
    best_ch: int | None = None
    for t_def, ch in defusers:
        if _round_of(rounds, t_def) != n or t_def > t_defuse + slack_ms:
            continue
        if best_t is None or t_def > best_t:
            best_t, best_ch = t_def, ch
    return best_ch


def _other_team(team: str) -> str:
    return TEAMS[1] if team == TEAMS[0] else TEAMS[0]


def _untrusted_side_rounds(order: list, regulation: int = REGULATION_ROUNDS) -> set:
    """Round numbers the two-half side model cannot describe, so they carry no sides.

    The model assumes one half-time swap inside a 24-round regulation match.
    Anything past regulation is overtime, where sides swap *every* round and the
    single swap no longer describes them, so those rounds are left out. A side is
    never guessed: excluded rounds simply have no ``sides``.
    """
    return set(order[regulation:]) if len(order) > regulation else set()


def _round_sides(order: list, rounds: dict, switch_ms, spikes: list,
                 exclude: set | None = None) -> dict:
    """Attacking/defending team per round, from defusal evidence + the half swap.

    A defusal can only be performed by the defending team, so a defuser's team
    names the defenders for every round in that half; the other half swaps.
    Rounds in ``exclude`` (overtime, and any round the two-half model cannot
    place — see ``_untrusted_side_rounds``) are left without sides, both as a
    vote and in the output. Returns {} when no defusal happened in the export
    (sides stay unknown rather than being guessed).
    """
    exclude = exclude or set()
    votes: dict[str, dict[str, int]] = {"first": {}, "second": {}}
    for sp in spikes:
        team = sp.get("defuser_team")
        if not team:
            continue
        n = _round_of(rounds, sp.get("t"))
        if n is None or n in exclude:
            continue
        start = rounds[n].get("start_ms") or 0
        half = "first" if (switch_ms is None or start < switch_ms) else "second"
        votes[half][team] = votes[half].get(team, 0) + 1
    defender: dict[str, str | None] = {}
    for half in ("first", "second"):
        defender[half] = max(votes[half].items(), key=lambda kv: kv[1])[0] if votes[half] else None
    if defender["first"] is None and defender["second"] is None:
        return {}
    if defender["first"] is None:
        defender["first"] = _other_team(defender["second"])
    if defender["second"] is None:
        defender["second"] = _other_team(defender["first"])
    out = {}
    for n in order:
        if n in exclude:
            continue
        start = rounds[n].get("start_ms") or 0
        half = "first" if (switch_ms is None or start < switch_ms) else "second"
        d = defender[half]
        if d:
            out[n] = {"attacker": _other_team(d), "defender": d}
    return out


def _round_winner(round_info, deaths_in_round, player_by_subject, spikes,
                  sides=None) -> tuple[str | None, str | None]:
    """Return ``(winner, source)`` for a round.

    ``source`` names the evidence behind the winner so the payload never carries a
    winner without saying how it was decided: ``"spike"`` for a defusal or a
    detonation, ``"elimination"`` for a wipe. An undecided round is ``(None, None)``.
    """
    start = round_info.get("start_ms")
    end = round_info.get("end_ms")
    for sp in spikes:
        if start is not None and sp["t"] < start:
            continue
        if end is not None and sp["t"] >= end:
            continue
        if sp["kind"] == "defused":
            team = sp.get("defuser_team") or (sides or {}).get("defender")
            if team:
                return team, "spike"
        elif sp["kind"] == "exploded":
            # the bomb exploding wins the round for the attacking team, even
            # when players are still alive on both sides
            team = (sides or {}).get("attacker")
            if team:
                return team, "spike"
    # last team standing (a revived player can die twice, so count players,
    # not death rows)
    dead = {d["victim"] for d in deaths_in_round if d.get("victim")}
    alive_by_team: dict[str, int] = {}
    for s, p in player_by_subject.items():
        t = p.get("team")
        if not t:
            continue
        alive_by_team[t] = alive_by_team.get(t, 0) + (0 if s in dead else 1)
    # a mutual wipe (both teams at zero) has no last team standing: say so
    # instead of handing the round to whichever team TEAMS lists first
    if alive_by_team and alive_by_team.get("Blue") == 0 and alive_by_team.get("Red") == 0:
        return None, None
    for team in TEAMS:
        if alive_by_team.get(team) == 0 and alive_by_team:
            return _other_team(team), "elimination"
    return None, None


def _apply_round_winners(rounds: dict, order: list, fields: list) -> tuple[dict, int]:
    """BaseTeamState.Wins is 'untyped' in the export but its replication TIMING is
    one row per round end. Correlating the team actor that fires with the
    event-derived winner gives an exact team -> Blue/Red mapping, which then
    gives the winner for every round."""
    rows = []
    for r in fields:
        gp = r.get("group_path") or ""
        if "BaseTeamState" in gp and r.get("field_name") == "Wins":
            rows.append((r.get("time_ms"), int(r.get("actor_net_guid") or 0)))
    winner_actor: dict[int, int] = {}
    for t, a in rows:
        rd = _round_of(rounds, t)
        if rd is not None and t is not None:
            winner_actor.setdefault(rd, a)
    votes: dict[int, dict[str, int]] = {}
    for rd, a in winner_actor.items():
        w = rounds[rd].get("winner")
        if w in TEAMS:
            votes.setdefault(a, {}).setdefault(w, 0)
            votes[a][w] += 1
    actor_team = {a: max(v.items(), key=lambda kv: kv[1])[0] for a, v in votes.items()}
    changed = 0
    for rd, a in winner_actor.items():
        t = actor_team.get(a)
        if t:
            if rounds[rd].get("winner") != t:
                changed += 1
            rounds[rd]["winner"] = t
            rounds[rd]["winner_source"] = "teamstate_wins"
    return actor_team, changed


def _teams_from_spawns(export_dir, player_by_subject, char2subj, rounds, order):
    missing = [s for s, p in player_by_subject.items() if not p.get("team")]
    if not missing:
        return
    p = export_dir / "movement.parquet"
    if not p.exists():
        return
    first_start = rounds[order[0]]["start_ms"] if order else 0
    t = pq.read_table(p, columns=["time_ms", "character_net_guid", "pos_x"])
    tms = t.column("time_ms").to_pylist()
    gs = t.column("character_net_guid").to_pylist()
    xs = t.column("pos_x").to_pylist()
    acc: dict[int, list[float]] = {}
    for i, g in enumerate(gs):
        if tms[i] < first_start or tms[i] > first_start + 4000:
            continue
        if g in char2subj and xs[i] is not None:
            acc.setdefault(g, []).append(xs[i])
    if not acc:
        return
    meanx = {g: sum(v) / len(v) for g, v in acc.items()}
    if len(meanx) < 2:
        return
    mid = median(meanx.values())
    for g, x in meanx.items():
        s = char2subj.get(g)
        if s and s in player_by_subject and not player_by_subject[s].get("team"):
            player_by_subject[s]["team"] = "Blue" if x < mid else "Red"


def _apply_money_score(fields, ps2subj, player_by_subject, rounds):
    money_round_vals: dict[str, dict[int, list]] = {}
    scores: dict[str, int] = {}
    for r in fields:
        gp = r.get("group_path") or ""
        fn = r.get("field_name") or ""
        actor = int(r.get("actor_net_guid") or 0)
        if "MoneyManagement" in gp and fn in ("StartOfRoundMoney", "Money"):
            s = ps2subj.get(actor)
            if not s:
                continue
            rd = _round_of(rounds, r.get("time_ms"))
            if rd is None:
                continue
            money_round_vals.setdefault(s, {}).setdefault(rd, [])
            money_round_vals[s][rd].append((fn, _num(r)))
        elif "PlayerScoreComponent" in gp and fn == "Score":
            s = ps2subj.get(actor)
            if s:
                v = _num(r)
                if v is not None and (s not in scores or v > scores[s]):
                    scores[s] = int(v)
    for s, byround in money_round_vals.items():
        if s in player_by_subject:
            out = {}
            for rd, vals in byround.items():
                start = [v for (n, v) in vals if n == "StartOfRoundMoney" and v is not None]
                anyv = [v for (n, v) in vals if v is not None]
                pick = start[0] if start else (anyv[0] if anyv else None)
                if pick is not None:
                    out[rd] = int(pick)
            player_by_subject[s]["_money_by_round"] = out
    for s, v in scores.items():
        if s in player_by_subject:
            player_by_subject[s]["_score"] = v


def _apply_ability_casts(fields, ps2subj, player_by_subject, char2subj=None):
    re_ab = re.compile(r"AbilityCastsThisRound\[(\d+)\]\.([A-Za-z_]+)")
    casts: dict[tuple, dict] = {}
    for r in fields:
        gp = r.get("group_path") or ""
        if "Comp_AbilityStatisticsReplicator" not in gp:
            continue
        m = re_ab.search(r.get("field_name") or "")
        if not m:
            continue
        actor = int(r.get("actor_net_guid") or 0)
        idx, base = m.group(1), m.group(2).rstrip("_")
        if base in ("Player", "Effects"):
            continue
        c = casts.setdefault((actor, idx), {})
        c[base] = _val(r)
    identities: dict[str, set] = {}
    for (actor, idx), c in casts.items():
        s = ps2subj.get(actor) or (char2subj or {}).get(actor)
        if not s:
            continue
        ident = (c.get("Round"), c.get("Slot"), round(float(c["CastTime"]), 3)
                 if isinstance(c.get("CastTime"), (int, float)) else c.get("CastTime"))
        if all(v is None for v in ident):
            continue
        identities.setdefault(s, set()).add(ident)
    for s, idents in identities.items():
        if s in player_by_subject:
            player_by_subject[s]["_ability_casts"] = len(idents)


def _overview(manifest, players_in, players_out, n_rounds, rounds, order, cr_validation,
              deaths, team_actors=None, winner_overrides=0, spike_overrides=0, side_rounds=None):
    score = {}
    for t in TEAMS:
        score[t] = sum(1 for r in order if rounds[r].get("winner") == t)
    first_bloods = {}
    for p in players_out:
        first_bloods[p["subject"]] = sum(1 for r in p["per_round"] if r.get("first_blood"))
    return {
        "friendly_name": manifest.get("friendly_name"),
        "build": manifest.get("replay_build"),
        "replay_version": manifest.get("replay_version"),
        "duration_ms": manifest.get("duration_ms"),
        "level_name": (manifest.get("level_names_and_times") or [{}])[0].get("name"),
        "n_rounds": n_rounds,
        "score": score,
        "total_kills_events": len([d for d in deaths if d]),
        "player_count": len(players_in),
        "combat_report_validation": cr_validation,
        "team_state_actors": team_actors or {},
        "winner_overrides": winner_overrides,
        "spike_overrides": spike_overrides,
        "side_rounds": side_rounds or {},
        "economy": _economy_overview(rounds, order),
        "economy_thresholds": {"eco_max_alv": ECO_MAX_ALV, "force_max_alv": FORCE_MAX_ALV},
    }


def _insight(text: str, kind: str, detail: str | None = None, n: int | None = None) -> dict:
    """One insight record: the sentence, a plain-English ``detail`` with the
    supporting number, the ``n`` its claim rests on, and its ``kind``.

    ``kind`` is one of ``INSIGHT_KINDS``. A record whose claim is a
    rate/percentage must carry ``n >= MIN_RATE_SAMPLE``; that is enforced where
    the line is built (see ``_insights`` and its helpers), never here, so a
    malformed record still surfaces instead of being silently dropped.
    """
    return {"text": text, "detail": detail, "n": n, "kind": kind}


def _tracked_deaths(p: dict) -> int:
    """Deaths that carry isolation/bait evidence, i.e. the denominator of
    ``isolated_pct`` / ``bait_pct`` (a death in a round whose positions could
    not be resolved is excluded from both, so it is not part of the sample)."""
    return sum(int(row.get("deaths") or 0) for row in (p.get("per_round") or []))


def _side_insight(rounds: dict, order: list) -> dict | None:
    """Which half paid off, and which team led it (``kind`` ``side``).

    Reads ``rounds[].sides`` (derived from defusal evidence + the half swap) and
    ``rounds[].winner``. The line names the stronger half for the match and the
    team with the best record on it; both need at least ``MIN_RATE_SAMPLE``
    decided rounds, otherwise the line is dropped rather than reported off one
    round.
    """
    atk_r = {t: 0 for t in TEAMS}
    atk_w = {t: 0 for t in TEAMS}
    def_r = {t: 0 for t in TEAMS}
    def_w = {t: 0 for t in TEAMS}
    for n in order:
        r = rounds.get(n) or {}
        sd = r.get("sides") or {}
        attacker, defender, winner = sd.get("attacker"), sd.get("defender"), r.get("winner")
        if attacker not in TEAMS or defender not in TEAMS or winner not in TEAMS:
            continue
        atk_r[attacker] += 1
        def_r[defender] += 1
        if winner == attacker:
            atk_w[attacker] += 1
        elif winner == defender:
            def_w[defender] += 1
    total = sum(atk_r.values())
    if total < MIN_RATE_SAMPLE:
        return None
    atk_total = sum(atk_w.values())
    def_total = sum(def_w.values())
    if atk_total > def_total:
        side, wins = "attack", atk_total
    elif def_total > atk_total:
        side, wins = "defence", def_total
    else:
        side, wins = None, None
    block_r, block_w = (atk_r, atk_w) if side != "defence" else (def_r, def_w)
    cands = [t for t in TEAMS if block_r[t] >= MIN_RATE_SAMPLE]
    if not cands:
        return None
    t = max(cands, key=lambda x: block_w[x] / block_r[x])
    rate = round(100.0 * block_w[t] / block_r[t], 1)
    if side == "attack":
        text = (f"Attack won more rounds than defence ({wins}/{total}); {t} led on "
                f"attack with {atk_w[t]}/{atk_r[t]} ({rate}%).")
    elif side == "defence":
        text = (f"Defence won more rounds than attack ({wins}/{total}); {t} led on "
                f"defence with {def_w[t]}/{def_r[t]} ({rate}%).")
    else:
        text = (f"Attack and defence split the decided rounds ({atk_total}/{total}); "
                f"{t} led on attack with {atk_w[t]}/{atk_r[t]} ({rate}%).")
    return _insight(
        text, "side",
        detail=("sides come from defusal evidence plus the half-time swap; a round with "
                "no known side or winner is left out of both halves."),
        n=total)


def _economy_insight(overview: dict | None, order: list) -> dict | None:
    """Round-win share per buy class (``kind`` ``economy``).

    Counts are team-rounds from the per-round economy cells, so a round
    contributes one row per team and the two teams' buys are counted apart. A
    class is only reported once it has ``MIN_RATE_SAMPLE`` team-rounds.
    """
    econ = (overview or {}).get("economy") or {}
    frags = []
    used = 0
    for k in ("full", "force", "eco", "pistol"):
        r_ = sum(((econ.get(t) or {}).get("buy_mix") or {}).get(k, 0) for t in TEAMS)
        w_ = sum(((econ.get(t) or {}).get("wins_by_buy") or {}).get(k, 0) for t in TEAMS)
        if r_ < MIN_RATE_SAMPLE:
            continue
        label = "full-buy" if k == "full" else k
        frags.append(f"{label} {w_}/{r_} ({round(100.0 * w_ / r_, 1)}%)")
        used += r_
    if not frags:
        return None
    return _insight(
        "Round wins by buy class — " + ", ".join(frags) + ".",
        "economy",
        detail=("buy class is the team's average loadout value (eco below 3000, force "
                "3000-5000, full 5000 and above; pistol is the first round of each "
                "half); counts are team-rounds, so both teams appear in one round."),
        n=used)


def _consistency_insight(players_out: list) -> dict | None:
    """The steadiest and the most volatile player's per-round index (``kind``
    ``consistency``). Only players with at least ``MIN_RATE_SAMPLE`` rounds are
    compared; one candidate cannot be both, so the line needs two."""
    cands = []
    for p in players_out:
        rr = p.get("rating_by_round") or {}
        if (rr.get("n") or 0) >= MIN_RATE_SAMPLE and rr.get("stdev") is not None:
            cands.append((p, rr))
    if len(cands) < 2:
        return None
    calm = min(cands, key=lambda pr: pr[1]["stdev"])
    wild = max(cands, key=lambda pr: pr[1]["stdev"])
    if calm[0].get("subject") == wild[0].get("subject"):
        return None
    n = min(calm[1]["n"], wild[1]["n"])
    return _insight(
        f"{short(calm[0].get('subject'))} was the most consistent round to round "
        f"(mean {calm[1]['mean']}, stdev {calm[1]['stdev']}); "
        f"{short(wild[0].get('subject'))} the most volatile "
        f"(mean {wild[1]['mean']}, stdev {wild[1]['stdev']}).",
        "consistency",
        detail=("round index = 2 per kill + damage / 200 (this project's own scale, not "
                f"an industry rating); stdev is the population spread over the player's "
                f"{n} rounds."),
        n=n)


def _insights(players_out, rounds, order, h2h, overview) -> list[dict]:
    """Plain-English takeaways, each with the sample its claim rests on.

    Every record is ``{"text", "detail", "n", "kind"}`` with ``kind`` in
    ``INSIGHT_KINDS``; ``text`` names players by their 8-char subject id (the web
    UI swaps in agent names). A percentage claim is only emitted when its
    denominator is at least ``MIN_RATE_SAMPLE`` — one death never becomes
    "100%". Counts may be reported at any ``n``, but ``n`` is always set.
    """
    out: list[dict] = []
    n_rounds = len(order)

    ranked = sorted(players_out, key=lambda p: -(p["stats"].get("kills") or 0))
    if ranked and (ranked[0]["stats"].get("kills") or 0) > 0:
        top = ranked[0]
        s = top["stats"]
        deaths = s.get("deaths") or 0
        bits = []
        if deaths >= MIN_RATE_SAMPLE:
            bits.append(f"{s.get('kd')} K/D over {deaths} deaths")
        if n_rounds >= MIN_RATE_SAMPLE and s.get("adr") is not None:
            bits.append(f"ADR {s.get('adr')} over {n_rounds} rounds")
        out.append(_insight(
            f"{short(top['subject'])} leads with {s.get('kills')} kills.",
            "kills",
            detail=("; ".join(bits) + ".") if bits else None,
            n=(s.get("kills") or 0) + deaths))

    best_hs = [p for p in players_out
               if p["stats"].get("hs_pct") is not None
               and (p["stats"].get("kills") or 0) >= MIN_RATE_SAMPLE]
    if best_hs:
        p = max(best_hs, key=lambda p: p["stats"]["hs_pct"])
        s = p["stats"]
        out.append(_insight(
            f"Best headshot rate: {short(p['subject'])} at {s['hs_pct']}%.",
            "aim",
            detail=(f"{s.get('headshot_kills')} headshot kills of {s.get('kills')} kills; "
                    f"headshot kills come from CombatReport damage regions."),
            n=s.get("kills")))

    if players_out:
        p = max(players_out, key=lambda p: p["stats"].get("entry_kills") or 0)
        ek = p["stats"].get("entry_kills") or 0
        if ek:
            out.append(_insight(
                f"Most opening kills: {short(p['subject'])} ({ek}).",
                "entry",
                detail=(f"{ek} first-blood kills across {n_rounds} rounds; the opening "
                        f"duel is the round's first elimination."),
                n=ek))

    iso = [p for p in players_out
           if p["stats"].get("isolated_pct") is not None
           and _tracked_deaths(p) >= MIN_RATE_SAMPLE]
    if iso:
        p = max(iso, key=lambda p: p["stats"]["isolated_pct"])
        s = p["stats"]
        n = _tracked_deaths(p)
        out.append(_insight(
            f"Most isolated deaths: {short(p['subject'])} "
            f"({s['isolated_pct']}% of deaths).",
            "survival",
            detail=(f"{s.get('isolated_deaths')} of {n} deaths with no teammate within "
                    f"12m at the moment of death; a single death would read 100%."),
            n=n))

    bait = [p for p in players_out
            if p["stats"].get("bait_pct") is not None
            and _tracked_deaths(p) >= MIN_RATE_SAMPLE]
    if bait:
        p = max(bait, key=lambda p: p["stats"]["bait_pct"])
        s = p["stats"]
        n = _tracked_deaths(p)
        if s["bait_pct"]:
            out.append(_insight(
                f"Highest bait indicator: {short(p['subject'])} at {s['bait_pct']}%.",
                "survival",
                detail=(f"bait = died untraded while a living teammate was within 12m of "
                        f"the killer; {n} deaths as the sample."),
                n=n))

    clutches = sorted(players_out, key=lambda p: -(p["stats"].get("clutch_wins") or 0))
    if clutches and (clutches[0]["stats"].get("clutch_wins") or 0):
        p = clutches[0]
        s = p["stats"]
        attempts = s.get("clutch_attempts") or 0
        won = s.get("clutch_wins") or 0
        if attempts >= MIN_RATE_SAMPLE:
            text = (f"Most clutch rounds won: {short(p['subject'])} "
                    f"({won}/{attempts}).")
        else:
            text = (f"Most clutch rounds won: {short(p['subject'])} "
                    f"({won} clutch round{'s' if won != 1 else ''}).")
        out.append(_insight(
            text, "clutch",
            detail=(f"clutch = the last player alive in a round their team won; "
                    f"{attempts} clutch attempt{'s' if attempts != 1 else ''} in the match."),
            n=attempts))

    for ctx in (_side_insight(rounds, order),
                _economy_insight(overview, order),
                _consistency_insight(players_out)):
        if ctx is not None:
            out.append(ctx)
    return out


INSIGHT_KINDS = ("kills", "aim", "entry", "survival", "clutch", "side",
                 "economy", "consistency")
# Kinds whose record reports a rate/percentage and therefore needs a real sample.
RATE_INSIGHT_KINDS = frozenset({"aim", "survival", "side", "economy", "consistency"})
# No rate claim is emitted below this many events (a single death is not 100%).
MIN_RATE_SAMPLE = 3


def short(subject: str | None) -> str:
    return (subject or "?")[:8]
