import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type Settings, type VariancePeriod, type VarianceReport, type VarianceRow, d, day, dayRanges, n } from "../lib/api";
import { Empty, Loading, Notice, Page, inputCls, useAsync } from "../lib/ui";

// Ledger layout from docs/food-inventory-ai-handoff, in the app's Night Shift style. Wording follows that brief:
// Should be left = opening + in − out − recipe usage − recorded waste; Actually left = closing count;
// Difference = actually left − should be left (negative = short). The backend's variance is the same
// number with the opposite sign (more gone than sales explain = positive).

type Num = string | number | null;
type Kind = "short" | "over" | "matches" | "not_counted";
type Filter = "all" | Exclude<Kind, "not_counted">;
type ViewKey = "variance_view_added" | "variance_view_removed";
const FILTERS: [Filter, string][] = [["all", "All"], ["short", "Short"], ["over", "Over"], ["matches", "Matches"]];
const BASE: Record<string, [string, number]> = { ml: ["L", 1000], g: ["kg", 1000], each: ["pcs", 1] };

type Line = VarianceRow & { available: number | null; shouldLeft: number | null; left: number | null; diff: number | null; kind: Kind };

function derive(x: VarianceRow): Line {
  const available = x.opening == null ? null : Number(x.opening) + Number(x.received) - Number(x.removed);
  const shouldLeft = available == null ? null : available - Number(x.should_use);
  const left = x.closing == null ? null : Number(x.closing);
  const diff = shouldLeft == null || left == null ? null : left - shouldLeft;
  const kind: Kind = diff == null ? "not_counted" : Math.abs(diff) < 1e-9 ? "matches" : diff < 0 ? "short" : "over";
  return { ...x, available, shouldLeft, left, diff, kind };
}

