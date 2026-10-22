"""Phase D coaching layer.

Normalizes the proxy metrics (Phase A/B) plus scoreboard stats (stats v7)
onto 0-100 against FIXED baselines, builds the 6-axis power radar (Reflexes,
Aim, Precision, Movement Discipline, Teamplay, Impact) with team averages
and an A-E overall badge, and produces the prescription card + headline
verdict.

Everything here is our own scale derived from replay-extractable proxies -
it is NOT OP.GG's scale and cross-tool scores must not be compared 1:1.
Later: per-user history could replace the fixed constants.
"""

from __future__ import annotations

import metrics as metrics_mod
import stats as stats_mod

COACH_VERSION = 1

AXES = ("reflexes", "aim", "precision", "movement", "teamplay", "impact")

AXIS_LABEL = {
    "reflexes": "Reflexes",
    "aim": "Aim",
    "precision": "Precision",
    "movement": "Movement Discipline",
    "teamplay": "Teamplay",
    "impact": "Impact",
}

# (worst -> 0, best -> 100) fixed baselines per raw metric.
BANDS = {
    "reflexes": (1150.0, 350.0),   # reaction ms (lower better)
    "aim": (18.0, 3.0),            # median pre-aim angle deg (lower better)
    "precision": (5.0, 35.0),      # head hits / gun hits % (higher better)
    "movement": (50.0, 92.0),      # counter-strafe % (higher better)
    "teamplay_traded": (20.0, 70.0),  # own deaths traded % (higher better)
    "teamplay_kast": (50.0, 82.0),    # KAST % (higher better)
    "impact": (90.0, 230.0),       # ADR incl utility (higher better)
}

WEIGHTS = {
    "reflexes": 0.15,
    "aim": 0.20,
    "precision": 0.20,
    "movement": 0.10,
    "teamplay": 0.15,
    "impact": 0.20,
}

GRADE_BANDS = ((75.0, "A"), (62.0, "B"), (48.0, "C"), (35.0, "D"))

ADVICE = {
    "reflexes": ("Your reaction to imminent duels is slow. Hold off-angles "
                 "closer to cover, pre-place the crosshair at head level and "
                 "avoid wide swings into multiple enemies."),
    "aim": ("Your crosshair starts far from the enemy head. Track lanes at "
            "head height, stop-aim before shooting and pre-aim the angles "
            "you are already holding."),
    "precision": ("Too little of your damage lands on heads. Aim upper chest "
                  "at range and burst-fire instead of committing to long "
                  "sprays."),
    "movement": ("You shoot while moving too often. Kill with a "
                 "counter-strafe (tap A/D, then fire) so the first bullet "
                 "flies accurate."),
    "teamplay": ("Your deaths are rarely traded. Play next to a teammate, "
                 "hold tradeable angles and avoid dying alone deep in enemy "
                 "territory."),
    "impact": ("Your damage output is below the lobby. Take more duels with "
               "utility support and play mid-round picks instead of passive "
               "saves."),
}


def _score(v, band) -> float | None:
    """0-100 clamp; band = (worst, best) works for either direction."""
    if v is None:
        return None
    worst, best = band
    return round(max(0.0, min(100.0, 100.0 * (v - worst) / (best - worst))), 1)


def _grade(score) -> str | None:
    if score is None:
        return None
    for cut, g in GRADE_BANDS:
        if score >= cut:
            return g
    return "E"


def _confidence(n) -> str:
    if n is None or n <= 0:
        return "none"
    if n >= 20:
        return "high"
    if n >= 8:
        return "medium"
    return "low"


def _overall(axes: dict) -> float | None:
    num = 0.0
    den = 0.0
    for a in AXES:
        s = axes.get(a)
        if s is None:
            continue
        num += s * WEIGHTS[a]
        den += WEIGHTS[a]
    if den <= 0:
        return None
    return round(num / den, 1)


