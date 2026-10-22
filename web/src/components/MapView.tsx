import { useEffect, useRef, useState } from "react";
import { api, Agent, TracksPayload, HeatmapPayload, agentIcon } from "../api";
import { fmtTime, shortId } from "../format";

const W = 760;
const BLUE = "#4cc2ff";
const RED = "#ff6060";

// Callout super-regions get fixed colours so the same place reads the same way
// everywhere. Nothing here is derived from replay data - it is only paint.
const REGION_COLORS: Record<string, string> = {
  A: "#ffd166",
  B: "#06d6a0",
  C: "#ef476f",
  Mid: "#8d99ae",
  "Attacker Side": "#f78c6b",
  "Defender Side": "#5bc0eb",
};

function regionColor(superName: string): string {
  return REGION_COLORS[superName] || "#9aa4b2";
}

type Pt = [number, number, number, number, number]; // t, u, v, yaw, speed

function sample(pts: Pt[], t: number): { u: number; v: number; yaw: number; speed: number } | null {
  if (!pts.length) return null;
  const first = pts[0];
  const last = pts[pts.length - 1];
  if (t <= first[0]) return { u: first[1], v: first[2], yaw: first[3], speed: first[4] };
  if (t >= last[0]) return { u: last[1], v: last[2], yaw: last[3], speed: last[4] };
  let lo = 0;
  let hi = pts.length - 1;
  while (lo + 1 < hi) {
    const mid = (lo + hi) >> 1;
    if (pts[mid][0] <= t) lo = mid;
    else hi = mid;
  }
  const a = pts[lo];
  const b = pts[hi];
  const span = b[0] - a[0] || 1;
  const f = (t - a[0]) / span;
  return {
    u: a[1] + (b[1] - a[1]) * f,
    v: a[2] + (b[2] - a[2]) * f,
    yaw: a[3],
    speed: a[4] + (b[4] - a[4]) * f,
  };
}

