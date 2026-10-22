"""valorant-api.com metadata cache + local asset (icon) sync.

Remote data is fetched once and cached under data/game/*.json. Icon images are
downloaded into data/assets/<kind>/<id>.<ext> so the UI works fully offline
after the first sync.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
GAME_DIR = DATA_DIR / "game"
ASSETS_DIR = DATA_DIR / "assets"

API = "https://valorant-api.com/v1"
UA = {"User-Agent": "valorant-replay-lab/0.1"}

_lock = threading.RLock()

# uuid -> map codename used in manifest level name (best-effort, refined by mapUrl)
CODENAME_HINTS = {
    "ascent": "Ascent",
    "duality": "Bind",
    "triad": "Haven",
    "bonsai": "Split",
    "port": "Icebox",
    "foxtrot": "Breeze",
    "canyon": "Fracture",
    "pitt": "Pearl",
    "jam": "Lotus",
    "juliett": "Sunset",
    "infinity": "Abyss",
    "rovuk": "Corrode",
    "range": "The Range",
}


def _get_json(url: str, cache: Path, force: bool = False) -> Any:
    GAME_DIR.mkdir(parents=True, exist_ok=True)
    if cache.exists() and not force:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    resp = requests.get(url, headers=UA, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    cache.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def agents(force: bool = False) -> list[dict[str, Any]]:
    with _lock:
        raw = _get_json(f"{API}/agents?isPlayableCharacter=true", GAME_DIR / "agents.json", force)
    out = []
    for a in raw.get("data", []):
        out.append(
            {
                "uuid": a.get("uuid"),
                "name": a.get("displayName"),
                "icon": a.get("displayIcon"),
                "bust": a.get("bustPortrait"),
                "role": (a.get("role") or {}).get("displayName"),
                "roleIcon": (a.get("role") or {}).get("displayIcon"),
                "abilities": [
                    {
                        "slot": ab.get("slot"),
                        "name": ab.get("displayName"),
                        "icon": ab.get("displayIcon"),
                    }
                    for ab in (a.get("abilities") or [])
                ],
            }
        )
    return out


def maps(force: bool = False) -> list[dict[str, Any]]:
    with _lock:
        raw = _get_json(f"{API}/maps", GAME_DIR / "maps.json", force)
    out = []
    for m in raw.get("data", []):
        out.append(
            {
                "uuid": m.get("uuid"),
                "name": m.get("displayName"),
                "mapUrl": m.get("mapUrl"),
                "listIcon": m.get("listViewIcon"),
                "splash": m.get("splash"),
                "icon": m.get("displayIcon"),
                "coordinates": m.get("coordinates"),
                "xMultiplier": m.get("xMultiplier"),
                "yMultiplier": m.get("yMultiplier"),
                "xScalarToAdd": m.get("xScalarToAdd"),
                "yScalarToAdd": m.get("yScalarToAdd"),
                "callouts": m.get("callouts"),
            }
        )
    return out


# The replay export names a few guns differently from valorant-api.com. This is the
# only place the two vocabularies are bridged; metrics.WEAPON_NAMES (the validated
# reference set) is never edited. Key = export display name, value = API displayName.
_WEAPON_NAME_ALIASES = {
    "Compact Pistol": "Bandit",
    "CompactPistol": "Bandit",
}


def _weapon_aliases(name: str | None) -> list[str]:
    if not name:
        return []
    return [name] + sorted(k for k, v in _WEAPON_NAME_ALIASES.items() if v == name)


def weapons(force: bool = False) -> list[dict[str, Any]]:
    with _lock:
        raw = _get_json(f"{API}/weapons", GAME_DIR / "weapons.json", force)
    out = []
    for w in raw.get("data", []):
        name = w.get("displayName")
        out.append(
            {
                "uuid": w.get("uuid"),
                "name": name,
                "category": (w.get("shopData") or {}).get("category"),
                "icon": w.get("displayIcon"),
                "killIcon": w.get("killStreamIcon"),
                "aliases": _weapon_aliases(name),
            }
        )
    return out


def map_for_level(level_name: str) -> dict[str, Any] | None:
    """Match manifest level path (e.g. /Game/Maps/Juliett/Juliett) to a map."""
    if not level_name:
        return None
    codename = level_name.rstrip("/").split("/")[-1].lower()
    for m in maps():
        url = (m.get("mapUrl") or "").rstrip("/").split("/")[-1].lower()
        if url and url == codename:
            return m
    for m in maps():
        if (m.get("name") or "").lower() == CODENAME_HINTS.get(codename, "").lower():
            return m
    hint = CODENAME_HINTS.get(codename, "").lower()
    if hint:
        for m in maps():
            if (m.get("name") or "").lower() == hint:
                return m
    return None


# ---- asset download -------------------------------------------------

_EXT_BY_KIND = {
    "agent": ".png",
    "map": ".png",
    "ability": ".png",
    "role": ".png",
    "weapon": ".png",
}


def _download(url: str, dest: Path) -> bool:
    if not url:
        return False
    if dest.exists() and dest.stat().st_size > 0:
        return True
    try:
        resp = requests.get(url, headers=UA, timeout=45)
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return True
    except Exception:
        return False


def sync_assets(force: bool = False) -> dict[str, int]:
    """Download all agent/map/ability icons. Returns counts written."""
    counts = {"agents": 0, "maps": 0, "abilities": 0, "weapons": 0, "failed": 0}
    for a in agents(force):
        if a["uuid"] and _download(a["icon"], ASSETS_DIR / "agent" / f"{a['uuid']}.png") and a["icon"]:
            counts["agents"] += 1
        for ab in a["abilities"]:
            if ab.get("icon"):
                key = f"{a['uuid']}_{ab['slot']}.png"
                if _download(ab["icon"], ASSETS_DIR / "ability" / key):
                    counts["abilities"] += 1
    for m in maps(force):
        if m["uuid"] and _download(m["listIcon"], ASSETS_DIR / "map" / f"{m['uuid']}.png"):
            counts["maps"] += 1
        if m["uuid"] and _download(m["icon"], ASSETS_DIR / "map" / f"{m['uuid']}_display.png"):
            counts["maps"] += 1
    for w in weapons(force):
        uuid = w.get("uuid")
        if not uuid:
            continue
        if w.get("icon") and _download(w["icon"], ASSETS_DIR / "weapon" / f"{uuid}.png"):
            counts["weapons"] += 1
        if w.get("killIcon") and _download(w["killIcon"], ASSETS_DIR / "weapon" / f"{uuid}_kill.png"):
            counts["weapons"] += 1
    return counts


def asset_path(kind: str, name: str) -> Path | None:
    p = ASSETS_DIR / kind / name
    if p.exists():
        return p
    base = ASSETS_DIR / kind
    if base.exists():
        for f in base.iterdir():
            if f.stem == name:
                return f
    return None
