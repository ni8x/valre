"""Valorant Replay Lab - FastAPI backend.

Run:  python server/app.py   (or)  uvicorn server.app:app --reload
"""
from __future__ import annotations

import io
import shutil
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ai as ai_mod  # noqa: E402
import analysis  # noqa: E402
import coach as coach_mod  # noqa: E402
import coverage as coverage_mod  # noqa: E402
import deaths as deaths_mod  # noqa: E402
import duo as duo_mod  # noqa: E402
import identity as identity_mod  # noqa: E402
import judge as judge_mod  # noqa: E402
import metrics as metrics_mod  # noqa: E402
import players as players_mod  # noqa: E402
import regions as regions_mod  # noqa: E402
import riot as riot_mod  # noqa: E402
import settings as settings_mod  # noqa: E402
import spatial as spatial_mod  # noqa: E402
import stats as stats_mod  # noqa: E402
import tables as tables_mod  # noqa: E402
import valorant_api  # noqa: E402
import vrfkit  # noqa: E402
from store import store  # noqa: E402

app = FastAPI(title="Valorant Replay Lab", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- job tracking ---------------------------------------------------

JOBS: dict[str, dict[str, Any]] = {}
_READERS: dict[tuple[str, str], tables_mod.TableReader] = {}

# JOBS and _READERS are written from request threads and from the background export
# thread (which sweeps a replay's readers). Single-key reads are atomic in CPython,
# but inserting into or iterating a dict while another thread does the same is not,
# so every structural change takes this lock.
_REG_LOCK = threading.Lock()

# Asset kinds served by /api/assets/{kind}/{name}; mirrors valorant_api._EXT_BY_KIND.
ASSET_KINDS = {"agent", "map", "ability", "role", "weapon"}


def _reader(replay_id: str, table: str) -> tables_mod.TableReader:
    key = (replay_id, table)
    with _REG_LOCK:
        if key not in _READERS:
            path = store.export_dir(replay_id) / f"{table}.parquet"
            if not path.exists():
                raise HTTPException(404, f"table '{table}' not found for {replay_id}")
            _READERS[key] = tables_mod.TableReader(path)
        return _READERS[key]


def _drop_readers(replay_id: str) -> int:
    """Drop cached parquet readers and derived JSON caches for a replay.

    A re-export replaces both the parquet files and the cached engine payloads;
    keeping either would serve pre-export results under the same version gate.
    """
    with _REG_LOCK:
        keys = [k for k in _READERS if k[0] == replay_id]
        for key in keys:
            _READERS.pop(key, None)
    for name in ("stats.json", "metrics.json"):
        try:
            (store.dir(replay_id) / name).unlink(missing_ok=True)
        except OSError:
            pass
    return len(keys)


def _run_export(replay_id: str, src: Path) -> None:
    with _REG_LOCK:
        job = JOBS.setdefault(replay_id, {})
        job.update({"phase": "Starting", "lines": [], "error": None})
    store.update(replay_id, status="exporting")
    _drop_readers(replay_id)

    def on_line(line: str) -> None:
        lines = job.setdefault("lines", [])
        lines.append(line)
        if len(lines) > 300:
            del lines[:-300]

    def on_phase(phase: str) -> None:
        job["phase"] = phase

    try:
        out = store.export_dir(replay_id)
        if out.exists() and any(out.iterdir()):
            shutil.rmtree(out, ignore_errors=True)
        vrfkit.export(src, out, checkpoints=True, on_line=on_line, on_phase=on_phase)
        manifest = analysis.load_manifest(store.manifest_path(replay_id))
        summary = analysis.summarize(manifest, replay_id)
        store.update(
            replay_id,
            status="ready",
            error=None,
            summary=summary,
            title=manifest.get("friendly_name") or replay_id,
        )
        job["phase"] = "Ready"
    except Exception as exc:  # noqa: BLE001
        store.update(replay_id, status="error", error=str(exc))
        job["phase"] = "Error"
        job["error"] = str(exc)
        job["trace"] = traceback.format_exc()[-2000:]


def _ingest(replay_id: str, src_path: Path) -> None:
    vrfkit.run_in_thread(_run_export, replay_id, src_path)


# ---- health / meta --------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "vrfkit": str(vrfkit.binary_path()),
        "vrfkit_available": vrfkit.available(),
    }


