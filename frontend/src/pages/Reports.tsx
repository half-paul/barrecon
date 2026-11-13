import { Fragment, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, d, n } from "../lib/api";
import { Button, Empty, Loading, Nil, Notice, Page, inputCls, useAsync } from "../lib/ui";
import { Tile } from "./BarPerformance";

type Num = string | number;
const num = (x: Num | null | undefined) => x == null ? null : Number(x);
const money = (x: Num | null | undefined, dp = 0) => x == null ? "—" : `${Number(x) < 0 ? "−" : ""}$${n(Math.abs(Number(x)), dp)}`;
const pct = (x: Num | null | undefined, dp = 1) => x == null ? "—" : `${n(x, dp)}%`;
const day = (s: string | null | undefined) => s ? d(s.length === 10 ? `${s}T12:00:00` : s) : "";
const plural = (k: number, w: string) => `${k} ${w}${k === 1 ? "" : "s"}`;

/** Report knobs live in the URL, so a report can be bookmarked or sent as a link. */
function useKnobs<K extends string>(defaults: Record<K, number>) {
  const [params, setParams] = useSearchParams();
  const values = Object.fromEntries(Object.entries(defaults).map(([k, v]) => {
    const raw = params.get(k);
    return [k, raw != null && raw !== "" && !Number.isNaN(Number(raw)) ? Number(raw) : v];
  })) as Record<K, number>;
  const set = (k: K, v: number) => {
    const p = new URLSearchParams(params);
    if (v === defaults[k]) p.delete(k); else p.set(k, String(v));
    setParams(p, { replace: true });
  };
  const query = new URLSearchParams(Object.entries(values).map(([k, v]) => [k, String(v)])).toString();
  return { values, set, query };
}

function Knob({ label, value, onChange, suffix, min, max, step = 1 }: { label: string; value: number; onChange: (v: number) => void; suffix: string; min: number; max: number; step?: number }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = () => { const v = Number(text); if (text !== "" && !Number.isNaN(v) && v >= min && v <= max) onChange(v); else setText(String(value)); };
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-muted">{label}</span>
      <span className="flex items-center gap-1.5">
        <input className={`${inputCls} w-20 num`} type="number" inputMode="decimal" min={min} max={max} step={step} value={text}
          onChange={(e) => setText(e.target.value)} onBlur={commit} onKeyDown={(e) => { if (e.key === "Enter") commit(); }} />
        <span className="text-muted">{suffix}</span>
      </span>
    </label>
  );
}

