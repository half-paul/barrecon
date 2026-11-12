import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type Area, type Business, type Level, type UserRow, dt } from "../lib/api";
import { useMe } from "../lib/auth";
import { Button, Empty, Field, Flash, inputCls, Loading, Nil, Notice, Page, useAsync } from "../lib/ui";

type Perms = Record<string, Level>;
/** access: business id -> levels there. A business that is not a key is one the person can’t enter. */
type Form = { username: string; display_name: string; email: string; password: string; is_admin: boolean; access: Record<string, Perms> };
const blank: Form = { username: "", display_name: "", email: "", password: "", is_admin: false, access: {} };
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

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
  const options = useAsync(() => api.get<{ email_enabled: boolean }>("/api/auth/options"));
  const emailOn = !!options.data?.email_enabled;
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const fail = (e: unknown) => setMsg({ kind: "error", text: (e as Error).message });
  const list = users.data ?? [];
  const areaList = areas.data ?? [];
  const businesses = me?.businesses ?? [];
  const here = me?.business?.id ?? "";

  const add = async (f: Form) => {
    try {
      const name = f.display_name.trim();
      const r = await api.post<UserRow>("/api/users", { ...f, access: f.is_admin ? {} : f.access, username: f.username.trim(), display_name: name, email: f.email.trim() || undefined, password: emailOn && f.email.trim() ? undefined : f.password });
      setMsg(r.invite_sent === false ? { kind: "error", text: `Added ${name}, but the invite email couldn’t be sent. Try Resend invite.` }
        : { kind: "ok", text: r.invite_sent ? `Invite sent to ${f.email.trim()}.` : `Added ${name}. They can log in as ${f.username.trim().toLowerCase()}.` });
      setAdding(false); users.reload();
    } catch (e) { fail(e); }
  };
  const resend = async (u: UserRow) => {
    try {
      const r = await api.post<UserRow>(`/api/users/${u.id}/invite`);
      setMsg(r.invite_sent ? { kind: "ok", text: `New invite sent to ${u.email}. The old link no longer works.` } : { kind: "error", text: "The invite email couldn’t be sent." });
    } catch (e) { fail(e); }
  };
  const resetMfa = async (u: UserRow) => {
    if (!confirm(`Reset two-step login for ${u.display_name}? Only do this once you’ve checked it’s really them. They’re logged out and set up a new authenticator at their next login.`)) return;
    try { await api.post(`/api/users/${u.id}/mfa/reset`); setMsg({ kind: "ok", text: `Two-step login reset for ${u.display_name}.` }); users.reload(); } catch (e) { fail(e); }
  };
  const save = async (u: UserRow, f: Form) => {
    try {
      await api.patch(`/api/users/${u.id}`, { username: f.username.trim(), display_name: f.display_name.trim(), email: f.email.trim(), is_admin: f.is_admin, access: f.access });
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
    if (what === "deactivate" && !confirm(`Switch off ${u.display_name}? They are logged out and can’t log in until switched back on. Their history stays.`)) return;
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
    <Page title="Users" sub={`Who can log in to ${me?.tenant?.name ?? "this account"}, and what each person can open or change in each business`} actions={<>
      <Link className="text-sm underline" to="/audit">Audit log</Link>
      <Button onClick={() => { setAdding((v) => !v); setEditing(null); }}>{adding ? "Cancel" : "Add user"}</Button>
    </>}>
      {msg && <Flash kind={msg.kind} onClose={() => setMsg(null)}>{msg.text}</Flash>}
      {users.loading && !users.data && !users.error && <Loading />}
      {users.error && <Notice kind="error">{users.error}</Notice>}
      {adding && areas.data && options.data && <UserForm areas={areaList} businesses={businesses} here={here} initial={{ ...blank, access: here ? { [here]: {} } : {} }} isNew emailOn={emailOn} onSave={add} onCancel={() => setAdding(false)} />}
      {users.data && list.length === 0 && <Empty>No users.</Empty>}
      {list.length > 0 && (
        <div className="table-box"><table className="grid">
          <thead><tr><th>Name</th><th>Access</th><th>Last login</th><th>Status</th><th /></tr></thead>
          <tbody>{list.map((u) => editing === u.id ? (
            <tr key={u.id}><td colSpan={5} className="!p-0">
              <UserForm areas={areaList} businesses={businesses} here={here} self={u.id === me?.id} initial={{ username: u.username, display_name: u.display_name, email: u.email ?? "", password: "", is_admin: u.is_admin, access: u.access }}
                onSave={(f) => save(u, f)} onCancel={() => setEditing(null)} onPassword={(pw) => setPassword(u, pw)} hasPin={u.has_pin} onPin={(pin) => setPin(u, pin)} />
            </td></tr>
          ) : (
            <tr key={u.id} className={u.is_active ? "" : "opacity-60"}>
              <td><div className="font-medium">{u.display_name}{u.id === me?.id && <span className="text-xs text-faint"> · you</span>}</div><div className="mono text-xs text-muted">{u.username}</div>{u.email && <div className="text-xs text-muted">{u.email}</div>}</td>
              <td className="text-sm max-w-md">{summary(u, areaList, businesses)}</td>
              <td className="text-sm text-muted whitespace-nowrap">{u.last_login_at ? dt(u.last_login_at) : "never"}</td>
              <td className="text-sm whitespace-nowrap">{u.is_active ? <span className="text-ok">Active</span> : <span className="text-muted">Switched off</span>}
                {u.has_pin && <span className="text-muted"> · PIN</span>}
                {u.mfa_enabled && <span className="text-muted"> · 2-step</span>}
                {u.invite_pending && <div><span className="text-muted">Invited</span> {u.email && emailOn && <button className="text-accent hover:underline ml-1" onClick={() => resend(u)}>Resend invite</button>}</div>}
                {u.locked && <div><span className="text-danger">Locked</span> <button className="text-accent hover:underline ml-1" onClick={() => unlock(u)}>Clear lock</button></div>}</td>
              <td className="r whitespace-nowrap">
                <Link className="text-sm text-muted hover:text-ink underline" to={`/audit?user=${u.id}`}>Activity</Link>
                <button className="text-sm text-muted hover:text-ink underline ml-3" aria-label={`Edit ${u.display_name}`} onClick={() => { setEditing(u.id); setAdding(false); }}>Edit</button>
                {u.mfa_enabled && u.id !== me?.id && <button className="text-sm text-muted hover:text-ink underline ml-3" onClick={() => resetMfa(u)}>Reset 2-step</button>}
                {u.id !== me?.id && (u.is_active
                  ? <><button className="text-sm text-muted hover:text-ink underline ml-3" onClick={() => act(u, "logout")}>Log out</button>
                      <button className="text-sm text-danger hover:underline ml-3" onClick={() => act(u, "deactivate")}>Switch off</button></>
                  : <button className="text-sm text-accent hover:underline ml-3" onClick={() => act(u, "activate")}>Switch on</button>)}
              </td>
            </tr>))}
          </tbody>
        </table></div>
      )}
      <p className="text-sm text-muted mt-4 max-w-3xl">Admins can do everything in every business, including managing users and businesses. Everyone else is given access business by business, with a level per area in each: <b>view</b> opens the page,
        <b> edit</b> also lets them change things there. A PIN logs in to counts and deliveries only, whatever else the person can do. Admins also need a code from an authenticator app when they log in with a password. The audit log records every change, login and refused attempt with who made it.</p>
    </Page>
  );
}

