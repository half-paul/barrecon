import { useContext, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type CountDetail, type CountLine, dt, groupBy } from "../lib/api";
import { useReadOnly } from "../lib/auth";
import { CountBarContext } from "../lib/countbar";
import { Bottle, Button, Empty, Flash, Loading, Nil, Notice, Page, inputCls, useAsync } from "../lib/ui";

/** ISO instant -> value for a datetime-local input, in the browser’s time. */
const localInput = (iso: string) => { const t = new Date(iso); return new Date(t.getTime() - t.getTimezoneOffset() * 60000).toISOString().slice(0, 16); };

export default function CountEntry() {
  const viewOnly = useReadOnly("counts");
  const { id } = useParams();
  const nav = useNavigate();
  const sess = useAsync(() => api.get<CountDetail>(`/api/counts/${id}`), [id]);
  const [edits, setEdits] = useState<Edits>({});
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const dirty = Object.keys(edits).length > 0;
  const timer = useRef<number | undefined>(undefined);

  const save = async (then?: () => void) => {
    if (!sess.data || !dirty) { then?.(); return; }
    setSaving(true);
    try {
      // a blank qty goes as nulls: the item is not counted and posting ignores it
      const lines = Object.entries(edits).map(([item_id, v]) => ({ item_id, ...toPacks(v) }));
      await api.put(`/api/counts/${id}/lines`, lines);
      setEdits({}); sess.reload(); then?.();
    } catch (e) { setMsg({ kind: "error", text: (e as Error).message }); } finally { setSaving(false); }
  };
  // autosave 1.5 s after the last keystroke
  useEffect(() => { if (!dirty) return; window.clearTimeout(timer.current); timer.current = window.setTimeout(() => save(), 1500); return () => window.clearTimeout(timer.current); }, [edits]); // eslint-disable-line react-hooks/exhaustive-deps

  const post = () => save(async () => {
    if (!confirm("Post this count? It becomes the official count for this date and can only be corrected by a new count.")) return;
    try { await api.post(`/api/counts/${id}/post`); nav("/counts"); } catch (e) { setMsg({ kind: "error", text: (e as Error).message }); }
  });

  /** Drops the draft for good; the pending autosave goes first so it can’t write to a count that is gone. */
  const cancel = async () => {
    if (!confirm("Cancel this count? The draft and every number entered in it are deleted. This can’t be undone.")) return;
    window.clearTimeout(timer.current);
    try { await api.del(`/api/counts/${id}`); nav("/counts"); } catch (e) { setMsg({ kind: "error", text: (e as Error).message }); }
  };

  // The header swaps Start count for these while this draft is open. The ref keeps them on the latest edits.
  const setCountBar = useContext(CountBarContext);
  const actions = useRef({ post, cancel });
  actions.current = { post, cancel };
  const editing = sess.data?.status === "draft" && !viewOnly;
  useEffect(() => {
    if (!editing) return;
    setCountBar({ post: () => actions.current.post(), cancel: () => actions.current.cancel(), busy: saving });
    return () => setCountBar(null);
  }, [editing, saving, setCountBar]);

  const setDate = async (value: string) => {
    if (!value) return;
    try { await api.patch(`/api/counts/${id}`, { counted_at: new Date(value).toISOString() }); sess.reload(); setMsg({ kind: "ok", text: `Count date set to ${dt(new Date(value).toISOString())}.` }); }
    catch (e) { setMsg({ kind: "error", text: (e as Error).message }); }
  };

  const [moveTo, setMoveTo] = useState("");
  const [reason, setReason] = useState("");
  /** A posted count is never rewritten: the server posts a new version at the new time and keeps this one as history. */
  const moveCount = async () => {
    if (!sess.data || !moveTo) return;
    const to = new Date(moveTo).toISOString();
    if (!confirm(`Move this count from ${dt(sess.data.counted_at)} to ${dt(to)}? A new version is posted at the new time; this one is kept as superseded history.`)) return;
    try {
      const r = await api.patch<CountDetail>(`/api/counts/${id}`, { counted_at: to, reason: reason || null });
      setMoveTo(""); setReason("");
      nav(`/counts/${r.id}`);
      setMsg({ kind: "ok", text: `Count moved to ${dt(r.counted_at)}. The earlier version is kept in the history below.` });
    } catch (e) { setMsg({ kind: "error", text: (e as Error).message }); }
  };

  /** One item fixed on a posted count: the server posts a new version with that one line changed and keeps this one as history. */
  const [fix, setFix] = useState<{ item_id: string; value: string; reason: string } | null>(null);
  const fixRef = useRef<HTMLDivElement>(null);
  const startFix = (l: CountLine) => {
    setFix({ item_id: l.item_id, value: valueOf(l, {}), reason: "" });
    fixRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  };
  const correct = async () => {
    const l = sess.data?.lines.find((x) => x.item_id === fix?.item_id);
    if (!sess.data || !fix || !l) return;
    const now = blank(fix.value) ? "not counted (taken out of the count)" : `${fix.value} ${l.pack_name ?? "packs"}`;
    if (!confirm(`Correct ${l.name} on the count from ${dt(sess.data.counted_at)} to ${now}? A new version of the count is posted with only this change; this one is kept as superseded history.`)) return;
    try {
      const r = await api.post<CountDetail>(`/api/counts/${id}/corrections`, { item_id: fix.item_id, ...toPacks(fix.value), reason: fix.reason.trim() || null });
      setFix(null);
      nav(`/counts/${r.id}`);
      setMsg({ kind: "ok", text: `${l.name} corrected. The earlier version is kept in the history below.` });
    } catch (e) { setMsg({ kind: "error", text: (e as Error).message }); }
  };

  const [find, setFind] = useState("");
  const [show, setShow] = useState<"all" | "todo" | "done">("all");
  const s = sess.data;
  const counted = (l: CountLine) => !blank(valueOf(l, edits));
  const needle = find.trim().toLowerCase();
  const visible = (s?.lines ?? []).filter((l) => (show === "all" || (show === "done") === counted(l))
    && (!needle || l.name.toLowerCase().includes(needle) || l.sku.toLowerCase().includes(needle) || (l.category ?? "").toLowerCase().includes(needle)));
  const groups = useMemo(() => groupBy(visible, (l) => l.category ?? "Uncategorised"), [visible]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!s) return <Page title="Count">{sess.error ? <Notice kind="error">{sess.error}</Notice> : <Loading rows={10} />}</Page>;
  const ro = s.status !== "draft" || viewOnly;
  const canFix = s.status === "posted" && !viewOnly;
  const onFix = canFix ? startFix : undefined;
  const fixLine = s.lines.find((l) => l.item_id === fix?.item_id);
  const totalCounted = s.lines.filter(counted).length;

  return (
    <Page area="counts" title={`Count · ${dt(s.counted_at)}`} actions={<>
      <Link className="text-sm underline" to="/counts">All counts</Link>
      {!ro && <label className="flex items-center gap-2 text-sm" title="When the stock was counted. Change it to back-date this count.">
        <span className="text-muted">Counted at</span>
        <input type="datetime-local" className={inputCls} key={s.counted_at} defaultValue={localInput(s.counted_at)} onBlur={(e) => e.target.value !== localInput(s.counted_at) && setDate(e.target.value)} />
      </label>}
      {!ro && <span className="text-sm text-muted">{saving ? "Saving…" : dirty ? "Unsaved changes" : "Saved"}</span>}
    </>}>
      {msg && <Flash kind={msg.kind} onClose={() => setMsg(null)}>{msg.text}</Flash>}
      {ro && <Notice>This count is {s.status}{s.posted_at ? ` (posted ${dt(s.posted_at)})` : ""}.{" "}
        {s.status === "deleted" ? "It is kept as history; variance and on-hand don’t use it."
          : s.status === "superseded" && s.superseded_by
          ? <>It was replaced by a <Link className="underline" to={`/counts/${s.superseded_by}`}>newer version</Link>.</>
          : canFix ? "To fix an item, use Correct one item below or the correct link next to it."
          : "To correct the numbers, start a new count at the same date and time."}</Notice>}
      {canFix && (
        <div ref={fixRef} className="card p-4 mb-4 flex flex-wrap items-end gap-3 text-sm">
          <label className="flex flex-col gap-1"><span className="text-muted">Correct one item</span>
            <select className={inputCls} value={fix?.item_id ?? ""}
              onChange={(e) => { const l = s.lines.find((x) => x.item_id === e.target.value); setFix(l ? { item_id: l.item_id, value: valueOf(l, {}), reason: fix?.reason ?? "" } : null); }}>
              <option value="">Choose an item…</option>
              {s.lines.map((l) => <option key={l.item_id} value={l.item_id}>{l.name}</option>)}
            </select></label>
          <label className="flex flex-col gap-1"><span className="text-muted">New qty{fixLine ? ` (${fixLine.pack_name ?? "packs"})` : ""}</span>
            <input className={`${inputCls} num w-24`} inputMode="decimal" disabled={!fix} value={fix?.value ?? ""} placeholder="blank = out"
              onChange={(e) => { const t = typed(e.target.value); if (t !== null && fix) setFix({ ...fix, value: t }); }} /></label>
          <label className="flex flex-col gap-1 grow max-w-md"><span className="text-muted">Reason (optional)</span>
            <input className={inputCls} disabled={!fix} value={fix?.reason ?? ""} placeholder="e.g. 45 cans, saved with the wrong pack size"
              onChange={(e) => fix && setFix({ ...fix, reason: e.target.value })} /></label>
          <Button onClick={correct} disabled={!fix}>Correct item</Button>
          {fix && <button type="button" className="text-sm underline text-muted" onClick={() => setFix(null)}>Cancel</button>}
          <span className="text-xs text-muted basis-full">Qty in packs, like the count itself (5.7 is 5 whole and an open one seven tenths full). Posts a new version with only this item changed and recalculates its stock units from the item’s current pack size; this version stays as history. Leave the qty blank to take the item out of the count.</span>
        </div>
      )}
      {s.status === "posted" && (
        <div className="card p-4 mb-4 flex flex-wrap items-end gap-3 text-sm">
          <label className="flex flex-col gap-1"><span className="text-muted">Move to date and time</span>
            <input type="datetime-local" className={inputCls} value={moveTo || localInput(s.counted_at)} onChange={(e) => setMoveTo(e.target.value)} /></label>
          <label className="flex flex-col gap-1 grow max-w-md"><span className="text-muted">Reason (optional)</span>
            <input className={inputCls} value={reason} placeholder="e.g. counted on the 17th, entered later" onChange={(e) => setReason(e.target.value)} /></label>
          <Button onClick={moveCount} disabled={!moveTo || moveTo === localInput(s.counted_at)}>Change date</Button>
          <span className="text-xs text-muted basis-full">Posts a new version with the same numbers at the new time. This version stays as history.</span>
        </div>
      )}
      {s.versions.length > 1 && (
        <details className="mb-4 text-sm" open={s.status !== "posted"}>
          <summary className="cursor-pointer">{s.versions.length} versions of this count</summary>
          <div className="table-box mt-2"><table className="grid">
            <thead><tr><th>Counted at</th><th>Status</th><th>Posted</th><th>Note</th></tr></thead>
            <tbody>{s.versions.map((v) => (
              <tr key={v.id} className={v.status === "superseded" || v.status === "deleted" ? "text-muted" : ""}>
                <td>{v.id === s.id ? <b>{dt(v.counted_at)}</b> : <Link className="underline" to={`/counts/${v.id}`}>{dt(v.counted_at)}</Link>}</td>
                <td>{v.status}</td><td>{v.posted_at ? dt(v.posted_at) : <Nil />}</td><td className="text-muted">{v.note}</td></tr>))}</tbody>
          </table></div>
        </details>
      )}
      {!ro && <div className="flex items-center gap-3 mb-3" title={`${totalCounted} of ${s.lines.length} items counted`}>
        <div className="flex-1 h-1.5 bg-line rounded-chip overflow-hidden"><div className="h-full bg-accent" style={{ width: `${s.lines.length ? (totalCounted / s.lines.length) * 100 : 0}%` }} /></div>
        <span className="mono text-sm">{totalCounted}/{s.lines.length}</span>
      </div>}
      {!ro && <p className="text-sm text-muted mb-4 max-w-[75ch]">Enter the quantity in packs as one number: 5.7 is 5 whole packs and an open one seven tenths full. Enter 0 if there is none; items left blank are not part of the count. Changes save by themselves.</p>}
      <div className="flex flex-wrap items-center gap-3 mb-3">
        <input type="search" className={`${inputCls} w-full sm:w-72`} placeholder="Find an item…" aria-label="Find an item" value={find} onChange={(e) => setFind(e.target.value)} />
        <div role="group" aria-label="Show" className="inline-flex rounded-control border border-line overflow-hidden text-sm">
          {([["all", "All"], ["todo", "Not counted"], ["done", "Counted"]] as const).map(([k, label]) => (
            <button key={k} type="button" aria-pressed={show === k} onClick={() => setShow(k)}
              className={`px-3 py-1.5 ${show === k ? "bg-raised text-ink font-medium" : "bg-surface text-muted hover:text-ink"}`}>{label}</button>))}
        </div>
      </div>
      {visible.length === 0 && <Empty>{s.lines.length ? "No item matches." : "This count has no items."}</Empty>}
      {visible.length > 0 && (<>
        <div className="hidden md:block table-box"><table className="grid">
          <thead><tr><th>Item</th><th>Pack</th><th className="r">Qty</th><th></th><th className="r">In stock units</th></tr></thead>
          <tbody>
            {groups.map(([cat, lines]) => (<Group key={cat} cat={cat} lines={lines} ro={ro} edits={edits} setEdits={setEdits} onFix={onFix} />))}
          </tbody>
        </table></div>
        <div className="md:hidden flex flex-col gap-4">
          {groups.map(([cat, lines]) => (
            <section key={cat}>
              <h2 className="label mb-2">{cat}</h2>
              <div className="flex flex-col border-t border-line-strong">{lines.map((l) => <CountCard key={l.item_id} l={l} ro={ro} edits={edits} setEdits={setEdits} onFix={onFix} />)}</div>
            </section>))}
        </div>
      </>)}
      {!ro && <div className="flex justify-end mt-5"><Button onClick={post} disabled={saving}>Post count</Button></div>}
    </Page>
  );
}

