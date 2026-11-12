import { type ReactNode, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type ChartStyle, type CountPage, type PostedCount, type Settings, type UserPrefs, type VariancePool, type VarianceReport, type VarianceRow, d, day, dayRanges, dt, n } from "../lib/api";
import { Empty, Loading, Notice, Page, inputCls, useAsync } from "../lib/ui";
import VarianceScatter, { sellingName } from "./VarianceScatter";
import { Icon, type IconName } from "../lib/icons";

// Ledger layout from docs/food-inventory-ai-handoff, in the app’s Night Shift style. Wording follows that brief:
// Should be left = opening + in − out − recipe usage − recorded waste; Actually left = closing count;
// Difference = actually left − should be left (negative = short). The backend’s variance is the same
// number with the opposite sign (more gone than sales explain = positive).

type Num = string | number | null;
type Kind = "short" | "over" | "matches" | "not_counted";
type Filter = "all" | Exclude<Kind, "not_counted">;
type ViewKey = "variance_view_added" | "variance_view_removed";
const PAGE = 10;  // counts listed in From / To; more load ten at a time
const MORE = "__more";
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

/** A pool's totals as one line: same formula as an item, in the pool's unit. */
function derivePool(p: VariancePool) {
  const available = Number(p.opening) + Number(p.received) - Number(p.removed);
  const shouldLeft = available - Number(p.should_use);
  const left = p.actual == null ? null : Number(p.closing);
  const diff = left == null ? null : left - shouldLeft;
  const kind: Kind = diff == null ? "not_counted" : Math.abs(diff) < 1e-9 ? "matches" : diff < 0 ? "short" : "over";
  return { available, shouldLeft, left, diff, kind };
}

/** Pools are always shown in L / kg / pcs: their items can come in different bottles or boxes. */
const qUnit = (v: Num | number, unit: string, signed = false) => {
  if (v == null) return "n/a";
  const [label, div] = BASE[unit] ?? [unit, 1];
  const y = Number(v) / div;
  return `${signed && y > 0 ? "+" : y < 0 ? "−" : ""}${n(Math.abs(y), 2)} ${label}`;
};

/** Nothing to chase: the count matches, or the difference is under the threshold. */
const isQuiet = (x: Line) => x.kind === "matches" || x.status === "ok";

