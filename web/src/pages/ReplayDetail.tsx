import { useEffect, useState } from "react";
import { api, Agent, MapInfo, ReplayRecord, Weapon, shortLevel } from "../api";
import { fmtDuration, fmtMatchDate, replayName } from "../format";
import MatchView from "../components/MatchView";
import StatsView from "../components/StatsView";
import RoundsView from "../components/RoundsView";
import TableExplorer from "../components/TableExplorer";
import AimView from "../components/AimView";
import MapView from "../components/MapView";
import CoachView from "../components/CoachView";
import JudgeView from "../components/JudgeView";
import AskView from "../components/AskView";

const TABS = [
  { key: "match", label: "Match" },
  { key: "rounds", label: "Rounds" },
  { key: "map", label: "Map" },
] as const;

const DEEPER = [
  { key: "aim", label: "Aim" },
  { key: "coach", label: "Coach" },
  { key: "judge", label: "Judge" },
  { key: "ai", label: "Ask AI" },
  { key: "stats", label: "Full stats" },
  { key: "tables", label: "Tables" },
] as const;

export default function ReplayDetail({
  id,
  tab,
  agents,
  weapons,
  me,
  names,
}: {
  id: string;
  tab: string;
  agents: Agent[];
  weapons: Weapon[];
  me: string | null;
  names: Record<string, string>;
}) {
  const [rec, setRec] = useState<ReplayRecord | null>(null);
  const [status, setStatus] = useState<any>(null);
  const [maps, setMaps] = useState<MapInfo[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState("");
  const [renameErr, setRenameErr] = useState<string | null>(null);

  useEffect(() => {
    let stop = false;
    let timer = 0;
    const load = async () => {
      try {
        const record = await api.replay(id);
        if (stop) return;
        setRec(record);
        setErr(null);
        const state = await api.status(id);
        if (stop) return;
        setStatus(state);
        if (record.status === "pending" || record.status === "exporting") {
          timer = window.setTimeout(load, 1500);
        }
      } catch (e) {
        if (!stop) setErr(String(e));
      }
    };
    load();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [id]);

  useEffect(() => {
    api.maps().then(setMaps).catch(() => {});
  }, []);

  if (err && !rec) return <div className="page detail-page"><div className="panel err">{err}</div></div>;
  if (!rec) return <div className="page detail-page"><div className="panel muted">Loading the match…</div></div>;

  const summary = rec.summary || {};
  const mapInfo = maps.find((m) => m.mapUrl && m.mapUrl === summary.level_name);
  const mapName = mapInfo?.name || shortLevel(summary.level_name);
  const title = replayName(rec.title, mapName);
  const active = tab === "overview" ? "match" : tab;
  const isPrimary = TABS.some((t) => t.key === active);
  const importing = rec.status === "pending" || rec.status === "exporting";

  const beginRename = () => {
    setRenaming(true);
    setDraft(title);
    setRenameErr(null);
  };

  const saveRename = async () => {
    const clean = draft.trim();
    if (!clean) {
      setRenameErr("A name is required.");
      return;
    }
    try {
      const updated = await api.rename(id, clean);
      setRec({ ...rec, title: updated.title, custom_title: true });
      setRenaming(false);
      setRenameErr(null);
    } catch (e) {
      setRenameErr(String(e));
    }
  };

  return (
    <div className="page detail-page">
      <a className="back-link" href="#/">
        <span aria-hidden="true">←</span> History
      </a>

      <header className="detail-top">
        <div className="detail-title-block">
          <div className="detail-eyebrow">
            <span>{mapName || "Unknown map"}</span>
            <i aria-hidden="true" />
            <span>{fmtMatchDate(summary.timestamp_ticks, rec.created_at)}</span>
            <i aria-hidden="true" />
            <span>{fmtDuration(summary.duration_ms)}</span>
          </div>
          {renaming ? (
            <form
              className="title-rename"
              onSubmit={(e) => {
                e.preventDefault();
                saveRename();
              }}
            >
              <input
                value={draft}
                maxLength={120}
                autoFocus
                aria-label="Replay name"
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    setRenaming(false);
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
                  setRenaming(false);
                  setRenameErr(null);
                }}
              >
                Cancel
              </button>
            </form>
          ) : (
            <h1>{title}</h1>
          )}
          {renameErr && <div className="err small">{renameErr}</div>}
        </div>
        {!renaming && (
          <button className="ghost small rename-btn" onClick={beginRename}>
            Rename
          </button>
        )}
      </header>

      {importing && (
        <div className="panel import-panel">
          <div className="import-line">
            <b>{status?.phase || (rec.status === "pending" ? "Queued" : "Importing the replay")}</b>
            <span className="muted small">The export runs once, then every page reads from the cache.</span>
          </div>
          <div className="progressbar">
            <div className={`bar ${status?.phase || ""}`} />
          </div>
          {status?.lines?.length ? (
            <details>
              <summary>Show import log</summary>
              <pre className="log">{status.lines.slice(-8).join("\n")}</pre>
            </details>
          ) : null}
        </div>
      )}

      {rec.status === "error" && <div className="panel err">{rec.error}</div>}

      <div className="detail-nav">
        <nav className="match-tabs" aria-label="Match views">
          {TABS.map((t) => (
            <a key={t.key} className={active === t.key ? "on" : ""} href={`#/r/${id}/${t.key}`}>
              {t.label}
            </a>
          ))}
        </nav>
        <nav className="deeper-links" aria-label="Deeper analysis">
          {DEEPER.map((t) => (
            <a key={t.key} className={active === t.key ? "on" : ""} href={`#/r/${id}/${t.key}`}>
              {t.label}
            </a>
          ))}
        </nav>
      </div>

      {isPrimary && active === "match" && (
        <MatchView id={id} rec={rec} agents={agents} names={names} me={me} />
      )}
      {isPrimary && active === "rounds" && (
        <RoundsView id={id} agents={agents} names={names} me={me} />
      )}
      {isPrimary && active === "map" && <MapView id={id} agents={agents} />}
      {active === "aim" && <AimView id={id} agents={agents} weapons={weapons} />}
      {active === "stats" && <StatsView id={id} agents={agents} />}
      {active === "coach" && <CoachView id={id} agents={agents} />}
      {active === "judge" && <JudgeView id={id} agents={agents} />}
      {active === "ai" && <AskView id={id} agents={agents} />}
      {active === "tables" && <TableExplorer id={id} />}
    </div>
  );
}
