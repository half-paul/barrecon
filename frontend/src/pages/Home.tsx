import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, type CountSession, type CurrentPeriod, type Issue, type Item, type Settings, type VarianceReport, cents, d, dayRanges, n } from "../lib/api";
import { can, useMe } from "../lib/auth";
import { Icon, type IconName } from "../lib/icons";
import { useAsync } from "../lib/ui";
import type { Performance } from "./BarPerformance";

type Num = string | number;
type SalesReport = { from: string; to: string; days_covered: number; summary: { net: Num; qty: Num }; periods: { from: string; to: string; qty: Num; net: Num }[] };
type DrinkRow = { code: string; name: string; category: string | null; sold: Num; revenue: Num; theoretical_cost?: Num | null; pour_cost_pct?: Num | null };
type PourReport = { rows: DrinkRow[]; no_recipe: DrinkRow[] };
type ReorderRow = { key: string; item_id: string; name: string; stock_unit: string; pack_name: string | null; on_hand: Num | null; on_hand_packs: Num | null;
  days_left: Num | null; packs: number; cases: number | null; status: "stockout" | "before_delivery" | "reorder" | "no_stock_data" | "excess" | "ok" };
type ReorderReport = { rows: ReorderRow[]; counted: boolean };

const DAY = 86400000;
const RANGES = [7, 14, 28] as const;
const iso = (t: Date) => `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}`;
const atNoon = (s: string) => new Date(`${s}T12:00:00`);
const short = (t: Date) => t.toLocaleDateString("en-CA", { month: "short", day: "numeric" });
const money = (x: Num, dp = 0) => `${Number(x) < 0 ? "−" : ""}$${dp ? cents(Math.abs(Number(x))) : n(Math.abs(Number(x)), 0)}`;
const signed = (x: number, dp = 1) => `${x > 0 ? "+" : x < 0 ? "−" : ""}${n(Math.abs(x), dp)}`;
const AT_RISK = new Set(["stockout", "before_delivery", "reorder"]);

/** Hide what this login may not see: resolves to null instead of calling an endpoint that would 403. */
function useIf<T>(ok: boolean, path: string) {
  return useAsync<T | null>(() => (ok ? api.get<T>(path) : Promise.resolve(null)), [ok, path]);
}

