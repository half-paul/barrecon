import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type DeliveryDetail, type Item, dt, n } from "../lib/api";
import { useReadOnly } from "../lib/auth";
import { Button, Combobox, Field, Flash, Lightbox, Loading, Notice, Page, inputCls, useAsync } from "../lib/ui";
import { statusChip } from "./Deliveries";

// One delivery: a receipt thumbnail up top, what it puts into stock below. Changes save by themselves;
// nothing reaches stock until "Post".

type Line = { key: number; text: string; receipt_sku: string | null; receipt_qty: string; line_total: string; item_id: string | null; itemText: string; packs: string; skip: boolean; matched_by: string | null };
type Form = { store: string; occurred_at: string; receipt_no: string; subtotal: string; tax: string; total: string; note: string; lines: Line[] };

const localInput = (iso: string | null) => { if (!iso) return ""; const t = new Date(iso); return new Date(t.getTime() - t.getTimezoneOffset() * 60000).toISOString().slice(0, 16); };
const s = (v: number | string | null | undefined) => v == null ? "" : String(v);
const num = (v: string) => v.trim() === "" ? null : v.trim();
const label = (it: { name: string; sku: string }) => `${it.name} · ${it.sku}`;
let lineKey = 0; // stable row keys, so removing a line doesn’t hand its box’s state to the next row

function toForm(d: DeliveryDetail): Form {
  return {
    store: s(d.store), occurred_at: localInput(d.occurred_at), receipt_no: s(d.receipt_no), subtotal: s(d.subtotal), tax: s(d.tax), total: s(d.total), note: s(d.note),
    lines: d.lines.map((l) => ({ key: ++lineKey, text: l.text, receipt_sku: l.receipt_sku, receipt_qty: s(l.receipt_qty), line_total: s(l.line_total), item_id: l.item_id, itemText: l.item ? label(l.item) : "",
      packs: s(l.packs), skip: l.skip, matched_by: l.matched_by })),
  };
}

function toBody(f: Form) {
  return {
    store: f.store, store_address: null as string | null, occurred_at: f.occurred_at ? new Date(f.occurred_at).toISOString() : null, receipt_no: f.receipt_no,
    subtotal: num(f.subtotal), tax: num(f.tax), total: num(f.total), note: f.note,
    lines: f.lines.filter((l) => l.text.trim()).map((l) => ({ text: l.text.trim(), receipt_sku: l.receipt_sku, receipt_qty: num(l.receipt_qty) ?? "1", line_total: num(l.line_total),
      item_id: l.skip ? null : l.item_id, packs: l.skip ? null : num(l.packs), skip: l.skip })),
  };
}

