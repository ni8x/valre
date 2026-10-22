"""Invariants that must hold for every ingested replay.

Cross-replay sanity checks (not metric values): kills must be fully accounted
for by deaths once suicides and spike-detonation deaths are counted, round
winners must be a team or an explicit "unknown", every derived layer must
build without raising, and rosters must be plausible. A replay that breaks an
invariant is either a code defect or a documented data reality (remake,
surrender, short match) — findings are recorded in the SDD ledger.
"""

import pytest

import coach
import metrics
import spatial
import stats
import store as sm

REPLAYS = [i for i in sm.store.ids() if sm.store.manifest_path(i).exists()]

# Explicit canary: the spike-detonation deaths observed in this dataset. The
# balance rule below would also quietly absorb a *new* unattributed death, so
# pin the exact count per replay — a change here must be explained, not hidden.
EXPECTED_EXPLOSION_DEATHS = {"b2d71503": 1, "1424bb2e": 1}


def explosion_deaths(round_info: dict, window_ms: int = 2000) -> int:
    """Deaths killed by the spike detonating, with no player credited.

    A detonation can take a player with it; the death is recorded but no killer
    is (there is no shooter), so it is missing from player kill counts. Observed
    in the m1 dataset at b2d71503 R8 (spike 672603, death 673643) and
    1424bb2e R12 (spike 1247228, death 1247275).
    """
    exploded = [sp["t"] for sp in (round_info.get("spikes") or []) if sp.get("kind") == "exploded"]
    if not exploded:
        return 0
    t_exp = min(exploded)
    return sum(
        1
        for d in (round_info.get("deaths") or [])
        if not d.get("killer") and t_exp <= d.get("t", -1) <= t_exp + window_ms
    )


