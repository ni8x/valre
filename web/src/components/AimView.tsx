import { Fragment, useEffect, useMemo, useState } from "react";
import { api, Agent, MetricsHit, MetricsPayload, Weapon, agentIcon, weaponIcon } from "../api";

import { fmt0, fmt1, fmtTime, pctRatio, shortId, vec3 } from "../format";

type PlayerMeta = { team: string | null; agent_uuid: string | null };

export default function AimView({
  id,
  agents,
  weapons,
}: {
  id: string;
  agents: Agent[];
  weapons: Weapon[];
}) {
  const [data, setData] = useState<MetricsPayload | null>(null);
  const [meta, setMeta] = useState<Record<string, PlayerMeta>>({});
  const [err, setErr] = useState<string | null>(null);

  const [sortKey, setSortKey] = useState("kills");
  const [desc, setDesc] = useState(true);
  const [weaponSort, setWeaponSort] = useState("dmg");
  const [weaponDesc, setWeaponDesc] = useState(true);

  const [sprayWho, setSprayWho] = useState("all");
  const [sprayRound, setSprayRound] = useState("");
  const [openSpray, setOpenSpray] = useState<number | null>(null);

  const [fShooter, setFShooter] = useState("all");
  const [fWeapon, setFWeapon] = useState("");
  const [fRound, setFRound] = useState("");
  const [fHead, setFHead] = useState(false);
  const [fWb, setFWb] = useState(false);
  const [fKill, setFKill] = useState(false);

  useEffect(() => {
    let stop = false;
    setErr(null);
    setData(null);
    api
      .metrics(id)
      .then((d) => {
        if (!stop) setData(d);
      })
      .catch((e) => {
        if (!stop) setErr(String(e));
      });
    api
      .stats(id)
      .then((s) => {
        if (stop) return;
        const m: Record<string, PlayerMeta> = {};
        for (const p of s.players) {
          m[p.subject] = { team: p.team ?? null, agent_uuid: p.agent_uuid ?? null };
        }
        setMeta(m);
      })
      .catch(() => {});
    return () => {
      stop = true;
    };
  }, [id]);

  const agentUuid = (subject: string | null | undefined) => {
    if (!subject) return null;
    return meta[subject]?.agent_uuid ?? null;
  };

  const agentName = (subject: string | null | undefined) => {
    const uuid = agentUuid(subject);
    if (!uuid) return null;
    return agents.find((a) => a.uuid.toLowerCase() === uuid.toLowerCase())?.name ?? null;
  };

  const nameCell = (subject: string | null | undefined) => {
    if (!subject) return <span className="muted">?</span>;
    const uuid = agentUuid(subject);
    return (
      <span className="pcell">
        {uuid && <img className="agent-ico" width={18} height={18} src={agentIcon(uuid)} alt="" />}
        <span className="mono small" title={subject}>{agentName(subject) ?? shortId(subject)}</span>
      </span>
    );
  };

  const weaponUuid = useMemo(() => {
    const m: Record<string, string> = {};
    for (const w of weapons) {
      for (const alias of [w.name, ...w.aliases]) m[alias.toLowerCase()] = w.uuid;
    }
    return m;
  }, [weapons]);

  // Weapon names come from the metrics engine's weapon_name() vocabulary; match the
  // API record through its aliases. Unknown names (ability damage) render text-only.
  const weaponCell = (name: string | null | undefined, opts: { muted?: boolean } = {}) => {
    const uuid = name ? weaponUuid[name.toLowerCase()] : undefined;
    const src = uuid ? weaponIcon(uuid) : undefined;
    return (
      <span className="wcell">
        {src && (
          <img
            className="weapon-ico"
            src={src}
            alt=""
            loading="lazy"
            onError={(e) => {
              e.currentTarget.style.display = "none";
            }}
          />
        )}
        <span className={opts.muted ? "muted" : undefined}>{name ?? "?"}</span>
      </span>
    );
  };

  const sortedPlayers = useMemo(() => {
    if (!data) return [];
    const rows = [...data.players];
    rows.sort((a, b) => {
      const av = (a as unknown as Record<string, unknown>)[sortKey];
      const bv = (b as unknown as Record<string, unknown>)[sortKey];
      const an = typeof av === "number" ? av : -1;
      const bn = typeof bv === "number" ? bv : -1;
      return desc ? bn - an : an - bn;
    });
    return rows;
  }, [data, sortKey, desc]);

  const weaponRows = useMemo(() => {
    if (!data) return [];
    const agg: Record<
      string,
      { shots: number; hits: number; dmg: number; kills: number; head_hits: number }
    > = {};
    for (const p of data.players) {
      for (const [name, w] of Object.entries(p.weapons)) {
        const cur = (agg[name] ??= { shots: 0, hits: 0, dmg: 0, kills: 0, head_hits: 0 });
        cur.shots += w.shots;
        cur.hits += w.hits;
        cur.dmg += w.dmg;
        cur.kills += w.kills;
        cur.head_hits += w.head_hits ?? 0;
      }
    }
    // Re-aggregate the match across players; rates are null when their denominator is zero.
    const rows = Object.entries(agg).map(([name, w]) => ({
      name,
      ...w,
      acc: w.shots > 0 ? w.hits / w.shots : null,
      hs_rate: w.hits > 0 ? w.head_hits / w.hits : null,
      dmg_per_shot: w.shots > 0 ? w.dmg / w.shots : null,
      shots_per_kill: w.kills > 0 ? w.shots / w.kills : null,
    }));
    rows.sort((a, b) => {
      const av = (a as unknown as Record<string, unknown>)[weaponSort];
      const bv = (b as unknown as Record<string, unknown>)[weaponSort];
      const an = typeof av === "number" ? av : -1;
      const bn = typeof bv === "number" ? bv : -1;
      return weaponDesc ? bn - an : an - bn;
    });
    return rows;
  }, [data, weaponSort, weaponDesc]);

  const filteredSprays = useMemo(() => {
    if (!data) return [];
    const r = sprayRound ? parseInt(sprayRound, 10) : null;
    return data.sprays.filter(
      (s) => (sprayWho === "all" || s.p === sprayWho) && (r == null || s.r === r)
    );
  }, [data, sprayWho, sprayRound]);

  const filteredHits = useMemo(() => {
    if (!data) return [];
    const r = fRound ? parseInt(fRound, 10) : null;
    const w = fWeapon.trim().toLowerCase();
    return data.hits.filter((h) => {
      if (fShooter !== "all" && h.shooter !== fShooter) return false;
      if (r != null && h.r !== r) return false;
      if (w && !(h.weapon ?? "").toLowerCase().includes(w)) return false;
      if (fHead && (h.bone ?? "").toLowerCase() !== "head") return false;
      if (fWb && !h.wallbang) return false;
      if (fKill && !h.killed) return false;
      return true;
    });
  }, [data, fShooter, fWeapon, fRound, fHead, fWb, fKill]);

  if (err) {
    return (
      <div className="panel err">
        <h2>Aim metrics</h2>
        <div className="mono small">{err}</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="panel muted">
        <h2>Aim metrics</h2>
        Loading aim metrics… (first load scans the export)
      </div>
    );
  }

  const v = data.validation;
  const totShots = data.players.reduce((s, p) => s + p.shots, 0);
  const totHits = data.players.reduce((s, p) => s + p.hits, 0);
  const totHead = data.players.reduce((s, p) => s + p.head_hits, 0);
  const totUtil = data.players.reduce((s, p) => s + p.util_hits, 0);
  const totUnres = (data.validation?.unresolved_shots ?? 0) + (data.validation?.unresolved_hits ?? 0);
  const overallAcc = totShots > 0 ? totHits / totShots : null;
  const overallHs = totHits > 0 ? totHead / totHits : null;

  const headOf = (key: string, label: string, title?: string) => (
    <th
      key={key}
      className="pointer"
      title={title}
      aria-sort={sortKey === key ? (desc ? "descending" : "ascending") : undefined}
    >
      <button
        type="button"
        className="thbtn"
        onClick={() => {
          if (sortKey === key) setDesc(!desc);
          else {
            setSortKey(key);
            setDesc(true);
          }
        }}
      >
        {label}
        {sortKey === key ? (desc ? " ↓" : " ↑") : ""}
      </button>
    </th>
  );

  return (
    <div className="stack">
      <div className="panel">
        <div className="row spread">
          <h2 style={{ margin: 0 }}>Aim &amp; shots</h2>
          <span className="muted small">
            {data.n_rounds} rounds, metrics v{data.version}
          </span>
        </div>
        <div className="metrics">
          <div><b>{totShots}</b><span>bullets</span></div>
          <div><b>{totHits}</b><span>gun hits</span></div>
          <div><b>{pctRatio(overallAcc)}</b><span>accuracy</span></div>
          <div><b>{totHead}</b><span>head hits</span></div>
          <div><b>{pctRatio(overallHs)}</b><span>headshot rate</span></div>
          <div><b>{totUtil}</b><span>utility hits</span></div>
          <div><b>{data.sprays.length}</b><span>sprays</span></div>
          {totUnres > 0 && (
            <div className="bad"><b>{totUnres}</b><span>unresolved</span></div>
          )}
          <div className={v.kill_feed_unpaired ? "bad" : "good"}>
            <b>{v.kill_feed_pairs}/{v.character_deaths}</b>
            <span>
              kill-feed{v.kill_feed_unpaired ? ` (${v.kill_feed_unpaired} unpaired)` : ""}
            </span>
          </div>
          <div>
            <b>{v.engagements_total ?? 0}</b>
            <span title="contiguous window where an enemy is alive within 3000 cm and 25° of aim (proxy — replays have no visibility data)">
              engagements (proxy)
            </span>
          </div>
          {v.eye_offset_cm != null && (
            <div>
              <b>{fmt0(v.eye_offset_cm)}</b>
              <span title="median (head-hit impact z − victim z) across this match — used as the geometric head height">
                eye height (cm)
              </span>
            </div>
          )}
        </div>
      </div>

      <div className="panel">
        <div className="row spread">
          <h2 style={{ margin: 0 }}>Duels &amp; aim (proxy ~)</h2>
          <span className="muted small">from position / yaw / pitch timeline · hover cells for sample sizes</span>
        </div>
        <div className="table-wrap" style={{ marginTop: 10 }}>
          <table className="data sortable">
            <thead>
              <tr>
                <th>Player</th>
                <th>Team</th>
                {headOf("engagements", "Eng", "contiguous window with an enemy alive within 3000 cm and 25° of aim")}
                {headOf("pre_aim_deg", "Pre-aim°", "angle between aim and the enemy head 100 ms before the first shot (median)")}
                {headOf("pre_aim_complete", "Complete", "share of pre-aims ≤ 3°")}
                {headOf("head_level_pct", "Head lvl", "share of in-view samples with vertical aim error ≤ 1.2° to the enemy head")}
                {headOf("reaction_ms", "React ms", "imminent window (≤ 1100 cm, 16°) start → first shot (median)")}
                {headOf("ttd_ms", "TTD ms", "imminence → first damage dealt to that enemy (median)")}
                {headOf("counter_strafe_pct", "C-strf", "share of shots fired at speed ≤ 100 cm/s")}
                {headOf("first_eng_acc", "1st acc", "share of first shots that hit the enemy within 200 ms")}
                {headOf("spray_radius_cm", "Spray cm", "RMS of impact points around the spray centroid")}
                {headOf("spray_spread_deg", "Sprd°", "mean angle of hits 2–4 vs hit 1 (spray spread)")}
                {headOf("spray_ttk_ms", "TTK ms", "kill time minus spray start (median)")}
                {headOf("hs_kill_pct", "HS-kill", "kills whose final blow was a headshot / gun kills")}
              </tr>
            </thead>
            <tbody>
              {sortedPlayers.map((p) => {
                const team = meta[p.subject]?.team ?? null;
                return (
                  <tr key={p.subject} className={team ? `tm-${team.toLowerCase()}` : ""}>
                    <td>{nameCell(p.subject)}</td>
                    <td>
                      {team ? (
                        <span className={`badge ${team}`}>{team.slice(0, 1)}</span>
                      ) : (
                        <span className="muted">?</span>
                      )}
                    </td>
                    <td>{p.engagements}</td>
                    <td title={`n=${p.pre_aim_n}`}>{fmt1(p.pre_aim_deg)}</td>
                    <td title={`n=${p.pre_aim_n}`}>{pctRatio(p.pre_aim_complete)}</td>
                    <td title={`in-view samples=${p.near_samples}`}>{pctRatio(p.head_level_pct)}</td>
                    <td title={`n=${p.reaction_n}`}>{fmt0(p.reaction_ms)}</td>
                    <td title={`n=${p.ttd_n}`}>{fmt0(p.ttd_ms)}</td>
                    <td title={`shots classified=${p.shots_classified}`}>{pctRatio(p.counter_strafe_pct)}</td>
                    <td title={`n=${p.first_eng_n}`}>{pctRatio(p.first_eng_acc)}</td>
                    <td>{fmt1(p.spray_radius_cm)}</td>
                    <td>{fmt1(p.spray_spread_deg)}</td>
                    <td>{fmt0(p.spray_ttk_ms)}</td>
                    <td>{pctRatio(p.hs_kill_pct)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="muted small" style={{ marginTop: 6 }}>
          ~ proxy metrics: replays carry no visibility data, so “in view” ≈ enemy alive + within 3000 cm + within 25° of aim.
          Engagement = merged in-view window (gaps &lt; 1 s joined). Pre-aim is sampled 100 ms before the first shot of each
          engagement; reaction runs from the imminent window (≤ 1100 cm, 16°) to that first shot; counter-strafe = shots
          fired at ≤ 100 cm/s; TTK = kill minus spray start. Small sample sizes (n) are shown on hover.
        </div>
      </div>

      <div className="panel">
        <h2>Players</h2>
        <div className="table-wrap">
          <table className="data sortable">
            <thead>
              <tr>
                <th>Player</th>
                <th>Team</th>
                {headOf("shots", "Shots")}
                {headOf("hits", "Hits")}
                {headOf("accuracy", "Acc%")}
                {headOf("head_hits", "HS hits")}
                {headOf("hs_pct", "HS%")}
                {headOf("dmg", "Dmg")}
                {headOf("adr_gun", "Gun ADR")}
                {headOf("kills", "Kills")}
                {headOf("sprays", "Sprays")}
                {headOf("spray_acc", "Spray acc")}
                {headOf("util_hits", "Util")}
                {headOf("wallbang_hits", "WB")}
              </tr>
            </thead>
            <tbody>
              {sortedPlayers.map((p) => {
                const team = meta[p.subject]?.team ?? null;
                return (
                  <tr key={p.subject} className={team ? `tm-${team.toLowerCase()}` : ""}>
                    <td>{nameCell(p.subject)}</td>
                    <td>
                      {team ? (
                        <span className={`badge ${team}`}>{team.slice(0, 1)}</span>
                      ) : (
                        <span className="muted">?</span>
                      )}
                    </td>
                    <td>{p.shots}</td>
                    <td>{p.hits}</td>
                    <td>{pctRatio(p.accuracy)}</td>
                    <td>{p.head_hits}</td>
                    <td>{pctRatio(p.hs_pct)}</td>
                    <td>{fmt1(p.dmg)}</td>
                    <td>{fmt1(p.adr_gun)}</td>
                    <td>{p.kills}</td>
                    <td>{p.sprays}</td>
                    <td>{pctRatio(p.spray_acc)}</td>
                    <td>{p.util_hits}</td>
                    <td>{p.wallbang_hits}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="muted small" style={{ marginTop: 6 }}>
          Shots = magazine decrements (no per-shot vectors in the replay). Acc% = gun hits / bullets. HS% = head
          hits / gun hits. Gun ADR excludes utility damage.
        </div>
      </div>

      <div className="panel">
        <h2>Weapons</h2>
        <div className="table-wrap">
          <table className="data sortable">
            <thead>
              <tr>
                <th>Weapon</th>
                {(
                  [
                    ["shots", "Shots", "magazine decrements with this weapon, whole match"],
                    ["hits", "Hits", "gun hits credited to this weapon"],
                    ["acc", "Acc%", "hits / shots with this weapon"],
                    ["head_hits", "HS hits", "hits whose bone is the head"],
                    ["hs_rate", "HS%", "head hits / hits with this weapon"],
                    ["dmg", "Dmg", "damage dealt with this weapon"],
                    ["dmg_per_shot", "Dmg/shot", "damage / shots"],
                    ["kills", "Kills", "kills whose final blow used this weapon"],
                    ["shots_per_kill", "Shots/kill", "shots / kills"],
                  ] as const
                ).map(([k, label, title]) => (
                  <th
                    key={k}
                    className="pointer"
                    title={title}
                    aria-sort={weaponSort === k ? (weaponDesc ? "descending" : "ascending") : undefined}
                  >
                    <button
                      type="button"
                      className="thbtn"
                      onClick={() => {
                        if (weaponSort === k) setWeaponDesc(!weaponDesc);
                        else {
                          setWeaponSort(k);
                          setWeaponDesc(true);
                        }
                      }}
                    >
                      {label}
                      {weaponSort === k ? (weaponDesc ? " ↓" : " ↑") : ""}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {weaponRows.map((w) => (
                <tr key={w.name}>
                  <td>{weaponCell(w.name)}</td>
                  <td title={`${w.shots} shots`}>{w.shots}</td>
                  <td title={`${w.hits} hits of ${w.shots} shots`}>{w.hits}</td>
                  <td>{pctRatio(w.acc)}</td>
                  <td title={w.hits > 0 ? `${w.head_hits} head hits of ${w.hits} hits` : "no hits"}>
                    {w.head_hits}
                  </td>
                  <td>{pctRatio(w.hs_rate)}</td>
                  <td>{fmt1(w.dmg)}</td>
                  <td title={`${w.dmg.toFixed(0)} damage over ${w.shots} shots`}>{fmt1(w.dmg_per_shot)}</td>
                  <td title={`${w.kills} kills`}>{w.kills}</td>
                  <td title={w.kills > 0 ? `${w.shots} shots for ${w.kills} kills` : "no kills"}>
                    {fmt1(w.shots_per_kill)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="muted small" style={{ marginTop: 6 }}>
          Match totals across both teams; hover a cell for its sample. Rates are “—” when the denominator is 0.
          Acc% can exceed 100% for shotguns — one blast counts as one shot but can land several pellet hits.
        </div>
      </div>

      <div className="panel">
        <div className="row spread">
          <h2 style={{ margin: 0 }}>Sprays</h2>
          <div className="row">
            <select value={sprayWho} onChange={(e) => setSprayWho(e.target.value)}>
              <option value="all">all players</option>
              {data.players.map((p) => (
                <option key={p.subject} value={p.subject}>
                  {agentName(p.subject) ?? shortId(p.subject)}
                </option>
              ))}
            </select>
            <input
              style={{ width: 70 }}
              placeholder="round"
              value={sprayRound}
              onChange={(e) => setSprayRound(e.target.value.replace(/[^0-9]/g, ""))}
            />
            <span className="muted small">{filteredSprays.length} sprays</span>
          </div>
        </div>
        <details>
          <summary>Sprays ({filteredSprays.length})</summary>
          <div className="table-wrap tall" style={{ marginTop: 10 }}>
          <table className="data">
            <thead>
              <tr>
                <th>R</th>
                <th>Time</th>
                <th>Player</th>
                <th>Weapon</th>
                <th>Shots</th>
                <th>Hits</th>
                <th>Acc</th>
                <th>Dmg</th>
                <th>Kills</th>
                <th>Duration</th>
              </tr>
            </thead>
            <tbody>
              {filteredSprays.slice(0, 400).map((s, i) => (
                <Fragment key={i}>
                  <tr className="pointer" onClick={() => setOpenSpray(openSpray === i ? null : i)}>
                    <td>
                      <button
                        type="button"
                        className="thbtn"
                        aria-expanded={openSpray === i}
                        aria-label={`Round ${s.r ?? "—"} spray details`}
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenSpray(openSpray === i ? null : i);
                        }}
                      >
                        {s.r ?? "—"}
                      </button>
                    </td>
                    <td>{fmtTime(s.start)}</td>
                    <td>{nameCell(s.p)}</td>
                    <td>{weaponCell(s.w)}</td>
                    <td>{s.shots}</td>
                    <td>{s.hits}</td>
                    <td>{pctRatio(s.shots > 0 ? s.hits / s.shots : null)}</td>
                    <td>{fmt1(s.dmg)}</td>
                    <td>{s.kills}</td>
                    <td className="mono small">{((s.end - s.start) / 1000).toFixed(1)}s</td>
                  </tr>
                  {openSpray === i && (
                    <tr>
                      <td colSpan={10}>
                        <div className="row wrap">
                          {s.ticks.map((t, j) => (
                            <span key={j} className="chip tiny mono">
                              {fmtTime(t)}
                            </span>
                          ))}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
          </div>
        </details>
      </div>

      <div className="panel">
        <div className="row spread">
          <h2 style={{ margin: 0 }}>Hit locations</h2>
          <span className="muted small">{filteredHits.length} of {data.hits.length} hits</span>
        </div>
        <div className="row wrap" style={{ marginTop: 10 }}>
          <select value={fShooter} onChange={(e) => setFShooter(e.target.value)}>
            <option value="all">all shooters</option>
            {data.players.map((p) => (
              <option key={p.subject} value={p.subject}>
                {agentName(p.subject) ?? shortId(p.subject)}
              </option>
            ))}
          </select>
          <input
            placeholder="weapon"
            value={fWeapon}
            onChange={(e) => setFWeapon(e.target.value)}
          />
          <input
            style={{ width: 70 }}
            placeholder="round"
            value={fRound}
            onChange={(e) => setFRound(e.target.value.replace(/[^0-9]/g, ""))}
          />
          <label className="row small pointer">
            <input type="checkbox" checked={fHead} onChange={(e) => setFHead(e.target.checked)} /> head only
          </label>
          <label className="row small pointer">
            <input type="checkbox" checked={fWb} onChange={(e) => setFWb(e.target.checked)} /> wallbang
          </label>
          <label className="row small pointer">
            <input type="checkbox" checked={fKill} onChange={(e) => setFKill(e.target.checked)} /> kills only
          </label>
        </div>
        <details>
          <summary>Hit locations ({filteredHits.length} rows)</summary>
          <div className="table-wrap tall" style={{ marginTop: 10 }}>
          <table className="data">
            <thead>
              <tr>
                <th>Time</th>
                <th>R</th>
                <th>Shooter</th>
                <th>Victim</th>
                <th>Weapon</th>
                <th>Dmg</th>
                <th>Bone</th>
                <th>Region</th>
                <th>WB</th>
                <th>Kill</th>
                <th>Impact (x, y, z) cm</th>
                <th>Origin</th>
              </tr>
            </thead>
            <tbody>
              {filteredHits.slice(0, 400).map((h: MetricsHit, i: number) => (
                <tr key={i}>
                  <td className="mono small">{fmtTime(h.t)}</td>
                  <td>{h.r ?? "—"}</td>
                  <td>{nameCell(h.shooter)}</td>
                  <td>
                    {h.victim_subject ? (
                      nameCell(h.victim_subject)
                    ) : (
                      <span className="mono small muted">#{h.victim}</span>
                    )}
                  </td>
                  <td>{weaponCell(h.weapon, { muted: !h.gun })}</td>
                  <td>{fmt1(h.damage_dealt)}</td>
                  <td>
                    {(h.bone ?? "").toLowerCase() === "head" ? (
                      <span className="chip fb tiny">head</span>
                    ) : (h.bone ?? "—") === "0" ? (
                      <span className="muted">body</span>
                    ) : (
                      h.bone ?? "—"
                    )}
                  </td>
                  <td>{h.region ?? "—"}</td>
                  <td>{h.wallbang ? "✓" : ""}</td>
                  <td>{h.killed ? "☠" : ""}</td>
                  <td className="mono small">{vec3(h.impact)}</td>
                  <td className="mono small muted">{vec3(h.origin)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="note small">
          Bone names and world coordinates come straight from <span className="mono">MulticastNotifyDamage_Point</span>{" "}
          (vrfkit-identical invocation parsing). Region 1 = head, 0 = body. Rows capped at 400 — use the filters to
          narrow down.
          </div>
        </details>
      </div>
    </div>
  );
}
