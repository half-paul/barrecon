import { useCallback, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type ImportSummary, type Item, type MatchQueue, type MatchRow, type MenuRow, type Recipe, type RecipeLine, day, groupBy, n } from "../lib/api";
import { useReadOnly } from "../lib/auth";
import { Button, Combobox, Empty, Field, FileButton, Flash, Lightbox, Loading, Notice, Page, inputCls, useAsync } from "../lib/ui";

// Every Swype item sits on one tab: not linked yet, a real recipe, a straight 1-to-1 pour, or taking no stock. A recipe row is the link either way.
const TABS = ["todo", "recipes", "direct", "nostock", "stock"] as const;
type Tab = (typeof TABS)[number];
const UNITS = ["oz", "ml", "dash", "each", "g", "l", "cl"];
const pack = (l: Pick<RecipeLine, "pack_name">) => l.pack_name || "pack";

/** "75 ml · 0.10 bottle" for one portion in the item's own unit. */
function portionText(l: RecipeLine) {
  if (l.stock_qty == null) return null;
  const same = l.unit === l.stock_unit;
  const packs = l.packs != null && Number(l.pack_size) > 1 ? `${n(l.packs, 3)} ${pack(l)}` : null;
  return [same ? null : `${n(l.stock_qty, 1)} ${l.stock_unit}`, packs].filter(Boolean).join(" · ");
}

/** Made rather than poured: two or more stock items, or preparation steps around a single one. */
const isMade = (r: Recipe) => r.lines.length > 1 || !!r.steps?.trim();

const photoUrl = (r: Recipe) => `/api/recipes/${encodeURIComponent(r.swype_code)}/photo?v=${encodeURIComponent(r.photo_at ?? "")}`;

/** Phone photos run to several MB; shrink to 1200px JPEG before upload. GIFs go as they are. */
async function shrink(f: File): Promise<Blob> {
  if (f.type === "image/gif") return f;
  const img = await createImageBitmap(f).catch(() => null);
  if (!img) return f;
  const scale = Math.min(1, 1200 / Math.max(img.width, img.height));
  const c = document.createElement("canvas");
  c.width = Math.round(img.width * scale); c.height = Math.round(img.height * scale);
  c.getContext("2d")!.drawImage(img, 0, 0, c.width, c.height);
  return new Promise((ok) => c.toBlob((b) => ok(b ?? f), "image/jpeg", 0.85));
}