@app.get("/api/game/agents")
def game_agents(force: bool = False) -> list[dict[str, Any]]:
    return valorant_api.agents(force)


@app.get("/api/game/maps")
def game_maps(force: bool = False) -> list[dict[str, Any]]:
    return valorant_api.maps(force)


@app.get("/api/game/weapons")
def game_weapons(force: bool = False) -> list[dict[str, Any]]:
    return valorant_api.weapons(force)


@app.post("/api/game/sync-assets")
def sync_assets(force: bool = False) -> dict[str, Any]:
    return valorant_api.sync_assets(force)


@app.get("/api/assets/{kind}/{name}")
def get_asset(kind: str, name: str) -> FileResponse:
    if kind not in ASSET_KINDS:
        raise HTTPException(400, "bad kind")
    p = valorant_api.asset_path(kind, name)
    if not p:
        raise HTTPException(404, "asset not found")
    return FileResponse(p, headers={"Cache-Control": "public, max-age=86400"})


# ---- replays --------------------------------------------------------


@app.get("/api/replays")
def list_replays() -> list[dict[str, Any]]:
    out = []
    for r in store.list_all():
        s = r.get("summary") or {}
        out.append(
            {
                "id": r["id"],
                "title": r.get("title"),
                "status": r.get("status"),
                "error": r.get("error"),
                "created_at": r.get("created_at"),
                "duration_ms": s.get("duration_ms"),
                "level_name": s.get("level_name"),
                "build": s.get("build"),
                "player_count": len(s.get("players") or []),
                "players": s.get("players"),
            }
        )
    return out


@app.post("/api/replays")
async def upload_replay(file: UploadFile = File(...)) -> dict[str, Any]:
    name = file.filename or "replay.vrf"
    if not name.lower().endswith(".vrf"):
        raise HTTPException(400, "expected a .vrf file")
    hint = Path(name).stem
    record = store.create(hint, name)
    dest = store.source_path(record["id"])
    with dest.open("wb") as fh:
        while chunk := await file.read(1024 * 1024):
            fh.write(chunk)
    _ingest(record["id"], dest)
    return {"id": record["id"], "status": "exporting"}


@app.post("/api/replays/link")
def link_replay(path: str = Body(..., embed=True)) -> dict[str, Any]:
    src = Path(path)
    if not src.exists():
        raise HTTPException(400, f"file not found: {path}")
    record = store.create(src.stem, src.name)
    dest = store.source_path(record["id"])
    shutil.copy2(src, dest)
    _ingest(record["id"], dest)
    return {"id": record["id"], "status": "exporting"}


@app.get("/api/replays/{replay_id}")
def get_replay(replay_id: str) -> dict[str, Any]:
    r = store.get(replay_id)
    if not r:
        raise HTTPException(404, "replay not found")
    return r


@app.get("/api/replays/{replay_id}/status")
def get_status(replay_id: str) -> dict[str, Any]:
    r = store.get(replay_id)
    if not r:
        raise HTTPException(404, "replay not found")
    job = JOBS.get(replay_id, {})
    return {
        "id": replay_id,
        "status": r.get("status"),
        "phase": job.get("phase"),
        "error": r.get("error") or job.get("error"),
        "lines": (job.get("lines") or [])[-40:],
        "summary": r.get("summary"),
    }


@app.delete("/api/replays/{replay_id}")
def delete_replay(replay_id: str) -> dict[str, Any]:
    _drop_readers(replay_id)
    if not store.delete(replay_id):
        raise HTTPException(404, "replay not found")
    with _REG_LOCK:
        JOBS.pop(replay_id, None)
    return {"deleted": replay_id}


