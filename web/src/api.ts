export type PlayerSummary = {
  subject: string;
  actor_net_guid: number;
  character_net_guid: number;
  all_character_net_guids: number[];
  agent_uuid: string | null;
  loadout?: unknown;
};

export type ReplayRow = {
  id: string;
  title: string | null;
  custom_title: boolean;
  original_name: string | null;
  status: string | null;
  error: string | null;
  created_at: number | string | null;
  timestamp_ticks: number | null;
  duration_ms: number | null;
  level_name: string | null;
  build: string | null;
  score: { Blue?: number; Red?: number } | null;
  n_rounds: number | null;
  player_count: number;
  players: PlayerSummary[] | null;
};

export type ReplayRecord = {
  id: string;
  original_name?: string;
  title?: string | null;
  custom_title?: boolean;
  status?: string;
  error?: string | null;
  created_at?: number | string;
  updated_at?: number | string;
  summary?: any;
};

export type LocalNames = { me: string | null; names: Record<string, string> };

export type StatusPayload = {
  id: string;
  status: string;
  phase?: string | null;
  error?: string | null;
  lines: string[];
  summary?: any;
};

export type TableInfo = {
  name: string;
  label: string;
  desc: string;
  rows: number;
  exists: boolean;
  checkpoint: boolean;
};

export type ColumnInfo = { name: string; type: string; hidden: boolean };

export type TablePage = {
  rows: Record<string, unknown>[];
  offset: number;
  limit: number;
  total: number;
  columns: ColumnInfo[];
};

export type Agent = {
  uuid: string;
  name: string;
  icon: string | null;
  bust: string | null;
  role: string | null;
  roleIcon: string | null;
  abilities: { slot: string | null; name: string | null; icon: string | null }[];
};

export type MapInfo = {
  uuid: string;
  name: string;
  mapUrl: string | null;
  listIcon: string | null;
  splash: string | null;
  icon: string | null;
};

// A purchasable weapon from valorant-api.com. `aliases` are the export's names for
// the same gun (metrics use "Compact Pistol" where the API says "Bandit"), so the
// UI matches metric weapon names against `[name, ...aliases]`. Chamber's ability
// weapons (Headhunter, Tour de Force) are not in /v1/weapons and simply have no icon.
export type Weapon = {
  uuid: string;
  name: string;
  category: string | null;
  icon: string | null;
  killIcon: string | null;
  aliases: string[];
};

// Shared block shape for the attack/defence and buy-class splits.
export type StatBlock = {
  rounds: number;
  kills: number;
  deaths: number;
  assists: number;
  damage: number;
  kast_rounds: number;
  hs_kills: number;
  entry_kills: number;
  entry_deaths: number;
  kd: number | null;
  adr: number | null;
  kast_pct: number | null;
  hs_pct: number | null;
};

export type SideSplit = { attack: StatBlock; defend: StatBlock };

export type PlayerEconomy = {
  rounds: number;
  rounds_with_money: number;
  avg_start_money: number | null;
  rounds_with_loadout_value: number;
  team_avg_loadout_value: number | null;
  buy_mix: Record<string, number>;
};

export type PlayerEntry = {
  duels: number;
  wins: number;
  first_deaths: number;
  win_pct: number | null;
};

export type RoundRating = {
  mean: number | null;
  stdev: number | null;
  median: number | null;
  best: number | null;
  worst: number | null;
  n: number;
};

export type RoundEconomyCell = { alv: number | null; loadout_value: number | null; buy: string };

export type PlayerStats = {
  subject: string;
  team: string | null;
  agent_uuid?: string | null;
  character_net_guid?: number | null;
  stats: Record<string, any>;
  per_round: any[];
  stats_by_side?: SideSplit;
  stats_by_buy?: Record<string, StatBlock>;
  economy?: PlayerEconomy;
  entry?: PlayerEntry;
  rating_by_round?: RoundRating;
  clean_rounds?: number;
};

// One insight record. `text` names players by 8-char subject id (StatsView
// swaps in agent names); `detail` is the supporting number with its sample;
// `n` is the denominator the claim rests on (null for a claim with no rate);
// `kind` is one of kills/aim/entry/survival/clutch/side/economy/consistency.
export type Insight = {
  text: string;
  detail: string | null;
  n: number | null;
  kind: string;
};

