import { useEffect, useRef, useState } from "react";
import { type VariancePool, type VarianceRow, n } from "../lib/api";

// One dot per stock item: units sold (menu items that use it, from Swype × recipes) across, the difference
// as a % of recipe usage up and down. Same sign as the ledger: above zero = more left than expected (over),
// below = less left (short). The shaded band is the flag threshold from Settings.
// A set of alternatives (items that stand in for each other in recipes) is one diamond, checked as a whole.

type Num = string | number | null;
type Kind = "within" | "over" | "short";
/** One plotted thing: a stock item, or a set of alternatives (`members` set). */
type Point = { key: string; name: string; sub: string | null; members: string[] | null; status: string; pct: Num; cost: Num;
  sold: number; should: string; actual: string; pick: () => void; match: (needle: string) => boolean };
type Dot = { x: Point; sold: number; y: number; kind: Kind; clamped: boolean };

const COLOR: Record<Kind, string> = { within: "var(--color-ok)", over: "var(--color-info)", short: "var(--color-danger)" };
const M = { l: 46, r: 14, t: 14, b: 34 };

function niceStep(span: number, target: number) {
  const raw = span / target, p = 10 ** Math.floor(Math.log10(raw)), f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
}
const signed = (v: number, dp = 0) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${n(Math.abs(v), dp)}%`;
const money = (v: number) => `${v < 0 ? "−" : "+"}$${n(Math.abs(v), 2)}`;

/** The Swype items that pour from a set ("Mystery Wine / Wine Special"): the ones whose recipe offers the choice,
 *  else any that sold from it, else the stock items when nothing sold. */
export function sellingName(p: VariancePool) {
  const pick = (cs: VariancePool["recipes"]) => [...new Set(cs.map((c) => c.name))];
  const names = pick(p.recipes.filter((c) => c.uses.length > 1));
  const all = names.length ? names : pick(p.recipes);
  return all.length ? all.join(" / ") : p.name;
}

export default function VarianceScatter({ rows, pools = [], threshold, needle, q, qUnit, onPick, onPickPool }: {
  rows: VarianceRow[]; pools?: VariancePool[]; threshold: Num; needle: string;
  q: (v: Num | number, x: VarianceRow, signed?: boolean) => string; qUnit: (v: Num | number, unit: string) => string;
  onPick: (x: VarianceRow) => void; onPickPool: (p: VariancePool) => void;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(720);
  const [hover, setHover] = useState<Dot | null>(null);
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, Math.floor(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const t = Number(threshold);
  // plottable: counted at both ends, a recipe uses it, and sales say some was used (else there's no %)
  const has = (f: (string | null)[], nd: string) => f.some((v) => (v ?? "").toLowerCase().includes(nd));
  const points: Point[] = [
    ...rows.map((x) => ({ key: x.item_id, name: x.name, sub: x.category, members: null, status: x.status, pct: x.variance_pct, cost: x.variance_cost,
      sold: x.contributions.reduce((a, c) => a + Number(c.sold), 0), should: q(x.should_use, x), actual: q(x.actual, x),
      pick: () => onPick(x), match: (nd: string) => has([x.name, x.sku, x.category], nd) })),
    ...pools.map((p) => ({ key: `pool-${p.id}`, name: sellingName(p), sub: null, members: p.members.map((m) => m.name), status: p.status,
      pct: p.variance_pct, cost: p.variance_cost, sold: p.recipes.reduce((a, c) => a + Number(c.sold), 0),
      should: qUnit(p.should_use, p.unit), actual: qUnit(p.actual, p.unit), pick: () => onPickPool(p),
      match: (nd: string) => has([p.name, ...p.members.flatMap((m) => [m.name, m.sku, m.category])], nd) })),
  ];
  const usable = points.filter((x) => (x.status === "ok" || x.status === "off") && x.pct != null);
  const raw = usable.map((x) => ({ x, sold: x.sold, y: -Number(x.pct) }));
  const left = points.length - raw.length;
  const maxAbs = Math.max(0, ...raw.map((d) => Math.abs(d.y)));
  const Y = Math.max(t * 2, Math.min(100, Math.ceil(maxAbs / 10) * 10));  // past ±100% a dot sits on the edge
  const dots: Dot[] = raw.map((d) => ({ ...d, kind: Math.abs(d.y) < t ? "within" : d.y > 0 ? "over" : "short", clamped: Math.abs(d.y) > Y }));
  const xStep = niceStep(Math.max(1, ...dots.map((d) => d.sold)), 5);
  const X = Math.max(xStep, Math.ceil(Math.max(1, ...dots.map((d) => d.sold)) * 1.04 / xStep) * xStep);
  const h = w < 520 ? 260 : 320;
  const pw = w - M.l - M.r, ph = h - M.t - M.b;
  const sx = (v: number) => M.l + (v / X) * pw;
  const sy = (v: number) => M.t + ((Y - Math.max(-Y, Math.min(Y, v))) / (2 * Y)) * ph;
  const yStep = niceStep(Y, 3);
  const yTicks: number[] = [];
  for (let v = -Math.floor(Y / yStep) * yStep; v <= Y + 1e-9; v += yStep) yTicks.push(v);
  const xTicks: number[] = [];
  for (let v = 0; v <= X + 1e-9; v += xStep) xTicks.push(v);

  const count = (k: Kind) => dots.filter((d) => d.kind === k).length;
  const match = (x: Point) => !needle || x.match(needle);
  // call out the worst short and the worst over by money where there's a cost, else by %
  const worst = (k: Kind) => dots.filter((d) => d.kind === k)
    .sort((a, b) => Math.abs(Number(b.x.cost ?? 0)) - Math.abs(Number(a.x.cost ?? 0)) || Math.abs(b.y) - Math.abs(a.y))[0];
  const callouts = [worst("short"), worst("over")].filter((d): d is Dot => !!d && d.x.key !== hover?.x.key);
  // draw within first so flagged dots sit on top; the hovered dot last
  const order = [...dots].sort((a, b) => (a.kind === "within" ? 0 : 1) - (b.kind === "within" ? 0 : 1) || a.sold - b.sold);

  if (dots.length === 0) return (
    <section className="panel">
      <h2 className="panel-title">Sales vs stock difference</h2>
      <p className="text-xs text-muted mt-2">No item can be plotted for this period: each needs both counts, a recipe and some sales that use it.</p>
    </section>);

  const tip = hover && (() => {
    const d = hover, x = d.x;
    const cx = sx(d.sold), cy = sy(d.y), right = cx > w * 0.6;
    return (
      <div role="tooltip" className="sc-tip" style={{ left: right ? undefined : cx + 12, right: right ? w - cx + 12 : undefined, top: Math.max(0, cy - 12) }}>
        <div className="font-semibold text-ink">{x.name}</div>
        {x.members ? <ul className="text-ink-2 my-0.5" aria-label="Poured from any one of">{x.members.map((m) => <li key={m}>· {m}</li>)}</ul> : <div className="text-muted">{x.sub}</div>}
        <dl className="sc-dl">
          <dt>Units sold</dt><dd>{n(d.sold, 1)}</dd>
          <dt>Recipe usage</dt><dd>{x.should}</dd>
          <dt>Actually used</dt><dd>{x.actual}</dd>
          <dt>Difference</dt><dd style={{ color: COLOR[d.kind] }}>{signed(d.y, 1)} {d.kind === "within" ? "(within)" : d.kind}</dd>
          {x.cost != null && <><dt>Value</dt><dd>{money(-Number(x.cost))}</dd></>}
        </dl>
        {x.members && <div className="text-faint mt-1">Checked together: Swype doesn’t say which one was poured.</div>}
        <div className="text-faint mt-1">Click to open in the list</div>
      </div>);
  })();

  return (
    <section className="panel" aria-labelledby="sc-title">
      <div className="flex items-baseline justify-between gap-3 flex-wrap mb-2">
        <div>
          <h2 id="sc-title" className="panel-title">Sales vs stock difference</h2>
          <p className="text-xs text-muted mt-0.5">Each dot is one stock item in this count period{pools.length ? ", each diamond a Swype item poured from alternative stock items, checked together" : ""}. Hover for details, click to open it below.</p>
        </div>
        <div className="flex items-center flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2">
          <span className="inline-flex items-center gap-1.5"><i className="sc-key" style={{ background: COLOR.within }} />Within ±{n(t, 0)}% <span className="num text-faint">{count("within")}</span></span>
          <span className="inline-flex items-center gap-1.5"><i className="sc-key" style={{ background: COLOR.over }} />More left than expected <span className="num text-faint">{count("over")}</span></span>
          <span className="inline-flex items-center gap-1.5"><i className="sc-key" style={{ background: COLOR.short }} />Less left than expected <span className="num text-faint">{count("short")}</span></span>
          {pools.length > 0 && <span className="inline-flex items-center gap-1.5"><svg width="10" height="10" aria-hidden><path d="M5,0 L10,5 L5,10 L0,5 Z" fill="currentColor" className="text-muted" /></svg>Swype item poured from alternatives</span>}
        </div>
      </div>

      <div ref={wrap} className="relative" onMouseLeave={() => setHover(null)}>
        <svg width={w} height={h} className="block overflow-visible" role="img"
          aria-label={`Scatter of ${dots.length} stock items and sets: ${count("within")} within ±${n(t, 0)}%, ${count("over")} with more left than expected, ${count("short")} with less left than expected.`}>
          {/* tolerance band and grid */}
          <rect x={M.l} width={pw} y={sy(t)} height={sy(-t) - sy(t)} className="sc-band" />
          {yTicks.map((v) => <line key={v} x1={M.l} x2={M.l + pw} y1={sy(v)} y2={sy(v)} className={v === 0 ? "sc-zero" : "sc-grid"} />)}
          <line x1={M.l} x2={M.l} y1={M.t} y2={M.t + ph} className="sc-axis" />
          <line x1={M.l} x2={M.l + pw} y1={M.t + ph} y2={M.t + ph} className="sc-axis" />
          {yTicks.map((v) => <text key={v} x={M.l - 6} y={sy(v)} dy="0.32em" textAnchor="end" className="sc-tick">{signed(v)}</text>)}
          {xTicks.map((v) => <text key={v} x={sx(v)} y={M.t + ph + 16} textAnchor="middle" className="sc-tick">{n(v, 0)}</text>)}
          <text x={M.l + pw / 2} y={h - 2} textAnchor="middle" className="sc-label">Units sold this period</text>
          <text transform={`translate(11 ${M.t + ph / 2}) rotate(-90)`} textAnchor="middle" className="sc-label">Difference</text>
          <text x={M.l + pw - 4} y={sy(t) + 12} textAnchor="end" className="sc-band-label">Tolerance ±{n(t, 0)}%</text>
          {w >= 520 && <text x={M.l + pw - 4} y={sy(0) - 4} textAnchor="end" className="sc-halo sc-tick">Count matches expected</text>}

          {order.map((d) => {
            const cx = sx(d.sold), cy = sy(d.y), dim = !match(d.x), on = hover?.x.key === d.x.key, r = on ? 6.5 : 4.5, dr = r * 1.45;
            return (
              <g key={d.x.key} className="cursor-pointer" opacity={dim ? 0.18 : 1}
                onMouseEnter={() => setHover(d)} onFocus={() => setHover(d)} onBlur={() => setHover(null)}
                onClick={d.x.pick} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); d.x.pick(); } }}
                tabIndex={dim ? -1 : 0} role="button"
                aria-label={`${d.x.name}${d.x.members ? ` (poured from ${d.x.members.join(" or ")})` : ""}: ${n(d.sold, 1)} sold, ${signed(d.y, 1)} ${d.kind === "within" ? "within tolerance" : d.kind === "over" ? "more left than expected" : "less left than expected"}`}>
                <circle cx={cx} cy={cy} r={12} fill="transparent" />
                {d.clamped
                  ? <path d={d.y > 0 ? `M${cx - 5},${cy + 4} L${cx + 5},${cy + 4} L${cx},${cy - 5} Z` : `M${cx - 5},${cy - 4} L${cx + 5},${cy - 4} L${cx},${cy + 5} Z`} fill={COLOR[d.kind]} className="sc-dot" />
                  : d.x.members ? <path d={`M${cx},${cy - dr} L${cx + dr},${cy} L${cx},${cy + dr} L${cx - dr},${cy} Z`} fill={COLOR[d.kind]} className="sc-dot" />
                  : <circle cx={cx} cy={cy} r={r} fill={COLOR[d.kind]} className="sc-dot" />}
              </g>);
          })}

          {callouts.map((d) => {
            const cx = sx(d.sold), cy = sy(d.y), right = cx > M.l + pw * 0.7;
            const text = `${d.x.name} ${signed(d.y, 0)}`, tw = text.length * 6.6 + 14;
            const bx = right ? cx - 14 - tw : cx + 14, by = Math.min(M.t + ph - 22, Math.max(M.t, cy + (d.y > 0 ? -30 : 8)));
            return (
              <g key={`c${d.x.key}`} pointerEvents="none">
                <line x1={cx} y1={cy} x2={right ? bx + tw : bx} y2={by + 10} className="sc-leader" />
                <rect x={bx} y={by} width={tw} height={20} rx={4} className="sc-callout" stroke={COLOR[d.kind]} />
                <text x={bx + 7} y={by + 14} className="sc-callout-text">{text}</text>
              </g>);
          })}
        </svg>
        {tip}
      </div>

      <p className="text-xs text-muted mt-2 max-w-[80ch]">
        Difference is actually left − should be left, as a % of recipe usage (Swype sales × recipes). Above the line, more is left than sales explain; below, less.
        The band is the ±{n(t, 0)}% flag threshold in Settings.{dots.some((d) => d.clamped) ? ` Triangles are past ±${n(Y, 0)}% and sit on the edge.` : ""}
        {left > 0 && ` ${left} item${left === 1 ? " isn’t" : "s aren’t"} plotted: no recipe uses ${left === 1 ? "it" : "them"}, nothing sold used ${left === 1 ? "it" : "them"}, or a count is missing.`}
      </p>
    </section>
  );
}