function summary(u: UserRow, areas: Area[], businesses: Business[]) {
  if (u.is_admin) return <span className="text-accent font-medium">Admin · everything{businesses.length > 1 ? ", every business" : ""}</span>;
  const label = (k: string) => areas.find((a) => a.key === k)?.label ?? k;
  const levels = (p: Perms) => {
    const by = (lv: Level) => Object.entries(p).filter(([, v]) => v === lv).map(([k]) => label(k));
    const edit = by("edit"), view = by("view");
    if (!edit.length && !view.length) return <span className="text-faint">no areas yet</span>;
    return <>{edit.length > 0 && <span><span className="text-muted">Edit:</span> {edit.join(", ")}</span>}{edit.length > 0 && view.length > 0 && " · "}{view.length > 0 && <span><span className="text-muted">View:</span> {view.join(", ")}</span>}</>;
  };
  const mine = businesses.filter((b) => u.access[b.id]);
  if (!mine.length) return <span className="text-faint">No access to any business</span>;
  // with one business its name adds nothing; with several, each gets its own line
  return <>{mine.map((b) => <div key={b.id}>{businesses.length > 1 && <span className="font-medium">{b.name}: </span>}{levels(u.access[b.id])}</div>)}</>;
}

function UserForm({ areas, businesses, here, initial, isNew, emailOn, self, onSave, onCancel, onPassword, hasPin, onPin }: {
  areas: Area[]; businesses: Business[]; here: string; initial: Form; isNew?: boolean; emailOn?: boolean; self?: boolean; onSave: (f: Form) => void; onCancel: () => void; onPassword?: (pw: string) => void;
  hasPin?: boolean; onPin?: (pin: string | null) => void;
}) {
  const [f, setF] = useState<Form>(initial);
  const [pw, setPw] = useState("");
  const [pin, setPin] = useState("");
  const [biz, setBiz] = useState(here || businesses[0]?.id || "");  // the business whose levels are being set
  const member = biz in f.access;
  const perms: Perms = f.access[biz] ?? {};
  const setPerms = (p: Perms) => setF({ ...f, access: { ...f.access, [biz]: p } });
  const setMember = (on: boolean) => { const a = { ...f.access }; if (on) a[biz] = a[biz] ?? {}; else delete a[biz]; setF({ ...f, access: a }); };
  const setLevel = (area: string, lv: Level | "none") => {
    const p = { ...perms };
    if (lv === "none") delete p[area]; else p[area] = lv;
    setPerms(p);
  };
  const bizName = businesses.find((b) => b.id === biz)?.name ?? "this business";
  const email = f.email.trim();
  const invite = !!(isNew && emailOn && email);  // email is optional: without one, set a password for them
  const missing = !f.display_name.trim() ? "Enter a name."
    : f.username.trim().length < 2 ? "Enter a username (2 or more characters)."
    : email && !EMAIL.test(email) ? "That email doesn’t look right. Fix it or leave it empty."
    : isNew && !invite && f.password.length < 8 ? (emailOn ? "Enter an email to send an invite, or a password (8 or more characters)." : "Enter a password (8 or more characters).")
    : "";
  const ok = !missing;
  return (
    <div className="card p-5 mb-6 bg-raised/30">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
        <Field label="Name"><input className={inputCls} value={f.display_name} autoFocus onChange={(e) => setF({ ...f, display_name: e.target.value })} /></Field>
        <Field label="Username" hint="letters, numbers, . _ - @"><input className={`${inputCls} mono`} autoCapitalize="none" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></Field>
        <Field label="Email (optional)" hint={isNew && emailOn ? "they get a link to choose their password; leave empty to set one" : "for password resets"}><input className={inputCls} type="email" autoCapitalize="none" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
        {isNew && !invite && <Field label="Password" hint={emailOn ? "at least 8 characters; no email, so no invite goes out" : "at least 8 characters; email isn’t set up, so no invite can go out"}><input className={inputCls} type="password" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></Field>}
      </div>
      <label className={`flex items-center gap-2 text-sm mb-4 ${self ? "opacity-60" : ""}`}>
        <input type="checkbox" checked={f.is_admin} disabled={self} onChange={(e) => setF({ ...f, is_admin: e.target.checked })} />
        <span><b>Admin</b> <span className="text-muted">: everything in every business, including users, businesses and the audit log{self ? " (you can’t remove your own admin)" : ""}</span></span>
      </label>
      {!f.is_admin && (
        <fieldset className="mb-4">
          {businesses.length > 1 && (
            <div role="tablist" aria-label="Business" className="flex flex-wrap gap-1 mb-3">
              {businesses.map((b) => (
                <button key={b.id} type="button" role="tab" aria-selected={b.id === biz} onClick={() => setBiz(b.id)}
                  className={`px-3 py-1.5 rounded-control text-sm border ${b.id === biz ? "bg-raised border-line-strong text-ink font-medium" : "border-transparent text-muted hover:text-ink"}`}>
                  {b.name}{b.id in f.access ? "" : " · no access"}</button>))}
            </div>)}
          <label className="flex items-center gap-2 text-sm mb-3">
            <input type="checkbox" checked={member} onChange={(e) => setMember(e.target.checked)} />
            <span>Can work in <b>{bizName}</b></span>
          </label>
          {member && <>
          <div className="flex flex-wrap items-center gap-2 mb-2">
            <legend className="text-sm text-muted mr-2">Access by area in {bizName}</legend>
            {PRESETS.map((p) => <button key={p.label} type="button" className="text-xs px-2 py-1 rounded-md border border-line hover:bg-raised" onClick={() => setPerms(p.perms(areas))}>{p.label}</button>)}
            <button type="button" className="text-xs px-2 py-1 rounded-md border border-line hover:bg-raised" onClick={() => setPerms({})}>None</button>
          </div>
          <div className="table-box"><table className="grid">
            <thead><tr><th>Area</th><th>No access</th><th>View</th><th>Edit</th></tr></thead>
            <tbody>{areas.map((a) => {
              const cur = perms[a.key] ?? "none";
              return (
                <tr key={a.key}>
                  <td>{a.label}</td>
                  {(["none", "view", "edit"] as const).map((lv) => (
                    <td key={lv}>{lv === "none" || a.levels.includes(lv)
                      ? <input type="radio" name={`perm-${biz}-${a.key}`} aria-label={`${a.label}: ${lv}`} checked={cur === lv} onChange={() => setLevel(a.key, lv)} />
                      : <Nil />}</td>))}
                </tr>);
            })}</tbody>
          </table></div>
          </>}
        </fieldset>)}
      <div className="flex flex-wrap items-end gap-3">
        <Button onClick={() => onSave(f)} disabled={!ok}>{isNew ? (invite ? "Send invite" : "Add user") : "Save"}</Button>
        <Button kind="quiet" onClick={onCancel}>Cancel</Button>
        {missing && <span className="text-sm text-muted self-center">{missing}</span>}
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
