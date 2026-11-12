import { useState } from "react";
import { api, type Area, type BusinessAdmin, type Level, type Member, logoUrl } from "../lib/api";
import { useMe } from "../lib/auth";
import { BusinessMark, switchBusiness } from "../lib/business";
import { Button, Empty, Field, FileButton, Flash, inputCls, Loading, Nil, Notice, Page, useAsync } from "../lib/ui";

type Form = { name: string; kind: string; address: string; city: string; phone: string; email: string; website: string;
  swype_email: string; swype_password: string; swype_company: string; swype_store_id: string; copy_from: string; copy_recipes: boolean };
const blank: Form = { name: "", kind: "bar", address: "", city: "", phone: "", email: "", website: "",
  swype_email: "", swype_password: "", swype_company: "", swype_store_id: "", copy_from: "", copy_recipes: false };
const KIND: Record<string, string> = { bar: "Bar", restaurant: "Restaurant", cafe: "Café", other: "Other" };
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const fromBusiness = (b: BusinessAdmin): Form => ({ ...blank, name: b.name, kind: b.kind ?? "", address: b.address ?? "", city: b.city ?? "", phone: b.phone ?? "",
  email: b.email ?? "", website: b.website ?? "", swype_email: b.swype_email ?? "", swype_company: b.swype_company ?? "",
  swype_store_id: b.swype_store_id == null ? "" : String(b.swype_store_id) });