type Edits = Record<string, string>;
const trim = (v: string) => (v.includes(".") ? v.replace(/\.?0+$/, "") : v);
/** Not counted yet: nothing entered, so totals stay blank instead of reading as a zero count. */
const blank = (v: string) => v === "" || v === ".";
/** What the input shows: blank when the item has no line yet, else whole and open pack as one number. */
const valueOf = (l: CountLine, edits: Edits) => {
  if (edits[l.item_id] !== undefined) return edits[l.item_id];
  if (l.packs_full === null && l.packs_partial === null) return "";
  return trim((Number(l.packs_full ?? 0) + Number(l.packs_partial ?? 0)).toFixed(4));
};
/** The one number back into the stored parts: 5.7 is 5 whole and 0.7 of the open pack. Split as text to keep float noise out. */
const toPacks = (v: string) => {
  if (blank(v)) return { packs_full: null, packs_partial: null };
  const [whole, tenths] = v.split(".");
  return { packs_full: whole || "0", packs_partial: tenths ? `0.${tenths}` : "0" };
};
/** Typed text as a qty, a comma counting as the decimal point; null when it is not a number, so the keystroke is dropped. */
const typed = (v: string) => { const t = v.trim().replace(",", "."); return /^\d*\.?\d*$/.test(t) ? t : null; };
const TENTHS = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9];
type FixProp = { onFix?: (l: CountLine) => void };
/** Stock units the count holds: what was saved for a posted count, else packs × pack size. `stale` when the saved
 * number no longer matches packs × the item’s current pack size (saved while the pack size was different). */
