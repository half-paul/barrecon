import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type Group, type ImportSummary, type Item, groupBy, n } from "../lib/api";
import { useMe, useReadOnly } from "../lib/auth";
import { Button, Empty, Field, FileButton, Flash, Help, inputCls, Loading, Nil, Notice, Page, useAsync } from "../lib/ui";

type Units = { code: string; dimension: string }[];
const blank = { sku: "", name: "", category: "Spirits", stock_unit: "ml", pack_name: "bottle", pack_size: "750", case_size: "12", pack_cost: "", par_level: "" };

type Status = "active" | "off" | "all";
const UNCAT = "Uncategorised";
const catOf = (i: Item) => i.category || UNCAT;
const byName = (a: string, b: string) => (a === UNCAT ? 1 : b === UNCAT ? -1 : a.localeCompare(b));

export default function Items() {
  const [params, setParams] = useSearchParams();
  const setParam = (k: string, v: string) => {
    const p = new URLSearchParams(params);
    if (v) p.set(k, v); else p.delete(k);
    setParams(p, { replace: true });
  };
  const q = params.get("q") ?? "";
  const cat = params.get("cat") ?? "";
  const items = useAsync(() => api.get<Item[]>("/api/items?include_inactive=true"));
  const all = items.data ?? [];
  // default to active items, unless nothing is switched on yet
  const status: Status = (params.get("status") as Status | null) ?? (all.some((i) => i.is_active) ? "active" : "all");
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const matchesQ = (i: Item) => words.every((w) => [i.name, i.sku, i.category ?? ""].some((f) => f.toLowerCase().includes(w)));
  const searched = all.filter(matchesQ);
  const statusCount = { active: searched.filter((i) => i.is_active).length, off: searched.filter((i) => !i.is_active).length, all: searched.length };
  const pool = searched.filter((i) => status === "all" || (status === "active") === i.is_active);
  const chips = groupBy(pool, catOf).map(([c, rows]) => [c, rows.length] as const).sort((a, b) => byName(a[0], b[0]));
  const groups = groupBy(pool.filter((i) => !cat || catOf(i) === cat), catOf).sort((a, b) => byName(a[0], b[0]));
  const shownCount = groups.reduce((t, [, rows]) => t + rows.length, 0);
  const groupList = useAsync(() => api.get<Group[]>("/api/groups"));
  const allGroups = [...new Set([...(groupList.data ?? []).map((g) => g.name), ...all.map((i) => i.category).filter((c): c is string => !!c)])].sort();
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const toggle = (c: string) => setCollapsed((s) => { const x = new Set(s); if (x.has(c)) x.delete(c); else x.add(c); return x; });
  const allCollapsed = groups.length > 0 && groups.every(([c]) => collapsed.has(c));
  const units = useAsync(() => api.get<Units>("/api/items/units"));
  const food = !!useMe()?.business?.track_food;  // food is stock here too: the hints name weights and food packs
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState(blank);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);

  // a known group fills in its counted unit and pack; the fields stay editable for this item
  const setCategory = (category: string) => {
    const g = groupList.data?.find((x) => x.name === category);
    const trim = (v: string) => v.replace(/\.?0+$/, "");
    setForm(g ? { ...form, category, stock_unit: g.stock_unit, pack_name: g.pack_name ?? "", pack_size: trim(g.pack_size), case_size: trim(g.case_size) } : { ...form, category });
  };
  const save = async () => {
    try {
      await api.post("/api/items", { ...form, pack_cost: form.pack_cost || null, par_level: form.par_level || null });
      setAdding(false); setForm(blank); items.reload(); groupList.reload(); setMsg({ kind: "ok", text: `Added ${form.name}.` });
    } catch (e) { setMsg({ kind: "error", text: (e as Error).message }); }
  };
  const update = async (it: Item, changes: Partial<Record<keyof Item, string | boolean | null>>) => {
    if (Object.keys(changes).length === 0) { setEditing(null); return; }
    try { await api.patch(`/api/items/${it.id}`, changes); setEditing(null); setMsg(null); items.reload(); groupList.reload(); }
    catch (e) { setMsg({ kind: "error", text: (e as Error).message }); }
  };
  const upload = async (f: File) => {
    setBusy(true);
    try {
      const r = await api.upload<ImportSummary>("/api/items/import", f);
      setMsg(r.status === "ok" ? { kind: "ok", text: `Imported ${f.name}: ${r.inserted} new, ${r.updated} updated.` }
                               : { kind: "error", text: `Nothing imported. ${r.rows.map((x) => x.message).join(" · ")}` });
      items.reload();
    } catch (e) { setMsg({ kind: "error", text: (e as Error).message }); } finally { setBusy(false); }
  };

  return (
    <Page area="items" title="Stock items" actions={<>
      <Link className="text-sm underline" to="/items/groups">Item groups</Link>
      <a className="text-sm underline" href="/api/items/export.xlsx">Download Items.xlsx</a>
      <FileButton label="Upload Items.xlsx" onFile={upload} busy={busy} />
      <Button onClick={() => setAdding((v) => !v)}>{adding ? "Cancel" : "Add item"}</Button>
    </>}>
      {msg && <Flash kind={msg.kind} onClose={() => setMsg(null)}>{msg.text}</Flash>}
      {adding && (
        <div className="card grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 mb-6 p-5">
          <Field label={<>SKU (BCLDB no. or your own code)<Help term="sku" /></>}><input className={inputCls} value={form.sku} onChange={(e) => setForm({ ...form, sku: e.target.value })} /></Field>
          <Field label="Name"><input className={inputCls} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          <Field label="Group" hint="Picking a group fills in its unit and pack"><input className={inputCls} list="item-groups" value={form.category} onChange={(e) => setCategory(e.target.value)} /></Field>
          <Field label={<>Counted in<Help term="stock_unit" /></>} hint={food ? "ml for liquids, each for cans/bottles of beer, kg, lb or g for food" : "ml for liquids, each for cans/bottles of beer"}><select className={inputCls} value={form.stock_unit} onChange={(e) => setForm({ ...form, stock_unit: e.target.value })}>{(units.data ?? []).map((u) => <option key={u.code}>{u.code}</option>)}</select></Field>
          <Field label={<>Pack name<Help term="pack" /></>}><input className={inputCls} value={form.pack_name} onChange={(e) => setForm({ ...form, pack_name: e.target.value })} /></Field>
          <Field label={<>Pack size (in counted unit)<Help term="pack_size" /></>} hint={food ? "750 for a 750 ml bottle; 1 for a can; 2.5 for a 2.5 kg bag" : "750 for a 750 ml bottle; 1 for a can"}><input className={`${inputCls} num`} value={form.pack_size} onChange={(e) => setForm({ ...form, pack_size: e.target.value })} /></Field>
          <Field label={<>Packs per case / flat<Help term="case_size" /></>}><input className={`${inputCls} num`} value={form.case_size} onChange={(e) => setForm({ ...form, case_size: e.target.value })} /></Field>
          <Field label={<>Cost per pack ($)<Help term="cost_per_pack" /></>}><input className={`${inputCls} num`} value={form.pack_cost} onChange={(e) => setForm({ ...form, pack_cost: e.target.value })} /></Field>
          <Field label={<>Par (packs)<Help term="par" /></>}><input className={`${inputCls} num`} value={form.par_level} onChange={(e) => setForm({ ...form, par_level: e.target.value })} /></Field>
          <div className="col-span-full"><Button onClick={save} disabled={!form.sku || !form.name}>Save item</Button></div>
        </div>
      )}
      {items.loading && !items.data && !items.error && <Loading />}
      {items.error && <Notice kind="error">{items.error}</Notice>}
      {items.data && items.data.length === 0 && <Empty>No items yet. Add one, upload Items.xlsx, or import your existing inventory workbook under Imports.</Empty>}
      {all.length > 0 && (<>
        <div className="flex flex-wrap items-center gap-3 mb-3">
          <input className={`${inputCls} w-full sm:w-72`} placeholder="Filter by name, SKU or group" aria-label="Filter items" value={q} onChange={(e) => setParam("q", e.target.value)} />
          <div className="inline-flex rounded-control border border-line overflow-hidden text-sm" role="group" aria-label="Status">
            {([["active", "Active"], ["off", "Switched off"], ["all", "All"]] as const).map(([k, label]) => (
              <button key={k} aria-pressed={status === k} onClick={() => setParam("status", k)}
                className={`px-3 py-1.5 ${status === k ? "bg-raised text-ink font-medium" : "bg-surface text-muted hover:text-ink"}`}>
                {label} <span className="mono text-xs text-faint">{statusCount[k]}</span></button>))}
          </div>
          <span className="text-sm text-muted">Showing {shownCount} of {all.length}</span>
          {!cat && groups.length > 1 && (
            <button className="text-sm text-muted hover:text-ink underline ml-auto" onClick={() => setCollapsed(allCollapsed ? new Set() : new Set(groups.map(([c]) => c)))}>
              {allCollapsed ? "Expand all groups" : "Collapse all groups"}</button>)}
        </div>
        <div className="flex flex-wrap gap-1.5 mb-5" role="group" aria-label="Item groups">
          <Chip on={!cat} onClick={() => setParam("cat", "")} label="All groups" count={pool.length} />
          {chips.map(([c, count]) => <Chip key={c} on={cat === c} onClick={() => setParam("cat", cat === c ? "" : c)} label={c} count={count} />)}
          {cat && !chips.some(([c]) => c === cat) && <Chip on onClick={() => setParam("cat", "")} label={cat} count={0} />}
        </div>
      </>)}
      <datalist id="item-groups">{allGroups.map((c) => <option key={c} value={c} />)}</datalist>
      {all.length > 0 && shownCount === 0 && (
        <Empty>No items match. <button className="underline" onClick={() => setParams({ status: "all" }, { replace: true })}>Clear filters</button></Empty>)}
      {shownCount > 0 && (
        <div className="table-box"><table className="grid">
          <thead><tr><th>SKU<Help term="sku" /></th><th>Item</th><th>Group</th><th>Pack<Help term="pack" /></th><th className="r">Pack size<Help term="pack_size" /></th><th className="r">Per case<Help term="case_size" /></th><th className="r">Cost / pack<Help term="cost_per_pack" /></th><th className="r">Par<Help term="par" /></th><th className="r">Last count</th><th>Active</th><th /></tr></thead>
          <tbody>
            {groups.map(([c, rows]) => (
              <Group key={c} cat={c} rows={rows} editing={editing} onEdit={setEditing} onSave={update} open={!!cat || !collapsed.has(c)}
                onToggle={cat ? undefined : () => toggle(c)} onOnly={cat ? undefined : () => setParam("cat", c)} />
            ))}
          </tbody>
        </table></div>
      )}
    </Page>
  );
}

