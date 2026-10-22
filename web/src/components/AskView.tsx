import { useEffect, useRef, useState } from "react";
import { api, Agent, AIStatus } from "../api";
import { shortId } from "../format";
import { AgentIcon, agentNameOf } from "./bits";

type Msg =
  | { role: "user"; text: string }
  | { role: "ai"; text: string; model?: string; usage?: Record<string, any> | null; error?: boolean };

const SUGGESTIONS = [
  "Who played best and why?",
  "Why did the losing team lose?",
  "Which rounds were thrown away?",
  "Summarize the match in 3 bullets.",
  "How can the losing team improve?",
];

function Inline({ text }: { text: string }) {
  const parts = text.split(/\*\*/);
  return (
    <>
      {parts.map((p, i) => (i % 2 === 1 ? <strong key={i}>{p}</strong> : <span key={i}>{p}</span>))}
    </>
  );
}

function Answer({ text }: { text: string }) {
  const lines = text.split("\n");
  return (
    <div className="stack">
      {lines.map((ln, i) => {
        const m = ln.match(/^\s*[-*]\s+(.*)$/);
        const body = m ? m[1] : ln;
        if (!body.trim()) return <div key={i} style={{ height: 6 }} />;
        return (
          <div key={i} className={m ? "row" : ""}>
            {m && <span className="muted">•</span>}
            <Inline text={body} />
          </div>
        );
      })}
    </div>
  );
}

export default function AskView({ id, agents }: { id: string; agents: Agent[] }) {
  const [players, setPlayers] = useState<{ subject: string; team: string | null; agent_uuid: string | null | undefined }[]>([]);
  const [status, setStatus] = useState<AIStatus | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const tail = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let stop = false;
    (async () => {
      try {
        const s = await api.stats(id);
        if (stop) return;
        setPlayers(
          s.players.map((p) => ({ subject: p.subject, team: p.team, agent_uuid: p.agent_uuid }))
        );
      } catch {
        /* player list is optional */
      }
      try {
        const st = await api.aiStatus();
        if (!stop) setStatus(st);
      } catch (e) {
        if (!stop) setStatus({ ok: false, base_url: "", model: "", error: String(e) });
      }
    })();
    return () => {
      stop = true;
    };
  }, [id]);

  useEffect(() => {
    tail.current?.scrollIntoView({ block: "end" });
  }, [msgs.length, busy]);

  const send = async (text?: string) => {
    const question = (text ?? q).trim();
    if (!question || busy) return;
    setQ("");
    setMsgs((m) => [...m, { role: "user", text: question }]);
    setBusy(true);
    try {
      const res = await api.ask(id, question, sel);
      setMsgs((m) => [...m, { role: "ai", text: res.answer, model: res.model, usage: res.usage }]);
    } catch (e) {
      setMsgs((m) => [...m, { role: "ai", text: String((e as Error)?.message || e), error: true }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <div className="panel">
        <div className="row spread">
          <div className="row">
            <strong>ASK THE COACH</strong>
            <span className="muted small">the model reads this match dossier (stats + rounds + coach) ~</span>
          </div>
          {status == null ? (
            <span className="muted small">checking AI…</span>
          ) : status.ok ? (
            <span className="small mono" style={{ color: "#35c26e" }}>
              AI ready · {status.model} · {status.models ?? "?"} models
            </span>
          ) : (
            <span className="err small">
              AI offline · start FreeLLMAPI (127.0.0.1:31415) or set AI_BASE_URL / AI_API_KEY
              {status.error ? ` · ${status.error}` : ""}
            </span>
          )}
        </div>
      </div>

      <div className="panel stack">
        <div className="row spread">
          <strong>Focus</strong>
          <span className="muted small">optional — extra context for the question</span>
        </div>
        <div className="row">
          <button
            className="chip pointer"
            aria-pressed={sel === null}
            style={sel === null ? { outline: "2px solid #4a8fe0" } : undefined}
            onClick={() => setSel(null)}
          >
            All players
          </button>
          {players.map((p) => (
            <button
              key={p.subject}
              className={`chip pointer ${p.team === "Red" ? "tm-red" : "tm-blue"}`}
              aria-pressed={sel === p.subject}
              style={sel === p.subject ? { outline: "2px solid #4a8fe0" } : undefined}
              onClick={() => setSel(p.subject === sel ? null : p.subject)}
              title={p.subject}
            >
              <span className="row">
                <AgentIcon uuid={p.agent_uuid} agents={agents} size={18} />
                {agentNameOf(agents, p.agent_uuid) ?? shortId(p.subject)}
              </span>
            </button>
          ))}
        </div>
        <textarea
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder="Ask about this match… e.g. why did Blue lose? who should they protect? what should they practice?"
          rows={3}
          style={{ width: "100%", resize: "vertical" }}
        />
        <div className="row spread">
          <div className="row">
            {SUGGESTIONS.map((s) => (
              <button key={s} className="chip pointer" onClick={() => send(s)} disabled={busy}>
                {s}
              </button>
            ))}
          </div>
          <button className="chip pointer" onClick={() => send()} disabled={busy || !q.trim()}>
            {busy ? "thinking…" : "Ask"}
          </button>
        </div>
      </div>

      {(msgs.length > 0 || busy) && (
        <div className="panel stack">
          {msgs.map((m, i) =>
            m.role === "user" ? (
              <div key={i} className="stack" style={{ borderLeft: "2px solid #4a8fe0", paddingLeft: 10 }}>
                <div className="muted small">YOU</div>
                <div>{m.text}</div>
              </div>
            ) : (
              <div
                key={i}
                className="stack"
                style={{ borderLeft: "2px solid " + (m.error ? "#d95d5d" : "#35c26e"), paddingLeft: 10 }}
              >
                <div className="muted small">
                  VRL COACH{m.model ? ` · ${m.model}` : ""}
                  {m.usage?.total_tokens ? ` · ${m.usage.total_tokens} tok` : ""}
                </div>
                {m.error ? <div className="err small">{m.text}</div> : <Answer text={m.text} />}
              </div>
            )
          )}
          {busy && <div className="muted small">thinking…</div>}
          <div ref={tail} />
        </div>
      )}
    </div>
  );
}
