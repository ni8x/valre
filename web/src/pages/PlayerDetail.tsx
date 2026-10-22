import { useCallback, useEffect, useState } from "react";
import {
  api,
  Agent,
  MapInfo,
  PlayerProfile,
  PlayersBuildStatus,
  SideBucket,
  shortLevel,
  Weapon,
  weaponIcon,
} from "../api";
import { AgentIcon } from "../components/bits";
import { DASH, fmtMatchDate, num, shortId } from "../format";

const GRADE_COLOR: Record<string, string> = {
  A: "#35c26e",
  B: "#a3d43d",
  C: "#e0b641",
  D: "#e08741",
  E: "#d95d5d",
};

const AIM_LABEL: Record<string, [string, string]> = {
  pre_aim_deg: ["Crosshair placement", "degrees off target before the first shot - lower is better"],
  reaction_ms: ["Reaction", "ms between seeing an enemy and the first shot - lower is better"],
  ttd_ms: ["Time to damage", "ms from seeing an enemy to damaging them - lower is better"],
  counter_strafe_pct: ["Counter-strafe", "share of shots taken while nearly stopped"],
  first_eng_acc: ["First engagement accuracy", "how often the first shot of a fight lands"],
  spray_spread_deg: ["Spray spread", "degrees the spray wanders while shooting"],
  spray_radius_cm: ["Spray radius", "cm the spray covers at its finish"],
  spray_ttk_ms: ["Spray time to kill", "ms of spraying before a kill"],
  hs_kill_pct: ["Headshot kills", "share of gun kills that were headshots"],
};

function bucketRow(label: string, b: SideBucket | undefined) {
  return (
    <tr key={label}>
      <td>{label}</td>
      <td>{num(b?.rounds)}</td>
      <td>{num(b?.kills)}</td>
      <td>{num(b?.deaths)}</td>
      <td>{num(b?.kd, 2)}</td>
      <td>{num(b?.adr, 1)}</td>
      <td>{b?.kast_pct == null ? DASH : `${num(b?.kast_pct, 1)}%`}</td>
      <td>
        {num(b?.entry_kills)} / {num(b?.entry_deaths)}
      </td>
    </tr>
  );
}