def _player_block(m: dict, s: dict) -> dict:
    mstats = s.get("stats") or {}

    reaction_ms = m.get("reaction_ms")
    pre_aim = m.get("pre_aim_deg")
    hs = m.get("hs_pct")
    cstrf = m.get("counter_strafe_pct")
    adr = mstats.get("adr")
    traded = mstats.get("traded_pct")
    kast = mstats.get("kast_pct")

    axes: dict[str, float | None] = {}
    axes["reflexes"] = _score(reaction_ms, BANDS["reflexes"])
    axes["aim"] = _score(pre_aim, BANDS["aim"])
    axes["precision"] = _score(None if hs is None else 100.0 * hs, BANDS["precision"])
    axes["movement"] = _score(None if cstrf is None else 100.0 * cstrf, BANDS["movement"])

    tp_parts = []
    if traded is not None:
        tp_parts.append((_score(traded, BANDS["teamplay_traded"]), 0.6))
    if kast is not None:
        tp_parts.append((_score(kast, BANDS["teamplay_kast"]), 0.4))
    tp_parts = [(v, w) for v, w in tp_parts if v is not None]
    if tp_parts:
        num = sum(v * w for v, w in tp_parts)
        den = sum(w for _, w in tp_parts)
        axes["teamplay"] = round(num / den, 1)
    else:
        axes["teamplay"] = None
    axes["impact"] = _score(adr, BANDS["impact"])

    axis_n = {
        "reflexes": m.get("reaction_n") or 0,
        "aim": m.get("pre_aim_n") or 0,
        "precision": m.get("gun_hits") or 0,
        "movement": m.get("shots_classified") or 0,
        "teamplay": mstats.get("deaths") or 0,
        "impact": None,  # rounds-based; filled below
    }

    overall = _overall(axes)
    grade = _grade(overall)

    def raw(axis):
        if axis == "reflexes":
            return None if reaction_ms is None else f"{reaction_ms:.0f} ms"
        if axis == "aim":
            return None if pre_aim is None else f"{pre_aim:.1f}°"
        if axis == "precision":
            return None if hs is None else f"{100.0 * hs:.1f}% HS"
        if axis == "movement":
            return None if cstrf is None else f"{100.0 * cstrf:.1f}% at ≤100 cm/s"
        if axis == "teamplay":
            bits = []
            if traded is not None:
                bits.append(f"{traded:.1f}% traded")
            if kast is not None:
                bits.append(f"{kast:.1f}% KAST")
            return " · ".join(bits) or None
        if axis == "impact":
            return None if adr is None else f"{adr:.1f} ADR"
        return None

    # prescription: weakest axis that has a real score
    cand = [(axes[a], a) for a in AXES if axes.get(a) is not None and axis_n.get(a, 0) and axis_n[a] > 0]
    prescription = None
    if cand:
        worst_score, worst_axis = min(cand, key=lambda t: t[0])
        n = axis_n.get(worst_axis) or 0
        conf = _confidence(n)
        text = ADVICE[worst_axis]
        if conf == "low":
            text += " (small sample - treat as a hint)"
        prescription = {
            "axis": worst_axis,
            "label": AXIS_LABEL[worst_axis],
            "score": worst_score,
            "grade": _grade(worst_score),
            "n": n,
            "confidence": conf,
            "raw": raw(worst_axis),
            "text": text,
        }

    # verdict: best vs worst
    scored = [(axes[a], a) for a in AXES if axes.get(a) is not None]
    verdict = None
    best_axis = best_score = worst_axis2 = None
    if scored:
        best_score, best_axis = max(scored, key=lambda t: t[0])
        w = min(scored, key=lambda t: t[0])
        worst_axis2 = w[1]
        if best_axis == w[1]:
            txt = f"Balanced profile - {AXIS_LABEL[best_axis]} is your identity ({best_score:.0f}/100)."
        elif w[0] >= 70.0:
            txt = (f"Strong profile: {AXIS_LABEL[best_axis]} stood out ({best_score:.0f}/100) "
                   f"and no axis sits below the baseline.")
        else:
            txt = (f"Your {AXIS_LABEL[best_axis]} stood out ({best_score:.0f}/100). "
                   f"Now raise your {AXIS_LABEL[w[1]]}.")
        verdict = {"text": txt, "best_axis": best_axis, "best_score": best_score,
                   "worst_axis": w[1], "worst_score": w[0]}

    return {
        "subject": s.get("subject") or m.get("subject"),
        "team": s.get("team"),
        "agent_uuid": s.get("agent_uuid"),
        "axes": axes,
        "axis_n": axis_n,
        "confidence": {a: _confidence(axis_n.get(a)) for a in AXES},
        "overall": overall,
        "grade": grade,
        "prescription": prescription,
        "verdict": verdict,
    }


def build(store, replay_id: str) -> dict:
    metrics_data = metrics_mod.build(store, replay_id)
    stats_data = stats_mod.build(store, replay_id)
    n_rounds = stats_data.get("overview", {}).get("n_rounds") or metrics_data.get("n_rounds") or 0

    by_subject = {p.get("subject"): p for p in stats_data.get("players", [])}
    players = []
    for m in metrics_data.get("players", []):
        s = by_subject.get(m.get("subject")) or {}
        blk = _player_block(m, s)
        blk["axis_n"]["impact"] = n_rounds
        blk["confidence"]["impact"] = _confidence(n_rounds)
        players.append(blk)

    teams: dict[str, dict] = {}
    for tm in ("Blue", "Red"):
        members = [p for p in players if p.get("team") == tm]
        t_axes = {}
        for a in AXES:
            vals = [p["axes"][a] for p in members if p["axes"].get(a) is not None]
            t_axes[a] = round(sum(vals) / len(vals), 1) if vals else None
        t_over = _overall(t_axes)
        teams[tm] = {
            "axes": t_axes,
            "overall": t_over,
            "grade": _grade(t_over),
            "n_players": len(members),
        }

    return {
        "version": COACH_VERSION,
        "replay_id": replay_id,
        "generated_at": None,
        "n_rounds": n_rounds,
        "axes": list(AXES),
        "axis_label": AXIS_LABEL,
        "weights": WEIGHTS,
        "bands": BANDS,
        "players": players,
        "teams": teams,
        "notes": [
            "0-100 scores are our own scale locked to fixed baselines - not comparable with OP.GG 1:1.",
            "Inputs are proxy metrics (no visibility data in replays): aim/reflexes come from the engagement model.",
            "A≥75 B≥62 C≥48 D≥35 else E (weighted mean of available axes).",
            "Confidence tags follow sample size: high n≥20, medium n≥8, low n<8.",
        ],
    }
