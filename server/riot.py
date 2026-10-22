"""Phase 14: the optional Riot sync that turns a Riot ID into a subject.

A replay cannot name anyone. The only way to attach a real Riot ID to a subject
is to ask Riot for the account's recent matches and line one up with the export:
same map, same start time (a 120 s window - Riot's match start and the replay's
first frame are not the same clock), then same agent and same team.

Nothing here runs without ``RIOT_API_KEY``. With no key every call says so and
writes nothing: the offline evidence picker in ``identity`` still works.

Env: ``RIOT_API_KEY`` (required), ``RIOT_REGION`` (platform, default ``ap``),
``RIOT_ROUTING`` (regional cluster, default ``asia``).
"""
from __future__ import annotations

import os
import urllib.parse
from typing import Any

import requests

import identity

HOST = "https://{host}.api.riotgames.com"
START_TOLERANCE_S = 120.0

PLATFORMS = ("ap", "br", "eu", "latam", "na", "sea", "kr")
ROUTINGS = ("asia", "americas", "europe", "sea")


class RiotError(Exception):
    """A Riot call that could not answer (no key, 404, non-200, network)."""


def key() -> str:
    return (os.environ.get("RIOT_API_KEY") or "").strip()


def region() -> str:
    got = (os.environ.get("RIOT_REGION") or "ap").strip().lower()
    return got if got in PLATFORMS else "ap"


def routing() -> str:
    got = (os.environ.get("RIOT_ROUTING") or "asia").strip().lower()
    return got if got in ROUTINGS else "asia"


def status() -> dict[str, Any]:
    """Whether the sync is usable, without ever echoing the key."""
    on = bool(key())
    return {
        "configured": on,
        "region": region(),
        "routing": routing(),
        "tolerance_s": START_TOLERANCE_S,
        "note": (
            "Riot sync is on: the account's recent matches are matched to the export by start time, "
            "map, agent and team."
            if on
            else "No RIOT_API_KEY on this machine, so the Riot sync is off. The offline evidence "
            "picker still works - set RIOT_API_KEY and the sync appears."
        ),
    }


def _get(url: str, timeout: float = 12.0):
    token = key()
    if not token:
        raise RiotError("no RIOT_API_KEY on this machine")
    try:
        res = requests.get(url, headers={"X-Riot-Token": token}, timeout=timeout)
    except requests.RequestException as exc:  # noqa: BLE001
        raise RiotError(f"Riot did not answer: {exc}") from None
    if res.status_code == 404:
        return None
    if res.status_code == 403:
        raise RiotError("Riot refused the key (403) - it may be expired or for another region")
    if res.status_code != 200:
        raise RiotError(f"Riot answered {res.status_code}")
    return res.json()


def account(game_name: str, tag_line: str) -> dict[str, Any]:
    """Riot ID -> puuid (account-v1)."""
    name = urllib.parse.quote((game_name or "").strip(), safe="")
    tag = urllib.parse.quote((tag_line or "").strip(), safe="")
    if not name or not tag:
        raise RiotError("a Riot ID needs both the name and the tag, e.g. knightx07#EUW")
    url = HOST.format(host=routing()) + f"/riot/account/v1/accounts/by-riot-id/{name}/{tag}"
    got = _get(url)
    if not got:
        raise RiotError(f"Riot does not know {game_name}#{tag_line}")
    return got


def recent(puuid: str, count: int = 10) -> list[dict[str, Any]]:
    """The account's recent VAL matches (match-v5 matchlist)."""
    url = HOST.format(host=region()) + f"/val/match/v1/matchlists/by-puuid/{puuid}"
    got = _get(url)
    history = (got or {}).get("history") or []
    return history[: max(int(count), 1)]


def match(match_id: str) -> dict[str, Any]:
    url = HOST.format(host=region()) + f"/val/match/v1/matches/{match_id}"
    got = _get(url)
    if not got:
        raise RiotError(f"Riot has no match {match_id}")
    return got


def _tail(path: str | None) -> str:
    return (path or "").rsplit("/", 1)[-1].strip().lower()


def tag(store, replay_id: str, game_name: str, tag_line: str,
        tolerance_s: float = START_TOLERANCE_S) -> dict[str, Any]:
    """Line a Riot account up with a subject in this export, or say why not."""
    if not key():
        return {
            "subject": None,
            "why": status()["note"],
            "asked": {"game_name": game_name, "tag_line": tag_line},
        }

    sig = identity.signature(store, replay_id)
    start_ms = sig.get("start_ms")
    level = _tail(sig.get("level"))
    if start_ms is None or not level:
        return {"subject": None, "why": "this export has no readable start time or map"}

    try:
        acct = account(game_name, tag_line)
        puuid = acct.get("puuid")
        entries = recent(puuid, 10)
    except RiotError as exc:
        return {"subject": None, "why": str(exc)}

    # the matchlist carries the start time, so ask for the closest few only
    near = sorted(
        entries,
        key=lambda e: abs((e.get("gameStartTimeMillis") or 0) - start_ms),
    )[:3]

    for entry in near:
        mid = entry.get("matchId")
        if not mid:
            continue
        try:
            got = match(mid)
        except RiotError as exc:
            return {"subject": None, "why": str(exc)}
        info = got.get("matchInfo") or {}
        riot_start = info.get("gameStartMillis")
        if riot_start is None:
            continue
        delta_s = (riot_start - start_ms) / 1000.0
        if abs(delta_s) > tolerance_s:
            continue
        if _tail(info.get("mapId")) != level:
            continue
        me = next(
            (p for p in got.get("players") or [] if p.get("puuid") == puuid),
            None,
        )
        if not me:
            continue
        agent_uuid = me.get("characterId")
        team = me.get("teamId")
        matches = [
            p
            for p in sig["players"]
            if p.get("agent_uuid") == agent_uuid
            and str(p.get("team") or "").lower() == str(team or "").lower()
        ]
        evidence = {
            "riot_id": f"{game_name}#{tag_line}",
            "match_id": mid,
            "map": sig.get("map"),
            "start_delta_s": round(delta_s, 1),
            "agent": next((p.get("agent") for p in matches), None),
            "team": team,
        }
        if len(matches) == 1:
            return {"subject": matches[0]["subject"], "why": None, "evidence": evidence}
        if not matches:
            return {
                "subject": None,
                "why": (
                    f"Riot's {info.get('mapId')} match at {evidence['start_delta_s']} s says agent "
                    f"{agent_uuid} on team {team}, but no player in this export matches both"
                ),
                "evidence": evidence,
            }
        return {
            "subject": None,
            "why": "two players in this export share that agent and team - the replay cannot split them",
            "evidence": evidence,
        }

    return {
        "subject": None,
        "why": (
            f"none of the account's last {len(entries)} matches starts within {tolerance_s:g} s of "
            f"this export ({sig.get('start_local')}) on {sig.get('map')}"
        ),
        "evidence": None,
    }