import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, d, n, cents } from "../lib/api";
import { Button, Empty, Loading, Notice, Page, inputCls, useAsync } from "../lib/ui";
import { Tile } from "./BarPerformance";

type Num = string | number;
type Period = { from: string; to: string; qty: Num; net: Num };
type Row = {
  code: string; name: string; category: string; department: string;
  qty: Num; paid_qty: Num; promo_qty: Num; free_qty: Num; gross: Num; net: Num;
  avg_price: Num | null; share_pct: Num | null; series_qty: Num[]; series_net: Num[];
};
type Report = {
  from: string; to: string; days_covered: number; bounds: { from: string | null; to: string | null };
  summary: { qty: Num; net: Num; gross: Num; items: number; avg_price: Num | null; per_day: Num | null };
  periods: Period[]; rows: Row[];
};
type Metric = "net" | "qty";
type SortKey = "name" | "category" | "qty" | "promo" | "discount" | "net" | "avg_price" | "share_pct";

const money = (x: Num | null | undefined, dp = 2) => x == null ? "—" : `${Number(x) < 0 ? "−" : ""}$${cents(Math.abs(Number(x)))}`;
const compact = (v: number, m: Metric) => {
  const s = Math.abs(v) >= 1e6 ? `${n(v / 1e6, 1)}M` : Math.abs(v) >= 1e4 ? `${n(v / 1e3, 0)}k` : Math.abs(v) >= 1e3 ? `${n(v / 1e3, 1)}k` : n(v, 0);
  return m === "net" ? `$${s}` : s;
};

