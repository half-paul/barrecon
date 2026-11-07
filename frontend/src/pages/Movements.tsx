import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type ImportSummary, type Movement, n } from "../lib/api";

type Delivery = { key: string; date: string; kind: string; ref: string; lines: Movement[] };
const day = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString("en-CA", { dateStyle: "medium" });
import { useReadOnly } from "../lib/auth";
import { Empty, FileButton, Flash, Loading, Notice, Page, inputCls, useAsync } from "../lib/ui";

export default function Movements() {
  const viewOnly = useReadOnly("deliveries");
  const [showAll, setShowAll] = useState(false);
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const moves = useAsync(() => api.get<Movement[]>(`/api/movements?include_inactive=${showAll}&limit=5000`), [showAll]);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const retract = async (m: Movement) => { if (confirm(`Retract ${m.name} from ${m.external_ref}?`)) { await api.post(`/api/movements/${m.id}/retract`); moves.reload(); } };
  const retractAll = async (g: Delivery) => {
    const active = g.lines.filter((m) => m.status === "active");
    if (!active.length || !confirm(`Retract the whole delivery ${g.ref} of ${day(g.date)} (${active.length} items)? Each line is kept as retracted history.`)) return;
    setBusy(true);
    try { for (const m of active) await api.post(`/api/movements/${m.id}/retract`); setMsg({ kind: "ok", text: `Retracted ${g.ref}: ${active.length} items.` }); }
    catch (e) { setMsg({ kind: "error", text: (e as Error).message }); } finally { setBusy(false); moves.reload(); }
  };
  const toggle = (k: string) => setOpen((s) => { const c = new Set(s); if (c.has(k)) c.delete(k); else c.add(k); return c; });

  // one row per delivery: lines with the same local date, kind and reference (an invoice), newest first
  const matches = (m: Movement) => words.every((w) => [m.name, m.sku, m.external_ref, m.kind, m.note ?? ""].some((f) => f.toLowerCase().includes(w)));
  const groups = new Map<string, Delivery>();
  for (const m of moves.data ?? []) {
    const date = new Date(m.occurred_at).toLocaleDateString("en-CA");
    const key = `${date}|${m.kind}|${m.external_ref}`;
    if (!groups.has(key)) groups.set(key, { key, date, kind: m.kind, ref: m.external_ref, lines: [] });
    groups.get(key)!.lines.push(m);
  }
  const shown = [...groups.values()]
    .map((g) => ({ ...g, lines: words.length ? g.lines.filter(matches) : g.lines }))
    .filter((g) => g.lines.length)
    .sort((a, b) => b.date.localeCompare(a.date) || a.ref.localeCompare(b.ref));
  const upload = async (f: File) => {
    setBusy(true);
    try {
      const r = await api.upload<ImportSummary>("/api/movements/import", f);
      setMsg(r.status === "ok" ? { kind: "ok", text: `${f.name}: ${r.inserted} new, ${r.updated} updated, ${r.duplicate} already recorded.` }
                               : { kind: "error", text: `Nothing imported. ${r.rows.map((x) => x.message).join(" · ")}` });
      moves.reload();
    } catch (e) { setMsg({ kind: "error", text: (e as Error).message }); } finally { setBusy(false); }
  };

  return (
    <Page area="deliveries" title="Stock movements" actions={<>
      <Link className="text-sm underline" to="/deliveries">Deliveries</Link>
      <a className="text-sm underline" href="/api/movements/template.xlsx">Download movements sheet</a>
      <FileButton label="Upload movements sheet" onFile={upload} busy={busy} />
      <Link to="/movements/new" className="inline-flex items-center px-3.5 py-1.5 rounded-[10px] text-sm bg-accent text-accent-ink font-semibold hover:bg-accent-deep">Record a delivery</Link>
    </>}>
      {msg && <Flash kind={msg.kind} onClose={() => setMsg(null)}>{msg.text}</Flash>}
      <div className="flex flex-wrap items-center gap-4 mb-3">
        <input className={`${inputCls} w-full sm:w-72`} placeholder="Filter by item, reference or note" aria-label="Filter movements" value={q} onChange={(e) => setParams(e.target.value ? { q: e.target.value } : {}, { replace: true })} />
        <label className="text-sm flex items-center gap-1"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> show superseded and retracted</label>
      </div>
      {moves.loading && !moves.data && !moves.error && <Loading />}
      {moves.error && <Notice kind="error">{moves.error}</Notice>}
      {moves.data && moves.data.length === 0 && <Empty>Nothing recorded yet. Use “Record a delivery” to enter an invoice; deliveries from your old workbook appear here after the legacy import.</Empty>}
      {moves.data && moves.data.length > 0 && shown.length === 0 && <Empty>Nothing matches “{q}”.</Empty>}
      {shown.length > 0 && (
        <div className="card table-box px-2"><table className="grid">
          <thead><tr><th>Date</th><th>Reference</th><th className="max-sm:hidden">Kind</th><th className="r">Items</th><th className="r max-sm:hidden">Packs</th><th className="r">Cost</th><th className="max-sm:hidden">Source</th><th></th></tr></thead>
          <tbody>{shown.map((g) => {
            const isOpen = open.has(g.key) || words.length > 0;
            const live = g.lines.filter((m) => m.status === "active");
            const packs = live.reduce((t, m) => t + Number(m.packs ?? 0), 0);
            const costed = live.filter((m) => m.packs && m.pack_cost);
            const cost = costed.reduce((t, m) => t + Number(m.packs) * Number(m.pack_cost), 0);
            const notes = [...new Set(g.lines.map((m) => m.note).filter(Boolean))];
            return [
              <tr key={g.key} className={`cursor-pointer ${live.length ? "" : "text-muted line-through"}`} onClick={() => toggle(g.key)}>
                <td className="whitespace-nowrap font-medium">
                  <button type="button" aria-expanded={isOpen} aria-label={`${isOpen ? "Hide" : "Show"} items of ${g.ref}`} className="inline-block w-5 text-muted" onClick={(e) => { e.stopPropagation(); toggle(g.key); }}>{isOpen ? "▾" : "▸"}</button>
                  {day(g.date)}</td>
                <td className="num">{g.ref}{notes.length > 0 && <span className="block text-xs text-muted font-sans">{notes.join(" · ")}</span>}</td>
                <td className="max-sm:hidden">{g.kind.replace("_", " ")}</td>
                <td className="r num">{live.length}{live.length !== g.lines.length && <span className="text-muted"> / {g.lines.length}</span>}</td>
                <td className="r num max-sm:hidden">{packs ? n(packs) : ""}</td>
                <td className="r num">{costed.length ? `$${n(cost, 2)}` : ""}{costed.length > 0 && costed.length < live.length && <span className="block text-xs text-muted">{live.length - costed.length} without cost</span>}</td>
                <td className="text-muted max-sm:hidden">{[...new Set(g.lines.map((m) => m.source))].join(", ")}</td>
                <td className="r">{g.lines[0].delivery_id
                  ? <Link className="text-xs underline" to={`/deliveries/${g.lines[0].delivery_id}`} onClick={(e) => e.stopPropagation()}>open delivery</Link>
                  : live.length > 0 && !viewOnly && <button className="text-danger text-xs" disabled={busy} onClick={(e) => { e.stopPropagation(); retractAll(g); }}>retract delivery</button>}</td>
              </tr>,
              isOpen && (
                <tr key={`${g.key}-items`}><td colSpan={8} className="!p-0 bg-paper/60">
                  <table className="grid">
                    <thead><tr><th className="pl-6 sm:pl-9">Item</th><th className="max-sm:hidden">SKU</th><th className="r">Packs</th><th className="r max-sm:hidden">Qty</th><th className="r max-sm:hidden">Cost / pack</th><th className="r">Line cost</th><th></th></tr></thead>
                    <tbody>{g.lines.slice().sort((a, b) => a.name.localeCompare(b.name)).map((m) => (
                      <tr key={m.id} className={m.status !== "active" ? "text-muted line-through" : ""}>
                        <td className="pl-6 sm:pl-9">{m.name}</td><td className="text-muted num max-sm:hidden">{m.sku}</td>
                        <td className="r num">{m.packs ? n(m.packs) : ""}</td><td className="r num max-sm:hidden">{n(m.qty, 0)}</td>
                        <td className="r num max-sm:hidden">{m.pack_cost ? n(m.pack_cost) : ""}</td>
                        <td className="r num">{m.packs && m.pack_cost ? `$${n(Number(m.packs) * Number(m.pack_cost), 2)}` : ""}</td>
                        <td className="r">{m.delivery_id || viewOnly ? null : m.status === "active" ? <button className="text-danger text-xs" onClick={() => retract(m)}>retract</button> : <span className="text-xs">{m.status}</span>}</td>
                      </tr>))}</tbody>
                  </table>
                </td></tr>),
            ];
          })}</tbody>
        </table></div>
      )}
    </Page>
  );
}
