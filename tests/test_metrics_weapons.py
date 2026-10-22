"""Unit tests for the per-weapon metric block in ``server/metrics.py``.

The weapon cells are built from real shots/hits rows in ``metrics._compute``;
these tests pin the finished rates over a synthetic cell so the arithmetic and
the null rules (a rate whose denominator is zero is ``None``, never ``0``)
cannot drift.
"""
import pytest

import metrics


def _cell(**kw):
    cell = metrics._new_weapon_cell()
    cell.update(kw)
    return cell


def test_new_weapon_cell_has_every_counter():
    cell = metrics._new_weapon_cell()
    assert set(cell) == {"shots", "hits", "dmg", "kills", "head_hits"}
    assert all(v == 0 or v == 0.0 for v in cell.values())


def test_finish_weapons_computes_rates_from_counts():
    out = metrics._finish_weapons(
        {"Vandal": _cell(shots=104, hits=15, dmg=1324.4, kills=8, head_hits=6)}
    )
    w = out["Vandal"]
    assert w["acc"] == pytest.approx(15 / 104)
    assert w["hs_rate"] == pytest.approx(6 / 15)
    assert w["dmg_per_shot"] == pytest.approx(12.7, abs=0.05)
    assert w["shots_per_kill"] == pytest.approx(13.0)


def test_finish_weapons_keeps_the_raw_counts():
    w = metrics._finish_weapons(
        {"Classic": _cell(shots=24, hits=4, dmg=208.0, kills=2, head_hits=1)}
    )["Classic"]
    assert (w["shots"], w["hits"], w["kills"], w["head_hits"]) == (24, 4, 2, 1)
    assert w["dmg"] == 208.0
    assert set(w) == {
        "shots", "hits", "dmg", "kills", "head_hits",
        "acc", "hs_rate", "dmg_per_shot", "shots_per_kill",
    }


def test_finish_weapons_rates_are_none_when_no_data():
    w = metrics._finish_weapons({"Phantom": _cell()})["Phantom"]
    assert w["acc"] is None
    assert w["hs_rate"] is None
    assert w["dmg_per_shot"] is None
    assert w["shots_per_kill"] is None


def test_finish_weapons_without_kills_has_no_shots_per_kill():
    w = metrics._finish_weapons(
        {"Spectre": _cell(shots=9, hits=3, dmg=154.0, head_hits=1)}
    )["Spectre"]
    assert w["shots_per_kill"] is None
    assert w["acc"] == pytest.approx(1 / 3)


def test_finish_weapons_with_shots_but_no_hits_keeps_accuracy_zero():
    w = metrics._finish_weapons({"Guardian": _cell(shots=14)})["Guardian"]
    assert w["acc"] == 0.0
    assert w["hs_rate"] is None