// Dates as whole UTC day numbers, so "YYYY-MM-DD" never shifts with the browser's time zone.
const DAY = 864e5;
const dayNum = (s: string) => { const [y, m, dd] = s.split("-").map(Number); return Date.UTC(y, m - 1, dd) / DAY; };
const iso = (k: number) => new Date(Math.round(k) * DAY).toISOString().slice(0, 10);
const label = (s: string) => d(`${s}T12:00:00`);
const short = (k: number) => new Date(Math.round(k) * DAY).toLocaleDateString("en-CA", { month: "short", day: "numeric", timeZone: "UTC" });
const todayIso = () => { const t = new Date(); return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}`; };
const span = (p: { from: string; to: string }) => p.from === p.to ? label(p.from) : `${label(p.from)} to ${label(p.to)}`;

function niceTicks(max: number, count = 4) {
  if (max <= 0) return [0, 1];
  const raw = max / count, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((f) => f * mag).find((s) => s >= raw) ?? raw;
  const top = Math.ceil(max / step) * step;
  return Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step);
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(800);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

// ---------- chart: one coloured line per item; drag across it to zoom into those dates ----------

// Distinct colour per item: hues step by the golden angle (never repeat), lightness/saturation alternate
// so neighbours stay apart. Keyed by the item's rank in the whole report, so a colour stays put while
// items are picked or hidden.
const colorOf = (i: number) => `hsl(${Math.round((i * 137.508 + 215) % 360)} ${[72, 58, 85][i % 3]}% ${[44, 34, 54][Math.floor(i / 3) % 3]}%)`;

function SalesChart({ report, metric, lines, colors, hidden, onToggle, onZoom }: {
  report: Report; metric: Metric; lines: Row[]; colors: Map<string, string>; hidden: Set<string>;
  onToggle: (code: string) => void; onZoom: (from: string, to: string) => void;
}) {
  const [box, width] = useWidth<HTMLDivElement>();
  const H = 340, M = { t: 14, r: 60, b: 30, l: 60 };
  const iw = width - M.l - M.r, ih = H - M.t - M.b;
  const d0 = dayNum(report.from), d1 = dayNum(report.to) + 1;
  const x = (k: number) => M.l + (k - d0) / Math.max(d1 - d0, 1) * iw;
  const xInv = (px: number) => d0 + (px - M.l) / iw * (d1 - d0);

  // each period plotted at its middle day
  const pts = report.periods.map((p) => ({ p, a: dayNum(p.from), b: dayNum(p.to) + 1, mid: (dayNum(p.from) + dayNum(p.to) + 1) / 2 }));
  const visible = lines.filter((l) => !hidden.has(l.code));
  const val = (l: Row, i: number) => Number(metric === "net" ? l.series_net[i] : l.series_qty[i]);
  const tot = (i: number) => Number(metric === "net" ? report.periods[i].net : report.periods[i].qty);
  // Both scales come from the whole report, not from what is shown, so picking or hiding lines never rescales.
  const ticks = niceTicks(Math.max(0, ...pts.map((_, i) => tot(i))));
  const yMax = ticks[ticks.length - 1] || 1;
  const y = (v: number) => M.t + ih - v / yMax * ih;
  const ticks2 = niceTicks(Math.max(0, ...report.rows.flatMap((l) => pts.map((_, i) => val(l, i)))));
  const yMax2 = ticks2[ticks2.length - 1] || 1;
  const y2 = (v: number) => M.t + ih - v / yMax2 * ih;

  const xTicks = useMemo(() => {
    const want = Math.max(2, Math.floor(iw / 90)), stepDays = Math.max(1, Math.ceil((d1 - d0) / want));
    const out: number[] = [];
    for (let k = d0; k <= d1 - 1; k += stepDays) out.push(k);
    return out;
  }, [d0, d1, iw]);

  const [drag, setDrag] = useState<{ a: number; b: number } | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const px = (e: React.PointerEvent) => {
    const r = svg.current!.getBoundingClientRect();
    return Math.min(Math.max(e.clientX - r.left, M.l), M.l + iw);
  };

  const finish = () => {
    if (!drag) return;
    const lo = Math.min(drag.a, drag.b), hi = Math.max(drag.a, drag.b);
    setDrag(null);
    if (hi - lo < 6) return; // a click, not a drag
    const a = xInv(lo), b = xInv(hi);
    // Sales are period totals, so snap to the whole periods the selection touches.
    const hit = pts.filter((r) => r.b > a && r.a < b);
    if (hit.length) onZoom(hit[0].p.from, hit[hit.length - 1].p.to);
    else onZoom(iso(Math.floor(a)), iso(Math.max(Math.ceil(b) - 1, Math.floor(a))));
  };

  useEffect(() => {
    if (!drag) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setDrag(null); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [drag]);

  // hover snaps to the nearest period
  const hi = hover == null || !pts.length ? -1
    : pts.reduce((best, r, i) => Math.abs(x(r.mid) - hover) < Math.abs(x(pts[best].mid) - hover) ? i : best, 0);
  const unit = metric === "net" ? (v: number) => money(v, 2) : (v: number) => n(v, 2);
  const tipRows = hi < 0 ? [] : [...visible].map((l) => ({ l, v: val(l, hi) })).sort((a, b) => b.v - a.v);

  return (
    <div ref={box} className="relative select-none">
      <svg ref={svg} width={width} height={H} role="img" className="block touch-none cursor-crosshair"
        aria-label={`${metric === "net" ? "Net sales" : "Quantity sold"} per item by sales period, ${span(report)}. Drag across the chart to zoom into a date range.`}
        onPointerDown={(e) => { if (e.button !== 0) return; svg.current!.setPointerCapture(e.pointerId); const p = px(e); setDrag({ a: p, b: p }); }}
        onPointerMove={(e) => { const p = px(e); setHover(p); if (drag) setDrag({ ...drag, b: p }); }}
        onPointerUp={finish} onPointerCancel={() => setDrag(null)} onPointerLeave={() => { if (!drag) setHover(null); }}>
        {pts.map((r, i) => i % 2 === 1 && (
          <rect key={r.p.from} x={x(r.a)} y={M.t} width={x(r.b) - x(r.a)} height={ih} fill="var(--color-info)" opacity={0.05} />))}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={M.l} x2={M.l + iw} y1={y(t)} y2={y(t)} stroke="var(--color-line)" />
            <text x={M.l - 8} y={y(t)} dy="0.32em" textAnchor="end" className="num" fontSize={11} fill="var(--color-muted)">{compact(t, metric)}</text>
          </g>))}
        {ticks2.map((t) => (
          <text key={t} x={M.l + iw + 8} y={y2(t)} dy="0.32em" className="num" fontSize={11} fill="var(--color-ink-2)">{compact(t, metric)}</text>))}
        <text transform={`translate(12 ${M.t + ih / 2}) rotate(-90)`} textAnchor="middle" fontSize={11} fill="var(--color-muted)">Total, all items (bars)</text>
        <text transform={`translate(${width - 8} ${M.t + ih / 2}) rotate(90)`} textAnchor="middle" fontSize={11} fill="var(--color-ink-2)">Per item (lines)</text>
        {pts.map((r, i) => (
          <rect key={`bar${r.p.from}`} x={x(r.a) + 2} y={y(tot(i))} width={Math.max(x(r.b) - x(r.a) - 4, 1)} height={Math.max(y(0) - y(tot(i)), 0)}
            rx={2} fill="var(--color-muted)" opacity={i === hi ? 0.5 : 0.3} />))}
        {xTicks.map((k) => (
          <text key={k} x={x(k + 0.5)} y={H - 8} textAnchor="middle" fontSize={11} fill="var(--color-muted)">{short(k)}</text>))}
        {visible.map((l) => {
          const c = colors.get(l.code)!;
          const d = pts.map((r, i) => `${i ? "L" : "M"}${x(r.mid).toFixed(1)},${y2(val(l, i)).toFixed(1)}`).join("");
          return (
            <g key={l.code}>
              <path d={d} fill="none" stroke={c} strokeWidth={2.25} strokeLinejoin="round" strokeLinecap="round" />
              {pts.length <= 40 && pts.map((r, i) => <circle key={i} cx={x(r.mid)} cy={y2(val(l, i))} r={i === hi ? 4 : 2.5} fill={c} />)}
            </g>);
        })}
        {hi >= 0 && !drag && <line x1={x(pts[hi].mid)} x2={x(pts[hi].mid)} y1={M.t} y2={M.t + ih} stroke="var(--color-muted)" strokeDasharray="2 2" pointerEvents="none" />}
        {drag && Math.abs(drag.b - drag.a) > 1 && (
          <rect x={Math.min(drag.a, drag.b)} y={M.t} width={Math.abs(drag.b - drag.a)} height={ih}
            fill="var(--color-info)" fillOpacity={0.15} stroke="var(--color-info)" strokeDasharray="3 2" />)}
      </svg>
      {drag && Math.abs(drag.b - drag.a) > 6 && (
        <div className="absolute top-1 text-xs rounded bg-ink text-paper px-2 py-1 pointer-events-none num"
          style={{ left: Math.min(Math.min(drag.a, drag.b), width - 160) }}>
          {short(Math.floor(xInv(Math.min(drag.a, drag.b))))} – {short(Math.ceil(xInv(Math.max(drag.a, drag.b))) - 1)}
        </div>)}
      {hi >= 0 && !drag && (
        <div className="absolute top-2 z-10 text-xs rounded-control border border-line bg-surface shadow-md px-3 py-2 pointer-events-none w-60"
          style={x(pts[hi].mid) > width / 2 ? { left: Math.max(x(pts[hi].mid) - 252, 0) } : { left: x(pts[hi].mid) + 12 }}>
          <div className="font-semibold mb-1">{span(pts[hi].p)}</div>
          {tipRows.slice(0, 12).map(({ l, v }) => (
            <div key={l.code} className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: colors.get(l.code) }} />
              <span className="truncate flex-1">{l.name}</span><span className="num">{unit(v)}</span></div>))}
          {tipRows.length > 12 && <div className="text-muted">+{tipRows.length - 12} more</div>}
          <div className="flex justify-between gap-4 mt-1 pt-1 border-t border-line"><span className="text-muted">All items</span>
            <span className="num">{unit(Number(metric === "net" ? pts[hi].p.net : pts[hi].p.qty))}</span></div>
        </div>)}
      {lines.length > 0 && (
        <ul className="mt-3 rounded-control border border-line px-3 py-2 grid gap-x-4 gap-y-1 grid-cols-[repeat(auto-fill,minmax(11rem,1fr))] text-sm">
          {lines.map((l) => {
            const off = hidden.has(l.code);
            return (
              <li key={l.code}>
                <button type="button" aria-pressed={!off} onClick={() => onToggle(l.code)} title={off ? "Show line" : "Hide line"}
                  className={`flex items-center gap-2 w-full text-left hover:text-ink ${off ? "text-faint" : ""}`}>
                  <span className="w-5 h-[3px] rounded shrink-0" style={{ background: off ? "var(--color-line-strong)" : colors.get(l.code) }} />
                  <span className={`truncate ${off ? "line-through" : ""}`}>{l.name}</span>
                </button>
              </li>);
          })}
        </ul>)}
    </div>
  );
}

// ---------- page ----------

export default function ItemSalesReport() {
  const [params, setParams] = useSearchParams();
  // No dates in the URL → last 30 days.
  const qTo = params.get("to") ?? todayIso(), qFrom = params.get("from") ?? iso(dayNum(qTo) - 29);
  const query = new URLSearchParams(Object.entries({ from: qFrom, to: qTo }).filter(([, v]) => v)).toString();
  const r = useAsync(() => api.get<Report>(`/api/reports/item-sales?${query}`), [query]);
  const p = r.data;

  const [metric, setMetric] = useState<Metric>("net");
  const [dept, setDept] = useState("");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "net", desc: true });
  // Items drawn as lines: the top 10 by sales until someone picks their own from the table.
  const [picked, setPicked] = useState<string[] | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  // Zooming pushes a history entry, so the browser's Back button zooms back out.
  const setRange = (from: string, to: string, push = true) => {
    const q = new URLSearchParams(params);
    q.set("from", from); q.set("to", to);
    setParams(q, { replace: !push });
  };
  const preset = (days: number) => { const t = todayIso(); setRange(iso(dayNum(t) - days + 1), t); };
  const zoomOut = () => {
    if (!p) return;
    const a = dayNum(p.from), b = dayNum(p.to), w = b - a + 1;
    let lo = a - Math.ceil(w / 2), hi = b + Math.ceil(w / 2);
    if (p.bounds.from) lo = Math.max(lo, Math.min(dayNum(p.bounds.from), a));
    hi = Math.min(hi, Math.max(dayNum(todayIso()), b));
    setRange(iso(lo), iso(hi));
  };

  const depts = useMemo(() => [...new Set((p?.rows ?? []).map((x) => x.department))].sort(), [p]);
  const rows = useMemo(() => {
    const s = search.trim().toLowerCase();
    const val = (x: Row): string | number => {
      switch (sort.key) {
        case "name": return x.name.toLowerCase();
        case "category": return x.category.toLowerCase();
        case "promo": return Number(x.promo_qty) + Number(x.free_qty);
        case "discount": return Number(x.gross) - Number(x.net);
        default: return Number(x[sort.key] ?? -Infinity);
      }
    };
    return (p?.rows ?? [])
      .filter((x) => (!dept || x.department === dept) && (!s || `${x.name} ${x.code} ${x.category}`.toLowerCase().includes(s)))
      .sort((a, b) => { const va = val(a), vb = val(b); const c = va < vb ? -1 : va > vb ? 1 : 0; return sort.desc ? -c : c; });
  }, [p, dept, search, sort]);
  const totals = useMemo(() => rows.reduce((t, x) => ({
    qty: t.qty + Number(x.qty), promo: t.promo + Number(x.promo_qty) + Number(x.free_qty), gross: t.gross + Number(x.gross), net: t.net + Number(x.net),
  }), { qty: 0, promo: 0, gross: 0, net: 0 }), [rows]);
  const top = useMemo(() => [...rows].sort((a, b) => Number(b.net) - Number(a.net)).slice(0, 10).map((x) => x.code), [rows]);
  const onChart = picked ?? top;
  const byCode = useMemo(() => new Map((p?.rows ?? []).map((x) => [x.code, x])), [p]);
  const lines = onChart.map((c) => byCode.get(c)).filter((x): x is Row => !!x);
  const colors = useMemo(() => new Map((p?.rows ?? []).map((x, i) => [x.code, colorOf(i)])), [p]);
  const togglePick = (code: string) => {
    const cur = picked ?? top;
    setPicked(cur.includes(code) ? cur.filter((c) => c !== code) : [...cur, code]);
    setHidden((h) => { const n2 = new Set(h); n2.delete(code); return n2; });
  };
  const toggleHidden = (code: string) => setHidden((h) => { const n2 = new Set(h); if (n2.has(code)) n2.delete(code); else n2.add(code); return n2; });
  const maxShare = Math.max(...rows.map((x) => Number(x.share_pct ?? 0)), 1);

  const exportCsv = () => {
    if (!p) return;
    const esc = (v: unknown) => { const s = String(v ?? ""); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const lines = [["Swype item #", "Item", "Department", "Category", "Qty", "Promo/free qty", "Gross $", "Net $", "Avg price $", "Share %"],
      ...rows.map((x) => [x.code, x.name, x.department, x.category, x.qty, Number(x.promo_qty) + Number(x.free_qty), x.gross, x.net,
        x.avg_price == null ? "" : Number(x.avg_price).toFixed(2), x.share_pct == null ? "" : Number(x.share_pct).toFixed(2)])];
    const blob = new Blob([lines.map((l) => l.map(esc).join(",")).join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = `item-sales_${p.from}_${p.to}.csv`; a.click();
    URL.revokeObjectURL(a.href);
  };

  const Th = ({ k, children, right }: { k: SortKey; children: React.ReactNode; right?: boolean }) => (
    <th className={right ? "r" : ""} aria-sort={sort.key === k ? (sort.desc ? "descending" : "ascending") : "none"}>
      <button type="button" className="hover:text-ink whitespace-nowrap"
        onClick={() => setSort((s) => ({ key: k, desc: s.key === k ? !s.desc : !(k === "name" || k === "category") }))}>
        {children}{sort.key === k ? (sort.desc ? " ↓" : " ↑") : ""}
      </button>
    </th>);

  const toggle = (on: boolean) => `h-8 px-3 text-sm rounded-control border ${on ? "border-accent bg-amber-soft text-ink font-medium" : "border-line-strong text-muted hover:text-ink"}`;

  return (
    <Page title="Item sales" area="reports"
      sub={p ? <>{span(p)} · {p.days_covered ? `${p.days_covered} day${p.days_covered === 1 ? "" : "s"} of Swype sales on file` : "no Swype sales on file"}</> : "What sold, how many and for how much, between two dates"}
      actions={p && p.rows.length > 0 ? <Button kind="quiet" onClick={exportCsv}>Export CSV</Button> : undefined}>
      <div className="flex flex-wrap gap-x-4 gap-y-3 mb-5 items-end">
        <label className="flex flex-col gap-1 text-sm"><span className="text-muted">From</span>
          <input type="date" className={inputCls} value={p?.from ?? qFrom} max={p?.to}
            onChange={(e) => e.target.value && setRange(e.target.value, p?.to ?? qTo ?? todayIso(), false)} /></label>
        <label className="flex flex-col gap-1 text-sm"><span className="text-muted">To</span>
          <input type="date" className={inputCls} value={p?.to ?? qTo} min={p?.from}
            onChange={(e) => e.target.value && setRange(p?.from ?? qFrom, e.target.value, false)} /></label>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Quick ranges">
          {([[7, "7d"], [30, "30d"], [90, "90d"], [365, "1y"]] as const).map(([k, l]) => {
            const on = qTo === todayIso() && dayNum(qTo) - dayNum(qFrom) + 1 === k;
            return <button key={k} type="button" aria-pressed={on} className={toggle(on)} onClick={() => preset(k)}>{l}</button>;
          })}
          {p?.bounds.from && <button type="button" aria-pressed={qFrom === p.bounds.from && qTo === p.bounds.to}
            className={toggle(qFrom === p.bounds.from && qTo === p.bounds.to)} onClick={() => setRange(p.bounds.from!, p.bounds.to!)}>All</button>}
          <button type="button" className={toggle(false)} onClick={zoomOut} disabled={!p}>Zoom out</button>
        </div>
      </div>

      {r.error && <Notice kind="error">{r.error}</Notice>}
      {r.loading && !p && <Loading />}
      {p && (
        <div className={r.loading ? "opacity-60 transition-opacity" : ""}>
          <div className="grid gap-3 grid-cols-2 lg:grid-cols-4 mb-5">
            <Tile label="Net sales" value={money(p.summary.net)} sub={`${money(p.summary.gross)} before discounts`} />
            <Tile label="Items sold" value={n(p.summary.qty, 0)} sub={`${p.summary.items} different item${p.summary.items === 1 ? "" : "s"}`} />
            <Tile label="Average price" value={money(p.summary.avg_price, 2)} sub="Net sales ÷ items sold" />
            <Tile label="Per day" value={money(p.summary.per_day)} sub="Net sales ÷ days on file" />
          </div>

          <section className="card p-4 mb-5">
            <div className="flex flex-wrap items-center gap-3 mb-2">
              <h3 className="font-semibold flex-1">{metric === "net" ? "Net sales" : "Quantity sold"} per item</h3>
              <span className="text-sm text-muted">{picked ? `${lines.length} picked item${lines.length === 1 ? "" : "s"}` : `Top ${lines.length} by sales`}
                {picked && <button type="button" className="underline ml-2" onClick={() => { setPicked(null); setHidden(new Set()); }}>reset</button>}
                {lines.length > 0 && <button type="button" className="underline ml-2" onClick={() => { setPicked([]); setHidden(new Set()); }}>clear</button>}</span>
              <div role="group" aria-label="Chart metric" className="flex gap-1.5">
                <button type="button" aria-pressed={metric === "net"} className={toggle(metric === "net")} onClick={() => setMetric("net")}>Sales $</button>
                <button type="button" aria-pressed={metric === "qty"} className={toggle(metric === "qty")} onClick={() => setMetric("qty")}>Qty</button>
              </div>
            </div>
            {p.periods.length ? (
              <>
                <SalesChart report={p} metric={metric} lines={lines} colors={colors} hidden={hidden} onToggle={toggleHidden} onZoom={(a, b) => setRange(a, b)} />
                <p className="text-xs text-muted mt-1">Drag across the chart to zoom into those dates. Each point is one imported Swype sales period. Back or “Zoom out” widens the range again. Click a legend entry to hide its line; click a row below to add or remove it.</p>
              </>
            ) : <div className="text-muted py-10 text-center">No Swype sales imported for these dates.</div>}
          </section>

          {p.rows.length === 0 ? <Empty>Nothing sold in this range.</Empty> : (
            <>
              <div className="flex flex-wrap gap-3 mb-3 items-center">
                <input type="search" placeholder="Search items" className={`${inputCls} w-60`} value={search} onChange={(e) => setSearch(e.target.value)} />
                <div role="group" aria-label="Department" className="flex flex-wrap gap-1.5">
                  <button type="button" aria-pressed={!dept} className={toggle(!dept)} onClick={() => setDept("")}>All</button>
                  {depts.map((x) => <button key={x} type="button" aria-pressed={dept === x} className={toggle(dept === x)} onClick={() => setDept(x)}>{x}</button>)}
                </div>
                <span className="text-sm text-muted ml-auto">{rows.length} of {p.rows.length} items</span>
              </div>
              <div className="table-box">
                <table className="grid">
                  <thead>
                    <tr>
                      <Th k="name">Item</Th><Th k="category">Category</Th><Th k="qty" right>Qty</Th><Th k="promo" right>Promo/free</Th>
                      <Th k="discount" right>Discounts</Th><Th k="net" right>Net sales</Th><Th k="avg_price" right>Avg price</Th><Th k="share_pct">Share</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((x) => {
                      const on = onChart.includes(x.code), promo = Number(x.promo_qty) + Number(x.free_qty);
                      return (
                        <tr key={x.code} onClick={() => togglePick(x.code)} aria-selected={on}
                          className="cursor-pointer hover:bg-raised">
                          <td><div className="font-medium flex items-center gap-2"><span className="w-3 h-[3px] rounded shrink-0" style={{ background: on ? colors.get(x.code) : "transparent", outline: on ? undefined : "1px dashed var(--color-line-strong)" }} />{x.name}</div><div className="text-xs text-muted num">{x.code}</div></td>
                          <td><div>{x.category}</div><div className="text-xs text-muted">{x.department}</div></td>
                          <td className="r num">{n(x.qty, 2)}</td>
                          <td className="r num text-muted">{promo ? n(promo, 2) : ""}</td>
                          <td className="r num">{Number(x.gross) - Number(x.net) ? money(Number(x.gross) - Number(x.net)) : "—"}</td>
                          <td className="r num font-medium">{money(x.net, 2)}</td>
                          <td className="r num">{money(x.avg_price, 2)}</td>
                          <td className="min-w-32">
                            <div className="flex items-center gap-2">
                              <span className="h-2 flex-1 rounded bg-line overflow-hidden">
                                <span className="block h-full bg-accent" style={{ width: `${Number(x.share_pct ?? 0) / maxShare * 100}%` }} /></span>
                              <span className="num text-xs w-12 text-right">{x.share_pct == null ? "—" : `${n(x.share_pct, 1)}%`}</span>
                            </div>
                          </td>
                        </tr>);
                    })}
                  </tbody>
                  <tfoot>
                    <tr className="font-semibold">
                      <td colSpan={2}>Total{rows.length !== p.rows.length ? " (shown)" : ""}</td>
                      <td className="r num">{n(totals.qty, 2)}</td><td className="r num text-muted">{totals.promo ? n(totals.promo, 2) : ""}</td>
                      <td className="r num">{totals.gross - totals.net ? money(totals.gross - totals.net) : "—"}</td><td className="r num">{money(totals.net, 2)}</td>
                      <td className="r num">{totals.qty ? money(totals.net / totals.qty, 2) : "—"}</td>
                      <td className="num text-xs">{Number(p.summary.net) ? `${n(totals.net / Number(p.summary.net) * 100, 1)}%` : ""}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            </>
          )}
        </div>
      )}
    </Page>
  );
}
