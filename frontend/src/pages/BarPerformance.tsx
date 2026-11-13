import { Link } from "react-router-dom";
import { api, d, n } from "../lib/api";
import { can, useMe } from "../lib/auth";
import { useAsync } from "../lib/ui";

type Num = string | number;
type Usage = { from: string; to: string; used: Num; sales: Num; pct: Num | null; variance_cost: Num | null };
export type Performance = {
  week: { from: string; to: string };
  net_sales: Num | null; prior_net_sales: Num | null;
  sales_periods: [string, string][];
  categories: { category: string; net: Num }[];
  beverage_cost: Usage | null; prior_beverage_cost: Usage | null;
  stock: { value: Num; counted_at: string; uncosted: number } | null;
};

const money = (x: Num) => `${Number(x) < 0 ? "−" : ""}$${n(Math.abs(Number(x)), 0)}`;
const day = (s: string) => d(`${s}T12:00:00`);

/** The Overview's owner stats: sales, beverage cost, variance and stock value. Needs Stock vs sales access. */
export default function BarPerformance() {
  const me = useMe();
  if (!can(me, "variance")) return null;
  return <Dashboard />;
}

function Dashboard() {
  const r = useAsync(() => api.get<Performance>("/api/variance/performance"));
  const p = r.data;
  if (r.error) return null;

  const sales = p?.net_sales != null ? Number(p.net_sales) : null;
  const prior = p?.prior_net_sales != null ? Number(p.prior_net_sales) : null;
  const change = sales != null && prior ? (sales - prior) / prior * 100 : null;
  const bc = p?.beverage_cost, pbc = p?.prior_beverage_cost;
  const pp = bc?.pct != null && pbc?.pct != null ? Number(bc.pct) - Number(pbc.pct) : null;
  const variance = bc?.variance_cost != null ? Number(bc.variance_cost) : null;

  return (
    <section className="card p-5 mb-8">
      <div className="flex items-start justify-between gap-3 mb-4">
        <div>
          <h2 className="text-xl font-semibold">Bar Performance</h2>
          <p className="text-sm text-muted">{p ? `${day(p.week.from)} to ${day(p.week.to)}` : "…"}</p>
        </div>
        <span className="rounded-chip bg-raised px-2.5 py-1 text-sm font-medium">Last 7 days</span>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Tile label="Net sales" value={p ? (sales != null ? money(sales) : "No sales") : "…"}
          sub={change != null ? `${change >= 0 ? "+" : "−"}${n(Math.abs(change), 1)}% vs prior week`
            : sales != null ? "No sales on file for the week before"
            : <Link className="underline" to="/imports">Import Swype Item Sales</Link>} />
        <Tile label="Beverage cost" value={p ? (bc?.pct != null ? `${n(bc.pct, 1)}%` : "—") : "…"}
          sub={bc?.pct != null
            ? `${pp != null ? `${pp >= 0 ? "+" : "−"}${n(Math.abs(pp), 1)} pp vs prior count · ` : ""}${money(bc.used)} used, ${d(bc.from)} to ${d(bc.to)}`
            : bc ? "No drink sales on file between the last two counts" : "Needs two posted counts"} />
        <Tile label="Inventory variance" value={p ? (variance != null ? money(variance) : "—") : "…"}
          tone={variance != null && variance < 0 ? "bad" : undefined}
          sub={variance != null ? <>{variance < 0 ? "Unexplained shortfall" : "No shortfall"}, <Link className="underline" to="/variance">last count period</Link></>
            : "Needs two posted counts and costed items"} />
        <Tile label="Inventory at cost" value={p ? (p.stock ? money(p.stock.value) : "—") : "…"}
          sub={p?.stock ? `Counted ${d(p.stock.counted_at)}${p.stock.uncosted ? `, ${p.stock.uncosted} item${p.stock.uncosted === 1 ? "" : "s"} without cost` : ""}`
            : "No posted count yet"} />
      </div>

      {p && p.categories.length > 0 && <Categories rows={p.categories} />}
      {p && p.sales_periods.length > 0 && (
        <p className="text-xs text-faint mt-3">
          Net sales from {p.sales_periods.length} imported Swype report{p.sales_periods.length > 1 ? "s" : ""} ending in the week
          ({p.sales_periods.map(([a, b]) => a === b ? day(a) : `${day(a)}–${day(b)}`).join(", ")}).
        </p>)}
    </section>
  );
}

export function Tile({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub: React.ReactNode; tone?: "bad" }) {
  return (
    <div className="rounded-lg bg-raised p-4">
      <div className="label">{label}</div>
      <div className={`text-3xl font-semibold num mt-1 ${tone === "bad" ? "text-danger" : ""}`}>{value}</div>
      <div className="text-xs text-muted mt-1">{sub}</div>
    </div>
  );
}

function Categories({ rows }: { rows: { category: string; net: Num }[] }) {
  const top = rows.slice(0, 8);
  const rest = rows.slice(8).reduce((s, r) => s + Number(r.net), 0);
  const bars = rest > 0 ? [...top.map((r) => ({ label: r.category, v: Number(r.net) })), { label: "Other", v: rest }]
    : top.map((r) => ({ label: r.category, v: Number(r.net) }));
  const max = Math.max(...bars.map((b) => b.v), 1);
  return (
    <div className="mt-6">
      <h3 className="font-semibold mb-3">Revenue by category</h3>
      <ul className="space-y-2">
        {bars.map((b) => (
          <li key={b.label} className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)_5rem] items-center gap-3 text-sm">
            <span className="truncate" title={b.label}>{b.label}</span>
            <span className="h-5 rounded bg-line overflow-hidden">
              <span className="block h-full rounded bg-accent" style={{ width: `${Math.max(b.v / max * 100, 0.5)}%` }} />
            </span>
            <span className="num text-right">{money(b.v)}</span>
          </li>))}
      </ul>
    </div>
  );
}