# ---- tables ---------------------------------------------------------


@app.get("/api/replays/{replay_id}/tables")
def list_tables(replay_id: str) -> list[dict[str, Any]]:
    exp = store.export_dir(replay_id)
    if not exp.exists():
        raise HTTPException(404, "no export for this replay")
    counts = analysis.table_row_counts(exp, tables_mod.ALL_TABLES)
    out = []
    for name in tables_mod.ALL_TABLES:
        info = tables_mod.TABLE_INFO.get(name, {})
        out.append(
            {
                "name": name,
                "label": info.get("label", name),
                "desc": info.get("desc", ""),
                "rows": counts.get(name, 0),
                "exists": counts.get(name, 0) > 0,
                "checkpoint": name.startswith("checkpoint_"),
            }
        )
    return out


@app.get("/api/replays/{replay_id}/tables/{table}")
def read_table(
    replay_id: str,
    table: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    sort: str | None = None,
    desc: bool = False,
    filter: list[str] | None = Query(None, description="col:op:value, repeatable"),
) -> dict[str, Any]:
    if table not in tables_mod.ALL_TABLES:
        raise HTTPException(400, f"unknown table '{table}'")
    reader = _reader(replay_id, table)
    filters = []
    for f in filter or []:
        parts = f.split(":", 2)
        if len(parts) != 3:
            continue
        col, op, raw = parts
        val: Any = raw
        try:
            val = int(raw)
        except ValueError:
            if raw.lower() in {"true", "false"}:
                val = raw.lower() == "true"
            else:
                try:
                    val = float(raw)
                except ValueError:
                    val = raw
        filters.append((col, op, val))
    return reader.page(offset=offset, limit=limit, filters=filters, sort=sort, descending=desc)


@app.get("/api/replays/{replay_id}/tables/{table}/distinct")
def distinct(replay_id: str, table: str, column: str) -> list[Any]:
    if table not in tables_mod.ALL_TABLES:
        raise HTTPException(400, f"unknown table '{table}'")
    return _reader(replay_id, table).distinct(column)


# ---- analysis -------------------------------------------------------


@app.get("/api/replays/{replay_id}/rounds")
def get_rounds(replay_id: str) -> list[dict[str, Any]]:
    return analysis.rounds_from_events(store.export_dir(replay_id) / "events.parquet")


@app.get("/api/replays/{replay_id}/stats")
def get_stats(
    replay_id: str, fresh: bool = False
) -> dict[str, Any]:
    if not store.manifest_path(replay_id).exists():
        raise HTTPException(404, "no export for this replay")
    return stats_mod.build(store, replay_id, fresh=fresh)


@app.get("/api/replays/{replay_id}/metrics")
def get_metrics(
    replay_id: str, fresh: bool = False, light: bool = False
) -> dict[str, Any]:
    if not store.manifest_path(replay_id).exists():
        raise HTTPException(404, "no export for this replay")
    data = metrics_mod.build(store, replay_id, fresh=fresh)
    if light:
        data = {
            k: v
            for k, v in data.items()
            if k not in ("shots", "hits")
        }
        data["sprays"] = [
            {k: v for k, v in s.items() if k != "ticks"}
            for s in data.get("sprays", [])
        ]
    return data


@app.get("/api/replays/{replay_id}/coverage")
def get_coverage(replay_id: str) -> dict[str, Any]:
    if not store.manifest_path(replay_id).exists():
        raise HTTPException(404, "no export for this replay")
    return coverage_mod.build(store, replay_id)


@app.get("/api/replays/{replay_id}/deaths")
def get_deaths(replay_id: str) -> dict[str, Any]:
    if not store.manifest_path(replay_id).exists():
        raise HTTPException(404, "no export for this replay")
    return deaths_mod.build(store, replay_id)


@app.get("/api/replays/{replay_id}/judge")
def get_judge(replay_id: str) -> dict[str, Any]:
    if not store.manifest_path(replay_id).exists():
        raise HTTPException(404, "no export for this replay")
    return judge_mod.build(store, replay_id)


