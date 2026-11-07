import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type CurrentPeriod, type MenuSync, type Settings, d, dayRanges, dt, menuText, n } from "../lib/api";
import { SoldItems } from "../lib/soldItems";
import { Button, Empty, Field, FileButton, Flash, Loading, Notice, Page, inputCls, useAsync } from "../lib/ui";

type SalesPreview = { period_from: string; period_to: string; price_type: string; items: number; total_qty: number; net_amount: number; departments: { department: string; items: number; qty: number; net_amount: number }[]; menu: MenuSync };
type SalesApplied = { period_from: string; period_to: string; items: number; inserted: number; updated: number; unchanged: number; removed: number; menu: MenuSync };
type DayFetched = (SalesApplied & { status: "ok" }) | { status: "no_sales"; period_from: string; period_to: string };
type SalesPeriod = { period_from: string; period_to: string; items: number; total_qty: number; net_amount: number };
const day = (iso: string) => d(`${iso}T12:00:00`);
const money = (v: number) => `$${n(v, 2)}`;
const iso = (x: Date) => x.toLocaleDateString("en-CA");
/** Every day from..to inclusive, as YYYY-MM-DD. */
function daysBetween(from: string, to: string) {
  const out: string[] = [];
  for (let x = new Date(`${from}T12:00:00`); iso(x) <= to; x.setDate(x.getDate() + 1)) out.push(iso(x));
  return out;
}
/** Last full Monday–Sunday week, as local YYYY-MM-DD. */
function lastWeek() {
  const t = new Date();
  const sun = new Date(t.getFullYear(), t.getMonth(), t.getDate() - (t.getDay() || 7));
  return { from: iso(new Date(sun.getFullYear(), sun.getMonth(), sun.getDate() - 6)), to: iso(sun) };
}
/** The count day (0 = Mon … 6 = Sun) that closes the week holding this date. */
function weekEnd(x: string, countWeekday: number) {
  const t = new Date(`${x}T12:00:00`);
  t.setDate(t.getDate() + ((countWeekday - (t.getDay() + 6) % 7) + 7) % 7);
  return iso(t);
}

