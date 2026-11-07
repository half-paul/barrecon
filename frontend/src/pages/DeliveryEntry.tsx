import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Item, groupBy, n } from "../lib/api";
import { Button, Field, Flash, Notice, Page, inputCls, useAsync } from "../lib/ui";

// One invoice on one sheet, like a count: every active item is listed and only the rows with a quantity are saved.
// The unsaved sheet is kept in this browser so leaving the page doesn't lose it.

type Kind = { kind: string; label: string };
type Line = { cases: string; packs: string; cost: string };
type Draft = { kind: string; date: string; ref: string; note: string; lines: Record<string, Line> };
const KEY = "delivery-draft";
const today = () => new Date().toISOString().slice(0, 10);
const fresh = (): Draft => ({ kind: "delivery", date: today(), ref: "", note: "", lines: {} });
const load = (): Draft => { try { const v = localStorage.getItem(KEY); return v ? { ...fresh(), ...JSON.parse(v) } : fresh(); } catch { return fresh(); } };
const keep = (d: Draft) => { try { localStorage.setItem(KEY, JSON.stringify(d)); } catch { /* storage unavailable: the sheet still works */ } };
const totalPacks = (it: Item, l?: Line) => l ? (Number(l.cases) || 0) * Number(it.case_size) + (Number(l.packs) || 0) : 0;

export default function DeliveryEntry() {
  const items = useAsync(() => api.get<Item[]>("/api/items"));
  const kinds = useAsync(() => api.get<Kind[]>("/api/movements/kinds"));
  const [d, setD] = useState<Draft>(load);
  const [q, setQ] = useState("");
  const [onlyEntered, setOnlyEntered] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => keep(d), [d]);

  const all = items.data ?? [];
  const entered = all.filter((it) => totalPacks(it, d.lines[it.id]) > 0);
  const bad = all.filter((it) => { const l = d.lines[it.id]; return l && [l.cases, l.packs, l.cost].some((v) => v.trim() !== "" && !(Number(v) >= 0)); });
  const spend = entered.reduce((t, it) => t + totalPacks(it, d.lines[it.id]) * (Number(d.lines[it.id].cost || it.pack_cost) || 0), 0);
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = all.filter((it) => (!onlyEntered || entered.includes(it)) && words.every((w) => [it.name, it.sku, it.category ?? ""].some((f) => f.toLowerCase().includes(w))));
  const groups = useMemo(() => groupBy(shown, (it) => it.category || "Uncategorised"), [shown]); // eslint-disable-line react-hooks/exhaustive-deps
  const delivery = d.kind === "delivery";

  const setLine = (id: string, k: keyof Line, v: string) => setD((x) => ({ ...x, lines: { ...x.lines, [id]: { ...{ cases: "", packs: "", cost: "" }, ...x.lines[id], [k]: v } } }));
  const clear = () => { if (confirm("Clear this sheet? Everything entered on it is lost.")) { setD(fresh()); setMsg(null); } };
  const save = async () => {
    setSaving(true);
    try {
      const r = await api.post<{ inserted: number; duplicate: number; superseded: number }>("/api/movements/batch", {
        kind: d.kind, occurred_at: new Date(`${d.date}T12:00:00`).toISOString(), external_ref: d.ref.trim(), note: d.note.trim() || null,
        lines: entered.map((it) => ({ item_id: it.id, packs: String(totalPacks(it, d.lines[it.id])), pack_cost: delivery && d.lines[it.id].cost.trim() ? d.lines[it.id].cost : null })),
      });
      const parts = [r.inserted && `${r.inserted} recorded`, r.superseded && `${r.superseded} updated`, r.duplicate && `${r.duplicate} already recorded`].filter(Boolean);
      setMsg({ kind: "ok", text: `${d.ref.trim()}: ${parts.join(", ")}.` });
      setD(fresh()); setQ(""); setOnlyEntered(false);
    } catch (e) { setMsg({ kind: "error", text: (e as Error).message }); } finally { setSaving(false); }
  };

  return (
    <Page area="deliveries" title="Record a delivery" sub={entered.length ? `${entered.length} item${entered.length === 1 ? "" : "s"} on this sheet${spend ? ` · $${n(spend, 2)}` : ""}` : "Enter what arrived; leave the rest empty"} actions={<>
      <Link className="text-sm underline" to="/movements">All deliveries</Link>
      {(entered.length > 0 || d.ref) && <button className="text-sm text-muted hover:text-ink underline" onClick={clear}>Clear sheet</button>}
      <Button onClick={save} disabled={saving || !d.ref.trim() || entered.length === 0 || bad.length > 0}>{saving ? "Saving…" : `Save ${entered.length || ""} item${entered.length === 1 ? "" : "s"}`}</Button>
    </>}>
      {msg && <Flash kind={msg.kind} onClose={() => setMsg(null)}>{msg.text}{msg.kind === "ok" && <> <Link className="underline" to="/movements">See them</Link></>}</Flash>}
      {bad.length > 0 && <Notice kind="error">Check {bad.map((it) => it.name).join(", ")}: quantities and costs must be numbers, 0 or more.</Notice>}
      <div className="card grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 mb-6 p-5">
        <Field label="What happened"><select className={inputCls} value={d.kind} onChange={(e) => setD({ ...d, kind: e.target.value })}>{(kinds.data ?? []).map((k) => <option key={k.kind} value={k.kind}>{k.label}</option>)}</select></Field>
        <Field label="Date"><input type="date" className={inputCls} value={d.date} onChange={(e) => setD({ ...d, date: e.target.value })} /></Field>
        <Field label="Reference" hint="Invoice number. Saving the same invoice again updates it instead of adding it twice."><input className={inputCls} value={d.ref} onChange={(e) => setD({ ...d, ref: e.target.value })} /></Field>
        <Field label="Note"><input className={inputCls} value={d.note} onChange={(e) => setD({ ...d, note: e.target.value })} /></Field>
      </div>
      <div className="flex flex-wrap items-center gap-3 mb-3">
        <input className={`${inputCls} w-full sm:w-72`} type="search" placeholder="Find an item…" aria-label="Find an item" value={q} onChange={(e) => setQ(e.target.value)} />
        <label className="text-sm flex items-center gap-2"><input type="checkbox" checked={onlyEntered} onChange={(e) => setOnlyEntered(e.target.checked)} /> only items on this sheet</label>
        {!delivery && <span className="text-sm text-muted">Costs are only kept for deliveries.</span>}
      </div>
      {items.error && <Notice kind="error">{items.error}</Notice>}
      {shown.length === 0 && items.data ? <p className="text-sm text-muted py-6 text-center">{onlyEntered ? "Nothing entered yet." : "No item matches."}</p> : (
        <div className="card table-box px-2"><table className="grid">
          <thead><tr><th>Item</th><th className="max-sm:hidden">Pack</th><th className="r">Cases</th><th className="r">Loose packs</th>{delivery && <th className="r">Cost / pack ($)</th>}<th className="r max-sm:hidden">Total packs</th><th className="r max-sm:hidden">In stock units</th></tr></thead>
          <tbody>{groups.map(([cat, rows]) => (<GroupRows key={cat} cat={cat} rows={rows} lines={d.lines} setLine={setLine} delivery={delivery} />))}</tbody>
        </table></div>
      )}
    </Page>
  );
}