export default function Home() {
  const me = useMe();
  const seeVar = can(me, "variance"), seeRep = can(me, "reports");
  const [days, setDays] = useState<number>(7);

  const today = new Date();
  const from = new Date(today.getTime() - (days - 1) * DAY);
  const pTo = new Date(from.getTime() - DAY), pFrom = new Date(from.getTime() - days * DAY);

  const current = useAsync(() => api.get<CurrentPeriod>("/api/variance/current"));
  const counts = useAsync(() => api.get<CountSession[]>("/api/counts"));
  const settings = useAsync(() => api.get<Settings>("/api/settings"));
  const issues = useAsync(() => api.get<Issue[]>("/api/imports/issues"));
  const items = useAsync(() => api.get<Item[]>("/api/items"));
  const perf = useIf<Performance>(seeVar, "/api/variance/performance");
  const variance = useIf<VarianceReport>(seeVar, "/api/variance");
  const sales = useIf<SalesReport>(seeRep, `/api/reports/item-sales?from=${iso(from)}&to=${iso(today)}`);
  const prior = useIf<SalesReport>(seeRep, `/api/reports/item-sales?from=${iso(pFrom)}&to=${iso(pTo)}`);
  const pour = useIf<PourReport>(seeRep, `/api/reports/pour-cost?days=${days}`);
  const reorder = useIf<ReorderReport>(seeRep, "/api/reports/reorder");

  // count status: a draft, overdue, or the next count day from Settings (0 = Monday)
  const posted = counts.data?.filter((c) => c.status === "posted") ?? [];
  const last = posted.find((c) => new Date(c.counted_at).getTime() <= Date.now());
  const future = posted.filter((c) => new Date(c.counted_at).getTime() > Date.now());
  const draft = counts.data?.find((c) => c.status === "draft");
  const sinceLast = last ? Math.floor((Date.now() - new Date(last.counted_at).getTime()) / DAY) : null;
  const remind = settings.data?.count_reminder_enabled !== false;
  const overdue = remind && sinceLast != null && sinceLast > 7;
  const cw = settings.data?.count_weekday;
  const untilCount = cw == null ? null : (cw - (today.getDay() + 6) % 7 + 7) % 7;
  const countDay = untilCount == null ? null : new Date(today.getTime() + untilCount * DAY);
  const countWhen = untilCount == null ? "" : untilCount === 0 ? "today" : untilCount === 1 ? "tomorrow" : `on ${countDay!.toLocaleDateString("en-CA", { weekday: "long" })}`;

  // KPIs
  const net = sales.data ? Number(sales.data.summary.net) : null;
  const priorNet = prior.data ? Number(prior.data.summary.net) : null;
  const salesChange = net != null && priorNet ? (net - priorNet) / priorNet * 100 : null;
  const bc = perf.data?.beverage_cost, pbc = perf.data?.prior_beverage_cost;
  const pp = bc?.pct != null && pbc?.pct != null ? Number(bc.pct) - Number(pbc.pct) : null;
  const unexplained = bc?.variance_cost != null ? Number(bc.variance_cost) : null;
  const unexplainedPct = unexplained != null && bc && Number(bc.sales) ? Math.abs(unexplained) / Number(bc.sales) * 100 : null;
  const risks = reorder.data?.rows.filter((r) => AT_RISK.has(r.status)) ?? [];

  // variance rows that are off, largest first
  const vrows = (variance.data?.rows ?? []).filter((r) => r.actual != null && r.variance != null && (r.status === "off" || r.status === "ok"))
    .sort((a, b) => Math.abs(Number(b.variance_cost ?? b.variance_pct ?? 0)) - Math.abs(Number(a.variance_cost ?? a.variance_pct ?? 0)));
  const offOver = vrows.filter((r) => r.status === "off" && Number(r.variance) > 0);

  const c = current.data;
  const attention: Attn[] = [];
  for (const r of offOver.slice(0, 2))
    attention.push({ tone: "bad", title: `${r.name} — high unexplained usage`, detail: `${r.variance_cost != null ? `${money(r.variance_cost)} ` : ""}(≈ ${n(r.variance, 1)} ${r.stock_unit})`, to: "/variance" });
  for (const r of risks.filter((r) => r.status !== "reorder").slice(0, 2))
    attention.push({ tone: "bad", title: `${r.name} — ${r.status === "stockout" ? "out of stock" : "running low"}`,
      detail: r.on_hand != null ? `≈ ${n(r.on_hand, 1)} ${r.stock_unit} left${r.days_left != null ? ` (≈ ${n(r.days_left, 0)} days)` : ""}` : "", to: "/reports/reorder" });
  if (c?.unmapped) attention.push({ tone: "info", title: `${c.unmapped} missing recipe${c.unmapped === 1 ? "" : "s"}`, detail: "Add recipes to improve tracking", to: "/recipes?tab=todo" });
  if (c?.opening && c.missing_sales_days.length) attention.push({ tone: "warn", title: `Sales missing for ${c.missing_sales_days.length} day${c.missing_sales_days.length === 1 ? "" : "s"}`, detail: dayRanges(c.missing_sales_days), to: "/sales" });
  if (issues.data?.length) attention.push({ tone: "warn", title: `${issues.data.length} import issue${issues.data.length === 1 ? "" : "s"}`, detail: "Review what didn’t match", to: "/imports" });
  if (future.length) attention.push({ tone: "warn", title: `${future.length} count${future.length === 1 ? "" : "s"} dated in the future`, detail: "Check the count date", to: "/counts" });
  if (draft) attention.push({ tone: "info", title: "Count in progress", detail: `Draft for ${d(draft.counted_at)}: resume`, to: `/counts/${draft.id}` });
  else if (overdue) attention.push({ tone: "warn", title: "Count overdue", detail: `Last count ${sinceLast} days ago`, to: "/counts" });
  else if (remind && untilCount != null && untilCount <= 1) attention.push({ tone: "info", title: `Count due ${countWhen}`, detail: "Schedule or start the count", to: "/counts" });

  const strip: ReactNode[] = [];
  if (draft) strip.push(<Link key="count" to={`/counts/${draft.id}`}><Icon name="calendar" className="text-accent" /><strong>Count in progress</strong><span className="flex-1 text-ink-2">Draft for {d(draft.counted_at)}. Finish and post it to close the period.</span></Link>);
  else if (overdue) strip.push(<Link key="count" to="/counts"><Icon name="calendar" className="text-accent" /><strong>Count overdue</strong><span className="flex-1 text-ink-2">The last beverage count was {sinceLast} days ago.</span></Link>);
  else if (remind && countDay) strip.push(<Link key="count" to="/counts"><Icon name="calendar" className="text-accent" /><strong>Count due</strong><span className="flex-1 text-ink-2">Next beverage count is due {countWhen} ({short(countDay)}).</span></Link>);
  if (c?.unmapped) strip.push(<Link key="recipes" to="/recipes?tab=todo"><Icon name="book" className="text-accent" /><strong>{c.unmapped} missing recipe{c.unmapped === 1 ? "" : "s"}</strong><span className="flex-1 text-ink-2">Add recipes to track usage and cost accurately.</span><Icon name="chevron" className="text-accent" /></Link>);

  const stockLabel = (r: { on_hand: Num | null; stock_unit: string }) => r.on_hand == null ? "—" : `${n(r.on_hand, 1)} ${r.stock_unit}`;

  return (
    <div className="px-4 sm:px-7 py-5 sm:py-6 max-w-[1320px] mx-auto">
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3 mb-4">
        <div className="min-w-0">
          <div className="eyebrow">Owner’s overview</div>
          <h1 className="text-[2rem] font-bold leading-tight max-w-none">Overview</h1>
        </div>
        <label className="ctl-chip">
          <Icon name="calendar" size={16} className="text-muted" />
          <span className="sr-only">Period</span>
          <select className="bg-transparent outline-none pr-1" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {RANGES.map((k) => <option key={k} value={k} className="bg-paper-2">{k === days ? `${short(from)} – ${short(today)}` : `Last ${k} days`}</option>)}
          </select>
        </label>
      </header>

      {strip.length > 0 && <div className="alert-strip mb-4">{strip}</div>}

      <div className="grid gap-3 grid-cols-2 lg:grid-cols-4 mb-3">
        <Kpi icon="glass" label="Beverage sales" value={!seeRep ? "—" : net == null ? "…" : money(net)}
          delta={salesChange != null ? { text: `${signed(salesChange)}%`, good: salesChange >= 0 } : undefined}
          note={salesChange != null ? `vs. prior ${days} days` : seeRep && net === 0 ? <Link className="underline" to="/sales">Import Swype sales</Link> : seeRep ? "" : "Needs Reports access"} />
        <Kpi icon="coins" label="Beverage cost" value={!seeVar ? "—" : !perf.data ? "…" : bc?.pct != null ? `${n(bc.pct, 1)}%` : "—"}
          delta={pp != null ? { text: `${signed(pp)} pp`, good: pp <= 0 } : undefined}
          note={pp != null ? "vs. prior count" : !seeVar ? "Needs Stock vs sales access" : bc ? "No drink sales between counts" : perf.data ? "Needs two posted counts" : ""} />
        <Kpi icon="warn" label="Unexplained usage" value={!seeVar ? "—" : !perf.data ? "…" : unexplained != null ? money(unexplained) : "—"}
          tone={unexplained != null && unexplained < 0 ? "bad" : undefined}
          note={unexplainedPct != null ? `≈ ${n(unexplainedPct, 1)}% of sales · last count period` : unexplained != null ? "Last count period" : ""} to="/variance" />
        <Kpi icon="box" label="Stock risks" value={!seeRep ? "—" : !reorder.data ? "…" : String(risks.length)}
          note={!reorder.data ? "" : risks.length ? <Link to="/reports/reorder" className="inline-flex items-center gap-1 hover:text-accent">Items need attention <Icon name="chevron" size={12} className="text-accent" /></Link> : reorder.data.counted ? "Nothing running low" : "Needs a posted count"} />
      </div>

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1.65fr)_minmax(0,1fr)] mb-3">
        <RevenueCard report={sales.data} allowed={seeRep} change={salesChange} days={days} />
        <section className="dcard">
          <div className="dcard-head"><h2 className="dcard-title">Needs attention ({attention.length})</h2></div>
          {attention.length === 0 ? <p className="text-sm text-muted py-6 text-center">All clear.</p> : (
            <ul>{attention.slice(0, 7).map((a) => (
              <li key={a.title}><Link to={a.to} className="attn">
                <Icon name={a.tone === "info" ? "info" : "warn"} size={16} className={a.tone === "bad" ? "text-danger" : a.tone === "warn" ? "text-accent" : "text-info"} />
                <span className="font-semibold min-w-0 flex-1 truncate">{a.title}</span>
                <span className="text-xs text-muted text-right shrink-0 max-w-[45%] truncate">{a.detail}</span>
                <Icon name="chevron" size={14} className="attn-go text-faint" />
              </Link></li>))}
            </ul>)}
        </section>
      </div>

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)] mb-3">
        <section className="dcard">
          <div className="dcard-head">
            <h2 className="dcard-title">Biggest usage differences</h2>
            {variance.data && <Link to="/variance" className="text-xs text-muted hover:text-accent">{d(variance.data.opening.counted_at)} – {d(variance.data.closing.counted_at)}</Link>}
          </div>
          {!seeVar ? <p className="text-sm text-muted">Needs Stock vs sales access.</p>
            : variance.error ? <p className="text-sm text-muted">Needs two posted counts, that period’s Swype sales and recipes.</p>
            : !variance.data ? <p className="text-sm text-muted">…</p>
            : vrows.length === 0 ? <p className="text-sm text-muted">No usage to compare yet.</p> : (
            <div className="overflow-x-auto"><table className="dt">
              <thead><tr><th>Item</th><th className="r">Expected usage</th><th className="r">Actual usage</th><th className="r">Difference</th><th className="r">Variance</th></tr></thead>
              <tbody>{vrows.slice(0, 5).map((r) => {
                const pct = r.variance_pct == null ? null : Number(r.variance_pct);
                return (
                  <tr key={r.item_id}>
                    <td className="font-medium">{r.name}</td>
                    <td className="r">{n(r.should_use, 1)} {r.stock_unit}</td>
                    <td className="r">{n(r.actual, 1)} {r.stock_unit}</td>
                    <td className="r">{signed(Number(r.variance))} {r.stock_unit}</td>
                    <td className="r">{pct == null ? <span className="tag tag-bad">no sales</span> : <span className={`tag ${pct > 0 ? "tag-bad" : "tag-good"}`}>{signed(pct)}%</span>}</td>
                  </tr>);
              })}</tbody>
            </table></div>)}
        </section>

        <section className="dcard">
          {seeRep ? (<>
            <div className="dcard-head"><h2 className="dcard-title">Running low ({risks.length})</h2><Link to="/reports/reorder" className="text-xs text-muted hover:text-accent">Purchasing</Link></div>
            {!reorder.data ? <p className="text-sm text-muted">…</p> : risks.length === 0
              ? <p className="text-sm text-muted">{reorder.data.counted ? "Nothing running low." : "Needs a posted count."}</p> : (
              <div className="overflow-x-auto"><table className="dt">
                <thead><tr><th>Item</th><th className="r">On hand</th><th className="r">Est. days left</th><th className="r">Reorder suggestion</th></tr></thead>
                <tbody>{risks.slice(0, 5).map((r) => {
                  const dl = r.days_left == null ? null : Number(r.days_left);
                  return (
                    <tr key={r.key}>
                      <td className="font-medium">{r.name}</td>
                      <td className="r">{stockLabel(r)}</td>
                      <td className="r">{dl == null ? "—" : <span className={`tag ${dl < 4 ? "tag-bad" : "tag-warn"}`}>≈ {n(dl, 0)} day{Math.round(dl) === 1 ? "" : "s"}</span>}</td>
                      <td className="r">{r.packs ? <Link to="/reports/reorder" className="inline-flex items-center gap-1 text-accent font-semibold"><Icon name="cart" size={14} />Order {r.packs} {r.pack_name ?? (r.packs === 1 ? "pack" : "packs")}</Link> : "—"}</td>
                    </tr>);
                })}</tbody>
              </table></div>)}
          </>) : <BelowPar items={items.data} />}
        </section>
      </div>

      {seeRep && <TopDrinks report={pour.data} />}
    </div>
  );
}