@pytest.mark.slow
@pytest.mark.parametrize("rid", REPLAYS, ids=REPLAYS)
def test_replay_invariants(rid):
    s = stats.build(sm.store, rid)
    m = metrics.build(sm.store, rid)
    c = coach.build(sm.store, rid)

    players = s["players"]
    assert len(players) in (0, 2, 4, 6, 8, 10), f"{rid}: player count {len(players)}"

    kills = sum(p["stats"]["kills"] for p in players)
    deaths = sum(p["stats"]["deaths"] for p in players)
    suicides = sum(
        1 for r in s["rounds"] for d in (r.get("deaths") or []) if d.get("suicide")
    )
    explosions = sum(explosion_deaths(r) for r in s["rounds"])
    assert explosions == EXPECTED_EXPLOSION_DEATHS.get(rid[:8], 0), (
        f"{rid}: {explosions} spike-detonation deaths, expected "
        f"{EXPECTED_EXPLOSION_DEATHS.get(rid[:8], 0)}"
    )
    assert deaths - kills == suicides + explosions, (
        f"{rid}: kills {kills} deaths {deaths} suicides {suicides} explosions {explosions}"
    )

    for r in s["rounds"]:
        assert r.get("winner") in ("Blue", "Red", None), (
            f"{rid} R{r.get('round')}: winner {r.get('winner')!r}"
        )
        if r.get("winner") is None:
            assert r.get("winner_source") == "unknown", (
                f"{rid} R{r.get('round')}: undecided winner without an explicit source"
            )
        else:
            # A winner must name its evidence: a spike outcome, the last team
            # standing, or the BaseTeamState.Wins correlation. 1424bb2e has no
            # team-state rows at all, so its rounds used to carry a winner with a
            # blank source.
            assert r.get("winner_source") in ("spike", "elimination", "teamstate_wins"), (
                f"{rid} R{r.get('round')}: winner {r['winner']} without a named source "
                f"({r.get('winner_source')!r})"
            )
        sd = r.get("sides")
        if sd:
            assert sd.get("attacker") in ("Blue", "Red"), f"{rid} R{r['round']}: attacker side"
            assert sd.get("defender") in ("Blue", "Red"), f"{rid} R{r['round']}: defender side"
            assert sd["attacker"] != sd["defender"], f"{rid} R{r['round']}: one team on both sides"

    # Attack/defence split must partition the match, never double count, and must
    # agree with the one rule the replay states outright: only defenders defuse.
    side_rounds = sum(1 for r in s["rounds"] if r.get("sides"))
    if side_rounds:
        for p in players:
            blocks = p.get("stats_by_side")
            assert blocks, f"{rid}: missing side split for {p['subject']}"
            n = blocks["attack"]["rounds"] + blocks["defend"]["rounds"]
            assert n == side_rounds, f"{rid} {p['subject']}: side rounds {n} != {side_rounds}"
            assert blocks["attack"]["kills"] + blocks["defend"]["kills"] == p["stats"]["kills"], (
                f"{rid} {p['subject']}: side kills do not reconstruct the match"
            )
            assert blocks["attack"]["deaths"] + blocks["defend"]["deaths"] == p["stats"]["deaths"], (
                f"{rid} {p['subject']}: side deaths do not reconstruct the match"
            )
        for r in s["rounds"]:
            for sp in r.get("spikes") or []:
                if sp.get("kind") != "defused":
                    continue
                assert sp.get("defuser_team") in ("Blue", "Red"), (
                    f"{rid} R{r['round']}: defused spike without a defuser team"
                )
                assert sp["defuser_team"] == r["sides"]["defender"], (
                    f"{rid} R{r['round']}: defuser {sp['defuser_team']} was not the defending side"
                )
                assert r["winner"] == sp["defuser_team"], f"{rid} R{r['round']}: defused winner"

    # Economy: every round carries both teams' loadout value and a buy class
    # from the known set, the team total is five players' gear (Riot's scale),
    # and each player's per-round rows land in exactly one buy block.
    for r in s["rounds"]:
        for team, cell in (r.get("economy") or {}).items():
            assert team in ("Blue", "Red"), f"{rid} R{r['round']}: economy team {team}"
            assert cell.get("buy") in stats.BUY_KEYS, (
                f"{rid} R{r['round']}: buy {cell.get('buy')!r}"
            )
            if cell.get("alv") is not None:
                assert abs(cell["loadout_value"] - 5 * cell["alv"]) <= 1, (
                    f"{rid} R{r['round']}: loadout value is not 5x the average"
                )
    for p in players:
        buy_rounds = sum(b["rounds"] for b in (p.get("stats_by_buy") or {}).values())
        assert buy_rounds == len(s["rounds"]), (
            f"{rid} {p['subject']}: buy blocks cover {buy_rounds} rounds"
        )

    # Context: opening-duel share, own-scale per-round index, clean rounds.
    entry_wins = 0
    n_rounds_s = len(s["rounds"])
    for p in players:
        e = p.get("entry") or {}
        assert e.get("duels", 0) >= e.get("first_deaths", 0) >= 0, (
            f"{rid} {p['subject']}: entry counters"
        )
        assert e.get("wins", 0) <= e.get("duels", 0), f"{rid} {p['subject']}: entry wins"
        assert (e.get("win_pct") is None) == (not e.get("duels")), (
            f"{rid} {p['subject']}: entry win_pct null rule"
        )
        if e.get("win_pct") is not None:
            assert 0.0 <= e["win_pct"] <= 100.0, f"{rid} {p['subject']}: entry win_pct"
        entry_wins += e.get("wins", 0)
        rating = p.get("rating_by_round") or {}
        assert rating.get("n") == n_rounds_s, f"{rid} {p['subject']}: rating n"
        if n_rounds_s:
            assert (rating.get("worst") <= rating.get("median")
                    <= rating.get("best")), f"{rid} {p['subject']}: rating order"
        else:
            assert rating.get("mean") is None, f"{rid} {p['subject']}: empty rating"
        clean = p.get("clean_rounds")
        assert 0 <= clean <= n_rounds_s, f"{rid} {p['subject']}: clean rounds"
        assert clean == sum(1 for row in p["per_round"]
                            if row["kills"] > 0 and row["deaths"] == 0), (
            f"{rid} {p['subject']}: clean rounds count"
        )
    rounds_with_first_death = sum(1 for r in s["rounds"] if (r.get("deaths") or []))
    assert entry_wins == rounds_with_first_death, (
        f"{rid}: entry wins {entry_wins} != rounds with a first death {rounds_with_first_death}"
    )

    for p in m["players"]:
        rows = p.get("weapons") or {}
        assert sum(r["shots"] for r in rows.values()) == p["shots"], f"{rid} {p['subject']}: weapon shots"
        assert sum(r["hits"] for r in rows.values()) == p["hits"], f"{rid} {p['subject']}: weapon hits"
        assert sum(r["head_hits"] for r in rows.values()) == p["head_hits"], f"{rid} {p['subject']}: weapon heads"
        assert abs(sum(r["dmg"] for r in rows.values()) - p["dmg"]) <= 1.0, f"{rid} {p['subject']}: weapon dmg"
        for name, r in rows.items():
            assert (r["acc"] is None) == (r["shots"] == 0), f"{rid} {p['subject']} {name}: acc null rule"
            assert (r["shots_per_kill"] is None) == (r["kills"] == 0), f"{rid} {p['subject']} {name}: spk null rule"
            # hits per shot; above 1.0 is real for shotguns (one blast, many pellets).
            assert r["acc"] is None or r["acc"] >= 0.0, f"{rid} {p['subject']} {name}: acc range"
            assert r["hs_rate"] is None or 0.0 <= r["hs_rate"] <= 1.0, f"{rid} {p['subject']} {name}: hs range"

    assert m["version"] and s["version"] and c["version"]
    assert len(m["players"]) == len(players), f"{rid}: metrics roster mismatch"
    assert len(c["players"]) == len(players), f"{rid}: coach roster mismatch"


@pytest.mark.slow
@pytest.mark.parametrize("rid", REPLAYS, ids=REPLAYS)
def test_tracks_and_heatmap_build_for_every_replay(rid):
    t = spatial.tracks(sm.store, rid, 1)
    assert isinstance(t, dict) and ("tracks" in t or "cells" in t), f"{rid}: tracks shape"
    h = spatial.heatmap(sm.store, rid, mode="all")
    assert isinstance(h, dict) and "cells" in h, f"{rid}: heatmap shape"