export default function Variance() {
  const [params, setParams] = useSearchParams();
  const pick = params.get("p") ?? "";
  const [opening, closing] = pick ? pick.split(":") : ["", ""];
  const report = useAsync(() => api.get<VarianceReport>(`/api/variance${opening ? `?opening=${opening}&closing=${closing}` : ""}`), [pick]);
  // From / To: posted counts newest first, PAGE at a time. A choice that fails validation stays in `draft`
  // (shown in the controls with the reason) and the report keeps the last valid pair until it's fixed.
  const [counts, setCounts] = useState<PostedCount[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [moreError, setMoreError] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ from: string; to: string } | null>(null);
  const loadCounts = (offset: number) => api.get<CountPage>(`/api/variance/counts?limit=${PAGE}&offset=${offset}`)
    .then((pg) => { setTotal(pg.total); setCounts((c) => [...c.slice(0, offset), ...pg.counts]); setMoreError(null); })
    .catch((e) => setMoreError(String(e.message ?? e)));
  useEffect(() => { loadCounts(0); }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  const [packs, setPacks] = useState(true);
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());  // every item starts collapsed
  const [quietPicked, setQuietPicked] = useState<boolean | null>(null);  // null: the bottom group follows the filter
  const settings = useAsync(() => api.get<Settings>("/api/settings"));
  const [picked, setPicked] = useState<Partial<Record<ViewKey, string[]>>>({});
  const [choosing, setChoosing] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const r = report.data;
  const usePacks = (x: VarianceRow) => packs && Number(x.pack_size) > 1;
  const unitOf = (x: VarianceRow) => usePacks(x) ? x.pack_name || "pack" : (BASE[x.stock_unit]?.[0] ?? x.stock_unit);
  const q = (v: Num | number, x: VarianceRow, signed = false) => {
    if (v == null) return "n/a";
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
  const pools = r?.pools ?? [];
  const poolItems = pools.reduce((t, p) => t + p.members.length, 0);
  const poolsShort = pools.filter((p) => derivePool(p).kind === "short").length;
  const shown = all.filter(inView);
  const count = (k: Kind) => shown.filter((x) => x.kind === k).length;
  const notCounted = all.filter((x) => x.kind === "not_counted").length;
  const off = shown.filter((x) => x.status === "off").length;
  const needle = search.trim().toLowerCase();
  const lines = shown.filter((x) => (filter === "all" || x.kind === filter)
    && (!needle || x.name.toLowerCase().includes(needle) || x.sku.toLowerCase().includes(needle) || (x.category ?? "").toLowerCase().includes(needle)));
  // Items that match or are off by less than the threshold need no attention: they go in one group at the bottom,
  // closed until opened by hand, or while a search or the Matches filter is asking for them.
  const quiet = lines.filter(isQuiet), loud = lines.filter((x) => !isQuiet(x));
  const quietOpen = quietPicked ?? (filter === "matches" || needle !== "");
  const visible = quietOpen ? lines : loud;
  const allExpanded = visible.length > 0 && visible.every((x) => expanded.has(x.item_id));
  const setOpen = (id: string, isOpen: boolean) => setExpanded((s) => {
    if (isOpen === s.has(id)) return s;
    const c = new Set(s); if (isOpen) c.add(id); else c.delete(id); return c;
  });
  const row = (x: Line) => <Section key={x.item_id} x={x} q={q} open={expanded.has(x.item_id)} onToggle={(o) => setOpen(x.item_id, o)} threshold={r!.threshold_pct} />;

  // the tiles count what's in view: each individual item, and each set of alternatives once
  const poolLines = pools.map((p) => ({ p, ...derivePool(p) }));
  // one bucket each, as on the sample: major = off by the threshold or more (either way), minor = short but under it
  const checked = [...shown.map((x) => ({ kind: x.kind, status: x.status })), ...poolLines.map((x) => ({ kind: x.kind, status: x.p.status }))]
    .filter((x) => x.kind !== "not_counted");
  const majorN = checked.filter((x) => x.status === "off").length;
  const minorN = checked.filter((x) => x.status !== "off" && x.kind === "short").length;
  const onTarget = checked.length - majorN - minorN;
  const jump = (f: Filter) => { setFilter(f); requestAnimationFrame(() => document.getElementById("individual")?.scrollIntoView({ behavior: "smooth", block: "start" })); };
  const ledgerHead = <div aria-hidden className="lg-cols lg-head band max-md:hidden"><span>Stock item</span><span className="text-right">Should be left</span><span className="text-right">Actually left</span><span className="text-right">Difference</span></div>;

  return (
    <Page title="Stock vs sales" sub={r ? `${d(r.opening.counted_at)} count to ${d(r.closing.counted_at)} count · stock vs expected usage` : "Sold × recipe against what the counts say is left"}
      actions={total != null && total >= 2 ? (
        <div className="flex items-center gap-2 rounded-panel border border-line bg-paper-2 shadow-[var(--shadow-panel)] px-3 py-1.5">
          <Icon name="calendar" size={16} className="text-muted" />
          <CountRange counts={counts} total={total} report={r ?? null} url={opening ? { from: opening, to: closing } : null} draft={draft}
            onMore={() => loadCounts(counts.length)}
            onPick={(from, to, valid) => {
              if (!valid) { setDraft({ from, to }); return; }
              setDraft(null); setQuietPicked(null);
              setParams({ p: `${from}:${to}` }, { replace: true });
            }} />
        </div>) : undefined}>
        {moreError && <Notice kind="error">Couldn’t load more counts: {moreError}</Notice>}

        {report.loading && !report.data && !report.error && <Loading rows={8} />}
        {report.error && (total != null && total < 2
          ? <Empty>This page compares two posted counts, and there aren’t two yet. It also needs the Swype sales for every day between them and recipes for what sold.
              <br /><Link className="underline text-accent" to="/">See what’s still missing on the Overview</Link></Empty>
          : <Notice kind="error">{report.error}</Notice>)}

        {r && (<div className="flex flex-col gap-5">
          <div className="kpis">
            <Kpi icon="boxes" tone="bg-paper-3 text-ink-2" label="Total stock items" value={shown.length + poolItems}
              note={[pools.length ? `${poolItems} in ${pools.length} set${pools.length === 1 ? "" : "s"} of alternatives` : null, notCounted ? `${notCounted} not counted` : null].filter(Boolean).join(" · ") || null}
              onClick={() => jump("all")} />
            <Kpi icon="check" tone="bg-ok-soft text-ok" valueCls="text-ok" label="On target" value={onTarget} note={`matching, or over by less than ${n(r.threshold_pct, 0)}%`} onClick={() => { setQuietPicked(true); requestAnimationFrame(() => document.getElementById("quiet")?.scrollIntoView({ behavior: "smooth", block: "start" })); }} />
            <Kpi icon="warn" tone="bg-amber-soft text-amber" valueCls={minorN ? "text-amber" : ""} label="Minor shortage" value={minorN} note={`short by less than ${n(r.threshold_pct, 0)}%`} onClick={() => jump("short")} />
            <Kpi icon="alert" tone="bg-danger-soft text-danger" valueCls={majorN ? "text-danger" : ""} label="Major difference" value={majorN} note={`off by ${n(r.threshold_pct, 0)}% or more, short or over`} onClick={() => jump("all")} />
          </div>

          {(r.between_counts > 0 || r.sales_periods.length === 0 || r.missing_sales_days.length > 0 || r.straddling_periods.length > 0 || r.no_movements_recorded) && <div>
            <Coverage r={r} />
            {r.between_counts > 0 && <Notice>{r.between_counts} count{r.between_counts === 1 ? "" : "s"} between {d(r.opening.counted_at)} and {d(r.closing.counted_at)} {r.between_counts === 1 ? "is" : "are"} ignored. This compares the From count with the To count, using every sale and stock movement in between.</Notice>}
          </div>}

          {(all.length > 0 || pools.length > 0) && <VarianceScatter rows={shown} pools={pools} threshold={r.threshold_pct} needle={needle} q={q} qUnit={qUnit}
            onPickPool={(p) => requestAnimationFrame(() => document.getElementById(`pool-${p.id}`)?.scrollIntoView({ behavior: "smooth", block: "center" }))}
            onPick={(x) => {
            setFilter("all");
            if (needle && ![x.name, x.sku, x.category ?? ""].some((f) => f.toLowerCase().includes(needle))) setSearch("");
            setOpen(x.item_id, true);
            if (isQuiet(derive(x))) setQuietPicked(true);
            requestAnimationFrame(() => document.getElementById(`item-${x.item_id}`)?.scrollIntoView({ behavior: "smooth", block: "center" }));
          }} />}
          {all.length > 0 && <Chart lines={all} />}

          <section id="individual" className="panel scroll-mt-4" aria-labelledby="individual-title">
            <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
              <PanelHead icon="box" id="individual-title" title="Individual stock items"
                sub={pools.length ? "Stock items never used as recipe alternatives." : "Every stock item in this count period."} />
              <div className="flex flex-wrap items-center gap-2">
                <input className={`${inputCls} w-full sm:w-52`} type="search" placeholder="Search items…" aria-label="Search stock items" value={search} onChange={(e) => setSearch(e.target.value)} />
                <div role="group" aria-label="Filter by difference" className="inline-flex flex-wrap rounded-control border border-line overflow-hidden text-sm">
                  {FILTERS.map(([k, label]) => (
                    <button key={k} type="button" aria-pressed={filter === k} onClick={() => setFilter(k)}
                      className={`px-3 py-1.5 ${filter === k ? "bg-raised text-ink font-medium" : "bg-surface text-muted hover:text-ink"}`}>
                      {label}{k !== "all" ? <span className="mono text-xs text-faint ml-1">{count(k)}</span> : null}
                    </button>))}
                </div>
              </div>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 mb-2 text-sm">
              <label className="flex items-center gap-2 text-muted"><input type="checkbox" checked={packs} onChange={(e) => setPacks(e.target.checked)} /> Bottles / kegs instead of L</label>
              <span className="flex gap-4">
                {all.length > 0 && <button type="button" className="text-muted hover:text-ink underline" aria-expanded={choosing} onClick={() => setChoosing((v) => !v)}>
                  {choosing ? "Done" : `Add / remove items (${shown.length} of ${all.length})`}</button>}
                {visible.length > 0 && <button type="button" className="text-muted hover:text-ink underline"
                  onClick={() => setExpanded(allExpanded ? new Set() : new Set(visible.map((x) => x.item_id)))}>{allExpanded ? "Collapse all" : "Expand all"}</button>}
              </span>
            </div>
            {saveError && <Notice kind="error">Couldn’t save the items in view: {saveError}</Notice>}
            {choosing && <ItemPicker items={all} label="Find an item to add or remove" none="" checked={inView} onToggle={toggleView}
              clear={viewList("variance_view_added").length + viewList("variance_view_removed").length > 0
                ? ["Reset to counted items", () => saveView({ variance_view_added: [], variance_view_removed: [] })] : null} />}
            {lines.length === 0 ? <p className="text-sm text-muted py-6 text-center">{!all.length ? "No individual stock item moved or was used in this period." : shown.length ? "No stock item matches this filter." : "No items in view. Use “Add / remove items” to pick some."}</p>
              : loud.length === 0 ? <p className="text-sm text-muted py-6 text-center">Nothing to chase: every item in view matches or is off by less than {n(r.threshold_pct, 0)}%. <a className="underline" href="#quiet" onClick={() => setQuietPicked(true)}>See them at the bottom of the page</a>.</p>
              : <>{ledgerHead}{loud.map(row)}</>}
          </section>

          {pools.length > 0 && (
            <section id="alternatives" className="panel scroll-mt-4" aria-labelledby="alternatives-title">
              <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
                <PanelHead icon="shuffle" id="alternatives-title" title="Stock items used as alternatives"
                  sub="Stock items that can stand in for one another in recipes. Swype doesn’t say which one was poured, so each set is checked as one." />
                <span className="pill pill-none">{pools.length} set{pools.length === 1 ? "" : "s"}</span>
              </div>
              <div className="flex flex-col gap-5">{pools.map((p) => <PoolCard key={p.id} p={p} threshold={r.threshold_pct} />)}</div>
            </section>)}

          {r.unmapped.length > 0 && (
            <section className="panel" aria-labelledby="unmapped-title">
              <PanelHead icon="recipe" id="unmapped-title" title="Sold with no recipe"
                sub={<>These Swype items aren’t in recipe usage, so stock items they use will look short. <Link className="underline" to="/recipes?tab=todo">Add recipes</Link> or mark them as taking no stock.</>} />
              <ul className="text-sm grid gap-x-6 gap-y-0.5 sm:grid-cols-2 lg:grid-cols-3">{r.unmapped.slice(0, 60).map((u) => (
                <li key={u.code} className="flex justify-between gap-2"><span>{u.name} <span className="text-muted text-xs">{u.category}</span></span><span className="num text-muted">{n(u.sold, 0)}</span></li>))}</ul>
              {r.unmapped.length > 60 && <p className="text-xs text-muted mt-2">and {r.unmapped.length - 60} more</p>}
            </section>)}

          {quiet.length > 0 && (
            <section className="panel !py-3" aria-label="Items matching or within the threshold">
              <details id="quiet" className="lg-group scroll-mt-4" open={quietOpen} onToggle={(e) => { const o = e.currentTarget.open; if (o !== quietOpen) setQuietPicked(o); }}>
                <summary className="flex items-center gap-3 !text-ink">
                  <span className="lg-arrow inline-block text-lg leading-none transition-transform" aria-hidden>›</span>
                  <span className="kpi-icon !w-8 !h-8 bg-ok-soft text-ok"><Icon name="check" size={16} /></span>
                  <span className="font-semibold">Matching or off by less than {n(r.threshold_pct, 0)}%</span>
                  <span className="pill pill-ok !text-xs">{quiet.length}</span>
                </summary>
                <div className="pt-2">{ledgerHead}{quiet.map(row)}</div>
              </details>
            </section>)}

          <footer className="text-xs text-muted leading-relaxed max-w-[75ch]">
            “Actually left” is the closing physical count. “Recipe usage” is estimated from Swype sales × recipes, not measured.
            A difference is not proof of theft, over-pouring or waste. One sale can appear under several stock items; don’t add units sold across sections.
          </footer>
        </div>)}
    </Page>
  );
}

function PanelHead({ icon, id, title, sub }: { icon: IconName; id?: string; title: string; sub?: ReactNode }) {
  return (
    <div className="panel-head !mb-0 min-w-0">
      <span className="panel-icon"><Icon name={icon} size={20} /></span>
      <div className="min-w-0"><h2 id={id} className="panel-title">{title}</h2>{sub && <p className="panel-sub">{sub}</p>}</div>
    </div>);
}

function Kpi({ icon, tone, label, value, valueCls = "", note, onClick }: { icon: IconName; tone: string; label: string; value: number; valueCls?: string; note?: string | null; onClick?: () => void }) {
  return (
    <button type="button" className="kpi hover:border-line-strong" onClick={onClick}>
      <span className={`kpi-icon ${tone}`}><Icon name={icon} size={22} /></span>
      <span className="min-w-0"><span className="kpi-label block">{label}</span><span className={`kpi-value block ${valueCls}`}>{value}</span>{note && <span className="kpi-note block sm:truncate">{note}</span>}</span>
    </button>);
}

/** From and To count pickers. Each lists the loaded counts newest first and ends with "Load 10 earlier counts…".
 *  A pair is valid when both are picked, differ, and From is earlier than To. */
function CountRange({ counts, total, report, url, draft, onMore, onPick }: {
  counts: PostedCount[]; total: number; report: VarianceReport | null; url: { from: string; to: string } | null;
  draft: { from: string; to: string } | null; onMore: () => void; onPick: (from: string, to: string, valid: boolean) => void;
}) {
  // the counts in use may be older than what's loaded (a shared link): list them too
  const known = new Map(counts.map((c) => [c.id, c]));
  for (const c of report ? [report.opening, report.closing] : []) if (!known.has(c.id)) known.set(c.id, c);
  const list = [...known.values()].sort((a, b) => +new Date(b.counted_at) - +new Date(a.counted_at));
  const from = draft?.from ?? url?.from ?? report?.opening.id ?? "";
  const to = draft?.to ?? url?.to ?? report?.closing.id ?? "";
  const at = (id: string) => known.get(id) ? +new Date(known.get(id)!.counted_at) : NaN;
  const sameDay = (c: PostedCount) => list.filter((o) => d(o.counted_at) === d(c.counted_at)).length > 1;
  const label = (c: PostedCount) => sameDay(c) ? dt(c.counted_at) : d(c.counted_at);
  const problem = (f: string, t: string) => !f || !t ? "Pick both counts."
    : f === t ? "From and To are the same count. Pick two different counts."
    : at(f) >= at(t) ? "From must be an earlier count than To." : null;
  const error = draft ? problem(draft.from, draft.to) : null;
  const change = (f: string, t: string) => onPick(f, t, problem(f, t) == null);
  const select = (which: "From" | "To", value: string) => (
    <label className="flex items-center gap-2 text-sm">
      <span className="text-muted">{which}</span>
      <select className={`${inputCls} ${error ? "border-danger" : ""}`} aria-label={`${which} count`} aria-invalid={!!error} value={value}
        onChange={(e) => {
          const v = e.target.value;
          if (v === MORE) { onMore(); return; }
          if (which === "From") change(v, to); else change(from, v);
        }}>
        {list.map((c) => <option key={c.id} value={c.id}>{label(c)}</option>)}
        {counts.length < total && <option value={MORE}>Load {Math.min(PAGE, total - counts.length)} earlier counts…</option>}
      </select>
    </label>);
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      {select("From", from)}
      <span aria-hidden className="text-muted">→</span>
      {select("To", to)}
      {error && <span role="alert" className="text-xs text-danger basis-full">{error} Still showing {report ? `${d(report.opening.counted_at)} → ${d(report.closing.counted_at)}` : "the last valid range"}.</span>}
    </div>);
}