export type StatsPayload = {
  version: number;
  replay_id: string;
  generated_at: string;
  overview: any;
  players: PlayerStats[];
  rounds: any[];
  head_to_head: Record<string, Record<string, number>>;
  insights: Insight[];
  notes: string[];
  validation: any;
};

// Per-weapon row. Rates are null when their denominator is zero (no shots / no hits /
// no kills); acc can exceed 1.0 for shotguns (one blast is one shot with many pellet hits).
export type WeaponRow = {
  shots: number;
  hits: number;
  dmg: number;
  kills: number;
  head_hits: number;
  acc: number | null;
  hs_rate: number | null;
  dmg_per_shot: number | null;
  shots_per_kill: number | null;
};

export type MetricsPlayer = {
  subject: string;
  shots: number;
  hits: number;
  gun_hits: number;
  head_hits: number;
  leg_hits: number;
  util_hits: number;
  dmg: number;
  kills: number;
  wallbang_hits: number;
  weapons: Record<string, WeaponRow>;
  sprays: number;
  spray_hits: number;
  accuracy: number;
  hs_pct: number;
  adr_gun: number;
  spray_acc: number;
  engagements: number;
  pre_aim_deg: number | null;
  pre_aim_n: number;
  pre_aim_complete: number | null;
  head_level_pct: number | null;
  near_samples: number;
  reaction_ms: number | null;
  reaction_n: number;
  ttd_ms: number | null;
  ttd_n: number;
  counter_strafe_pct: number | null;
  shots_classified: number;
  first_eng_n: number;
  first_eng_acc: number | null;
  spray_radius_cm: number | null;
  spray_spread_deg: number | null;
  spray_ttk_ms: number | null;
  hs_kill_pct: number | null;
};

export type MetricsVec = number[] | null;

export type MetricsHit = {
  fn: string;
  t: number;
  r: number | null;
  shooter: string | null;
  shooter_pawn: number | null;
  damager_state: number | null;
  credit_state: number | null;
  victim: number;
  victim_subject: string | null;
  character: number | null;
  weapon: string | null;
  wclass: string | null;
  weapon_guid: number | null;
  gun: boolean;
  bone: string | null;
  region: number | null;
  damage_dealt: number | null;
  damage_taken: number | null;
  wallbang: boolean;
  killed: boolean;
  falloff: number | null;
  respawn: number | null;
  net_timestamp: number | null;
  origin: MetricsVec;
  impact: MetricsVec;
  impact_normal: MetricsVec;
  direction: MetricsVec;
};

export type MetricsShot = {
  t: number;
  r: number | null;
  p: string | null;
  pawn: number | null;
  w: string | null;
  wclass: string | null;
  n: number;
};

export type MetricsSpray = {
  p: string | null;
  wclass: string | null;
  w: string | null;
  r: number | null;
  start: number;
  end: number;
  shots: number;
  hits: number;
  dmg: number;
  kills: number;
  ticks: number[];
};

export type MetricsPayload = {
  version: number;
  replay_id: string;
  generated_at: string;
  n_rounds: number;
  players: MetricsPlayer[];
  hits: MetricsHit[];
  shots: MetricsShot[];
  sprays: MetricsSpray[];
  validation: Record<string, number>;
  notes: string[];
};

export type MapRef = { uuid: string; name: string; image: string } | null;
export type MapProj = { xm: number; ym: number; xsa: number; ysa: number } | null;

// One of the game's own callout names (valorant-api.com), projected onto the map
// image with the same transform as the tracks. `poly` is the callout's Voronoi
// cell - the exact pixel area whose nearest centre is this callout - clipped to
// the unit square; empty for a duplicate centre. A name is the *nearest* callout
// centre: an estimate (2D, no floors, no walls), not a boundary.
export type MapRegion = {
  name: string;
  super: string;
  label: string;
  u: number;
  v: number;
  has_box: boolean;
  poly: [number, number][];
};

