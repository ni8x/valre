import { useEffect, useState } from "react";
import { api, PairDeathRow, PairMatchRow, PairPayload, PairSide, shortLevel } from "../api";

type Props = { me: string; partner: string; go: (path: string) => void };

// The bands the server publishes, in the order the argument reads best.
const BAND_ORDER = ["<=12m", "12-25m", ">25m", "unknown"];

const BAND_LABEL: Record<string, string> = {
  "<=12m": "Within 12 m",
  "12-25m": "12–25 m away",
  ">25m": "Over 25 m away",
  unknown: "No position read",
};

const msg = (e: unknown) => (e instanceof Error ? e.message : String(e));

function num(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined ? "—" : v.toFixed(digits);
}

function int(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : String(v);
}

function pctOf(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined ? "—" : `${v.toFixed(digits)}%`;
}

function mmss(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  const total = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function when(ms: number | null | undefined): string {
  return ms ? new Date(ms).toLocaleString() : "—";
}

function shortId(id: string | null | undefined): string {
  return id ? id.slice(0, 8) : "—";
}

function who(label: string | null | undefined, subject: string): string {
  return label || `player ${shortId(subject)}`;
}

function move(v: PairDeathRow["partner_move"]): string {
  if (v === "up") return "moving up";
  if (v === "back") return "backing off";
  if (v === "static") return "static";
  return "—";
}

function region(r: PairDeathRow["my_region"]): string {
  if (!r) return "—";
  return `${r.label} (${num(r.cm / 100)} m off)`;
}

function arrival(v: number | null | undefined): string {
  if (v === null || v === undefined) return "never came";
  if (v === 0) return "already there";
  return `came at +${mmss(v)}`;
}

function track(row: PairDeathRow): string {
  const pts = row.partner_track || [];
  if (!pts.length) return "—";
  return pts.map((p) => num(p.distance_m)).join(" → ");
}

function wonChip(v: boolean | null | undefined) {
  if (v === null || v === undefined) return <span className="chip">no winner</span>;
  return <span className={`chip ${v ? "ready" : "warn"}`}>{v ? "won" : "lost"}</span>;
}

function SideRow({ label, side }: { label: string; side: PairSide }) {
  return (
    <tr>
      <td>{label}</td>
      <td>{int(side.rounds)}</td>
      <td>{int(side.kills)}</td>
      <td>{int(side.deaths)}</td>
      <td>{int(side.assists)}</td>
      <td>{num(side.adr)}</td>
      <td>{num(side.kd, 2)}</td>
      <td>{pctOf(side.kast_pct)}</td>
    </tr>
  );
}

function DeathTable({
  rows,
  mine,
  levels,
}: {
  rows: PairDeathRow[];
  mine: boolean;
  levels: Map<string, string | null>;
}) {
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>Match</th>
            <th>Round</th>
            <th>Into round</th>
            <th>Distance</th>
            <th>Where</th>
            <th>{mine ? "He was" : "I was"}</th>
            <th>{mine ? "His move" : "My move"}</th>
            <th>{mine ? "His track (4s→death)" : "My track (4s→death)"}</th>
            <th>Closest</th>
            <th>{mine ? "Did he come" : "Did I come"}</th>
            <th>{mine ? "His damage" : "My damage"}</th>
            <th>{mine ? "Killed my killer" : "I killed his killer"}</th>
            <th>Trade</th>
            <th>Alone</th>
            <th>Result</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={15} className="muted">
                No shared-round deaths.
              </td>
            </tr>
          )}
          {rows.map((d, i) => (
            <tr key={`${d.match_id}-${d.round}-${d.t}-${i}`}>
              <td>{levels.get(d.match_id || "") || shortId(d.match_id)}</td>
              <td>
                {int(d.round)} <span className="muted small">{d.suicide ? "self" : ""}</span>
              </td>
              <td>{mmss((d.t_into_round_s ?? 0) * 1000)}</td>
              <td>{d.distance_m === null ? "—" : `${num(d.distance_m)} m`}</td>
              <td className="small">
                {mine ? "I" : "he"} {region(d.my_region)}
                <span className="muted small">
                  {" · "}
                  {mine ? "he" : "I"} {region(d.mate_region)}
                </span>
              </td>
              <td>
                {d.partner_forward_m === null ? "—" : `${num(d.partner_forward_m)} m`}
                <span className="muted small">
                  {d.partner_forward_m === null ? "" : d.partner_forward_m < 0 ? " behind" : " ahead"}
                </span>
              </td>
              <td>{move(d.partner_move)}</td>
              <td className="mono small">{track(d)}</td>
              <td>
                {d.partner_closest_cm === null ? "—" : `${num(d.partner_closest_cm / 100, 1)} m`}
                <span className="muted small">
                  {d.partner_closest_at_ms === null ? "" : ` at +${mmss(d.partner_closest_at_ms)}`}
                </span>
              </td>
              <td>{arrival(d.partner_arrived_after_ms)}</td>
              <td>{num(d.partner_damage_that_round)}</td>
              <td>{d.partner_killed_my_killer ? `yes (+${mmss(d.partner_killed_my_killer_at_ms)})` : "no"}</td>
              <td>
                <span className={`chip ${d.traded ? "ready" : "warn"}`}>{d.traded ? "traded" : "untraded"}</span>
              </td>
              <td>
                <span className={`chip ${d.isolated === false ? "ready" : d.isolated === true ? "warn" : ""}`}>
                  {d.isolated === null || d.isolated === undefined ? "—" : d.isolated ? "alone" : "with him"}
                </span>
              </td>
              <td>{wonChip(d.round_won)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MatchTable({ rows }: { rows: PairMatchRow[] }) {
  const shared = rows.filter((m) => m.rounds_together > 0);
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>Match</th>
            <th>Team</th>
            <th>Rounds together</th>
            <th>W–L</th>
            <th>Win rate</th>
            <th>My deaths</th>
            <th>His deaths</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {shared.length === 0 && (
            <tr>
              <td colSpan={8} className="muted">
                No shared matches.
              </td>
            </tr>
          )}
          {shared.map((m) => (
            <tr key={m.match_id}>
              <td>
                <a href={`#/r/${m.replay_id}/match`}>{shortLevel(m.level_name) || shortId(m.match_id)}</a>
              </td>
              <td>{m.team || "—"}</td>
              <td>
                {int(m.rounds_together)}
                {m.rounds_without_winner ? (
                  <span className="muted small"> (+{m.rounds_without_winner} no winner)</span>
                ) : null}
              </td>
              <td>
                {int(m.wins)}–{int(m.losses)}
              </td>
              <td>{pctOf(m.win_pct)}</td>
              <td>{int(m.my_deaths)}</td>
              <td>{int(m.his_deaths)}</td>
              <td className="muted small">{m.why || ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function PairView({ me, partner, go }: Props) {
  const [data, setData] = useState<PairPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    setErr(null);
    api
      .pair(me, partner)
      .then((p) => {
        if (alive) setData(p);
      })
      .catch((e) => {
        if (alive) setErr(msg(e));
      });
    return () => {
      alive = false;
    };
  }, [me, partner]);

  if (err) return <div className="page"><div className="panel err">{err}</div></div>;
  if (!data)
    return (
      <div className="page">
        <div className="panel muted">Reading the two of you out of every replay…</div>
      </div>
    );

  const mine = data.trades.mine;
  const his = data.trades.his;
  const untradedAlive = (data.deaths || []).filter((d) => !d.traded && d.mate_alive);
  const byBand = new Map<string, number>();
  for (const d of untradedAlive) byBand.set(d.distance_band, (byBand.get(d.distance_band) || 0) + 1);
  const levels = new Map((data.matches || []).map((m) => [m.match_id, shortLevel(m.level_name)]));

  const meName = who(data.me.label, data.me.subject);
  const partnerName = who(data.partner.label, data.partner.subject);
  const decided = data.pair.wins + data.pair.losses;
  const close = byBand.get("<=12m") || 0;
  const sharedMatches = (data.matches || []).filter((m) => m.rounds_together > 0);
  const onlyOne = Math.max(0, (data.pair.matches_seen || 0) - sharedMatches.length);

  return (
    <div className="page detail-page stack">
      <div>
        <a className="back-link" href="#/duo">
          ← Duo check
        </a>
        <div className="detail-top player-top">
          <div className="detail-title-block">
            <div className="detail-eyebrow">
              <span>{int(data.pair.matches)} matches together</span>
              <i aria-hidden="true" />
              <span>{int(data.pair.rounds_together)} shared rounds</span>
            </div>
            <h1>
              <a href={`#/p/${encodeURIComponent(data.me.subject)}`}>{meName}</a>
              <span className="muted">with</span>
              <a href={`#/p/${encodeURIComponent(data.partner.subject)}`}>{partnerName}</a>
            </h1>
            {data.pair.why ? (
              <p className="warn small" style={{ margin: "10px 0 0" }}>
                {data.pair.why}
              </p>
            ) : null}
          </div>
          <div className="row wrap player-actions">
            <button className="ghost" onClick={() => go("#/duo")}>
              Pick another pair
            </button>
          </div>
        </div>
      </div>

      <div className="player-band" aria-label="The pair's record">
        <div>
          <b>{int(data.pair.matches)}</b>
          <span>Matches</span>
          <small>played on the same team</small>
        </div>
        <div>
          <b>{int(data.pair.rounds_together)}</b>
          <span>Rounds together</span>
          <small>the only rounds counted here</small>
        </div>
        <div>
          <b>
            {int(data.pair.wins)}–{int(data.pair.losses)}
          </b>
          <span>Rounds won</span>
          <small>
            {data.pair.rounds_without_winner
              ? `${data.pair.rounds_without_winner} have no winner in the data`
              : "decided rounds"}
          </small>
        </div>
        <div>
          <b>{pctOf(data.pair.win_pct)}</b>
          <span>Win rate</span>
          <small>
            {int(data.pair.wins)} of {int(decided)} decided
          </small>
        </div>
      </div>

      <div className="panel">
        <h2>When I died</h2>
        <p className="muted small" style={{ marginTop: 0, maxWidth: "72ch" }}>
          Every death of mine in a round we shared. The distance is measured at the moment I died,
          from my position to {partnerName}'s.
        </p>

        <div className="metrics">
          <div>
            <b>{int(mine.total)}</b>
            <span>deaths</span>
          </div>
          <div>
            <b>{int(mine.traded_by_mate)}</b>
            <span>traded by {partnerName}</span>
          </div>
          <div>
            <b>{int(mine.traded_by_anyone)}</b>
            <span>traded by anyone</span>
          </div>
          <div>
            <b>{int(mine.untraded_mate_alive)}</b>
            <span>untraded, {partnerName} alive</span>
          </div>
          <div>
            <b>{int(mine.untraded_mate_dead)}</b>
            <span>untraded, {partnerName} already dead</span>
          </div>
          <div>
            <b>{int(mine.untraded_mate_never_came)}</b>
            <span>untraded, never came</span>
          </div>
        </div>

        <h3 className="pair-subhead">Untraded deaths with {partnerName} alive</h3>
        {untradedAlive.length ? (
          <>
            <p className="muted small" style={{ margin: "0 0 12px", maxWidth: "72ch" }}>
              {partnerName} was within 12 m in {close} of {untradedAlive.length}.
            </p>
            <div className="band-list">
              {BAND_ORDER.map((b) => {
                const n = byBand.get(b) || 0;
                const share = (100 * n) / untradedAlive.length;
                return (
                  <div className="band-row" key={b}>
                    <span className="band-name">{BAND_LABEL[b]}</span>
                    <span className="band-track" aria-hidden="true">
                      <i style={{ width: `${share}%` }} />
                    </span>
                    <b>{n}</b>
                    <span className="muted small">{share.toFixed(0)}%</span>
                  </div>
                );
              })}
            </div>
          </>
        ) : (
          <p className="muted small">No untraded deaths with {partnerName} alive.</p>
        )}

        <p className="muted small pair-flip">
          Read the other way: {his.total} of {partnerName}'s deaths came in shared rounds,{" "}
          {his.untraded_mate_alive} untraded with me alive and {his.traded_by_mate} traded by me.
        </p>
      </div>

      <details className="more-panel">
        <summary>
          <span>Every death of mine, read from {partnerName}'s side</span>
          <span className="muted small">
            {data.deaths.length} deaths · distance, region, movement, trade
          </span>
        </summary>
        <DeathTable rows={data.deaths || []} mine levels={levels} />
      </details>

      <details className="more-panel">
        <summary>
          <span>Every death of {partnerName}'s, read from my side</span>
          <span className="muted small">{data.his_deaths.length} deaths · the same read, reversed</span>
        </summary>
        <DeathTable rows={data.his_deaths || []} mine={false} levels={levels} />
      </details>

      <div className="panel">
        <h2>Opening duels</h2>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Rounds</th>
                <th>I took first blood</th>
                <th>{partnerName} took first blood</th>
                <th>I died first</th>
                <th>{partnerName} died first</th>
                <th>First death traded by me</th>
                <th>First death traded by {partnerName}</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>{int(data.contacts.rounds)}</td>
                <td>{int(data.contacts.mine_first_blood)}</td>
                <td>{int(data.contacts.his_first_blood)}</td>
                <td>{int(data.contacts.mine_first_death)}</td>
                <td>{int(data.contacts.his_first_death)}</td>
                <td>{int(data.contacts.first_traded_by_me)}</td>
                <td>{int(data.contacts.first_traded_by_him)}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <h2>Together</h2>
        <p className="muted small" style={{ marginTop: 0 }}>
          Both of you, over the rounds you played together.
        </p>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Who</th>
                <th>Rounds</th>
                <th>K</th>
                <th>D</th>
                <th>A</th>
                <th>ADR</th>
                <th>K / D</th>
                <th>KAST</th>
              </tr>
            </thead>
            <tbody>
              <SideRow label="The two of us" side={data.combined} />
              <SideRow label={meName} side={data.combined.me} />
              <SideRow label={partnerName} side={data.combined.partner} />
            </tbody>
          </table>
        </div>
        <p className="muted small" style={{ margin: "12px 0 0", maxWidth: "72ch" }}>
          {data.combined.n_note}
        </p>
      </div>

      <div className="panel">
        <h2>Match by match</h2>
        <MatchTable rows={data.matches || []} />
        <p className="muted small" style={{ margin: "12px 0 0", maxWidth: "72ch" }}>
          {sharedMatches.length} shared {sharedMatches.length === 1 ? "match" : "matches"}
          {onlyOne
            ? `; ${onlyOne} other ${onlyOne === 1 ? "replay holds" : "replays hold"} only one of you`
            : ""}
          .
        </p>
        {data.skipped && data.skipped.length > 0 ? (
          <ul className="checks">
            {data.skipped.map((s) => (
              <li key={s.replay_id} className="warn">
                {shortId(s.replay_id)}: {s.why}
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      <details className="more-panel">
        <summary>
          <span>How this is read</span>
          <span className="muted small">The counting rules, and what these numbers cannot say</span>
        </summary>
        <p className="small muted" style={{ marginTop: 14 }}>
          {data.trades.rule}. {data.trades.range_rule}.
        </p>
        <ul className="notes">
          {(data.notes || []).map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
        <h4>What this cannot say</h4>
        <ul className="notes">
          {(data.limits || []).map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
        <p className="small muted">
          Computed on demand in {num(data.seconds, 2)} s, {when(data.generated_at)}. Nothing here is
          cached, so a new replay shows up on the next load.
        </p>
      </details>
    </div>
  );
}