/** `first`: a new tenant with no business yet; the page is all there is until one is added. */
export default function Businesses({ first }: { first?: boolean }) {
  const me = useMe();
  const list = useAsync(() => api.get<BusinessAdmin[]>("/api/businesses"));
  const areas = useAsync(() => api.get<Area[]>("/api/auth/areas"));
  const [adding, setAdding] = useState(!!first);
  const [open, setOpen] = useState<{ id: string; tab: "details" | "people" } | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const fail = (e: unknown) => setMsg({ kind: "error", text: (e as Error).message });
  const rows = list.data ?? [];
  const here = me?.business?.id;

  const body = (f: Form, isNew: boolean) => ({
    name: f.name.trim(), kind: f.kind || null, address: f.address.trim(), city: f.city.trim(), phone: f.phone.trim(), email: f.email.trim(), website: f.website.trim(),
    swype_email: f.swype_email.trim(), swype_company: f.swype_company.trim(), swype_store_id: f.swype_store_id === "" ? null : Number(f.swype_store_id),
    ...(f.swype_password ? { swype_password: f.swype_password } : {}),
    ...(isNew && f.copy_from ? { copy_from: f.copy_from, copy_what: f.copy_recipes ? ["recipes"] : [] } : {}),
  });
  const add = async (f: Form) => {
    try {
      const b = await api.post<BusinessAdmin>("/api/businesses", body(f, true));
      if (first) { await switchBusiness(b.id); return; }  // their first business: start working in it
      const c = b.copied;
      setMsg({ kind: "ok", text: `Added ${b.name}.${c ? ` Copied ${c.items} stock items, ${c.groups} groups${c.recipes ? ` and ${c.recipes} recipes` : ""}.` : ""} Give people access to it under People.` });
      setAdding(false); list.reload();
    } catch (e) { fail(e); }
  };
  const save = async (b: BusinessAdmin, f: Form) => {
    try {
      await api.patch(`/api/businesses/${b.id}`, body(f, false));
      if (b.id === here) { window.location.reload(); return; }  // the header shows this one's name
      setMsg({ kind: "ok", text: `Saved ${f.name.trim()}.` }); setOpen(null); list.reload();
    } catch (e) { fail(e); }
  };
  const setActive = async (b: BusinessAdmin, on: boolean) => {
    if (!on && !confirm(`Switch off ${b.name}? Nobody can work in it until it is switched back on. Its counts, deliveries and history stay.`)) return;
    try {
      await api.patch(`/api/businesses/${b.id}`, { is_active: on });
      if (b.id === here) { window.location.assign("/"); return; }
      setMsg({ kind: "ok", text: on ? `${b.name} is back on.` : `${b.name} is switched off.` }); list.reload();
    } catch (e) { fail(e); }
  };
  const logo = async (b: BusinessAdmin, file: File | null) => {
    try {
      if (file) await api.upload(`/api/businesses/${b.id}/logo`, file, "PUT"); else await api.del(`/api/businesses/${b.id}/logo`);
      if (b.id === here) { window.location.reload(); return; }
      setMsg({ kind: "ok", text: file ? `Logo saved for ${b.name}.` : `Logo removed from ${b.name}.` }); list.reload();
    } catch (e) { fail(e); }
  };

  return (
    <Page title={first ? "Add your first business" : "Businesses"}
      sub={first ? "A business is a bar, restaurant or café. Its stock, counts, deliveries and sales are kept apart from your other businesses."
        : `${me?.tenant?.name ?? "Your account"}: each business has its own stock, counts, deliveries, sales and settings`}
      actions={!first && <Button onClick={() => { setAdding((v) => !v); setOpen(null); }}>{adding ? "Cancel" : "Add business"}</Button>}>
      {msg && <Flash kind={msg.kind} onClose={() => setMsg(null)}>{msg.text}</Flash>}
      {list.loading && !list.data && !list.error && <Loading />}
      {list.error && <Notice kind="error">{list.error}</Notice>}
      {adding && <BusinessForm initial={blank} isNew others={rows} onSave={add} onCancel={first ? undefined : () => setAdding(false)} />}
      {list.data && rows.length === 0 && !first && <Empty>No businesses yet.</Empty>}
      {rows.length > 0 && (
        <div className="flex flex-col gap-3">
          {rows.map((b) => (
            <section key={b.id} className={`card ${b.is_active ? "" : "opacity-70"}`}>
              <div className="p-4 sm:p-5 flex flex-wrap items-center gap-x-5 gap-y-3">
                <BusinessMark business={b} size={44} />
                <div className="min-w-0 flex-1 basis-56">
                  <h2 className="font-semibold flex flex-wrap items-center gap-2">{b.name}
                    {b.id === here && <span className="text-xs font-normal rounded-chip border border-line-strong px-2 py-0.5 text-muted">You are here</span>}
                    {!b.is_active && <span className="text-xs font-normal rounded-chip border border-line-strong px-2 py-0.5 text-muted">Switched off</span>}
                  </h2>
                  <div className="text-sm text-muted">{[b.kind ? KIND[b.kind] ?? b.kind : null, b.address, b.city].filter(Boolean).join(" · ") || "No address yet"}</div>
                  <div className="text-sm text-muted">{[b.phone, b.email, b.website].filter(Boolean).join(" · ")}</div>
                </div>
                <div className="text-sm text-muted">
                  <div>{b.members} {b.members === 1 ? "person" : "people"} with access, plus admins</div>
                  <div>{b.swype_email && b.swype_password_set ? "Swype login set" : "No Swype login"}</div>
                </div>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                  {b.is_active && b.id !== here && <button className="text-accent hover:underline" onClick={() => switchBusiness(b.id).catch(fail)}>Work here</button>}
                  <button className="text-muted hover:text-ink underline" aria-expanded={open?.id === b.id && open.tab === "details"}
                    onClick={() => { setOpen(open?.id === b.id && open.tab === "details" ? null : { id: b.id, tab: "details" }); setAdding(false); }}>Edit</button>
                  <button className="text-muted hover:text-ink underline" aria-expanded={open?.id === b.id && open.tab === "people"}
                    onClick={() => { setOpen(open?.id === b.id && open.tab === "people" ? null : { id: b.id, tab: "people" }); setAdding(false); }}>People</button>
                  {b.is_active ? <button className="text-danger hover:underline" onClick={() => setActive(b, false)}>Switch off</button>
                    : <button className="text-accent hover:underline" onClick={() => setActive(b, true)}>Switch on</button>}
                </div>
              </div>
              {open?.id === b.id && open.tab === "details" && (
                <div className="border-t border-line">
                  <div className="p-4 sm:p-5 flex flex-wrap items-center gap-4 border-b border-line">
                    <div className="w-40 h-20 grid place-items-center rounded-control border border-line bg-paper">
                      {logoUrl(b) ? <img src={logoUrl(b)!} alt={`${b.name} logo`} className="max-w-36 max-h-16 object-contain" /> : <span className="text-sm text-faint">No logo</span>}
                    </div>
                    <div className="flex flex-col gap-2">
                      <div className="text-sm text-muted max-w-md">Shown in the header of every page of this business. PNG, JPEG, WebP or GIF, up to 1 MB; a wide logo on a transparent background works best.</div>
                      <div className="flex gap-2">
                        <FileButton label={b.logo_at ? "Replace logo" : "Upload logo"} accept="image/png,image/jpeg,image/webp,image/gif" onFile={(f) => logo(b, f)} />
                        {b.logo_at && <Button kind="quiet" edit onClick={() => logo(b, null)}>Remove</Button>}
                      </div>
                    </div>
                  </div>
                  <BusinessForm initial={fromBusiness(b)} passwordSet={b.swype_password_set} onSave={(f) => save(b, f)} onCancel={() => setOpen(null)} />
                </div>)}
              {open?.id === b.id && open.tab === "people" && areas.data && <People business={b} areas={areas.data} onError={fail} onChanged={list.reload} />}
            </section>))}
        </div>)}
      {!first && <p className="text-sm text-muted mt-4 max-w-3xl">Admins can work in every business. Everyone else only sees the businesses they are given access to, with their own level per area in each one.
        Anyone with more than one business switches between them from the name at the top left. A business is never deleted: switching it off hides it and keeps its history.</p>}
    </Page>
  );
}

