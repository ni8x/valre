"""Unresolved shots/hits are match-level, not player-level.

A shot whose magazine decrements cannot be tied to a player pawn, and a damage
invocation with no shooter, have no player to live on. `metrics.build` must
report them in `validation` instead of dropping them silently and leaving
per-player cells that can never be anything but zero.
"""

import pytest

import metrics


def test_unresolved_counts_reads_enriched_payloads():
    shots = [{"p": "a"}, {"p": None}, {"p": None}, {}]
    hits = [{"shooter": "a"}, {"shooter": None}, {"shooter": None}]
    assert metrics.unresolved_counts(shots, hits) == (3, 2)


@pytest.mark.slow
def test_m1_validation_reports_unresolved_shots_and_hits(m1_metrics):
    val = m1_metrics["validation"]
    assert isinstance(val["unresolved_shots"], int)
    assert isinstance(val["unresolved_hits"], int)
    shots_unresolved = sum(1 for s in m1_metrics["shots"] if s.get("p") is None)
    hits_unresolved = sum(1 for h in m1_metrics["hits"] if h.get("shooter") is None)
    assert val["unresolved_shots"] == shots_unresolved
    assert val["unresolved_hits"] == hits_unresolved
    # per-player cells are gone: they could never be non-zero
    for p in m1_metrics["players"]:
        assert "unresolved_shots" not in p
        assert "unresolved_hits" not in p