function Knobs({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-wrap gap-x-6 gap-y-3 mb-5 items-end">{children}</div>;
}

function Chip({ tone, children, title }: { tone: "bad" | "warn" | "ok" | "info"; children: React.ReactNode; title?: string }) {
  const cls = { bad: "border-danger text-danger", warn: "border-accent text-accent", ok: "border-ok text-ok", info: "border-line-strong text-muted" }[tone];
  return <span title={title} className={`inline-block whitespace-nowrap text-xs rounded-chip border px-2 py-0.5 ${cls}`}>{children}</span>;
}

function Coverage({ from, to, days }: { from: string; to: string; days: number }) {
  return <>{day(from)} to {day(to)} · {days ? `${plural(days, "day")} of Swype sales on file` : "no Swype sales on file"}</>;
}

// ---------- 1. Pour cost and profitability ----------

type PourRow = {
  code: string; name: string; category: string; sold: Num; revenue: Num; price: Num | null; menu_price: Num | null;
  serve_cost: Num | null; serve_cost_before: Num | null; cost_change_pct: Num | null; pour_cost_pct: Num | null;
  margin: Num | null; gross_profit: Num | null; missing_cost: string[]; flags: string[];
};
type PourReport = {
  from: string; to: string; days_covered: number; target_pct: Num; rise_pct: Num; compare_from: string;
  summary: { revenue: Num; theoretical_cost: Num; gross_profit: Num; pour_cost_pct: Num | null; drinks: number; flagged: number; uncosted_revenue: Num; no_recipe_revenue: Num };
  rows: PourRow[];
  rising: { item_id: string; name: string; pack_name: string | null; pack_cost: Num; pack_cost_before: Num; change_pct: Num; drinks: string[] }[];
  no_recipe: { code: string; name: string; category: string; sold: Num; revenue: Num }[];
};
const FLAG: Record<string, [string, "bad" | "warn" | "info", string]> = {
  loss: ["Sells at a loss", "bad", "Average price is at or below the ingredient cost. Often a case price entered as the item's cost per pack: check the stock item"],
  high_cost: ["High pour cost", "warn", "Pour cost at or above the target"],
  cost_rising: ["Cost rising", "warn", "Ingredient cost went up since the comparison date"],
  under_menu_price: ["Below menu price", "warn", "Average price is more than 10% under the menu price: discounts, comps or a wrong button"],
  missing_cost: ["Missing cost", "info", "An ingredient has no pack cost, so the drink can't be costed"],
};

export function PourCostReport() {
  const k = useKnobs({ days: 28, target: 25, rise: 5 });
  const r = useAsync(() => api.get<PourReport>(`/api/reports/pour-cost?${k.query}`), [k.query]);
  const [show, setShow] = useState<"flagged" | "all">("flagged");
  const p = r.data;
  const rows = p ? (show === "flagged" ? p.rows.filter((x) => x.flags.length) : p.rows) : [];
  const byCat = useMemo(() => {
    const m = new Map<string, PourRow[]>();
    for (const x of rows) m.set(x.category, [...(m.get(x.category) ?? []), x]);
    return [...m.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [rows]);

  return (
    <Page title="Pour cost & profitability" area="reports"
      sub={p ? <Coverage from={p.from} to={p.to} days={p.days_covered} /> : "Drink revenue against what the recipes say the ingredients cost"}>
      <Knobs>
        <Knob label="Sales window" value={k.values.days} onChange={(v) => k.set("days", v)} suffix="days" min={1} max={366} />
        <Knob label="Target pour cost" value={k.values.target} onChange={(v) => k.set("target", v)} suffix="%" min={0} max={100} />
        <Knob label="Flag cost rises over" value={k.values.rise} onChange={(v) => k.set("rise", v)} suffix="%" min={0} max={1000} />
      </Knobs>
      {r.error && <Notice kind="error">{r.error}</Notice>}
      {r.loading && !p && <Loading />}
      {p && (<>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 mb-6">
          <Tile label="Drink revenue" value={money(p.summary.revenue)} sub={`${plural(p.summary.drinks, "costed drink")} on file`} />
          <Tile label="Theoretical cost" value={money(p.summary.theoretical_cost)} sub="Recipe portions at today's pack costs" />
          <Tile label="Pour cost" value={pct(p.summary.pour_cost_pct)} tone={num(p.summary.pour_cost_pct) != null && num(p.summary.pour_cost_pct)! >= Number(p.target_pct) ? "bad" : undefined}
            sub={`Target ${n(p.target_pct, 0)}%`} />
          <Tile label="Gross profit" value={money(p.summary.gross_profit)} sub={`${plural(p.summary.flagged, "drink")} flagged`} />
        </div>
        {(num(p.summary.no_recipe_revenue)! > 0 || num(p.summary.uncosted_revenue)! > 0) && (
          <p className="text-sm text-muted mb-4">
            Not in the totals: {num(p.summary.no_recipe_revenue)! > 0 && <>{money(p.summary.no_recipe_revenue)} from {plural(p.no_recipe.length, "drink")} with no recipe (<Link className="underline" to="/recipes">add recipes</Link>)</>}
            {num(p.summary.no_recipe_revenue)! > 0 && num(p.summary.uncosted_revenue)! > 0 && "; "}
            {num(p.summary.uncosted_revenue)! > 0 && <>{money(p.summary.uncosted_revenue)} from drinks with an ingredient that has no cost</>}.
          </p>)}

        {p.rising.length > 0 && (
          <section className="mb-8">
            <h2 className="font-semibold mb-2">Rising ingredient costs <span className="text-muted font-normal text-sm">since {day(p.compare_from)}</span></h2>
            <div className="table-box"><table className="grid">
              <thead><tr><th>Item</th><th className="r">Was</th><th className="r">Now</th><th className="r">Change</th><th>Goes into</th></tr></thead>
              <tbody>{p.rising.map((x) => (
                <tr key={x.item_id}>
                  <td>{x.name}{x.pack_name && <span className="text-muted"> · {x.pack_name}</span>}</td>
                  <td className="r num">{money(x.pack_cost_before, 2)}</td>
                  <td className="r num">{money(x.pack_cost, 2)}</td>
                  <td className="r num text-accent">+{n(x.change_pct, 1)}%</td>
                  <td className="text-sm text-muted">{x.drinks.length ? x.drinks.slice(0, 4).join(", ") + (x.drinks.length > 4 ? ` +${x.drinks.length - 4}` : "") : "No recipe"}</td>
                </tr>))}</tbody>
            </table></div>
          </section>)}

        <div className="flex items-center justify-between gap-3 mb-2 flex-wrap">
          <h2 className="font-semibold">Drinks</h2>
          <div className="inline-flex rounded-control border border-line overflow-hidden text-sm" role="group" aria-label="Which drinks">
            {(["flagged", "all"] as const).map((v) => (
              <button key={v} aria-pressed={show === v} onClick={() => setShow(v)}
                className={`px-3 py-1.5 ${show === v ? "bg-raised text-ink font-medium" : "bg-surface text-muted hover:text-ink"}`}>
                {v === "flagged" ? `Flagged (${p.summary.flagged})` : `All (${p.rows.length})`}</button>))}
          </div>
        </div>
        {rows.length === 0 ? <Empty>{p.rows.length ? "Nothing flagged: every costed drink is under target with steady costs." : "No sold drinks with recipes in this window. Import Swype sales and add recipes first."}</Empty> : (
          <div className="table-box"><table className="grid">
            <thead><tr><th>Drink</th><th className="r">Sold</th><th className="r">Avg price</th><th className="r">Cost / serve</th><th className="r">Pour cost</th><th className="r">Margin</th><th className="r">Gross profit</th><th>Flags</th></tr></thead>
            <tbody>{byCat.map(([cat, xs]) => (<Fragment key={cat}>
              <tr className="section"><td colSpan={8}>{cat} <span className="text-faint font-normal">{xs.length}</span></td></tr>
              {xs.map((x) => (
                <tr key={x.code}>
                  <td>{x.name}<div className="text-xs text-faint num">{x.code}</div></td>
                  <td className="r num">{n(x.sold, 0)}</td>
                  <td className="r num whitespace-nowrap">{money(x.price, 2)}{x.menu_price != null && Math.abs(Number(x.menu_price) - Number(x.price)) > 0.01 && <div className="text-xs text-muted">menu {money(x.menu_price, 2)}</div>}</td>
                  <td className="r num whitespace-nowrap">{money(x.serve_cost, 2)}{x.cost_change_pct != null && Math.abs(Number(x.cost_change_pct)) >= 0.5 && <div className={`text-xs ${Number(x.cost_change_pct) > 0 ? "text-accent" : "text-ok"}`}>{Number(x.cost_change_pct) > 0 ? "▲" : "▼"} {n(Math.abs(Number(x.cost_change_pct)), 1)}%</div>}</td>
                  <td className={`r num ${num(x.pour_cost_pct) != null && num(x.pour_cost_pct)! >= Number(p.target_pct) ? "text-danger font-medium" : ""}`}>{pct(x.pour_cost_pct)}</td>
                  <td className="r num">{money(x.margin, 2)}</td>
                  <td className="r num">{money(x.gross_profit)}</td>
                  <td><div className="flex flex-wrap gap-1">{x.flags.map((f) => <Chip key={f} tone={FLAG[f][1]} title={f === "missing_cost" ? `No cost: ${x.missing_cost.join(", ")}` : FLAG[f][2]}>{FLAG[f][0]}</Chip>)}</div></td>
                </tr>))}
            </Fragment>))}</tbody>
          </table></div>)}
        <p className="text-xs text-faint mt-3">Avg price is net sales ÷ units rung in, so comps, promos and combos pull it down. A line that pours from any of several items is costed at their average.</p>
      </>)}
    </Page>
  );
}

// ---------- 2. Purchasing and reorder ----------

type ReorderRow = {
  key: string; item_id: string; sku: string; name: string; category: string | null; stock_unit: string; pack_name: string | null; pack_size: Num; case_size: Num;
  substitutes: { item_id: string; name: string }[]; used: Num; daily: Num; on_hand: Num | null; on_hand_packs: Num | null; par_packs: Num | null;
  need: Num; days_left: Num | null; packs: number; cases: number | null; pack_cost: Num | null; line_cost: Num | null;
  supplier: string | null; last_delivered: string | null; drinks: string[];
  status: "stockout" | "before_delivery" | "reorder" | "no_stock_data" | "excess" | "ok";
};
type ReorderReport = {
  from: string; to: string; days_covered: number; lead_days: number; cover_days: number; demand_pct: Num; counted: boolean;
  summary: { lines: number; cost: Num; at_risk: number; excess: number }; rows: ReorderRow[];
};
const STATUS: Record<ReorderRow["status"], [string, "bad" | "warn" | "ok" | "info"]> = {
  stockout: ["Out", "bad"], before_delivery: ["Runs out before delivery", "bad"], reorder: ["Reorder", "warn"],
  no_stock_data: ["Not counted", "info"], excess: ["Overstocked", "info"], ok: ["OK", "ok"],
};

export function ReorderReport() {
  const k = useKnobs({ days: 28, lead: 3, cover: 7, demand: 0 });
  const r = useAsync(() => api.get<ReorderReport>(`/api/reports/reorder?${k.query}`), [k.query]);
  const p = r.data;
  const [qty, setQty] = useState<Record<string, number>>({});
  const [approved, setApproved] = useState<{ at: Date; lines: (ReorderRow & { order: number })[] } | null>(null);
  useEffect(() => { setQty(Object.fromEntries((p?.rows ?? []).map((x) => [x.key, x.packs]))); setApproved(null); }, [p]);
  const [show, setShow] = useState<"order" | "all">("order");

  const lines = (p?.rows ?? []).map((x) => ({ ...x, order: qty[x.key] ?? x.packs })).filter((x) => x.order > 0);
  const total = lines.reduce((s, x) => s + (x.pack_cost != null ? Number(x.pack_cost) * x.order : 0), 0);
  const rows = p ? (show === "order" ? p.rows.filter((x) => x.packs > 0 || (qty[x.key] ?? 0) > 0 || x.status === "before_delivery" || x.status === "stockout") : p.rows) : [];

  return (
    <Page title="Purchasing & reorder" area="reports"
      sub={p ? <Coverage from={p.from} to={p.to} days={p.days_covered} /> : "What to buy, from recent sales, lead time and stock on hand"}>
      <Knobs>
        <Knob label="Sales history" value={k.values.days} onChange={(v) => k.set("days", v)} suffix="days" min={1} max={366} />
        <Knob label="Lead time" value={k.values.lead} onChange={(v) => k.set("lead", v)} suffix="days" min={0} max={90} />
        <Knob label="Buy enough for" value={k.values.cover} onChange={(v) => k.set("cover", v)} suffix="days" min={1} max={180} />
        <Knob label="Upcoming demand" value={k.values.demand} onChange={(v) => k.set("demand", v)} suffix="% vs recent" min={-90} max={500} step={5} />
      </Knobs>
      <p className="text-xs text-faint -mt-3 mb-5">Expecting an event or a holiday? Raise upcoming demand (+50% for a week half again as busy). Quiet week: go negative.</p>
      {r.error && <Notice kind="error">{r.error}</Notice>}
      {r.loading && !p && <Loading />}
      {p && !p.counted && <Notice>No posted count yet, so stock on hand is unknown. Suggestions below assume an empty shelf; post a count for real numbers.</Notice>}
      {p && p.counted && p.rows.some((x) => x.status === "no_stock_data") && <p className="text-sm text-muted mb-2">Items marked “Not counted” weren’t on the last count, so their suggestion assumes none on hand.</p>}
      {p && (<>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 my-6">
          <Tile label="Suggested order" value={plural(p.summary.lines, "line")} sub={`${money(p.summary.cost)} at last pack costs`} />
          <Tile label="Stockout risk" value={String(p.summary.at_risk)} tone={p.summary.at_risk ? "bad" : undefined} sub={`Out, or runs out within ${plural(p.lead_days, "day")} lead time`} />
          <Tile label="Overstocked" value={String(p.summary.excess)} sub={<Link className="underline" to="/reports/slow-moving">See slow-moving stock</Link>} />
          <Tile label="Your order" value={money(total)} sub={`${plural(lines.length, "line")}, ${lines.reduce((s, x) => s + x.order, 0)} packs`} />
        </div>

        {approved ? <ApprovedOrder order={approved} onEdit={() => setApproved(null)} /> : (<>
          <div className="flex items-center justify-between gap-3 mb-2 flex-wrap">
            <div className="inline-flex rounded-control border border-line overflow-hidden text-sm" role="group" aria-label="Which items">
              {(["order", "all"] as const).map((v) => (
                <button key={v} aria-pressed={show === v} onClick={() => setShow(v)}
                  className={`px-3 py-1.5 ${show === v ? "bg-raised text-ink font-medium" : "bg-surface text-muted hover:text-ink"}`}>
                  {v === "order" ? "To order" : `All tracked (${p.rows.length})`}</button>))}
            </div>
            <Button disabled={!lines.length} onClick={() => setApproved({ at: new Date(), lines })}>Approve order ({lines.length})</Button>
          </div>
          {rows.length === 0 ? <Empty>{p.rows.length ? "Nothing to order: stock covers the lead time plus the days you buy for." : "No items with recipe sales or par levels yet."}</Empty> : (
            <div className="table-box"><table className="grid">
              <thead><tr><th>Item</th><th className="r">On hand</th><th className="r">Per day</th><th className="r">Days left</th><th className="r">Suggested</th><th className="r">Order</th><th className="r">Cost</th><th>Status</th></tr></thead>
              <tbody>{rows.map((x) => {
                const o = qty[x.key] ?? x.packs;
                const pack = x.pack_name ?? "pack";
                return (
                  <tr key={x.key}>
                    <td>
                      {x.name}<span className="text-muted text-sm"> · {x.category ?? "No group"}</span>
                      {x.substitutes.length > 0 && <div className="text-xs text-muted">or {x.substitutes.map((s) => s.name).join(", ")} (stock and demand combined)</div>}
                      <div className="text-xs text-faint">{x.drinks.length ? `${plural(x.drinks.length, "drink")}: ${x.drinks.slice(0, 3).join(", ")}${x.drinks.length > 3 ? "…" : ""}` : "Par level only"}{x.supplier ? ` · last from ${x.supplier}` : ""}</div>
                    </td>
                    <td className="r num whitespace-nowrap">{x.on_hand_packs == null ? <Nil /> : `${n(x.on_hand_packs, 1)} ${pack}`}{x.par_packs != null && <div className="text-xs text-muted">par {n(x.par_packs, 1)}</div>}</td>
                    <td className="r num whitespace-nowrap">{Number(x.daily) ? `${n(Number(x.daily) / Number(x.pack_size || 1), 2)} ${pack}` : <Nil />}</td>
                    <td className="r num">{x.days_left == null ? <Nil /> : n(x.days_left, 0)}</td>
                    <td className="r num">{x.packs || <Nil />}{x.cases != null && <div className="text-xs text-muted">{plural(x.cases, "case")} of {n(x.case_size, 0)}</div>}</td>
                    <td className="r"><input className={`${inputCls} w-16 num text-right`} type="number" min={0} step={1} aria-label={`Order ${x.name}`} value={o}
                      onChange={(e) => setQty((q) => ({ ...q, [x.key]: Math.max(0, Math.round(Number(e.target.value) || 0)) }))} /></td>
                    <td className="r num">{x.pack_cost != null && o ? money(Number(x.pack_cost) * o, 2) : <Nil />}</td>
                    <td><Chip tone={STATUS[x.status][1]}>{STATUS[x.status][0]}</Chip></td>
                  </tr>);
              })}</tbody>
            </table></div>)}
          <p className="text-xs text-faint mt-3">Suggested = (per day × (lead time + days you buy for), or par if higher) − on hand, rounded up to whole packs. On hand is the last count plus deliveries, minus what Swype sales poured since.</p>
        </>)}
      </>)}
    </Page>
  );
}

function ApprovedOrder({ order, onEdit }: { order: { at: Date; lines: (ReorderRow & { order: number })[] }; onEdit: () => void }) {
  const bySupplier = new Map<string, typeof order.lines>();
  for (const x of order.lines) bySupplier.set(x.supplier ?? "No supplier on file", [...(bySupplier.get(x.supplier ?? "No supplier on file") ?? []), x]);
  const total = order.lines.reduce((s, x) => s + (x.pack_cost != null ? Number(x.pack_cost) * x.order : 0), 0);
  const csv = () => {
    const esc = (v: unknown) => { const s = String(v ?? ""); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const out = [["Supplier", "SKU", "Item", "Pack", "Qty", "Pack cost", "Line cost"].join(",")];
    for (const [sup, xs] of bySupplier) for (const x of xs)
      out.push([sup, x.sku, x.name, x.pack_name ?? "", x.order, x.pack_cost ?? "", x.pack_cost != null ? (Number(x.pack_cost) * x.order).toFixed(2) : ""].map(esc).join(","));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([out.join("\n")], { type: "text/csv" }));
    a.download = `purchase-order-${order.at.toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  return (
    <section className="card p-5 print:shadow-none" aria-label="Approved purchase order">
      <div className="flex items-start justify-between gap-3 flex-wrap mb-4">
        <div>
          <h2 className="text-lg font-semibold">Purchase order</h2>
          <p className="text-sm text-muted">Approved {order.at.toLocaleString("en-CA", { dateStyle: "medium", timeStyle: "short" })} · {plural(order.lines.length, "line")} · {money(total, 2)}</p>
        </div>
        <div className="flex gap-2 print:hidden">
          <Button kind="quiet" onClick={onEdit}>Edit</Button>
          <Button kind="quiet" onClick={() => window.print()}>Print</Button>
          <Button onClick={csv}>Download CSV</Button>
        </div>
      </div>
      {[...bySupplier.entries()].map(([sup, xs]) => (
        <div key={sup} className="mb-5">
          <h3 className="font-semibold mb-1">{sup}</h3>
          <div className="table-box"><table className="grid">
            <thead><tr><th>SKU</th><th>Item</th><th className="r">Qty</th><th className="r">Pack cost</th><th className="r">Line</th></tr></thead>
            <tbody>{xs.map((x) => (
              <tr key={x.key}><td className="num text-muted">{x.sku}</td><td>{x.name}</td><td className="r num">{x.order} {x.pack_name ?? "pack"}</td>
                <td className="r num">{money(x.pack_cost, 2)}</td><td className="r num">{x.pack_cost != null ? money(Number(x.pack_cost) * x.order, 2) : <Nil />}</td></tr>))}</tbody>
          </table></div>
        </div>))}
      <p className="text-xs text-faint">Send this to your suppliers. When the delivery arrives, record it under Deliveries as usual.</p>
    </section>
  );
}

// ---------- 3. Slow-moving and dead stock ----------

type SlowRow = {
  item_id: string; sku: string; name: string; category: string | null; stock_unit: string; pack_name: string | null; pack_size: Num;
  on_hand: Num; on_hand_packs: Num | null; value: Num | null; daily: Num; days_cover: Num | null; last_sold: string | null; days_since_sold: number | null;
  last_delivered: string | null; perishable: boolean; expiry_risk: boolean; status: "dead" | "no_recipe" | "slow" | "expiry"; action: string; drinks: string[];
};
type SlowReport = {
  from: string; to: string; days_covered: number; slow_days: number; perish_days: number; counted: boolean;
  summary: { tied_up: Num; by_status: Partial<Record<SlowRow["status"], Num>>; stock_value: Num; items: number; uncosted: number }; rows: SlowRow[];
};
const SLOW: Record<SlowRow["status"], [string, "bad" | "warn" | "info"]> = {
  dead: ["Dead", "bad"], no_recipe: ["No recipe", "info"], slow: ["Slow", "warn"], expiry: ["May expire", "warn"],
};

export function SlowMovingReport() {
  const k = useKnobs({ days: 90, slow: 90, perish: 30 });
  const r = useAsync(() => api.get<SlowReport>(`/api/reports/slow-moving?${k.query}`), [k.query]);
  const p = r.data;
  const [filter, setFilter] = useState<SlowRow["status"] | "all">("all");
  const rows = p ? p.rows.filter((x) => filter === "all" || x.status === filter) : [];
  const share = p && Number(p.summary.stock_value) ? Number(p.summary.tied_up) / Number(p.summary.stock_value) * 100 : null;

  return (
    <Page title="Slow-moving & dead stock" area="reports"
      sub={p ? <Coverage from={p.from} to={p.to} days={p.days_covered} /> : "Stock that isn't selling, and the money sitting in it"}>
      <Knobs>
        <Knob label="Look back" value={k.values.days} onChange={(v) => k.set("days", v)} suffix="days" min={7} max={730} />
        <Knob label="Slow when stock lasts over" value={k.values.slow} onChange={(v) => k.set("slow", v)} suffix="days" min={7} max={730} />
        <Knob label="Perishables: flag over" value={k.values.perish} onChange={(v) => k.set("perish", v)} suffix="days on hand" min={1} max={365} />
      </Knobs>
      {r.error && <Notice kind="error">{r.error}</Notice>}
      {r.loading && !p && <Loading />}
      {p && !p.counted && <Notice>No posted count yet: this report needs stock on hand from a count.</Notice>}
      {p && p.counted && (<>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 my-6">
          <Tile label="Tied up" value={money(p.summary.tied_up)} tone={Number(p.summary.tied_up) > 0 ? "bad" : undefined}
            sub={share != null ? `${n(share, 0)}% of ${money(p.summary.stock_value)} on hand` : "At current pack costs"} />
          <Tile label="Dead stock" value={money(p.summary.by_status.dead ?? 0)} sub={`Not sold in ${plural(p.days_covered || Number(k.values.days), "day")}`} />
          <Tile label="Slow movers" value={money(p.summary.by_status.slow ?? 0)} sub={`Over ${p.slow_days} days of stock`} />
          <Tile label="No recipe / may expire" value={money(Number(p.summary.by_status.no_recipe ?? 0) + Number(p.summary.by_status.expiry ?? 0))}
            sub={p.summary.uncosted ? `${plural(p.summary.uncosted, "item")} without cost not counted` : "Sales can't use it up, or it's perishable"} />
        </div>
        <div className="flex items-center gap-3 mb-2 flex-wrap">
          <div className="inline-flex rounded-control border border-line overflow-hidden text-sm" role="group" aria-label="Filter">
            {(["all", "dead", "slow", "expiry", "no_recipe"] as const).map((v) => {
              const c = v === "all" ? p.rows.length : p.rows.filter((x) => x.status === v).length;
              return <button key={v} aria-pressed={filter === v} onClick={() => setFilter(v)}
                className={`px-3 py-1.5 ${filter === v ? "bg-raised text-ink font-medium" : "bg-surface text-muted hover:text-ink"}`}>{v === "all" ? "All" : SLOW[v][0]} ({c})</button>;
            })}
          </div>
        </div>
        {rows.length === 0 ? <Empty>{p.rows.length ? "None in this group." : "Nothing slow: every counted item sells through within the limits above."}</Empty> : (
          <div className="table-box"><table className="grid">
            <thead><tr><th>Item</th><th className="r">On hand</th><th className="r">Value</th><th className="r">Days of stock</th><th className="r">Last sold</th><th>Status</th><th>Suggested action</th></tr></thead>
            <tbody>{rows.map((x) => (
              <tr key={x.item_id}>
                <td>{x.name}<span className="text-muted text-sm"> · {x.category ?? "No group"}</span>
                  {x.drinks.length > 0 && <div className="text-xs text-faint">{x.drinks.slice(0, 3).join(", ")}{x.drinks.length > 3 ? "…" : ""}</div>}</td>
                <td className="r num whitespace-nowrap">{x.on_hand_packs != null ? `${n(x.on_hand_packs, 1)} ${x.pack_name ?? "pack"}` : `${n(x.on_hand, 0)} ${x.stock_unit}`}</td>
                <td className="r num">{money(x.value)}</td>
                <td className="r num">{x.days_cover == null ? "∞" : n(x.days_cover, 0)}</td>
                <td className="r num whitespace-nowrap">{x.last_sold ? <>{day(x.last_sold)}<div className="text-xs text-muted">{plural(x.days_since_sold ?? 0, "day")} ago</div></> : <span className="text-faint">never</span>}</td>
                <td><div className="flex flex-wrap gap-1"><Chip tone={SLOW[x.status][1]}>{SLOW[x.status][0]}</Chip>{x.expiry_risk && x.status !== "expiry" && <Chip tone="warn">May expire</Chip>}</div></td>
                <td className="text-sm text-muted">{x.action}</td>
              </tr>))}</tbody>
          </table></div>)}
        <p className="text-xs text-faint mt-3">On hand is the last count plus deliveries, minus what Swype sales poured since. "May expire" is a guess from the item's name and group (beer, wine, juice, garnish…): expiry dates aren't recorded.</p>
      </>)}
    </Page>
  );
}