export default function Recipes() {
  const [params, setParams] = useSearchParams();
  const setParam = (k: string, v: string) => { const p = new URLSearchParams(params); if (v) p.set(k, v); else p.delete(k); setParams(p, { replace: true }); };
  const q = params.get("q") ?? "";
  const menu = useAsync(() => api.get<MenuRow[]>("/api/recipes/menu"));
  const queue = useAsync(() => api.get<MatchQueue>("/api/recipes/match"));
  const items = useAsync(() => api.get<Item[]>("/api/items?include_inactive=true"));
  const [editing, setEditing] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [viewing, setViewing] = useState<MenuRow | null>(null);
  const closePhoto = useCallback(() => setViewing(null), []);
  const [busy, setBusy] = useState(false);

  const all = menu.data ?? [];
  const todo = queue.data?.rows ?? [];
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const hit = (fields: (string | null | undefined)[]) => words.every((w) => fields.some((f) => f?.toLowerCase().includes(w)));
  const found = all.filter((r) => r.recipe && hit([r.name, r.code, r.category, r.recipe.steps, r.recipe.notes, ...r.recipe.lines.map((l) => l.item_name)]));
  const linked = found.filter((r) => !r.recipe!.ignored);
  const made = linked.filter((r) => isMade(r.recipe!));
  const direct = linked.filter((r) => !isMade(r.recipe!));
  const noStock = found.filter((r) => r.recipe!.ignored);
  const todoShown = todo.filter((r) => hit([r.name, r.code, r.category, ...r.lines.map((l) => l.name)]));
  const withStock = all.filter((r) => r.recipe && !r.recipe.ignored);
  const counts = { made: withStock.filter((r) => isMade(r.recipe!)).length, direct: withStock.filter((r) => !isMade(r.recipe!)).length, nostock: all.filter((r) => r.recipe?.ignored).length };
  // a search with no tab chosen opens on wherever it found something
  const asked = params.get("tab") as Tab | null;
  const tab: Tab = asked && TABS.includes(asked) ? asked : q ? (made.length ? "recipes" : direct.length ? "direct" : noStock.length ? "nostock" : "todo") : queue.data && todo.length === 0 ? "recipes" : "todo";

  const error = (text: string) => setMsg({ kind: "error", text });
  const saved = (text: string) => { setEditing(null); setAdding(false); setMsg({ kind: "ok", text }); menu.reload(); queue.reload(); };
  const upload = async (f: File) => {
    setBusy(true);
    try {
      const r = await api.upload<ImportSummary>("/api/recipes/import", f);
      const warn = r.rows.filter((x) => x.message.includes("can't be converted")).length;
      if (r.status === "ok") saved(`Imported ${f.name}: ${r.inserted} new, ${r.updated} updated recipes.${warn ? ` ${warn} use items counted in 'each', so their portions can't be converted until the item's unit is fixed on Stock items.` : ""}`);
      else error(`Nothing imported. ${r.rows.map((x) => x.message).join(" · ")}`);
      items.reload();
    } catch (e) { error((e as Error).message); } finally { setBusy(false); }
  };
  const shared = { editing, setEditing, onPhoto: setViewing, items: items.data ?? [], onSaved: saved, onError: error };

  return (
    <Page area="recipes" title="Recipes" sub="Link what Swype sells to what you count" actions={<>
      <Button kind="quiet" edit onClick={() => setAdding((v) => !v)}>{adding ? "Close" : "Add unsold item"}</Button>
      <FileButton label="Upload Recipes.xlsx" onFile={upload} busy={busy} />
    </>}>
      {msg && <Flash kind={msg.kind} onClose={() => setMsg(null)}>{msg.text}</Flash>}
      {(menu.error || queue.error) && <Notice kind="error">{menu.error ?? queue.error}</Notice>}
      {adding && <AddItem items={items.data ?? []} onSaved={saved} onError={error} />}
      {!menu.data || !queue.data ? !(menu.error || queue.error) && <Loading /> : all.length === 0 ? <Empty>No Swype sales imported yet. Import them on the Sales page, or add an unsold item above.</Empty> : (<>
        <div className="flex flex-wrap items-center gap-3 mb-5">
          <Seg value={tab} onChange={(v) => setParam("tab", v)} options={[["todo", `To link (${todo.length})`], ["recipes", `Recipes (${counts.made})`], ["direct", `1-to-1 (${counts.direct})`], ["nostock", `No stock (${counts.nostock})`], ["stock", "By stock item"]]} />
          <input className={`${inputCls} w-full sm:w-64`} placeholder="Search" aria-label="Search recipes" value={q} onChange={(e) => setParam("q", e.target.value)} />
        </div>
        {tab === "todo" && <ToLink rows={todoShown} foodCoffee={queue.data.no_stock} {...shared} />}
        {tab === "recipes" && <RecipeList rows={made} empty={q ? "Nothing matches." : "No recipes yet. Use Full recipe on To link to write one."} {...shared} />}
        {tab === "direct" && <RecipeList rows={direct} empty={q ? "Nothing matches." : "Nothing linked 1-to-1 yet. Approve items on To link."} {...shared} />}
        {tab === "nostock" && <RecipeList rows={noStock} empty={q ? "Nothing matches." : "Nothing is marked as taking no stock."} {...shared} />}
        {tab === "stock" && <ByIngredient rows={linked} />}
      </>)}
      {viewing?.recipe?.photo_at && <Lightbox src={photoUrl(viewing.recipe)} alt={viewing.name} caption={viewing.name} onClose={closePhoto} />}
    </Page>
  );
}

function Seg<T extends string>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: [T, string][] }) {
  return (
    <div className="inline-flex flex-wrap rounded-[10px] border border-line overflow-hidden text-sm" role="group">
      {options.map(([k, label]) => <button key={k} aria-pressed={value === k} onClick={() => onChange(k)}
        className={`px-3 py-1.5 ${value === k ? "bg-raised text-ink font-medium" : "bg-surface text-muted hover:text-ink"}`}>{label}</button>)}
    </div>
  );
}

