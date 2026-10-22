"""The judge: who cost the team the round, judged from what the export shows.

This module answers four questions the ordinary views cannot:

* why did we lose this round - from the plant, the lead and the deaths, never
  from a guess;
* who is responsible - a weighted fault list per player over the lost rounds,
  each point traceable to a fact the export produced;
* who failed to trade - deaths that went untraded while a teammate was alive;
* who entered last - first damage time per player per round.

It is blunt on purpose (this is the Judge tab) and it is labelled as ours: every
grade comes with the numbers behind it, the weights are published in the payload,
and each player also gets the strongest counter-case the same facts support, so a
reader can argue back. Nothing here is versioned or cached; missing stays ``None``.
"""

from __future__ import annotations

import bisect
import re
import time
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

import deaths as deaths_mod
import metrics as metrics_mod
import stats as stats_mod

# --- field names the judge reads ---------------------------------------------
BOMB_FIELDS = (
    "BombPlantedRPC.BombPlanter",
    "BombPlantedRPC.PlantLocation",
    "BombPlantedRPC.PlantSite",
    "PlantedAtSite",
    "CurrentDefuser",
    "DefuseProgress",
)
INTERACTION_SUBJECT = r"Interactions\[\d+\]\.ParticipantSubject$"
DEALT_DAMAGE = r"DealtInteractions\[\d+\]\.Regions\[\d+\]\.Damage$"
FIELD_RX = "|".join(
    [re.escape(f) for f in BOMB_FIELDS] + [INTERACTION_SUBJECT, DEALT_DAMAGE]
)
KEY_RX = re.compile(r"^Rounds\[(\d+)\]\.Reports\[(\d+)\]\.Interactions\[(\d+)\]\.")

# --- judgement constants, all published in the payload -----------------------
# What a player can be blamed for, and what each fault is worth. The numbers are
# ours; the counts behind them are not.
FAULT_WEIGHTS = (
    ("opening_death", "died first in the round", 3),
    ("untraded_death", "died untraded", 2),
    ("no_damage", "died without damaging an opponent", 2),
    ("late_contact", "only started dealing damage after two of his own team had died", 2),
    ("isolated_death", "died alone, out of trade range", 1),
    ("ult_charged", "died with an ultimate charged and unspent", 1),
    ("missed_trade", "was alive and did not trade a teammate", 1),
)
WEIGHTS = {key: w for (key, _label, w) in FAULT_WEIGHTS}

# Share of the team's blame points -> grade. Equal shares would be 20% each.
GRADE_BANDS = ((12.0, "A"), (17.0, "B"), (22.0, "C"), (28.0, "D"))
BUY_STEP = {"pistol": 0, "eco": 1, "force": 2, "full": 3, "unknown": -1}

NOTES = [
    "The blame points are our scoring, not a fact: each one is a counted thing "
    "the export shows, multiplied by a weight you can read in the payload.",
    "A fault is only counted in rounds the player's team lost, so a winning "
    "round never blames anyone.",
    "Every player also carries the strongest counter-case the same facts support. "
    "Read it before you agree with the grade.",
    "First contact comes from the damage records, so it can lag a kill by the "
    "replication delay (8-9 ms in 6 of the 24 rounds of the reference match).",
    "A missed trade is counted for every teammate still alive when a death went "
    "untraded with a teammate within trade range; the exact distance to each "
    "teammate is not in the payload, so the count is an approximation that the "
    "counter-case states plainly.",
    "Entering last is measured from the damage records: entry order is the rank of "
    "his first damage in the round, and a late entry means he only started dealing "
    "damage after two of his own team had already died. Standing in a spot and not "
    "firing is not a fault the export can see.",
    "No drill is built on ability casts: the export records only 4 to 7 casts for a "
    "whole match, far below what any player really uses, so a low cast count says "
    "more about the export than about the player. Ultimate casts come from the "
    "ultimate events and are used.",
]


def _subject_maps(players: list[dict]) -> tuple[dict, dict]:
    by_state, by_char = {}, {}
    for p in players:
        subj = p.get("subject")
        st, ch = p.get("player_state_guid"), p.get("character_net_guid")
        if subj and st not in (None, ""):
            by_state[int(st)] = subj
        if subj and ch not in (None, ""):
            by_char[int(ch)] = subj
    return by_state, by_char