type Attn = { tone: "bad" | "warn" | "info"; title: string; detail: string; to: string };

function Kpi({ icon, label, value, delta, note, tone, to }: { icon: IconName; label: string; value: ReactNode; delta?: { text: string; good: boolean }; note?: ReactNode; tone?: "bad"; to?: string }) {
  const body = (
    <div className="dkpi">
      <Icon name={icon} size={22} className="text-accent mt-0.5" />
      <div className="min-w-0">
        <div className="dkpi-label">{label}</div>
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
          <div className={`dkpi-value ${tone === "bad" ? "text-danger" : ""}`}>{value}</div>
          {(delta || note) && <div className="dkpi-note leading-tight">
            {delta && <div className={`flex items-center gap-1 text-sm ${delta.good ? "delta-up" : "delta-down"}`}><Icon name={delta.good ? "up" : "down"} size={12} strokeWidth={2.5} />{delta.text}</div>}
            {note && <div>{note}</div>}
          </div>}
        </div>
      </div>
    </div>);
  return to ? <Link to={to} className="dcard block hover:border-line-strong">{body}</Link> : <div className="dcard">{body}</div>;
}

function RevenueCard({ report, allowed, change, days }: { report?: SalesReport | null; allowed: boolean; change: number | null; days: number }) {
  // one bar per sales period (a day for daily Swype imports)
  const bars = (report?.periods ?? []).map((p) => ({ ...p, net: Number(p.net) }));
  const max = Math.max(1, ...bars.map((b) => b.net));
  const step = niceStep(max / 4);
  const top = step * 4;
  return (
    <section className="dcard">
      <div className="dcard-head flex-wrap">
        <h2 className="dcard-title">Daily beverage revenue</h2>
        {report && <div className="text-sm"><span className="font-bold text-base">{money(report.summary.net)}</span> <span className="text-muted">total</span>
          {change != null && <span className={`ml-3 ${change >= 0 ? "delta-up" : "delta-down"}`}>{change >= 0 ? "▲" : "▼"} {signed(change)}%</span>}
          {change != null && <span className="text-muted ml-1">vs. prior {days} days</span>}</div>}
      </div>
      {!allowed ? <p className="text-sm text-muted">Needs Reports access.</p>
        : !report ? <p className="text-sm text-muted h-[220px]">…</p>
        : bars.length === 0 ? <p className="text-sm text-muted py-16 text-center">No Swype sales in this period. <Link className="underline" to="/sales">Import sales</Link></p> : (
        <div className="flex gap-2">
          <div className="flex flex-col justify-between text-[11px] text-muted text-right h-[190px] -mt-1.5 pb-0 shrink-0">
            {[4, 3, 2, 1, 0].map((k) => <span key={k} className="leading-none">${n(step * k, 0)}</span>)}
          </div>
          <div className="flex-1 min-w-0">
            <div className="bars bars-grid border-b border-line-strong">
              {bars.map((b) => (
                <div key={b.from} className="bar-col" title={`${b.from === b.to ? d(`${b.from}T12:00`) : `${b.from} – ${b.to}`}: ${money(b.net, 2)}`}>
                  {bars.length <= 14 && <span className="bar-val">{money(b.net)}</span>}
                  <div className="bar" style={{ height: `${(b.net / top) * 100}%` }} />
                </div>))}
            </div>
            <div className="grid grid-flow-col auto-cols-fr gap-2 mt-1.5">
              {bars.map((b, i) => {
                const t = atNoon(b.to);
                const show = bars.length <= 14 || i % Math.ceil(bars.length / 10) === 0;
                return <div key={b.from} className="bar-x">{show && <>{short(t)}<br />{bars.length <= 14 && t.toLocaleDateString("en-CA", { weekday: "short" })}</>}</div>;
              })}
            </div>
          </div>
        </div>)}
    </section>
  );
}