const stockUnits = (l: CountLine, v: string, ro: boolean) => {
  const calc = (Number(v) || 0) * Number(l.pack_size);
  const saved = ro && l.qty != null && !blank(v) ? Number(l.qty) : null;
  return { units: saved ?? calc, stale: saved != null && Math.abs(saved - calc) > 1e-6, calc };
};
const fixLink = (l: CountLine, onFix: FixProp["onFix"]) => onFix &&
  <button type="button" className="ml-2 text-xs underline text-muted hover:text-ink" aria-label={`Correct ${l.name}`} onClick={() => onFix(l)}>correct</button>;

/** One item as a card for phones: one qty box with a whole-pack stepper, and tap-a-tenth to set the open pack in that same qty. */
function CountCard({ l, ro, edits, setEdits, onFix }: { l: CountLine; ro: boolean; edits: Edits; setEdits: React.Dispatch<React.SetStateAction<Edits>> } & FixProp) {
  const v = valueOf(l, edits);
  const su = stockUnits(l, v, ro);
  const qty = Number(v) || 0, whole = Math.floor(qty), open = qty - whole;
  const decimals = v.includes(".") ? v.slice(v.indexOf(".")) : "";
  const keg = (l.pack_name ?? "").toLowerCase().includes("keg");
  const set = (next: string) => setEdits((x) => ({ ...x, [l.item_id]: next }));
  const step = "ctl w-11 h-11 rounded-control border border-line-strong text-lg hover:bg-raised disabled:opacity-55";
  return (
    <div className="py-3 border-b border-line">
      <div className="flex items-baseline justify-between gap-2 mb-2">
        <div className="font-medium">{l.name}{fixLink(l, onFix)}</div>
        <div className={`text-xs shrink-0 num ${blank(v) ? "text-accent" : "text-muted"}`}>{blank(v) ? "not counted" : `${qty.toFixed(1)} ${l.pack_name ?? "packs"}`}</div>
      </div>
      {su.stale && <div className="text-xs text-danger mb-2">Saved as {su.units.toLocaleString("en-CA", { maximumFractionDigits: 2 })} {l.stock_unit}; at today’s pack size that is {su.calc.toLocaleString("en-CA", { maximumFractionDigits: 2 })}. Correct it to recalculate.</div>}
      <div className="flex items-center gap-2 mb-2">
        <span className="text-xs text-muted w-14">Qty</span>
        <button type="button" className={step} disabled={ro || whole <= 0} aria-label={`One less ${l.name}`} onClick={() => set(`${whole - 1}${decimals}`)}>−</button>
        <input className={`${inputCls} num w-20 h-11 text-center text-lg`} disabled={ro} inputMode="decimal" aria-label={`${l.name} qty`} value={v} onChange={(ev) => { const t = typed(ev.target.value); if (t !== null) set(t); }} onFocus={(ev) => ev.target.select()} />
        <button type="button" className={step} disabled={ro} aria-label={`One more ${l.name}`} onClick={() => set(`${whole + 1}${decimals}`)}>+</button>
        {l.stock_unit !== "each" && <span className="ml-auto"><Bottle fraction={open} keg={keg} /></span>}
      </div>
      {l.stock_unit !== "each" && (
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted w-14">Open</span>
          <div role="radiogroup" aria-label={`${l.name} open pack`} className="grid grid-cols-5 gap-1 flex-1">
            {TENTHS.map((t) => {
              const on = !blank(v) && Math.abs(open - t) < 0.001;
              return (
              <button key={t} type="button" role="radio" aria-checked={on} disabled={ro} onClick={() => set(t === 0 ? String(whole) : `${whole}.${Math.round(t * 10)}`)}
                className={`h-11 rounded-control text-sm num border ${on ? "bg-accent text-accent-ink border-accent font-semibold" : "border-line-strong text-muted"}`}>
                {t === 0 ? "0" : `.${Math.round(t * 10)}`}</button>);
            })}
          </div>
        </div>)}
    </div>
  );
}

