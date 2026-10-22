import { useEffect, useMemo, useState } from "react";
import { api, Agent, StatsPayload } from "../api";
import { DASH, fmtTime, shortId } from "../format";
import { AgentIcon, agentNameOf } from "./bits";

type Side = "Blue" | "Red";

function buyTag(round: any, team: Side): string {
  const cell = round?.economy?.[team];
  if (!cell) return "—";
  const buy = typeof cell.buy === "string" ? cell.buy : "?";
  return cell.alv != null ? `${buy} ${(cell.alv / 1000).toFixed(1)}k` : buy;
}

export default function RoundsView({
  id,
  agents,
  names,
  me,
}: {
  id: string;
  agents: Agent[];
  names: Record<string, string>;
  me: string | null;
}) {
  const [data, setData] = useState<StatsPayload | null>(null);
  const [basic, setBasic] = useState<any[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    setBasic(null);
    setErr(null);
    api
      .stats(id)
      .then((payload) => {
        if (alive) setData(payload);
      })
      .catch(() => {
        api
          .rounds(id)
          .then((list) => {
            if (alive) setBasic(list);
          })
          .catch((e) => {
            if (alive) setErr(String(e));
          });
      });
    return () => {
      alive = false;
    };
  }, [id]);

  const playersById = useMemo(() => {
    const map = new Map<string, any>();
    for (const p of data?.players || []) map.set(p.subject, p);
    return map;
  }, [data]);

  const decorated = useMemo(() => {
    let blue = 0;
    let red = 0;
    return (data?.rounds || []).map((round: any) => {
      if (round.winner === "Blue") blue += 1;
      if (round.winner === "Red") red += 1;
      const firstBlood = (round.deaths || []).find((d: any) => d.first_blood && !d.suicide);
      return {
        round,
        running: { Blue: blue, Red: red },
        firstBlood,
        kills: (round.deaths || []).filter((d: any) => !d.suicide).length,
      };
    });
  }, [data]);

  if (err) return <div className="panel err">{err}</div>;
  if (basic) {
    return (
      <div className="panel">
        <h2>Rounds</h2>
        <table className="data">
          <thead>
            <tr>
              <th>#</th>
              <th>Start</th>
              <th>Teams switch</th>
            </tr>
          </thead>
          <tbody>
            {basic.map((r) => (
              <tr key={r.round}>
                <td className="mono">{r.round}</td>
                <td className="mono">{fmtTime(r.start_ms)}</td>
                <td className="mono">{fmtTime(r.switch_teams_ms)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  if (!data) return <div className="panel muted">Reading the rounds…</div>;

  const rounds = decorated;
  const overview = data.overview || {};
  const finalScore = overview.score;
  const half = rounds.find((r) => r.round.round === 12)?.running;
  const pistolRounds = rounds.filter((r) => r.round.round === 1 || r.round.round === 13);
  const label = (subject: string) => {
    const player = playersById.get(subject);
    return names[subject] || agentNameOf(agents, player?.agent_uuid) || shortId(subject);
  };

  return (
    <div className="rounds-view stack">
      <div className="section-head">
        <div>
          <h2>Round log</h2>
          <p className="muted small">
            Open any round for the kill order. First bloods and trades are marked; the score is what it
            was after that round.
          </p>
        </div>
      </div>

      <div className="round-highlights">
        <div>
          <b>
            {finalScore?.Blue ?? rounds[rounds.length - 1]?.running.Blue ?? DASH}
            <span>:</span>
            {finalScore?.Red ?? rounds[rounds.length - 1]?.running.Red ?? DASH}
          </b>
          <span>final score</span>
        </div>
        <div>
          <b>
            {half ? `${half.Blue} : ${half.Red}` : DASH}
          </b>
          <span>at the half</span>
        </div>
        <div>
          <b>
            {pistolRounds
              .map((r) => (r.round.winner ? `${r.round.round === 1 ? "R1" : "R13"} ${r.round.winner}` : null))
              .filter(Boolean)
              .join(" · ") || DASH}
          </b>
          <span>pistol rounds</span>
        </div>
      </div>

      <div className="round-list">
        {rounds.map(({ round, running, firstBlood, kills }) => {
          const winner = round.winner as Side | null;
          const fbName = firstBlood ? label(firstBlood.killer) : null;
          const winnerLabel = winner || "Undecided";
          return (
            <details
              key={round.round}
              className={`round-row ${winner === "Blue" ? "won-blue" : winner === "Red" ? "won-red" : ""}`}
            >
              <summary>
                <span className="round-index">R{round.round}</span>
                <span className={`round-winner ${winner ? winner.toLowerCase() : "none"}`}>
                  {winnerLabel}
                </span>
                <span className="round-running">
                  {running.Blue} : {running.Red}
                </span>
                <span className="round-buys">
                  <i className="buy-dot blue" aria-hidden="true" />
                  {buyTag(round, "Blue")}
                  <span className="round-vs">vs</span>
                  <i className="buy-dot red" aria-hidden="true" />
                  {buyTag(round, "Red")}
                </span>
                <span className="round-kills small muted">
                  {kills} {kills === 1 ? "kill" : "kills"}
                </span>
                <span className="round-fb small muted">
                  {fbName ? (
                    <>
                      First blood <i aria-hidden="true">·</i> {fbName}
                    </>
                  ) : (
                    "No opening kill"
                  )}
                </span>
                <span className="round-chevron" aria-hidden="true">
                  +
                </span>
              </summary>
              <div className="round-body">
                {round.sides && (
                  <div className="round-side-note small muted">
                    {round.sides.attacker} attacked · {round.sides.defender} defended
                  </div>
                )}
                {(round.deaths || []).length === 0 ? (
                  <p className="muted small">No kills were recorded in this round.</p>
                ) : (
                  <ol className="kill-list">
                    {round.deaths.map((d: any, i: number) => (
                      <li key={i} className={d.suicide ? "muted" : ""}>
                        <span className="kill-time">{fmtTime(d.t)}</span>
                        {!d.suicide && (
                          <>
                            <AgentIcon
                              uuid={playersById.get(d.killer)?.agent_uuid}
                              agents={agents}
                              size={22}
                            />
                            <span
                              className={`kill-name ${me === d.killer ? "is-you" : ""}`}
                              title={d.killer}
                            >
                              {label(d.killer)}
                            </span>
                            <span className="kill-arrow" aria-hidden="true">
                              →
                            </span>
                          </>
                        )}
                        <AgentIcon
                          uuid={playersById.get(d.victim)?.agent_uuid}
                          agents={agents}
                          size={22}
                        />
                        <span
                          className={`kill-name ${me === d.victim ? "is-you" : ""}`}
                          title={d.victim}
                        >
                          {label(d.victim)}
                          {d.suicide ? " (self)" : ""}
                        </span>
                        {d.first_blood && <span className="tag fb-tag">first blood</span>}
                        {!d.suicide && d.traded && (
                          <span className="tag" title="The killer died within five seconds">
                            traded
                          </span>
                        )}
                      </li>
                    ))}
                  </ol>
                )}
              </div>
            </details>
          );
        })}
      </div>
    </div>
  );
}