@app.get("/api/replays/{replay_id}/coach")
def get_coach(replay_id: str) -> dict[str, Any]:
    if not store.manifest_path(replay_id).exists():
        raise HTTPException(404, "no export for this replay")
    return coach_mod.build(store, replay_id)


# ---- players (cross-match) ------------------------------------------
#
# Player Analysis folds every ingested replay into a profile per subject. The
# fast layer reads cached payloads only; the deep layer (judge + deaths) parses
# each match's field stream, so it is built by an explicit job and cached in
# data/players.json with its age reported rather than recomputed per request.

_PLAYERS_JOB = "players:deep"


def _run_players_deep(fresh: bool) -> None:
    with _REG_LOCK:
        job = JOBS.setdefault(_PLAYERS_JOB, {})
        job.update({"phase": "Building the deep layer", "error": None, "result": None})
    try:
        cache = players_mod.build_deep(store, fresh=fresh)
        job["phase"] = "Ready"
        job["result"] = {
            "matches": len(cache.get("matches") or []),
            "built": len(cache.get("built") or []),
            "failed": cache.get("failed") or [],
            "seconds": cache.get("seconds"),
            "built_at": cache.get("built_at"),
        }
    except Exception as exc:  # noqa: BLE001
        job["phase"] = "Error"
        job["error"] = str(exc)
        job["trace"] = traceback.format_exc()[-2000:]


def _players_status() -> dict[str, Any]:
    with _REG_LOCK:
        job = dict(JOBS.get(_PLAYERS_JOB) or {})
    return {
        "phase": job.get("phase"),
        "error": job.get("error"),
        "result": job.get("result"),
        "state": players_mod.deep_state(store),
    }


@app.get("/api/players")
def get_players() -> dict[str, Any]:
    return players_mod.catalog(store)


@app.get("/api/players/build/status")
def get_players_build_status() -> dict[str, Any]:
    return _players_status()


@app.post("/api/players/build")
def post_players_build(fresh: bool = Body(False, embed=True)) -> dict[str, Any]:
    with _REG_LOCK:
        running = (JOBS.get(_PLAYERS_JOB) or {}).get("phase") == "Building the deep layer"
    if not running:
        vrfkit.run_in_thread(_run_players_deep, bool(fresh))
    return _players_status()


@app.post("/api/players/me")
def post_players_me(subject: str | None = Body(None, embed=True)) -> dict[str, Any]:
    return settings_mod.set_me(subject)


@app.post("/api/players/names")
def post_players_names(
    subject: str = Body(..., embed=True), label: str = Body("", embed=True)
) -> dict[str, Any]:
    return settings_mod.set_name(subject, label)


@app.get("/api/players/{subject}/teammates")
def get_player_teammates(subject: str) -> dict[str, Any]:
    try:
        return players_mod.teammates(store, subject)
    except KeyError:
        raise HTTPException(404, "no replay holds this player") from None


@app.get("/api/players/{subject}")
def get_player(subject: str, deep: bool = False) -> dict[str, Any]:
    try:
        return players_mod.profile(store, subject, deep=deep)
    except KeyError:
        raise HTTPException(404, "no replay holds this player") from None


# ---- the duo check (pair view) ---------------------------------------
#
# One death read from the other player's side: distance at the death, ahead/behind in the victim's
# facing, whether the partner ever came up, and the exact trade attribution. Computed on demand from
# the cached stats payloads plus each match's movement.parquet - never cached, never versioned, so it
# cannot go stale.


@app.get("/api/duo/pair")
def get_duo_pair(me: str, partner: str, match: str | None = None) -> dict[str, Any]:
    if not me or not partner:
        raise HTTPException(400, "me and partner are both required")
    return duo_mod.pair(store, me, partner, match)


