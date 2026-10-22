import { BUY_ORDER, fmt0 } from "../format";

// Buy classes come from the server's own convention (team average loadout value
// at round start). A round whose export carries no team-state data has no
// economy object at all, so every cell falls back to "unknown" — the strip must
// never invent a plausible buy.
const BUYS = BUY_ORDER as readonly string[];

function buyOf(round: any, side: "Blue" | "Red"): string {
  const b = round?.economy?.[side]?.buy;
  return typeof b === "string" && BUYS.includes(b) ? b : "unknown";
}

/** "Blue full (6.1k)" or "Blue ?" when the export has no loadout reading. */
function sideTag(round: any, side: "Blue" | "Red"): string {
  const alv = round?.economy?.[side]?.alv;
  if (alv == null || !Number.isFinite(alv)) return `${side} ?`;
  return `${side} ${buyOf(round, side)} (${(alv / 1000).toFixed(1)}k)`;
}

/** "R3: Blue full (6.1k) vs Red eco (2.6k) — Blue won" (or "— undecided"). */
function tickLabel(round: any): string {
  const winner =
    round?.winner === "Blue" ? "Blue won" : round?.winner === "Red" ? "Red won" : "undecided";
  return `R${round?.round}: ${sideTag(round, "Blue")} vs ${sideTag(round, "Red")} — ${winner}`;
}

/**
 * One Blue/Red tick pair per round, in round order. Fill = buy class, top edge =
 * winner; a null winner (undecided replay) shows the neutral edge on both ticks.
 * Every tick is a real button with title + aria-label; clicking is display-only.
 */
export function RoundStrip({
  rounds,
  selected,
  onPick,
}: {
  rounds: any[];
  selected?: number | null;
  onPick?: (round: number) => void;
}) {
  return (
    <div className="strip" role="group" aria-label="Rounds: fill is the buy class, top edge is the winner">
      {rounds.map((r: any) => {
        const label = tickLabel(r);
        const blue = `b-${buyOf(r, "Blue")} ${r?.winner === "Blue" ? "w-Blue" : "w-none"}`;
        const red = `b-${buyOf(r, "Red")} ${r?.winner === "Red" ? "w-Red" : "w-none"}`;
        return (
          <div className="rpair" key={r.round}>
            {onPick ? (
              <>
                <button
                  type="button"
                  className={blue}
                  title={label}
                  aria-label={label}
                  aria-current={selected === r.round}
                  onClick={() => onPick(r.round)}
                />
                <button
                  type="button"
                  className={red}
                  title={label}
                  aria-label={label}
                  aria-current={selected === r.round}
                  onClick={() => onPick(r.round)}
                />
              </>
            ) : (
              <>
                <span className={`tick ${blue}`} title={label} aria-label={label} role="img" />
                <span className={`tick ${red}`} title={label} aria-label={label} role="img" />
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}

/**
 * The five buy classes and their colours, plus the loadout-value thresholds that
 * separate them. Thresholds are passed in from `overview.economy_thresholds`
 * when available; the static wording matches the server convention otherwise.
 */
export function BuyLegend({
  thresholds,
}: {
  thresholds?: { eco_max_alv?: number | null; force_max_alv?: number | null } | null;
}) {
  const eco = thresholds?.eco_max_alv;
  const force = thresholds?.force_max_alv;
  const scale =
    eco != null && force != null
      ? `eco < ${fmt0(eco)} · force ${fmt0(eco)}–${fmt0(force)} · full ≥ ${fmt0(force)}`
      : "eco < 3000 · force 3000–5000 · full ≥ 5000";
  return (
    <div className="legend" aria-label="Buy classes">
      {BUY_ORDER.map((b) => (
        <span key={b} className="lgd small muted">
          <span className={`swatch b-${b}`} aria-hidden="true" />
          {b}
        </span>
      ))}
      <span className="lgd small muted">{scale}</span>
    </div>
  );
}
