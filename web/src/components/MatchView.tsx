import { useEffect, useMemo, useState } from "react";
import { Agent, StatsPayload, api } from "../api";
import { BUY_ORDER, DASH, fmtDuration, fmt0, fmt1, pct } from "../format";
import { AgentIcon, agentNameOf } from "./bits";
import { BuyLegend, RoundStrip } from "./RoundStrip";

type Side = "Blue" | "Red";

const TEAM_ORDER: Side[] = ["Blue", "Red"];

function teamClass(team: Side) {
  return team === "Blue" ? "team-blue" : "team-red";
}

function StatHeader({ label, tip }: { label: string; tip: string }) {
  return (
    <th scope="col" title={tip}>
      {label}
    </th>
  );
}

function TeamBoard({
  team,
  players,
  agents,
  names,
  me,
  startSide,
}: {
  team: Side;
  players: any[];
  agents: Agent[];
  names: Record<string, string>;
  me: string | null;
  startSide: string | null;
}) {
  return (
    <section className={`team-board ${teamClass(team)}`} aria-label={`${team} team`}>
      <header className="team-board-head">
        <span className="team-name">
          <i aria-hidden="true" />
          {team}
        </span>
        {startSide && <span className="team-side">{startSide}</span>}
      </header>
      <div className="table-scroll">
        <table className="match-table">
          <thead>
            <tr>
              <th scope="col">Player</th>
              <StatHeader label="K" tip="Kills" />
              <StatHeader label="D" tip="Deaths, including deaths to your own utility" />
              <StatHeader label="A" tip="Assists" />
              <StatHeader label="ACS" tip="Average combat score per round" />
              <StatHeader label="ADR" tip="Damage to opponents per round" />
              <StatHeader label="KAST" tip="Rounds with a kill, assist, survival or trade" />
              <StatHeader label="HS" tip="Share of this player's kills that were headshots" />
              <StatHeader label="FB" tip="First bloods: opening kills won" />
            </tr>
          </thead>
          <tbody>
            {players.map((p) => {
              const s = p.stats || {};
              const agentName = agentNameOf(agents, p.agent_uuid) || "Unknown agent";
              const nick = names[p.subject];
              const you = me === p.subject;
              return (
                <tr key={p.subject} className={you ? "is-you" : ""}>
                  <td>
                    <div className="match-player">
                      <AgentIcon uuid={p.agent_uuid} agents={agents} size={28} />
                      <div className="match-player-name">
                        <b title={p.subject}>{nick || agentName}</b>
                        {nick && <span>{agentName}</span>}
                      </div>
                      {you && <span className="you-tag">you</span>}
                    </div>
                  </td>
                  <td className="num">{fmt0(s.kills)}</td>
                  <td className="num">{fmt0(s.deaths)}</td>
                  <td className="num">{fmt0(s.assists)}</td>
                  <td className="num strong" title="Average combat score">{fmt0(s.acs)}</td>
                  <td className="num" title="Damage to opponents per round">{fmt1(s.adr)}</td>
                  <td className="num">{pct(s.kast_pct, 0)}</td>
                  <td className="num">{pct(s.hs_pct, 0)}</td>
                  <td className="num">{fmt0(s.entry_kills)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function scoreWinner(score: { Blue?: number; Red?: number } | null | undefined): Side | null {
  if (!score || !Number.isFinite(score.Blue) || !Number.isFinite(score.Red)) return null;
  if (score.Blue === score.Red) return null;
  return (score.Blue as number) > (score.Red as number) ? "Blue" : "Red";
}

function EconomyCard({ team, economy }: { team: Side; economy: any }) {
  const data = economy?.[team];
  if (!data) {
    return (
      <div className={`econ-card ${teamClass(team)}`}>
        <div className="econ-top">
          <span className="team-name">
            <i aria-hidden="true" />
            {team}
          </span>
        </div>
        <p className="muted small">No economy reading in this export.</p>
      </div>
    );
  }
  const mix = data.buy_mix || {};
  const segments = BUY_ORDER.filter((b) => b !== "unknown").map((b) => ({
    key: b,
    count: mix[b] || 0,
  }));
  const total = segments.reduce((sum, s) => sum + s.count, 0);
  const fullPct = data.win_pct_by_buy?.full;
  const fullWins = data.wins_by_buy?.full;
  const fullRounds = mix.full;

  return (
    <div className={`econ-card ${teamClass(team)}`}>
      <div className="econ-top">
        <span className="team-name">
          <i aria-hidden="true" />
          {team}
        </span>
        <span className="econ-alv" title="Team average loadout value at round start">
          {data.avg_alv != null ? `${(data.avg_alv / 1000).toFixed(1)}k` : DASH}
          <small>average gear</small>
        </span>
      </div>
      <div className="econ-split">
        <div>
          <b>{fullPct != null ? `${Math.round(fullPct)}%` : DASH}</b>
          <span>full-buy rounds won</span>
        </div>
        <div>
          <b>
            {fullWins != null && fullRounds != null ? `${fullWins} / ${fullRounds}` : DASH}
          </b>
          <span>full buys converted</span>
        </div>
      </div>
      <div className="buy-strip" aria-hidden="true">
        {segments.map((s) =>
          s.count > 0 ? (
            <i
              key={s.key}
              className={`b-${s.key}`}
              style={{ width: `${total ? (s.count / total) * 100 : 0}%` }}
              title={`${s.key}: ${s.count}`}
            />
          ) : null,
        )}
      </div>
      <div className="buy-caption small muted">
        {segments
          .filter((s) => s.count > 0)
          .map((s) => `${s.count} ${s.key}`)
          .join(" · ") || "No buys recorded"}
      </div>
    </div>
  );
}

export default function MatchView({
  id,
  rec,
  agents,
  names,
  me,
}: {
  id: string;
  rec: any;
  agents: Agent[];
  names: Record<string, string>;
  me: string | null;
}) {
  const [data, setData] = useState<StatsPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const ready = rec.status === "ready";

  useEffect(() => {
    if (!ready) return;
    let alive = true;
    api
      .stats(id)
      .then((payload) => {
        if (alive) setData(payload);
      })
      .catch((e) => {
        if (alive) setErr(String(e));
      });
    return () => {
      alive = false;
    };
  }, [id, ready]);

  const byTeam = useMemo(() => {
    const groups: Record<Side, any[]> = { Blue: [], Red: [] };
    for (const p of data?.players || []) {
      const team = p.team === "Red" ? "Red" : "Blue";
      groups[team].push(p);
    }
    for (const team of TEAM_ORDER) {
      groups[team].sort((a, b) => (Number(b.stats?.acs) || 0) - (Number(a.stats?.acs) || 0));
    }
    return groups;
  }, [data]);

  if (!ready) {
    return (
      <div className="panel">
        <p className="muted">
          {rec.status === "error"
            ? "This replay could not be read."
            : "The match opens as soon as the replay finishes importing."}
        </p>
      </div>
    );
  }
  if (err) return <div className="panel err">{err}</div>;
  if (!data) return <div className="panel muted">Reading the match…</div>;

  const overview = data.overview || {};
  const score = overview.score;
  const winner = scoreWinner(score);
  const rounds = data.rounds || [];
  const nRounds = overview.n_rounds ?? rounds.length;
  const startSides = rounds[0]?.sides || null;
  const sideWord = (team: Side) =>
    startSides
      ? startSides.attacker === team
        ? "started on attack"
        : "started on defence"
      : null;
  const duration = overview.duration_ms ?? rec.summary?.duration_ms;

  return (
    <div className="match-view stack">
      <section className="score-panel" aria-label="Final score">
        <div className="score-team blue">
          <span className="team-name">
            <i aria-hidden="true" />
            Blue
          </span>
          <small>{sideWord("Blue") || "\u00a0"}</small>
        </div>
        <div className="score-main">
          {score ? (
            <>
              <b className={`score-num ${winner === "Blue" ? "won" : ""}`}>{score.Blue}</b>
              <span className="score-colon">:</span>
              <b className={`score-num ${winner === "Red" ? "won" : ""}`}>{score.Red}</b>
            </>
          ) : (
            <span className="score-missing">No final score recorded</span>
          )}
        </div>
        <div className="score-team red">
          <span className="team-name">
            <i aria-hidden="true" />
            Red
          </span>
          <small>{sideWord("Red") || "\u00a0"}</small>
        </div>
        <div className="score-facts">
          <span>{nRounds} rounds</span>
          <span>{fmtDuration(duration)}</span>
          <span>{data.players.length} players</span>
        </div>
      </section>

      <section className="section-block">
        <div className="section-head">
          <div>
            <h2>Scoreboard</h2>
            <p className="muted small">
              Kills, deaths and assists beside the per-round reads that show impact: combat score,
              damage, survival and opening picks.
            </p>
          </div>
        </div>
        <div className="boards">
          {TEAM_ORDER.map((team) => (
            <TeamBoard
              key={team}
              team={team}
              players={byTeam[team]}
              agents={agents}
              names={names}
              me={me}
              startSide={sideWord(team)}
            />
          ))}
        </div>
        <p className="stat-note small muted">
          ACS — average combat score. ADR — damage to opponents per round. KAST — rounds with a kill,
          assist, survival or trade. HS — headshot kills. FB — first bloods.
        </p>
      </section>

      <section className="section-block">
        <div className="section-head">
          <div>
            <h2>Economy</h2>
            <p className="muted small">
              What each side could afford, and whether the full buys actually converted.
            </p>
          </div>
        </div>
        <div className="econ-grid">
          {TEAM_ORDER.map((team) => (
            <EconomyCard key={team} team={team} economy={overview.economy} />
          ))}
        </div>
      </section>

      <section className="section-block">
        <div className="section-head">
          <div>
            <h2>Round by round</h2>
            <p className="muted small">
              One pair per round: the fill is what each team bought, the top edge is who won.
            </p>
          </div>
          <a className="text-link" href={`#/r/${id}/rounds`}>
            Read the round log
          </a>
        </div>
        <RoundStrip rounds={rounds} />
        <BuyLegend thresholds={overview.economy_thresholds} />
      </section>

      <div className="deeper">
        <span className="muted small">Deeper reads</span>
        <a href={`#/r/${id}/aim`}>Aim detail</a>
        <a href={`#/r/${id}/coach`}>Coach notes</a>
        <a href={`#/r/${id}/judge`}>Judge grades</a>
        <a href={`#/r/${id}/ai`}>Ask AI</a>
        <a href={`#/r/${id}/tables`}>Data tables</a>
      </div>
    </div>
  );
}