// Where a point was, in the game's words, plus how far it is from that callout
// centre (the label is the closest named place, not a box).
export type RegionHit = { name: string; super: string; label: string; cm: number };

// [t(ms), u, v, yaw, speed]
export type TrackPoint = [number, number, number, number, number];

export type TracksPayload = {
  map: MapRef;
  proj: MapProj;
  regions: MapRegion[];
  round: number | null;
  start_ms: number;
  end_ms: number;
  rounds: { round: number; start_ms: number; end_ms: number | null }[];
  tracks: { subject: string; pawn: number; team: string | null; points: TrackPoint[] }[];
  kills: {
    t: number;
    r: number | null;
    u: number;
    v: number;
    shooter: string | null;
    victim: string | null;
    weapon: string | null;
  }[];
};

export type HeatmapCell = [number, number, number];

export type HeatmapPayload = {
  map: MapRef;
  proj: MapProj;
  regions: MapRegion[];
  grid: number;
  cells: HeatmapCell[];
  total: number;
  max: number;
};

export type CoachPrescription = {
  axis: string;
  label: string;
  score: number;
  grade: string;
  n: number;
  confidence: string;
  raw: string | null;
  text: string;
};

export type CoachPlayer = {
  subject: string;
  team: string;
  agent_uuid: string | null;
  axes: Record<string, number | null>;
  axis_n: Record<string, number>;
  confidence: Record<string, string>;
  overall: number | null;
  grade: string | null;
  prescription: CoachPrescription | null;
  verdict: {
    text: string;
    best_axis: string;
    best_score: number;
    worst_axis: string;
    worst_score: number;
  } | null;
};

export type CoachTeam = {
  axes: Record<string, number | null>;
  overall: number | null;
  grade: string | null;
  n_players: number;
};

export type CoachPayload = {
  version: number;
  replay_id: string;
  n_rounds: number;
  axes: string[];
  axis_label: Record<string, string>;
  weights: Record<string, number>;
  bands: Record<string, [number, number]>;
  players: CoachPlayer[];
  teams: Record<string, CoachTeam>;
  notes: string[];
};

// Coverage: how much of a match the export actually holds. Every field can be
// null when the export or an engine cache does not provide it.
export type CoverageCheck = { level: "ok" | "warn" | "info"; text: string };

export type CoveragePayload = {
  replay_id: string;
  generated_at: number;
  sources: Record<string, any>;
  streams: Record<string, number | null>;
  decode: Record<string, number | null>;
  match: Record<string, number | null>;
  damage: Record<string, number | null>;
  shots: Record<string, number | null> | null;
  checks: CoverageCheck[];
  notes: string[];
};

// The death audit: what the export shows around each death, never a verdict.
export type DeathEvent = {
  round: number;
  t: number | null;
  round_ms: number | null;
  round_won: boolean | null;
  killer: string | null;
  killer_team: string | null;
  killer_agent: string | null;
  killer_survived: boolean | null;
  damage_round: number | null;
  dealt_no_damage: boolean | null;
  first_blood: boolean | null;
  traded: boolean | null;
  isolated: boolean | null;
  bait: boolean | null;
  suicide: boolean | null;
  ult_cost: number | null;
  ult_points: number | null;
  ult_ready: boolean | null;
  ult_active: boolean | null;
};

export type DeathClass = { key: string; label: string; n: number; text: string };

export type DeathPlayer = {
  subject: string;
  team: string | null;
  agent_uuid: string | null;
  deaths: number;
  classes: DeathClass[];
  events: DeathEvent[];
};

export type DeathsPayload = {
  replay_id: string;
  generated_at: number;
  n_rounds: number;
  ult_available: boolean;
  players: DeathPlayer[];
  totals: Record<string, number>;
  classes: { key: string; label: string }[];
  notes: string[];
};

export type JudgeFault = {
  key: string;
  label: string;
  weight: number;
  n: number;
  points: number;
};

export type JudgePlayer = {
  subject: string;
  team: string;
  agent_uuid: string | null;
  deaths: number;
  adr: number | null;
  kast_pct: number | null;
  points: number;
  share_pct: number | null;
  grade: string | null;
  faults: JudgeFault[];
  case_against: string[];
  case_for: string[];
  missed_trades: number;
  entry_first: number;
  entry_late: number;
  entry_avg_order: number | null;
};