/** Searchable checklist of stock items, shared by the list above and the chart. */
function ItemPicker({ items, label, none, checked, onToggle, clear }: { items: Line[]; label: string; none: string; checked: (x: Line) => boolean; onToggle: (x: Line) => void; clear: [string, () => void] | null }) {
  const [find, setFind] = useState("");
  const words = find.toLowerCase().split(/\s+/).filter(Boolean);
  const options = items.filter((x) => words.every((w) => [x.name, x.sku, x.category ?? ""].some((f) => f.toLowerCase().includes(w))));
  return (
    <div className="border-y border-line py-3 mb-4">
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

const STYLES: [ChartStyle, string][] = [["bars", "Bars"], ["gap", "Gap"], ["difference", "Difference"]];
const signed = (v: number, dp: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${n(Math.abs(v), dp)}%`;

/** Should be left vs actually left, each as % of that item’s own available stock, drawn one of three ways:
 *  two bars per item, two dots joined by the gap between them, or the difference alone around zero.
 *  Only items picked by hand are charted; that pick is saved for the business, the chart style for this login in it. */
function Chart({ lines }: { lines: Line[] }) {
  const settings = useAsync(() => api.get<Settings>("/api/settings"));
  const prefs = useAsync(() => api.get<UserPrefs>("/api/settings/me"));
  const [picked, setPicked] = useState<string[] | null>(null);
  const [stylePicked, setStylePicked] = useState<ChartStyle | null>(null);
  const [choosing, setChoosing] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const style = stylePicked ?? prefs.data?.variance_chart_style ?? "bars";
  const chosen = new Set(picked ?? settings.data?.variance_chart_items ?? []);
  // items with no available stock (opening + in − out ≤ 0) have no percentage, so they can’t be charted or picked
  const stocked = lines.filter((x) => x.available != null && x.available > 0);
  const saved = (p: Promise<unknown>) => p.then(() => setSaveError(null)).catch((e) => setSaveError(String(e.message ?? e)));
  const save = (list: string[]) => { setPicked(list); saved(api.put("/api/settings", { variance_chart_items: list })); };
  const saveStyle = (s: ChartStyle) => { setStylePicked(s); saved(api.put("/api/settings/me", { variance_chart_style: s })); };
  const toggle = (id: string) => save(chosen.has(id) ? [...chosen].filter((c) => c !== id) : [...chosen, id]);

  const rows = stocked.filter((x) => chosen.has(x.item_id)).map((x) => {
    const e = pct(x.shouldLeft, x.available), a = pct(x.left, x.available);
    return { x, e, a, gap: e == null || a == null ? null : a - e };
  });
  const vals = rows.flatMap((r) => [r.e, r.a]).filter((v): v is number => v != null);
  const lo = Math.min(0, ...vals), hi = Math.max(100, ...vals), span = hi - lo;
  const most = Math.max(5, ...rows.map((r) => Math.abs(r.gap ?? 0)));  // the difference chart runs −most … +most
  const diff = style === "difference";
  const odd = diff ? [] : rows.filter((r) => [r.e, r.a].some((v) => v != null && (v < 0 || v > 100)));
  const pos = (v: number) => diff ? 50 + (v / most) * 50 : ((v - lo) / span) * 100;
  const ticks = diff ? [-most, 0, most] : [lo, 0, 50, 100, hi].filter((t, i, s) => s.indexOf(t) === i);
  const zero = lo < 0 && <span className="lg-zero" style={{ left: `${pos(0)}%` }} />;
  const bar = (v: number | null, cls: string) => (
    <div className="lg-track">
      {zero}
      {v != null && <span className={`lg-fill bar ${cls}`} style={{ left: `${pos(Math.min(0, v))}%`, width: `${(Math.abs(v) / span) * 100}%` }} />}
    </div>);
  const plot = ({ e, a, gap }: (typeof rows)[number]) => style === "bars" ? <>{bar(e, "lg-e")}{bar(a, "lg-a")}</>
    : style === "gap" ? (
      <div className="lg-rail">
        {zero}
        {gap != null && <span className="lg-link" style={{ left: `${pos(Math.min(e!, a!))}%`, width: `${(Math.abs(gap) / span) * 100}%` }} />}
        {e != null && <span className="lg-dot lg-e" style={{ left: `${pos(e)}%` }} />}
        {a != null && <span className="lg-dot lg-a" style={{ left: `${pos(a)}%` }} />}
      </div>)
    : (
      <div className="lg-track lg-track-tall">
        <span className="lg-zero" style={{ left: "50%" }} />
        {gap != null && gap !== 0 && <span className={`lg-fill ${gap < 0 ? "bg-danger" : "bg-accent"}`} style={{ left: `${pos(Math.min(0, gap))}%`, width: `${(Math.abs(gap) / most) * 50}%` }} />}
      </div>);
  const gapText = (gap: number | null, a: number | null) => gap == null ? (a == null ? "Not counted" : "n/a") : Math.abs(gap) < 0.05 ? "Matches" : `${signed(gap, 1)} ${gap < 0 ? "short" : "over"}`;
  const swatch = (cls: string, text: string) => <span><b className={`inline-block w-2.5 h-2 mr-1.5 rounded-sm ${cls}`} />{text}</span>;
  const cols = "grid grid-cols-[minmax(0,110px)_1fr_auto] sm:grid-cols-[minmax(0,180px)_1fr_120px] gap-3.5";
  return (
    <section className="panel">
      <div className="flex items-center justify-between gap-2.5 flex-wrap mb-4">
        <PanelHead icon="chart" title={diff ? "Actually left − should be left" : "Stock remaining / available stock"} sub="Individual items you chose to follow." />
        <div className="flex items-center flex-wrap gap-3.5 text-xs text-muted">
          {diff ? <>{swatch("bg-danger", "Short")}{swatch("bg-accent", "Over")}</> : <>{swatch("lg-e", "Should be left")}{swatch("lg-a", "Actually left")}</>}
          <div role="group" aria-label="Chart style" className="inline-flex rounded-control border border-line overflow-hidden">
            {STYLES.map(([k, label]) => (
              <button key={k} type="button" aria-pressed={style === k} onClick={() => saveStyle(k)}
                className={`px-2.5 py-1 ${style === k ? "bg-raised text-ink font-medium" : "bg-surface text-muted hover:text-ink"}`}>{label}</button>))}
          </div>
          <button type="button" className="text-xs text-muted hover:text-ink underline" aria-expanded={choosing} onClick={() => setChoosing((v) => !v)}>
            {choosing ? "Done" : `Choose items (${rows.length})`}
          </button>
        </div>
      </div>
      {saveError && <Notice kind="error">Couldn’t save your choice: {saveError}</Notice>}
      {choosing && <ItemPicker items={stocked} label="Find an item to chart" none="No item has available stock in this period."
        checked={(x) => chosen.has(x.item_id)} onToggle={(x) => toggle(x.item_id)} clear={rows.length > 0 ? ["Clear all", () => save([])] : null} />}
      {settings.data && rows.length === 0 && !choosing && <p className="text-xs text-muted py-2">No items chosen for this chart. Use “Choose items” to pick the ones to follow; your choice is kept for next time.</p>}
      {rows.length > 0 && (<>
      {rows.map((r) => {
        const { x, e, a, gap } = r;
        const say = `${x.name}: should be left ${e == null ? "unknown" : p1(e)}, actually left ${a == null ? "not counted" : p1(a)} of available stock${gap == null ? "" : `, difference ${gapText(gap, a)}`}`;
        return (
        <div key={x.item_id} className={`${cols} items-center text-xs py-1`} title={say}>
          <span className="truncate">{x.name}</span>
          <div className="grid gap-1" role="img" aria-label={say}>{plot(r)}</div>
          <span className="text-right num text-muted whitespace-nowrap">
            {diff ? gapText(gap, a) : `${e == null ? "n/a" : p1(e)} / ${a == null ? "Not counted" : p1(a)}`}
          </span>
        </div>);
      })}
      <div className={`${cols} mt-1.5 text-xs text-muted`}>
        <span /><div className="relative h-3">
          {ticks.map((t) => (
            <span key={t} className="absolute -translate-x-1/2 first:translate-x-0 last:-translate-x-full" style={{ left: `${pos(t)}%` }}>{diff ? signed(t, 0) : `${n(t, 0)}%`}</span>))}
        </div><span />
      </div>
      <p className="text-xs text-muted mt-2">{diff
        ? "The difference in points of each item’s own available stock (opening + in − out). Left of zero is short, right is over."
        : `Each item uses its own available stock (opening + in − out) as the denominator for both ${style === "gap" ? "dots; the line between them is the difference" : "bars"}.`}</p>
      {odd.length > 0 && <p className="text-xs text-danger mt-1">Check the data: {odd.map((r) => r.x.name).join(", ")} {odd.length === 1 ? "is" : "are"} below 0% or above 100% of available stock, so the scale runs {n(lo, 0)}% to {n(hi, 0)}%.</p>}
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

/** Difference as a tinted pill: amber short, blue over, green matching, red past the threshold. */
function DiffPill({ kind, status, text }: { kind: Kind; status: string; text: string }) {
  const cls = kind === "not_counted" ? "pill-none" : kind === "matches" ? "pill-ok" : status === "off" ? "pill-off" : kind === "short" ? "pill-short" : "pill-over";
  const icon: IconName | null = kind === "matches" ? "check" : kind === "short" ? (status === "off" ? "alert" : "warn") : kind === "over" ? "up" : null;
  return <span className={`pill ${cls}`}>{icon && <Icon name={icon} size={14} strokeWidth={2.25} />}{text}</span>;
}

function Section({ x, q, open, onToggle, threshold }: { x: Line; q: (v: Num | number, x: VarianceRow, signed?: boolean) => string; open: boolean; onToggle: (open: boolean) => void; threshold: Num }) {
  const diffText = x.diff == null ? "n/a" : x.kind === "matches" ? "Matches" : `${q(Math.abs(x.diff), x)} ${x.kind}`;
  const chip = x.status === "off" ? [`Off ≥ ${n(threshold, 0)}%`, "bg-danger-soft text-danger"] : CHIP[x.status];
  const flow = [`Opening ${q(x.opening, x)}`];
  if (Number(x.received)) flow.push(`+ in ${q(x.received, x)}`);
  if (Number(x.removed)) flow.push(`− out ${q(x.removed, x)}`);
  flow.push(`− recipe usage ${q(x.expected, x)}`);
  if (Number(x.explained)) flow.push(`− recorded waste ${q(x.explained, x)}`);
  return (
    <details id={`item-${x.item_id}`} className="lg-row scroll-mt-4" open={open} onToggle={(e) => onToggle(e.currentTarget.open)}>
      <summary className="lg-cols">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap font-semibold">
            <span className="lg-arrow inline-block text-muted text-lg leading-none transition-transform" aria-hidden>›</span>
            <span>{x.name}</span>
            {chip && <span className={`lg-chip ${chip[1]}`}>{chip[0]}</span>}
          </div>
          <div className="text-xs text-muted mt-0.5 pl-4 num">{x.category ? `${x.category} · ` : ""}{flow.join(" ")}</div>
        </div>
        <div className="md:text-right num"><span className="md:sr-only block text-xs text-muted">Should be left</span><strong className="font-medium whitespace-nowrap">{q(x.shouldLeft, x)}</strong></div>
        <div className="md:text-right num"><span className="md:sr-only block text-xs text-muted">Actually left</span><strong className="font-medium whitespace-nowrap">{x.left == null ? "Not counted" : q(x.left, x)}</strong></div>
        <div className="md:text-right"><span className="md:sr-only block text-xs text-muted">Difference</span><DiffPill kind={x.kind} status={x.status} text={diffText} /></div>
      </summary>
      <div className="pl-7 pr-3 pb-3 overflow-x-auto">
        {x.no_recipe ? <p className="text-xs text-muted pb-1">No recipe uses {x.name}, so sales can’t explain any of its use. Add it to the recipe of every Swype item that takes it (a bottle of beer too: 1 each).</p>
          : x.contributions.length === 0 ? <p className="text-xs text-muted pb-1">No sold Swype item with a recipe uses {x.name} in this period.</p> : (
          <table className="lg-links" aria-label={`Menu items using ${x.name}`}>
            <thead><tr><th>Menu item</th><th>Units sold</th><th>Per item</th><th>Recipe usage</th></tr></thead>
            <tbody>{x.contributions.map((c, i) => (
              <tr key={i}><td>{c.name} <span className="text-muted mono text-xs">{c.code}</span></td><td>{n(c.sold, 2)}</td><td>{n(c.portion_qty, 2)} {c.portion_unit}</td><td>{q(c.used, x)}</td></tr>))}
            </tbody>
            <tfoot>
              <tr><td>Total recipe usage</td><td /><td /><td>{q(x.expected, x)}</td></tr>
              {Number(x.explained) > 0 && <tr><td className="!font-normal text-muted">Recorded breakage / expired (Deliveries &amp; stock)</td><td /><td /><td className="!font-normal">{q(x.explained, x)}</td></tr>}
            </tfoot>
          </table>)}
        {x.issues.map((t) => <p key={t} className="text-xs text-danger py-1">{t}. Fix the item’s unit on Stock items, or the portion on Recipes.</p>)}
      </div>
    </details>
  );
}

function PoolCard({ p, threshold }: { p: VariancePool; threshold: Num }) {
  const x = derivePool(p);
  const offPct = p.status === "off";
  const status: [string, string, IconName] = x.kind === "not_counted" ? [CHIP[p.status]?.[0] ?? "Not counted", "pill-none", "alert"]
    : x.kind === "matches" ? ["Matches", "pill-ok", "check"] : p.status === "ok" ? [`Within ±${n(threshold, 0)}%`, "pill-ok", "check"]
    : x.kind === "short" ? ["Shortage", offPct ? "pill-off" : "pill-short", "warn"] : ["Over", "pill-over", "up"];
  const diffLabel = x.kind === "short" ? "Shortage" : x.kind === "over" ? "Over" : "Difference";
  const diffText = x.diff == null ? "n/a" : x.kind === "matches" ? "0" : qUnit(Math.abs(x.diff), p.unit);
  const diffTile = x.kind === "short" ? (offPct ? "bg-danger-soft text-danger" : "bg-amber-soft text-amber") : x.kind === "over" ? "bg-info-soft text-info" : x.kind === "matches" ? "bg-ok-soft text-ok" : "bg-paper-3";
  const shared = p.members.filter((m) => p.recipes.filter((c) => c.uses.includes(m.name)).length > 1).map((m) => m.name);
  const sold = p.recipes.reduce((t, c) => t + Number(c.sold), 0);
  const flow = [`Opening ${qUnit(p.opening, p.unit)}`];
  if (Number(p.received)) flow.push(`+ in ${qUnit(p.received, p.unit)}`);
  if (Number(p.removed)) flow.push(`− out ${qUnit(p.removed, p.unit)}`);
  flow.push(`− recipe usage ${qUnit(p.expected, p.unit)}`);
  if (Number(p.explained)) flow.push(`− recorded waste ${qUnit(p.explained, p.unit)}`);
  return (
    <article id={`pool-${p.id}`} className="scroll-mt-4 grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)] items-start">
      <div className="min-w-0 rounded-[12px] border border-line bg-paper p-4">
        <div className="flex items-start gap-3">
          <span className="panel-icon !bg-paper-3 !text-ink"><Icon name="glass" size={20} /></span>
          <div className="min-w-0 flex-1">
            <h3 className="text-lg font-semibold leading-tight">{sellingName(p)}</h3>
            <p className="text-sm text-muted">{p.members.map((m) => m.name).join(", ")}</p>
          </div>
          <span className={`pill ${status[1]}`}><Icon name={status[2]} size={14} strokeWidth={2.25} />{status[0]}</span>
        </div>
        <div className="grid grid-cols-3 gap-2 mt-4">
          <div className="figure-tile bg-info-soft"><div>Should be left</div><div>{qUnit(x.shouldLeft, p.unit)}</div></div>
          <div className="figure-tile bg-info-soft"><div>Actually left</div><div>{x.left == null ? "n/a" : qUnit(x.left, p.unit)}</div></div>
          <div className={`figure-tile ${diffTile}`}><div className="!text-current opacity-80">{diffLabel}</div><div>{diffText}</div></div>
        </div>
        <p className="text-xs text-muted num mt-3">{flow.join(" ")}</p>
        <details className="mt-2 text-sm">
          <summary className="cursor-pointer text-muted hover:text-ink select-none text-xs">Counts by stock item</summary>
          <table className="lg-links mt-1" aria-label={`Counts of ${p.name}`}>
            <thead><tr><th>Stock item</th><th>Opening</th><th>Closing</th><th>Gone</th></tr></thead>
            <tbody>{p.members.map((m) => (
              <tr key={m.item_id}><td>{m.name} <span className="text-muted mono text-xs">{m.sku}</span></td>
                <td>{m.opening == null ? "n/a" : qUnit(m.opening, m.stock_unit)}</td><td>{m.closing == null ? "n/a" : qUnit(m.closing, m.stock_unit)}</td>
                <td>{m.actual == null ? "n/a" : qUnit(m.actual, m.stock_unit)}</td></tr>))}
            </tbody>
          </table>
        </details>
      </div>
      <div className="min-w-0 overflow-x-auto lg:border-l lg:border-line lg:pl-5">
        <h4 className="flex items-center gap-2 font-semibold mb-2"><Icon name="doc" size={18} className="text-muted" />Participating recipes</h4>
        {p.recipes.length === 0 ? <p className="text-sm text-muted">Nothing that uses these items sold in this period.</p> : (
          <table className="lg-links boxed" aria-label={`Recipes using ${p.name}`}>
            <thead><tr><th>Recipe</th><th>Sold</th><th>Per item</th><th>Required usage</th></tr></thead>
            <tbody>{p.recipes.map((c, i) => (
              <tr key={i}>
                <td><span className="flex items-start gap-2"><Icon name="glass" size={16} className="text-accent mt-0.5" /><span className="min-w-0">{c.name}
                  <span className="flex flex-wrap gap-1 mt-0.5">{c.uses.map((u) => <span key={u} className="rounded-full bg-info-soft text-info text-[11px] px-2 py-px whitespace-nowrap">{u}</span>)}</span></span></span></td>
                <td>{n(c.sold, 0)}</td><td>{n(c.portion_qty, 2)} {c.portion_unit}</td><td>{qUnit(c.used, p.unit)}</td></tr>))}
            </tbody>
            <tfoot>
              <tr><td>Total (all recipes)</td><td>{n(sold, 0)}</td><td /><td>{qUnit(p.expected, p.unit)}</td></tr>
              {Number(p.explained) > 0 && <tr><td className="!font-normal text-muted !bg-transparent">Recorded breakage / expired</td><td className="!bg-transparent" /><td className="!bg-transparent" /><td className="!font-normal !bg-transparent">{qUnit(p.explained, p.unit)}</td></tr>}
            </tfoot>
          </table>)}
        <p className="flex items-start gap-2 text-xs text-muted mt-3 rounded-[10px] bg-paper-3 px-3 py-2">
          <Icon name="alert" size={14} className="mt-px" />
          <span>{p.members.length === 2 ? "Both" : `The ${p.members.length}`} items are calculated together because they can stand in for one another{shared.length ? ` across recipes (${shared.join(", ")} ${shared.length === 1 ? "is" : "are"} used in more than one)` : ""}.</span>
        </p>
        {p.issues.map((t) => <p key={t} className="text-xs text-danger pt-1">{t}. Fix the item’s unit on Stock items, or the portion on Recipes.</p>)}
      </div>
    </article>);
}

function Coverage({ r }: { r: VarianceReport }) {
  const notes: string[] = [];
  const missing = r.missing_sales_days;
  if (r.sales_periods.length === 0) notes.push("No Swype sales are imported for this period, so recipe usage is zero. Import the Item Sales report for these days.");
  else if (missing.length) notes.push(`Missing Swype sales for ${missing.length} day${missing.length === 1 ? "" : "s"}: ${dayRanges(missing)}. Recipe usage is too low for those days; import them under Imports.`);
  for (const p of r.straddling_periods) notes.push(`Sales for ${day(p.from)} to ${day(p.to)} cross a count date and are left out. Import sales week by week, matching the count days.`);
  if (r.no_movements_recorded) notes.push("No deliveries were recorded in this period. If stock arrived, enter it under Deliveries, or items will look over.");
  return (<>
    {notes.map((t) => <Notice key={t}>{t}</Notice>)}
  </>);
}
