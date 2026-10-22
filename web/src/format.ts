// The one place raw payload values become display strings.
//
// Rule: a missing value never looks like a measured zero. null/undefined render
// as "—" (DASH); a real 0 renders as "0". Every helper keeps that split.
//
// There are two percentage conventions in the payloads, so there are two
// helpers and they are not interchangeable:
//   pct(v)      v is 0-100 (stats.py: kast_pct, hs_pct, win_pct, ...)
//   pctRatio(v) v is 0-1   (metrics.py: accuracy, hs_pct, spray_acc, acc, ...)

export const DASH = "—";

/** Fixed-point number; "—" when missing or not finite. */
export function num(v: number | null | undefined, digits = 0): string {
  return v == null || !Number.isFinite(v) ? DASH : v.toFixed(digits);
}

export const fmt0 = (v: number | null | undefined) => num(v, 0);
export const fmt1 = (v: number | null | undefined) => num(v, 1);
export const fmt2 = (v: number | null | undefined) => num(v, 2);

/** v is a 0-100 percentage. */
export function pct(v: number | null | undefined, digits = 1): string {
  return v == null || !Number.isFinite(v) ? DASH : `${v.toFixed(digits)}%`;
}

/** v is a 0-1 ratio (metrics.py convention). Values above 1 are real (shotguns). */
export function pctRatio(v: number | null | undefined, digits = 1): string {
  return v == null || !Number.isFinite(v) ? DASH : `${(v * 100).toFixed(digits)}%`;
}

/** Milliseconds to m:ss, clamped at zero. */
export function fmtTime(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return DASH;
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Milliseconds to "12m 34s". */
export function fmtDuration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms <= 0) return DASH;
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

/** Subject ids are long guids; eight characters are enough to tell players apart. */
export function shortId(subject: string | null | undefined): string {
  return subject ? subject.slice(0, 8) : DASH;
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** True for the manifest's GUID friendly_name, which is never a useful title. */
export function isUuid(value: string | null | undefined): boolean {
  return !!value && UUID_RE.test(value.trim());
}

/** .NET ticks (UTC) to Unix milliseconds. */
export function ticksToMs(ticks: number | null | undefined): number | null {
  if (ticks == null || !Number.isFinite(ticks)) return null;
  return ticks / 10000 - 62135596800000;
}

function toMs(value: number | string | null | undefined): number | null {
  if (value == null) return null;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

/** "18 Oct 2026". Prefers the replay's own timestamp, falls back to import time. */
export function fmtMatchDate(
  ticks: number | null | undefined,
  fallback?: number | string | null,
): string {
  const ms = ticksToMs(ticks) ?? toMs(fallback);
  if (ms == null) return DASH;
  return new Date(ms).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** A replay title a person can read: never a manifest GUID. */
export function replayName(
  title: string | null | undefined,
  mapName: string | null | undefined,
): string {
  const clean = (title || "").trim();
  if (clean && !isUuid(clean)) return clean;
  return mapName ? `${mapName} replay` : "Untitled replay";
}

/** Team name to the css class used by player cards and round panels. */
export function teamClass(team: string | null | undefined): string {
  return team === "Blue" ? "tm-blue" : team === "Red" ? "tm-red" : "";
}

export function vec3(v: number[] | null | undefined): string {
  return v && v.length >= 3
    ? `${v[0].toFixed(0)}, ${v[1].toFixed(0)}, ${v[2].toFixed(0)}`
    : DASH;
}

// Round economy. The buy classes come from the server's own convention
// (team average loadout value at round start, see stats.py).
export const BUY_ORDER = ["pistol", "eco", "force", "full", "unknown"] as const;

/** "full 11 · force 6", only the classes the team actually played. */
export function buyMix(mix: Record<string, number> | null | undefined): string {
  if (!mix) return DASH;
  return (
    BUY_ORDER.filter((k) => (mix[k] ?? 0) > 0)
      .map((k) => `${k} ${mix[k]}`)
      .join(" · ") || DASH
  );
}

/** "fu 64% · fo 60%", the win share per buy class. */
export function winMix(wins: Record<string, number | null> | null | undefined): string {
  if (!wins) return DASH;
  return (
    BUY_ORDER.filter((k) => wins[k] != null)
      .map((k) => `${k.slice(0, 2)} ${wins[k]!.toFixed(0)}%`)
      .join(" · ") || DASH
  );
}

/** "full · 6.1k" so a round's buy and its size are readable at a glance. */
/**
 * Provenance for the numbers whose definition needs saying out loud. Kept here so the
 * scoreboard, the overview and any future surface say the same thing.
 */
export const SOURCE_TIPS = {
  acs: "The game's own combat score (Riot's PlayerScoreComponent), divided by rounds.",
  adr: "Damage dealt to opponents per round. Matches the OP.GG report for the reference match.",
  kast:
    "Kills, assists, survived or traded, per round. The reference report counts one round " +
    "fewer for one player, so the two are a percent apart.",
  deaths:
    "Includes self-inflicted deaths (your own utility, or a spike detonation). The " +
    "reference report for this replay counts one fewer.",
  selfDamage: "Raw region damage for the round, including any damage the player dealt to themselves.",
} as const;

export function buyCell(
  cell: { buy?: string | null; alv?: number | null } | null | undefined,
): string {
  if (!cell) return DASH;
  const alv = cell.alv;
  return alv == null ? cell.buy || DASH : `${cell.buy || "?"} · ${(alv / 1000).toFixed(1)}k`;
}