function BusinessForm({ initial, isNew, others = [], passwordSet, onSave, onCancel }: {
  initial: Form; isNew?: boolean; others?: BusinessAdmin[]; passwordSet?: boolean; onSave: (f: Form) => void; onCancel?: () => void }) {
  const [f, setF] = useState<Form>(initial);
  const set = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const missing = !f.name.trim() ? "Enter a name." : f.email.trim() && !EMAIL.test(f.email.trim()) ? "That email doesn’t look right. Fix it or leave it empty."
    : f.swype_store_id !== "" && !/^\d+$/.test(f.swype_store_id) ? "The Swype store number is digits only." : "";
  return (
    <form className={`p-4 sm:p-5 ${isNew ? "card mb-6 bg-raised/30" : ""}`} onSubmit={(e) => { e.preventDefault(); if (!missing) onSave(f); }}>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
        <Field label="Name"><input className={inputCls} value={f.name} autoFocus={isNew} onChange={set("name")} /></Field>
        <Field label="Kind"><select className={inputCls} value={f.kind} onChange={set("kind")}>
          <option value="">Not set</option>{Object.entries(KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
        <Field label="Phone"><input className={inputCls} type="tel" value={f.phone} onChange={set("phone")} /></Field>
        <div className="sm:col-span-2"><Field label="Address"><input className={inputCls} value={f.address} onChange={set("address")} /></Field></div>
        <Field label="City"><input className={inputCls} value={f.city} onChange={set("city")} /></Field>
        <Field label="Email"><input className={inputCls} type="email" autoCapitalize="none" value={f.email} onChange={set("email")} /></Field>
        <Field label="Website"><input className={inputCls} autoCapitalize="none" placeholder="example.com" value={f.website} onChange={set("website")} /></Field>
      </div>
      <fieldset className="mb-4">
        <legend className="text-sm font-medium mb-1">Swype back office login</legend>
        <p className="text-sm text-muted mb-2 max-w-3xl">Used to pull this business’s sales from SwypePOS. Leave empty to upload the sales report by hand. The password is stored encrypted and never shown again.</p>
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
          <Field label="Swype email"><input className={inputCls} type="email" autoCapitalize="none" autoComplete="off" value={f.swype_email} onChange={set("swype_email")} /></Field>
          <Field label="Swype password" hint={passwordSet ? "set; type a new one to change it" : undefined}>
            <input className={inputCls} type="password" autoComplete="new-password" placeholder={passwordSet ? "unchanged" : ""} value={f.swype_password} onChange={set("swype_password")} /></Field>
          <Field label="Company" hint="the name in the Swype address, e.g. caffebarney"><input className={`${inputCls} mono`} autoCapitalize="none" value={f.swype_company} onChange={set("swype_company")} /></Field>
          <Field label="Store number" hint="1 unless Swype shows several stores"><input className={`${inputCls} num`} inputMode="numeric" placeholder="1" value={f.swype_store_id} onChange={set("swype_store_id")} /></Field>
        </div>
      </fieldset>
      {isNew && others.length > 0 && (
        <fieldset className="mb-4">
          <legend className="text-sm font-medium mb-1">Start from another business</legend>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <select className={inputCls} aria-label="Copy stock items from" value={f.copy_from} onChange={set("copy_from")}>
              <option value="">Start empty</option>{others.map((o) => <option key={o.id} value={o.id}>Copy stock items and groups from {o.name}</option>)}</select>
            {f.copy_from && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.copy_recipes} onChange={(e) => setF({ ...f, copy_recipes: e.target.checked })} /> also its Swype items and recipes</label>}
          </div>
          <p className="text-sm text-muted mt-1">Counts, deliveries, sales and cost history are never copied. After that the two businesses change on their own.</p>
        </fieldset>)}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={!!missing}>{isNew ? "Add business" : "Save"}</Button>
        {onCancel && <Button type="button" kind="quiet" onClick={onCancel}>Cancel</Button>}
        {missing && <span className="text-sm text-muted">{missing}</span>}
      </div>
    </form>
  );
}

const PRESETS: { label: string; perms: (areas: Area[]) => Record<string, Level> }[] = [
  { label: "Bartender", perms: () => ({ counts: "edit", deliveries: "edit" }) },
  { label: "Manager", perms: (areas) => Object.fromEntries(areas.map((a) => [a.key, a.levels[a.levels.length - 1]])) },
  { label: "Read only", perms: (areas) => Object.fromEntries(areas.filter((a) => a.levels.includes("view")).map((a) => [a.key, "view" as Level])) },
];

/** Who may work in one business, and what they can do there. */
function People({ business, areas, onError, onChanged }: { business: BusinessAdmin; areas: Area[]; onError: (e: unknown) => void; onChanged: () => void }) {
  const members = useAsync(() => api.get<Member[]>(`/api/businesses/${business.id}/members`), [business.id]);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, Level>>({});
  const put = async (m: Member, perms: Record<string, Level>) => {
    try { await api.put(`/api/businesses/${business.id}/members/${m.user_id}`, { permissions: perms }); setEditing(null); members.reload(); onChanged(); } catch (e) { onError(e); }
  };
  const remove = async (m: Member) => {
    if (!confirm(`Take ${m.display_name} out of ${business.name}? They keep their login and any other businesses.`)) return;
    try { await api.del(`/api/businesses/${business.id}/members/${m.user_id}`); members.reload(); onChanged(); } catch (e) { onError(e); }
  };
  const label = (k: string) => areas.find((a) => a.key === k)?.label ?? k;
  const summary = (p: Record<string, Level>) => {
    const by = (lv: Level) => Object.entries(p).filter(([, v]) => v === lv).map(([k]) => label(k));
    const edit = by("edit"), view = by("view");
    if (!edit.length && !view.length) return <span className="text-faint">Can log in here, no areas yet</span>;
    return <>{edit.length > 0 && <div><span className="text-muted">Edit:</span> {edit.join(", ")}</div>}{view.length > 0 && <div><span className="text-muted">View:</span> {view.join(", ")}</div>}</>;
  };
  return (
    <div className="border-t border-line p-4 sm:p-5">
      {members.loading && !members.data && <Loading rows={3} />}
      {members.error && <Notice kind="error">{members.error}</Notice>}
      {members.data && (
        <div className="table-box"><table className="grid">
          <thead><tr><th>Person</th><th>Access to {business.name}</th><th /></tr></thead>
          <tbody>{members.data.filter((m) => m.is_active || m.member).map((m) => (
            <tr key={m.user_id} className={m.is_active ? "" : "opacity-60"}>
              <td className="whitespace-nowrap align-top"><div className="font-medium">{m.display_name}</div><div className="mono text-xs text-muted">{m.username}</div></td>
              <td className="text-sm">
                {m.is_admin ? <span className="text-accent font-medium">Admin · everything, in every business</span>
                  : editing === m.user_id ? (
                    <div>
                      <div className="flex flex-wrap items-center gap-2 mb-2">
                        {PRESETS.map((p) => <button key={p.label} type="button" className="text-xs px-2 py-1 rounded-md border border-line hover:bg-raised" onClick={() => setDraft(p.perms(areas))}>{p.label}</button>)}
                        <button type="button" className="text-xs px-2 py-1 rounded-md border border-line hover:bg-raised" onClick={() => setDraft({})}>None</button>
                      </div>
                      <div className="table-box"><table className="grid">
                        <thead><tr><th>Area</th><th>No access</th><th>View</th><th>Edit</th></tr></thead>
                        <tbody>{areas.map((a) => (
                          <tr key={a.key}><td>{a.label}</td>
                            {(["none", "view", "edit"] as const).map((lv) => (
                              <td key={lv}>{lv === "none" || a.levels.includes(lv)
                                ? <input type="radio" name={`m-${m.user_id}-${a.key}`} aria-label={`${a.label}: ${lv}`} checked={(draft[a.key] ?? "none") === lv}
                                    onChange={() => { const p = { ...draft }; if (lv === "none") delete p[a.key]; else p[a.key] = lv; setDraft(p); }} />
                                : <Nil />}</td>))}
                          </tr>))}</tbody>
                      </table></div>
                      <div className="flex gap-2 mt-3"><Button onClick={() => put(m, draft)}>{m.member ? "Save" : "Give access"}</Button><Button kind="quiet" onClick={() => setEditing(null)}>Cancel</Button></div>
                    </div>)
                  : m.member ? summary(m.permissions) : <span className="text-faint">No access to this business</span>}
              </td>
              <td className="r whitespace-nowrap align-top text-sm">
                {!m.is_admin && editing !== m.user_id && <>
                  <button className="text-muted hover:text-ink underline" onClick={() => { setEditing(m.user_id); setDraft(m.permissions); }}>{m.member ? "Change" : "Give access"}</button>
                  {m.member && <button className="text-danger hover:underline ml-3" onClick={() => remove(m)}>Remove</button>}
                </>}
              </td>
            </tr>))}
          </tbody>
        </table></div>)}
      <p className="text-sm text-muted mt-3">New people are added under Setup, Users; this list sets what each one can do in {business.name}.</p>
    </div>
  );
}
