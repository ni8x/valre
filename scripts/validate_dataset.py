"""Print a validation report for every ingested replay.

Usage (from the repo root):  python -X utf8 scripts/validate_dataset.py

Reads the version-gated stats/metrics/coach caches (building them when stale)
and prints one line per replay plus totals, so the same numbers the test suite
asserts on can be inspected by hand and pasted into the ledger.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import coach  # noqa: E402
import metrics  # noqa: E402
import spatial  # noqa: E402
import stats  # noqa: E402
import store as sm  # noqa: E402


def insights_ok(insights) -> bool:
    """Every insight record is well formed and no rate claim is below the floor."""
    for rec in insights:
        if not isinstance(rec, dict) or set(rec) != {"text", "detail", "n", "kind"}:
            return False
        if not isinstance(rec["text"], str) or not rec["text"].strip():
            return False
        if rec["detail"] is not None and not str(rec["detail"]).strip():
            return False
        if rec["n"] is not None and not (isinstance(rec["n"], int) and rec["n"] >= 1):
            return False
        if rec["kind"] not in stats.INSIGHT_KINDS:
            return False
        is_rate = (rec["kind"] in stats.RATE_INSIGHT_KINDS
                   or "%" in rec["text"] or "%" in (rec["detail"] or ""))
        if is_rate and (rec["n"] is None or rec["n"] < stats.MIN_RATE_SAMPLE):
            return False
    return True


def row(rid: str) -> dict:
    s = stats.build(sm.store, rid)
    m = metrics.build(sm.store, rid)
    c = coach.build(sm.store, rid)

    players = s["players"]
    rounds = s["rounds"]
    kills = sum(p["stats"]["kills"] for p in players)
    deaths = sum(p["stats"]["deaths"] for p in players)
    suicides = sum(1 for r in rounds for d in (r.get("deaths") or []) if d.get("suicide"))
    winners = [r.get("winner") for r in rounds]
    spikes = sum(len(r.get("spikes") or []) for r in rounds)
    explosions = sum(
        1 for r in rounds for sp in (r.get("spikes") or []) if sp.get("kind") == "exploded"
    )
    defused = sum(
        1 for r in rounds for sp in (r.get("spikes") or []) if sp.get("kind") == "defused"
    )
    explosion_deaths = 0
    for r in rounds:
        exps = [sp["t"] for sp in (r.get("spikes") or []) if sp.get("kind") == "exploded"]
        if not exps:
            continue
        lo = min(exps)
        explosion_deaths += sum(
            1
            for d in (r.get("deaths") or [])
            if not d.get("killer") and lo <= d.get("t", 0) <= lo + 2000
        )

    sides_rounds = sum(1 for r in rounds if r.get("sides"))
    attackers: dict[str, int] = {}
    for r in rounds:
        sd = r.get("sides")
        if sd:
            attackers[sd["attacker"]] = attackers.get(sd["attacker"], 0) + 1

    buys: dict[str, int] = {}
    alvs: list[float] = []
    for r in rounds:
        for cell in (r.get("economy") or {}).values():
            buy = cell.get("buy") or "unknown"
            buys[buy] = buys.get(buy, 0) + 1
            if cell.get("alv"):
                alvs.append(cell["alv"])

    t = spatial.tracks(sm.store, rid, 1)
    h = spatial.heatmap(sm.store, rid, mode="all")

    weapon_rows = sum(len(p.get("weapons") or {}) for p in m["players"])
    weapons_ok = all(
        (sum(r["shots"] for r in (p.get("weapons") or {}).values()) == p["shots"]
         and sum(r["hits"] for r in (p.get("weapons") or {}).values()) == p["hits"]
         and all((r["acc"] is None) == (r["shots"] == 0)
                 for r in (p.get("weapons") or {}).values()))
        for p in m["players"]
    )

    return {
        "id": rid,
        "map": (s["overview"].get("level_name") or "").rsplit("/", 1)[-1],
        "rounds": s["overview"].get("n_rounds"),
        "players": len(players),
        "kills": kills,
        "deaths": deaths,
        "suicides": suicides,
        "kills_ok": deaths - kills == suicides + explosion_deaths,
        "expl_deaths": explosion_deaths,
        "none_winners": winners.count(None),
        "winners_ok": all(w in ("Blue", "Red", None) for w in winners)
        and all(
            r.get("winner_source") == "unknown" for r in rounds if r.get("winner") is None
        ),
        "sides": sides_rounds,
        "attackers": ",".join(f"{k}:{v}" for k, v in sorted(attackers.items())),
        "buys": ",".join(f"{k}:{v}" for k, v in sorted(buys.items())) or "-",
        "alv": f"{min(alvs):.0f}-{max(alvs):.0f}" if alvs else "-",
        "econ_ok": bool(rounds) and all(r.get("economy") for r in rounds),
        "weapon_rows": weapon_rows,
        "weapons_ok": weapons_ok,
        "ins": len(s.get("insights") or []),
        "insights_ok": insights_ok(s.get("insights") or []),
        "spikes": spikes,
        "exploded": explosions,
        "defused": defused,
        "stats_v": s["version"],
        "metrics_v": m["version"],
        "coach_v": c["version"],
        "unres_shots": m["validation"].get("unresolved_shots"),
        "unres_hits": m["validation"].get("unresolved_hits"),
        "kill_feed": m["validation"].get("kill_feed_pairs"),
        "tracks": len(t.get("tracks") or []),
        "heat_cells": len(h.get("cells") or []),
    }


def main() -> int:
    ids = [i for i in sm.store.ids() if sm.store.manifest_path(i).exists()]
    print(f"{len(ids)} ingested replays\n")
    bad = 0
    for rid in ids:
        try:
            r = row(rid)
        except Exception:
            traceback.print_exc()
            print(f"{rid} FAILED\n")
            bad += 1
            continue
        flag = "ok" if (r["kills_ok"] and r["winners_ok"] and r["econ_ok"] and r["weapons_ok"]
                        and r["insights_ok"]) else "CHECK"
        print(
            f"{flag:5} {rid} {r['map'][:14]:14} rounds={r['rounds']:<3} players={r['players']:<3} "
            f"kills={r['kills']:<4} deaths={r['deaths']:<4} suicides={r['suicides']:<2} "
            f"expl={r['expl_deaths']:<2} "
            f"k-d-suicide={r['kills_ok']} none_winners={r['none_winners']:<2} "
            f"sides={r['sides']:<3} att={r['attackers']:<14} "
            f"buys={r['buys']} alv={r['alv']} wpn={r['weapon_rows']:<3} "
            f"spikes={r['spikes']:<3} (exp {r['exploded']}/def {r['defused']}) "
            f"stats_v={r['stats_v']} metrics_v={r['metrics_v']} coach_v={r['coach_v']} "
            f"ins={r['ins']:<2} ins_ok={r['insights_ok']} "
            f"unres={r['unres_shots']}/{r['unres_hits']} killfeed={r['kill_feed']} "
            f"tracks={r['tracks']} heat={r['heat_cells']}"
        )
    print(f"\nfailures: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