function GroupRows({ cat, rows, lines, setLine, delivery }: { cat: string; rows: Item[]; lines: Record<string, Line>; setLine: (id: string, k: keyof Line, v: string) => void; delivery: boolean }) {
  const cell = (it: Item, k: keyof Line, label: string, placeholder?: string) => (
    <input className="cell-input num" inputMode="decimal" aria-label={`${it.name} ${label}`} placeholder={placeholder}
      value={lines[it.id]?.[k] ?? ""} onChange={(e) => setLine(it.id, k, e.target.value)} onFocus={(e) => e.target.select()} />);
  return (<>
    <tr className="section"><td colSpan={delivery ? 7 : 6}>{cat}</td></tr>
    {rows.map((it) => {
      const total = totalPacks(it, lines[it.id]);
      return (
        <tr key={it.id}>
          <td>{it.name}<span className="sm:hidden block text-xs text-muted">{it.pack_name}{Number(it.case_size) > 1 && ` · ${n(it.case_size, 0)}/case`}{total ? ` · ${n(total, 2)} packs` : ""}</span></td>
          <td className="text-muted max-sm:hidden">{it.pack_name}{Number(it.case_size) > 1 && <span className="text-faint"> · {n(it.case_size, 0)}/case</span>}</td>
          <td className="r">{Number(it.case_size) > 1 ? cell(it, "cases", "cases") : <span className="text-faint">—</span>}</td>
          <td className="r">{cell(it, "packs", "loose packs")}</td>
          {delivery && <td className="r">{cell(it, "cost", "cost per pack", it.pack_cost ? n(it.pack_cost) : "")}</td>}
          <td className={`r num max-sm:hidden ${total ? "" : "text-faint"}`}>{total ? n(total, 2) : "—"}</td>
          <td className="r num text-muted max-sm:hidden">{total ? `${n(total * Number(it.pack_size), 0)} ${it.stock_unit}` : ""}</td>
        </tr>
      );
    })}
  </>);
}
