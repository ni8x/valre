import { useEffect, useRef, useState } from "react";
import { api, ColumnInfo, TableInfo, TablePage } from "../api";

const PAGE = 100;

export default function TableExplorer({ id }: { id: string }) {
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [table, setTable] = useState<string>("fields");
  const [page, setPage] = useState<TablePage | null>(null);
  const [offset, setOffset] = useState(0);
  const [sort, setSort] = useState<string>("");
  const [desc, setDesc] = useState(false);
  const [filters, setFilters] = useState<{ col: string; op: string; val: string }[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [cell, setCell] = useState<{ col: string; val: unknown } | null>(null);
  const modalRef = useRef<HTMLDivElement | null>(null);
  const openerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!cell) return;
    openerRef.current = document.activeElement as HTMLElement | null;
    modalRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setCell(null);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      openerRef.current?.focus();
    };
  }, [cell]);

  useEffect(() => {
    api.tables(id).then(setTables).catch((e) => setErr(String(e)));
  }, [id]);

  useEffect(() => {
    setLoading(true);
    setErr(null);
    api
      .table(id, table, {
        offset,
        limit: PAGE,
        sort: sort || undefined,
        desc,
        filter: filters.filter((f) => f.col && f.val !== "").map((f) => `${f.col}:${f.op}:${f.val}`),
      })
      .then(setPage)
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }, [id, table, offset, sort, desc, filters]);

  const cols: ColumnInfo[] = (page?.columns || []).filter((c) => !c.hidden);
  const total = page?.total ?? 0;

  return (
    <div className="stack">
      <section className="panel">
        <h2>Tables</h2>
        <div className="row wrap">
          {tables.map((t) => (
            <button
              key={t.name}
              className={table === t.name ? "btn small" : "btn ghost small"}
              title={t.desc}
              disabled={!t.exists}
              onClick={() => {
                setTable(t.name);
                setOffset(0);
                setSort("");
                setFilters([]);
                setCell(null);
              }}
            >
              {t.label} <span className="muted">({t.rows.toLocaleString()})</span>
            </button>
          ))}
        </div>
        <div className="muted small">{tables.find((t) => t.name === table)?.desc}</div>
      </section>

      <section className="panel">
        <div className="row spread">
          <div className="row">
            <b className="mono">{table}.parquet</b>
            <span className="muted small">{total.toLocaleString()} rows</span>
          </div>
          <div className="row">
            <input
              className="mono small"
              placeholder="sort column"
              value={sort}
              list={`cols-${table}`}
              onChange={(e) => { setSort(e.target.value); setOffset(0); }}
            />
            <datalist id={`cols-${table}`}>
              {cols.map((c) => <option key={c.name} value={c.name} />)}
            </datalist>
            <button className="ghost small" onClick={() => setDesc(!desc)}>{desc ? "desc ↓" : "asc ↑"}</button>
          </div>
        </div>

        <div className="filters">
          {filters.map((f, i) => (
            <div key={i} className="row">
              <input
                className="mono small"
                placeholder="column"
                value={f.col}
                list={`cols-${table}`}
                onChange={(e) => { const n = [...filters]; n[i] = { ...f, col: e.target.value }; setFilters(n); setOffset(0); }}
              />
              <select value={f.op} onChange={(e) => { const n = [...filters]; n[i] = { ...f, op: e.target.value }; setFilters(n); setOffset(0); }}>
                {["eq", "ne", "contains", "gt", "gte", "lt", "lte"].map((o) => <option key={o}>{o}</option>)}
              </select>
              <input
                className="mono small grow"
                placeholder="value"
                value={f.val}
                onChange={(e) => { const n = [...filters]; n[i] = { ...f, val: e.target.value }; setFilters(n); setOffset(0); }}
              />
              <button className="ghost small" aria-label="Remove filter" onClick={() => setFilters(filters.filter((_, j) => j !== i))}>×</button>
            </div>
          ))}
          <button className="ghost small" onClick={() => setFilters([...filters, { col: "", op: "eq", val: "" }])}>
            + filter
          </button>
        </div>

        {err && <div className="err small">{err}</div>}
        {loading && <div className="muted small">Loading…</div>}

        <div className="table-wrap tall">
          <table className="data mono small">
            <thead>
              <tr>
                <th className="rowno">#</th>
                {cols.map((c) => (
                  <th
                    key={c.name}
                    className={sort === c.name ? "sorted" : ""}
                    aria-sort={sort === c.name ? (desc ? "descending" : "ascending") : undefined}
                  >
                    <button type="button" className="thbtn" onClick={() => { setSort(c.name); setOffset(0); }}>
                      {c.name}
                      <span className="ctype">{c.type}</span>
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(page?.rows || []).map((r, i) => (
                <tr key={i}>
                  <td className="rowno">{offset + i + 1}</td>
                  {cols.map((c) => {
                    const v = r[c.name];
                    return (
                      <td key={c.name} onClick={() => setCell({ col: c.name, val: v })} title="click to inspect">
                        {render(v)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="row spread">
          <button className="ghost small" disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - PAGE))}>← prev</button>
          <span className="muted small mono">
            {offset + 1}–{Math.min(offset + PAGE, total)} of {total.toLocaleString()}
          </span>
          <button className="ghost small" disabled={offset + PAGE >= total || loading} onClick={() => setOffset(offset + PAGE)}>next →</button>
        </div>
      </section>

      {cell && (
        <div className="modal" onClick={() => setCell(null)}>
          <div
            className="modal-box"
            role="dialog"
            aria-modal="true"
            aria-label={`Cell value: ${cell.col}`}
            tabIndex={-1}
            ref={modalRef}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="row spread">
              <b className="mono">{cell.col}</b>
              <button className="ghost small" onClick={() => setCell(null)}>close</button>
            </div>
            <pre className="log">{jsonify(cell.val)}</pre>
          </div>
        </div>
      )}
    </div>
  );
}

function render(v: unknown) {
  if (v === null || v === undefined) return <span className="null">null</span>;
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") return String(v);
  const s = String(v);
  if (s.length > 60) return s.slice(0, 60) + "…";
  return s;
}

function jsonify(v: unknown) {
  try {
    if (typeof v === "string" && (v.startsWith("{") || v.startsWith("["))) {
      return JSON.stringify(JSON.parse(v), null, 2);
    }
  } catch {
    /* ignore */
  }
  return typeof v === "string" ? v : JSON.stringify(v, null, 2);
}
