import { useCallback, useEffect, useState } from "react";
import {
  Agent,
  api,
  IdentityAnswer,
  IdentitySignature,
  IdentityStatus,
  PlayersCatalog,
  ReplayRow,
  RiotTag,
  TeammatesPayload,
} from "../api";
import PairView from "../components/PairView";
import { DASH, fmtMatchDate, num } from "../format";

type Props = { go: (path: string) => void; agents: Agent[]; me?: string; partner?: string };

const msg = (e: unknown) => (e instanceof Error ? e.message : String(e));

function shortId(id: string | null | undefined): string {
  return id ? id.slice(0, 8) : "—";
}

// The offline tag: every replay already holds the four facts - the agent each player used, the map,
// the match time and the first-half side. Ask three of them and the list ranks itself. A candidate
// is "certain" only when it answered every asked fact and nobody else did.
function FindMe({ agents, onTag }: { agents: Agent[]; onTag: (subject: string) => void }) {
  const [replays, setReplays] = useState<ReplayRow[]>([]);
  const [replay, setReplay] = useState("");
  const [sig, setSig] = useState<IdentitySignature | null>(null);
  const [agent, setAgent] = useState("");
  const [side, setSide] = useState("");
  const [at, setAt] = useState("");
  const [answer, setAnswer] = useState<IdentityAnswer | null>(null);
  const [status, setStatus] = useState<IdentityStatus | null>(null);
  const [riotName, setRiotName] = useState("");
  const [riotLine, setRiotLine] = useState("");
  const [riot, setRiot] = useState<RiotTag | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api
      .replays()
      .then((rs) => {
        setReplays(rs);
        setReplay((prev) => prev || rs[0]?.id || "");
      })
      .catch((e) => setErr(msg(e)));
    api.identityStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    if (!replay) return;
    let alive = true;
    setSig(null);
    setAnswer(null);
    setRiot(null);
    api
      .identitySignature(replay)
      .then((s) => {
        if (!alive) return;
        setSig(s);
        setAt(s.start_local || "");
      })
      .catch((e) => {
        if (alive) setErr(msg(e));
      });
    return () => {
      alive = false;
    };
  }, [replay]);

  const find = () => {
    setBusy(true);
    setErr(null);
    api
      .identify(replay, { agent, side, at })
      .then(setAnswer)
      .catch((e) => setErr(msg(e)))
      .finally(() => setBusy(false));
  };

  const sync = () => {
    setBusy(true);
    setErr(null);
    api
      .riotTag(replay, riotName.trim(), riotLine.trim())
      .then((out) => {
        setRiot(out);
        if (out.subject) onTag(out.subject);
      })
      .catch((e) => setErr(msg(e)))
      .finally(() => setBusy(false));
  };

  const mark = (v: boolean | null | undefined) => (v === true ? "yes" : v === false ? "no" : "—");

  return (
    <div>
      <p className="muted small" style={{ marginTop: 14 }}>
        Choose a replay, then add the agent you played, the side you started on or the match time. The
        app ranks the players who fit those facts. It only calls a match certain when one player fits
        every fact you gave and nobody else does.
      </p>
      <div className="row wrap" style={{ gap: 6 }}>
        <select className="small" value={replay} onChange={(e) => setReplay(e.target.value)}>
          {replays.map((r) => (
            <option key={r.id} value={r.id}>
              {r.title || shortId(r.id)} {r.level_name ? `· ${r.level_name.split("/").pop()}` : ""}
            </option>
          ))}
        </select>
        <select className="small" value={agent} onChange={(e) => setAgent(e.target.value)}>
          <option value="">agent…</option>
          {agents.map((a) => (
            <option key={a.uuid} value={a.name}>
              {a.name}
            </option>
          ))}
        </select>
        <select className="small" value={side} onChange={(e) => setSide(e.target.value)}>
          <option value="">side…</option>
          <option value="attacker">attacker first</option>
          <option value="defender">defender first</option>
        </select>
        <input
          className="small"
          style={{ width: 160 }}
          value={at}
          placeholder="2026-04-19T21:43"
          onChange={(e) => setAt(e.target.value)}
          title="Match start, local time. Within 120 minutes counts as the same match."
        />
        <button className="small" disabled={!replay || busy} onClick={find}>
          Find me
        </button>
      </div>

      {sig ? (
        <p className="muted small">
          {sig.map || "map unknown"} · started {sig.start_local || "—"} · first half{" "}
          {sig.sides_first.attacker ? `attacker ${sig.sides_first.attacker}, defender ${sig.sides_first.defender}` : "—"}
          {" · "}
          {sig.players.length} players
        </p>
      ) : null}

      {answer ? (
        <>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Player</th>
                  <th>Agent</th>
                  <th>Team</th>
                  <th>First half</th>
                  <th>Facts</th>
                  <th>Score</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {answer.candidates.map((c) => (
                  <tr key={c.subject}>
                    <td className="mono small">
                      {shortId(c.subject)}
                      {c.certain ? <span className="chip ready"> certain</span> : null}
                    </td>
                    <td className="small">{c.agent || "—"}</td>
                    <td className="small">{c.team || "—"}</td>
                    <td className="small">{c.side_first || "—"}</td>
                    <td className="small mono">
                      agent {mark(c.facts.agent)} · side {mark(c.facts.side)} · time {mark(c.facts.time)}
                    </td>
                    <td>
                      {c.score}/{answer.asked.facts}
                    </td>
                    <td>
                      <button className="small" onClick={() => onTag(c.subject)}>
                        That is me
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted small">{answer.note}</p>
        </>
      ) : null}

      {status?.configured ? (
        <div className="row wrap" style={{ gap: 6, marginTop: 6 }}>
          <input
            className="small"
            style={{ width: 130 }}
            value={riotName}
            placeholder="Riot name"
            onChange={(e) => setRiotName(e.target.value)}
          />
          <input
            className="small"
            style={{ width: 60 }}
            value={riotLine}
            placeholder="TAG"
            onChange={(e) => setRiotLine(e.target.value)}
          />
          <button className="small" disabled={busy || !riotName || !riotLine} onClick={sync}>
            Sync with Riot
          </button>
          {riot ? (
            <span className="small muted">
              {riot.subject ? `tagged ${shortId(riot.subject)}` : "no tag"} · {riot.why || "evidence written"}
              {riot.evidence ? ` · ${riot.evidence.map || "?"} ± ${riot.evidence.start_delta_s ?? "?"} s` : ""}
            </span>
          ) : null}
        </div>
      ) : (
        <p className="muted small">{status?.note || "Reading the Riot setting…"}</p>
      )}
      {err ? <p className="err small">{err}</p> : null}
    </div>
  );
}

function Picker({ go, agents }: { go: (path: string) => void; agents: Agent[] }) {
  const [catalog, setCatalog] = useState<PlayersCatalog | null>(null);
  const [mates, setMates] = useState<TeammatesPayload | null>(null);
  const [pick, setPick] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(() => {
    setErr(null);
    api
      .players()
      .then((c) => {
        setCatalog(c);
        setPick((prev) => prev || c.me || c.likely_me || c.players[0]?.subject || null);
      })
      .catch((e) => setErr(msg(e)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!pick) return;
    let alive = true;
    setMates(null);
    api
      .teammates(pick)
      .then((m) => {
        if (alive) setMates(m);
      })
      .catch((e) => {
        if (alive) setErr(msg(e));
      });
    return () => {
      alive = false;
    };
  }, [pick]);

  if (err) return <div className="page"><div className="panel err">{err}</div></div>;
  if (!catalog)
    return (
      <div className="page">
        <div className="panel muted">Reading every replay…</div>
      </div>
    );
  if (!catalog.players.length)
    return (
      <div className="page">
        <div className="empty-archive">
          <span className="drop-mark" aria-hidden="true">.vrf</span>
          <h3>No players yet</h3>
          <p className="muted">Add a replay from History, then pick yourself here.</p>
          <a className="text-link" href="#/">Go to History</a>
        </div>
      </div>
    );

  const selected = catalog.players.find((p) => p.subject === pick) || null;
  const others = catalog.players.filter((p) => p.subject !== pick);
  const repeats = (mates?.teammates || []).filter((t) => t.matches > 1);
  const showAllMates = showAll || repeats.length === 0;
  const mateRows = showAllMates ? mates?.teammates || [] : repeats;

  const setMe = (subject: string | null) => {
    api.playerSetMe(subject).then(() => load());
  };

  return (
    <div className="page stack duo-page">
      <div className="panel">
        <div className="row spread">
          <h2>Duo check</h2>
          <span className="muted small">Every number counts shared rounds only</span>
        </div>
        <p className="muted small" style={{ marginTop: 0 }}>
          Pick yourself, then open a teammate. The pair page shows the rounds you actually played
          together — evidence, not a guess.
        </p>
        <div className="duo-identity">
          <label>
            <span className="small muted">Reading for</span>
            <select value={pick || ""} onChange={(e) => setPick(e.target.value)}>
              {catalog.players.map((p) => (
                <option key={p.subject} value={p.subject}>
                  {p.label || `player ${shortId(p.subject)}`} · {p.matches} matches
                </option>
              ))}
            </select>
          </label>
          {pick && catalog.me !== pick ? (
            <button className="ghost small" onClick={() => setMe(pick)}>
              This is me
            </button>
          ) : (
            <span className="chip ready">This is you</span>
          )}
          {pick && (
            <a className="text-link" href={`#/p/${encodeURIComponent(pick)}`}>
              Open profile
            </a>
          )}
        </div>
        {selected && (
          <p className="muted small" style={{ margin: "10px 0 0" }}>
            {selected.matches} matches · {selected.rounds ?? DASH} rounds in this archive
          </p>
        )}
      </div>

      <div className="panel">
        <div className="row spread roster-head">
          <h2>Who you play with</h2>
          <div className="row wrap" style={{ gap: 10 }}>
            <span className="muted small">
              {mates
                ? showAllMates
                  ? `${mates.teammates.length} teammates`
                  : `${mateRows.length} repeat ${mateRows.length === 1 ? "partner" : "partners"}`
                : "Reading…"}
            </span>
            {mates && repeats.length > 0 && (
              <button
                className={`ghost small ${showAll ? "on" : ""}`}
                aria-pressed={showAll}
                onClick={() => setShowAll((v) => !v)}
              >
                {showAll ? "Only repeats" : `Show all ${mates.teammates.length}`}
              </button>
            )}
          </div>
        </div>
        {!mates ? (
          <p className="muted small">Reading teammates…</p>
        ) : mates.teammates.length === 0 ? (
          <p className="muted small">
            No teammate shares a round with this player yet. You can still compare with anyone below.
          </p>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Partner</th>
                  <th>Matches</th>
                  <th>Rounds</th>
                  <th>W–L</th>
                  <th>Win rate</th>
                  <th>Last played</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {mateRows.map((t) => (
                  <tr key={t.subject}>
                    <td>
                      <a
                        href={`#/p/${encodeURIComponent(t.subject)}`}
                        title={t.subject}
                      >
                        {t.label || `player ${shortId(t.subject)}`}
                      </a>
                    </td>
                    <td>{t.matches}</td>
                    <td
                      title={
                        t.rounds_without_winner
                          ? `${t.rounds_without_winner} shared rounds have no winner in the replay and are kept out of the win rate`
                          : undefined
                      }
                    >
                      {t.rounds_together ?? DASH}
                    </td>
                    <td>
                      {t.wins}–{t.losses}
                    </td>
                    <td>{t.win_pct == null ? DASH : `${num(t.win_pct, 1)}%`}</td>
                    <td>{fmtMatchDate(t.last_seen)}</td>
                    <td>
                      <button
                        className="small ghost"
                        disabled={!pick}
                        onClick={() =>
                          pick && go(`#/duo/${encodeURIComponent(pick)}/${encodeURIComponent(t.subject)}`)
                        }
                      >
                        Open pair
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="panel duo-anyone">
        <h2>Compare with anyone</h2>
        <p className="muted small" style={{ marginTop: 0 }}>
          Even a pair who never shared a round gets an honest answer, not a table of zeros.
        </p>
        <label className="field-inline">
          <span className="small muted">Partner</span>
          <select
            defaultValue=""
            onChange={(e) => {
              const v = e.target.value;
              if (v && pick) go(`#/duo/${encodeURIComponent(pick)}/${encodeURIComponent(v)}`);
            }}
          >
            <option value="">Pick a player…</option>
            {others.map((p) => (
              <option key={p.subject} value={p.subject}>
                {p.label || `player ${shortId(p.subject)}`} ({p.matches} matches)
              </option>
            ))}
          </select>
        </label>
      </div>

      <details className="more-panel">
        <summary>
          <span>Not sure which one is you?</span>
          <span className="muted small">Match yourself by agent, side and start time</span>
        </summary>
        <FindMe
          agents={agents}
          onTag={(subject) => {
            setMe(subject);
            setPick(subject);
          }}
        />
      </details>

      <details className="more-panel">
        <summary>
          <span>How this is counted</span>
          <span className="muted small">Teammates, shared rounds and win rate</span>
        </summary>
        <ul className="notes" style={{ marginTop: 12 }}>
          {(mates?.notes || catalog.notes || []).map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      </details>
    </div>
  );
}

export default function DuoCheck({ go, agents, me, partner }: Props) {
  if (me && partner) return <PairView me={me} partner={partner} go={go} />;
  return <Picker go={go} agents={agents} />;
}