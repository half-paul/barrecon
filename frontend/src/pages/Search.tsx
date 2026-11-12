import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, groupBy } from "../lib/api";
import { type SearchResult, searchHits } from "../lib/palette";
import { Empty, Loading, Notice, Page, inputCls } from "../lib/ui";

const LIMIT = 500; // per section; the API’s cap

/** Everything the search bar found, not just its first few per section. */
export default function Search() {
  const [params, setParams] = useSearchParams();
  const setParam = (k: string, v: string) => { const p = new URLSearchParams(params); if (v) p.set(k, v); else p.delete(k); setParams(p, { replace: true }); };
  const q = params.get("q") ?? "";
  const s = q.trim();
  const [found, setFound] = useState<{ q: string; r: SearchResult } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    if (!s) return;
    let live = true;
    const t = setTimeout(() => {
      api.get<SearchResult>(`/api/search?q=${encodeURIComponent(s)}&limit=${LIMIT}`)
        .then((r) => { if (live) setFound({ q: s, r }); })
        .catch((e) => { if (live) setError((e as Error).message); });
    }, 200);
    return () => { live = false; clearTimeout(t); };
  }, [s]);

  const current = found?.q === s ? found : null;
  const sections = current ? groupBy(searchHits(current.r), (h) => h.group) : [];
  const total = sections.reduce((n, [, rows]) => n + rows.length, 0);
  const only = params.get("in");
  const shown = sections.filter(([name]) => !only || name === only || !sections.some(([x]) => x === only));
  const chip = (name: string, label: string, on: boolean) => (
    <button key={name} aria-pressed={on} onClick={() => setParam("in", name)}
      className={`px-3 py-1.5 ${on ? "bg-raised text-ink font-medium" : "bg-surface text-muted hover:text-ink"}`}>{label}</button>);

  return (
    <Page title="Search" sub={current && s ? `${total} result${total === 1 ? "" : "s"}` : undefined}>
      <input className={`${inputCls} w-full mb-5`} placeholder="Search items, Swype, recipes, notes, counts, deliveries…" aria-label="Search the portal"
        autoFocus value={q} onChange={(e) => setParam("q", e.target.value)} />
      {error && <Notice kind="error">{error}</Notice>}
      {!s && <Empty>Type something to search the whole portal.</Empty>}
      {s && !current && !error && <Loading />}
      {current && total === 0 && <Empty>Nothing matches.</Empty>}
      {sections.length > 1 && (
        <div className="inline-flex flex-wrap rounded-control border border-line overflow-hidden text-sm mb-5" role="group" aria-label="Show one section">
          {chip("", `All (${total})`, shown.length === sections.length)}
          {sections.map(([name, rows]) => chip(name, `${name} (${rows.length})`, shown.length === 1 && shown[0][0] === name))}
        </div>
      )}
      {shown.map(([name, rows]) => (
        <section key={name} className="mb-6">
          <h2 className="label pb-1 border-b border-line-strong">{name} ({rows.length})</h2>
          <ul>
            {rows.map((h) => (
              <li key={h.id} className="border-t border-line">
                <Link to={h.to} className="px-4 py-2 flex flex-wrap items-baseline justify-between gap-x-4 hover:bg-raised">
                  <span>{h.label}</span>
                  {h.hint && <span className="text-xs text-faint">{h.hint}</span>}
                </Link>
              </li>
            ))}
          </ul>
          {rows.length >= LIMIT && <p className="px-4 py-2 border-t border-line text-sm text-muted">Showing the first {LIMIT}. Add another word to narrow it down.</p>}
        </section>
      ))}
    </Page>
  );
}