export type JudgePlant = {
  planter?: string | null;
  plant_t?: number | null;
  location?: string | null;
  site?: number | null;
  defuser?: string | null;
  defuse_s?: number | null;
} | null;

export type JudgeRoundClass = { key: string; text: string };

export type JudgeLostRound = {
  round: number;
  winner: string;
  loser: string;
  sides: Record<string, string> | null;
  overtime: boolean;
  buy: string;
  opponent_buy: string;
  changeable: boolean;
  max_lead_lost: number;
  plant: JudgePlant;
  classes: JudgeRoundClass[];
};

export type JudgeTeam = {
  lost: number;
  changeable: number;
  points: number;
  ranked: { subject: string; points: number; share_pct: number | null; grade: string | null }[];
  summary: string;
  devil: string;
};

export type Drill = {
  key: string;
  label: string;
  value: number;
  n: number;
  good: number;
  threshold: number;
  reverse: boolean;
  severity: number;
  drill: string;
  why: string;
  match_id?: string;
};

export type JudgePayload = {
  replay_id: string;
  generated_at: number;
  n_rounds: number;
  lost_rounds: JudgeLostRound[];
  players: JudgePlayer[];
  teams: Record<string, JudgeTeam>;
  weights: Record<string, number>;
  grade_bands: [number, string][];
  ult_available: boolean;
  plant_available: boolean;
  contact_available: boolean;
  training: Record<string, Drill[]>;
  notes: string[];
};

export type PlayersMatchPlayer = { subject: string; team: string | null; agent_uuid: string | null };

export type PlayersMatch = {
  match_id: string;
  replay_id: string;
  duplicates: string[];
  level_name: string | null;
  duration_ms: number | null;
  timestamp_ticks: number | null;
  build: number | null;
  players: PlayersMatchPlayer[];
};

export type PlayersRow = {
  subject: string;
  label: string | null;
  matches: number;
  match_ids: string[];
  agents: string[];
  teams: string[];
  rounds: number | null;
  kills: number | null;
  deaths: number | null;
  assists: number | null;
  kd: number | null;
  adr: number | null;
  acs: number | null;
  kast_pct: number | null;
  likely_me: boolean;
};

export type DeepState = {
  built: boolean;
  stale: boolean;
  version?: number;
  built_at?: number;
  seconds?: number;
  match_count?: number;
  matches: number;
  fingerprint: string;
};

export type PlayersCatalog = {
  generated_at: number;
  match_count: number;
  matches: PlayersMatch[];
  players: PlayersRow[];
  likely_me: string | null;
  me: string | null;
  names: Record<string, string>;
  skipped: { replay_id: string; why: string }[];
  deep: DeepState;
  seconds: number;
  notes: string[];
};

export type PlayerMatchRow = {
  match_id: string;
  replay_id: string;
  duplicates: string[];
  level_name: string | null;
  timestamp_ticks: number | null;
  team: string | null;
  agent_uuid: string | null;
  rounds: number | null;
  kills: number | null;
  deaths: number | null;
  assists: number | null;
  kd: number | null;
  adr: number | null;
  acs: number | null;
  kast_pct: number | null;
  hs_pct: number | null;
  entry_kills: number | null;
  entry_deaths: number | null;
  traded_pct: number | null;
  isolated_pct: number | null;
  self_deaths: number | null;
  ults_used: number | null;
  ability_casts: number | null;
  clutch_attempts: number | null;
  clutch_wins: number | null;
  multikills: Record<string, number> | null;
  avg_loadout_money: number | null;
};

export type SideBucket = {
  rounds?: number | null;
  kills?: number | null;
  deaths?: number | null;
  assists?: number | null;
  kast_rounds?: number | null;
  hs_kills?: number | null;
  entry_kills?: number | null;
  entry_deaths?: number | null;
  damage?: number | null;
  kd?: number | null;
  adr?: number | null;
  kast_pct?: number | null;
  hs_pct?: number | null;
};

export type AimCell = { value: number | null; n: number };

