import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Item, groupBy, n, cents } from "../lib/api";
import { Button, Field, Flash, Help, inputCls, Nil, Notice, Page, useAsync } from "../lib/ui";

// One invoice on one sheet, like a count: every active item is listed and only the rows with a quantity are saved.
// Saving posts it as a delivery, listed under Deliveries. The unsaved sheet is kept in this browser so leaving the
// page doesn’t lose it.

type Line = { cases: string; packs: string; cost: string };
type Draft = { date: string; ref: string; store: string; note: string; lines: Record<string, Line> };
const KEY = "delivery-draft";
const today = () => new Date().toISOString().slice(0, 10);
const fresh = (): Draft => ({ date: today(), ref: "", store: "", note: "", lines: {} });
const load = (): Draft => { try { const v = localStorage.getItem(KEY); return v ? { ...fresh(), ...JSON.parse(v) } : fresh(); } catch { return fresh(); } };
const keep = (d: Draft) => { try { localStorage.setItem(KEY, JSON.stringify(d)); } catch { /* storage unavailable: the sheet still works */ } };
const totalPacks = (it: Item, l?: Line) => l ? (Number(l.cases) || 0) * Number(it.case_size) + (Number(l.packs) || 0) : 0;

export default function DeliveryEntry() {
  const items = useAsync(() => api.get<Item[]>("/api/items"));
  const [d, setD] = useState<Draft>(load);
  const [q, setQ] = useState("");
  const [onlyEntered, setOnlyEntered] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  useEffect(() => keep(d), [d]);

  const all = items.data ?? [];
  const entered = all.filter((it) => totalPacks(it, d.lines[it.id]) > 0);
  const bad = all.filter((it) => { const l = d.lines[it.id]; return l && [l.cases, l.packs, l.cost].some((v) => v.trim() !== "" && !(Number(v) >= 0)); });
  const spend = entered.reduce((t, it) => t + totalPacks(it, d.lines[it.id]) * (Number(d.lines[it.id].cost || it.pack_cost) || 0), 0);
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = all.filter((it) => (!onlyEntered || entered.includes(it)) && words.every((w) => [it.name, it.sku, it.category ?? ""].some((f) => f.toLowerCase().includes(w))));
  const groups = useMemo(() => groupBy(shown, (it) => it.category || "Uncategorised"), [shown]); // eslint-disable-line react-hooks/exhaustive-deps

  const setLine = (id: string, k: keyof Line, v: string) => setD((x) => ({ ...x, lines: { ...x.lines, [id]: { ...{ cases: "", packs: "", cost: "" }, ...x.lines[id], [k]: v } } }));
  const clear = () => { if (confirm("Clear this sheet? Everything entered on it is lost.")) { setD(fresh()); setMsg(null); } };
  const save = async () => {
    setSaving(true);
    try {
      const r = await api.post<{ outcome: "inserted" | "superseded" | "duplicate"; delivery_id: string; lines: number }>("/api/movements/batch", {
        kind: "delivery", occurred_at: new Date(`${d.date}T12:00:00`).toISOString(), external_ref: d.ref.trim(),
        store: d.store.trim() || null, note: d.note.trim() || null,
        lines: entered.map((it) => ({ item_id: it.id, packs: String(totalPacks(it, d.lines[it.id])), pack_cost: d.lines[it.id].cost.trim() ? d.lines[it.id].cost : null })),
      });
      const what = { inserted: `delivery posted, ${r.lines} items`, superseded: "delivery updated", duplicate: "already recorded, nothing changed" }[r.outcome];
      setSaved(r.delivery_id);
      setMsg({ kind: "ok", text: `${d.ref.trim()}: ${what}.` });
      setD(fresh()); setQ(""); setOnlyEntered(false);
    } catch (e) { setMsg({ kind: "error", text: (e as Error).message }); } finally { setSaving(false); }
  };

  return (
    <Page area="deliveries" title="Record a delivery" sub={entered.length ? `${entered.length} item${entered.length === 1 ? "" : "s"} on this sheet${spend ? ` · $${cents(spend)}` : ""}` : "Enter what arrived; leave the rest empty"} actions={<>
      <Link className="text-sm underline" to="/deliveries">All deliveries</Link>
      {(entered.length > 0 || d.ref) && <button className="text-sm text-muted hover:text-ink underline" onClick={clear}>Clear sheet</button>}
      <Button onClick={save} disabled={saving || !d.ref.trim() || entered.length === 0 || bad.length > 0}>{saving ? "Saving…" : `Save ${entered.length || ""} item${entered.length === 1 ? "" : "s"}`}</Button>
    </>}>
      {msg && <Flash kind={msg.kind} onClose={() => setMsg(null)}>{msg.text}{msg.kind === "ok" && saved && <> <Link className="underline" to={`/deliveries/${saved}`}>Open it</Link></>}</Flash>}
      {bad.length > 0 && <Notice kind="error">Check {bad.map((it) => it.name).join(", ")}: quantities and costs must be numbers, 0 or more.</Notice>}
      <div className="card grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 mb-6 p-5">
        <Field label="Store"><input className={inputCls} value={d.store} placeholder="BC Liquor Store" onChange={(e) => setD({ ...d, store: e.target.value })} /></Field>
        <Field label="Date"><input type="date" className={inputCls} value={d.date} onChange={(e) => setD({ ...d, date: e.target.value })} /></Field>
        <Field label="Reference" hint="Invoice number. Saving the same invoice again updates it instead of adding it twice."><input className={inputCls} value={d.ref} onChange={(e) => setD({ ...d, ref: e.target.value })} /></Field>
        <Field label="Note"><input className={inputCls} value={d.note} onChange={(e) => setD({ ...d, note: e.target.value })} /></Field>
      </div>
      <div className="flex flex-wrap items-center gap-3 mb-3">
        <input className={`${inputCls} w-full sm:w-72`} type="search" placeholder="Find an item…" aria-label="Find an item" value={q} onChange={(e) => setQ(e.target.value)} />
        <label className="text-sm flex items-center gap-2"><input type="checkbox" checked={onlyEntered} onChange={(e) => setOnlyEntered(e.target.checked)} /> only items on this sheet</label>
      </div>
      {items.error && <Notice kind="error">{items.error}</Notice>}
      {shown.length === 0 && items.data ? <p className="text-sm text-muted py-6 text-center">{onlyEntered ? "Nothing entered yet." : "No item matches."}</p> : (
        <div className="table-box"><table className="grid">
          <thead><tr><th>Item</th><th className="max-sm:hidden">Pack<Help term="pack" /></th><th className="r">Cases<Help term="case_size" /></th><th className="r">Loose packs<Help term="loose_packs" /></th><th className="r">Cost / pack ($)<Help term="cost_per_pack" /></th><th className="r max-sm:hidden">Total packs<Help text="Total packs = cases × per case + loose packs." /></th><th className="r max-sm:hidden">In stock units<Help term="stock_unit" /></th></tr></thead>
          <tbody>{groups.map(([cat, rows]) => (<GroupRows key={cat} cat={cat} rows={rows} lines={d.lines} setLine={setLine} />))}</tbody>
        </table></div>
      )}
    </Page>
  );
}