function Chip({ on, onClick, label, count }: { on: boolean; onClick: () => void; label: string; count: number }) {
  return (
    <button aria-pressed={on} onClick={onClick}
      className={`rounded-full border px-3 py-1 text-sm ${on ? "bg-accent text-accent-ink border-accent font-semibold" : "bg-surface border-line text-muted hover:text-ink hover:border-line-strong"}`}>
      {label} <span className={`mono text-xs ${on ? "" : "text-faint"}`}>{count}</span>
    </button>
  );
}

type Changes = Partial<Record<keyof Item, string | boolean | null>>;

function Group({ cat, rows, editing, onEdit, onSave, open, onToggle, onOnly }: { cat: string; rows: Item[]; editing: string | null;
  onEdit: (id: string | null) => void; onSave: (it: Item, changes: Changes) => void; open: boolean; onToggle?: () => void; onOnly?: () => void }) {
  const active = rows.filter((r) => r.is_active).length;
  return (<>
    <tr className="section"><td colSpan={11}>
      <div className="flex items-center gap-3">
        {onToggle ? (
          <button type="button" className="flex items-center gap-2 hover:text-accent" aria-expanded={open} onClick={onToggle}>
            <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden className={`transition-transform ${open ? "rotate-90" : ""}`}><path d="M3 1l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg>
            {cat}</button>
        ) : <span>{cat}</span>}
        <span className="text-faint font-normal">{rows.length} items · {active} active</span>
        {onOnly && <button className="text-xs font-normal text-faint hover:text-ink underline" onClick={onOnly}>show only this group</button>}
      </div>
    </td></tr>
    {open && rows.map((it) => editing === it.id
      ? <EditRow key={it.id} it={it} onSave={(c) => onSave(it, c)} onCancel={() => onEdit(null)} />
      : <ViewRow key={it.id} it={it} onEdit={() => onEdit(it.id)} />)}
  </>);
}