export type PlayerWeaponRow = {
  name: string;
  matches: number;
  shots: number;
  hits: number;
  kills: number;
  dmg: number;
  acc: number | null;
  hs_rate: number | null;
  dmg_per_shot: number | null;
  shots_per_kill: number | null;
};

export type PlayerDeep = {
  subject: string;
  label: string | null;
  matches: number;
  points: number;
  team_points: number;
  share_pct: number | null;
  grade: string | null;
  worst_share_pct: number | null;
  worst_match_id: string | null;
  deaths: number;
  classes: { key: string; n: number }[];
  faults: { key: string; label: string | null; weight: number; n: number; points: number }[];
  drills: Drill[];
  history: {
    match_id: string;
    at: number | null;
    points: number | null;
    share_pct: number | null;
    grade: string | null;
    deaths: number | null;
    adr: number | null;
  }[];
};

export type PlayerProfile = {
  generated_at: number;
  subject: string;
  label: string | null;
  me: string | null;
  is_me: boolean;
  match_count: number;
  matches: PlayerMatchRow[];
  pooled: {
    rounds: number | null;
    kills: number | null;
    deaths: number | null;
    assists: number | null;
    kd: number | null;
    adr: number | null;
    acs: number | null;
    kast_pct: number | null;
    self_deaths: number | null;
    self_damage: number | null;
    ults_used: number | null;
  };
  mean_of_matches: Record<string, number | null>;
  sides: Record<string, SideBucket>;
  buys: Record<string, SideBucket>;
  aim: Record<string, AimCell | number | null>;
  weapons: PlayerWeaponRow[];
  trend: {
    match_id: string;
    replay_id: string;
    at: number | null;
    rounds: number | null;
    adr: number | null;
    acs: number | null;
    kd: number | null;
    kast_pct: number | null;
    hs_pct: number | null;
  }[];
  agents: string[];
  teams: string[];
  deep: PlayerDeep | null;
  deep_state: DeepState;
  skipped: { replay_id: string; why: string }[];
  seconds: number;
  notes: string[];
};

export type PlayersBuildStatus = {
  phase: string | null;
  error: string | null;
  result: {
    matches: number;
    built: number;
    failed: { replay_id: string; why: string }[];
    seconds: number;
    built_at: number;
  } | null;
  state: DeepState;
};

// ---- the duo check (pair view) ----

// One player the subject actually shared a team with, plus the evidence behind the pairing.
export type TeammateRow = {
  subject: string;
  label: string | null;
  matches: number;
  match_ids: string[];
  rounds_together: number | null;
  rounds_without_winner: number;
  wins: number;
  losses: number;
  win_pct: number | null;
  first_seen: number | null;
  last_seen: number | null;
};

export type TeammatesPayload = {
  subject: string;
  label: string | null;
  likely_me: boolean;
  match_count: number;
  teammates: TeammateRow[];
  skipped: { replay_id: string; why: string }[];
  seconds: number;
  notes: string[];
};

// The other player's distance to my death position, sampled before the death.
export type DeathTrackPoint = { ms_before: number; distance_m: number | null };

// One of my deaths read from the pair's side. Every field is measured, never inferred;
// a missing sample is null (and its band is "unknown"), never 0.
export type PairDeathRow = {
  match_id: string | null;
  round: number;
  t: number;
  t_into_round_s: number | null;
  first_blood: boolean;
  round_winner: string | null;
  round_won: boolean | null;
  distance_cm: number | null;
  distance_m: number | null;
  distance_band: string;
  my_region: RegionHit | null;
  mate_region: RegionHit | null;
  my_sample_dt_ms: number | null;
  mate_sample_dt_ms: number | null;
  mate_alive: boolean;
  partner_forward_m: number | null;
  partner_lateral_m: number | null;
  partner_closing_cm: number | null;
  partner_move: "up" | "back" | "static" | null;
  partner_track: DeathTrackPoint[];
  partner_closest_cm: number | null;
  partner_closest_at_ms: number | null;
  partner_arrived_after_ms: number | null;
  partner_damage_that_round: number | null;
  partner_killed_my_killer: boolean;
  partner_killed_my_killer_at_ms: number | null;
  traded: boolean;
  isolated: boolean | null;
  bait: boolean | null;
  suicide: boolean;
  killer: string | null;
  damage_dealt_that_round: number | null;
};