export default function Sales() {
  const periods = useAsync(() => api.get<SalesPeriod[]>("/api/imports/swype-item-sales/periods"));
  const swype = useAsync(() => api.get<{ configured: boolean; next_from: string | null; through: string;
    auto: { enabled: boolean; hour: number; last: { at: string; imported: string[]; no_sales: string[]; error: string | null } | null } }>("/api/imports/swype-item-sales/fetch"));
  const current = useAsync(() => api.get<CurrentPeriod>("/api/variance/current"));
  const settings = useAsync(() => api.get<Settings>("/api/settings"));
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [salesFile, setSalesFile] = useState<File | null>(null);
  const [salesPreview, setSalesPreview] = useState<SalesPreview | null>(null);
  const [progress, setProgress] = useState<{ done: number; total: number; day: string } | null>(null);
  const [range, setRange] = useState(lastWeek);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const reloadAll = () => { periods.reload(); swype.reload(); current.reload(); };

  const chooseSales = async (f: File) => {
    setBusy(true); setSalesPreview(null); setSalesFile(f);
    try { setSalesPreview(await api.upload<SalesPreview>("/api/imports/swype-item-sales/preview", f)); }
    catch (e) { setMsg({ kind: "error", text: (e as Error).message }); setSalesFile(null); } finally { setBusy(false); }
  };
  const applySales = async () => {
    if (!salesFile) return;
    setBusy(true);
    try {
      const r = await api.upload<SalesApplied>("/api/imports/swype-item-sales", salesFile);
      setMsg({ kind: "ok", text: `Swype sales ${day(r.period_from)} – ${day(r.period_to)}: ${r.items} items (${r.inserted} new, ${r.updated} changed, ${r.unchanged} unchanged${r.removed ? `, ${r.removed} no longer listed` : ""}). Swype menu: ${menuText(r.menu)}.` });
      setSalesPreview(null); setSalesFile(null);
    } catch (e) { setMsg({ kind: "error", text: (e as Error).message }); } finally { setBusy(false); reloadAll(); }
  };

  /** Import from Swype one day at a time so the bar can show progress. Stops at the first failure; days already imported stay. */
  const importDays = async (days: string[]) => {
    if (!days.length) return;
    setBusy(true); setSalesPreview(null); setSalesFile(null);
    const imported: SalesApplied[] = [];
    const noSales: string[] = [];
    let error: string | null = null;
    try {
      for (const [i, x] of days.entries()) {
        setProgress({ done: i, total: days.length, day: x });
        try {
          const r = await api.post<DayFetched>("/api/imports/swype-item-sales/fetch", { date_from: x, date_to: x });
          if (r.status === "no_sales") noSales.push(x); else imported.push(r);
        } catch (e) { error = `Stopped at ${day(x)}: ${(e as Error).message}`; break; }
      }
      const items = imported.reduce((t, r) => t + r.items, 0);
      const parts = [`${imported.length} of ${days.length} days imported (${items} item lines)`];
      if (noSales.length) parts.push(`no sales on ${noSales.map(day).join(", ")}`);
      const off = imported.reduce((t, r) => t + (r.menu.deactivated ?? 0), 0);
      if (off) parts.push(`${off} Swype items switched off (nothing sold in a month)`);
      const text = `Swype sales ${dayRanges(days)}: ${parts.join("; ")}.`;
      setMsg(error ? { kind: "error", text: `${text} ${error}` } : { kind: "ok", text });
    } finally { setProgress(null); setBusy(false); reloadAll(); }
  };
  const upToDate = !!swype.data?.next_from && swype.data.next_from > swype.data.through;
  const missing = current.data?.missing_sales_days ?? [];
  const cantFetch = busy || swype.data?.configured === false;

  const remove = async (from: string, to: string, label: string) => {
    if (!confirm(`Remove the Swype sales imported for ${label}? Variance for those dates will have no sales until they are imported again.`)) return;
    setBusy(true);
    try {
      const r = await api.del<{ deleted: number; periods: number; kept_partial: { period_from: string; period_to: string }[] }>(
        `/api/imports/swype-item-sales/periods?date_from=${from}&date_to=${to}`);
      const kept = r.kept_partial.length ? ` Kept ${r.kept_partial.map((x) => `${day(x.period_from)} – ${day(x.period_to)}`).join(", ")}: only partly inside the dates, widen them to remove.` : "";
      setMsg({ kind: "ok", text: `Removed ${r.periods} imported period${r.periods === 1 ? "" : "s"} (${r.deleted} item lines) for ${label}.${kept}` });
    } catch (e) { setMsg({ kind: "error", text: (e as Error).message }); } finally { setBusy(false); reloadAll(); }
  };
  const label = (p: SalesPeriod) => p.period_from === p.period_to ? day(p.period_from) : `${day(p.period_from)} – ${day(p.period_to)}`;

  // imported periods grouped into count weeks (each ends on the count day), newest first
  const cw = settings.data?.count_weekday ?? 6;
  const weeks = new Map<string, SalesPeriod[]>();
  for (const p of periods.data ?? []) {
    const k = weekEnd(p.period_to, cw);
    if (!weeks.has(k)) weeks.set(k, []);
    weeks.get(k)!.push(p);
  }
  const weekList = [...weeks.entries()].sort((a, b) => b[0].localeCompare(a[0]));
  const toggle = (k: string) => setOpen((s) => { const c = new Set(s); if (c.has(k)) c.delete(k); else c.add(k); return c; });

  return (
    <Page area="sales" title="Sales" sub="Swype item sales, imported one day at a time">
      {msg && <Flash kind={msg.kind} onClose={() => setMsg(null)}>{msg.text}</Flash>}
      {swype.data?.configured === false && <Notice kind="error">Swype login not set up. Add SWYPE_EMAIL and SWYPE_PASSWORD to .env and restart the portal.</Notice>}

      <section className="card mb-6 p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2 mb-3">
          <h2 className="font-semibold">Import from Swype</h2>
          {swype.data && <AutoStatus auto={swype.data.auto} />}
        </div>
        {current.data?.opening && (
          <div className="flex flex-wrap items-center gap-3 mb-3">
            {missing.length
              ? <><Button onClick={() => importDays(missing)} disabled={cantFetch}>Import {missing.length} missing day{missing.length === 1 ? "" : "s"}</Button>
                  <span className="text-sm text-muted">Since the count on {d(current.data.opening.counted_at)}: {dayRanges(missing)}</span></>
              : <span className="text-sm text-ok">Every day since the count on {d(current.data.opening.counted_at)} is imported.</span>}
          </div>
        )}
        {swype.data?.next_from && !upToDate && (
          <div className="flex flex-wrap items-center gap-3 mb-3">
            <Button kind="quiet" edit onClick={() => importDays(daysBetween(swype.data!.next_from!, swype.data!.through))} disabled={cantFetch}>Import new sales</Button>
            <span className="text-sm text-muted">{day(swype.data.next_from)} – {day(swype.data.through)}, after the latest imported day (today is left for tomorrow).</span>
          </div>
        )}
        <div className="flex flex-wrap items-end gap-3">
          <Field label="From"><input type="date" className={inputCls} value={range.from} max={range.to} onChange={(e) => setRange((r) => ({ ...r, from: e.target.value }))} /></Field>
          <Field label="To"><input type="date" className={inputCls} value={range.to} min={range.from} onChange={(e) => setRange((r) => ({ ...r, to: e.target.value }))} /></Field>
          <Button kind="quiet" edit onClick={() => importDays(daysBetween(range.from, range.to))} disabled={cantFetch || !range.from || !range.to}>Import these dates</Button>
          <Button kind="danger" onClick={() => remove(range.from, range.to, `${day(range.from)} – ${day(range.to)}`)} disabled={busy || !range.from || !range.to}>Remove these dates</Button>
        </div>
        {progress && (
          <div className="mt-4" role="progressbar" aria-valuemin={0} aria-valuemax={progress.total} aria-valuenow={progress.done}>
            <div className="flex justify-between text-sm text-muted mb-1">
              <span>Importing {day(progress.day)}…</span><span className="num">{progress.done} / {progress.total} days</span>
            </div>
            <div className="h-2 rounded-full bg-raised overflow-hidden">
              <div className="h-full bg-accent transition-[width] duration-300" style={{ width: `${(progress.done / progress.total) * 100}%` }} />
            </div>
          </div>
        )}
      </section>

      <details className="card mb-6 p-5">
        <summary className="cursor-pointer font-semibold">Upload a sales report file instead</summary>
        <p className="text-sm text-muted my-3">The Excel file from Swype → Reports → Item Sales Report, run for All Price Types. Every subtotal is checked before anything is written. Importing the same dates again replaces them; a file overlapping different imported dates is refused.</p>
        <FileButton label={salesFile ? "Choose a different file" : "Choose sales report"} onFile={chooseSales} busy={busy} />
        {salesPreview && (
          <div className="mt-4 text-sm">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-3">
              <div><div className="text-muted">Period</div><div>{day(salesPreview.period_from)} – {day(salesPreview.period_to)}</div></div>
              <div><div className="text-muted">Items sold</div><div className="num">{salesPreview.items}</div></div>
              <div><div className="text-muted">Quantity</div><div className="num">{n(salesPreview.total_qty, 0)}</div></div>
              <div><div className="text-muted">Net sales</div><div className="num">{money(salesPreview.net_amount)}</div></div>
            </div>
            <p className="text-muted mb-3">{salesPreview.departments.map((x) => `${x.department}: ${x.items} items, ${n(x.qty, 0)} sold, ${money(x.net_amount)}`).join(" · ")}</p>
            <p className="text-muted mb-3">Swype menu: {menuText(salesPreview.menu)}.</p>
            {salesPreview.price_type.toLowerCase() !== "all price type" && <Notice kind="error">This report was run for "{salesPreview.price_type}" only and will be refused. Run it with All Price Types.</Notice>}
            <Button onClick={applySales} disabled={busy}>Import</Button>
          </div>
        )}
      </details>

      <h2 className="font-semibold mb-2">Imported sales</h2>
      {periods.loading && !periods.data && <Loading />}
      {periods.error && <Notice kind="error">{periods.error}</Notice>}
      {periods.data && periods.data.length === 0 && <Empty>No Swype sales imported yet. Use Import from Swype above.</Empty>}
      {weekList.length > 0 && (
        <div className="card table-box px-2"><table className="grid">
          <thead><tr><th>Count week</th><th className="r">Days</th><th className="r">Item lines</th><th className="r">Qty</th><th className="r">Net sales</th><th></th></tr></thead>
          <tbody>{weekList.map(([end, ps]) => {
            const from = ps[0].period_from, to = ps[ps.length - 1].period_to;
            const shown = open.has(end);
            const days = ps.reduce((t, p) => t + daysBetween(p.period_from, p.period_to).length, 0);
            return [
              <tr key={end}>
                <td><button type="button" className="underline-offset-2 hover:underline" aria-expanded={shown} onClick={() => toggle(end)}>
                  <span aria-hidden className="inline-block w-4 text-muted">{shown ? "▾" : "▸"}</span>{day(from)} – {day(to)}</button></td>
                <td className="r num">{days}</td>
                <td className="r num">{ps.reduce((t, p) => t + p.items, 0)}</td>
                <td className="r num">{n(ps.reduce((t, p) => t + p.total_qty, 0), 0)}</td>
                <td className="r num">{money(ps.reduce((t, p) => t + p.net_amount, 0))}</td>
                <td className="r"><Button kind="quiet" edit onClick={() => remove(from, to, `${day(from)} – ${day(to)}`)} disabled={busy}>Remove week</Button></td>
              </tr>,
              ...(shown ? ps.slice().reverse().flatMap((p) => {
                const k = `items:${p.period_from}:${p.period_to}`;
                const itemsShown = open.has(k);
                return [
                  <tr key={p.period_from} className="text-muted">
                    <td className="pl-9 whitespace-nowrap"><button type="button" className="underline-offset-2 hover:underline" aria-expanded={itemsShown} onClick={() => toggle(k)}>
                      <span aria-hidden className="inline-block w-4">{itemsShown ? "▾" : "▸"}</span>{label(p)}</button></td>
                    <td /><td className="r num">{p.items}</td>
                    <td className="r num">{n(p.total_qty, 0)}</td><td className="r num">{money(p.net_amount)}</td>
                    <td className="r"><Button kind="quiet" edit onClick={() => remove(p.period_from, p.period_to, label(p))} disabled={busy}>Remove</Button></td>
                  </tr>,
                  ...(itemsShown ? [<tr key={k}><td colSpan={6} className="pl-9"><SoldItems key={`${p.items}:${p.total_qty}:${p.net_amount}`} url={`/api/imports/swype-item-sales/periods/${p.period_from}/${p.period_to}/items`} /></td></tr>] : []),
                ];
              }) : []),
            ];
          })}</tbody>
        </table></div>
      )}
    </Page>
  );
}

function AutoStatus({ auto }: { auto: { enabled: boolean; hour: number; last: { at: string; imported: string[]; no_sales: string[]; error: string | null } | null } }) {
  const at = `${String(auto.hour).padStart(2, "0")}:00`;
  if (!auto.enabled) return <span className="text-sm text-muted">Nightly import is off (<Link className="underline" to="/settings">Settings</Link>).</span>;
  const l = auto.last;
  return (
    <span className={`text-sm ${l?.error ? "text-danger" : "text-muted"}`}>
      Imports by itself every night at {at}.{" "}
      {l && (l.error ? `Last night stopped at ${l.error}.`
        : `Last run ${dt(l.at)}: ${l.imported.length ? `${l.imported.length} day${l.imported.length === 1 ? "" : "s"} imported` : "nothing new"}.`)}
    </span>
  );
}
