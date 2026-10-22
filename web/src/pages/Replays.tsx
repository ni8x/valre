import { useCallback, useEffect, useMemo, useState } from "react";
import { api, Agent, MapInfo, ReplayRow, mapIcon, shortLevel } from "../api";
import { DASH, fmtDuration, fmtMatchDate, replayName } from "../format";
import UploadDialog from "../components/UploadDialog";
import heroSplash from "../assets/hero-sunset.png";

const STATUS_LABEL: Record<string, string> = {
  pending: "Queued",
  exporting: "Importing",
  error: "Failed",
};

function mapFor(maps: MapInfo[], level: string | null | undefined) {
  const match = maps.find((m) => m.mapUrl && m.mapUrl === level);
  return { name: match?.name || shortLevel(level), uuid: match?.uuid };
}

export default function Replays({
  agents,
  me,
  names,
}: {
  agents: Agent[];
  me: string | null;
  names: Record<string, string>;
}) {
  const [rows, setRows] = useState<ReplayRow[] | null>(null);
  const [maps, setMaps] = useState<MapInfo[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [dialog, setDialog] = useState<{ open: boolean; mode: "upload" | "link" }>({
    open: false,
    mode: "upload",
  });
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [renameErr, setRenameErr] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api
      .replays()
      .then((list) => {
        setRows(list);
        setErr(null);
      })
      .catch((e) => setErr(String(e)));
  }, []);

  useEffect(() => {
    refresh();
    api.maps().then(setMaps).catch(() => {});
  }, [refresh]);

  useEffect(() => {
    const active = rows?.some((r) => r.status === "pending" || r.status === "exporting");
    if (!active) return;
    const timer = window.setInterval(refresh, 2000);
    return () => window.clearInterval(timer);
  }, [rows, refresh]);

  const titleOf = (r: ReplayRow) => replayName(r.title, mapFor(maps, r.level_name).name);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!rows) return [];
    if (!needle) return rows;
    return rows.filter((r) => {
      const map = mapFor(maps, r.level_name).name || "";
      const haystack = [
        titleOf(r),
        r.original_name || "",
        map,
        fmtMatchDate(r.timestamp_ticks, r.created_at),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(needle);
    });
  }, [rows, maps, query]);

  const ready = rows?.filter((r) => r.status === "ready").length ?? 0;
  const rounds = rows?.reduce((sum, r) => sum + (r.n_rounds || 0), 0) ?? 0;

  const openUpload = () => setDialog({ open: true, mode: "upload" });
  const openLink = () => setDialog({ open: true, mode: "link" });

  const beginRename = (r: ReplayRow) => {
    setRenaming(r.id);
    setDraft(titleOf(r));
    setRenameErr(null);
  };

  const saveRename = async (r: ReplayRow) => {
    const clean = draft.trim();
    if (!clean) {
      setRenameErr("A name is required.");
      return;
    }
    try {
      await api.rename(r.id, clean);
      setRenaming(null);
      setRenameErr(null);
      refresh();
    } catch (e) {
      setRenameErr(String(e));
    }
  };

  const remove = async (r: ReplayRow) => {
    if (!confirm(`Delete “${titleOf(r)}” and its exported data? This cannot be undone.`)) return;
    try {
      await api.remove(r.id);
      refresh();
    } catch (e) {
      setErr(String(e));
    }
  };

  const syncIcons = async () => {
    setSyncing(true);
    setNote(null);
    try {
      const counts = await api.syncAssets();
      setNote(
        `Icons ready: ${counts.agents ?? 0} agents, ${counts.maps ?? 0} maps, ${counts.abilities ?? 0} abilities.`,
      );
      setMaps(await api.maps());
    } catch (e) {
      setNote(String(e));
    } finally {
      setSyncing(false);
    }
  };

  return (
    <>
      <section className="hero">
        <div className="hero-media" style={{ backgroundImage: `url(${heroSplash})` }} aria-hidden="true" />
        <div className="hero-shade" aria-hidden="true" />
        <div className="hero-inner">
          <div className="hero-rail">
            <p className="hero-kicker">
              <span aria-hidden="true" />
              A local match archive
            </p>
            <p className="hero-count">
              {rows == null
                ? "Reading the archive…"
                : `${rows.length} replays · ${rounds} rounds · ${ready} ready to read`}
            </p>
          </div>
          <h1 className="hero-title">
            Every match,
            <br />
            read back.
          </h1>
          <p className="hero-lead">
            Drop in a Valorant replay and see the parts that matter: the score, the opening picks, the
            buys that shaped each half, and how the rounds came apart.
          </p>
          <div className="hero-actions">
            <button className="primary large" onClick={openUpload}>
              Upload a replay
            </button>
            <button className="ghost large" onClick={openLink}>
              Use a file on this PC
            </button>
          </div>
        </div>
        <div className="hero-word" aria-hidden="true">
          <span>VAL</span>
          <span className="red">RE</span>
        </div>
      </section>

      <section className="history" aria-labelledby="history-title">
        <div className="history-head">
          <div>
            <h2 id="history-title">History</h2>
            <p className="muted small">Newest first. Every replay and every name stays on this machine.</p>
          </div>
          <div className="history-tools">
            <label className="search">
              <span className="sr-only">Search replays</span>
              <input
                type="search"
                value={query}
                placeholder="Search by name, map or date"
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <button className="primary" onClick={openUpload}>
              Add replay
            </button>
            <button className="text-btn" onClick={syncIcons} disabled={syncing}>
              {syncing ? "Syncing icons…" : "Sync game icons"}
            </button>
          </div>
        </div>

        {note && <div className="note small">{note}</div>}
        {err && <div className="panel err">{err}</div>}

        {rows == null && <div className="archive-loading">Reading the archive…</div>}

        {rows && rows.length === 0 && (
          <div className="empty-archive">
            <span className="drop-mark" aria-hidden="true">
              .vrf
            </span>
            <h3>No replays yet</h3>
            <p className="muted">
              Add your first .vrf file and it will appear here with its score, date and rounds.
            </p>
            <button className="primary" onClick={openUpload}>
              Upload a replay
            </button>
          </div>
        )}

        {rows && rows.length > 0 && filtered.length === 0 && (
          <div className="empty-archive small-empty">
            <p className="muted">No replay matches “{query}”.</p>
            <button className="ghost small" onClick={() => setQuery("")}>
              Clear search
            </button>
          </div>
        )}

        {filtered.length > 0 && (
          <div className="history-list">
            {filtered.map((r) => {
              const map = mapFor(maps, r.level_name);
              const icon = map.uuid ? mapIcon(map.uuid) : undefined;
              const date = fmtMatchDate(r.timestamp_ticks, r.created_at);
              const score = r.score;
              const hasScore = score && Number.isFinite(score.Blue) && Number.isFinite(score.Red);
              const mine = me ? (r.players || []).find((p) => p.subject === me) : null;
              const myAgent = mine
                ? agents.find((a) => a.uuid === (mine.agent_uuid || "").toLowerCase())?.name
                : null;
              const myNick = me ? names[me] : null;
              const isRenaming = renaming === r.id;
              return (
                <article className={`hrow ${r.status === "error" ? "is-error" : ""}`} key={r.id}>
                  <a className="hrow-map" href={`#/r/${r.id}/match`} tabIndex={-1} aria-hidden="true">
                    {icon ? (
                      <img src={icon} alt="" onError={(e) => (e.currentTarget.style.opacity = "0.15")} />
                    ) : (
                      <span>{(map.name || "?").slice(0, 2)}</span>
                    )}
                  </a>

                  <div className="hrow-main">
                    {isRenaming ? (
                      <form
                        className="rename-form"
                        onSubmit={(e) => {
                          e.preventDefault();
                          saveRename(r);
                        }}
                      >
                        <input
                          value={draft}
                          maxLength={120}
                          autoFocus
                          aria-label={`Name for ${titleOf(r)}`}
                          onChange={(e) => setDraft(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Escape") {
                              setRenaming(null);
                              setRenameErr(null);
                            }
                          }}
                        />
                        <button className="primary small" type="submit">
                          Save
                        </button>
                        <button
                          className="ghost small"
                          type="button"
                          onClick={() => {
                            setRenaming(null);
                            setRenameErr(null);
                          }}
                        >
                          Cancel
                        </button>
                      </form>
                    ) : (
                      <a className="hrow-title" href={`#/r/${r.id}/match`}>
                        {titleOf(r)}
                      </a>
                    )}

                    <div className="hrow-meta">
                      <span>{map.name || "Unknown map"}</span>
                      <i aria-hidden="true" />
                      <span>{date}</span>
                      <i aria-hidden="true" />
                      <span>{fmtDuration(r.duration_ms)}</span>
                      {r.n_rounds ? (
                        <>
                          <i aria-hidden="true" />
                          <span>{r.n_rounds} rounds</span>
                        </>
                      ) : null}
                    </div>

                    {(myAgent || myNick) && (
                      <div className="hrow-you">
                        {myAgent ? `You as ${myAgent}` : "You in this match"}
                        {myNick && myAgent ? ` · ${myNick}` : ""}
                      </div>
                    )}

                    {isRenaming && renameErr && <div className="err small">{renameErr}</div>}
                    {r.status === "error" && r.error && <div className="err small">{r.error}</div>}
                  </div>

                  <div className="hrow-result">
                    {hasScore ? (
                      <div
                        className="hscore"
                        title={`Blue ${score.Blue} · Red ${score.Red}`}
                        aria-label={`Blue ${score.Blue}, Red ${score.Red}`}
                      >
                        <span className="hscore-dot blue" aria-hidden="true" />
                        <b>{score.Blue}</b>
                        <span className="hscore-sep">:</span>
                        <b>{score.Red}</b>
                        <span className="hscore-dot red" aria-hidden="true" />
                      </div>
                    ) : (
                      <span className="hscore-empty">{DASH}</span>
                    )}
                    {STATUS_LABEL[r.status || ""] && r.status !== "ready" && (
                      <span className={`status-badge ${r.status}`}>
                        {(r.status === "pending" || r.status === "exporting") && (
                          <i className="live-dot" aria-hidden="true" />
                        )}
                        {STATUS_LABEL[r.status || ""]}
                      </span>
                    )}
                  </div>

                  <div className="hrow-actions">
                    <a className="hrow-open" href={`#/r/${r.id}/match`}>
                      Open
                    </a>
                    {!isRenaming && (
                      <button className="text-btn" onClick={() => beginRename(r)}>
                        Rename
                      </button>
                    )}
                    <button className="text-btn danger-text" onClick={() => remove(r)}>
                      Delete
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>

      <UploadDialog
        open={dialog.open}
        initialMode={dialog.mode}
        onClose={() => setDialog((d) => ({ ...d, open: false }))}
        onAdded={refresh}
      />
    </>
  );
}
