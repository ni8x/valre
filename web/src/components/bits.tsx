import { Agent, agentIcon } from "../api";
import { shortId } from "../format";

export function AgentIcon({
  uuid,
  agents,
  size = 32,
}: {
  uuid: string | null | undefined;
  agents: Agent[];
  size?: number;
}) {
  const key = (uuid || "").toLowerCase();
  const a = agents.find((x) => x.uuid === key);
  const src = agentIcon(uuid);
  if (!src) return <div className="agent-ph" style={{ width: size, height: size }} />;
  return (
    <img
      className="agent-ico"
      style={{ width: size, height: size }}
      src={src}
      title={a?.name || uuid || ""}
      alt={a?.name || ""}
      onError={(e) => ((e.target as HTMLImageElement).style.opacity = "0.2")}
    />
  );
}

export function agentNameOf(agents: Agent[], uuid?: string | null): string | null {
  const key = (uuid || "").toLowerCase();
  if (!key || !agents.length) return null;
  return agents.find((x) => x.uuid === key)?.name ?? null;
}

export function PlayerLabel({ subject, name }: { subject: string; name?: string | null }) {
  return (
    <span className="mono small muted" title={subject}>
      {name || shortId(subject)}
    </span>
  );
}

export function Bar({ value, max, color }: { value: number; max: number; color?: string }) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className="minibar">
      <div className="fill" style={{ width: `${pct}%`, background: color }} />
    </div>
  );
}
