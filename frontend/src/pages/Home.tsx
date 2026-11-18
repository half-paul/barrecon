import { Link } from "react-router-dom";
import { api, type CurrentPeriod, type VarianceReport, type Batch, type CountSession, type Issue, type Item, type Movement, type Settings, d, datasetName, dayRanges, n } from "../lib/api";
import { Empty, Page, useAsync } from "../lib/ui";
import BarPerformance from "./BarPerformance";

const DAY = 86400000;

export default function Home() {
  const items = useAsync(() => api.get<Item[]>("/api/items"));
  const counts = useAsync(() => api.get<CountSession[]>("/api/counts"));
  const issues = useAsync(() => api.get<Issue[]>("/api/imports/issues"));
  const moves = useAsync(() => api.get<Movement[]>("/api/movements?limit=200"));
  const batches = useAsync(() => api.get<Batch[]>("/api/imports"));
  const settings = useAsync(() => api.get<Settings>("/api/settings"));

  // a count dated after now (e.g. a legacy "Week 5" put on a future count day) is not the last count
  const posted = counts.data?.filter((c) => c.status === "posted") ?? [];
  const future = posted.filter((c) => new Date(c.counted_at).getTime() > Date.now());
  const last = posted.find((c) => new Date(c.counted_at).getTime() <= Date.now());
  const draft = counts.data?.find((c) => c.status === "draft");
  const daysSince = last ? Math.floor((Date.now() - new Date(last.counted_at).getTime()) / DAY) : null;
  const overdue = daysSince != null && daysSince > 7 && settings.data?.count_reminder_enabled !== false;

  const withPar = (items.data ?? []).filter((i) => i.par_level && Number(i.par_level) > 0 && i.last_counted_packs != null)
    .map((i) => ({ ...i, on: Number(i.last_counted_packs), par: Number(i.par_level) }))
    .sort((a, b) => a.on / a.par - b.on / b.par);
  const below = withPar.filter((i) => i.on < i.par);

  const weekAgo = Date.now() - 7 * DAY;
  const recentDeliveries = (moves.data ?? []).filter((m) => m.kind === "delivery" && new Date(m.occurred_at).getTime() >= weekAgo);
  const costed = recentDeliveries.filter((m) => m.packs && m.pack_cost);
  const spend = costed.reduce((s, m) => s + Number(m.packs) * Number(m.pack_cost), 0);
  const invoices = new Set(recentDeliveries.map((m) => m.external_ref)).size;

  return (
    <Page title="Overview" sub={last ? `Since the last count on ${d(last.counted_at)}` : undefined}>
      <ThisWeek />
      <BarPerformance />
      <div className="stats mb-8">
        <Stat label="Last count" value={daysSince == null ? "None yet" : daysSince === 0 ? "Today" : `${daysSince} day${daysSince === 1 ? "" : "s"} ago`}
          sub={draft ? <Link className="underline" to={`/counts/${draft.id}`}>A draft count is open: resume</Link>
            : future.length ? <Link className="underline" to="/counts">{future.length} count{future.length === 1 ? " is" : "s are"} dated in the future: check</Link>
            : overdue ? "Weekly count overdue" : daysSince == null ? "Start one from the top bar" : d(last!.counted_at)}
          tone={overdue || draft || future.length ? "warn" : undefined} />
        <Stat label="Below par" value={items.data ? <>{below.length} <span className="text-base text-muted font-normal">of {items.data.length}</span></> : "…"}
          sub={below.length ? summarise(below.map((i) => i.category ?? "Other")) : withPar.length ? "Everything at or above par" : "Set par levels under Stock items"}
          tone={below.length ? "warn" : undefined} />
        <Stat label="Received this week" value={moves.data ? (costed.length ? `$${n(spend, 0)}` : String(recentDeliveries.length)) : "…"}
          sub={recentDeliveries.length ? `${recentDeliveries.length} deliveries, ${invoices} reference${invoices === 1 ? "" : "s"}${costed.length && costed.length < recentDeliveries.length ? `, ${recentDeliveries.length - costed.length} without cost` : ""}` : "No deliveries in the last 7 days"} />
        <Stat label="Needs review" value={issues.data ? String(issues.data.length) : "…"}
          sub={issues.data?.length ? <Link className="underline" to="/imports">From imports: review</Link> : "Nothing waiting"} tone={issues.data?.length ? "warn" : undefined} />
      </div>

      <div className="grid gap-x-10 gap-y-8 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <section className="min-w-0">
          <div className="flex items-baseline justify-between mb-2 gap-3">
            <h2 className="font-semibold">Stock vs par</h2>
            <span className="text-xs text-muted">packs at last count · lowest first</span>
          </div>
          {items.data && withPar.length === 0 && <Empty>No items with a par level and a count yet.</Empty>}
          {withPar.length > 0 && (
            <div className="table-box">
              <table className="grid">
                <thead><tr><th>Item</th><th className="w-[42%]">Level</th><th className="r">On hand</th><th className="r">Par</th></tr></thead>
                <tbody>{withPar.slice(0, 10).map((i) => {
                  const pct = Math.min(100, Math.round((i.on / i.par) * 100));
                  return (
                    <tr key={i.id}>
                      <td>{i.name} <span className="text-xs text-faint">{i.category}</span></td>
                      <td>
                        <div className="h-1.5 bg-line rounded-chip overflow-hidden" role="img" aria-label={`${pct}% of par`}>
                          <div className={`h-full ${i.on < i.par ? "bg-accent" : "bg-ok"}`} style={{ width: `${pct}%` }} />
                        </div>
                      </td>
                      <td className="r mono">{i.on.toFixed(1)}</td>
                      <td className="r mono text-muted">{n(i.par, 1)}</td>
                    </tr>
                  );
                })}</tbody>
              </table>
              {withPar.length > 10 && <Link className="underline text-sm text-muted mt-3 inline-block" to="/items">All {withPar.length} items with a par</Link>}
            </div>
          )}
        </section>

        <aside className="min-w-0 flex flex-col gap-8">
          <VarianceCard />
          <section>
            <h2 className="font-semibold mb-2">Activity</h2>
            {/* a feed this login may not read counts as empty */}
            <Activity counts={counts.error ? [] : counts.data} moves={moves.error ? [] : moves.data} batches={batches.error ? [] : batches.data} />
          </section>
        </aside>
      </div>
    </Page>
  );
}