// The counting side of the argument, one death at a time, in both directions.
export type PairTradeCounts = {
  total: number;
  traded_by_mate: number;
  traded_by_anyone: number;
  untraded_mate_alive: number;
  untraded_mate_alive_in_range: number;
  untraded_mate_alive_far: number;
  untraded_mate_alive_unknown: number;
  untraded_mate_dead: number;
  untraded_mate_never_came: number;
};

// A combined block over the rounds the two played together; every rate is null with no rounds.
export type PairSide = {
  rounds: number | null;
  kills: number | null;
  deaths: number | null;
  assists: number | null;
  damage_opponents: number | null;
  kast_rounds: number | null;
  adr: number | null;
  kd: number | null;
  kast_pct: number | null;
  kpr: number | null;
};

export type PairCombined = PairSide & {
  me: PairSide;
  partner: PairSide;
  n_note: string;
};

export type PairMatchRow = {
  match_id: string;
  replay_id: string;
  level_name: string | null;
  team: string | null;
  rounds_together: number;
  rounds_without_winner: number;
  wins: number;
  losses: number;
  win_pct: number | null;
  my_deaths: number;
  his_deaths: number;
  why: string | null;
};

// ---- identity: the four facts a replay holds about "who is me" ----
//
// A replay carries no Riot ID. What it does hold: the agent each player used, the map, the match
// start time and the first-half side. These types carry those facts and the ranked answer the
// evidence produces - a wrong answer is reported, never rounded up.

export type IdentityPlayer = {
  subject: string;
  agent: string | null;
  agent_uuid: string | null;
  team: string | null;
  side_first: "attacker" | "defender" | null;
  pawns: string[];
};

export type IdentitySignature = {
  replay_id: string;
  map: string | null;
  level: string | null;
  start_utc: string | null;
  start_local: string | null;
  start_ms: number | null;
  duration_s: number | null;
  sides_first: Record<string, string>;
  players: IdentityPlayer[];
  notes: string[];
};

export type IdentityCandidate = {
  subject: string;
  agent: string | null;
  team: string | null;
  side_first: string | null;
  score: number;
  facts: { agent: boolean; side: boolean; time: boolean | null };
  delta_min: number | null;
  certain: boolean;
};

export type IdentityAnswer = {
  replay_id: string;
  map: string | null;
  start_local: string | null;
  asked: {
    agent: string | null;
    side: string | null;
    at: string | null;
    tolerance_min: number;
    facts: number;
  };
  candidates: IdentityCandidate[];
  note: string;
  notes: string[];
};

export type IdentityStatus = {
  configured: boolean;
  region: string;
  routing: string;
  tolerance_s: number;
  note: string;
};

export type RiotTag = {
  subject: string | null;
  why: string | null;
  evidence?: {
    riot_id?: string;
    match_id?: string;
    map?: string;
    start_delta_s?: number;
    agent?: string;
    team?: string;
  };
};

export type PairPayload = {
  generated_at: number;
  me: { subject: string; label: string | null };
  partner: { subject: string; label: string | null };
  pair: {
    matches: number;
    matches_seen: number;
    rounds_together: number | null;
    wins: number;
    losses: number;
    win_pct: number | null;
    rounds_without_winner: number;
    why: string | null;
  };
  matches: PairMatchRow[];
  trades: { mine: PairTradeCounts; his: PairTradeCounts; rule: string; range_rule: string };
  deaths: PairDeathRow[];
  his_deaths: PairDeathRow[];
  contacts: {
    rounds: number;
    mine_first_blood: number;
    his_first_blood: number;
    mine_first_death: number;
    his_first_death: number;
    first_traded_by_me: number;
    first_traded_by_him: number;
  };
  combined: PairCombined;
  bands: Record<string, number>;
  skipped: { replay_id: string; why: string }[];
  seconds: number;
  notes: string[];
  limits: string[];
};

export type AIStatus = {
  ok: boolean;
  base_url: string;
  model: string;
  models?: number;
  error?: string;
};