const lastCount = (it: Item) => it.last_counted_packs != null ? Number(it.last_counted_packs).toFixed(1) : <span className="text-muted">never</span>;

function ViewRow({ it, onEdit }: { it: Item; onEdit: () => void }) {
  const viewOnly = useReadOnly();
  return (
    <tr className={it.is_active ? "" : "text-muted"}>
      <td className="num text-muted">{it.sku}</td>
      <td>{it.name}</td>
      <td>{it.category || <span className="text-faint">{UNCAT}</span>}</td>
      <td>{it.pack_name || <Nil />}</td>
      <td className="r num whitespace-nowrap">{n(it.pack_size, 3)}&nbsp;<span className="text-muted">{it.stock_unit}</span></td>
      <td className="r num">{n(it.case_size, 3)}</td>
      <td className="r num">{it.pack_cost ? n(it.pack_cost) : <Nil />}</td>
      <td className="r num">{it.par_level ? n(it.par_level, 1) : <Nil />}</td>
      <td className="r num">{lastCount(it)}</td>
      <td>{it.is_active ? "Yes" : <span className="text-faint">Off</span>}</td>
      <td className="r"><button hidden={viewOnly} className="text-sm text-muted hover:text-ink underline" aria-label={`Edit ${it.name}`} onClick={onEdit}>Edit</button></td>
    </tr>
  );
}

