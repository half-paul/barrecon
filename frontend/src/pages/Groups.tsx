import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Group, n } from "../lib/api";
import { useMe, useReadOnly } from "../lib/auth";
import { Button, Empty, Field, Flash, inputCls, Loading, Nil, Notice, Page, useAsync } from "../lib/ui";

type Units = { code: string; dimension: string }[];
type Form = { name: string; stock_unit: string; pack_name: string; pack_size: string; case_size: string; sheet_in_cases: boolean };
const blank: Form = { name: "", stock_unit: "ml", pack_name: "bottle", pack_size: "750", case_size: "12", sheet_in_cases: false };
// food comes by weight in bags, boxes and tubs, not 750 ml bottles: the pack a new group starts with once its unit is a weight
const FOOD_PACK = { pack_name: "bag", pack_size: "1", case_size: "1" };
const FOOD_PACKS = ["bag", "box", "case", "tub", "tray", "jar", "can", "pail", "block"];
const enc = encodeURIComponent;
const trim = (v: string) => v.replace(/\.?0+$/, "");

export default function Groups() {
  const viewOnly = useReadOnly("items");
  const food = !!useMe()?.business?.track_food;  // food is stock here too: weights and food packs are offered
  const groups = useAsync(() => api.get<Group[]>("/api/groups"));
  const units = useAsync(() => api.get<Units>("/api/items/units"));
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState(blank);
  const [editing, setEditing] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const fail = (e: unknown) => setMsg({ kind: "error", text: (e as Error).message });

  const add = async () => {
    try {
      await api.post("/api/groups", { ...form, name: form.name.trim(), pack_name: form.pack_name.trim() || null });
      setMsg({ kind: "ok", text: `Added ${form.name.trim()}.` }); setAdding(false); setForm(blank); groups.reload();
    } catch (e) { fail(e); }
  };
  const save = async (g: Group, f: Form) => {
    const c: Record<string, string | boolean | null> = {};
    if (f.name.trim() !== g.name) c.name = f.name.trim();
    if (f.stock_unit !== g.stock_unit) c.stock_unit = f.stock_unit;
    if (f.pack_name.trim() !== (g.pack_name ?? "")) c.pack_name = f.pack_name.trim() || null;
    if (Number(f.pack_size) !== Number(g.pack_size)) c.pack_size = f.pack_size;
    if (Number(f.case_size) !== Number(g.case_size)) c.case_size = f.case_size;
    if (f.sheet_in_cases !== g.sheet_in_cases) c.sheet_in_cases = f.sheet_in_cases;
    try {
      if (Object.keys(c).length) await api.patch(`/api/groups/${enc(g.name)}`, c);
      setEditing(null); setMsg(c.name ? { kind: "ok", text: `Renamed ${g.name} to ${c.name}; its ${g.item_count} items moved with it.` } : null); groups.reload();
    } catch (e) { fail(e); }
  };
  const apply = async (g: Group) => {
    if (!confirm(`Set ${g.differing} item${g.differing === 1 ? "" : "s"} in ${g.name} to ${pack(g)}?`)) return;
    try {
      const r = await api.post<{ updated: number; skipped_unit: string[] }>(`/api/groups/${enc(g.name)}/apply`, { fields: ["stock_unit", "pack_name", "pack_size", "case_size"] });
      setMsg({ kind: "ok", text: `Updated ${r.updated} item${r.updated === 1 ? "" : "s"}.${r.skipped_unit.length
        ? ` Kept the counted unit of ${r.skipped_unit.join(", ")}: they already have counts or deliveries recorded in it.` : ""}` });
      groups.reload();
    } catch (e) { fail(e); }
  };
  const remove = async (g: Group, moveTo: string) => {
    try {
      await api.del(`/api/groups/${enc(g.name)}${moveTo ? `?move_to=${enc(moveTo)}` : ""}`);
      setMsg({ kind: "ok", text: `Removed ${g.name}.${g.item_count ? ` Its ${g.item_count} items moved to ${moveTo || "Uncategorised"}.` : ""}` });
      setRemoving(null); groups.reload();
    } catch (e) { fail(e); }
  };

  const unitOptions = (units.data ?? []).map((u) => <option key={u.code} value={u.code}>{u.code}</option>);
  const isMass = (code: string) => units.data?.find((u) => u.code === code)?.dimension === "mass";
  // a form still on the bottle it opened with takes a food pack when its unit becomes a weight, and the bottle back when it leaves
  const setUnit = (stock_unit: string) => setForm((f) => {
    const untouched = (p: { pack_name: string; pack_size: string; case_size: string }) => f.pack_name === p.pack_name && f.pack_size === p.pack_size && f.case_size === p.case_size;
    if (food && isMass(stock_unit) && untouched(blank)) return { ...f, stock_unit, ...FOOD_PACK };
    if (!isMass(stock_unit) && untouched(FOOD_PACK)) return { ...f, stock_unit, pack_name: blank.pack_name, pack_size: blank.pack_size, case_size: blank.case_size };
    return { ...f, stock_unit };
  });
  const list = groups.data ?? [];
  return (
    <Page area="items" title="Item groups" sub={food ? "Group defaults for new items: counted unit and pack, for drinks and food" : "Group defaults for new items: counted unit and pack"} actions={<>
      <Link className="text-sm underline" to="/items">Stock items</Link>
      <Button onClick={() => setAdding((v) => !v)}>{adding ? "Cancel" : "Add group"}</Button>
    </>}>
      {msg && <Flash kind={msg.kind} onClose={() => setMsg(null)}>{msg.text}</Flash>}
      {groups.loading && !groups.data && !groups.error && <Loading />}
      {groups.error && <Notice kind="error">{groups.error}</Notice>}
      {adding && (
        <div className="card grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3 mb-6 p-5">
          <Field label="Name"><input className={inputCls} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          <Field label="Counted in" hint={food ? "ml for liquids, each for cans and bottles of beer, kg, lb or g for food" : "ml for liquids, each for cans and bottles of beer"}><select className={inputCls} value={form.stock_unit} onChange={(e) => setUnit(e.target.value)}>{unitOptions}</select></Field>
          <Field label="Pack name" hint={food ? "bottle, keg; bag, box or tub for food" : undefined}><input className={inputCls} list={food ? "food-packs" : undefined} value={form.pack_name} onChange={(e) => setForm({ ...form, pack_name: e.target.value })} /></Field>
          <Field label="Pack size (in counted unit)" hint={food ? "750 for a 750 ml bottle; 1 for a can; 2.5 for a 2.5 kg bag" : "750 for a 750 ml bottle; 1 for a can"}><input className={`${inputCls} num`} inputMode="decimal" value={form.pack_size} onChange={(e) => setForm({ ...form, pack_size: e.target.value })} /></Field>
          <Field label="Packs per case / flat" hint={food ? "1 when a bag or box comes on its own" : undefined}><input className={`${inputCls} num`} inputMode="decimal" value={form.case_size} onChange={(e) => setForm({ ...form, case_size: e.target.value })} /></Field>
          <Field label="Deliveries sheet" hint="packs column counts cases of this group"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.sheet_in_cases} onChange={(e) => setForm({ ...form, sheet_in_cases: e.target.checked })} /> Sheet counts cases</label></Field>
          <div className="col-span-full"><Button onClick={add} disabled={!form.name.trim() || !(Number(form.pack_size) > 0)}>Save group</Button></div>
        </div>
      )}
      {food && <datalist id="food-packs">{FOOD_PACKS.map((p) => <option key={p} value={p} />)}</datalist>}
      {groups.data && list.length === 0 && <Empty>No groups yet. Add one, or give items a group on Stock items.</Empty>}
      {list.length > 0 && (
        <div className="table-box"><table className="grid">
          <thead><tr><th>Group</th><th>Counted in</th><th>Pack</th><th className="r">Pack size</th><th className="r">Per case</th><th>Sheet counts</th><th className="r">Items</th><th>Items that differ</th><th /></tr></thead>
          <tbody>{list.map((g) => editing === g.name
            ? <EditRow key={g.name} g={g} food={food} unitOptions={unitOptions} onSave={(f) => save(g, f)} onCancel={() => setEditing(null)} />
            : (<Fragment key={g.name}>
              <tr>
                <td className="font-medium">{g.name}</td>
                <td>{g.stock_unit}</td>
                <td>{g.pack_name || <Nil />}</td>
                <td className="r num">{n(g.pack_size, 3)} <span className="text-muted">{g.stock_unit}</span></td>
                <td className="r num">{n(g.case_size, 3)}</td>
                <td>{g.sheet_in_cases ? "cases" : <span className="text-faint">{g.pack_name || "packs"}</span>}</td>
                <td className="r num"><Link className="underline" to={`/items?status=all&cat=${enc(g.name)}`}>{g.item_count}</Link>{g.active_count < g.item_count && <span className="text-faint text-xs"> · {g.active_count} active</span>}</td>
                <td>{g.differing && !viewOnly ? <button className="text-sm text-accent hover:underline" onClick={() => apply(g)}>{g.differing}: set to group pack</button> : <span className="text-faint">none</span>}</td>
                <td className="r whitespace-nowrap" hidden={viewOnly}>
                  <button className="text-sm text-muted hover:text-ink underline" aria-label={`Edit ${g.name}`} onClick={() => { setEditing(g.name); setRemoving(null); }}>Edit</button>
                  <button className="text-sm text-danger hover:underline ml-3" aria-label={`Remove ${g.name}`} onClick={() => setRemoving(removing === g.name ? null : g.name)}>Remove</button>
                </td>
              </tr>
              {removing === g.name && <RemoveRow g={g} others={list.filter((o) => o.name !== g.name)} onRemove={(to) => remove(g, to)} onCancel={() => setRemoving(null)} />}
            </Fragment>))}
          </tbody>
        </table></div>
      )}
      <p className="text-sm text-muted mt-4 max-w-3xl">Changing a group’s pack changes the defaults for new items. Existing items keep their own pack until you use
        “set to group pack”. An item that already has counts or deliveries keeps its counted unit, because those are stored in it.
        With “sheet counts cases”, the packs number on an uploaded deliveries sheet is cases: 2 of a 12-bottle case adds 24 bottles. pack_cost stays per bottle.{food && " Food counted by weight: the pack size is the weight of one bag, box or tub in the counted unit, and can be a fraction (2.5 kg, 0.5 lb)."}</p>
    </Page>
  );
}

