import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type Area, type Level, type UserRow, dt } from "../lib/api";
import { useMe } from "../lib/auth";
import { Button, Empty, Field, Flash, Loading, Notice, Page, inputCls, useAsync } from "../lib/ui";

type Perms = Record<string, Level>;
type Form = { username: string; display_name: string; password: string; is_admin: boolean; permissions: Perms };
const blank: Form = { username: "", display_name: "", password: "", is_admin: false, permissions: {} };

/** Starting points; every box can still be changed after picking one. */
const PRESETS: { label: string; perms: (areas: Area[]) => Perms }[] = [
  { label: "Bartender", perms: () => ({ counts: "edit", deliveries: "edit" }) },
  { label: "Manager", perms: (areas) => Object.fromEntries(areas.map((a) => [a.key, a.levels[a.levels.length - 1]])) },
  { label: "Read only", perms: (areas) => Object.fromEntries(areas.filter((a) => a.levels.includes("view")).map((a) => [a.key, "view" as Level])) },
];

export default function Users() {
  const me = useMe();
  const users = useAsync(() => api.get<UserRow[]>("/api/users"));
  const areas = useAsync(() => api.get<Area[]>("/api/auth/areas"));
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const fail = (e: unknown) => setMsg({ kind: "error", text: (e as Error).message });
  const list = users.data ?? [];
  const areaList = areas.data ?? [];

  const add = async (f: Form) => {
    try {
      await api.post("/api/users", { ...f, username: f.username.trim(), display_name: f.display_name.trim() });
      setMsg({ kind: "ok", text: `Added ${f.display_name.trim()}. They can log in as ${f.username.trim().toLowerCase()}.` }); setAdding(false); users.reload();
    } catch (e) { fail(e); }
  };
  const save = async (u: UserRow, f: Form) => {
    try {
      await api.patch(`/api/users/${u.id}`, { username: f.username.trim(), display_name: f.display_name.trim(), is_admin: f.is_admin, permissions: f.permissions });
      setMsg({ kind: "ok", text: `Saved ${f.display_name.trim()}. Changes apply on their next click.` }); setEditing(null); users.reload();
    } catch (e) { fail(e); }
  };
  const unlock = async (u: UserRow) => {
    try { await api.post(`/api/users/${u.id}/unlock`); setMsg({ kind: "ok", text: `${u.display_name} can try logging in again.` }); users.reload(); } catch (e) { fail(e); }
  };
  const setPin = async (u: UserRow, pin: string | null) => {
    try {
      if (pin) await api.post(`/api/users/${u.id}/pin`, { pin }); else await api.del(`/api/users/${u.id}/pin`);
      setMsg({ kind: "ok", text: pin ? `PIN set for ${u.display_name}.` : `PIN removed for ${u.display_name}.` }); users.reload();
    } catch (e) { fail(e); }
  };
  const act = async (u: UserRow, what: "activate" | "deactivate" | "logout") => {
    if (what === "deactivate" && !confirm(`Switch off ${u.display_name}? They are logged out and can't log in until switched back on. Their history stays.`)) return;
    try {
      if (what === "logout") await api.post(`/api/users/${u.id}/logout`);
      else await api.patch(`/api/users/${u.id}`, { is_active: what === "activate" });
      setMsg({ kind: "ok", text: what === "logout" ? `${u.display_name} is logged out on every device.` : what === "activate" ? `${u.display_name} can log in again.` : `${u.display_name} is switched off.` });
      users.reload();
    } catch (e) { fail(e); }
  };
  const setPassword = async (u: UserRow, pw: string) => {
    try { await api.post(`/api/users/${u.id}/password`, { password: pw }); setMsg({ kind: "ok", text: `New password set for ${u.display_name}. They are logged out everywhere.` }); users.reload(); }
    catch (e) { fail(e); }
  };

  return (
    <Page title="Users" sub="Who can log in, and what each person can open or change" actions={<>
      <Link className="text-sm underline" to="/audit">Audit log</Link>
      <Button onClick={() => { setAdding((v) => !v); setEditing(null); }}>{adding ? "Cancel" : "Add user"}</Button>
    </>}>
      {msg && <Flash kind={msg.kind} onClose={() => setMsg(null)}>{msg.text}</Flash>}
      {users.loading && !users.data && !users.error && <Loading />}
      {users.error && <Notice kind="error">{users.error}</Notice>}
      {adding && areas.data && <UserForm areas={areaList} initial={blank} isNew onSave={add} onCancel={() => setAdding(false)} />}
      {users.data && list.length === 0 && <Empty>No users.</Empty>}
      {list.length > 0 && (
        <div className="card table-box px-2"><table className="grid">
          <thead><tr><th>Name</th><th>Access</th><th>Last login</th><th>Status</th><th /></tr></thead>
          <tbody>{list.map((u) => editing === u.id ? (
            <tr key={u.id}><td colSpan={5} className="!p-0">
              <UserForm areas={areaList} self={u.id === me?.id} initial={{ username: u.username, display_name: u.display_name, password: "", is_admin: u.is_admin, permissions: u.permissions }}
                onSave={(f) => save(u, f)} onCancel={() => setEditing(null)} onPassword={(pw) => setPassword(u, pw)} hasPin={u.has_pin} onPin={(pin) => setPin(u, pin)} />
            </td></tr>
          ) : (
            <tr key={u.id} className={u.is_active ? "" : "opacity-60"}>
              <td><div className="font-medium">{u.display_name}{u.id === me?.id && <span className="text-xs text-faint"> · you</span>}</div><div className="mono text-xs text-muted">{u.username}</div></td>
              <td className="text-sm max-w-md">{summary(u, areaList)}</td>
              <td className="text-sm text-muted whitespace-nowrap">{u.last_login_at ? dt(u.last_login_at) : "never"}</td>
              <td className="text-sm whitespace-nowrap">{u.is_active ? <span className="text-ok">Active</span> : <span className="text-muted">Switched off</span>}
                {u.has_pin && <span className="text-muted"> · PIN</span>}
                {u.locked && <div><span className="text-danger">Locked</span> <button className="text-accent hover:underline ml-1" onClick={() => unlock(u)}>Unlock</button></div>}</td>
              <td className="r whitespace-nowrap">
                <Link className="text-sm text-muted hover:text-ink underline" to={`/audit?user=${u.id}`}>Activity</Link>
                <button className="text-sm text-muted hover:text-ink underline ml-3" aria-label={`Edit ${u.display_name}`} onClick={() => { setEditing(u.id); setAdding(false); }}>Edit</button>
                {u.id !== me?.id && (u.is_active
                  ? <><button className="text-sm text-muted hover:text-ink underline ml-3" onClick={() => act(u, "logout")}>Log out</button>
                      <button className="text-sm text-danger hover:underline ml-3" onClick={() => act(u, "deactivate")}>Switch off</button></>
                  : <button className="text-sm text-accent hover:underline ml-3" onClick={() => act(u, "activate")}>Switch on</button>)}
              </td>
            </tr>))}
          </tbody>
        </table></div>
      )}
      <p className="text-sm text-muted mt-4 max-w-3xl">Admins can do everything, including managing users. Everyone else gets a level per area: <b>view</b> opens the page,
        <b> edit</b> also lets them change things there. A PIN logs in to counts and deliveries only, whatever else the person can do. The audit log records every change, login and refused attempt with who made it.</p>
    </Page>
  );
}