type Shared = { editing: string | null; setEditing: (c: string | null) => void; onPhoto: (r: MenuRow) => void; items: Item[]; onSaved: (t: string) => void; onError: (t: string) => void };

function Thumb({ row, onPhoto, size = "w-12 h-12" }: { row: MenuRow; onPhoto: (r: MenuRow) => void; size?: string }) {
  if (!row.recipe?.photo_at) return null;
  return (
    <button type="button" onClick={() => onPhoto(row)} aria-label={`Show photo of ${row.name}`} className="shrink-0 rounded-md focus-visible:outline-2 focus-visible:outline-accent">
      <img src={photoUrl(row.recipe)} alt="" loading="lazy" className={`${size} rounded-md object-cover border border-line`} />
    </button>);
}

/** Portions, version history and the recipe notes that explain them. */
function Portions({ r }: { r: MenuRow }) {
  if (!r.recipe) return null;
  return (<>
    {r.recipe.ignored ? <span className="text-muted text-sm">takes no tracked stock</span>
      : <ul className="text-sm space-y-0.5">{r.recipe.lines.map((l, i) => (
          <li key={i}><span className="num">{n(l.qty, 2)} {l.unit}</span> {l.item_name}
            {l.error ? <span className="text-danger text-xs"> · can't convert to {l.stock_unit}</span>
              : portionText(l) && <span className="text-faint text-xs"> · {portionText(l)}</span>}</li>))}</ul>}
    {r.recipe.versions.length > 1 && <div className="text-xs text-faint">since {day(r.recipe.effective_from)} · {r.recipe.versions.length} versions</div>}
    {r.recipe.notes && <div className="mt-1.5 border-l-2 border-accent/60 pl-2 text-xs text-muted whitespace-pre-line max-w-md">{r.recipe.notes}</div>}
  </>);
}

/** Saved recipes by Swype category. Steps, notes and history live in the editor. */
function RecipeList({ rows, empty, editing, setEditing, onPhoto, items, onSaved, onError }: Shared & { rows: MenuRow[]; empty: string }) {
  if (rows.length === 0) return <Empty>{empty}</Empty>;
  return (
    <div className="flex flex-col gap-6">
      {groupBy(rows, (r) => r.category ?? "Other").map(([cat, list]) => (
        <section key={cat}>
          <h2 className="label-caps mb-2 px-1">{cat}</h2>
          <ul className="card">{list.map((r) => (
            <li key={r.code} className="border-b border-line last:border-0">
              <div className="grid gap-x-4 gap-y-1.5 p-3 items-start md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_5rem_auto]">
                <div className="flex items-start gap-2.5 min-w-0"><Thumb row={r} onPhoto={onPhoto} /><div className="min-w-0">{r.name}<div className="text-xs text-faint mono">{r.code}</div></div></div>
                <div><Portions r={r} /></div>
                <div className="text-sm text-muted md:text-right"><span className="num">{n(r.sold, 0)}</span> sold</div>
                <Button kind="quiet" className="justify-self-start" onClick={() => setEditing(editing === r.code ? null : r.code)}>{editing === r.code ? "Close" : "Edit"}</Button>
              </div>
              {editing === r.code && <div className="border-t border-line bg-paper"><Editor row={r} items={items} onSaved={onSaved} onError={onError} onPhoto={onPhoto} /></div>}
            </li>))}</ul>
        </section>))}
    </div>
  );
}

/** A Swype item that hasn't sold yet, so it isn't on any tab: name it, then write its recipe. */
function AddItem({ items, onSaved, onError }: { items: Item[]; onSaved: (t: string) => void; onError: (t: string) => void }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const row = code.trim() && name.trim() ? blankRow(code.trim(), name.trim(), null) : null;
  return (
    <div className="card p-5 mb-6 flex flex-col gap-3">
      <div className="flex flex-wrap items-end gap-3 text-sm">
        <Field label="Swype #"><input className={`${inputCls} w-28 mono`} value={code} onChange={(e) => setCode(e.target.value)} /></Field>
        <Field label="Name"><input className={`${inputCls} w-full sm:w-64`} value={name} onChange={(e) => setName(e.target.value)} /></Field>
      </div>
      {row && <div className="border-t border-line -mx-5 bg-paper"><Editor key={row.code} row={row} items={items} onSaved={onSaved} onError={onError} /></div>}
    </div>
  );
}

