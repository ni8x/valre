import { useEffect, useState } from "react";
import { api, Agent, JudgePayload, JudgePlayer } from "../api";
import { AgentIcon, agentNameOf } from "./bits";
import { DASH, fmt0, fmt1, shortId } from "../format";

const TEAM_COLOR: Record<string, string> = { Blue: "#4a8fe0", Red: "#e05a4a" };
const GRADE_COLOR: Record<string, string> = {
  A: "#35c26e",
  B: "#a3d43d",
  C: "#e0b641",
  D: "#e08741",
  E: "#d95d5d",
};

function severityLabel(s: number): string {
  if (s >= 0.9) return "far past the line";
  if (s >= 0.7) return "well past the line";
  return "past the line";
}

function fmtValue(key: string, v: number): string {
  if (key === "reaction" || key === "time_to_damage") return `${fmt0(v)} ms`;
  if (key === "crosshair_placement" || key === "spray_control") return `${fmt1(v)}°`;
  if (key === "counter_strafe" || key === "first_engagement" || key === "headshots")
    return `${fmt1(v * 100)}%`;
  if (key === "trades" || key === "isolation") return `${fmt1(v)}%`;
  return fmt1(v);
}

export default function JudgeView({ id, agents }: { id: string; agents: Agent[] }) {
  const [data, setData] = useState<JudgePayload | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);

  useEffect(() => {
    let stop = false;
    api
      .judge(id)
      .then((d) => {
        if (stop) return;
        setData(d);
        setSel((cur) =>
          cur && d.players.some((p) => p.subject === cur) ? cur : d.players[0]?.subject ?? null,
        );
      })
      .catch((e) => {
        if (!stop) setErr(String(e));
      });
    return () => {
      stop = true;
    };
  }, [id]);

  if (err) return <div className="panel err">{err}</div>;
  if (!data) return <div className="panel muted">Judging the rounds…</div>;

  const byId = new Map(data.players.map((p) => [p.subject, p]));
  const p: JudgePlayer = (sel ? byId.get(sel) : null) ?? data.players[0];
  const name = (subject: string) =>
    agentNameOf(agents, byId.get(subject)?.agent_uuid ?? null) ?? shortId(subject);
  const drills = data.training[p.subject] ?? [];

  return (
    <div className="stack">
      <div className="panel row spread" style={{ flexWrap: "wrap", gap: 12 }}>
        <div>
          <h2>Judge</h2>
          <div className="muted small">
            {data.n_rounds} rounds, {data.lost_rounds.length} of them lost · blame points are ours,
            the facts behind them are the export's
          </div>
        </div>
        <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
          {data.players.map((q) => {
            const active = q.subject === p.subject;
            return (
              <button
                key={q.subject}
                className={`chip ${q.team === "Blue" ? "tm-blue" : "tm-red"}`}
                aria-pressed={active}
                onClick={() => setSel(q.subject)}
                title={`${q.points} blame points · ${fmt1(q.share_pct ?? 0)}% of the team`}
                style={active ? { outline: "1px solid var(--line)" } : undefined}
              >
                <AgentIcon agents={agents} uuid={q.agent_uuid} />
                {name(q.subject)}{" "}
                <b style={{ color: GRADE_COLOR[q.grade ?? ""] ?? "inherit" }}>{q.grade ?? DASH}</b>
              </button>
            );
          })}
        </div>
      </div>

      <div className="row" style={{ alignItems: "flex-start", flexWrap: "wrap", gap: 12 }}>
        {(["Blue", "Red"] as const).map((t) => {
          const team = data.teams[t];
          if (!team) return null;
          return (
            <div className="panel" key={t} style={{ flex: "1 1 380px" }}>
              <div className="detail-head">
                <h2 style={{ color: TEAM_COLOR[t] }}>{t}</h2>
                <span className="muted small">
                  {team.lost} lost · {team.changeable} on a buy that could win · {team.points}{" "}
                  points
                </span>
              </div>
              <p>{team.summary}</p>
              <table className="data">
                <thead>
                  <tr>
                    <th>Player</th>
                    <th title="Blame points: counted faults, times the published weights">
                      Points
                    </th>
                    <th title="Points as a share of the team's points">Share</th>
                    <th>Grade</th>
                  </tr>
                </thead>
                <tbody>
                  {team.ranked.map((r) => (
                    <tr key={r.subject}>
                      <td>
                        <AgentIcon agents={agents} uuid={byId.get(r.subject)?.agent_uuid ?? null} />
                        {name(r.subject)}
                      </td>
                      <td>{r.points}</td>
                      <td>{fmt1(r.share_pct ?? 0)}%</td>
                      <td>
                        <b style={{ color: GRADE_COLOR[r.grade ?? ""] ?? "inherit" }}>
                          {r.grade ?? DASH}
                        </b>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="muted small">{team.devil}</p>
            </div>
          );
        })}
      </div>

      <div className="panel">
        <div className="detail-head">
          <h2>
            {name(p.subject)} · grade {p.grade ?? DASH}
          </h2>
          <span className="muted small">
            {p.points} blame points · {fmt1(p.share_pct ?? 0)}% of {p.team} ·{" "}
            {data.n_rounds} rounds
          </span>
        </div>
        <div className="metrics">
          <div>
            <b>{p.deaths}</b>
            <span>deaths</span>
          </div>
          <div>
            <b>{p.entry_first}</b>
            <span>rounds he entered first</span>
          </div>
          <div className={p.entry_late ? "bad" : ""}>
            <b>{p.entry_late}</b>
            <span>late entries</span>
          </div>
          <div>
            <b>{p.entry_avg_order === null ? DASH : fmt1(p.entry_avg_order)}</b>
            <span>average entry order</span>
          </div>
          <div className={p.missed_trades ? "bad" : ""}>
            <b>{p.missed_trades}</b>
            <span>untraded deaths he was alive for</span>
          </div>
        </div>
        <div className="row" style={{ alignItems: "flex-start", flexWrap: "wrap", gap: 16 }}>
          <div style={{ flex: "1 1 300px" }}>
            <h3>The case against</h3>
            {p.faults.length === 0 ? (
              <p className="muted small">No counted fault in a lost round.</p>
            ) : (
              <ul className="checks">
                {p.faults.map((f) => (
                  <li key={f.key} className={f.weight >= 2 ? "warn" : "info"}>
                    {f.label} · {f.n}× · {f.weight} points each · {f.points} total
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div style={{ flex: "1 1 300px" }}>
            <h3>The case for</h3>
            <ul className="checks">
              {p.case_for.map((c, i) => (
                <li key={i} className="ok">
                  {c}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="detail-head">
          <h2>Training plan</h2>
          <span className="muted small">
            measured weaknesses, each with the number and the sample behind it
          </span>
        </div>
        {drills.length === 0 ? (
          <p className="muted small">
            Nothing measured came out on the bad side of a threshold with a big enough sample.
          </p>
        ) : (
          <div className="stack">
            {drills.map((d) => (
              <div key={d.key}>
                <div className="row spread">
                  <b>{d.label}</b>
                  <span className="muted small">
                    {fmtValue(d.key, d.value)} vs {fmtValue(d.key, d.threshold)} threshold · n=
                    {d.n} · {severityLabel(d.severity)}
                  </span>
                </div>
                <div className="muted small">{d.why}</div>
                <div>{d.drill}</div>
              </div>
            ))}
          </div>
        )}
        <div className="muted small" style={{ marginTop: 8 }}>
          Sample sizes: entry order and blame come from the {data.n_rounds} rounds, the aim numbers
          from the shots the export could tie to this player.
        </div>
      </div>

      <div className="panel">
        <div className="detail-head">
          <h2>Why the rounds were lost</h2>
          <span className="muted small">
            {data.lost_rounds.filter((r) => r.changeable).length} of {data.lost_rounds.length} were
            on a buy that could win
          </span>
        </div>
        <details className="detail">
          <summary>Every lost round</summary>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Round</th>
                  <th>Lost by</th>
                  <th>Buy</th>
                  <th title="Their buy against the winner's buy">Against</th>
                  <th title="Whether the buy could win the round">Changeable</th>
                  <th title="The biggest player advantage the losing team held">Lead</th>
                  <th title="Who planted, where, and how far the defuse got">Spike</th>
                  <th>Why</th>
                </tr>
              </thead>
              <tbody>
                {data.lost_rounds.map((r) => (
                  <tr key={r.round}>
                    <td>R{r.round}</td>
                    <td>{r.loser}</td>
                    <td>{r.buy}</td>
                    <td>{r.opponent_buy}</td>
                    <td>{r.changeable ? "yes" : "no"}</td>
                    <td>{r.max_lead_lost}</td>
                    <td>
                      {r.plant
                        ? [
                            r.plant.planter ? name(r.plant.planter) : DASH,
                            `site ${r.plant.site ?? DASH}`,
                            r.plant.defuser ? `${name(r.plant.defuser)} defused` : null,
                            r.plant.defuse_s ? `${fmt1(r.plant.defuse_s)}s of defuse` : null,
                          ]
                            .filter(Boolean)
                            .join(" · ")
                        : DASH}
                    </td>
                    <td className="small">
                      <div className="row" style={{ flexWrap: "wrap", gap: 4 }}>
                        {r.classes.map((c) => (
                          <span
                            key={c.key}
                            className={c.key === "lost_a_lead" || c.key === "no_plant" ? "chip warn" : "chip"}
                            title={c.text}
                          >
                            {c.text}
                          </span>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </div>

      <div className="panel muted small">
        {data.notes.map((n, i) => (
          <div key={i}>{n}</div>
        ))}
      </div>
    </div>
  );
}