export type AIAnswer = {
  answer: string;
  model: string;
  usage?: Record<string, any> | null;
  context?: { players: number; rounds: number; question: string; focus?: string | null };
};

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  replays: () => fetch("/api/replays").then((r) => j<ReplayRow[]>(r)),
  replay: (id: string) => fetch(`/api/replays/${id}`).then((r) => j<ReplayRecord>(r)),
  status: (id: string) => fetch(`/api/replays/${id}/status`).then((r) => j<StatusPayload>(r)),
  remove: (id: string) =>
    fetch(`/api/replays/${id}`, { method: "DELETE" }).then((r) => j<any>(r)),
  rename: (id: string, title: string) =>
    fetch(`/api/replays/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    }).then((r) => j<ReplayRecord>(r)),
  link: (path: string, title?: string) =>
    fetch("/api/replays/link", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, title: title?.trim() || undefined }),
    }).then((r) => j<{ id: string; status: string }>(r)),
  upload: (file: File, title?: string) => {
    const fd = new FormData();
    fd.append("file", file);
    if (title?.trim()) fd.append("title", title.trim());
    return fetch("/api/replays", { method: "POST", body: fd }).then((r) =>
      j<{ id: string; status: string }>(r)
    );
  },
  tables: (id: string) => fetch(`/api/replays/${id}/tables`).then((r) => j<TableInfo[]>(r)),
  table: (
    id: string,
    table: string,
    opts: { offset?: number; limit?: number; sort?: string; desc?: boolean; filter?: string[] } = {}
  ) => {
    const q = new URLSearchParams();
    q.set("offset", String(opts.offset ?? 0));
    q.set("limit", String(opts.limit ?? 100));
    if (opts.sort) q.set("sort", opts.sort);
    if (opts.desc) q.set("desc", "true");
    for (const f of opts.filter ?? []) q.append("filter", f);
    return fetch(`/api/replays/${id}/tables/${table}?${q}`).then((r) => j<TablePage>(r));
  },
  distinct: (id: string, table: string, column: string) =>
    fetch(`/api/replays/${id}/tables/${table}/distinct?column=${encodeURIComponent(column)}`).then(
      (r) => j<any[]>(r)
    ),
  rounds: (id: string) => fetch(`/api/replays/${id}/rounds`).then((r) => j<any[]>(r)),
  stats: (id: string, fresh = false) =>
    fetch(`/api/replays/${id}/stats${fresh ? "?fresh=true" : ""}`).then((r) =>
      j<StatsPayload>(r)
    ),
  metrics: (id: string, light = false) =>
    fetch(`/api/replays/${id}/metrics${light ? "?light=true" : ""}`).then((r) =>
      j<MetricsPayload>(r)
    ),
  tracks: (id: string, round?: number | null) =>
    fetch(`/api/replays/${id}/tracks${round != null ? `?round=${round}` : ""}`).then((r) =>
      j<TracksPayload>(r)
    ),
  heatmap: (
    id: string,
    opts: { player?: string; team?: string; round?: number | null; mode?: string } = {}
  ) => {
    const q = new URLSearchParams();
    if (opts.player) q.set("player", opts.player);
    if (opts.team) q.set("team", opts.team);
    if (opts.round != null) q.set("round", String(opts.round));
    if (opts.mode) q.set("mode", opts.mode);
    return fetch(`/api/replays/${id}/heatmap?${q}`).then((r) => j<HeatmapPayload>(r));
  },
  regions: (id: string) =>
    fetch(`/api/replays/${id}/regions`).then((r) =>
      j<{ map: MapRef; proj: MapProj; regions: MapRegion[] }>(r)
    ),
  coach: (id: string) => fetch(`/api/replays/${id}/coach`).then((r) => j<CoachPayload>(r)),
  coverage: (id: string) =>
    fetch(`/api/replays/${id}/coverage`).then((r) => j<CoveragePayload>(r)),
  deaths: (id: string) => fetch(`/api/replays/${id}/deaths`).then((r) => j<DeathsPayload>(r)),
  judge: (id: string) => fetch(`/api/replays/${id}/judge`).then((r) => j<JudgePayload>(r)),
  players: () => fetch("/api/players").then((r) => j<PlayersCatalog>(r)),
  playerNames: () => fetch("/api/players/names").then((r) => j<LocalNames>(r)),
  player: (subject: string, deep = false) =>
    fetch(`/api/players/${encodeURIComponent(subject)}${deep ? "?deep=true" : ""}`).then((r) =>
      j<PlayerProfile>(r)
    ),
  playerBuildStatus: () =>
    fetch("/api/players/build/status").then((r) => j<PlayersBuildStatus>(r)),
  playerBuild: (fresh = true) =>
    fetch("/api/players/build", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fresh }),
    }).then((r) => j<PlayersBuildStatus>(r)),
  playerSetMe: (subject: string | null) =>
    fetch("/api/players/me", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subject }),
    }).then((r) => j<Record<string, any>>(r)),
  playerSetName: (subject: string, label: string) =>
    fetch("/api/players/names", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subject, label }),
    }).then((r) => j<Record<string, any>>(r)),
  identitySignature: (replay: string) =>
    fetch(`/api/identity/signature?replay=${encodeURIComponent(replay)}`).then((r) =>
      j<IdentitySignature>(r)
    ),
  identify: (
    replay: string,
    ask: { agent?: string; side?: string; at?: string; toleranceMin?: number } = {}
  ) => {
    const q = new URLSearchParams({ replay });
    if (ask.agent) q.set("agent", ask.agent);
    if (ask.side) q.set("side", ask.side);
    if (ask.at) q.set("at", ask.at);
    if (ask.toleranceMin) q.set("tolerance_min", String(ask.toleranceMin));
    return fetch(`/api/identity/identify?${q.toString()}`).then((r) => j<IdentityAnswer>(r));
  },
  identityStatus: () => fetch("/api/identity/status").then((r) => j<IdentityStatus>(r)),
  riotTag: (replay_id: string, game_name: string, tag_line: string) =>
    fetch("/api/identity/riot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ replay_id, game_name, tag_line }),
    }).then((r) => j<RiotTag>(r)),
  teammates: (subject: string) =>
    fetch(`/api/players/${encodeURIComponent(subject)}/teammates`).then((r) =>
      j<TeammatesPayload>(r)
    ),
  pair: (me: string, partner: string, match?: string) => {
    const q = new URLSearchParams();
    q.set("me", me);
    q.set("partner", partner);
    if (match) q.set("match", match);
    return fetch(`/api/duo/pair?${q}`).then((r) => j<PairPayload>(r));
  },
  aiStatus: () => fetch("/api/analysis/status").then((r) => j<AIStatus>(r)),
  ask: (id: string, question: string, subject?: string | null, model?: string | null) =>
    fetch("/api/analysis/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        replay_id: id,
        question,
        subject: subject || undefined,
        model: model || undefined,
      }),
    }).then((r) => j<AIAnswer>(r)),
  agents: () => fetch("/api/game/agents").then((r) => j<Agent[]>(r)),
  maps: () => fetch("/api/game/maps").then((r) => j<MapInfo[]>(r)),
  weapons: () => fetch("/api/game/weapons").then((r) => j<Weapon[]>(r)),
  syncAssets: () =>
    fetch("/api/game/sync-assets", { method: "POST" }).then((r) => j<any>(r)),
  health: () => fetch("/api/health").then((r) => j<any>(r)),
};

export const agentIcon = (uuid: string | null | undefined) =>
  uuid ? `/api/assets/agent/${uuid.toLowerCase()}.png` : undefined;

export const mapIcon = (uuid: string | null | undefined) =>
  uuid ? `/api/assets/map/${uuid.toLowerCase()}.png` : undefined;

export const weaponIcon = (uuid: string | null | undefined) =>
  uuid ? `/api/assets/weapon/${uuid.toLowerCase()}.png` : undefined;

// "/Game/Maps/Juliett/Juliett" -> "Juliett"
export const shortLevel = (level: string | null | undefined) => {
  if (!level) return null;
  const parts = level.split("/").filter(Boolean);
  return parts[parts.length - 1] || level;
};