const blankRow = (code: string, name: string, category: string | null) =>
  ({ code, name, department: null, category, sold: 0, weeks: 0, last_sold: null, suggested_item: null, recipe: null }) as MenuRow;

type Draft = { item: string; qty: string; unit: string };

function Editor({ row, seed, items, onSaved, onError, onPhoto }: { row: MenuRow; seed?: Draft[]; items: Item[]; onSaved: (t: string) => void; onError: (t: string) => void;
  onPhoto?: (r: MenuRow) => void }) {
  const viewOnly = useReadOnly();
  const label = (it: Item) => `${it.name} — ${it.sku}`;
  const byLabel = useMemo(() => new Map(items.map((it) => [label(it), it])), [items]);
  const itemLabels = useMemo(() => [...byLabel.keys()], [byLabel]);
  const byId = useMemo(() => new Map(items.map((it) => [it.id, it])), [items]);
  const start: Draft[] = row.recipe?.lines.map((l) => ({ item: byId.get(l.item_id) ? label(byId.get(l.item_id)!) : l.item_name, qty: String(Number(l.qty)), unit: l.unit }))
    ?? seed ?? (row.suggested_item && byId.get(row.suggested_item.id) ? [{ item: label(byId.get(row.suggested_item.id)!), qty: "", unit: "oz" }] : [{ item: "", qty: "", unit: "oz" }]);
  const [lines, setLines] = useState<Draft[]>(start);
  const [ignored, setIgnored] = useState(row.recipe?.ignored ?? false);
  const [steps, setSteps] = useState(row.recipe?.steps ?? "");
  const [notes, setNotes] = useState(row.recipe?.notes ?? "");
  const [photoBusy, setPhotoBusy] = useState(false);
  const [dated, setDated] = useState(false);
  const [from, setFrom] = useState(new Date().toLocaleDateString("en-CA"));
  const [busy, setBusy] = useState(false);
  const set = (i: number, p: Partial<Draft>) => setLines((ls) => ls.map((l, j) => (j === i ? { ...l, ...p } : l)));

  const save = async () => {
    const out = [];
    for (const l of ignored ? [] : lines) {
      if (!l.item && !l.qty) continue;
      const it = byLabel.get(l.item);
      if (!it) return onError(`Pick "${l.item || "an ingredient"}" from the list of stock items.`);
      out.push({ item_id: it.id, qty: l.qty, unit: l.unit });
    }
    setBusy(true);
    try {
      await api.put<Recipe>(`/api/recipes/${encodeURIComponent(row.code)}`, { name: row.name, lines: out, ignored, steps: steps.trim() || null, notes: notes.trim() || null, effective_from: dated ? from : null });
      onSaved(`Saved recipe for ${row.name}.`);
    } catch (e) { onError((e as Error).message); } finally { setBusy(false); }
  };
  const remove = async () => {
    try { await api.del(`/api/recipes/${encodeURIComponent(row.code)}`); onSaved(`Removed recipe for ${row.name}.`); } catch (e) { onError((e as Error).message); }
  };
  const photoPath = `/api/recipes/${encodeURIComponent(row.code)}/photo`;
  const setPhoto = async (f: File) => {
    setPhotoBusy(true);
    try {
      const b = await shrink(f);
      await api.upload<Recipe>(photoPath, b, "PUT", b === f ? f.name : "photo.jpg");
      onSaved(`Saved photo for ${row.name}.`);
    } catch (e) { onError((e as Error).message); } finally { setPhotoBusy(false); }
  };
  const removePhoto = async () => {
    try { await api.del(photoPath); onSaved(`Removed photo for ${row.name}.`); } catch (e) { onError((e as Error).message); }
  };

  return (
    <div className="p-3 flex flex-col gap-3">
      {!ignored && (<>
        {lines.map((l, i) => {
          const it = byLabel.get(l.item);
          return (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <Combobox className="w-full sm:w-80" options={itemLabels} placeholder="Stock item" aria-label="Stock item" value={l.item} onChange={(v) => set(i, { item: v, ...(byLabel.get(v)?.stock_unit === "each" ? { unit: "each" } : {}) })} />
              <input className={`${inputCls} w-20 num text-right`} placeholder="qty" value={l.qty} onChange={(e) => set(i, { qty: e.target.value })} aria-label="Portion" />
              <select className={inputCls} value={l.unit} onChange={(e) => set(i, { unit: e.target.value })} aria-label="Unit">{UNITS.map((u) => <option key={u}>{u}</option>)}</select>
              {it && <span className="text-xs text-faint">counted in {it.stock_unit}{Number(it.pack_size) > 1 ? `, ${n(it.pack_size, 0)} ${it.stock_unit} per ${it.pack_name || "pack"}` : ""}</span>}
              <button className="text-xs text-muted hover:text-danger underline" onClick={() => setLines((ls) => ls.filter((_, j) => j !== i))}>remove</button>
            </div>);
        })}
        <div><button className="text-sm underline text-muted hover:text-ink" onClick={() => setLines((ls) => [...ls, { item: "", qty: "", unit: "oz" }])}>+ add stock item</button></div>
      </>)}
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={ignored} onChange={(e) => setIgnored(e.target.checked)} /> Takes no stock (leave it out of Variance)</label>
      <details className="text-sm">
        <summary className="text-muted cursor-pointer select-none py-1">Steps, notes and photo</summary>
        <div className="flex flex-col gap-3 pt-2">
          <div className="grid gap-3 lg:grid-cols-2 max-w-5xl">
            <Field label="Preparation steps"><textarea className={`${inputCls} w-full`} rows={5} value={steps} onChange={(e) => setSteps(e.target.value)} /></Field>
            <Field label="Notes"><textarea className={`${inputCls} w-full`} rows={5} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            {onPhoto && <Thumb row={row} onPhoto={onPhoto} size="w-24 h-24" />}
            {row.recipe
              ? <>
                  <FileButton label={row.recipe.photo_at ? "Replace photo" : "Add photo"} accept="image/jpeg,image/png,image/webp,image/gif" onFile={setPhoto} busy={photoBusy} />
                  {row.recipe.photo_at && !viewOnly && <button className="text-xs text-muted hover:text-danger underline" onClick={removePhoto}>remove photo</button>}
                </>
              : <span className="text-xs text-faint">Save the recipe first to add a photo.</span>}
          </div>
        </div>
      </details>
      {row.recipe && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <label className="flex items-center gap-2"><input type="checkbox" checked={dated} onChange={(e) => setDated(e.target.checked)} /> Recipe changed on a date, keep earlier weeks as they were</label>
          {dated && <input type="date" className={inputCls} aria-label="Date the recipe changed" value={from} onChange={(e) => setFrom(e.target.value)} />}
        </div>)}
      <div className="flex gap-2"><Button onClick={save} disabled={busy}>Save recipe</Button>{row.recipe && <Button kind="danger" onClick={remove}>Delete recipe</Button>}</div>
    </div>
  );
}

