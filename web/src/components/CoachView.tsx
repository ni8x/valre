import { useEffect, useState } from "react";
import { api, Agent, CoachPayload, CoachPlayer, DeathsPayload } from "../api";
import { DASH, fmt0, fmtTime, shortId } from "../format";
import { AgentIcon, agentNameOf } from "./bits";

const GRADE_COLOR: Record<string, string> = {
  A: "#35c26e",
  B: "#a3d43d",
  C: "#e0b641",
  D: "#e08741",
  E: "#d95d5d",
};

const TEAM_COLOR: Record<string, string> = { Blue: "#4a8fe0", Red: "#e05a4a" };

function polar(cx: number, cy: number, r: number, i: number, n: number): [number, number] {
  const a = -Math.PI / 2 + (2 * Math.PI * i) / n;
  return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
}

function Radar({
  axes,
  label,
  values,
  teamValues,
  nRings = [0.25, 0.5, 0.75, 1],
}: {
  axes: string[];
  label: Record<string, string>;
  values: Record<string, number | null>;
  teamValues: Record<string, number | null> | null;
  nRings?: number[];
}) {
  const cx = 140;
  const cy = 132;
  const R = 92;
  const n = axes.length;
  const pts = (vals: Record<string, number | null>) =>
    axes
      .map((a, i) => {
        const s = Math.max(0, Math.min(100, vals[a] ?? 0));
        const [x, y] = polar(cx, cy, (R * s) / 100, i, n);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
  return (
    <svg viewBox="0 0 280 268" width={300} height={280}>
      {nRings.map((f) => (
        <polygon
          key={f}
          points={axes
            .map((_, i) =>
              polar(cx, cy, R * f, i, n)
                .map((v) => v.toFixed(1))
                .join(",")
            )
            .join(" ")}
          fill="none"
          stroke="#2c3240"
          strokeWidth={f === 1 ? 1.5 : 1}
        />
      ))}
      {axes.map((a, i) => {
        const [x, y] = polar(cx, cy, R, i, n);
        return <line key={a} x1={cx} y1={cy} x2={x} y2={y} stroke="#2c3240" />;
      })}
      {teamValues && (
        <polygon
          points={pts(teamValues)}
          fill="none"
          stroke="#8b93a5"
          strokeDasharray="4 4"
          strokeWidth={1.5}
        />
      )}
      <polygon
        points={pts(values)}
        fill="rgba(74,143,224,0.22)"
        stroke="#4a8fe0"
        strokeWidth={2}
      />
      {axes.map((a, i) => {
        const [x, y] = polar(cx, cy, R + 17, i, n);
        const cos = Math.cos(-Math.PI / 2 + (2 * Math.PI * i) / n);
        const anchor = Math.abs(cos) < 0.3 ? "middle" : cos > 0 ? "start" : "end";
        return (
          <text key={a} x={x} y={y + 4} textAnchor={anchor} fontSize={11} fill="#aeb6c4">
            {label[a]}
          </text>
        );
      })}
    </svg>
  );
}

export default function CoachView({ id, agents }: { id: string; agents: Agent[] }) {
  const [data, setData] = useState<CoachPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [deaths, setDeaths] = useState<DeathsPayload | null>(null);

  useEffect(() => {
    api
      .coach(id)
      .then((d) => {
        setData(d);
        setSel((s) => (s && d.players.some((p) => p.subject === s) ? s : d.players[0]?.subject ?? null));
      })
      .catch((e) => setErr(String(e)));
  }, [id]);

  useEffect(() => {
    api.deaths(id).then(setDeaths).catch(() => {});
  }, [id]);

  if (err) return <div className="panel err">{err}</div>;
  if (!data) return <div className="panel muted">Loading coach profile…</div>;

  const byId = new Map(data.players.map((p) => [p.subject, p]));
  const p: CoachPlayer = (sel ? byId.get(sel) : null) ?? data.players[0];
  if (!p) return <div className="panel muted">No players.</div>;
  const team = data.teams[p.team] ?? null;
  const color = TEAM_COLOR[p.team] ?? "#4a8fe0";
  const gradeCol = p.grade ? GRADE_COLOR[p.grade] ?? "#8b93a5" : "#8b93a5";
  const dr = deaths?.players.find((x) => x.subject === p.subject) ?? null;
  const dcount = (key: string) => dr?.classes.find((c) => c.key === key)?.n ?? 0;

  return (
    <div className="stack">
      <div className="panel">
        <div className="row spread">
          <h2>Power profile</h2>
          <span className="muted small">
            {data.n_rounds} rounds, our own 0-100 scale, v{data.version}
          </span>
        </div>
        <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
          {data.players.map((q) => {
            const active = q.subject === p.subject;
            return (
              <button
                key={q.subject}
                className={`chip pointer ${q.team === "Blue" ? "tm-blue" : "tm-red"}`}
                aria-pressed={active}
                onClick={() => setSel(q.subject)}
                style={{ outline: active ? "2px solid #4a8fe0" : "none" }}
                title={`${q.subject} · overall ${q.overall ?? "—"} (${q.grade ?? "?"})`}
              >
                <AgentIcon uuid={q.agent_uuid} agents={agents} size={18} />
                <span className="mono small">{agentNameOf(agents, q.agent_uuid) ?? shortId(q.subject)}</span>
                <span className="mono small" style={{ color: q.grade ? GRADE_COLOR[q.grade] : undefined }}>
                  {q.grade ?? "—"}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="row" style={{ alignItems: "flex-start", flexWrap: "wrap", gap: 12 }}>
        <div className="panel" style={{ flex: "0 0 auto" }}>
          <div className="row">
            <div
              style={{
                width: 64,
                height: 64,
                borderRadius: "50%",
                border: `3px solid ${gradeCol}`,
                color: gradeCol,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 30,
                fontWeight: 700,
              }}
              title="Overall grade: weighted mean of the available axes"
            >
              {p.grade ?? "—"}
            </div>
            <div className="stack" style={{ marginLeft: 10 }}>
              <span className="mono">{agentNameOf(agents, p.agent_uuid) ?? shortId(p.subject)}</span>
              <span className="muted small">
                overall {p.overall ?? "—"} / 100 · {p.team}
              </span>
              {p.verdict && <span style={{ fontSize: 14, maxWidth: 260 }}>{p.verdict.text}</span>}
            </div>
          </div>
          <Radar
            axes={data.axes}
            label={data.axis_label}
            values={p.axes}
            teamValues={team ? team.axes : null}
          />
          <div className="muted small">
            solid = player · dashed = {p.team} team average · rings 25/50/75/100
          </div>
        </div>

        <div className="panel" style={{ flex: "1 1 320px", minWidth: 320 }}>
          <h2>Axes</h2>
          {data.axes.map((a) => {
            const s = p.axes[a];
            const n = p.axis_n[a] ?? 0;
            const conf = p.confidence[a] ?? "none";
            return (
              <div key={a} className="row" style={{ gap: 8, marginBottom: 6 }} title={`weight ${((data.weights[a] ?? 0) * 100).toFixed(0)}% · band worst ${data.bands[a]?.[0]} → best ${data.bands[a]?.[1]}`}>
                <span className="small" style={{ width: 130 }}>
                  {data.axis_label[a]}
                </span>
                <div style={{ flex: 1, height: 8, background: "#232837", borderRadius: 4 }}>
                  <div
                    style={{
                      width: `${s ?? 0}%`,
                      height: 8,
                      borderRadius: 4,
                      background: color,
                    }}
                  />
                </div>
                <span className="mono small" style={{ width: 42, textAlign: "right" }}>
                  {s ?? "—"}
                </span>
                <span className="muted small" style={{ width: 74 }}>
                  n={n} · {conf}
                </span>
              </div>
            );
          })}
          <div className="muted small">
            n = samples behind the axis (duel windows, gun hits, rounds…). low = treat as a hint.
          </div>
        </div>

        <div className="panel" style={{ flex: "1 1 320px", minWidth: 320 }}>
          <h2>Prescription</h2>
          {p.prescription ? (
            <div className="stack">
              <div className="row">
                <span className="badge" style={{ background: GRADE_COLOR[p.prescription.grade] ?? undefined }}>
                  {p.prescription.grade}
                </span>
                <b>{p.prescription.label}</b>
                <span className="muted small">
                  {p.prescription.raw ?? ""} · n={p.prescription.n} · {p.prescription.confidence} confidence
                </span>
              </div>
              <p style={{ margin: 0 }}>{p.prescription.text}</p>
              <div className="muted small">
                weakest axis of the profile (score {p.prescription.score}/100) - fix it and the rest follows.
              </div>
            </div>
          ) : (
            <div className="muted small">No prescription yet - not enough samples.</div>
          )}
          <h2 style={{ marginTop: 14 }}>Team averages</h2>
          <table className="data">
            <thead>
              <tr>
                <th>Team</th>
                <th>Overall</th>
                <th>Grade</th>
                {data.axes.map((a) => (
                  <th key={a}>{data.axis_label[a]}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.teams).map(([tm, t]) => (
                <tr key={tm} className={`tm-${tm.toLowerCase()}`}>
                  <td>{tm}</td>
                  <td className="mono">{t.overall ?? "—"}</td>
                  <td className="mono" style={{ color: t.grade ? GRADE_COLOR[t.grade] : undefined }}>
                    {t.grade ?? "—"}
                  </td>
                  {data.axes.map((a) => (
                    <td key={a} className="mono">
                      {t.axes[a] ?? "—"}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {deaths && (
        <div className="panel">
          <div className="row spread">
            <h2>Deaths</h2>
            <span className="muted small">
              {dr ? `${dr.deaths} deaths this match` : "no deaths"} · what the export shows, not a verdict
            </span>
          </div>
          {!dr ? (
            <div className="muted small">{shortId(p.subject)} - no death was recorded for this player.</div>
          ) : (
            <div className="stack">
              <div className="metrics">
                <div>
                  <b>{dr.deaths}</b>
                  <span>deaths</span>
                </div>
                {deaths.classes.map((c) => (
                  <div key={c.key} className={dcount(c.key) ? "bad" : undefined}>
                    <b>{dcount(c.key)}</b>
                    <span>{c.label}</span>
                  </div>
                ))}
              </div>
              <ul className="checks">
                {dr.classes.map((c) => (
                  <li key={c.key} className="info">
                    {c.text}
                  </li>
                ))}
              </ul>
              <details className="detail">
                <summary>Every death, in order</summary>
                <table className="data">
                  <thead>
                    <tr>
                      <th>Round</th>
                      <th>Into round</th>
                      <th>Killer</th>
                      <th title="Damage dealt to opponents in that round">Damage</th>
                      <th title="Ultimate points at the moment of death, against the cost of one cast where the export shows a cast">Ult charge</th>
                      <th>Flags</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dr.events.map((e, i) => (
                      <tr key={i}>
                        <td className="mono">R{e.round}</td>
                        <td className="mono">{e.round_ms === null ? DASH : fmtTime(e.round_ms)}</td>
                        <td>
                          {e.suicide
                            ? "self"
                            : e.killer
                              ? agentNameOf(agents, e.killer_agent) ?? shortId(e.killer)
                              : DASH}
                        </td>
                        <td className="mono">{fmt0(e.damage_round)}</td>
                        <td className="mono">
                          {e.ult_points === null
                            ? DASH
                            : e.ult_cost
                              ? `${e.ult_points}/${e.ult_cost}`
                              : `${e.ult_points}`}
                          {e.ult_ready === true && <span className="chip warn"> charged</span>}
                        </td>
                        <td>
                          <div className="row" style={{ flexWrap: "wrap", gap: 4 }}>
                            {e.first_blood && <span className="chip">opening</span>}
                            {e.traded === false && <span className="chip warn">untraded</span>}
                            {e.isolated === true && <span className="chip">isolated</span>}
                            {e.dealt_no_damage === true && <span className="chip warn">no damage</span>}
                            {e.suicide && <span className="chip warn">self</span>}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!deaths.ult_available && (
                  <div className="muted small">
                    This export carries no ultimate charge, so the ult column stays empty.
                  </div>
                )}
              </details>
            </div>
          )}
        </div>
      )}

      <div className="panel muted small">
        {data.notes.map((nt, i) => (
          <div key={i}>· {nt}</div>
        ))}
      </div>
    </div>
  );
}
