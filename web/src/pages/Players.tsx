import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  Agent,
  MapInfo,
  PlayersBuildStatus,
  PlayersCatalog,
  PlayersRow,
  shortLevel,
} from "../api";
import { AgentIcon } from "../components/bits";
import { DASH, fmtDuration, fmtMatchDate, num, shortId } from "../format";

function age(builtAt?: number | null): string {
  if (!builtAt) return "never";
  const mins = Math.max(0, Math.round((Date.now() - builtAt) / 60000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

export default function Players({ agents }: { agents: Agent[] }) {
  const [data, setData] = useState<PlayersCatalog | null>(null);
  const [maps, setMaps] = useState<MapInfo[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [status, setStatus] = useState<PlayersBuildStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [onlyMulti, setOnlyMulti] = useState(false);
  const [query, setQuery] = useState("");

  const load = useCallback(() => {
    api
      .players()
      .then(setData)
      .catch((e) => setErr(String(e)));
  }, []);

  useEffect(() => {
    load();
    api.playerBuildStatus().then(setStatus).catch(() => {});
    api.maps().then(setMaps).catch(() => {});
  }, [load]);

  const building = status?.phase === "Building the deep layer";
  useEffect(() => {
    if (!building) return;
    const t = window.setInterval(() => {
      api
        .playerBuildStatus()
        .then((s) => {
          setStatus(s);
          if (s.phase !== "Building the deep layer") load();
        })
        .catch(() => {});
    }, 1500);
    return () => window.clearInterval(t);
  }, [building, load]);

  const startBuild = async () => {
    setBusy(true);
    try {
      setStatus(await api.playerBuild(true));
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const setMe = async (subject: string) => {
    await api.playerSetMe(subject).catch(() => {});
    load();
  };

  const rows = useMemo<PlayersRow[]>(() => {
    const list = data?.players ?? [];
    const matched = query.trim()
      ? list.filter((p) => {
          const needle = query.trim().toLowerCase();
          return (p.label || "").toLowerCase().includes(needle) || p.subject.toLowerCase().includes(needle);
        })
      : list;
    return onlyMulti ? matched.filter((p) => p.matches > 1) : matched;
  }, [data, onlyMulti, query]);

  if (err) return <div className="page"><div className="panel err">{err}</div></div>;
  if (!data) return <div className="page"><div className="panel muted">Reading every replay…</div></div>;

  const deep = data.deep;
  const guess = data.players.find((p) => p.likely_me) ?? null;
  const mapName = (level: string | null) =>
    maps.find((m) => m.mapUrl && m.mapUrl === level)?.name || shortLevel(level) || "Unknown map";

  return (
    <div className="page stack">
      <div className="panel">
        <div className="row spread">
          <h2>Players</h2>
          <span className="muted small">
            {data.match_count} matches · {data.players.length} players
          </span>
        </div>
        <p className="small muted" style={{ marginTop: 0 }}>
          Everyone who appears in your replays, grouped by the player id each replay carries. Replays
          don't include Riot IDs, so a name here is a nickname kept on this machine. Open a player to
          give them one.
        </p>
        <div className="row wrap roster-tools">
          <span className={`chip ${data.me ? "ready" : guess ? "warn" : ""}`}>
            {data.me
              ? `You · ${data.names[data.me] || shortId(data.me)}`
              : guess
              ? `Probably you · ${shortId(guess.subject)} (${guess.matches} matches)`
              : "You · not set"}
          </span>
          {!data.me && guess && (
            <button className="ghost small" onClick={() => setMe(guess.subject)}>
              That is me
            </button>
          )}
          {data.me && (
            <button className="ghost small" onClick={() => api.playerSetMe(null).then(load)}>
              Not me
            </button>
          )}
          <button
            className={`ghost small ${onlyMulti ? "on" : ""}`}
            aria-pressed={onlyMulti}
            onClick={() => setOnlyMulti((v) => !v)}
          >
            Repeat players
          </button>
          <label className="search roster-search">
            <span className="sr-only">Search players</span>
            <input
              type="search"
              value={query}
              placeholder="Search by name or id"
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
        </div>
      </div>

      <div className="panel">
        <div className="row spread roster-head">
          <h2>Roster</h2>
          <span className="muted small">{rows.length} shown</span>
        </div>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Player</th>
                <th>Matches</th>
                <th>Rounds</th>
                <th title="Damage to opponents per round, pooled over every round of every match">ADR</th>
                <th title="Riot's own combat score divided by rounds, pooled">ACS</th>
                <th>K / D</th>
                <th title="Rounds with a kill, assist, survival or trade, pooled">KAST</th>
                <th>Agents</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => {
                const isMe = data.me === p.subject;
                return (
                  <tr key={p.subject}>
                    <td>
                      <span className="row" style={{ gap: 6, flexWrap: "wrap" }}>
                        {isMe && <span className="badge Blue">you</span>}
                        {!data.me && p.likely_me && <span className="chip tiny warn">probably you</span>}
                        <a href={`#/p/${encodeURIComponent(p.subject)}`} title={p.subject}>
                          {p.label || (
                            <span className="mono small">player {shortId(p.subject)}</span>
                          )}
                        </a>
                      </span>
                    </td>
                    <td>{p.matches}</td>
                    <td>{num(p.rounds)}</td>
                    <td>{num(p.adr, 1)}</td>
                    <td>{num(p.acs, 1)}</td>
                    <td>
                      {num(p.kills)} / {num(p.deaths)}
                    </td>
                    <td>{p.kast_pct == null ? DASH : `${num(p.kast_pct, 1)}%`}</td>
                    <td>
                      <span className="agentstrip">
                        {p.agents.slice(0, 8).map((a) => (
                          <AgentIcon key={a} uuid={a} agents={agents} size={18} />
                        ))}
                      </span>
                    </td>
                  </tr>
                );
              })}
              {!rows.length && (
                <tr>
                  <td colSpan={8} className="muted">
                    {query
                      ? `No player matches “${query}”.`
                      : "No one appears twice in this archive yet."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <details className="more-panel">
        <summary>
          <span>Deeper reads</span>
          <span className="muted small">
            {building
              ? "Reading every match…"
              : deep.built
              ? `Aim, coach and judge data · ${deep.stale ? "older than the archive" : `built ${age(deep.built_at)}`}`
              : "Aim, coach and judge data"}
          </span>
        </summary>
        <p className="small muted" style={{ marginTop: 14 }}>
          The aim, coaching and judge views need one extra pass over each match's movement data. Build
          it once here and those pages stay quick; the stats above come from the replay caches either
          way.
        </p>
        <div className="row wrap">
          <button className="ghost small" disabled={busy || building} onClick={startBuild}>
            {building
              ? "Building…"
              : deep.built && !deep.stale
              ? "Refresh the deeper read"
              : "Build the deeper read"}
          </button>
          {building && (
            <span className="muted small">reading {deep.matches} matches, this takes a few seconds</span>
          )}
          {status?.error && <span className="chip error small">{status.error}</span>}
          {status?.result && !building && (
            <span className="muted small">
              last run: {status.result.built}/{status.result.matches} matches in {status.result.seconds}s
              {status.result.failed.length
                ? ` · ${status.result.failed.length} could not be read`
                : ""}
            </span>
          )}
        </div>
      </details>

      <details className="more-panel">
        <summary>
          <span>Matches in the archive</span>
          <span className="muted small">
            {data.matches.length} matches · how the roster was put together
          </span>
        </summary>
        <div className="table-wrap" style={{ marginTop: 14 }}>
          <table className="data">
            <thead>
              <tr>
                <th>Map</th>
                <th>Date</th>
                <th>Match</th>
                <th>Players</th>
                <th>Length</th>
                <th title="Other replays of the same match, counted once">Copies</th>
              </tr>
            </thead>
            <tbody>
              {data.matches.map((m) => (
                <tr key={m.match_id}>
                  <td>
                    <a href={`#/r/${m.replay_id}/match`}>{mapName(m.level_name)}</a>
                  </td>
                  <td className="small">{fmtMatchDate(m.timestamp_ticks)}</td>
                  <td className="mono small">{m.match_id.slice(0, 8)}</td>
                  <td>{m.players.length}</td>
                  <td>{fmtDuration(m.duration_ms)}</td>
                  <td className="muted small">
                    {m.duplicates.length ? m.duplicates.map((d) => shortId(d)).join(", ") : DASH}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data.skipped.length > 0 && (
          <ul className="checks">
            {data.skipped.map((s) => (
              <li key={s.replay_id} className="warn">
                {shortId(s.replay_id)}: {s.why}
              </li>
            ))}
          </ul>
        )}
        <ul className="notes">
          {data.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      </details>
    </div>
  );
}
