"""Player Analysis: every ingested replay folded into a profile per player.

The join key is the ``subject`` GUID the export itself carries. It is account-level and repeats across
matches (probed 2026-10-18: one subject in 8 of the 9 distinct matches on this machine), which is why
a profile can span matches at all. There is **no Riot ID in a replay** - the account structure is
deliberately obfuscated and the one name field holds client profile names - so a display label is a
nickname kept in local settings and the payload says so.

Two layers, on purpose:

* the **fast layer** here reads the cached ``stats.json`` and ``metrics.json`` of each replay, so a
  profile is exactly what the replay pages show for those matches, with nothing recomputed;
* the **deep layer** (judge grades, death classes, drills) needs one parse of each match's field stream,
  so it is built on demand into ``data/players.json`` and reports its age rather than being recomputed
  on a page load.

Nothing here is cached as truth about a match: a missing number stays ``None`` and every pooled value
carries the ``n`` behind it.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import deaths as deaths_mod
import judge as judge_mod
import metrics as metrics_mod
import settings as settings_mod
import stats as stats_mod

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEEP_PATH = DATA_DIR / "players.json"
DEEP_VERSION = 1

BUY_ORDER = ("pistol", "eco", "force", "full", "unknown")
SIDES = ("attack", "defend")

# metric key -> the count that weights it when matches are pooled
AIM_WEIGHTED = (
    ("pre_aim_deg", "pre_aim_n"),
    ("reaction_ms", "reaction_n"),
    ("ttd_ms", "ttd_n"),
    ("counter_strafe_pct", "shots_classified"),
    ("first_eng_acc", "first_eng_n"),
    ("spray_spread_deg", "shots_classified"),
    ("spray_radius_cm", "shots_classified"),
    ("spray_ttk_ms", "shots_classified"),
    ("hs_kill_pct", "gun_hits"),
)
AIM_SUMS = ("shots", "hits", "gun_hits", "head_hits", "leg_hits", "util_hits", "wallbang_hits",
            "dmg", "kills", "sprays", "spray_hits", "engagements", "shots_classified")

NOTES = (
    "Player Analysis reads the cached stats and aim payloads of every ingested replay, so a number here "
    "is the same number the replay pages show for that match.",
    "The join key is the export's own subject id, which repeats across matches. A replay carries no Riot "
    "ID, so a label is a nickname kept on this machine and never an account name.",
    "Two replays of the same match (same match id) are counted once, and the copies are listed with the "
    "match rather than added again.",
    "A pooled number puts every round of every match in one pool; the mean of matches is the plain "
    "average of each match's own rate. They answer different questions, so both are published.",
    "Aim numbers are pooled by their own sample size, so a match with more duels moves them more, and "
    "every pooled number carries its n.",
    "The deep layer (judge grades, death classes and drills) parses each match's field stream, so it is "
    "built on demand and cached. When it is missing or older than the current matches the page says so "
    "instead of showing a stale grade.",
)

TEAMMATE_NOTES = (
    "A teammate here is a player who was on the same team as this subject in at least one deduped match.",
    "Rounds together counts the rounds where both players have a per-round row, so it is the evidence "
    "that the two were actually in the round.",
    "A round whose winner the export does not carry is counted and kept out of the win rate, never read "
    "as a loss.",
    "The list comes from the cached stats payloads alone; the labels are your own local nicknames.",
)


# ---- small helpers ---------------------------------------------------


def _r(v: Any, digits: int = 1) -> float | None:
    return None if v is None else round(float(v), digits)


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _pct(part: Any, whole: Any, digits: int = 1) -> float | None:
    if part is None or not whole:
        return None
    return round(100.0 * float(part) / float(whole), digits)


def _div(part: Any, whole: Any, digits: int = 2) -> float | None:
    if part is None or not whole:
        return None
    return round(float(part) / float(whole), digits)


def _mean(values: list[float | None]) -> float | None:
    keep = [float(v) for v in values if v is not None]
    return round(sum(keep) / len(keep), 2) if keep else None


def _weighted(pairs: list[tuple[Any, Any]], digits: int = 1) -> tuple[float | None, int]:
    """Weighted mean of a metric over matches, plus the total weight behind it."""
    num = 0.0
    den = 0
    for value, weight in pairs:
        if value is None or not weight:
            continue
        num += float(value) * float(weight)
        den += int(weight)
    return (round(num / den, digits) if den else None), den


def _sum_field(rows: list[dict], key: str) -> float | int | None:
    vals = [r.get(key) for r in rows if r.get(key) is not None]
    if not vals:
        return None
    total = sum(vals)
    return int(total) if all(isinstance(v, int) for v in vals) else round(float(total), 1)


def _finish_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    """Recompute the rates of a pooled side/buy bucket from its own sums."""
    rounds = bucket.get("rounds") or 0
    kills = bucket.get("kills")
    deaths = bucket.get("deaths")
    bucket["kd"] = _div(kills, deaths, 2)
    bucket["adr"] = _div(bucket.get("damage"), rounds, 1)
    bucket["kast_pct"] = _pct(bucket.get("kast_rounds"), rounds, 1)
    bucket["hs_pct"] = _pct(bucket.get("hs_kills"), kills, 1)
    if not rounds:
        for k in ("kd", "adr", "kast_pct", "hs_pct"):
            bucket[k] = None
    return bucket


def _add_bucket(into: dict[str, Any], src: dict[str, Any] | None) -> None:
    if not isinstance(src, dict):
        return
    for key in ("rounds", "kills", "deaths", "assists", "kast_rounds", "hs_kills",
                "entry_kills", "entry_deaths"):
        v = src.get(key)
        if v is not None:
            into[key] = (into.get(key) or 0) + int(v)
    dmg = src.get("damage")
    if dmg is not None:
        into["damage"] = round((into.get("damage") or 0.0) + float(dmg), 1)


# ---- the match set ---------------------------------------------------


def _matches(store) -> list[dict[str, Any]]:
    """Every ready replay, deduped by its own match id (newest copy first wins)."""
    seen: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for replay_id in store.ids():
        rec = store.get(replay_id) or {}
        summary = rec.get("summary") or {}
        if rec.get("status") != "ready" or not summary:
            continue
        match_id = summary.get("friendly_name") or replay_id
        if match_id in seen:
            seen[match_id]["duplicates"].append(replay_id)
            continue
        seen[match_id] = {
            "match_id": match_id,
            "replay_id": replay_id,
            "duplicates": [],
            "summary": summary,
        }
        order.append(match_id)
    return [seen[m] for m in order]


def _fingerprint(matches: list[dict[str, Any]]) -> str:
    parts = [f"{m['match_id']}:{m['replay_id']}" for m in matches]
    parts.append(f"stats{stats_mod.STATS_VERSION}")
    parts.append(f"metrics{metrics_mod.METRICS_VERSION}")
    parts.append(f"deep{DEEP_VERSION}")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _metrics_by_subject(store, replay_id: str) -> dict[str, dict[str, Any]]:
    try:
        payload = metrics_mod.build(store, replay_id)
    except Exception:
        return {}
    return {p.get("subject"): p for p in (payload.get("players") or [])}


# ---- the fast layer --------------------------------------------------


def _blank_head() -> dict[str, Any]:
    return {"matches": 0, "rounds": 0, "kills": 0, "deaths": 0, "assists": 0,
            "kast_rounds": 0, "opponent_damage": 0.0, "score": 0.0}


def _head_from_stats(st: dict[str, Any], per_round: list[dict]) -> dict[str, Any]:
    dmg = st.get("damage_to_opponents")
    total_dmg = sum(v for v in dmg.values() if isinstance(v, (int, float))) if isinstance(dmg, dict) else None
    return {
        "rounds": len(per_round) or None,
        "kills": st.get("kills"),
        "deaths": st.get("deaths"),
        "assists": st.get("assists"),
        "kast_rounds": sum(1 for r in per_round if r.get("kast")) or None,
        "opponent_damage": total_dmg,
        "score": st.get("score"),
    }


def _grade(share: float | None) -> str | None:
    """The judging engine's grade bands, applied to a pooled share of the team's points."""
    if share is None:
        return None
    for limit, letter in judge_mod.GRADE_BANDS:
        if share <= limit:
            return letter
    return "E"


def catalog(store) -> dict[str, Any]:
    """Every match and every player behind them, plus whose profile this probably is."""
    started = time.time()
    settings = settings_mod.load()
    matches = _matches(store)
    players: dict[str, dict[str, Any]] = {}
    match_rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for m in matches:
        summary = m["summary"]
        try:
            payload = stats_mod.build(store, m["replay_id"])
        except Exception as exc:  # noqa: BLE001 - one unreadable replay must not kill the list
            skipped.append({"replay_id": m["replay_id"], "why": str(exc)[:200]})
            continue
        listed = payload.get("players") or []
        teams: dict[str, list[str]] = {}
        for p in listed:
            subj = p.get("subject")
            if not subj:
                continue
            teams.setdefault(p.get("team") or "?", []).append(subj)
            block = players.setdefault(subj, {
                "subject": subj,
                "match_ids": [],
                "agents": [],
                "teams": [],
                "rounds": 0,
                "head": _blank_head(),
            })
            head = block["head"]
            h = _head_from_stats(p.get("stats") or {}, p.get("per_round") or [])
            head["matches"] = (head.get("matches") or 0) + 1
            head["rounds"] = (head.get("rounds") or 0) + (h["rounds"] or 0)
            for key in ("kills", "deaths", "assists", "kast_rounds"):
                if h.get(key) is not None:
                    head[key] = (head.get(key) or 0) + h[key]
            for key in ("opponent_damage", "score"):
                if h.get(key) is not None:
                    head[key] = round((head.get(key) or 0.0) + float(h[key]), 1)
            block["match_ids"].append(m["match_id"])
            agent = p.get("agent_uuid")
            if agent and agent not in block["agents"]:
                block["agents"].append(agent)
            team = p.get("team")
            if team and team not in block["teams"]:
                block["teams"].append(team)

        match_rows.append({
            "match_id": m["match_id"],
            "replay_id": m["replay_id"],
            "duplicates": list(m["duplicates"]),
            "level_name": summary.get("level_name"),
            "duration_ms": summary.get("duration_ms"),
            "timestamp_ticks": summary.get("timestamp_ticks"),
            "build": summary.get("build"),
            "players": [
                {"subject": p.get("subject"), "team": p.get("team"), "agent_uuid": p.get("agent_uuid")}
                for p in listed
            ],
        })

    # every player, with the pooled headline numbers and the local label
    out_players = []
    for subj, block in players.items():
        head = dict(block["head"])
        head["matches"] = len(block["match_ids"])
        rates = {
            "kd": _div(head.get("kills"), head.get("deaths"), 2),
            "adr": _div(head.get("opponent_damage"), head.get("rounds"), 1),
            "acs": _div(head.get("score"), head.get("rounds"), 1),
            "kast_pct": _pct(head.get("kast_rounds"), head.get("rounds"), 1),
        }
        out_players.append({
            "subject": subj,
            "label": settings["names"].get(subj),
            "matches": len(block["match_ids"]),
            "match_ids": block["match_ids"],
            "agents": block["agents"],
            "teams": block["teams"],
            "rounds": head.get("rounds"),
            "kills": head.get("kills"),
            "deaths": head.get("deaths"),
            "assists": head.get("assists"),
            **rates,
            "likely_me": False,
        })

    order = sorted(out_players, key=lambda p: (-(p["matches"] or 0), -(p["rounds"] or 0), p["subject"]))
    guess = order[0]["subject"] if order else None
    for p in out_players:
        p["likely_me"] = p["subject"] == guess
    out_players.sort(key=lambda p: (-(p["likely_me"]), -(p["matches"] or 0), p["subject"]))

    return {
        "generated_at": int(time.time() * 1000),
        "match_count": len(match_rows),
        "matches": match_rows,
        "players": out_players,
        "likely_me": guess,
        "me": settings["me"],
        "names": settings["names"],
        "skipped": skipped,
        "deep": deep_state(store, matches),
        "seconds": round(time.time() - started, 2),
        "notes": list(NOTES),
    }


def teammates(store, subject: str) -> dict[str, Any]:
    """Who this player actually shares a team with, and how much they share.

    A pure read of the cached payloads, deduped by match id like the catalog: no engine is run and no
    name is guessed. Raises KeyError when no replay holds the subject.
    """
    started = time.time()
    settings = settings_mod.load()
    matches = _matches(store)
    blocks: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []
    seen_subject = False

    for m in matches:
        try:
            payload = stats_mod.build(store, m["replay_id"])
        except Exception as exc:  # noqa: BLE001 - one unreadable replay must not kill the list
            skipped.append({"replay_id": m["replay_id"], "why": str(exc)[:200]})
            continue
        listed = payload.get("players") or []
        mine = next((p for p in listed if p.get("subject") == subject), None)
        if mine is None:
            continue
        seen_subject = True
        my_team = mine.get("team")
        my_rounds = {r.get("round") for r in (mine.get("per_round") or []) if r.get("round") is not None}
        winners = {r.get("round"): r.get("winner") for r in (payload.get("rounds") or [])}
        stamp = (m["summary"] or {}).get("timestamp_ticks")

        for p in listed:
            other = p.get("subject")
            if not other or other == subject or p.get("team") != my_team:
                continue
            block = blocks.setdefault(other, {
                "subject": other,
                "match_ids": [],
                "rounds_together": 0,
                "rounds_without_winner": 0,
                "wins": 0,
                "first_seen": None,
                "last_seen": None,
            })
            shared = my_rounds & {r.get("round") for r in (p.get("per_round") or [])
                                  if r.get("round") is not None}
            block["match_ids"].append(m["match_id"])
            block["rounds_together"] += len(shared)
            for n in shared:
                winner = winners.get(n)
                if winner is None:
                    block["rounds_without_winner"] += 1
                elif winner == my_team:
                    block["wins"] += 1
            if stamp is not None:
                lo, hi = block["first_seen"], block["last_seen"]
                block["first_seen"] = stamp if lo is None else min(lo, stamp)
                block["last_seen"] = stamp if hi is None else max(hi, stamp)

    if not seen_subject:
        raise KeyError(subject)

    rows = []
    for block in blocks.values():
        played = block["rounds_together"]
        decided = played - block["rounds_without_winner"]
        rows.append({
            "subject": block["subject"],
            "label": settings["names"].get(block["subject"]),
            "matches": len(block["match_ids"]),
            "match_ids": list(block["match_ids"]),
            "rounds_together": played or None,
            "rounds_without_winner": block["rounds_without_winner"],
            "wins": block["wins"],
            "losses": decided - block["wins"],
            "win_pct": _pct(block["wins"], decided, 1),
            "first_seen": block["first_seen"],
            "last_seen": block["last_seen"],
        })
    rows.sort(key=lambda r: (-(r["matches"] or 0), -(r["rounds_together"] or 0), r["subject"]))

    return {
        "subject": subject,
        "label": settings["names"].get(subject),
        "likely_me": settings["me"] == subject,
        "match_count": len(matches),
        "teammates": rows,
        "skipped": skipped,
        "seconds": round(time.time() - started, 2),
        "notes": list(TEAMMATE_NOTES),
    }


def profile(store, subject: str, deep: bool = False) -> dict[str, Any]:
    """One player across every match he appears in. Raises KeyError for an unknown subject."""
    started = time.time()
    settings = settings_mod.load()
    matches = _matches(store)
    rows: list[dict[str, Any]] = []
    sums: dict[str, Any] = {"rounds": 0, "kills": 0, "deaths": 0, "assists": 0, "kast_rounds": 0,
                            "opponent_damage": 0.0, "score": 0.0, "self_deaths": 0, "ults_used": 0,
                            "self_damage": 0.0}
    sides: dict[str, dict[str, Any]] = {s: {} for s in SIDES}
    buy: dict[str, dict[str, Any]] = {b: {} for b in BUY_ORDER}
    aim_pairs: dict[str, list[tuple[Any, Any]]] = {k: [] for k, _ in AIM_WEIGHTED}
    aim_sums: dict[str, int] = {k: 0 for k in AIM_SUMS}
    weapons: dict[str, dict[str, Any]] = {}
    trend: list[dict[str, Any]] = []
    agents: list[str] = []
    teams: list[str] = []
    seen_matches = 0
    skipped: list[dict[str, Any]] = []

    for m in matches:
        try:
            payload = stats_mod.build(store, m["replay_id"])
        except Exception as exc:  # noqa: BLE001
            skipped.append({"replay_id": m["replay_id"], "why": str(exc)[:200]})
            continue
        me = next((p for p in (payload.get("players") or []) if p.get("subject") == subject), None)
        if me is None:
            continue
        seen_matches += 1
        st = me.get("stats") or {}
        per_round = me.get("per_round") or []
        rounds = len(per_round) or None
        kast_rounds = sum(1 for r in per_round if r.get("kast"))
        dmg = st.get("damage_to_opponents")
        opp_dmg = sum(v for v in dmg.values() if isinstance(v, (int, float))) if isinstance(dmg, dict) else None

        rows.append({
            "match_id": m["match_id"],
            "replay_id": m["replay_id"],
            "duplicates": list(m["duplicates"]),
            "level_name": (m["summary"] or {}).get("level_name"),
            "timestamp_ticks": (m["summary"] or {}).get("timestamp_ticks"),
            "team": me.get("team"),
            "agent_uuid": me.get("agent_uuid"),
            "rounds": rounds,
            "kills": st.get("kills"),
            "deaths": st.get("deaths"),
            "assists": st.get("assists"),
            "kd": st.get("kd"),
            "adr": st.get("adr"),
            "acs": st.get("acs"),
            "kast_pct": st.get("kast_pct"),
            "hs_pct": st.get("hs_pct"),
            "entry_kills": st.get("entry_kills"),
            "entry_deaths": st.get("entry_deaths"),
            "traded_pct": st.get("traded_pct"),
            "isolated_pct": st.get("isolated_pct"),
            "self_deaths": st.get("self_deaths"),
            "ults_used": st.get("ults_used"),
            "ability_casts": st.get("ability_casts"),
            "clutch_attempts": st.get("clutch_attempts"),
            "clutch_wins": st.get("clutch_wins"),
            "multikills": st.get("multikills"),
            "avg_loadout_money": st.get("avg_loadout_money"),
        })

        for key, value in (("rounds", rounds), ("kills", st.get("kills")), ("deaths", st.get("deaths")),
                           ("assists", st.get("assists")), ("kast_rounds", kast_rounds),
                           ("self_deaths", st.get("self_deaths")), ("ults_used", st.get("ults_used"))):
            if value is not None:
                sums[key] = (sums.get(key) or 0) + int(value)
        for key, value in (("opponent_damage", opp_dmg), ("score", st.get("score")),
                           ("self_damage", st.get("self_damage"))):
            if value is not None:
                sums[key] = round((sums.get(key) or 0.0) + float(value), 1)

        for side, block in (me.get("stats_by_side") or {}).items():
            if side in sides:
                _add_bucket(sides[side], block)
        for b, block in (me.get("stats_by_buy") or {}).items():
            if b in buy:
                _add_bucket(buy[b], block)

        agent = me.get("agent_uuid")
        if agent and agent not in agents:
            agents.append(agent)
        team = me.get("team")
        if team and team not in teams:
            teams.append(team)

        mp = _metrics_by_subject(store, m["replay_id"]).get(subject)
        if mp:
            for key, weight_key in AIM_WEIGHTED:
                aim_pairs[key].append((mp.get(key), mp.get(weight_key)))
            for key in AIM_SUMS:
                v = mp.get(key)
                if isinstance(v, (int, float)):
                    aim_sums[key] += v
            for name, cell in (mp.get("weapons") or {}).items():
                if not isinstance(cell, dict):
                    continue
                w = weapons.setdefault(name, {"matches": 0, "shots": 0, "hits": 0, "head_hits": 0,
                                              "dmg": 0.0, "kills": 0})
                shots = cell.get("shots") or 0
                if shots:
                    w["matches"] += 1
                w["shots"] += int(shots)
                w["hits"] += int(cell.get("hits") or 0)
                w["head_hits"] += int(cell.get("head_hits") or 0)
                w["dmg"] = round(w["dmg"] + float(cell.get("dmg") or 0.0), 1)
                w["kills"] += int(cell.get("kills") or 0)

        trend.append({
            "match_id": m["match_id"],
            "replay_id": m["replay_id"],
            "at": (m["summary"] or {}).get("timestamp_ticks"),
            "rounds": rounds,
            "adr": st.get("adr"),
            "acs": st.get("acs"),
            "kd": st.get("kd"),
            "kast_pct": st.get("kast_pct"),
            "hs_pct": st.get("hs_pct"),
        })

    if not seen_matches:
        raise KeyError(subject)

    pooled = {
        "rounds": sums.get("rounds") or None,
        "kills": sums.get("kills"),
        "deaths": sums.get("deaths"),
        "assists": sums.get("assists"),
        "kd": _div(sums.get("kills"), sums.get("deaths"), 2),
        "adr": _div(sums.get("opponent_damage"), sums.get("rounds"), 1),
        "acs": _div(sums.get("score"), sums.get("rounds"), 1),
        "kast_pct": _pct(sums.get("kast_rounds"), sums.get("rounds"), 1),
        "self_deaths": sums.get("self_deaths"),
        "self_damage": sums.get("self_damage"),
        "ults_used": sums.get("ults_used"),
    }
    mean_of_matches = {
        key: _mean([row.get(key) for row in rows])
        for key in ("adr", "acs", "kd", "kast_pct", "hs_pct", "traded_pct", "isolated_pct")
    }
    aim = {key: {"value": v, "n": n} for key, (v, n) in
           ((key, _weighted(pairs)) for key, pairs in aim_pairs.items())}
    aim["shots"] = aim_sums["shots"]
    aim["hits"] = aim_sums["hits"]
    aim["gun_kills"] = aim_sums["kills"]
    aim["accuracy"] = _div(aim_sums["hits"], aim_sums["shots"], 4)
    aim["head_hits"] = aim_sums["head_hits"]
    aim["wallbang_hits"] = aim_sums["wallbang_hits"]
    aim["spray_acc"] = _div(aim_sums["spray_hits"], aim_sums["sprays"], 4)

    weapon_rows = []
    for name, w in weapons.items():
        weapon_rows.append({
            "name": name,
            "matches": w["matches"],
            "shots": w["shots"],
            "hits": w["hits"],
            "kills": w["kills"],
            "dmg": w["dmg"],
            "acc": _div(w["hits"], w["shots"], 4),
            "hs_rate": _div(w["head_hits"], w["hits"], 4),
            "dmg_per_shot": _div(w["dmg"], w["shots"], 1),
            "shots_per_kill": _div(w["shots"], w["kills"], 1),
        })
    weapon_rows.sort(key=lambda w: (-(w["shots"] or 0), w["name"]))

    trend.sort(key=lambda t: (t["at"] is None, t["at"] or 0))

    deep_block = None
    deep_info = deep_state(store, matches)
    if deep and deep_info.get("built") and not deep_info.get("stale"):
        deep_block = (_read_deep() or {}).get("players", {}).get(subject)

    return {
        "generated_at": int(time.time() * 1000),
        "subject": subject,
        "label": settings["names"].get(subject),
        "me": settings["me"],
        "is_me": settings["me"] == subject,
        "match_count": seen_matches,
        "matches": rows,
        "pooled": pooled,
        "mean_of_matches": mean_of_matches,
        "sides": {s: _finish_bucket(b) for s, b in sides.items()},
        "buys": {b: _finish_bucket(v) for b, v in buy.items()},
        "aim": aim,
        "weapons": weapon_rows,
        "trend": trend,
        "agents": agents,
        "teams": teams,
        "deep": deep_block,
        "deep_state": deep_info,
        "skipped": skipped,
        "seconds": round(time.time() - started, 2),
        "notes": list(NOTES),
    }


# ---- the deep layer --------------------------------------------------


def _read_deep() -> dict[str, Any] | None:
    try:
        raw = json.loads(DEEP_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    return raw if isinstance(raw, dict) else None


def deep_state(store, matches: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    matches = matches if matches is not None else _matches(store)
    current = _fingerprint(matches)
    cache = _read_deep()
    if not cache:
        return {"built": False, "stale": True, "matches": len(matches), "fingerprint": current}
    built = cache.get("version") == DEEP_VERSION and cache.get("fingerprint") == current
    return {
        "built": True,
        "stale": not built,
        "version": cache.get("version"),
        "built_at": cache.get("built_at"),
        "seconds": cache.get("seconds"),
        "match_count": len(cache.get("matches") or []),
        "matches": len(matches),
        "fingerprint": current,
    }


def build_deep(store, fresh: bool = False) -> dict[str, Any]:
    """Build (or reuse) the cached judge/death layer across every distinct match."""
    started = time.time()
    matches = _matches(store)
    fingerprint = _fingerprint(matches)
    if not fresh:
        cache = _read_deep()
        if cache and cache.get("version") == DEEP_VERSION and cache.get("fingerprint") == fingerprint:
            return cache

    players: dict[str, dict[str, Any]] = {}
    match_ids: list[str] = []
    built: list[str] = []
    failed: list[dict[str, Any]] = []

    def block(subject: str) -> dict[str, Any]:
        return players.setdefault(subject, {
            "matches": 0, "points": 0, "team_points": 0, "deaths": 0,
            "classes": {}, "faults": {}, "case_against": [], "case_for": [],
            "drills": [], "history": [],
        })

    for m in matches:
        match_ids.append(m["match_id"])
        try:
            j = judge_mod.build(store, m["replay_id"])
            d = deaths_mod.build(store, m["replay_id"])
        except Exception as exc:  # noqa: BLE001
            failed.append({"replay_id": m["replay_id"], "why": str(exc)[:200]})
            continue
        built.append(m["match_id"])
        teams = j.get("teams") or {}
        training = j.get("training") or {}
        for p in j.get("players") or []:
            subj = p.get("subject")
            if not subj:
                continue
            b = block(subj)
            b["matches"] += 1
            b["points"] = (b["points"] or 0) + int(p.get("points") or 0)
            team_pts = (teams.get(p.get("team")) or {}).get("points")
            if team_pts:
                b["team_points"] = (b["team_points"] or 0) + int(team_pts)
            for f in p.get("faults") or []:
                cell = b["faults"].setdefault(f["key"], {"key": f["key"], "label": f.get("label"),
                                                        "weight": f.get("weight"), "n": 0, "points": 0})
                cell["n"] += int(f.get("n") or 0)
                cell["points"] += int(f.get("points") or 0)
            history = {"match_id": m["match_id"], "at": (m["summary"] or {}).get("timestamp_ticks"),
                       "points": p.get("points"), "share_pct": p.get("share_pct"),
                       "grade": p.get("grade"), "deaths": p.get("deaths"), "adr": p.get("adr")}
            for item in (training.get(subj) or []):
                b["drills"].append({**item, "match_id": m["match_id"]})
            b["history"].append(history)
        for p in d.get("players") or []:
            subj = p.get("subject")
            if not subj:
                continue
            b = block(subj)
            b["deaths"] = (b["deaths"] or 0) + int(p.get("deaths") or 0)
            for c in p.get("classes") or []:
                b["classes"][c["key"]] = (b["classes"].get(c["key"]) or 0) + int(c.get("n") or 0)

    out_players: dict[str, dict[str, Any]] = {}
    for subj, b in players.items():
        faults = sorted(b["faults"].values(), key=lambda f: -(f["points"] or 0))
        drills: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in sorted(b["drills"], key=lambda d: -(d.get("severity") or 0)):
            if item.get("key") in seen:
                continue
            seen.add(item.get("key"))
            drills.append(item)
        latest = sorted((h for h in b["history"] if h.get("share_pct") is not None),
                        key=lambda h: -(h.get("share_pct") or 0))
        out_players[subj] = {
            "subject": subj,
            "label": settings_mod.names().get(subj),
            "matches": b["matches"],
            "points": b["points"],
            "team_points": b["team_points"],
            "share_pct": _pct(b["points"], b["team_points"], 1),
            "grade": _grade(_pct(b["points"], b["team_points"], 1)) if b["team_points"] else None,
            "worst_share_pct": latest[0]["share_pct"] if latest else None,
            "worst_match_id": latest[0]["match_id"] if latest else None,
            "deaths": b["deaths"],
            "classes": [{"key": k, "n": n} for k, n in sorted(b["classes"].items(), key=lambda kv: -kv[1])],
            "faults": faults,
            "drills": drills[:6],
            "history": sorted(b["history"], key=lambda h: (h.get("at") is None, h.get("at") or 0)),
        }

    cache = {
        "version": DEEP_VERSION,
        "fingerprint": fingerprint,
        "built_at": int(time.time() * 1000),
        "seconds": round(time.time() - started, 2),
        "matches": match_ids,
        "built": built,
        "failed": failed,
        "players": out_players,
        "notes": [
            "Blame points, shares and grades come from the judging engine, unchanged, summed over the "
            "matches this player appears in. A share is of his own team's points in those matches.",
            "Death classes come from the death audit. Both layers parse each match's field stream, which "
            "is why this is cached and dated rather than recomputed for every page.",
        ],
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DEEP_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    tmp.replace(DEEP_PATH)
    return cache