function GroupRows({ cat, rows, lines, setLine }: { cat: string; rows: Item[]; lines: Record<string, Line>; setLine: (id: string, k: keyof Line, v: string) => void }) {
  const cell = (it: Item, k: keyof Line, label: string, placeholder?: string) => (
    <input className="cell-input num" inputMode="decimal" aria-label={`${it.name} ${label}`} placeholder={placeholder}
      value={lines[it.id]?.[k] ?? ""} onChange={(e) => setLine(it.id, k, e.target.value)} onFocus={(e) => e.target.select()} />);
  return (<>
    <tr className="section"><td colSpan={7}>{cat}</td></tr>
    {rows.map((it) => {
      const total = totalPacks(it, lines[it.id]);
      return (
        <tr key={it.id}>
          <td>{it.name}<span className="sm:hidden block text-xs text-muted">{it.pack_name}{Number(it.case_size) > 1 && ` · ${n(it.case_size, 0)}/case`}{total ? ` · ${n(total, 2)} packs` : ""}</span></td>
          <td className="text-muted max-sm:hidden">{it.pack_name}{Number(it.case_size) > 1 && <span className="text-faint"> · {n(it.case_size, 0)}/case</span>}</td>
          <td className="r">{Number(it.case_size) > 1 ? cell(it, "cases", "cases") : <Nil />}</td>
          <td className="r">{cell(it, "packs", "loose packs")}</td>
          <td className="r">{cell(it, "cost", "cost per pack", it.pack_cost ? n(it.pack_cost) : "")}</td>
          <td className={`r num max-sm:hidden ${total ? "" : "text-faint"}`}>{total ? n(total, 2) : <Nil />}</td>
          <td className="r num text-muted max-sm:hidden">{total ? `${n(total * Number(it.pack_size), 0)} ${it.stock_unit}` : ""}</td>
        </tr>
      );
    })}
  </>);
}