function niceStep(x: number) {
  const p = 10 ** Math.floor(Math.log10(Math.max(x, 1)));
  return ([1, 2, 2.5, 5, 10].find((m) => m * p >= x) ?? 10) * p;
}

function TopDrinks({ report }: { report?: PourReport | null }) {
  const rows = report ? [...report.rows, ...report.no_recipe].sort((a, b) => Number(b.revenue) - Number(a.revenue)).slice(0, 5) : [];
  return (
    <section className="dcard">
      <div className="dcard-head"><h2 className="dcard-title">Top drinks <span className="text-muted font-normal text-sm">(by beverage sales)</span></h2>
        <Link to="/reports/pour-cost" className="text-xs text-muted hover:text-accent">Pour cost</Link></div>
      {!report ? <p className="text-sm text-muted">…</p> : rows.length === 0 ? <p className="text-sm text-muted">No drink sales in this period.</p> : (
        <div className="overflow-x-auto"><table className="dt">
          <thead><tr><th>Rank</th><th>Drink</th><th>Category</th><th className="r">Units sold</th><th className="r">Beverage sales</th><th className="r">Beverage cost</th><th className="r">Cost %</th></tr></thead>
          <tbody>{rows.map((r, i) => {
            const cost = r.theoretical_cost == null ? null : Number(r.theoretical_cost);
            const pct = cost != null && Number(r.revenue) ? cost / Number(r.revenue) * 100 : null;
            return (
              <tr key={r.code}>
                <td><span className="rank">{i + 1}</span></td>
                <td><span className="inline-flex items-center gap-2 font-medium"><Icon name={drinkIcon(r.category)} size={16} className="text-ink-2" />{r.name}</span></td>
                <td className="text-ink-2">{r.category ?? "—"}</td>
                <td className="r">{n(r.sold, 0)}</td>
                <td className="r">{money(r.revenue)}</td>
                <td className="r">{cost == null ? <span className="text-faint">no recipe</span> : money(cost)}</td>
                <td className="r">{pct == null ? "—" : `${n(pct, 1)}%`}</td>
              </tr>);
          })}</tbody>
        </table></div>)}
    </section>
  );
}