function summarise(cats: string[]) {
  const m = new Map<string, number>();
  for (const c of cats) m.set(c, (m.get(c) ?? 0) + 1);
  return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3).map(([c, k]) => `${k} ${c.toLowerCase()}`).join(", ");
}

type Event = { at: number; title: string; sub: string; to: string };

function Activity({ counts, moves, batches }: { counts?: CountSession[]; moves?: Movement[]; batches?: Batch[] }) {
  if (!counts || !moves || !batches) return <div className="text-muted">…</div>;
  const ev: Event[] = [];
  for (const c of counts.filter((c) => c.status === "posted").slice(0, 5))
    ev.push({ at: new Date(c.counted_at).getTime(), title: "Count posted", sub: `${d(c.counted_at)} · ${c.line_count} items`, to: `/counts/${c.id}` });
  // movements arrive one per item line; show one event per day and kind
  const byDay = new Map<string, Movement[]>();
  for (const m of moves) { const k = `${m.occurred_at.slice(0, 10)}|${m.kind}`; if (!byDay.has(k)) byDay.set(k, []); byDay.get(k)!.push(m); }
  for (const [k, ms] of [...byDay.entries()].slice(0, 5)) {
    const kind = k.split("|")[1].replace("_", " ");
    ev.push({ at: new Date(ms[0].occurred_at).getTime(), title: kind.charAt(0).toUpperCase() + kind.slice(1), sub: `${d(ms[0].occurred_at)} · ${ms.length} line${ms.length === 1 ? "" : "s"}`, to: ms[0].delivery_id ? `/deliveries/${ms[0].delivery_id}` : "/deliveries" });
  }
  for (const b of batches.filter((b) => b.status === "ok").slice(0, 3))
    ev.push({ at: new Date(b.imported_at).getTime(), title: `Imported ${datasetName(b.dataset)}`, sub: `${d(b.imported_at)} · ${b.rows_inserted} new rows`, to: b.dataset === "item_sales" ? "/sales" : "/imports" });
  ev.sort((a, b) => b.at - a.at);
  if (!ev.length) return <div className="text-muted">Nothing yet.</div>;
  return (
    <ul className="border-t border-line-strong">
      {ev.slice(0, 6).map((e, i) => (
        <li key={i} className="border-b border-line">
          <Link to={e.to} className="block py-2 group">
            <span className="group-hover:text-accent">{e.title}</span><span className="block text-xs text-muted">{e.sub}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function Stat({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub: React.ReactNode; tone?: "warn" }) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className="text-3xl font-semibold num mt-1">{value}</div>
      <div className={`text-sm mt-0.5 ${tone === "warn" ? "text-accent" : "text-muted"}`}>{sub}</div>
    </div>
  );
}

function VarianceCard() {
  const r = useAsync(() => api.get<VarianceReport>("/api/variance"));
  // items and sets of alternatives ("Box A, Box B and Box C") alike
  const off = [...(r.data?.rows ?? []).map((x) => ({ ...x, key: x.item_id })), ...(r.data?.pools ?? []).map((x) => ({ ...x, key: x.id }))]
    .filter((x) => x.status === "off").sort((a, b) => Math.abs(Number(b.variance_pct ?? 0)) - Math.abs(Number(a.variance_pct ?? 0)));
  return (
    <section className="card p-4">
      <div className="flex items-baseline justify-between mb-1"><h2 className="font-semibold">Stock vs sales</h2><Link className="text-sm underline" to="/variance">Open</Link></div>
      {r.error && <p className="text-muted">Needs two posted counts, that week’s Swype sales and recipes.</p>}
      {r.data && (<>
        <p className="text-muted text-sm mb-2">{d(r.data.opening.counted_at)} to {d(r.data.closing.counted_at)}: {off.length} off by {n(r.data.threshold_pct, 0)}% or more</p>
        <ul className="text-sm space-y-0.5">{off.slice(0, 5).map((x) => (
          <li key={x.key} className="flex justify-between gap-2"><span>{x.name}</span><span className="num text-danger">{x.variance_pct == null ? "no sales" : `${Number(x.variance_pct) > 0 ? "+" : ""}${n(x.variance_pct, 1)}%`}</span></li>))}</ul>
      </>)}
    </section>
  );
}

type Step = { state: "done" | "todo" | "wait"; title: string; sub?: string; to?: string; action?: string };

/** The weekly loop in order: what the next variance report still needs. */
function ThisWeek() {
  const r = useAsync(() => api.get<CurrentPeriod>("/api/variance/current"));
  const c = r.data;
  if (!c) return null;
  const miss = c.missing_sales_days;
  const steps: Step[] = [
    c.opening
      ? { state: "done", title: `Opening count ${d(c.opening.counted_at)}`, to: `/counts/${c.opening.id}` }
      : { state: "todo", title: "No posted count yet", sub: "Every period starts with a count.", to: "/counts", action: "Count" },
    { state: c.deliveries ? "done" : "wait", title: c.deliveries ? `${c.deliveries} deliver${c.deliveries === 1 ? "y" : "ies"} recorded` : "No deliveries recorded",
      sub: c.deliveries ? undefined : "If stock arrived, enter it or items will look over.", to: "/deliveries", action: "Record" },
    !c.opening || c.days === 0
      ? { state: "wait", title: "Swype sales", sub: "Nothing to import yet." }
      : miss.length
        ? { state: "todo", title: `Sales missing for ${miss.length} of ${c.days} days`, sub: dayRanges(miss), to: "/sales", action: "Import" }
        : { state: "done", title: `Sales imported for all ${c.days} days` },
    c.unmapped
      ? { state: "todo", title: `${c.unmapped} sold item${c.unmapped === 1 ? " has" : "s have"} no recipe`, sub: "Their pours don’t count toward usage.", to: "/recipes?tab=todo", action: "Add" }
      : { state: c.opening ? "done" : "wait", title: "Every sold item has a recipe" },
    c.draft
      ? { state: "todo", title: "Closing count in progress", sub: `Draft for ${d(c.draft.counted_at)}`, to: `/counts/${c.draft.id}`, action: "Resume" }
      : { state: "wait", title: "Closing count", sub: "Count again at the end of the week (Start count, top right)." },
  ];
  const mark = { done: "✓", todo: "!", wait: "○" };
  const tone = { done: "text-ok", todo: "text-accent", wait: "text-faint" };
  return (
    <section className="mb-8" aria-labelledby="this-week">
      <div className="flex flex-wrap items-baseline justify-between gap-2 mb-3">
        <h2 id="this-week" className="font-semibold">This week</h2>
        {c.last_variance
          ? <Link className="text-sm underline" to="/variance">Last stock vs sales: {d(c.last_variance.opening_at)} to {d(c.last_variance.closing_at)}</Link>
          : <span className="text-sm text-muted">Stock vs sales appears after two posted counts.</span>}
      </div>
      <ol className="loop">
        {steps.map((s) => (
          <li key={s.title} data-state={s.state} className="text-sm">
            <div className={`flex gap-2 font-medium ${s.state === "wait" ? "text-muted" : ""}`}>
              <span aria-hidden className={`shrink-0 w-4 font-semibold ${tone[s.state]}`}>{mark[s.state]}</span>
              <span className="min-w-0">{s.title}</span>
            </div>
            <div className="pl-6">
              {s.sub && <div className="text-xs text-muted mt-0.5 break-words">{s.sub}</div>}
              {s.to && s.action && <Link className="underline text-accent mt-1 inline-block" to={s.to}>{s.action}</Link>}
              {s.to && !s.action && <Link className="underline text-muted mt-1 inline-block" to={s.to}>Open</Link>}
            </div>
          </li>))}
      </ol>
    </section>
  );
}