type MatchDraft = { on: boolean; noStock: boolean; changing: boolean; item: string; qty: string; unit: string };
const itemLabel = (it: { name: string; sku: string }) => `${it.name} — ${it.sku}`;

/** Everything sold with no recipe, split into what can be approved as it stands and what needs a person. */
function ToLink({ rows, foodCoffee, editing, setEditing, items, onSaved, onError }: Shared & { rows: MatchRow[]; foodCoffee: number }) {
  const ro = useReadOnly();
  const byLabel = useMemo(() => new Map(items.map((it) => [itemLabel(it), it])), [items]);
  const options = useMemo(() => [...byLabel.keys()], [byLabel]);
  const [edits, setEdits] = useState<Record<string, Partial<MatchDraft>>>({});
  const [busy, setBusy] = useState(false);
  const draft = (r: MatchRow): MatchDraft => {
    const l = r.lines.length === 1 ? r.lines[0] : null;
    return { on: r.clear, noStock: false, changing: false, item: l ? itemLabel(l) : "", qty: l?.qty == null ? "" : String(Number(l.qty)), unit: l?.unit ?? "oz", ...edits[r.code] };
  };
  const set = (code: string, p: Partial<MatchDraft>) => setEdits((e) => ({ ...e, [code]: { ...e[code], ...p } }));
  const pick = (r: MatchRow, d: MatchDraft, v: string) => {
    const it = byLabel.get(v);
    set(r.code, { item: v, unit: it?.stock_unit === "each" ? "each" : "oz", on: d.on || (!!it && Number(d.qty) > 0) });
  };
  const ready = (r: MatchRow, d: MatchDraft) => d.noStock || r.lines.length > 1 || (byLabel.has(d.item) && Number(d.qty) > 0);
  const chosen = rows.filter((r) => { const d = draft(r); return d.on && ready(r, d); });

  const act = async (fn: () => Promise<string>) => {
    setBusy(true);
    try { const t = await fn(); setEdits({}); onSaved(t); } catch (e) { onError((e as Error).message); } finally { setBusy(false); }
  };
  const approve = () => act(async () => {
    const body = chosen.map((r) => {
      const d = draft(r);
      const lines = d.noStock ? [] : r.lines.length > 1 ? r.lines.map((l) => ({ item_id: l.id, qty: l.qty, unit: l.unit })) : [{ item_id: byLabel.get(d.item)!.id, qty: d.qty, unit: d.unit }];
      return { code: r.code, name: r.name, lines, ignored: d.noStock };
    });
    const x = await api.post<{ saved: number }>("/api/recipes/match/approve", { rows: body });
    return `Linked ${x.saved} items.`;
  });
  const markFood = () => act(async () => `Marked ${(await api.post<{ saved: number }>("/api/recipes/no-stock", {})).saved} food and coffee items as taking no stock.`);

  const row = (r: MatchRow) => {
    const d = draft(r);
    const edit = !d.noStock && r.lines.length <= 1 && (!r.clear || d.changing);
    return (
      <li key={r.code} className="border-b border-line last:border-0">
        <div className="grid gap-x-4 gap-y-1.5 p-3 items-center grid-cols-[auto_minmax(0,1fr)] md:grid-cols-[auto_minmax(0,1fr)_minmax(0,1.4fr)_5rem_auto]">
          <input type="checkbox" disabled={ro || !ready(r, d)} aria-label={`Link ${r.name}`} checked={d.on && ready(r, d)} onChange={(e) => set(r.code, { on: e.target.checked })} />
          <div className="min-w-0">{r.name}{r.check && <span className="block text-xs text-amber">{r.check}</span>}</div>
          <div className="col-start-2 md:col-start-auto min-w-0">
            {d.noStock ? <span className="text-sm text-muted">Takes no stock</span>
              : r.lines.length > 1 ? <span className="text-sm">{r.lines.map((l) => `${n(l.qty, 2)} ${l.unit} ${l.name}`).join(", ")}</span>
              : !edit ? <span className="text-sm"><span className="num">{d.qty} {d.unit}</span> {byLabel.get(d.item)?.name ?? d.item}</span>
              : (<>
                <div className="flex items-center gap-2">
                  <Combobox className="flex-1 min-w-0" aria-label={`${r.name} stock item`} value={d.item} onChange={(v) => pick(r, d, v)} options={options} placeholder="Pick the stock item" />
                  <input className="cell-input num w-14 shrink-0" inputMode="decimal" placeholder="qty" aria-label={`${r.name} portion`} value={d.qty} onChange={(e) => set(r.code, { qty: e.target.value, on: d.on || (byLabel.has(d.item) && Number(e.target.value) > 0) })} />
                  <span className="text-sm text-muted shrink-0">{d.unit}</span>
                </div>
                {r.candidates.length > 1 && <span className="block text-xs text-muted mt-1">or {r.candidates.filter((c) => itemLabel(c) !== d.item).map((c) => <button key={c.id} className="underline mr-2" disabled={ro} onClick={() => pick(r, d, itemLabel(c))}>{c.name}</button>)}</span>}
              </>)}
          </div>
          <div className="col-start-2 md:col-start-auto text-sm text-muted md:text-right"><span className="num">{n(r.sold, 0)}</span> sold</div>
          <div className="col-start-2 md:col-start-auto flex gap-3 text-xs text-muted whitespace-nowrap">
            {r.clear && !d.noStock && !edit && <button className="underline hover:text-ink" disabled={ro} onClick={() => set(r.code, { changing: true })}>Change</button>}
            <button className="underline hover:text-ink" disabled={ro} onClick={() => setEditing(editing === r.code ? null : r.code)}>{editing === r.code ? "Close" : "Full recipe"}</button>
            <button className="underline hover:text-ink" disabled={ro} onClick={() => set(r.code, { noStock: !d.noStock, on: !d.noStock })}>{d.noStock ? "Undo" : "No stock"}</button>
          </div>
        </div>
        {editing === r.code && <div className="border-t border-line bg-paper"><Editor row={blankRow(r.code, r.name, r.category)} items={items} onSaved={onSaved} onError={onError}
          seed={r.lines.map((l) => ({ item: itemLabel(l), qty: l.qty == null ? "" : String(Number(l.qty)), unit: l.unit }))} /></div>}
      </li>);
  };

  const clear = rows.filter((r) => r.clear), rest = rows.filter((r) => !r.clear);
  if (rows.length === 0) return <Empty>Nothing to link here.</Empty>;
  return (
    <div className="flex flex-col gap-6">
      {foodCoffee > 0 && <Notice kind="info"><span className="flex flex-wrap items-center justify-between gap-3">{foodCoffee} food and coffee items take no stock.<Button kind="quiet" edit onClick={markFood} disabled={busy}>Mark them</Button></span></Notice>}
      {clear.length > 0 && <details className="card p-3">
        <summary className="cursor-pointer select-none"><span className="font-semibold">Ready to link ({clear.length})</span> <span className="text-sm text-muted">matched clearly. Open to check them, and untick anything wrong.</span></summary>
        <ul className="mt-3 -mx-3 -mb-3 border-t border-line">{clear.map(row)}</ul>
      </details>}
      {rest.length > 0 && <section><h2 className="font-semibold mb-1 px-1">Needs you</h2><p className="text-sm text-muted mb-2 px-1">Check the stock item and portion, write a full recipe for a cocktail, or mark it No stock.</p><ul className="card">{rest.map(row)}</ul></section>}
      <div className="sticky bottom-0 z-10 card p-3 flex items-center justify-between gap-3">
        <span className="text-sm text-muted">{chosen.length} ticked</span>
        <Button onClick={approve} disabled={busy || chosen.length === 0}>Link {chosen.length} items</Button>
      </div>
    </div>
  );
}