function drinkIcon(cat: string | null): IconName {
  const c = (cat ?? "").toLowerCase();
  if (/coffee|espresso|latte|tea|hot/.test(c)) return "coffee";
  if (/beer|lager|ale|draught|draft|cider/.test(c)) return "beer";
  if (/wine|prosecco|champagne|sparkling/.test(c)) return "wine";
  if (/spirit|whisk|vodka|gin|rum|tequila|shot/.test(c)) return "bottle";
  return "glass";
}

/** Without Reports access: the lowest stock against par from the last count. */
function BelowPar({ items }: { items?: Item[] }) {
  const rows = (items ?? []).filter((i) => i.par_level && Number(i.par_level) > 0 && i.last_counted_packs != null)
    .map((i) => ({ ...i, on: Number(i.last_counted_packs), par: Number(i.par_level) })).filter((i) => i.on < i.par)
    .sort((a, b) => a.on / a.par - b.on / b.par);
  return (<>
    <div className="dcard-head"><h2 className="dcard-title">Below par ({rows.length})</h2><Link to="/items" className="text-xs text-muted hover:text-accent">Stock items</Link></div>
    {!items ? <p className="text-sm text-muted">…</p> : rows.length === 0 ? <p className="text-sm text-muted">Everything at or above par.</p> : (
      <table className="dt">
        <thead><tr><th>Item</th><th className="r">On hand</th><th className="r">Par</th></tr></thead>
        <tbody>{rows.slice(0, 5).map((i) => (
          <tr key={i.id}><td className="font-medium">{i.name}</td><td className="r"><span className="tag tag-warn">{n(i.on, 1)}</span></td><td className="r text-muted">{n(i.par, 1)}</td></tr>))}</tbody>
      </table>)}
  </>);
}
