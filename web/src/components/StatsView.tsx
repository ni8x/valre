import { useEffect, useMemo, useState } from "react";
import { api, Agent, StatsPayload } from "../api";
import { BUY_ORDER, DASH, SOURCE_TIPS, buyCell, buyMix, fmt0, fmt1, fmt2, pct as pct1, shortId, winMix } from "../format";
import { AgentIcon, Bar, agentNameOf } from "./bits";
import { BuyLegend, RoundStrip } from "./RoundStrip";

type SortKey = string;

export default function StatsView({ id, agents }: { id: string; agents: Agent[] }) {
  const [data, setData] = useState<StatsPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [fresh, setFresh] = useState(false);
  const [sort, setSort] = useState<SortKey>("kills");
  const [picked, setPicked] = useState<string | null>(null);
  const [selectedRound, setSelectedRound] = useState<number | null>(null);

  useEffect(() => {
    setData(null);
    api
      .stats(id, fresh)
      .then((d) => {
        setData(d);
        setFresh(false);
      })
      .catch((e) => setErr(String(e)));
  }, [id, fresh]);

  const players = data?.players || [];

  const named = (text: string) => {
    let out = text;
    for (const p of players) out = out.split(shortId(p.subject)).join(label(p.subject));
    return out;
  };
  const label = (subject?: string | null) =>
    agentNameOf(agents, players.find((p) => p.subject === subject)?.agent_uuid) ?? shortId(subject);
  const sorted = useMemo(() => {
    const list = [...players];
    list.sort((a, b) => num(b.stats[sort]) - num(a.stats[sort]));
    return list;
  }, [players, sort]);

  if (err) return <div className="panel err">{err}</div>;
  if (!data) return <div className="panel muted">Computing stats… (first run ~10s)</div>;

  const maxKills = Math.max(1, ...players.map((p) => p.stats.kills || 0));
  const pickedPlayer = players.find((p) => p.subject === picked) || players[0];

  return (
    <div className="stack">
      <section className="panel row spread">
        <div className="row">
          <span className={`chip ${data.overview?.score?.Blue > data.overview?.score?.Red ? "ready" : ""}`}>
            Blue {data.overview?.score?.Blue}
          </span>
          <span className={`chip ${data.overview?.score?.Red > data.overview?.score?.Blue ? "ready" : ""}`}>
            Red {data.overview?.score?.Red}
          </span>
          <span className="muted small">
            {data.overview?.n_rounds} rounds, stats v{data.version}
          </span>
        </div>
        <button className="ghost small" onClick={() => setFresh(true)}>Recompute</button>
      </section>

      {data.insights?.length > 0 && (
        <section className="panel">
          <h2>Insights</h2>
          <ul className="insights">
            {data.insights.map((rec, i) => (
              <li key={i}>
                {named(rec.text)}{" "}
                {rec.detail && <span className="muted small">{named(rec.detail)}</span>}{" "}
                {rec.n != null && <span className="chip tiny" title={rec.kind}>n={rec.n}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="panel">
        <h2>Scoreboard</h2>
        <div className="table-wrap">
          <table className="data sortable">
            <thead>
              <tr>
                <th>Player</th>
                <th>Team</th>
                {[
                  ["kills", "K"],
                  ["deaths", "D"],
                  ["assists", "A"],
                  ["kd", "K/D"],
                  ["acs", "ACS", SOURCE_TIPS.acs],
                  ["adr", "ADR", SOURCE_TIPS.adr],
                  ["hs_pct", "HS%"],
                  ["head_damage_pct", "HeadDmg%"],
                  ["kast_pct", "KAST%", SOURCE_TIPS.kast],
                  ["entry_kills", "EntryK"],
                  ["entry_deaths", "EntryD"],
                  ["traded_pct", "Traded%"],
                  ["isolated_pct", "Iso%"],
                  ["bait_pct", "Bait%"],
                  ["clutch_wins", "Clutch"],
                  ["ults_used", "Ults"],
                  ["ability_casts", "Casts"],
                  ["wallbangs", "WB"],
                  ["score", "Score"],
                ].map(([k, label, tip]) => (
                  <th
                    key={k}
                    className={sort === k ? "sorted" : ""}
                    aria-sort={sort === k ? "descending" : undefined}
                    title={tip ?? "click to sort"}
                  >
                    <button type="button" className="thbtn" onClick={() => setSort(k)}>
                      {label}
                    </button>
                  </th>
                ))}
                <th>Multi</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((p) => {
                const s = p.stats;
                return (
                  <tr key={p.subject} className={p.team === "Blue" ? "tm-blue" : "tm-red"}>
                    <td className="pcell">
                      <AgentIcon uuid={p.agent_uuid} agents={agents} size={26} />
                      <span className="mono small" title={p.subject}>{label(p.subject)}</span>
                    </td>
                    <td><span className={`badge ${p.team}`}>{p.team}</span></td>
                    <td className="mono">
                      {s.kills}
                      <Bar value={s.kills} max={maxKills} color="#ff4655" />
                    </td>
                    <td className="mono" title={s.self_deaths ? SOURCE_TIPS.deaths : undefined}>
                      {s.deaths}
                      {s.self_deaths ? "*" : ""}
                    </td>
                    <td className="mono">{s.assists}</td>
                    <td className="mono">{s.kd}</td>
                    <td className="mono">{s.acs}</td>
                    <td className="mono">{s.adr}</td>
                    <td className="mono">{s.hs_pct}</td>
                    <td className="mono">{s.head_damage_pct}</td>
                    <td className="mono">{s.kast_pct}</td>
                    <td className="mono">{s.entry_kills}</td>
                    <td className="mono">{s.entry_deaths}</td>
                    <td className="mono">{s.traded_pct}</td>
                    <td className="mono">{s.isolated_pct}</td>
                    <td className="mono">{s.bait_pct ?? "—"}</td>
                    <td className="mono">{s.clutch_wins}/{s.clutch_attempts}</td>
                    <td className="mono">{s.ults_used}</td>
                    <td className="mono">{s.ability_casts ?? "—"}</td>
                    <td className="mono">{s.wallbangs}</td>
                    <td className="mono">{s.score}</td>
                    <td className="mono small">
                      {Object.entries(s.multikills || {})
                        .filter(([, v]) => (v as number) > 0)
                        .map(([k, v]) => `${k}k×${v}`)
                        .join(" ") || "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <h2>Attack vs defence</h2>
        <div className="row wrap" style={{ marginBottom: 8 }}>
          {["Blue", "Red"].map((t) => {
            const s = data.overview?.side_rounds?.[t];
            return (
              <span key={t} className="chip">
                {t} · att {s?.attack ?? "—"} / def {s?.defend ?? "—"}
              </span>
            );
          })}
          <span className="muted small">
            sides derived per round (defusal evidence + half-time swap; overtime rounds carry no sides)
          </span>
        </div>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Player</th><th>Team</th>
                <th title="rounds played on attack">Att R</th>
                <th title="kills / deaths / assists on attack">Att K/D/A</th>
                <th title="average damage per attack round">Att ADR</th>
                <th title="share of attack rounds with a kill, assist, survival or trade">Att KAST%</th>
                <th title="rounds played on defence">Def R</th>
                <th title="kills / deaths / assists on defence">Def K/D/A</th>
                <th title="average damage per defence round">Def ADR</th>
                <th title="share of defence rounds with a kill, assist, survival or trade">Def KAST%</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((p) => {
                const a = p.stats_by_side?.attack;
                const d = p.stats_by_side?.defend;
                return (
                  <tr key={p.subject} className={p.team === "Blue" ? "tm-blue" : "tm-red"}>
                    <td className="pcell">
                      <AgentIcon uuid={p.agent_uuid} agents={agents} size={26} />
                      <span className="mono small" title={p.subject}>{label(p.subject)}</span>
                    </td>
                    <td><span className={`badge ${p.team}`}>{p.team}</span></td>
                    <td className="mono">{a?.rounds ?? "—"}</td>
                    <td className="mono">{a ? `${a.kills}/${a.deaths}/${a.assists}` : "—"}</td>
                    <td className="mono">{fmt1(a?.adr)}</td>
                    <td className="mono">{pct1(a?.kast_pct)}</td>
                    <td className="mono">{d?.rounds ?? "—"}</td>
                    <td className="mono">{d ? `${d.kills}/${d.deaths}/${d.assists}` : "—"}</td>
                    <td className="mono">{fmt1(d?.adr)}</td>
                    <td className="mono">{pct1(d?.kast_pct)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="muted small" style={{ marginTop: 6 }}>
          Each round counts for the side its team played. Rounds without a derived side are excluded from both
          blocks, and a rate is “—” (no data) rather than 0 when the side was never played.
        </div>
      </section>

      <section className="panel">
        <h2>Economy</h2>
        <div className="row wrap" style={{ marginBottom: 8 }}>
          <span className="chip" title="buy classes are this project's own convention from team loadout value">
            buy: eco &lt; {fmt0(data.overview?.economy_thresholds?.eco_max_alv)} · force &lt;{" "}
            {fmt0(data.overview?.economy_thresholds?.force_max_alv)} · full ≥{" "}
            {fmt0(data.overview?.economy_thresholds?.force_max_alv)}
          </span>
          {["Blue", "Red"].map((t) => {
            const e = data.overview?.economy?.[t];
            return (
              <span key={t} className="chip">
                {t} avg gear {fmt0(e?.avg_alv)} {e?.avg_alv != null ? "¢" : ""}
                {e?.buy_mix ? ` · ${buyMix(e.buy_mix)}` : ""}
              </span>
            );
          })}
          <span className="muted small">
            loadout value is Riot's per-player gear+abilities accounting, averaged over the team
          </span>
        </div>
        <RoundStrip
          rounds={data.rounds || []}
          selected={selectedRound}
          onPick={setSelectedRound}
        />
        <div className="muted small" style={{ marginTop: 6 }}>
          One Blue/Red pair per round in order: the fill is each team's buy class and the top edge is the
          winner. Click a tick to highlight that round's row below — the highlight is display-only.
        </div>
        <BuyLegend thresholds={data.overview?.economy_thresholds} />
        <div className="table-wrap" style={{ marginTop: 10 }}>
          <table className="data">
            <thead>
              <tr>
                <th>Player</th><th>Team</th>
                <th title="rounds the player's team played">Rounds</th>
                <th title="start-of-round credits (MoneyManagementComponent)">Avg start ¢</th>
                <th title="team average loadout value, the team's figure not the player's own gear">Team ALV</th>
                <th title="rounds the player's team played in each buy class">Buy mix</th>
                <th title="rounds won per buy class for this team">Team win% by buy</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((p) => {
                const e = p.economy;
                const ov = data.overview?.economy?.[p.team ?? ""];
                return (
                  <tr key={p.subject} className={p.team === "Blue" ? "tm-blue" : "tm-red"}>
                    <td className="pcell">
                      <AgentIcon uuid={p.agent_uuid} agents={agents} size={26} />
                      <span className="mono small" title={p.subject}>{label(p.subject)}</span>
                    </td>
                    <td><span className={`badge ${p.team}`}>{p.team}</span></td>
                    <td className="mono">{e?.rounds ?? "—"}</td>
                    <td className="mono" title={`rounds with a money reading: ${e?.rounds_with_money ?? 0}`}>
                      {fmt0(e?.avg_start_money)}
                    </td>
                    <td className="mono">{fmt0(e?.team_avg_loadout_value)}</td>
                    <td className="mono small">{e?.buy_mix ? buyMix(e.buy_mix) : "—"}</td>
                    <td className="mono small">{ov?.win_pct_by_buy ? winMix(ov.win_pct_by_buy) : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <details>
          <summary>Round buys ({(data.rounds || []).length})</summary>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr><th>R</th><th>Blue buy</th><th>Red buy</th><th>Winner</th></tr>
              </thead>
              <tbody>
                {(data.rounds || []).map((r: any) => (
                  <tr key={r.round} className={selectedRound === r.round ? "hl" : ""}>
                    <td className="mono">{r.round}</td>
                    <td className="mono small">{buyCell(r.economy?.Blue)}</td>
                    <td className="mono small">{buyCell(r.economy?.Red)}</td>
                    <td><span className={`badge ${r.winner || ""}`}>{r.winner || DASH}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
        <div className="muted small" style={{ marginTop: 6 }}>
          Buy class comes from each team's average loadout value at round start; a round with no loadout-value
          reading shows unknown. No “credits spent” figure is derived — start-minus-end money mixes grants,
          rewards, refunds and purchases.
        </div>
      </section>

      <section className="panel">
        <h2>Opening duels &amp; consistency</h2>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Player</th><th>Team</th>
                <th title="rounds where the player was the killer or the victim of the round's first death">Duels</th>
                <th title="opening duels won (first kill of the round)">Wins</th>
                <th title="opening-duel win share">Win%</th>
                <th title="rounds where the player was the first to die">1st deaths</th>
                <th title="mean per-round index (kills×2 + damage/200), own scale not an industry rating">Rating</th>
                <th title="population standard deviation of the per-round index">± SD</th>
                <th>Median</th>
                <th>Best</th>
                <th>Worst</th>
                <th title="rounds with at least one kill and no death">Clean</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((p) => {
                const e = p.entry;
                const r = p.rating_by_round;
                return (
                  <tr key={p.subject} className={p.team === "Blue" ? "tm-blue" : "tm-red"}>
                    <td className="pcell">
                      <AgentIcon uuid={p.agent_uuid} agents={agents} size={26} />
                      <span className="mono small" title={p.subject}>{label(p.subject)}</span>
                    </td>
                    <td><span className={`badge ${p.team}`}>{p.team}</span></td>
                    <td className="mono">{e?.duels ?? "—"}</td>
                    <td className="mono">{e?.wins ?? "—"}</td>
                    <td className="mono" title={e ? `${e.wins} of ${e.duels}` : "no opening duels"}>
                      {pct1(e?.win_pct, 0)}
                    </td>
                    <td className="mono">{e?.first_deaths ?? "—"}</td>
                    <td className="mono" title={`n=${r?.n ?? 0} rounds`}>{fmt2(r?.mean)}</td>
                    <td className="mono">{fmt2(r?.stdev)}</td>
                    <td className="mono">{fmt2(r?.median)}</td>
                    <td className="mono">{fmt2(r?.best)}</td>
                    <td className="mono">{fmt2(r?.worst)}</td>
                    <td className="mono">{p.clean_rounds ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="muted small" style={{ marginTop: 6 }}>
          Rating here is this project's own transparent index (kills×2 + damage/200 per round), not an industry
          rating — read it with the sample size (n = rounds) and the spread, not as a verdict. A first death with
          no recorded killer counts against the victim but credits no win.
        </div>
      </section>

      <section className="panel">
        <h2>Round by round</h2>
        <div className="row wrap">
          {players.map((p) => (
            <button
              key={p.subject}
              className={pickedPlayer?.subject === p.subject ? "btn small" : "btn ghost small"}
              aria-pressed={pickedPlayer?.subject === p.subject}
              onClick={() => setPicked(p.subject)}
            >
              {label(p.subject)} ({p.team})
            </button>
          ))}
        </div>
        {pickedPlayer && (
          <details open>
            <summary>Per-round table ({(pickedPlayer.per_round || []).length} rounds)</summary>
            {pickedPlayer.stats?.self_damage > 0 && (
              <div className="muted small" style={{ marginTop: 6 }}>
                {fmt1(pickedPlayer.stats.self_damage)} of this player's damage was self-inflicted (own
                utility, or a spike detonation) and is excluded from ADR.
              </div>
            )}
            <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>R</th><th>K</th><th>D</th><th title={SOURCE_TIPS.selfDamage}>DMG</th><th>Money</th>
                  <th>FB</th><th>FD</th><th>Traded</th><th>Clutch</th>
                </tr>
              </thead>
              <tbody>
                {pickedPlayer.per_round.map((r) => (
                  <tr key={r.round} className={r.clutch ? "hl" : ""}>
                    <td className="mono">{r.round}</td>
                    <td className="mono">{r.kills}</td>
                    <td className="mono">{r.deaths}</td>
                    <td className="mono">{r.damage}</td>
                    <td className="mono">{r.money ?? "—"}</td>
                    <td>{r.first_blood ? "✓" : ""}</td>
                    <td>{r.first_death ? "✓" : ""}</td>
                    <td>{r.traded ? "✓" : ""}</td>
                    <td>{r.clutch ? "★" : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          </details>
        )}
      </section>

      {data.rounds?.length > 0 && (
        <section className="panel">
          <h2>Round timeline</h2>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr><th>R</th><th>Winner</th><th>Kills</th><th>Spikes</th><th>Timeline</th></tr>
              </thead>
              <tbody>
                {data.rounds.map((r: any) => (
                  <tr key={r.round}>
                    <td className="mono">{r.round}</td>
                    <td><span className={`badge ${r.winner || ""}`}>{r.winner || DASH}</span></td>
                    <td className="mono">{r.deaths?.length ?? 0}</td>
                    <td className="mono small">
                      {(r.spikes || []).map((s: any) => `${s.kind}@${(s.t / 1000).toFixed(0)}s`).join(", ") || "—"}
                    </td>
                    <td className="small">
                      {(r.deaths || []).map((d: any, i: number) => (
                        <span key={i} className={d.suicide ? "mono muted" : "mono"}>
                          {d.suicide ? `${label(d.victim)} (self)` : `${label(d.killer)}→${label(d.victim)}`}
                          {d.first_blood && <b className="fb"> FB</b>}
                          {i < r.deaths.length - 1 ? "  ·  " : ""}
                        </span>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="panel">
        <h2>Note</h2>
        <ul className="muted small">
          {(data.notes || []).map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
        {data.validation && (
          <details>
            <summary>Raw validation</summary>
            <pre className="log">{JSON.stringify(data.validation, null, 2)}</pre>
          </details>
        )}
      </section>
    </div>
  );
}

function num(v: any) {
  const n = typeof v === "number" ? v : parseFloat(v);
  return Number.isFinite(n) ? n : -Infinity;
}
