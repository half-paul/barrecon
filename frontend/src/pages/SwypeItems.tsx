import { Fragment, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type SwypeItem, day, groupBy, n } from "../lib/api";
import { useReadOnly } from "../lib/auth";
import { Button, Empty, Flash, Loading, Notice, Page, inputCls, useAsync } from "../lib/ui";

type Status = "active" | "off" | "all";
type Range = "all" | "day" | "week" | "month" | "quarter" | "year" | "custom";
type SalesPeriod = { period_from: string; period_to: string };
const RANGES: [Range, string, number][] = [["all", "All time", 0], ["day", "Last day", 1], ["week", "Last 7 days", 7],
  ["month", "Last 30 days", 30], ["quarter", "Last 90 days", 90], ["year", "Last year", 365], ["custom", "Custom", 0]];
const iso = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const daysAgo = (k: number) => { const d = new Date(); d.setDate(d.getDate() - k); return iso(d); };
const UNCAT = "Uncategorised";
const catOf = (i: SwypeItem) => i.category || UNCAT;
const byName = (a: string, b: string) => (a === UNCAT ? 1 : b === UNCAT ? -1 : a.localeCompare(b));

export default function SwypeItems() {
  const viewOnly = useReadOnly("swype_items");
  const [params, setParams] = useSearchParams();
  const setParam = (k: string, v: string) => setMany({ [k]: v });
  const setMany = (kv: Record<string, string>) => {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(kv)) { if (v) p.set(k, v); else p.delete(k); }
    setParams(p, { replace: true });
  };
  const q = params.get("q") ?? "";
  const status = (params.get("status") as Status | null) ?? "active";
  const range = (params.get("range") as Range | null) ?? "all";
  const days = RANGES.find((r) => r[0] === range)?.[2] ?? 0;
  const [from, to] = range === "custom" ? [params.get("from") ?? "", params.get("to") ?? ""]
    : days ? [daysAgo(days), daysAgo(0)] : ["", ""];
  const qs = new URLSearchParams(Object.entries({ from, to }).filter(([, v]) => v)).toString();
  const items = useAsync(() => api.get<SwypeItem[]>(`/api/swype-items${qs ? `?${qs}` : ""}`), [qs]);
  const periods = useAsync(() => api.get<SalesPeriod[]>("/api/imports/swype-item-sales/periods"));
  const hit = (periods.data ?? []).filter((p) => (!from || p.period_to >= from) && (!to || p.period_from <= to));
  const pickRange = (r: Range) => setMany(r === "custom"
    ? { range: r, from: from || daysAgo(7), to: to || daysAgo(0) }
    : { range: r === "all" ? "" : r, from: "", to: "" });
  const all = items.data ?? [];
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const searched = all.filter((i) => words.every((w) => [i.code, i.name, i.swype_name, i.category ?? ""].some((f) => f.toLowerCase().includes(w))));
  const statusCount = { active: searched.filter((i) => i.is_active).length, off: searched.filter((i) => !i.is_active).length, all: searched.length };
  const shown = searched.filter((i) => status === "all" || (status === "active") === i.is_active);
  const groups = groupBy(shown, catOf).sort((a, b) => byName(a[0], b[0]));
  const [editing, setEditing] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const patch = async (it: SwypeItem, body: { name?: string; is_active?: boolean }) => {
    try { await api.patch(`/api/swype-items/${encodeURIComponent(it.code)}`, body); setEditing(null); setMsg(null); items.reload(); }
    catch (e) { setMsg((e as Error).message); }
  };

  return (
    <Page area="swype_items" title="Swype items" sub="What is rung in on SwypePOS. The Swype # never changes; a rename here survives imports. Items with nothing sold in the month up to the latest imported sales are switched off; a sale switches them back on.">
      {msg && <Flash kind="error" onClose={() => setMsg(null)}>{msg}</Flash>}
      {items.loading && !items.data && !items.error && <Loading />}
      {items.error && <Notice kind="error">{items.error}</Notice>}
      {items.data && all.length === 0 && <Empty>No Swype items yet. Import a Swype sales report or the Export Items CSV under Imports.</Empty>}
      {all.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 mb-4">
          <input className={`${inputCls} w-full sm:w-72`} placeholder="Filter by name, Swype # or category" aria-label="Filter Swype items" value={q} onChange={(e) => setParam("q", e.target.value)} />
          <div className="inline-flex rounded-[10px] border border-line overflow-hidden text-sm" role="group" aria-label="Status">
            {([["active", "Active"], ["off", "Switched off"], ["all", "All"]] as const).map(([k, label]) => (
              <button key={k} aria-pressed={status === k} onClick={() => setParam("status", k === "active" ? "" : k)}
                className={`px-3 py-1.5 ${status === k ? "bg-raised text-ink font-medium" : "bg-surface text-muted hover:text-ink"}`}>
                {label} <span className="mono text-xs text-faint">{statusCount[k]}</span></button>))}
          </div>
          <span className="text-sm text-muted">A switched-off item comes back on when a Swype import shows it sold.</span>
        </div>
      )}
      {all.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 mb-4">
          <div className="inline-flex flex-wrap rounded-[10px] border border-line overflow-hidden text-sm" role="group" aria-label="Sold in">
            {RANGES.map(([k, label]) => (
              <button key={k} aria-pressed={range === k} onClick={() => pickRange(k)}
                className={`px-3 py-1.5 ${range === k ? "bg-raised text-ink font-medium" : "bg-surface text-muted hover:text-ink"}`}>{label}</button>))}
          </div>
          {range === "custom" && (<>
            <input type="date" className={inputCls} aria-label="From" value={from} max={to || undefined} onChange={(e) => setParam("from", e.target.value)} />
            <span className="text-sm text-muted">to</span>
            <input type="date" className={inputCls} aria-label="To" value={to} min={from || undefined} onChange={(e) => setParam("to", e.target.value)} />
          </>)}
          {range !== "all" && periods.data && (
            <span className="text-sm text-muted">{hit.length
              ? <>Sold counts {hit.length} imported report{hit.length > 1 ? "s" : ""}, {day(hit[0].period_from)} – {day(hit[hit.length - 1].period_to)}. A report overlapping the range counts whole.</>
              : "No Swype sales imported for this range."}</span>)}
        </div>
      )}
      {all.length > 0 && shown.length === 0 && <Empty>No Swype items match.</Empty>}
      {shown.length > 0 && (
        <div className="card table-box px-2"><table className="grid">
          <thead><tr><th>Swype #</th><th>Name</th><th className="r">Sold</th><th className="r">Last sold</th><th>Active</th><th /></tr></thead>
          <tbody>
            {groups.map(([c, rows]) => (<Fragment key={c}>
              <tr className="section"><td colSpan={6}>{c} <span className="text-faint font-normal normal-case tracking-normal">{rows.length}</span></td></tr>
              {rows.map((it) => editing === it.code
                ? <EditRow key={it.code} it={it} onSave={(name) => patch(it, { name })} onCancel={() => setEditing(null)} />
                : (
                  <tr key={it.code} className={it.is_active ? "" : "text-muted"}>
                    <td className="num text-muted whitespace-nowrap">{it.code}</td>
                    <td>{it.name}{it.renamed && <div className="text-xs text-muted">Swype: {it.swype_name}</div>}</td>
                    <td className="r num">{it.sold ? n(it.sold, 0) : <span className="text-faint">—</span>}</td>
                    <td className="r num whitespace-nowrap">{it.last_sold ? day(it.last_sold) : <span className="text-faint">never</span>}</td>
                    <td><input type="checkbox" disabled={viewOnly} aria-label={`${it.name} active`} checked={it.is_active} onChange={(e) => patch(it, { is_active: e.target.checked })} /></td>
                    <td className="r"><button hidden={viewOnly} className="text-sm text-muted hover:text-ink underline" aria-label={`Rename ${it.name}`} onClick={() => setEditing(it.code)}>Rename</button></td>
                  </tr>))}
            </Fragment>))}
          </tbody>
        </table></div>
      )}
    </Page>
  );
}

function EditRow({ it, onSave, onCancel }: { it: SwypeItem; onSave: (name: string) => void; onCancel: () => void }) {
  const [name, setName] = useState(it.name);
  return (
    <tr className="bg-raised/40" onKeyDown={(e) => { if (e.key === "Enter") onSave(name); if (e.key === "Escape") onCancel(); }}>
      <td className="num text-muted whitespace-nowrap">{it.code}</td>
      <td colSpan={4}>
        <input className="cell-input text-left w-full max-w-md" autoFocus value={name} aria-label="Name" onChange={(e) => setName(e.target.value)} />
        <div className="text-xs text-muted mt-1">Swype: {it.swype_name}. Leave blank to use Swype's name.</div>
      </td>
      <td className="r whitespace-nowrap">
        <Button onClick={() => onSave(name)}>Save</Button>
        <button className="text-sm text-muted hover:text-ink underline ml-2" onClick={onCancel}>Cancel</button>
      </td>
    </tr>
  );
}
