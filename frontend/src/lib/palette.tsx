import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, d } from "./api";

export type Command = { id: string; label: string; hint?: string; group: string; run: () => void };

export type Hit = { id: string; group: string; label: string; hint?: string; to: string };

export type SearchResult = {
  items: { id: string; name: string; sku: string; category: string | null; is_active: boolean; swype_codes: string[]; sold_last_week: number | null }[];
  swype_items: { code: string; name: string; category: string | null; is_active: boolean; has_recipe: boolean; sold_last_week: number | null }[];
  recipes: { code: string; name: string; category: string | null; ignored: boolean; ingredients: string[]; snippet: string | null }[];
  groups: { name: string; items: number; active: number }[];
  counts: { id: string; counted_at: string; status: string; source: string; note: string | null }[];
  movements: { id: string; occurred_at: string; kind: string; external_ref: string; item: string; status: string; note: string | null }[];
  deliveries: { id: string; store: string | null; receipt_no: string | null; status: string; note: string | null; occurred_at: string | null }[];
  imports: { id: string; file_name: string | null; dataset: string; status: string; imported_at: string }[];
  issues: { id: string; code: string; detail: string | null }[];
};

export const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
export const shortcutLabel = isMac ? "⌘K" : "Ctrl K";

/** One row per thing found, section by section. The search bar and the Search page both show these. */
export function searchHits(r: SearchResult): Hit[] {
  return [
    ...r.items.map((i) => ({
      id: i.id, group: "Stock items", label: i.name,
      hint: [i.category, i.sku, i.swype_codes.length ? `Swype ${i.swype_codes[0]}${i.swype_codes.length > 1 ? ` +${i.swype_codes.length - 1}` : ""}` : null,
             i.sold_last_week != null ? `${i.sold_last_week} sold last wk` : null, i.is_active ? null : "off"].filter(Boolean).join(" · "),
      to: `/items?q=${encodeURIComponent(i.sku)}&status=all`,
    })),
    ...r.swype_items.map((i) => ({
      id: i.code, group: "Swype items", label: i.name,
      hint: [i.category, i.code, i.sold_last_week != null ? `${i.sold_last_week} sold last wk` : null, i.has_recipe ? null : "no recipe", i.is_active ? null : "off"].filter(Boolean).join(" · "),
      to: `/swype-items?q=${encodeURIComponent(i.code)}&status=all`,
    })),
    ...r.recipes.map((c) => ({
      id: c.code, group: "Recipes", label: c.name,
      hint: [c.snippet ?? (c.ingredients.join(", ") || c.category), c.ignored ? "no stock" : null].filter(Boolean).join(" · "),
      to: `/recipes?q=${encodeURIComponent(c.code)}`,
    })),
    ...r.groups.map((g) => ({ id: g.name, group: "Item groups", label: g.name, hint: `${g.items} items · ${g.active} active`, to: `/items?cat=${encodeURIComponent(g.name)}&status=all` })),
    ...r.counts.map((c) => ({ id: c.id, group: "Counts", label: `Count ${d(c.counted_at)}`, hint: [c.status, c.note].filter(Boolean).join(" · "), to: `/counts/${c.id}` })),
    ...r.movements.map((m) => ({ id: m.id, group: "Deliveries", label: `${m.item}: ${m.external_ref}`, hint: [m.kind.replace(/_/g, " "), d(m.occurred_at), m.status !== "active" ? m.status : null, m.note].filter(Boolean).join(" · "), to: `/movements?q=${encodeURIComponent(m.external_ref)}` })),
    ...r.deliveries.map((x) => ({ id: x.id, group: "Receipts", label: [x.store ?? "Receipt", x.receipt_no].filter(Boolean).join(" "), hint: [x.occurred_at ? d(x.occurred_at) : null, x.status, x.note].filter(Boolean).join(" · "), to: `/deliveries/${x.id}` })),
    ...r.imports.map((b) => ({ id: b.id, group: "Imports", label: b.file_name ?? b.dataset, hint: [b.dataset.replace(/_/g, " "), d(b.imported_at), b.status === "ok" ? null : b.status].filter(Boolean).join(" · "), to: "/imports" })),
    ...r.issues.map((i) => ({ id: i.id, group: "To review", label: i.detail ?? i.code.replace(/_/g, " "), hint: i.code.replace(/_/g, " "), to: "/imports" })),
  ];
}