export default function DeliveryReview() {
  const viewOnly = useReadOnly("deliveries");
  const { id } = useParams();
  const nav = useNavigate();
  const items = useAsync(() => api.get<Item[]>("/api/items"));
  const stores = useAsync(() => api.get<string[]>("/api/deliveries/stores"));
  const [d, setD] = useState<DeliveryDetail | null>(null);
  const [f, setF] = useState<Form | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState("");
  const [zoom, setZoom] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  const load = (x: DeliveryDetail) => { setD(x); setF(toForm(x)); setDirty(false); };
  useEffect(() => { api.get<DeliveryDetail>(`/api/deliveries/${id}`).then(load).catch((e) => setErr(e.message)); }, [id]);

  const byLabel = useMemo(() => new Map((items.data ?? []).map((it) => [label(it), it])), [items.data]);
  const byId = useMemo(() => new Map((items.data ?? []).map((it) => [it.id, it])), [items.data]);
  const options = useMemo(() => (items.data ?? []).filter((it) => it.is_active).map(label), [items.data]);

  const save = async (form: Form) => {
    window.clearTimeout(timer.current);
    try {
      const x = await api.put<DeliveryDetail>(`/api/deliveries/${id}`, toBody(form));
      setD(x); setDirty(false);
      // keep what’s being typed; take back only what the server worked out (matches stay as entered)
      setF((cur) => cur && cur === form ? { ...cur, lines: cur.lines.map((l, i) => ({ ...l, matched_by: x.lines[i]?.matched_by ?? l.matched_by })) } : cur);
      return x;
    } catch (e) { setMsg({ kind: "error", text: (e as Error).message }); return null; }
  };
  const change = (next: Form) => {
    setF(next); setDirty(true);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => save(next), 900);
  };
  useEffect(() => () => window.clearTimeout(timer.current), []);

  if (err) return <Page title="Delivery"><Notice kind="error">{err}</Notice></Page>;
  if (!d || !f) return <Page title="Delivery"><Loading rows={10} /></Page>;
  const ro = d.status !== "draft" || viewOnly;
  const c = d.checks;
  const setH = (k: keyof Omit<Form, "lines">, v: string) => change({ ...f, [k]: v });
  const setL = (i: number, patch: Partial<Line>) => change({ ...f, lines: f.lines.map((l, j) => j === i ? { ...l, ...patch } : l) });
  const pickItem = (i: number, text: string) => {
    const it = byLabel.get(text);
    const l = f.lines[i];
    setL(i, { itemText: text, item_id: it?.id ?? null, packs: it && !l.packs ? l.receipt_qty || "1" : l.packs });
  };
  const addLine = () => change({ ...f, lines: [...f.lines, { key: ++lineKey, text: "", receipt_sku: null, receipt_qty: "1", line_total: "", item_id: null, itemText: "", packs: "", skip: false, matched_by: null }] });
  const removeLine = (i: number) => change({ ...f, lines: f.lines.filter((_, j) => j !== i) });

  const act = async (what: "post" | "reopen" | "void" | "read", confirmText?: string) => {
    if (confirmText && !confirm(confirmText)) return;
    setBusy(what);
    try {
      if (dirty && !(await save(f))) return;
      const x = await api.post<DeliveryDetail>(`/api/deliveries/${id}/${what}`);
      load(x);
      const done = { post: "Posted: the items are in stock from the purchase time.", reopen: "Reopened: its stock is taken back out until you post it again.", void: "Voided: its stock is taken back out.", read: x.read_error ? "" : "Read again." }[what];
      if (x.read_error && what === "read") setMsg({ kind: "error", text: x.read_error }); else setMsg({ kind: "ok", text: done });
      stores.reload();
    } catch (e) { setMsg({ kind: "error", text: (e as Error).message }); } finally { setBusy(""); }
  };
  const remove = async () => {
    if (!confirm("Delete this draft and its receipt?")) return;
    try { await api.del(`/api/deliveries/${id}`); nav("/deliveries"); } catch (e) { setMsg({ kind: "error", text: (e as Error).message }); }
  };
  const attach = async (file: File) => {
    try { load(await api.upload<DeliveryDetail>(`/api/deliveries/${id}/receipt`, file, "PUT")); } catch (e) { setMsg({ kind: "error", text: (e as Error).message }); }
  };

  const toMatch = c.unmatched.length + c.no_packs.length;
  const price = new Map(c.prices.map((p) => [p.position, p]));
  const receiptUrl = `/api/deliveries/${id}/receipt`;
  const money = (v: number | null) => v == null ? "n/a" : `$${n(v, 2)}`;

  return (
    <Page area="deliveries" title={d.store || "Delivery"} sub={<span className="inline-flex items-center gap-2">{d.occurred_at ? dt(d.occurred_at) : "no date yet"} {statusChip(d.status)}</span>} actions={<>
      <Link className="text-sm underline" to="/deliveries">All deliveries</Link>
      {!ro && <span className="text-sm text-muted">{dirty ? "Saving…" : "Saved"}</span>}
      {!ro && d.can_read && <Button kind="quiet" disabled={!!busy} onClick={() => act("read", "Read the receipt again? The store, date, totals and every line are replaced by what is read.")}>{busy === "read" ? "Reading…" : "Read again"}</Button>}
      {!ro && !d.posted_at && <Button kind="danger" onClick={remove}>Delete draft</Button>}
      {d.status === "posted" && <Button kind="quiet" edit disabled={!!busy} onClick={() => act("reopen", "Reopen this delivery to correct it? Its stock is taken back out until you post it again.")}>Reopen to edit</Button>}
      {d.status !== "void" && (d.status === "posted" || d.posted_at) && <Button kind="danger" disabled={!!busy} onClick={() => act("void", "Void this delivery? Its stock is taken back out; the delivery stays as history.")}>Void</Button>}
      {!ro && <Button disabled={!!busy || toMatch > 0} title={toMatch ? "Match every line to a stock item, or mark it not stock" : undefined} onClick={() => act("post")}>{busy === "post" ? "Posting…" : "Post delivery"}</Button>}
    </>}>
      {msg && <Flash kind={msg.kind} onClose={() => setMsg(null)}>{msg.text}</Flash>}
      {d.read_error && !ro && <Notice kind="error">The receipt couldn’t be read: {d.read_error}</Notice>}
      {c.possible_duplicate && <Notice kind="error">This looks like a delivery already entered (same store, time and total). <Link className="underline" to={`/deliveries/${c.possible_duplicate.id}`}>Open the other one</Link> ({c.possible_duplicate.status}).</Notice>}
      {d.status === "void" && <Notice>This delivery is void; it adds nothing to stock.</Notice>}

      <section className="card p-3 mb-4 flex items-center gap-3" aria-label="Receipt">
        {d.has_receipt && d.receipt_type === "application/pdf" && (
          <a href={receiptUrl} target="_blank" rel="noreferrer" aria-label="Open the receipt PDF"
            className="w-12 h-16 shrink-0 grid place-items-center rounded-control border border-line bg-paper text-xs text-muted hover:text-ink">PDF</a>)}
        {d.has_receipt && d.receipt_type !== "application/pdf" && (
          <button type="button" className="w-12 h-16 shrink-0" onClick={() => setZoom(true)} aria-label="Show the receipt full size">
            <img src={receiptUrl} alt="Receipt" className="w-full h-full object-cover rounded-control border border-line bg-paper" />
          </button>)}
        <span className="text-sm text-muted">{d.has_receipt ? "Click the thumbnail to view the receipt" : "No receipt attached."}</span>
        {!ro && (
          <label className="ml-auto shrink-0 inline-flex items-center px-3 py-1.5 rounded-control text-sm bg-surface border border-line cursor-pointer hover:bg-raised">
            {d.has_receipt ? "Replace receipt" : "Attach receipt"}
            <input type="file" accept="image/*,application/pdf" className="hidden" onChange={(e) => { const x = e.target.files?.[0]; if (x) attach(x); e.currentTarget.value = ""; }} />
          </label>)}
      </section>

      <div className="min-w-0">
        <fieldset disabled={ro} className="card grid grid-cols-2 sm:grid-cols-3 gap-3 p-4 mb-4">
          <div className="col-span-2"><Field label="Store">
            {ro ? <input className={inputCls} value={f.store} readOnly /> : <Combobox aria-label="Store" value={f.store} onChange={(v) => setH("store", v)} options={stores.data ?? []} placeholder="Where it was bought" />}
          </Field></div>
          <Field label="Bought at"><input type="datetime-local" className={inputCls} value={f.occurred_at} onChange={(e) => setH("occurred_at", e.target.value)} /></Field>
          <Field label="Receipt no."><input className={inputCls} value={f.receipt_no} onChange={(e) => setH("receipt_no", e.target.value)} /></Field>
          <Field label="Subtotal ($)"><input className={`${inputCls} num`} inputMode="decimal" value={f.subtotal} onChange={(e) => setH("subtotal", e.target.value)} /></Field>
          <Field label="Tax ($)"><input className={`${inputCls} num`} inputMode="decimal" value={f.tax} onChange={(e) => setH("tax", e.target.value)} /></Field>
          <Field label="Total ($)"><input className={`${inputCls} num`} inputMode="decimal" value={f.total} onChange={(e) => setH("total", e.target.value)} /></Field>
          <div className="col-span-2"><Field label="Note"><input className={inputCls} value={f.note} onChange={(e) => setH("note", e.target.value)} /></Field></div>
        </fieldset>

        <div className="card p-4 mb-4 text-sm flex flex-wrap gap-x-6 gap-y-2" aria-live="polite">
          <span className={c.totals_ok ? "text-ok" : "text-danger"}>
            {c.totals_ok ? "✓ " : "✗ "}Lines {money(c.lines_sum)}{d.tax != null && <> + tax {money(d.tax)}</>}{d.total != null && <> vs total {money(d.total)}</>}
            {c.total_diff != null && Math.abs(c.total_diff) > 0.05 && <> ({c.total_diff > 0 ? "+" : ""}{n(c.total_diff, 2)})</>}
            {c.subtotal_diff != null && Math.abs(c.subtotal_diff) > 0.05 && <>; subtotal off by {n(c.subtotal_diff, 2)}</>}
            {c.missing_amounts > 0 && <>; {c.missing_amounts} line{c.missing_amounts === 1 ? "" : "s"} without an amount</>}
          </span>
          {!ro && <span className={toMatch ? "text-danger" : "text-ok"}>{toMatch ? `${toMatch} line${toMatch === 1 ? "" : "s"} to match` : "✓ Every line matched"}</span>}
          {c.price_alerts > 0 && <span className="text-accent">{c.price_alerts} pack cost{c.price_alerts === 1 ? "" : "s"} moved {n(c.threshold_pct, 0)} % or more</span>}
        </div>

        <div className="table-box"><table className="grid">
          <thead><tr><th>Receipt line</th><th className="r">Qty</th><th className="r">Amount</th><th>Stock item</th><th className="r">Packs</th><th className="r">$ / pack</th><th title="Not stock: bags, deposits, snacks">Not stock</th>{!ro && <th></th>}</tr></thead>
          <tbody>
            {f.lines.map((l, i) => {
              const it = l.item_id ? byId.get(l.item_id) : undefined;
              const p = price.get(i);
              const packCost = !l.skip && Number(l.packs) > 0 && l.line_total.trim() !== "" ? Number(l.line_total) / Number(l.packs) : null;
              const need = !ro && !l.skip && (!l.item_id || !(Number(l.packs) > 0));
              return (
                <tr key={l.key} className={l.skip ? "text-muted" : need ? "bg-amber-soft/50" : ""}>
                  <td className="min-w-40 w-1/3">{ro ? l.text : <input className="cell-input !text-left !w-full" aria-label={`Line ${i + 1} text`} value={l.text} onChange={(e) => setL(i, { text: e.target.value })} />}
                    {l.receipt_sku && <span className="block text-xs text-muted mt-0.5">SKU {l.receipt_sku}</span>}</td>
                  <td className="r w-16">{ro ? n(l.receipt_qty) : <input className="cell-input num" inputMode="decimal" aria-label={`Line ${i + 1} quantity`} value={l.receipt_qty} onChange={(e) => setL(i, { receipt_qty: e.target.value })} />}</td>
                  <td className="r w-24">{ro ? n(l.line_total, 2) : <input className="cell-input num" inputMode="decimal" aria-label={`Line ${i + 1} amount`} value={l.line_total} onChange={(e) => setL(i, { line_total: e.target.value })} />}</td>
                  <td className="min-w-52">
                    {l.skip ? <span className="text-xs">not stock</span> : ro ? (it ? label(it) : "") : (
                      <Combobox aria-label={`Line ${i + 1} stock item`} value={l.itemText} onChange={(v) => pickItem(i, v)} options={options} placeholder="Pick the stock item" />)}
                    {l.matched_by === "memory" && !ro && <span className="block text-xs text-muted mt-0.5">filled in from an earlier receipt</span>}
                    {l.matched_by === "sku" && !ro && <span className="block text-xs text-muted mt-0.5">matched by SKU</span>}
                  </td>
                  <td className="r w-24">
                    {!l.skip && (ro ? n(l.packs) : <input className="cell-input num" inputMode="decimal" aria-label={`Line ${i + 1} packs`} value={l.packs} onChange={(e) => setL(i, { packs: e.target.value })} />)}
                    {!l.skip && it && Number(l.packs) > 0 && <span className="block text-xs text-muted whitespace-nowrap">{it.pack_name ?? "packs"} · {n(Number(l.packs) * Number(it.pack_size), 0)} {it.stock_unit}</span>}
                  </td>
                  <td className="r num w-24 whitespace-nowrap">
                    {packCost != null && n(packCost, 2)}
                    {p?.alert && <span className="block text-xs text-accent" title={`was $${n(p.previous, 2)} a pack`}>{p.change_pct! > 0 ? "▲" : "▼"} {n(Math.abs(p.change_pct!), 0)} % vs ${n(p.previous, 2)}</span>}
                  </td>
                  <td className="text-center"><input type="checkbox" disabled={ro} aria-label={`Line ${i + 1} is not stock`} checked={l.skip} onChange={(e) => setL(i, { skip: e.target.checked })} /></td>
                  {!ro && <td><button type="button" className="text-muted hover:text-danger px-1" aria-label={`Remove line ${i + 1}`} onClick={() => removeLine(i)}>✕</button></td>}
                </tr>);
            })}
            {f.lines.length === 0 && <tr><td colSpan={8} className="text-center text-muted py-6">No lines yet{d.has_receipt && !d.can_read ? "; type them from the receipt" : ""}.</td></tr>}
          </tbody>
        </table></div>
        {!ro && <div className="mt-3 flex flex-wrap gap-3 items-center">
          <Button kind="quiet" onClick={addLine}>Add line</Button>
          <span className="text-xs text-muted">Packs are what goes into stock: a 24-pack line counted in cans is 24. What you set is remembered for this store’s next receipt.</span>
        </div>}
      </div>
      {zoom && <Lightbox src={receiptUrl} alt="Receipt" onClose={() => setZoom(false)} />}
    </Page>
  );
}
