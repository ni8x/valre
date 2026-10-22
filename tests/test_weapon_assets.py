"""Weapon metadata + icon download (Phase 5).

``/v1/weapons`` is cached exactly like the agent/map payloads. The replay export
names a few guns differently from valorant-api.com (``CompactPistol`` is exported
as "Compact Pistol" but the API calls it "Bandit"), so every record carries an
``aliases`` list the UI matches on. Nothing here touches the network: ``_get_json``
and ``_download`` are stubbed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import app as app_mod
import valorant_api

BANDIT_UUID = "410b2e0b-4ceb-1321-1727-20858f7f3477"
VANDAL_UUID = "9c82e19d-4575-0200-1a81-3eacf00cf872"
MELEE_UUID = "2f59173c-4bed-b6c3-2191-dea9b58be9c7"


def _gun(uuid: str, name: str, category: str | None) -> dict:
    return {
        "uuid": uuid,
        "displayName": name,
        "shopData": None if category is None else {"category": category},
        "displayIcon": f"https://media.example/{uuid}/icon.png",
        "killStreamIcon": f"https://media.example/{uuid}/kill.png",
    }


_PAYLOAD = {
    "data": [
        _gun(VANDAL_UUID, "Vandal", "Rifles"),
        _gun(BANDIT_UUID, "Bandit", "Pistols"),
        _gun(MELEE_UUID, "Melee", None),
    ]
}


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(valorant_api, "_get_json", lambda url, cache, force=False: _PAYLOAD)


def test_weapons_parses_icon_and_category(offline):
    by_name = {w["name"]: w for w in valorant_api.weapons()}
    assert set(by_name) == {"Vandal", "Bandit", "Melee"}

    v = by_name["Vandal"]
    assert v["uuid"] == VANDAL_UUID
    assert v["category"] == "Rifles"
    assert v["icon"].endswith("/icon.png")
    assert v["killIcon"].endswith("/kill.png")

    assert by_name["Melee"]["category"] is None


def test_bandit_aliases_cover_the_metric_name(offline):
    bandit = next(w for w in valorant_api.weapons() if w["name"] == "Bandit")
    assert bandit["uuid"] == BANDIT_UUID
    # The UI matches metric weapon names against these, so the export's name for
    # the Bandit must be present.
    assert "Bandit" in bandit["aliases"]
    assert "Compact Pistol" in bandit["aliases"]
    assert "CompactPistol" in bandit["aliases"]


def test_client_lookup_resolves_metric_names_and_omits_unknowns(offline):
    lookup: dict[str, str] = {}
    for w in valorant_api.weapons():
        for alias in [w["name"], *w["aliases"]]:
            lookup[alias.lower()] = w["uuid"]

    assert lookup["vandal"] == VANDAL_UUID
    assert lookup["compact pistol"] == BANDIT_UUID
    # Chamber's ultimate is not a purchasable weapon in /v1/weapons: the UI must
    # fall back to a text-only row rather than invent an icon.
    assert lookup.get("tour de force") is None


def test_sync_assets_downloads_weapon_icons(monkeypatch):
    calls: list[tuple[str, Path]] = []
    monkeypatch.setattr(valorant_api, "_get_json", lambda url, cache, force=False: _PAYLOAD)
    monkeypatch.setattr(valorant_api, "agents", lambda force=False: [])
    monkeypatch.setattr(valorant_api, "maps", lambda force=False: [])

    def fake_download(url: str, dest: Path) -> bool:
        calls.append((url, dest))
        return True

    monkeypatch.setattr(valorant_api, "_download", fake_download)

    counts = valorant_api.sync_assets()
    # 3 guns x (shop displayIcon + kill-stream icon)
    assert counts["weapons"] == 6
    assert all(url and dest.parent.name == "weapon" for url, dest in calls)
    names = {dest.name for _, dest in calls}
    assert f"{VANDAL_UUID}.png" in names
    assert f"{VANDAL_UUID}_kill.png" in names


def test_asset_kind_allowlist_includes_weapon():
    assert "weapon" in app_mod.ASSET_KINDS