function summary(u: UserRow, areas: Area[]) {
  if (u.is_admin) return <span className="text-accent font-medium">Admin · everything</span>;
  const label = (k: string) => areas.find((a) => a.key === k)?.label ?? k;
  const by = (lv: Level) => Object.entries(u.permissions).filter(([, v]) => v === lv).map(([k]) => label(k));
  const edit = by("edit"), view = by("view");
  if (!edit.length && !view.length) return <span className="text-faint">No access</span>;
  return <>{edit.length > 0 && <div><span className="text-muted">Edit:</span> {edit.join(", ")}</div>}{view.length > 0 && <div><span className="text-muted">View:</span> {view.join(", ")}</div>}</>;
}

function UserForm({ areas, initial, isNew, self, onSave, onCancel, onPassword, hasPin, onPin }: {
  areas: Area[]; initial: Form; isNew?: boolean; self?: boolean; onSave: (f: Form) => void; onCancel: () => void; onPassword?: (pw: string) => void;
  hasPin?: boolean; onPin?: (pin: string | null) => void;
}) {
  const [f, setF] = useState<Form>(initial);
  const [pw, setPw] = useState("");
  const [pin, setPin] = useState("");
  const setLevel = (area: string, lv: Level | "none") => {
    const p = { ...f.permissions };
    if (lv === "none") delete p[area]; else p[area] = lv;
    setF({ ...f, permissions: p });
  };
  const ok = f.username.trim().length >= 2 && f.display_name.trim() && (!isNew || f.password.length >= 8);
  return (
    <div className="card p-5 mb-6 bg-raised/30">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
        <Field label="Name"><input className={inputCls} value={f.display_name} autoFocus onChange={(e) => setF({ ...f, display_name: e.target.value })} /></Field>
        <Field label="Username" hint="letters, numbers, . _ - @"><input className={`${inputCls} mono`} autoCapitalize="none" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></Field>
        {isNew && <Field label="Password" hint="at least 8 characters"><input className={inputCls} type="password" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></Field>}
      </div>
      <label className={`flex items-center gap-2 text-sm mb-4 ${self ? "opacity-60" : ""}`}>
        <input type="checkbox" checked={f.is_admin} disabled={self} onChange={(e) => setF({ ...f, is_admin: e.target.checked })} />
        <span><b>Admin</b> <span className="text-muted">— everything, including users and the audit log{self ? " (you can't remove your own admin)" : ""}</span></span>
      </label>
      {!f.is_admin && (
        <fieldset className="mb-4">
          <div className="flex flex-wrap items-center gap-2 mb-2">
            <legend className="text-sm text-muted mr-2">Access by area</legend>
            {PRESETS.map((p) => <button key={p.label} type="button" className="text-xs px-2 py-1 rounded-md border border-line hover:bg-raised" onClick={() => setF({ ...f, permissions: p.perms(areas) })}>{p.label}</button>)}
            <button type="button" className="text-xs px-2 py-1 rounded-md border border-line hover:bg-raised" onClick={() => setF({ ...f, permissions: {} })}>None</button>
          </div>
          <div className="table-box"><table className="grid">
            <thead><tr><th>Area</th><th>No access</th><th>View</th><th>Edit</th></tr></thead>
            <tbody>{areas.map((a) => {
              const cur = f.permissions[a.key] ?? "none";
              return (
                <tr key={a.key}>
                  <td>{a.label}</td>
                  {(["none", "view", "edit"] as const).map((lv) => (
                    <td key={lv}>{lv === "none" || a.levels.includes(lv)
                      ? <input type="radio" name={`perm-${a.key}`} aria-label={`${a.label}: ${lv}`} checked={cur === lv} onChange={() => setLevel(a.key, lv)} />
                      : <span className="text-faint">—</span>}</td>))}
                </tr>);
            })}</tbody>
          </table></div>
        </fieldset>)}
      <div className="flex flex-wrap items-end gap-3">
        <Button onClick={() => onSave(f)} disabled={!ok}>{isNew ? "Add user" : "Save"}</Button>
        <Button kind="quiet" onClick={onCancel}>Cancel</Button>
        {onPassword && (
          <form className="ml-auto flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); if (pw.length >= 8) { onPassword(pw); setPw(""); } }}>
            <Field label="Set a new password"><input className={inputCls} type="password" autoComplete="new-password" placeholder="at least 8 characters" value={pw} onChange={(e) => setPw(e.target.value)} /></Field>
            <Button type="submit" kind="quiet" disabled={pw.length < 8}>Set password</Button>
          </form>)}
        {onPin && (
          <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); if (pin.length >= 4) { onPin(pin); setPin(""); } }}>
            <Field label={hasPin ? "Change PIN" : "Set a PIN"}><input className={`${inputCls} mono w-28`} type="password" inputMode="numeric" autoComplete="off" placeholder="4–8 digits" value={pin} onChange={(e) => setPin(e.target.value)} /></Field>
            <Button type="submit" kind="quiet" disabled={pin.length < 4}>Set PIN</Button>
            {hasPin && <Button type="button" kind="quiet" onClick={() => onPin(null)}>Remove PIN</Button>}
          </form>)}
      </div>
    </div>
  );
}