export default function Variance() {
  const [params, setParams] = useSearchParams();
  const periods = useAsync(() => api.get<VariancePeriod[]>("/api/variance/periods"));
  const pick = params.get("p") ?? "";
  const [opening, closing] = pick ? pick.split(":") : ["", ""];
  const report = useAsync(() => api.get<VarianceReport>(`/api/variance${opening ? `?opening=${opening}&closing=${closing}` : ""}`), [pick]);
  const [packs, setPacks] = useState(true);
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const settings = useAsync(() => api.get<Settings>("/api/settings"));
  const [picked, setPicked] = useState<Partial<Record<ViewKey, string[]>>>({});
  const [choosing, setChoosing] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const r = report.data;
  const usePacks = (x: VarianceRow) => packs && Number(x.pack_size) > 1;
  const unitOf = (x: VarianceRow) => usePacks(x) ? x.pack_name || "pack" : (BASE[x.stock_unit]?.[0] ?? x.stock_unit);
  const q = (v: Num | number, x: VarianceRow, signed = false) => {
    if (v == null) return "—";
    const div = usePacks(x) ? Number(x.pack_size) : (BASE[x.stock_unit]?.[1] ?? 1);
    const y = Number(v) / div;
    return `${signed && y > 0 ? "+" : y < 0 ? "−" : ""}${n(Math.abs(y), 2)} ${unitOf(x)}`;
  };

  // Not-counted items stay out of the list until added by hand; counted items stay in until removed. Saved in settings.
  const viewList = (k: ViewKey) => picked[k] ?? settings.data?.[k] ?? [];
  const inView = (x: Line) => x.kind === "not_counted" ? viewList("variance_view_added").includes(x.item_id) : !viewList("variance_view_removed").includes(x.item_id);
  const saveView = (patch: Partial<Record<ViewKey, string[]>>) => {
    setPicked((p) => ({ ...p, ...patch }));
    api.put("/api/settings", patch).then(() => setSaveError(null)).catch((e) => setSaveError(String(e.message ?? e)));
  };
  const toggleView = (x: Line) => {
    const k: ViewKey = x.kind === "not_counted" ? "variance_view_added" : "variance_view_removed";
    const list = viewList(k);
    saveView({ [k]: list.includes(x.item_id) ? list.filter((id) => id !== x.item_id) : [...list, x.item_id] });
  };

  const all = (r?.rows ?? []).map(derive);
  const shown = all.filter(inView);
  const count = (k: Kind) => shown.filter((x) => x.kind === k).length;
  const notCounted = all.filter((x) => x.kind === "not_counted").length;
  const off = shown.filter((x) => x.status === "off").length;
  const needle = search.trim().toLowerCase();
  const lines = shown.filter((x) => (filter === "all" || x.kind === filter)
    && (!needle || x.name.toLowerCase().includes(needle) || x.sku.toLowerCase().includes(needle) || (x.category ?? "").toLowerCase().includes(needle)));
  const allCollapsed = lines.length > 0 && lines.every((x) => collapsed.has(x.item_id));
  const setOpen = (id: string, isOpen: boolean) => setCollapsed((s) => {
    if (isOpen !== s.has(id)) return s;
    const c = new Set(s); if (isOpen) c.delete(id); else c.add(id); return c;
  });

  return (
    <Page title="Variance" sub={r ? `${d(r.opening.counted_at)} count → ${d(r.closing.counted_at)} count` : "Sold × recipe against what the counts say is left"}>

        {periods.data && periods.data.length > 0 && (
          <div className="flex flex-wrap items-center gap-3 mb-3">
            <select className={inputCls} aria-label="Count period" value={pick || `${periods.data[0].opening_id}:${periods.data[0].closing_id}`}
              onChange={(e) => setParams(e.target.value ? { p: e.target.value } : {}, { replace: true })}>
              {periods.data.map((p) => <option key={p.closing_id} value={`${p.opening_id}:${p.closing_id}`}>{d(p.opening_at)} → {d(p.closing_at)}</option>)}
            </select>
            <input className={`${inputCls} w-full sm:w-56`} type="search" placeholder="Search items…" aria-label="Search stock items" value={search} onChange={(e) => setSearch(e.target.value)} />
            <div role="group" aria-label="Filter by difference" className="inline-flex flex-wrap rounded-[10px] border border-line overflow-hidden text-sm">
              {FILTERS.map(([k, label]) => (
                <button key={k} type="button" aria-pressed={filter === k} onClick={() => setFilter(k)}
                  className={`px-3 py-1.5 ${filter === k ? "bg-raised text-ink font-medium" : "bg-surface text-muted hover:text-ink"}`}>
                  {label}{r && k !== "all" ? <span className="mono text-xs text-faint ml-1">{count(k)}</span> : null}
                </button>))}
            </div>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={packs} onChange={(e) => setPacks(e.target.checked)} /> bottles / kegs instead of L</label>
          </div>)}

        {report.loading && !report.data && !report.error && <Loading rows={8} />}
        {report.error && (periods.data?.length === 0
          ? <Empty>Variance compares two posted counts, and there aren't two yet. It also needs the Swype sales for every day between them and recipes for what sold.
              <br /><Link className="underline text-accent" to="/">See what's still missing on the Overview</Link></Empty>
          : <Notice kind="error">{report.error}</Notice>)}

        {r && (<>
          <div className="flex flex-wrap gap-x-7 gap-y-3 py-4 text-xs text-muted">
            <span><b className="text-lg font-medium text-ink mr-1 num">{shown.length}</b>stock items</span>
            <span className="text-danger"><b className="text-lg font-medium mr-1 num">{count("short")}</b>short</span>
            <span><b className="text-lg font-medium text-ink mr-1 num">{count("over")}</b>over</span>
            <span><b className="text-lg font-medium text-ink mr-1 num">{count("matches")}</b>match</span>
            <span><b className="text-lg font-medium text-ink mr-1 num">{notCounted}</b>not counted</span>
            <span><b className={`text-lg font-medium mr-1 num ${off ? "text-danger" : "text-ink"}`}>{off}</b>off by {n(r.threshold_pct, 0)}% or more</span>
            {r.unmapped.length > 0 && <span><b className="text-lg font-medium text-ink mr-1 num">{r.unmapped.length}</b>sold with no recipe</span>}
          </div>
          <Coverage r={r} />

          {all.length > 0 && <Chart lines={all} />}
          {all.length > 0 && (
            <div className="flex items-center justify-between gap-3 mb-2">
              <button type="button" className="text-sm text-muted hover:text-ink underline" aria-expanded={choosing} onClick={() => setChoosing((v) => !v)}>
                {choosing ? "Done" : `Add / remove items (${shown.length} of ${all.length})`}
              </button>
              {lines.length > 0 && <button type="button" className="text-sm text-muted hover:text-ink underline"
                onClick={() => setCollapsed(allCollapsed ? new Set() : new Set(lines.map((x) => x.item_id)))}>
                {allCollapsed ? "Expand all" : "Collapse all"}
              </button>}
            </div>)}
          {saveError && <Notice kind="error">Couldn't save the items in view: {saveError}</Notice>}
          {choosing && <ItemPicker items={all} label="Find an item to add or remove" none="" checked={inView} onToggle={toggleView}
            clear={viewList("variance_view_added").length + viewList("variance_view_removed").length > 0
              ? ["Reset to counted items", () => saveView({ variance_view_added: [], variance_view_removed: [] })] : null} />}
          {lines.length === 0 ? <Empty>{!all.length ? "No stock moved and no recipe usage in this period." : shown.length ? "No stock item matches this filter." : "No items in view. Use “Add / remove items” to pick some."}</Empty>
            : lines.map((x) => <Section key={x.item_id} x={x} q={q} open={!collapsed.has(x.item_id)} onToggle={(o) => setOpen(x.item_id, o)} threshold={r.threshold_pct} />)}

          {r.unmapped.length > 0 && (
            <section className="lg-box p-5">
              <h2 className="font-semibold mb-1">Sold with no recipe</h2>
              <p className="text-xs text-muted mb-3">These Swype items aren't in recipe usage, so stock items they use will look short. <Link className="underline" to="/recipes?tab=todo">Add recipes</Link> or mark them as taking no stock.</p>
              <ul className="text-sm grid gap-x-6 gap-y-0.5 sm:grid-cols-2 lg:grid-cols-3">{r.unmapped.slice(0, 60).map((u) => (
                <li key={u.code} className="flex justify-between gap-2"><span>{u.name} <span className="text-muted text-xs">{u.category}</span></span><span className="num text-muted">{n(u.sold, 0)}</span></li>))}</ul>
              {r.unmapped.length > 60 && <p className="text-xs text-muted mt-2">and {r.unmapped.length - 60} more</p>}
            </section>)}

          <footer className="text-xs text-muted leading-relaxed mt-4">
            “Actually left” is the closing physical count. “Recipe usage” is estimated from Swype sales × recipes, not measured.
            A difference is not proof of theft, over-pouring or waste. One sale can appear under several stock items; don't add units sold across sections.
          </footer>
        </>)}
    </Page>
  );
}

/** Searchable checklist of stock items, shared by the list above and the chart. */
function ItemPicker({ items, label, none, checked, onToggle, clear }: { items: Line[]; label: string; none: string; checked: (x: Line) => boolean; onToggle: (x: Line) => void; clear: [string, () => void] | null }) {
  const [find, setFind] = useState("");
  const words = find.toLowerCase().split(/\s+/).filter(Boolean);
  const options = items.filter((x) => words.every((w) => [x.name, x.sku, x.category ?? ""].some((f) => f.toLowerCase().includes(w))));
  return (
    <div className="border border-line rounded-lg p-3 mb-4">
      <div className="flex flex-wrap items-center gap-3 mb-2">
        <input className={`${inputCls} w-full sm:w-56 text-sm`} type="search" placeholder="Find an item…" aria-label={label} value={find} onChange={(e) => setFind(e.target.value)} />
        {clear && <button type="button" className="text-xs text-muted hover:text-ink underline" onClick={clear[1]}>{clear[0]}</button>}
      </div>
      {options.length === 0 ? <p className="text-xs text-muted">{items.length ? "No item matches." : none}</p> : (
        <ul className="grid gap-x-6 gap-y-0.5 sm:grid-cols-2 lg:grid-cols-3 max-h-64 overflow-y-auto text-sm">{options.map((x) => (
          <li key={x.item_id}><label className="flex items-center gap-2 py-0.5 cursor-pointer">
            <input type="checkbox" checked={checked(x)} onChange={() => onToggle(x)} />
            <span className="truncate">{x.name} <span className="text-muted text-xs">{[x.category, x.kind === "not_counted" && "not counted"].filter(Boolean).join(" · ")}</span></span>
          </label></li>))}
        </ul>)}
    </div>);
}

const pct = (v: number | null, of: number | null) => v == null || of == null || of <= 0 ? null : (v / of) * 100;
const p1 = (v: number) => `${n(v, 1)}%`;

/** Expected vs actual remaining, each as % of that item's own available stock, on one shared scale.
 *  Only items picked by hand are charted; the pick is saved in settings so it survives logins. */
function Chart({ lines }: { lines: Line[] }) {
  const settings = useAsync(() => api.get<Settings>("/api/settings"));
  const [picked, setPicked] = useState<string[] | null>(null);
  const [choosing, setChoosing] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const chosen = new Set(picked ?? settings.data?.variance_chart_items ?? []);
  // items with no available stock (opening + in − out ≤ 0) have no percentage, so they can't be charted or picked
  const stocked = lines.filter((x) => x.available != null && x.available > 0);
  const save = (list: string[]) => {
    setPicked(list);
    api.put("/api/settings", { variance_chart_items: list }).then(() => setSaveError(null)).catch((e) => setSaveError(String(e.message ?? e)));
  };
  const toggle = (id: string) => save(chosen.has(id) ? [...chosen].filter((c) => c !== id) : [...chosen, id]);

  const rows = stocked.filter((x) => chosen.has(x.item_id)).map((x) => ({ x, e: pct(x.shouldLeft, x.available), a: pct(x.left, x.available) }));
  const vals = rows.flatMap((r) => [r.e, r.a]).filter((v): v is number => v != null);
  const lo = Math.min(0, ...vals), hi = Math.max(100, ...vals), span = hi - lo;
  const odd = rows.filter((r) => [r.e, r.a].some((v) => v != null && (v < 0 || v > 100)));
  const pos = (v: number) => ((v - lo) / span) * 100;
  const bar = (v: number | null, cls: string) => (
    <div className="lg-track">
      {lo < 0 && <span className="lg-zero" style={{ left: `${pos(0)}%` }} />}
      {v != null && <span className={`lg-fill bar ${cls}`} style={{ left: `${pos(Math.min(0, v))}%`, width: `${(Math.abs(v) / span) * 100}%` }} />}
    </div>);
  return (
    <section className="lg-box p-5 mb-5">
      <div className="flex items-center justify-between gap-2.5 flex-wrap mb-4">
        <h2 className="font-semibold">Stock remaining / available stock</h2>
        <div className="flex items-center gap-3.5 text-[11px] text-muted">
          <span><b className="inline-block w-2.5 h-2 mr-1.5 rounded-sm lg-e" />Should be left</span>
          <span><b className="inline-block w-2.5 h-2 mr-1.5 rounded-sm lg-a" />Actually left</span>
          <button type="button" className="text-xs text-muted hover:text-ink underline" aria-expanded={choosing} onClick={() => setChoosing((v) => !v)}>
            {choosing ? "Done" : `Choose items (${rows.length})`}
          </button>
        </div>
      </div>
      {saveError && <Notice kind="error">Couldn't save the chosen items: {saveError}</Notice>}
      {choosing && <ItemPicker items={stocked} label="Find an item to chart" none="No item has available stock in this period."
        checked={(x) => chosen.has(x.item_id)} onToggle={(x) => toggle(x.item_id)} clear={rows.length > 0 ? ["Clear all", () => save([])] : null} />}
      {settings.data && rows.length === 0 && !choosing && <p className="text-xs text-muted py-2">No items chosen for this chart. Use “Choose items” to pick the ones to follow; your choice is kept for next time.</p>}
      {rows.length > 0 && (<>
      {rows.map(({ x, e, a }) => (
        <div key={x.item_id} className="grid grid-cols-[minmax(0,110px)_1fr_auto] sm:grid-cols-[minmax(0,180px)_1fr_120px] gap-3.5 items-center text-[11px] py-1">
          <span className="truncate" title={x.name}>{x.name}</span>
          <div className="grid gap-1" role="img"
            aria-label={`${x.name}: should be left ${e == null ? "unknown" : p1(e)}, actually left ${a == null ? "not counted" : p1(a)} of available stock`}>
            {bar(e, "lg-e")}{bar(a, "lg-a")}
          </div>
          <span className="text-right num text-muted whitespace-nowrap">
            {`${e == null ? "—" : p1(e)} / ${a == null ? "Not counted" : p1(a)}`}
          </span>
        </div>))}
      <div className="grid grid-cols-[minmax(0,110px)_1fr_auto] sm:grid-cols-[minmax(0,180px)_1fr_120px] gap-3.5 mt-1.5 text-[10px] text-muted">
        <span /><div className="relative h-3">
          {[lo, 0, 50, 100, hi].filter((t, i, s) => s.indexOf(t) === i).map((t) => (
            <span key={t} className="absolute -translate-x-1/2 first:translate-x-0 last:-translate-x-full" style={{ left: `${pos(t)}%` }}>{n(t, 0)}%</span>))}
        </div><span />
      </div>
      <p className="text-[11px] text-muted mt-2">Each item uses its own available stock (opening + in − out) as the denominator for both bars.</p>
      {odd.length > 0 && <p className="text-[11px] text-danger mt-1">Check the data: {odd.map((r) => r.x.name).join(", ")} {odd.length === 1 ? "is" : "are"} below 0% or above 100% of available stock, so the scale runs {n(lo, 0)}% to {n(hi, 0)}%.</p>}
      </>)}
    </section>
  );
}

const CHIP: Partial<Record<VarianceRow["status"], [string, string]>> = {
  unit_issue: ["Unit issue", "bg-danger-soft text-danger"],
  missing_opening: ["No opening count", "bg-raised text-muted"],
  missing_closing: ["No closing count", "bg-raised text-muted"],
  no_recipe: ["No recipe uses it", "bg-raised text-accent"],
};

function Section({ x, q, open, onToggle, threshold }: { x: Line; q: (v: Num | number, x: VarianceRow, signed?: boolean) => string; open: boolean; onToggle: (open: boolean) => void; threshold: Num }) {
  const tone = x.kind === "short" ? "text-danger" : x.kind === "over" ? "text-accent" : x.kind === "matches" ? "text-ok" : "text-muted";
  const diffText = x.diff == null ? "—" : x.kind === "matches" ? "Matches" : `${q(Math.abs(x.diff), x)} ${x.kind}`;
  const chip = x.status === "off" ? [`Off ≥ ${n(threshold, 0)}%`, "bg-danger-soft text-danger"] : CHIP[x.status];
  const flow = [`Opening ${q(x.opening, x)}`];
  if (Number(x.received)) flow.push(`+ in ${q(x.received, x)}`);
  if (Number(x.removed)) flow.push(`− out ${q(x.removed, x)}`);
  flow.push(`− recipe usage ${q(x.expected, x)}`);
  if (Number(x.explained)) flow.push(`− recorded waste ${q(x.explained, x)}`);
  return (
    <details className="lg-box" open={open} onToggle={(e) => onToggle(e.currentTarget.open)}>
      <summary className="px-[18px] py-[15px] grid gap-3.5 items-center md:grid-cols-[1fr_1.35fr]">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap font-semibold text-base">
            <span className="lg-arrow inline-block text-muted text-lg leading-none transition-transform" aria-hidden>›</span>
            <span>{x.name}</span>
            {chip && <span className={`lg-chip ${chip[1]}`}>{chip[0]}</span>}
          </div>
          <div className="text-[11px] text-muted mt-1 num">{x.category ? `${x.category} · ` : ""}{flow.join(" ")}</div>
        </div>
        <div className="lg-balances grid grid-cols-[1fr_1fr_1.1fr] gap-2 num">
          <div><span className="block text-[11px] text-muted mb-0.5">Should be left</span><strong className="font-medium text-base whitespace-nowrap">{q(x.shouldLeft, x)}</strong></div>
          <div><span className="block text-[11px] text-muted mb-0.5">Actually left</span><strong className="font-medium text-base whitespace-nowrap">{x.left == null ? "Not counted" : q(x.left, x)}</strong></div>
          <div className={tone}><span className="block text-[11px] text-muted mb-0.5">Difference</span><strong className="font-medium text-[13px] whitespace-nowrap">{diffText}</strong></div>
        </div>
      </summary>
      <div className="px-[18px] pb-2.5 border-t border-line overflow-x-auto">
        {x.no_recipe ? <p className="text-xs text-muted py-3">No recipe uses {x.name}, so sales can't explain any of its use. Add it to the recipe of every Swype item that takes it (a bottle of beer too: 1 each).</p>
          : x.contributions.length === 0 ? <p className="text-xs text-muted py-3">No sold Swype item with a recipe uses {x.name} in this period.</p> : (
          <table className="lg-links" aria-label={`Menu items using ${x.name}`}>
            <thead><tr><th>Menu item</th><th>Units sold</th><th>Per item</th><th>Recipe usage</th></tr></thead>
            <tbody>{x.contributions.map((c, i) => (
              <tr key={i}><td>{c.name} <span className="text-muted mono text-[10px]">{c.code}</span></td><td>{n(c.sold, 2)}</td><td>{n(c.portion_qty, 2)} {c.portion_unit}</td><td>{q(c.used, x)}</td></tr>))}
            </tbody>
            <tfoot>
              <tr><td>Total recipe usage</td><td /><td /><td>{q(x.expected, x)}</td></tr>
              {Number(x.explained) > 0 && <tr><td className="!font-normal text-muted">Recorded breakage / expired (Deliveries &amp; stock)</td><td /><td /><td className="!font-normal">{q(x.explained, x)}</td></tr>}
            </tfoot>
          </table>)}
        {x.issues.map((t) => <p key={t} className="text-xs text-danger py-1">{t}. Fix the item's unit on Stock items, or the portion on Recipes.</p>)}
      </div>
    </details>
  );
}

function Coverage({ r }: { r: VarianceReport }) {
  const notes: string[] = [];
  const missing = r.missing_sales_days;
  if (r.sales_periods.length === 0) notes.push("No Swype sales are imported for this period, so recipe usage is zero. Import the Item Sales report for these days.");
  else if (missing.length) notes.push(`Missing Swype sales for ${missing.length} day${missing.length === 1 ? "" : "s"}: ${dayRanges(missing)}. Recipe usage is too low for those days; import them under Imports.`);
  for (const p of r.straddling_periods) notes.push(`Sales for ${day(p.from)} – ${day(p.to)} cross a count date and are left out. Import sales week by week, matching the count days.`);
  if (r.no_movements_recorded) notes.push("No deliveries were recorded in this period. If stock arrived, enter it under Deliveries, or items will look over.");
  return (<>
    {notes.map((t) => <Notice key={t}>{t}</Notice>)}
  </>);
}