/** Opens on Cmd/Ctrl+K. Searches the whole portal (stock items, Swype items, recipes, groups, counts, deliveries, receipts, imports, review items, and their notes),
 *  then offers the pages and actions whose names match. */
export function CommandPalette({ open, onClose, commands }: { open: boolean; onClose: () => void; commands: Command[] }) {
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const [found, setFound] = useState<{ q: string; r: SearchResult } | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setQ(""); setSel(0); setFound(null);
    input.current?.focus();
  }, [open]);

  useEffect(() => {
    const s = q.trim();
    if (!open || !s) return;
    let live = true;
    const t = setTimeout(() => {
      api.get<SearchResult>(`/api/search?q=${encodeURIComponent(s)}`).then((r) => { if (live) setFound({ q: s, r }); }).catch(() => {});
    }, 150);
    return () => { live = false; clearTimeout(t); };
  }, [q, open]);

  const results = useMemo(() => {
    const s = q.trim().toLowerCase();
    const words = s.split(/\s+/).filter(Boolean);
    const cmds = commands.filter((c) => words.every((w) => c.label.toLowerCase().includes(w)));
    if (!s) return cmds;
    const r = found?.r;
    if (!r) return cmds;
    const hits: Command[] = searchHits(r).map((h) => ({ ...h, run: () => nav(h.to) }));
    return [...hits, ...cmds];
  }, [q, commands, found, nav]);
  const searching = !!q.trim() && found?.q !== q.trim();
  const anyHits = !searching && !!found && Object.values(found.r).some((rows) => rows.length > 0);

  useEffect(() => { setSel(0); }, [q]);
  if (!open) return null;

  const run = (c: Command | undefined) => { if (!c) return; onClose(); c.run(); };
  const seeAll = () => { onClose(); nav(`/search?q=${encodeURIComponent(q.trim())}`); };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSel((x) => Math.min(results.length - 1, x + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel((x) => Math.max(0, x - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); if (e.shiftKey && anyHits) seeAll(); else run(results[sel]); }
    else if (e.key === "Escape") { e.preventDefault(); onClose(); }
  };

  let lastGroup = "";
  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-start justify-center px-4 pt-[12vh]" onMouseDown={onClose}>
      <div role="dialog" aria-modal="true" aria-label="Search and jump" className="card w-full max-w-xl overflow-hidden shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 px-4 border-b border-line">
          <SearchIcon />
          <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} placeholder="Search items, Swype, recipes, notes, counts, deliveries…"
            aria-label="Search" className="flex-1 bg-transparent py-3.5 outline-none focus-visible:outline-none text-[15px]" />
          <kbd className="mono text-[11px] text-muted border border-line rounded px-1.5">Esc</kbd>
        </div>
        <ul className="max-h-[50vh] overflow-y-auto py-2" role="listbox">
          {results.length === 0 && <li className="px-4 py-6 text-center text-muted">{searching ? "Searching…" : "Nothing matches."}</li>}
          {results.map((c, i) => {
            const head = c.group !== lastGroup ? (lastGroup = c.group) : null;
            return (
              <li key={`${c.group}-${c.id}`}>
                {head && <div className="label-caps px-4 pt-2 pb-1 text-[11px]">{head}</div>}
                <button role="option" aria-selected={i === sel} onMouseEnter={() => setSel(i)} onClick={() => run(c)}
                  className={`w-full text-left px-4 py-2 flex items-center justify-between gap-3 ${i === sel ? "bg-raised" : ""}`}>
                  <span>{c.label}</span>
                  {c.hint && <span className="text-xs text-faint truncate">{c.hint}</span>}
                </button>
              </li>
            );
          })}
        </ul>
        {anyHits && (
          <button onClick={seeAll} className="w-full flex items-center justify-between gap-3 px-4 py-2.5 border-t border-line text-sm hover:bg-raised">
            <span className="underline">See all results</span>
            <kbd className="mono text-[11px] text-muted border border-line rounded px-1.5 hidden sm:inline">⇧ Enter</kbd>
          </button>
        )}
      </div>
    </div>
  );
}

export function SearchIcon() {
  return <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden className="text-muted shrink-0"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>;
}