function Group({ cat, lines, ro, edits, setEdits, onFix }: { cat: string; lines: CountLine[]; ro: boolean; edits: Edits; setEdits: React.Dispatch<React.SetStateAction<Edits>> } & FixProp) {
  return (<>
    <tr className="section"><td colSpan={5}>{cat}</td></tr>
    {lines.map((l) => {
      const v = valueOf(l, edits);
      const qty = Number(v) || 0;
      const keg = (l.pack_name ?? "").toLowerCase().includes("keg");
      const su = stockUnits(l, v, ro);
      return (
        <tr key={l.item_id}>
          <td>{l.name}{fixLink(l, onFix)}</td><td className="text-muted">{l.pack_name}</td>
          <td className="r"><input className="cell-input num" disabled={ro} inputMode="decimal" aria-label={`${l.name} qty`} value={v} onChange={(ev) => { const t = typed(ev.target.value); if (t !== null) setEdits((x) => ({ ...x, [l.item_id]: t })); }} onFocus={(ev) => ev.target.select()} /></td>
          <td className="w-6">{l.stock_unit === "each" ? null : <Bottle fraction={qty - Math.floor(qty)} keg={keg} />}</td>
          <td className={`r num ${su.stale ? "text-danger" : "text-muted"}`}
            title={su.stale ? `Saved as ${su.units} ${l.stock_unit}, but ${v} × today’s pack size (${Number(l.pack_size)}) is ${su.calc}. Correct this item to recalculate.` : undefined}>
            {blank(v) ? "" : <>{su.units.toLocaleString("en-CA", { maximumFractionDigits: 0 })}{su.stale && " ⚠"}</>}</td>
        </tr>
      );
    })}
  </>);
}
