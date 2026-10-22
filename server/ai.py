"""AI analysis layer (Phase E).

Provider-agnostic OpenAI-compatible chat client. Defaults to the local
FreeLLMAPI gateway (http://127.0.0.1:31415/v1); its unified API key is read
from the FreeLLMAPI sqlite settings when available. Everything can be
overridden with environment variables:

    AI_BASE_URL   e.g. http://127.0.0.1:31415/v1  (or http://127.0.0.1:11434/v1 for Ollama)
    AI_API_KEY    bearer token (any value for keyless local servers)
    AI_MODEL      model id (default: auto - routed by the gateway)

ask() builds a compact dossier from the cached metrics/stats/coach layers and
posts a single chat completion. No streaming; ~120 s timeout.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import requests

import analysis
import coach as coach_mod
import judge as judge_mod
import metrics as metrics_mod
import regions as regions_mod
import stats as stats_mod
import valorant_api

DEFAULT_BASE_URL = "http://127.0.0.1:31415/v1"
DEFAULT_KEY = "freellmapi-d11f26b92d2d70cfee3f675b3087369392f2b0abc2e684d1"
DEFAULT_MODEL = os.environ.get("AI_MODEL", "auto")
_FREELMAPI_DB = Path(os.environ.get("APPDATA", "")) / "FreeLLMAPI" / "freeapi.db"

SYSTEM_PROMPT = (
    "You are 'VRL Coach', an expert Valorant analyst embedded in a local replay viewer. "
    "You are given a JSON dossier extracted from the actual match replay plus our proxy metrics. "
    "Rules:\n"
    "- Answer ONLY from the dossier. Never invent players, stats or events.\n"
    "- Cite concrete numbers (e.g. 'Raze: 27 kills, ADR 178.7, HS 17.9%').\n"
    "- Some aim/movement fields are geometric proxies (~) computed without visibility data; "
    "mention this briefly when your answer leans on them.\n"
    "- Be direct: verdict first, then 3-6 short bullet points of evidence. "
    "Keep it under ~250 words unless the user asks for more detail.\n"
    "- Answer in the language of the question.\n"
    "Dossier key legend: p=player id, ag=agent, tm=team, k/d/a=kills/deaths/assists, "
    "kast=KAST%, hk=HS-kill%, hdm=head dmg share of damage, wb=wallbang kills, "
    "ek/ed=entry kills/deaths, tr=traded-death%, iso=isolated deaths, cl=clutches won/attempted, "
    "mk=multikills, sh/gh=bullets/gun hits, acc=accuracy%, hh=head hits, hsp=headshot-hit%, "
    "gadr=gun ADR, spr=sprays, spacc=spray accuracy%, eng=engagements, "
    "pa=pre-aim deg (~), pan=pre-aim samples, pac=pre-aim completion%, hl=head-level%, "
    "rt/rtn=reaction ms/samples (~), ttd/ttdn=time-to-damage ms (~), cs=counter-strafe% (~), "
    "fe=first-engagement accuracy%, spr_cm/spr_deg/spr_ttk=spray radius cm/spread deg/TTK ms (~), "
    "co=coach layer {ov=overall, gr=grade, ax=six-axis scores, cf=confidence, wk=weakest axis, wkr=its raw value}. "
    "rz=where that player spent most of the match (game callout names, share of time; nearest centre, "
    "2D, no walls). "
    "Rounds: w=winner, kb/kr=kills by Blue/Red, fb=first-blood team, sp=spikes, "
    "site=planted site letter (the export's own index, else the callout nearest the plant), "
    "ut=untraded deaths per team, is=isolated deaths per team (b/r=Blue/Red)."
)


class AIError(Exception):
    """Raised when the AI endpoint is unreachable or returns an error."""


def _unified_key() -> str:
    try:
        con = sqlite3.connect(f"file:{_FREELMAPI_DB}?mode=ro", uri=True, timeout=2)
        row = con.execute(
            "select value from settings where key='unified_api_key'"
        ).fetchone()
        con.close()
        if row and row[0]:
            return str(row[0])
    except Exception:
        pass
    return DEFAULT_KEY


def config() -> dict[str, str]:
    return {
        "base_url": os.environ.get("AI_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        "api_key": os.environ.get("AI_API_KEY") or _unified_key(),
        "model": os.environ.get("AI_MODEL", DEFAULT_MODEL),
    }


def _headers(cfg: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {cfg['api_key']}"} if cfg["api_key"] else {}


def status() -> dict[str, Any]:
    """Cheap health probe for the UI (models list)."""
    cfg = config()
    try:
        r = requests.get(f"{cfg['base_url']}/models", headers=_headers(cfg), timeout=4)
        r.raise_for_status()
        models = r.json().get("data", [])
        return {
            "ok": True,
            "base_url": cfg["base_url"],
            "model": cfg["model"],
            "models": len(models),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "base_url": cfg["base_url"],
            "model": cfg["model"],
            "error": f"{type(exc).__name__}: {exc}",
        }


def _pct(v: Any) -> float | None:
    return None if v is None else round(float(v) * 100.0, 1)


def _r(v: Any, nd: int = 1) -> float | None:
    return None if v is None else round(float(v), nd)


def _drill_line(items: list[dict[str, Any]] | None) -> str | None:
    """The one most urgent measured weakness for a player, as a single line."""
    if not items:
        return None
    top = items[0]
    return (
        f"{top.get('label')}: {top.get('drill')} "
        f"(measured {top.get('value')} against a threshold of {top.get('threshold')}, "
        f"n={top.get('n')})"
    )


def _region_line(items) -> str | None:
    """'Mid Top 34.2%, A Main 18.0%' - the top named places a player held."""
    if not items:
        return None
    return ", ".join(f"{it['label']} {it['pct']}%" for it in items if it.get("label"))


def build_context(store: Any, replay_id: str) -> dict[str, Any]:
    """Compact dossier: match overview + per-player metrics/coach + round flow."""
    m = metrics_mod.build(store, replay_id)
    s = stats_mod.build(store, replay_id)
    c = coach_mod.build(store, replay_id)

    try:
        j = judge_mod.build(store, replay_id)
    except Exception:  # noqa: BLE001
        j = {}
    j_by = {p["subject"]: p for p in j.get("players", [])}
    train_by = j.get("training", {}) or {}

    try:
        agent_names = {
            (a.get("uuid") or "").lower(): a.get("name") or a.get("displayName")
            for a in valorant_api.agents()
            if a.get("uuid")
        }
    except Exception:  # noqa: BLE001
        agent_names = {}

    m_by = {p["subject"]: p for p in m.get("players", [])}
    s_by = {p["subject"]: p for p in s.get("players", [])}
    c_by = {p["subject"]: p for p in c.get("players", [])}

    # where the fight happened, in the game's own words (callout names)
    geo = regions_mod.regions_for_replay(store, replay_id)
    site_by_round: dict[int, str] = {}
    for rn, plant in (j.get("plants") or {}).items():
        if not isinstance(plant, dict):
            continue
        letter = regions_mod.site_letter(geo.get("proj"), geo.get("regions") or [], plant)
        if letter:
            try:
                site_by_round[int(rn)] = letter
            except (TypeError, ValueError):
                continue
    rz_by_subject: dict[str, Any] = {}
    try:
        manifest = analysis.load_manifest(store.manifest_path(replay_id))
        pawns = regions_mod.pawns_by_subject(manifest)
        export = store.export_dir(replay_id)
        for subj, guids in pawns.items():
            top = regions_mod.time_in_regions(export, geo.get("proj"), geo.get("regions") or [], guids)
            if top:
                rz_by_subject[subj] = top
    except Exception:  # noqa: BLE001
        rz_by_subject = {}

    players: list[dict[str, Any]] = []
    for subj, sp in s_by.items():
        mp = m_by.get(subj, {})
        cp = c_by.get(subj, {})
        st = sp.get("stats", {})
        pres = cp.get("prescription") or {}
        jp = j_by.get(subj, {})
        players.append(
            {
                "p": subj[:8],
                "ag": agent_names.get((sp.get("agent_uuid") or "").lower()) or (sp.get("agent_uuid") or "")[:8].lower(),
                "tm": sp.get("team"),
                "rz": _region_line(rz_by_subject.get(subj)),
                "k": st.get("kills"),
                "d": st.get("deaths"),
                "a": st.get("assists"),
                "kd": _r(st.get("kd"), 2),
                "acs": _r(st.get("acs")),
                "adr": _r(st.get("adr")),
                "kast": _pct(st.get("kast_pct")),
                "hk": _pct(st.get("hs_pct")),
                "hdm": _pct(st.get("head_damage_pct")),
                "wb": st.get("wallbangs"),
                "ek": st.get("entry_kills"),
                "ed": st.get("entry_deaths"),
                "tr": _pct(st.get("traded_pct")),
                "iso": st.get("isolated_deaths"),
                "cl": f"{st.get('clutch_wins') or 0}/{st.get('clutch_attempts') or 0}",
                "mk": st.get("multikills"),
                # shots / accuracy (from the 3D hit layer)
                "sh": mp.get("shots"),
                "gh": mp.get("gun_hits"),
                "acc": _pct(mp.get("accuracy")),
                "hh": mp.get("head_hits"),
                "hsp": _pct(mp.get("hs_pct")),
                "gadr": _r(mp.get("adr_gun")),
                "spr": mp.get("sprays"),
                "spacc": _pct(mp.get("spray_acc")),
                "eng": mp.get("engagements"),
                # aim proxies (~)
                "pa": _r(mp.get("pre_aim_deg")),
                "pan": mp.get("pre_aim_n"),
                "pac": _pct(mp.get("pre_aim_complete")),
                "hl": _pct(mp.get("head_level_pct")),
                "rt": _r(mp.get("reaction_ms"), 0),
                "rtn": mp.get("reaction_n"),
                "ttd": _r(mp.get("ttd_ms"), 0),
                "ttdn": mp.get("ttd_n"),
                "cs": _pct(mp.get("counter_strafe_pct")),
                "fe": _pct(mp.get("first_eng_acc")),
                "spr_cm": _r(mp.get("spray_radius_cm")),
                "spr_deg": _r(mp.get("spray_spread_deg")),
                "spr_ttk": _r(mp.get("spray_ttk_ms"), 0),
                # coach layer
                "co": {
                    "ov": cp.get("overall"),
                    "gr": cp.get("grade"),
                    "ax": {k: _r(v, 0) for k, v in (cp.get("axes") or {}).items()},
                    "cf": cp.get("confidence"),
                    "wk": pres.get("label"),
                    "wkr": pres.get("raw"),
                },
                # judging layer: our weighted blame, and the one drill that matters most
                "blame": {
                    "pts": jp.get("points"),
                    "share": _r(jp.get("share_pct")),
                    "gr": jp.get("grade"),
                    "ago": jp.get("case_against"),
                    "for": jp.get("case_for"),
                },
                "drill": _drill_line(train_by.get(subj)),
            }
        )

    rounds: list[dict[str, Any]] = []
    for r in s.get("rounds", []):
        deaths = r.get("deaths", [])
        kills = {"Blue": 0, "Red": 0}
        untraded = {"Blue": 0, "Red": 0}
        isolated = {"Blue": 0, "Red": 0}
        fb = None
        for d in deaths:
            if d.get("suicide"):
                continue
            kt = d.get("killer_team")
            vt = d.get("victim_team")
            if kt in kills:
                kills[kt] += 1
            if not d.get("traded") and vt in untraded:
                untraded[vt] += 1
            if d.get("isolated") and vt in isolated:
                isolated[vt] += 1
            if d.get("first_blood") and fb is None:
                fb = kt
        spikes = [sp.get("kind") for sp in (r.get("spikes") or [])]
        rounds.append(
            {
                "r": r.get("round"),
                "w": r.get("winner"),
                "kb": kills["Blue"],
                "kr": kills["Red"],
                "fb": fb,
                "sp": spikes,
                "site": site_by_round.get(r.get("round")),
                "ut": {"b": untraded["Blue"], "r": untraded["Red"]},
                "is": {"b": isolated["Blue"], "r": isolated["Red"]},
            }
        )

    ov = s.get("overview", {})
    return {
        "map": ov.get("level_name"),
        "build": ov.get("build"),
        "n_rounds": ov.get("n_rounds"),
        "score": ov.get("score"),
        "note": (
            "Fields derived geometrically without visibility data are marked 'proxy'. "
            "pre-aim/head-level/reaction/ttd/spray-radius/spread/TTK are proxies; "
            "counts, damage, kills, accuracy, trades, KAST, spikes and round winners are exact."
        ),
        "judge_note": (
            "blame points, share, grade, case_against/case_for and the drill come from our own "
            "judging layer: counted facts of the export times weights we publish. They are our "
            "reading, not a claim about intent, and a player can lose a round and still be right."
        ),
        "judge": {
            "teams": {
                team: {
                    "lost": tb.get("lost"),
                    "changeable": tb.get("changeable"),
                    "pts": tb.get("points"),
                    "summary": tb.get("summary"),
                    "devil": tb.get("devil"),
                }
                for team, tb in (j.get("teams") or {}).items()
            },
            "lost_rounds": [
                {
                    "r": lr.get("round"),
                    "lost": lr.get("loser"),
                    "buy": lr.get("buy"),
                    "vs": lr.get("opponent_buy"),
                    "why": [c.get("key") for c in (lr.get("classes") or [])],
                    "plant": bool(lr.get("plant")),
                }
                for lr in (j.get("lost_rounds") or [])
            ],
        },
        "players": players,
        "rounds": rounds,
        "team_avg": {
            team: {"ov": tb.get("overall"), "gr": tb.get("grade"), "ax": tb.get("axes")}
            for team, tb in (c.get("teams") or {}).items()
        },
    }


def ask(
    store: Any,
    replay_id: str,
    question: str,
    subject: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    cfg = config()
    ctx = build_context(store, replay_id)
    focus = ""
    if subject:
        match = [p for p in ctx["players"] if p["p"] == subject[:8]]
        if match:
            focus = f"\nFocus player: {match[0]['ag']} ({match[0]['p']})."
    user_msg = (
        "MATCH DOSSIER (JSON):\n"
        + json.dumps(ctx, ensure_ascii=False)
        + f"\n\nQUESTION: {question}{focus}"
    )
    payload = {
        "model": model or cfg["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": 700,
        "temperature": 0.3,
    }
    try:
        r = requests.post(
            f"{cfg['base_url']}/chat/completions",
            headers={**_headers(cfg), "Content-Type": "application/json"},
            json=payload,
            timeout=180,
        )
    except requests.exceptions.ConnectionError as exc:
        raise AIError(
            f"AI endpoint unreachable at {cfg['base_url']} - start FreeLLMAPI "
            f"(or set AI_BASE_URL / AI_API_KEY). [{exc}]"
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise AIError(f"AI request timed out after 180 s. [{exc}]") from exc

    if r.status_code != 200:
        detail = r.text[:400]
        raise AIError(f"AI endpoint returned HTTP {r.status_code}: {detail}")

    data = r.json()
    try:
        answer = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIError(f"Unexpected AI response shape: {str(data)[:300]}") from exc

    return {
        "answer": (answer or "").strip(),
        "model": data.get("model", cfg["model"]),
        "usage": data.get("usage"),
        "context": {
            "players": len(ctx["players"]),
            "rounds": len(ctx["rounds"]),
            "question": question,
            "focus": subject,
        },
    }