export default function MapView({ id, agents }: { id: string; agents: Agent[] }) {
  const [data, setData] = useState<TracksPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [round, setRound] = useState(1);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(2);
  const [t, setT] = useState(0);
  const [heatMode, setHeatMode] = useState("off");
  const [heatPlayer, setHeatPlayer] = useState("");
  const [regionMode, setRegionMode] = useState("off");
  const [heat, setHeat] = useState<HeatmapPayload | null>(null);
  const [meta, setMeta] = useState<Record<string, { name: string; team: string | null; icon?: string }>>({});

  const [iconTick, setIconTick] = useState(0);
  const iconsRef = useRef<Record<string, HTMLImageElement>>({});

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const tRef = useRef(0);
  const playingRef = useRef(false);
  const speedRef = useRef(2);
  const lastRef = useRef(0);

  // roster meta (agent name + team) from stats
  useEffect(() => {
    let stop = false;
    api
      .stats(id)
      .then((st) => {
        if (stop) return;
        const m: Record<string, { name: string; team: string | null; icon?: string }> = {};
        for (const p of st.players) {
          const agent = agents.find(
            (a) => a.uuid.toLowerCase() === (p.agent_uuid || "").toLowerCase()
          );
          m[p.subject] = {
            name: agent?.name || shortId(p.subject),
            team: p.team || null,
            icon: agentIcon(p.agent_uuid || undefined),
          };
        }
        setMeta(m);
      })
      .catch(() => {});
    return () => {
      stop = true;
    };
  }, [id, agents]);

  // tracks for current round
  useEffect(() => {
    let stop = false;
    setData(null);
    setErr(null);
    api
      .tracks(id, round)
      .then((d) => {
        if (stop) return;
        setData(d);
        tRef.current = d.start_ms;
        setT(d.start_ms);
        const img = new Image();
        img.onload = () => {
          imgRef.current = img;
          setIconTick((x) => x + 1);
        };
        img.src = d.map?.image || "";
      })
      .catch((e) => !stop && setErr(String(e)));
    return () => {
      stop = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, round]);

  function draw() {
    const canvas = canvasRef.current;
    const d = data;
    if (!canvas || !d) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, W, W);
    const img = imgRef.current;
    if (img && img.complete) ctx.drawImage(img, 0, 0, W, W);
    else {
      ctx.fillStyle = "#141821";
      ctx.fillRect(0, 0, W, W);
    }

    // heatmap layer
    if (heat && heat.cells.length) {
      const cell = W / (heat.grid + 1);
      const mx = heat.max || 1;
      ctx.fillStyle = "#ff5a1e";
      for (const [iu, iv, c] of heat.cells) {
        ctx.globalAlpha = Math.pow(c / mx, 0.5) * 0.5;
        ctx.fillRect(iu * cell, iv * cell, cell + 1, cell + 1);
      }
      ctx.globalAlpha = 1;
    }

    // named regions: the game's own callouts, drawn under the players
    if (regionMode !== "off" && d.regions?.length) {
      ctx.font = "10px ui-monospace, SFMono-Regular, Menlo, monospace";
      ctx.textAlign = "center";
      for (const r of d.regions) {
        const pts = r.poly || [];
        if (regionMode === "cells" && pts.length >= 3) {
          ctx.fillStyle = regionColor(r.super);
          ctx.globalAlpha = 0.16;
          ctx.beginPath();
          pts.forEach(([u, v], i) => (i ? ctx.lineTo(u * W, v * W) : ctx.moveTo(u * W, v * W)));
          ctx.closePath();
          ctx.fill();
          ctx.globalAlpha = 1;
          ctx.strokeStyle = "rgba(255,255,255,0.16)";
          ctx.lineWidth = 1;
          ctx.stroke();
        }
        ctx.fillStyle = "rgba(235,240,248,0.8)";
        ctx.fillText(r.label, r.u * W, r.v * W);
      }
    }

    // kills so far
    for (const k of d.kills) {
      if (k.t > t) continue;
      ctx.strokeStyle = "rgba(255,220,80,0.95)";
      ctx.lineWidth = 2;
      const x = k.u * W;
      const y = k.v * W;
      ctx.beginPath();
      ctx.moveTo(x - 5, y - 5);
      ctx.lineTo(x + 5, y + 5);
      ctx.moveTo(x + 5, y - 5);
      ctx.lineTo(x - 5, y + 5);
      ctx.stroke();
    }

    // players
    const live: { tr: (typeof d.tracks)[number]; x: number; y: number; dx: number; dy: number; color: string }[] = [];
    for (const tr of d.tracks) {
      const s = sample(tr.points as Pt[], t);
      if (!s) continue;
      const x = s.u * W;
      const y = s.v * W;
      const color = tr.team === "Blue" ? BLUE : tr.team === "Red" ? RED : "#cccccc";
      let dx = Math.sin((s.yaw * Math.PI) / 180) * (d.proj?.xm ?? 1);
      let dy = Math.cos((s.yaw * Math.PI) / 180) * (d.proj?.ym ?? 1);
      const len = Math.hypot(dx, dy) || 1;
      dx /= len;
      dy /= len;
      live.push({ tr, x, y, dx, dy, color });
    }
    // field of vision (dark grey cones)
    const R = 100;
    const half = (50 * Math.PI) / 180;
    for (const p of live) {
      const base = Math.atan2(p.dy, p.dx);
      ctx.fillStyle = "rgba(45,50,58,0.45)";
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
      ctx.arc(p.x, p.y, R, base - half, base + half);
      ctx.closePath();
      ctx.fill();
    }
    // players: agent icons (fallback: dot)
    for (const p of live) {
      let ico = iconsRef.current[p.tr.subject];
      if (!ico) {
        const url = meta[p.tr.subject]?.icon || "";
        if (url) {
          const im = new Image();
          im.onload = () => setIconTick((x) => x + 1);
          im.src = url;
          ico = im;
          iconsRef.current[p.tr.subject] = im;
        }
      }
      if (ico && ico.complete && ico.naturalWidth > 0) {
        const r = 11;
        ctx.save();
        ctx.beginPath();
        ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
        ctx.clip();
        ctx.drawImage(ico, p.x - r, p.y - r, r * 2, r * 2);
        ctx.restore();
        ctx.strokeStyle = p.color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
        ctx.stroke();
      } else {
        ctx.fillStyle = p.color;
        ctx.beginPath();
        ctx.arc(p.x, p.y, 5, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = "rgba(0,0,0,0.6)";
        ctx.lineWidth = 1;
        ctx.stroke();
      }
    }
  }

  // heatmap layer fetch
  useEffect(() => {
    let stop = false;
    if (heatMode === "off") {
      setHeat(null);
      return;
    }
    api
      .heatmap(id, { player: heatPlayer || undefined, round, mode: heatMode })
      .then((h) => !stop && setHeat(h))
      .catch(() => {});
    return () => {
      stop = true;
    };
  }, [id, round, heatMode, heatPlayer]);

  // redraw on time / data change
  useEffect(() => {
    draw();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, t, meta, heat, iconTick, regionMode]);

  // playback loop (setInterval — rAF is throttled when the tab is not focused)
  useEffect(() => {
    playingRef.current = playing;
    if (!playing) return;
    lastRef.current = performance.now();
    const timer = window.setInterval(() => {
      if (!playingRef.current) return;
      const now = performance.now();
      const dt = now - lastRef.current;
      lastRef.current = now;
      const d = data;
      let nt = tRef.current + dt * speedRef.current;
      if (d && nt >= d.end_ms) {
        nt = d.end_ms;
        playingRef.current = false;
        setPlaying(false);
      }
      tRef.current = nt;
      setT(nt);
    }, 40);
    return () => window.clearInterval(timer);
  }, [playing, data]);

  useEffect(() => {
    speedRef.current = speed;
  }, [speed]);

  function setTime(v: number) {
    tRef.current = v;
    setT(v);
  }

  if (err) return <div className="page"><div className="panel err">{err}</div></div>;
  if (!data) return <div className="page"><div className="panel muted">Loading map…</div></div>;

  const dur = Math.max(1, data.end_ms - data.start_ms);

  return (
    <div className="stack">
      <div className="panel">
        <div className="row spread">
          <h2>
            {data.map?.name || "Map"} · Round {round}
          </h2>
          <div className="row">
            <select value={round} onChange={(e) => setRound(Number(e.target.value))}>
              {data.rounds.map((r) => (
                <option key={r.round} value={r.round}>
                  Round {r.round}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="row wrap controls">
          <button className="tab" onClick={() => setPlaying((p) => !p)}>
            {playing ? "⏸ pause" : "▶ play"}
          </button>
          <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
            {[1, 2, 4, 8].map((s) => (
              <option key={s} value={s}>
                {s}×
              </option>
            ))}
          </select>
          <select value={heatMode} onChange={(e) => setHeatMode(e.target.value)}>
            <option value="off">heat: off</option>
            <option value="all">heat: all</option>
            <option value="move">heat: move</option>
            <option value="stay">heat: stay</option>
          </select>
          <select value={regionMode} onChange={(e) => setRegionMode(e.target.value)}>
            <option value="off">regions: off</option>
            <option value="labels">regions: names</option>
            <option value="cells">regions: areas</option>
          </select>
          {heatMode !== "off" && (
            <select value={heatPlayer} onChange={(e) => setHeatPlayer(e.target.value)}>
              <option value="">all players</option>
              {data.tracks.map((tr) => (
                <option key={tr.subject} value={tr.subject}>
                  {meta[tr.subject]?.name || shortId(tr.subject)}
                </option>
              ))}
            </select>
          )}
          <input
            type="range"
            className="grow"
            min={data.start_ms}
            max={data.end_ms}
            value={Math.round(t)}
            onChange={(e) => setTime(Number(e.target.value))}
          />
          <span className="mono small muted">
            {fmtTime(t - data.start_ms)} / {fmtTime(dur)}
          </span>
        </div>
        <canvas ref={canvasRef} className="minimap" width={W} height={W} />
        <div className="legend">
          {data.tracks.map((tr) => (
            <div key={tr.subject} className="lgd">
              <span className="swatch" style={{ background: tr.team === "Blue" ? BLUE : RED }} />
              <span className="mono small">{meta[tr.subject]?.name || shortId(tr.subject)}</span>
              <span className="small muted" title={tr.subject}>{shortId(tr.subject)}</span>
              <span className={`badge ${tr.team || ""}`}>{tr.team || "?"}</span>
            </div>
          ))}
        </div>
        <p className="note small muted">
          Each icon is a player at this moment; the shaded wedge is the direction they are facing.
          Yellow crosses mark kills. Scrub the timeline or press play to follow the round.
        </p>
        <details className="map-foot">
          <summary className="small muted">How this is drawn</summary>
          <p className="small muted">
            Positions and facing come from the replay's movement data and kills from its damage
            events. The map is shown flat, with no floors or walls. Place names are Riot's own
            callouts, drawn at the closest named spot rather than as exact room boundaries.
          </p>
        </details>
      </div>
    </div>
  );
}