const pack = (g: Group) => `${g.pack_name || "pack"} of ${n(g.pack_size, 3)} ${g.stock_unit}, ${n(g.case_size, 3)} per case`;

function EditRow({ g, food, unitOptions, onSave, onCancel }: { g: Group; food: boolean; unitOptions: React.ReactNode; onSave: (f: Form) => void; onCancel: () => void }) {
  const [f, setF] = useState<Form>({ name: g.name, stock_unit: g.stock_unit, pack_name: g.pack_name ?? "", pack_size: trim(g.pack_size), case_size: trim(g.case_size), sheet_in_cases: g.sheet_in_cases });
  const ok = f.name.trim() && Number(f.pack_size) > 0 && Number(f.case_size) > 0;
  return (
    <tr className="bg-raised/40" onKeyDown={(e) => { if (e.key === "Enter" && ok) onSave(f); if (e.key === "Escape") onCancel(); }}>
      <td><input className="cell-input text-left w-44" aria-label="Group name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></td>
      <td><select className="cell-input text-left w-24" aria-label="Counted in" value={f.stock_unit} onChange={(e) => setF({ ...f, stock_unit: e.target.value })}>{unitOptions}</select></td>
      <td><input className="cell-input text-left w-36" aria-label="Pack name" list={food ? "food-packs" : undefined} value={f.pack_name} onChange={(e) => setF({ ...f, pack_name: e.target.value })} /></td>
      <td className="r num"><input className="cell-input" inputMode="decimal" aria-label="Pack size" value={f.pack_size} onChange={(e) => setF({ ...f, pack_size: e.target.value })} /> <span className="text-muted">{f.stock_unit}</span></td>
      <td className="r num"><input className="cell-input" inputMode="decimal" aria-label="Per case" value={f.case_size} onChange={(e) => setF({ ...f, case_size: e.target.value })} /></td>
      <td><label className="flex items-center gap-1 text-sm"><input type="checkbox" aria-label="Sheet counts cases" checked={f.sheet_in_cases} onChange={(e) => setF({ ...f, sheet_in_cases: e.target.checked })} /> cases</label></td>
      <td className="r num">{g.item_count}</td>
      <td className="text-xs text-muted">{f.name.trim() !== g.name && g.item_count ? `${g.item_count} items move to the new name` : ""}</td>
      <td className="r whitespace-nowrap">
        <Button onClick={() => onSave(f)} disabled={!ok}>Save</Button>
        <button className="text-sm text-muted hover:text-ink underline ml-2" onClick={onCancel}>Cancel</button>
      </td>
    </tr>
  );
}

function RemoveRow({ g, others, onRemove, onCancel }: { g: Group; others: Group[]; onRemove: (moveTo: string) => void; onCancel: () => void }) {
  const [to, setTo] = useState("");
  return (
    <tr className="bg-danger-soft/40"><td colSpan={9}>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        {g.item_count ? <>
          <span>Remove {g.name}. Move its {g.item_count} item{g.item_count === 1 ? "" : "s"} to</span>
          <select className={inputCls} aria-label="Move items to" value={to} onChange={(e) => setTo(e.target.value)}>
            <option value="">Uncategorised</option>
            {others.map((o) => <option key={o.name} value={o.name}>{o.name}</option>)}
          </select>
        </> : <span>Remove {g.name}? It has no items.</span>}
        <Button kind="danger" onClick={() => onRemove(to)}>Remove group</Button>
        <button className="text-muted hover:text-ink underline" onClick={onCancel}>Cancel</button>
      </div>
    </td></tr>
  );
}