# ---- identity (Phase 14) ---------------------------------------------
#
# A replay carries no names, so "which one is me" is answered from the facts it
# does carry: agent, map, start time, first-half side. The offline picker ranks
# the ten subjects by how many of the asked facts they answer; the Riot sync is
# optional and only runs with RIOT_API_KEY set.


@app.get("/api/identity/signature")
def get_identity_signature(replay: str) -> dict[str, Any]:
    if not store.manifest_path(replay).exists():
        raise HTTPException(404, "no export for this replay")
    return identity_mod.signature(store, replay)


@app.get("/api/identity/identify")
def get_identity_identify(
    replay: str,
    agent: str | None = None,
    side: str | None = None,
    at: str | None = None,
    tolerance_min: float = 120.0,
) -> dict[str, Any]:
    if not store.manifest_path(replay).exists():
        raise HTTPException(404, "no export for this replay")
    return identity_mod.identify(
        store, replay, agent=agent, side=side, at=at, tolerance_min=tolerance_min
    )


@app.get("/api/identity/status")
def get_identity_status() -> dict[str, Any]:
    return riot_mod.status()


@app.post("/api/identity/riot")
def post_identity_riot(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    replay_id = payload.get("replay_id")
    if not replay_id:
        raise HTTPException(400, "replay_id required")
    if not store.manifest_path(replay_id).exists():
        raise HTTPException(404, "no export for this replay")
    out = riot_mod.tag(
        store, replay_id, payload.get("game_name") or "", payload.get("tag_line") or ""
    )
    if out.get("subject"):
        settings_mod.set_me(out["subject"])
    return out


@app.get("/api/replays/{replay_id}/tracks")
def get_tracks(replay_id: str, round: int | None = None) -> dict[str, Any]:
    if not store.manifest_path(replay_id).exists():
        raise HTTPException(404, "no export for this replay")
    return spatial_mod.tracks(store, replay_id, round)


@app.get("/api/replays/{replay_id}/regions")
def get_regions(replay_id: str) -> dict[str, Any]:
    if not store.manifest_path(replay_id).exists():
        raise HTTPException(404, "no export for this replay")
    return regions_mod.regions_for_replay(store, replay_id)


@app.get("/api/replays/{replay_id}/heatmap")
def get_heatmap(
    replay_id: str,
    player: str | None = None,
    team: str | None = None,
    round: int | None = None,
    mode: str = "all",
) -> dict[str, Any]:
    if not store.manifest_path(replay_id).exists():
        raise HTTPException(404, "no export for this replay")
    return spatial_mod.heatmap(
        store, replay_id, player=player, team=team, round_no=round, mode=mode
    )


@app.get("/api/analysis/status")
def analysis_status() -> dict[str, Any]:
    return ai_mod.status()


@app.post("/api/analysis/query")
def raw_query(payload: dict[str, Any] = Body(...)) -> Any:
    """AI analysis when 'question' is present, else raw table query helper."""
    replay_id = payload.get("replay_id")
    if not replay_id:
        raise HTTPException(400, "replay_id required")
    question = (payload.get("question") or "").strip()
    if question:
        if not store.manifest_path(replay_id).exists():
            raise HTTPException(404, "no export for this replay")
        try:
            return ai_mod.ask(
                store,
                replay_id,
                question,
                subject=payload.get("subject"),
                model=payload.get("model"),
            )
        except ai_mod.AIError as exc:
            raise HTTPException(503, str(exc)) from exc
    reader = _reader(replay_id, payload.get("table", "fields"))
    return reader.page(
        offset=int(payload.get("offset", 0)),
        limit=min(int(payload.get("limit", 100)), 1000),
        filters=[tuple(f) for f in payload.get("filters", [])],
        sort=payload.get("sort"),
        descending=bool(payload.get("desc")),
    )


# ---- static frontend ------------------------------------------------

DIST = ROOT / "web" / "dist"
if DIST.exists():
    app.mount("/assets/static", StaticFiles(directory=DIST), name="dist-assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> Any:
        candidate = DIST / full_path
        if full_path and candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