export default function PlayerDetail({
  subject,
  agents,
  weapons,
  go,
}: {
  subject: string;
  agents: Agent[];
  weapons: Weapon[];
  go: (path: string) => void;
}) {
  const [data, setData] = useState<PlayerProfile | null>(null);
  const [maps, setMaps] = useState<MapInfo[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [status, setStatus] = useState<PlayersBuildStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<string | null>(null);

  const load = useCallback(
    (deep = true) => {
      api
        .player(subject, deep)
        .then((p) => {
          setData(p);
          setErr(null);
        })
        .catch((e) => setErr(String(e)));
    },
    [subject]
  );

  useEffect(() => {
    setData(null);
    load(true);
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
          if (s.phase !== "Building the deep layer") load(true);
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

  const saveLabel = async () => {
    if (draft == null) return;
    await api.playerSetName(subject, draft).catch(() => {});
    setDraft(null);
    load(false);
  };

  if (err) return <div className="page"><div className="panel err">{err}</div></div>;
  if (!data) return <div className="page"><div className="panel muted">Reading this player…</div></div>;

  const name = data.label || shortId(subject);
  const pool = data.pooled;
  const aimEntries = Object.entries(data.aim).filter(
    ([key, v]) => AIM_LABEL[key] && typeof v === "object" && v !== null
  ) as [string, { value: number | null; n: number }][];
  const mean = data.mean_of_matches;
  const deep = data.deep;
  const deepOk = data.deep_state.built && !data.deep_state.stale;
  const entries = data.matches.reduce(
    (acc, m) => ({
      kills: acc.kills + (m.entry_kills || 0),
      deaths: acc.deaths + (m.entry_deaths || 0),
    }),
    { kills: 0, deaths: 0 }
  );

  const mapName = (level: string | null) =>
    maps.find((m) => m.mapUrl && m.mapUrl === level)?.name || shortLevel(level) || "Unknown map";
  const agentFor = (uuid: string | null) =>
    agents.find((a) => a.uuid.toLowerCase() === (uuid || "").toLowerCase()) || null;

  const weaponIconFor = (weaponName: string): string | undefined => {
    const needle = weaponName.toLowerCase();
    const w =
      weapons.find((x) => x.name.toLowerCase() === needle) ||
      weapons.find((x) => (x.aliases || []).some((a) => a.toLowerCase() === needle)) ||
      weapons.find((x) => x.name.toLowerCase().includes(needle) || needle.includes(x.name.toLowerCase()));
    return w ? weaponIcon(w.uuid) : undefined;
  };

  return (
    <div className="page detail-page stack">
      <div>
        <a className="back-link" href="#/players">
          ← All players
        </a>
        <div className="detail-top player-top">
          <div className="detail-title-block">
            <div className="detail-eyebrow">
              <span>{data.match_count} matches</span>
              <i aria-hidden="true" />
              <span>{num(pool.rounds)} rounds</span>
              <i aria-hidden="true" />
              <span className="mono" title="The id the replay uses; names are local nicknames">
                {shortId(subject)}
              </span>
            </div>
            {draft == null ? (
              <h1>
                {name}
                {data.is_me && (
                  <span className="badge Blue" style={{ marginLeft: 10, verticalAlign: "middle" }}>
                    you
                  </span>
                )}
              </h1>
            ) : (
              <form
                className="title-rename"
                onSubmit={(e) => {
                  e.preventDefault();
                  saveLabel();
                }}
              >
                <input
                  autoFocus
                  value={draft}
                  maxLength={80}
                  aria-label={`Nickname for ${name}`}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") setDraft(null);
                  }}
                />
                <button className="primary small" type="submit">
                  Save
                </button>
                <button className="ghost small" type="button" onClick={() => setDraft(null)}>
                  Cancel
                </button>
              </form>
            )}
            <div className="player-agents">
              <span className="agentstrip">
                {data.agents.map((a) => (
                  <AgentIcon key={a} uuid={a} agents={agents} size={22} />
                ))}
              </span>
              <span className="muted small">
                {data.agents
                  .map((a) => agentFor(a)?.name)
                  .filter(Boolean)
                  .join(" · ") || "No agent recorded"}
              </span>
            </div>
          </div>
          <div className="row wrap player-actions">
            {draft == null && (
              <button className="ghost" onClick={() => setDraft(data.label || "")}>
                {data.label ? "Rename" : "Give a name"}
              </button>
            )}
            {!data.is_me ? (
              <button
                className="ghost"
                onClick={() => api.playerSetMe(subject).then(() => load(false))}
              >
                This is me
              </button>
            ) : (
              <button className="ghost" onClick={() => api.playerSetMe(null).then(() => load(false))}>
                Clear identity
              </button>
            )}
            <button className="text-btn" onClick={() => go("#/players")}>
              All players
            </button>
          </div>
        </div>
      </div>

      <div className="player-band" aria-label="Headline stats">
        <div>
          <b>{num(pool.kd, 2)}</b>
          <span>K / D</span>
          <small>
            {num(pool.kills)} K · {num(pool.deaths)} D · {num(pool.assists)} A
          </small>
        </div>
        <div>
          <b>{num(pool.acs, 1)}</b>
          <span title="Riot's combat score, divided by rounds">ACS</span>
          <small>combat score</small>
        </div>
        <div>
          <b>{num(pool.adr, 1)}</b>
          <span title="Damage dealt to opponents per round">ADR</span>
          <small>damage a round</small>
        </div>
        <div>
          <b>{pool.kast_pct == null ? DASH : `${num(pool.kast_pct, 1)}%`}</b>
          <span title="Rounds with a kill, assist, survival or trade">KAST</span>
          <small>rounds impacted</small>
        </div>
        <div>
          <b>{mean.hs_pct == null ? DASH : `${num(mean.hs_pct, 1)}%`}</b>
          <span title="Average of each match's headshot share">HS</span>
          <small>headshots</small>
        </div>
        <div>
          <b>
            {entries.kills} / {entries.deaths}
          </b>
          <span title="Opening duels won and lost">First bloods</span>
          <small>won / lost</small>
        </div>
        <div>
          <b>{data.match_count}</b>
          <span>Matches</span>
          <small>{num(pool.rounds)} rounds played</small>
        </div>
      </div>

      <div className="panel">
        <h2>Match by match</h2>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Match</th>
                <th>Agent</th>
                <th>K / D / A</th>
                <th>ACS</th>
                <th>ADR</th>
                <th>KAST</th>
                <th>HS</th>
              </tr>
            </thead>
            <tbody>
              {data.matches.map((m) => {
                const agent = agentFor(m.agent_uuid);
                return (
                  <tr key={m.replay_id}>
                    <td>
                      <a href={`#/r/${m.replay_id}/match`}>{mapName(m.level_name)}</a>
                      <div className="muted small">{fmtMatchDate(m.timestamp_ticks)}</div>
                    </td>
                    <td>
                      <span className="row" style={{ gap: 7 }}>
                        <AgentIcon uuid={m.agent_uuid} agents={agents} size={20} />
                        <span className="small">{agent?.name || DASH}</span>
                      </span>
                    </td>
                    <td>
                      {num(m.kills)} / {num(m.deaths)} / {num(m.assists)}
                    </td>
                    <td>{num(m.acs, 1)}</td>
                    <td>{num(m.adr, 1)}</td>
                    <td>{m.kast_pct == null ? DASH : `${num(m.kast_pct, 1)}%`}</td>
                    <td>{m.hs_pct == null ? DASH : `${num(m.hs_pct, 1)}%`}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="muted small" style={{ margin: "12px 0 0" }}>
          Open a match for its round-by-round log. K / D / A counts every kill, death and assist.
        </p>
      </div>

      <div className="panel">
        <h2>Attacking and defending</h2>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Side</th>
                <th>Rounds</th>
                <th>K</th>
                <th>D</th>
                <th>K / D</th>
                <th>ADR</th>
                <th>KAST</th>
                <th title="Opening duels won / lost">First bloods</th>
              </tr>
            </thead>
            <tbody>
              {bucketRow("Attack", data.sides.attack)}
              {bucketRow("Defend", data.sides.defend)}
            </tbody>
          </table>
        </div>
        <p className="muted small" style={{ margin: "12px 0 0" }}>
          Rounds whose side could not be read, plus overtime, are counted in neither side.
        </p>
      </div>

      <details className="more-panel">
        <summary>
          <span>Buy rounds</span>
          <span className="muted small">How the player performs after pistol, eco, force and full buys</span>
        </summary>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Buy</th>
                <th>Rounds</th>
                <th>K</th>
                <th>D</th>
                <th>K / D</th>
                <th>ADR</th>
                <th>KAST</th>
                <th>First bloods</th>
              </tr>
            </thead>
            <tbody>
              {["pistol", "eco", "force", "full", "unknown"].map((b) => bucketRow(b, data.buys[b]))}
            </tbody>
          </table>
        </div>
        <p className="muted small" style={{ margin: "12px 0 0" }}>
          A round is classed by the team's average loadout value when it started.
        </p>
      </details>

      <details className="more-panel">
        <summary>
          <span>Aim and weapons</span>
          <span className="muted small">Shot-level detail from the replay</span>
        </summary>
        <div className="row" style={{ alignItems: "flex-start", flexWrap: "wrap", gap: 12, marginTop: 16 }}>
          <div className="panel" style={{ flex: "1 1 360px" }}>
            <h2>Aim</h2>
            <ul className="checks">
              {aimEntries.map(([key, v]) => {
                const [label, why] = AIM_LABEL[key];
                const ratio = key === "counter_strafe_pct" || key === "first_eng_acc" || key === "hs_kill_pct";
                const shown =
                  v.value == null
                    ? DASH
                    : ratio
                    ? `${num(v.value * 100, 1)}%`
                    : num(v.value, key.endsWith("_ms") ? 0 : 1);
                return (
                  <li key={key} className="info" title={why}>
                    {label}: <b>{shown}</b> <span className="muted small">n={v.n}</span>
                  </li>
                );
              })}
              <li className="info" title="Every shot record in these matches, including shots that were not part of a duel">
                Shots: <b>{num((data.aim.shots as number) ?? null)}</b>{" "}
                <span className="muted small">
                  accuracy {data.aim.accuracy == null ? DASH : `${num((data.aim.accuracy as number) * 100, 1)}%`}
                </span>
              </li>
            </ul>
            <p className="small muted" style={{ marginBottom: 0 }}>
              Measured from what the replay records, not from what the player could see.
            </p>
          </div>
          <div className="panel" style={{ flex: "1 1 360px" }}>
            <h2>Weapons</h2>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Weapon</th>
                    <th>Matches</th>
                    <th>Shots</th>
                    <th>Acc</th>
                    <th>HS</th>
                    <th>Dmg/shot</th>
                    <th>Shots/kill</th>
                  </tr>
                </thead>
                <tbody>
                  {data.weapons.slice(0, 12).map((w) => {
                    const icon = weaponIconFor(w.name);
                    return (
                      <tr key={w.name}>
                        <td>
                          <span className="row" style={{ gap: 6 }}>
                            {icon && (
                              <img
                                className="weapon-ico"
                                src={icon}
                                alt=""
                                onError={(e) => ((e.target as HTMLImageElement).style.display = "none")}
                              />
                            )}
                            {w.name}
                          </span>
                        </td>
                        <td>{w.matches}</td>
                        <td>{num(w.shots)}</td>
                        <td>{w.acc == null ? DASH : `${num(w.acc * 100, 1)}%`}</td>
                        <td>{w.hs_rate == null ? DASH : `${num(w.hs_rate * 100, 1)}%`}</td>
                        <td>{num(w.dmg_per_shot, 1)}</td>
                        <td>{num(w.shots_per_kill, 1)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </details>

      <details className="more-panel">
        <summary>
          <span>Judged across matches</span>
          <span className="muted small">
            {deepOk
              ? `Built ${new Date(data.deep_state.built_at || 0).toLocaleDateString()}`
              : "This app's own reading of lost rounds"}
          </span>
        </summary>
        {!deepOk ? (
          <div style={{ marginTop: 14 }}>
            <p className="small muted" style={{ marginTop: 0 }}>
              Grades, blame points and drills need an extra pass over each match's movement data.
              Build it once here and these pages stay quick.
            </p>
            <div className="row wrap">
              <button className="ghost small" disabled={busy || building} onClick={startBuild}>
                {building ? "Building…" : "Build the deeper read"}
              </button>
              {status?.error && <span className="chip error small">{status.error}</span>}
            </div>
          </div>
        ) : deep ? (
          <div className="stack" style={{ marginTop: 14 }}>
            <div className="row wrap">
              <span className="badge" style={{ background: "#222", color: GRADE_COLOR[deep.grade || "C"] }}>
                grade {deep.grade ?? DASH}
              </span>
              <span className="muted small">
                {num(deep.points)} blame points of {num(deep.team_points)} for this player's teams across{" "}
                {deep.matches} matches · {deep.share_pct == null ? DASH : `${num(deep.share_pct, 1)}%`} share
              </span>
              {deep.worst_match_id && (
                <span className="muted small">
                  worst match {deep.worst_match_id.slice(0, 8)} ({num(deep.worst_share_pct, 1)}%)
                </span>
              )}
            </div>
            {deep.classes.length > 0 && (
              <div className="metrics">
                {deep.classes.slice(0, 7).map((c) => (
                  <div key={c.key}>
                    <b>{c.n}</b>
                    <span>{c.key.replace(/_/g, " ")}</span>
                  </div>
                ))}
              </div>
            )}
            <div className="row" style={{ alignItems: "flex-start", flexWrap: "wrap", gap: 12 }}>
              <div style={{ flex: "1 1 320px" }}>
                <div className="small muted" style={{ marginBottom: 4 }}>
                  Blame across lost rounds
                </div>
                <ul className="checks">
                  {deep.faults.slice(0, 6).map((f) => (
                    <li key={f.key} className={f.weight >= 2 ? "warn" : "info"}>
                      {f.label ?? f.key} · {f.n}× · {f.points} points
                    </li>
                  ))}
                </ul>
              </div>
              <div style={{ flex: "1 1 320px" }}>
                <div className="small muted" style={{ marginBottom: 4 }}>
                  Drills, worst first
                </div>
                {deep.drills.length ? (
                  <ul className="checks">
                    {deep.drills.slice(0, 4).map((d) => (
                      <li key={`${d.key}-${d.match_id}`} className="info">
                        <span>
                          {d.label}: {d.drill}{" "}
                          <span className="muted small">
                            (measured {d.value} vs {d.threshold}, n={d.n})
                          </span>
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="muted small">No measured weakness cleared the sample-size gate.</div>
                )}
              </div>
            </div>
            {deep.history.length > 1 && (
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>Match</th>
                      <th>Blame points</th>
                      <th>Share</th>
                      <th>Grade</th>
                      <th>Deaths</th>
                      <th>ADR</th>
                    </tr>
                  </thead>
                  <tbody>
                    {deep.history.map((h) => (
                      <tr key={h.match_id}>
                        <td className="mono small">{h.match_id.slice(0, 8)}</td>
                        <td>{num(h.points)}</td>
                        <td>{num(h.share_pct, 1)}%</td>
                        <td style={{ color: GRADE_COLOR[h.grade || ""] || undefined }}>{h.grade ?? DASH}</td>
                        <td>{num(h.deaths)}</td>
                        <td>{num(h.adr, 1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="small muted" style={{ marginBottom: 0 }}>
              This is the app's own reading, counted from the export. A player can lose a round and
              still have been right.
            </p>
          </div>
        ) : null}
      </details>

      <details className="more-panel">
        <summary>
          <span>About these numbers</span>
          <span className="muted small">Where the totals come from</span>
        </summary>
        <ul className="notes" style={{ marginTop: 12 }}>
          {data.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
        {data.skipped.length > 0 && (
          <ul className="checks">
            {data.skipped.map((s) => (
              <li key={s.replay_id} className="warn">
                {shortId(s.replay_id)} could not be read: {s.why}
              </li>
            ))}
          </ul>
        )}
      </details>
    </div>
  );
}