def _round_bins(rounds: list[dict]):
    """Start times, for turning a millisecond stamp into a round number."""
    pairs = sorted(
        [(int(r["start_ms"]), int(r["round"])) for r in rounds if r.get("start_ms") is not None]
    )
    starts = [t for (t, _n) in pairs]
    return starts, [n for (_t, n) in pairs]


def _round_of(starts: list, nums: list, t) -> int | None:
    if not starts or t is None:
        return None
    i = bisect.bisect_right(starts, int(t)) - 1
    return nums[i] if i >= 0 else None


def _num(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return float(v)
    return v


def _scan(export_dir: Path, rounds: list[dict], by_state: dict, by_char: dict) -> dict:
    """One pass over the field stream: plants, defuses and first damage times.

    Only the handful of field names the judge needs are materialised, so the cost
    is a scan of the field file, not a table of it.
    """
    starts, nums = _round_bins(rounds)
    plants: dict[int, dict] = {}
    contacts: dict[int, dict] = {}
    keys_subject: dict[tuple, str] = {}
    keys_contact: dict[tuple, tuple] = {}
    path = export_dir / "fields.parquet"
    if not path.exists():
        return {"plants": {}, "contacts": {}}

    def bin_get(store: dict, rn: int) -> dict:
        return store.setdefault(rn, {})

    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(
        batch_size=200_000,
        columns=["time_ms", "actor_net_guid", "field_name", "value_i64", "value_f64", "value_str"],
    ):
        names = batch.column("field_name")
        if pa.types.is_dictionary(names.type):
            names = names.cast(pa.string())
        mask = pc.match_substring_regex(names, FIELD_RX)
        hit = batch.filter(mask)
        if hit.num_rows == 0:
            continue
        for row in hit.to_pylist():
            t = row.get("time_ms")
            name = row.get("field_name") or ""
            actor = row.get("actor_net_guid")
            subj = None
            if actor is not None:
                subj = by_state.get(int(actor)) or by_char.get(int(actor))
            if name == "BombPlantedRPC.BombPlanter" and row.get("value_i64") is not None:
                rn = _round_of(starts, nums, t)
                if rn is not None:
                    bin_get(plants, rn)["planter"] = by_char.get(int(row["value_i64"]))
                    bin_get(plants, rn)["plant_t"] = int(t)
            elif name == "BombPlantedRPC.PlantLocation" and row.get("value_str"):
                rn = _round_of(starts, nums, t)
                if rn is not None:
                    bin_get(plants, rn)["location"] = row["value_str"]
            elif name in ("BombPlantedRPC.PlantSite", "PlantedAtSite") and row.get("value_i64") is not None:
                rn = _round_of(starts, nums, t)
                if rn is not None:
                    bin_get(plants, rn)["site"] = int(row["value_i64"])
            elif name == "CurrentDefuser":
                rn = _round_of(starts, nums, t)
                v = row.get("value_i64")
                if rn is not None and v:
                    bin_get(plants, rn)["defuser"] = by_char.get(int(v))
            elif name == "DefuseProgress":
                rn = _round_of(starts, nums, t)
                v = _num(row.get("value_f64"))
                if rn is not None and v is not None:
                    # The value is seconds of defusing, not a 0-1 fraction: it
                    # ramps to about 7.0 for a complete defuse and about 3.5 for
                    # one that is only half done.
                    cur = bin_get(plants, rn).get("defuse_s")
                    bin_get(plants, rn)["defuse_s"] = v if cur is None else max(cur, v)
            else:
                m = KEY_RX.match(name)
                if not m or actor is None:
                    continue
                # The interaction carries its own subject - the report actor's kill
                # vector is not needed here - and the report index repeats across
                # actors, so the actor is part of the key.
                key = (int(m.group(1)) + 1, int(m.group(2)), int(m.group(3)), int(actor))
                if name.endswith("ParticipantSubject"):
                    subj = row.get("value_str")
                    if not subj and row.get("value_i64") is not None:
                        subj = by_state.get(int(row["value_i64"])) or by_char.get(int(row["value_i64"]))
                    if subj:
                        keys_subject[key] = subj
                else:
                    v = _num(row.get("value_i64"))
                    if v is None:
                        v = _num(row.get("value_f64"))
                    if v is None or v <= 0:
                        continue
                    best = keys_contact.get(key)
                    if best is None or int(t) < best[0]:
                        keys_contact[key] = (int(t), 1)
                    else:
                        keys_contact[key] = (best[0], best[1] + 1)

    for key, (t, n) in keys_contact.items():
        subj = keys_subject.get(key)
        if not subj:
            continue
        rn = key[0]
        cell = bin_get(contacts, rn).setdefault(subj, {"first": None, "n": 0})
        cell["n"] += n
        if cell["first"] is None or t < cell["first"]:
            cell["first"] = t
    return {"plants": plants, "contacts": contacts}


def _team_players(players: list[dict], team: str) -> list[str]:
    return [p["subject"] for p in players if p.get("team") == team]


def _grade(share: float) -> str:
    for limit, letter in GRADE_BANDS:
        if share <= limit:
            return letter
    return "E"


def _alive(deaths_sorted: list[dict], t: int) -> set:
    """Victims of deaths that happened strictly before ``t``."""
    return {d.get("victim") for d in deaths_sorted if d.get("t") is not None and int(d["t"]) < t}


def _pct(part, whole):
    if not whole:
        return None
    return round(100.0 * part / whole, 1)


def summarize(
    stats_payload: dict,
    bombs: dict | None = None,
    contacts: dict | None = None,
    ult: dict | None = None,
) -> dict:
    bombs = bombs or {}
    contacts = contacts or {}
    ult = ult or {}
    rounds = {int(r["round"]): r for r in stats_payload.get("rounds") or [] if r.get("round") is not None}
    players = stats_payload.get("players") or []
    teams = ("Blue", "Red")
    team_of = {p.get("subject"): p.get("team") for p in players}
    team_members = {t: _team_players(players, t) for t in teams}
    agent_of = {p.get("subject"): p.get("agent_uuid") for p in players}
    stats_of = {p.get("subject"): (p.get("stats") or {}) for p in players}

    per_round: dict[str, dict] = {}
    for p in players:
        rows = {int(r["round"]): r for r in (p.get("per_round") or []) if r.get("round") is not None}
        per_round[p.get("subject")] = rows

    faults: dict[str, dict[str, int]] = {p.get("subject"): {} for p in players}
    missed: dict[str, int] = {p.get("subject"): 0 for p in players}
    late_pairs: list[tuple] = []
    lost_rounds: list[dict] = []
    entries: dict[str, dict] = {p.get("subject"): {"first": 0, "late": 0, "orders": []} for p in players}

    def add(subj: str, key: str, n: int = 1) -> None:
        if subj in faults and n:
            faults[subj][key] = faults[subj].get(key, 0) + n

    for rn in sorted(rounds):
        rnd = rounds[rn]
        winner = rnd.get("winner")
        if not winner:
            continue
        deaths = [d for d in (rnd.get("deaths") or []) if d.get("t") is not None]
        deaths.sort(key=lambda d: int(d["t"]))
        round_contacts = contacts.get(rn) or {}
        # first damage of the round overall, for the entry order
        order = sorted(
            [(c["first"], subj) for subj, c in round_contacts.items() if c.get("first") is not None]
        )

        for loser in teams:
            if loser == winner:
                continue
            own = team_members[loser]
            other = team_members["Blue" if loser == "Red" else "Red"]
            econ = rnd.get("economy") or {}
            my_buy = (econ.get(loser) or {}).get("buy") or "unknown"
            opp_buy = (econ.get(winner) or {}).get("buy") or "unknown"
            changeable = (
                BUY_STEP.get(my_buy, -1) >= 0
                and BUY_STEP.get(my_buy, -1) >= BUY_STEP.get(opp_buy, -1) - 1
            )
            own_deaths = [d for d in deaths if d.get("victim_team") == loser]
            # the biggest man advantage the losers held, after the first death
            lead = 0
            max_lead = 0
            for d in deaths:
                dead = _alive(deaths, int(d["t"]) + 1)
                mine = sum(1 for s in own if s not in dead)
                theirs = sum(1 for s in other if s not in dead)
                lead = mine - theirs
                if mine + theirs < len(own) + len(other):
                    max_lead = max(max_lead, lead)
            classes = []
            if rnd.get("overtime"):
                classes.append({"key": "overtime", "text": "the round went past regulation"})
            if max_lead >= 2:
                classes.append({"key": "lost_a_lead", "text": f"led by {max_lead} players and still lost"})
            plant = bombs.get(rn)
            if plant:
                if plant.get("planter") and plant.get("planter") in own:
                    classes.append({"key": "lost_after_plant", "text": "planted the spike and lost"})
                elif plant.get("planter"):
                    classes.append({"key": "retake_failed", "text": "the enemy planted and we did not take it back"})
            else:
                classes.append({"key": "no_plant", "text": "no spike was planted"})
            lost_rounds.append(
                {
                    "round": rn,
                    "winner": winner,
                    "loser": loser,
                    "sides": rnd.get("sides"),
                    "overtime": bool(rnd.get("overtime")),
                    "buy": my_buy,
                    "opponent_buy": opp_buy,
                    "changeable": changeable,
                    "max_lead_lost": max_lead,
                    "plant": plant,
                    "classes": classes,
                }
            )

            last_enemy_death = max(
                (int(d["t"]) for d in deaths if d.get("victim_team") == winner), default=None
            )
            # entry order for this round
            for subj, c in round_contacts.items():
                if c.get("first") is None:
                    continue
                if subj in entries:
                    rank = 1 + sum(1 for (t0, s0) in order if t0 < c["first"])
                    entries[subj]["orders"].append(rank)
                    if rank == 1:
                        entries[subj]["first"] += 1
            own_death_times = sorted((int(d["t"]), d.get("victim")) for d in own_deaths)
            for subj, c in round_contacts.items():
                if c.get("first") is None or subj not in own:
                    continue
                # he only started dealing damage after two of his own team had
                # already died: a late arrival, not necessarily a fault
                before = sum(
                    1 for (t0, v0) in own_death_times if v0 != subj and t0 < c["first"]
                )
                if before >= 2:
                    late_pairs.append((subj, rn))

            for d in own_deaths:
                victim = d.get("victim")
                t = int(d["t"])
                damage = (per_round.get(victim) or {}).get(rn, {}).get("damage_opponents")
                if d.get("first_blood") is True:
                    add(victim, "opening_death")
                if d.get("traded") is False:
                    add(victim, "untraded_death")
                if damage == 0:
                    add(victim, "no_damage")
                if d.get("isolated") is True:
                    add(victim, "isolated_death")
                u = ult.get(victim) or {}
                pts = deaths_mod._passive_cast(u.get("points") or [], t)
                cost = u.get("cost")
                if pts is not None and cost is not None and pts >= cost:
                    add(victim, "ult_charged")
                # who was alive and could have traded
                if d.get("traded") is False:
                    alive = [s for s in own if s != victim and s not in _alive(deaths, t)]
                    near = d.get("isolated") is False
                    for s in alive:
                        if near:
                            missed[s] = missed.get(s, 0) + 1
                            add(s, "missed_trade")

    for subj, rn in late_pairs:
        add(subj, "late_contact")

    blocks = []
    all_points: dict[str, int] = {}
    for p in players:
        subj = p.get("subject")
        pts = sum(WEIGHTS.get(key, 0) * n for key, n in (faults.get(subj) or {}).items())
        all_points[subj] = pts
    team_points = {
        t: sum(all_points.get(s, 0) for s in team_members[t]) for t in teams
    }

    for p in players:
        subj = p.get("subject")
        team = p.get("team")
        st = stats_of.get(subj) or {}
        counts = faults.get(subj) or {}
        total = all_points.get(subj, 0)
        share = _pct(total, team_points.get(team))
        fault_rows = [
            {
                "key": key,
                "label": label,
                "weight": WEIGHTS[key],
                "n": counts.get(key, 0),
                "points": WEIGHTS[key] * counts.get(key, 0),
            }
            for (key, label, _w) in FAULT_WEIGHTS
            if counts.get(key)
        ]
        fault_rows.sort(key=lambda f: -f["points"])
        deaths_n = st.get("deaths") or 0
        if deaths_n:
            case_against = [
                f"{f['label']} in {f['n']} of the {deaths_n} deaths" for f in fault_rows[:4]
            ]
        else:
            case_against = [
                f"{f['label']} in {f['n']} round" + ("s" if f["n"] != 1 else "")
                for f in fault_rows[:4]
            ]

        # the counter-case, from the same facts
        case_for = []
        eco_openings = 0
        for rn in rounds:
            rnd = rounds[rn]
            for d in rnd.get("deaths") or []:
                if d.get("victim") != subj or d.get("first_blood") is not True:
                    continue
                econ = rnd.get("economy") or {}
                my_buy = (econ.get(team) or {}).get("buy") or "unknown"
                opp = "Blue" if team == "Red" else "Red"
                opp_buy = (econ.get(opp) or {}).get("buy") or "unknown"
                if my_buy in ("eco", "force") and BUY_STEP.get(my_buy, -1) < BUY_STEP.get(opp_buy, -1):
                    eco_openings += 1
        if eco_openings:
            case_for.append(f"{eco_openings} of his opening deaths came in rounds the team bought worse than the enemy")
        traded_n = st.get("traded_deaths") or 0
        if traded_n:
            case_for.append(f"{traded_n} of his deaths were traded by a teammate")
        ek, ed = st.get("entry_kills") or 0, st.get("entry_deaths") or 0
        if ek:
            case_for.append(f"he won {ek} of the {ek + ed} opening duels he took")
        multi = st.get("multikills") or {}
        multi_n = sum(int(v) for v in multi.values()) if isinstance(multi, dict) else int(multi or 0)
        if multi_n:
            best_n = max((int(k) for k in multi.keys()), default=0)
            case_for.append(f"{multi_n} multi-kill rounds, one of them a {best_n}k")
        if missed.get(subj):
            case_for.append(
                f"he was alive for {missed[subj]} untraded teammate deaths, which is the fault above that is least about his aim"
            )
        if not case_for:
            case_for.append("no mitigating fact stood out in this match")

        blocks.append(
            {
                "subject": subj,
                "team": team,
                "agent_uuid": agent_of.get(subj),
                "deaths": deaths_n,
                "adr": st.get("adr"),
                "kast_pct": st.get("kast_pct"),
                "points": total,
                "share_pct": share,
                "grade": _grade(share) if share is not None else None,
                "faults": fault_rows,
                "case_against": case_against,
                "case_for": case_for,
                "missed_trades": missed.get(subj, 0),
                "entry_first": entries[subj]["first"],
                "entry_late": sum(1 for (s, _rn) in late_pairs if s == subj),
                "entry_avg_order": (
                    round(sum(entries[subj]["orders"]) / len(entries[subj]["orders"]), 2)
                    if entries[subj]["orders"]
                    else None
                ),
            }
        )

    team_blocks = {}
    for t in teams:
        mine = [b for b in blocks if b["team"] == t]
        ranked = sorted(mine, key=lambda b: -b["points"])
        lost_n = sum(1 for lr in lost_rounds if lr["loser"] == t)
        chang = sum(1 for lr in lost_rounds if lr["loser"] == t and lr["changeable"])
        lead = sum(1 for lr in lost_rounds if lr["loser"] == t and lr["max_lead_lost"] >= 2)
        top = ranked[0] if ranked else None
        summary = f"lost {lost_n} rounds, {chang} of them on a buy that could win"
        if lead:
            summary += (
                f"; entered with a two-player lead in {lead} "
                + ("round" if lead == 1 else "rounds")
            )
        if top and top["points"]:
            summary += f". Most blame points: {top['points']} ({top['share_pct']}% of the team)"
        devil = "The harshest reading of the same numbers: "
        if lead:
            devil += (
                f"{lead} lost "
                + ("round was" if lead == 1 else "rounds were")
                + " entered with a two-player lead, which points at the mid-round call rather "
                "than one player's aim. "
            )
        forced = sum(1 for lr in lost_rounds if lr["loser"] == t and not lr["changeable"])
        if forced:
            devil += (
                f"{forced} of the lost rounds "
                + ("was" if forced == 1 else "were")
                + " on the worse buy, which no plan survives. "
            )
        if not lead and not forced:
            devil += "nothing in this match softens the verdict much."
        team_blocks[t] = {
            "lost": lost_n,
            "changeable": chang,
            "points": team_points.get(t, 0),
            "ranked": [
                {"subject": b["subject"], "points": b["points"], "share_pct": b["share_pct"], "grade": b["grade"]}
                for b in ranked
            ],
            "summary": summary,
            "devil": devil,
        }

    return {
        "n_rounds": len(rounds),
        "lost_rounds": lost_rounds,
        "players": blocks,
        "teams": team_blocks,
        "weights": WEIGHTS,
        "grade_bands": [[limit, letter] for (limit, letter) in GRADE_BANDS],
        "ult_available": bool(ult),
        "plant_available": bool(bombs),
        "plants": bombs,
        "contact_available": bool(contacts),
        "notes": NOTES,
    }


# --- training -----------------------------------------------------------------
# Weakness -> drill. Each entry is measured from the metrics or stats payload, is
# only reported when the sample is big enough, and names the drill, not a feeling.
TRAINING = (
    {
        "key": "crosshair_placement",
        "label": "Crosshair placement",
        "metric": "pre_aim_deg",
        "source": "metrics",
        "n_key": "pre_aim_n",
        "min_n": 20,
        "good": 3.0,
        "bad": 8.0,
        "reverse": False,
        "drill": "Deathmatch holding the floor line: the crosshair never drops below head height, and you do not crouch-spray.",
        "why": "pre-aim angle is how far your crosshair was from the head before the fight started",
    },
    {
        "key": "reaction",
        "label": "Reaction speed",
        "metric": "reaction_ms",
        "source": "metrics",
        "n_key": "reaction_n",
        "min_n": 20,
        "good": 350.0,
        "bad": 500.0,
        "reverse": False,
        "drill": "Aim Lab reflex modes or the range bot on random spawns, 10 minutes before you queue, twice a week.",
        "why": "reaction time is how long you took to fire after an enemy appeared",
    },
    {
        "key": "time_to_damage",
        "label": "Time to damage",
        "metric": "ttd_ms",
        "source": "metrics",
        "n_key": "ttd_n",
        "min_n": 20,
        "good": 350.0,
        "bad": 450.0,
        "reverse": False,
        "drill": "Peeker's advantage drill: swing, stop, then fire. In the range, strafe out of a corner and only shoot when the crosshair is already on the head.",
        "why": "time to damage folds together your pre-aim and your reaction, so it is the slowest of the two you need to fix",
    },
    {
        "key": "counter_strafe",
        "label": "Counter-strafing",
        "metric": "counter_strafe_pct",
        "source": "metrics",
        "n_key": "shots_classified",
        "min_n": 40,
        "good": 0.9,
        "bad": 0.6,
        "reverse": True,
        "drill": "In deathmatch, hold A then tap D (and the reverse) for every shot you take, and miss nothing else: the point is the stop, not the kill.",
        "why": "the share of shots fired while you were already stopped",
    },
    {
        "key": "spray_control",
        "label": "Spray control",
        "metric": "spray_spread_deg",
        "source": "metrics",
        "n_key": "shots_classified",
        "min_n": 60,
        "good": 1.0,
        "bad": 2.0,
        "reverse": False,
        "drill": "Spray walls at 15 and 25 m, 30 rounds a day: hold the pattern down, then add the strafe between bursts.",
        "why": "the angular width of your sprays",
    },
    {
        "key": "first_engagement",
        "label": "First engagement",
        "metric": "first_eng_acc",
        "source": "metrics",
        "n_key": "first_eng_n",
        "min_n": 20,
        "good": 0.5,
        "bad": 0.35,
        "reverse": True,
        "drill": "Duel discipline: in the first 30 seconds of a round, take only the duel you chose, with your crosshair where the enemy will walk.",
        "why": "how often your opening duel ends in a hit rather than a miss",
    },
    {
        "key": "headshots",
        "label": "Head-level aim",
        "metric": "hs_kill_pct",
        "source": "metrics",
        "n_key": "gun_hits",
        "min_n": 60,
        "good": 0.35,
        "bad": 0.15,
        "reverse": True,
        "drill": "Tracking drill on a moving bot, crosshair glued to the head, no flicking: if your pre-aim is fine and this is low, the gap is the micro-adjust.",
        "why": "the share of your gun hits that were headshots",
    },
    {
        "key": "trades",
        "label": "Trade discipline",
        "metric": "traded_pct",
        "source": "stats",
        "n_key": "deaths",
        "min_n": 10,
        "good": 70.0,
        "bad": 30.0,
        "reverse": True,
        "drill": "Play one step behind a partner and punish the enemy reload: every death of his inside 5 seconds is yours to avenge.",
        "why": "the share of your deaths a teammate traded inside the trade window",
    },
    {
        "key": "isolation",
        "label": "Off-angle deaths",
        "metric": "isolated_pct",
        "source": "stats",
        "n_key": "deaths",
        "min_n": 10,
        "good": 10.0,
        "bad": 25.0,
        "reverse": False,
        "drill": "Take no duel further than 12 m from a teammate: if the angle needs a solo walk, come back with utility or a partner.",
        "why": "the share of your deaths with no teammate inside trade range",
    },
    {
        "key": "ultimate",
        "label": "Ultimate usage",
        "metric": "ults_used_per_round",
        "source": "derived",
        "n_key": "n_rounds",
        "min_n": 10,
        "good": 0.4,
        "bad": 0.15,
        "reverse": True,
        "drill": "Spend the ultimate on the round you buy up, not the round you save: a full-buy round with your ult is where it pays.",
        "why": "ultimates cast per round",
    },
)


def _severity(value: float, good: float, bad: float) -> float:
    """How far past the threshold a value sits, 0 at the good end and 1 at the far end.

    The scale runs from ``good`` to ``bad``, then one more equal span: the
    threshold itself reads 0.5 and a value twice as far from ``good`` as the
    threshold reads 1.0. Bounded, so metrics in different units can be sorted
    against each other and one terrible number cannot drown the rest.
    """
    if good == bad:
        return 0.0
    gap = abs(value - good)
    return round(min(1.0, gap / (2 * abs(bad - good))), 3)


def training(player_metrics: dict | None, player_stats: dict | None, n_rounds: int) -> list[dict]:
    """The measured weaknesses of one player, each with its drill.

    Pure: it reads only the metrics and stats blocks, so the AI dossier can use it
    without touching the export. A metric is only mentioned when its sample is at
    least the spec's minimum and its value sits on the bad side of the threshold.
    """
    m = player_metrics or {}
    s = player_stats or {}
    out = []
    for spec in TRAINING:
        if spec["source"] == "metrics":
            value = m.get(spec["metric"])
        elif spec["source"] == "stats":
            value = s.get(spec["metric"])
        else:
            rounds = n_rounds
            casts = s.get("ults_used")
            value = round(casts / rounds, 2) if (casts is not None and rounds) else None
        if spec["source"] == "derived":
            n = n_rounds
        else:
            n = m.get(spec["n_key"]) if spec["source"] == "metrics" else s.get(spec["n_key"])
        if value is None or n is None or n < spec["min_n"]:
            continue
        weak = value < spec["bad"] if spec["reverse"] else value > spec["bad"]
        if not weak:
            continue
        out.append(
            {
                "key": spec["key"],
                "label": spec["label"],
                "value": value,
                "n": n,
                "good": spec["good"],
                "threshold": spec["bad"],
                "reverse": spec["reverse"],
                "severity": _severity(float(value), spec["good"], spec["bad"]),
                "drill": spec["drill"],
                "why": spec["why"],
            }
        )
    out.sort(key=lambda w: -w["severity"])
    return out


def build(store, replay_id: str) -> dict:
    """The judgement for one replay, computed now and not kept."""
    payload = stats_mod.build(store, replay_id)
    players = payload.get("players") or []
    rounds = payload.get("rounds") or []
    export_dir = store.export_dir(replay_id)
    by_state, by_char = _subject_maps(players)
    try:
        scanned = _scan(export_dir, rounds, by_state, by_char)
    except Exception:
        scanned = {"plants": {}, "contacts": {}}
    try:
        ult = deaths_mod._ultimates(export_dir, players)
    except Exception:
        ult = {}
    rep: dict[str, Any] = summarize(
        payload, scanned.get("plants"), scanned.get("contacts"), ult
    )
    metrics_payload = None
    try:
        metrics_payload = metrics_mod.build(store, replay_id)
    except Exception:
        metrics_payload = None
    metrics_of = {}
    if metrics_payload:
        metrics_of = {p.get("subject"): p for p in metrics_payload.get("players") or []}
    rep["training"] = {
        p.get("subject"): training(
            metrics_of.get(p.get("subject")),
            p.get("stats"),
            rep["n_rounds"],
        )
        for p in players
    }
    rep["replay_id"] = replay_id
    rep["generated_at"] = int(time.time() * 1000)
    return rep