/** Reverse view: each inventory item, and every sold item that pours from it. */
function ByIngredient({ rows }: { rows: MenuRow[] }) {
  const m = new Map<string, { name: string; line: RecipeLine; uses: { row: MenuRow; line: RecipeLine }[] }>();
  for (const r of rows) for (const l of r.recipe?.lines ?? []) {
    if (!m.has(l.item_id)) m.set(l.item_id, { name: l.item_name, line: l, uses: [] });
    m.get(l.item_id)!.uses.push({ row: r, line: l });
  }
  const list = [...m.values()].sort((a, b) => a.name.localeCompare(b.name));
  if (list.length === 0) return <Empty>No recipes here yet.</Empty>;
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {list.map((g) => {
        const total = g.uses.reduce((t, u) => t + Number(u.line.stock_qty ?? 0) * Number(u.row.sold), 0);
        return (
          <section key={g.line.item_id} className="card p-4">
            <h3 className="font-semibold">{g.name}</h3>
            <p className="text-xs text-faint mb-2">{g.uses.length} sold items · {n(total, 0)} {g.line.stock_unit} over the weeks on file
              {Number(g.line.pack_size) > 1 && ` (${n(total / Number(g.line.pack_size), 1)} ${pack(g.line)})`}</p>
            <ul className="text-sm space-y-0.5">{g.uses.sort((a, b) => Number(b.row.sold) - Number(a.row.sold)).map((u) => (
              <li key={u.row.code} className="flex justify-between gap-2"><span>{u.row.name}</span><span className="num text-muted shrink-0">{n(u.line.qty, 2)} {u.line.unit}</span></li>))}</ul>
          </section>);
      })}
    </div>
  );
}