const FIELDS = ["sku", "name", "category", "pack_name", "pack_size", "case_size", "pack_cost", "par_level"] as const;
type Field = (typeof FIELDS)[number];
const NUMERIC = new Set<Field>(["pack_size", "case_size", "pack_cost", "par_level"]);

// numbers come back as "750.0000": show at most 2 decimals, money always 2
const editValue = (it: Item, k: Field) => {
  const v = it[k];
  if (v == null || v === "") return "";
  if (!NUMERIC.has(k)) return String(v);
  return k === "pack_cost" ? Number(v).toFixed(2) : String(Number(Number(v).toFixed(2)));
};

function EditRow({ it, onSave, onCancel }: { it: Item; onSave: (c: Changes) => void; onCancel: () => void }) {
  const [initial] = useState(() => Object.fromEntries(FIELDS.map((k) => [k, editValue(it, k)])) as Record<Field, string>);
  const [f, setF] = useState(initial);
  const [active, setActive] = useState(it.is_active);
  const save = () => {
    const c: Changes = {};
    for (const k of FIELDS) { const v = f[k].trim(); if (v !== initial[k]) c[k] = v === "" ? null : v; }
    if (active !== it.is_active) c.is_active = active;
    onSave(c);
  };
  const input = (k: Field, opts: { right?: boolean; list?: string; placeholder?: string } = {}) => (
    <input className={`cell-input ${opts.right ? "w-20" : "text-left w-full min-w-24"}`} value={f[k]} placeholder={opts.placeholder} list={opts.list}
      inputMode={NUMERIC.has(k) ? "decimal" : undefined} aria-label={k.replace("_", " ")} onChange={(e) => setF({ ...f, [k]: e.target.value })} />
  );
  return (
    <tr className="bg-raised/40" onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") onCancel(); }}>
      <td className="num text-muted">{input("sku")}</td>
      <td>{input("name")}</td>
      <td>{input("category", { list: "item-groups", placeholder: UNCAT })}</td>
      <td>{input("pack_name")}</td>
      <td className="r num whitespace-nowrap">{input("pack_size", { right: true })}&nbsp;<span className="text-muted">{it.stock_unit}</span></td>
      <td className="r num">{input("case_size", { right: true })}</td>
      <td className="r num">{input("pack_cost", { right: true, placeholder: "" })}</td>
      <td className="r num">{input("par_level", { right: true, placeholder: "" })}</td>
      <td className="r num">{lastCount(it)}</td>
      <td><input type="checkbox" aria-label="Active" checked={active} onChange={(e) => setActive(e.target.checked)} /></td>
      <td className="r whitespace-nowrap">
        <Button onClick={save} disabled={!f.sku.trim() || !f.name.trim()}>Save</Button>
        <button className="text-sm text-muted hover:text-ink underline ml-2" onClick={onCancel}>Cancel</button>
      </td>
    </tr>
  );
}
