import { useState } from "react";
import { useStickyTop } from "../lib/sticky";
import { Link, NavLink, Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { ThemeToggle } from "../App";
import { api, d, dt, n } from "../lib/api";
import { useMe } from "../lib/auth";
import { Button, Empty, Field, Flash, inputCls, Loading, Nil, Notice, Page, useAsync } from "../lib/ui";
import Account from "./Account";
import Audit from "./Audit";

/** The platform console: above every tenant. Only platform admins see it, and they see nothing else. */

type TenantRow = {
  id: string; name: string; status: "active" | "suspended"; plan: string | null; contact_name: string | null; contact_email: string | null; notes: string | null;
  max_businesses: number | null; max_users: number | null; monthly_token_cap: number | null; suspended_at: string | null; created_at: string;
  businesses: number; users: number; active_users: number; logins: number; changes: number; counts_posted: number; deliveries_posted: number; sales_imports: number;
  receipts_read: number; receipts_failed: number; input_tokens: number; output_tokens: number; tokens: number; cost_usd: number; storage_bytes: number;
  cap_used_pct: number | null; last_activity: string | null;
};
type Daily = { day: string; input_tokens: number; output_tokens: number; cost_usd: number; calls: number };
type Overview = { month: string; tenants: number; active_tenants: number; suspended_tenants: number; businesses: number; users: number; active_users: number;
  tokens: number; cost_usd: number; receipts_read: number; storage_bytes: number; near_cap: { id: string; name: string; cap_used_pct: number }[];
  daily: Daily[]; prices: Record<string, [number, number]> };
type Detail = {
  tenant: TenantRow; month: string;
  businesses: { id: string; name: string; kind: string | null; city: string | null; is_active: boolean; has_logo: boolean; track_food: boolean; swype: boolean; members: number; items: number;
    counts_posted: number; receipts_read: number; tokens: number; cost_usd: number; storage_bytes: number; last_activity: string | null }[];
  users: { id: string; username: string; display_name: string; email: string | null; is_admin: boolean; is_active: boolean; mfa_enabled: boolean; invite_pending: boolean; last_login_at: string | null }[];
  months: { month: string; calls: number; input_tokens: number; output_tokens: number; cost_usd: number }[]; daily: Daily[];
};
type Report = { month: string; columns: { key: keyof TenantRow; label: string }[]; rows: TenantRow[]; totals: Record<string, number> };
type UsageRow = { id: string; at: string; tenant_id: string | null; tenant: string | null; business: string | null; kind: string; model: string; input_tokens: number; output_tokens: number; cost_usd: number; ok: boolean };

const usd = (v: number) => v.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: v !== 0 && Math.abs(v) < 1 ? 4 : 2 });
const bytes = (v: number) => v < 1024 ? `${v} B` : v < 1024 ** 2 ? `${(v / 1024).toFixed(0)} KB` : v < 1024 ** 3 ? `${(v / 1024 ** 2).toFixed(1)} MB` : `${(v / 1024 ** 3).toFixed(2)} GB`;
const monthName = (m: string) => new Date(`${m}-01T00:00`).toLocaleDateString("en-CA", { month: "long", year: "numeric" });
const thisMonth = () => new Date().toISOString().slice(0, 7);
const limit = (v: number | null) => v == null ? "no limit" : n(v, 0);
const numOrNull = (s: string) => s.trim() === "" ? null : Number(s.replace(/[,\s]/g, ""));
const tabs = [["/platform", "Tenants"], ["/platform/usage", "Claude usage"], ["/platform/reports", "Reports"], ["/platform/audit", "Audit log"], ["/platform/settings", "Settings"]] as const;

export default function Platform({ onLogout }: { onLogout: () => void }) {
  const me = useMe();
  const bar = useStickyTop();
  return (
    <div className="min-h-screen flex flex-col">
      <header ref={bar} className="sticky top-0 z-40 bg-paper border-b border-line">
        <div className="max-w-[1280px] mx-auto px-4 sm:px-7 py-3 flex flex-wrap items-center gap-x-4 gap-y-2">
          <NavLink to="/platform" className="flex items-center gap-2 font-semibold mr-2">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="var(--color-accent)" strokeWidth="1.8" aria-hidden><path d="M3 9l9-6 9 6v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z" /><path d="M9 21v-8h6v8" /></svg>
            Platform console
          </NavLink>
          <nav aria-label="Platform" className="flex flex-wrap gap-1">
            {tabs.map(([to, label]) => (
              <NavLink key={to} to={to} end={to === "/platform"} className={({ isActive }) => `px-3 py-1.5 rounded-control text-sm ${isActive ? "bg-raised text-ink font-medium" : "text-muted hover:text-ink"}`}>{label}</NavLink>))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <ThemeToggle />
            <NavLink to="/account" title="My account" className="text-xs text-muted hover:text-ink px-1 max-w-32 truncate">{me?.display_name}</NavLink>
            <button onClick={onLogout} className="text-xs text-faint hover:text-ink px-1">Log out</button>
          </div>
        </div>
      </header>
      <main className="flex-1 min-w-0">
        <Routes>
          <Route path="/platform" element={<Tenants />} />
          <Route path="/platform/tenants/:id" element={<TenantPage />} />
          <Route path="/platform/usage" element={<Usage />} />
          <Route path="/platform/reports" element={<Reports />} />
          <Route path="/platform/audit" element={<Audit platform />} />
          <Route path="/platform/settings" element={<PlatformSettings />} />
          <Route path="/account" element={<Account />} />
          <Route path="*" element={<Navigate to="/platform" replace />} />
        </Routes>
      </main>
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="card p-4">
      <div className="label">{label}</div>
      <div className="text-2xl font-semibold num mt-1">{value}</div>
      {sub && <div className="text-xs text-muted mt-1">{sub}</div>}
    </div>
  );
}

function StatusChip({ status }: { status: string }) {
  return status === "active" ? <span className="text-ok">Active</span> : <span className="text-danger">Suspended</span>;
}

/** Share of the monthly token cap used: the figure, and a bar that turns red at the cap. */
function CapUse({ row }: { row: Pick<TenantRow, "cap_used_pct" | "monthly_token_cap" | "tokens"> }) {
  if (row.cap_used_pct == null) return <span className="text-faint">no cap</span>;
  const p = Math.min(100, row.cap_used_pct);
  return (
    <div className="min-w-28" title={`${n(row.tokens, 0)} of ${n(row.monthly_token_cap, 0)} tokens`}>
      <div className={`num text-sm ${row.cap_used_pct >= 100 ? "text-danger font-medium" : ""}`}>{n(row.cap_used_pct, 1)}%</div>
      <div className="h-1.5 rounded-full bg-raised overflow-hidden mt-1" role="img" aria-label={`${n(row.cap_used_pct, 1)}% of the monthly token cap used`}>
        <div className="h-full rounded-full" style={{ width: `${p}%`, background: row.cap_used_pct >= 100 ? "var(--color-danger)" : "var(--color-accent)" }} />
      </div>
    </div>
  );
}

// ---- tenants ------------------------------------------------------------------------------------------------

type NewTenant = { name: string; plan: string; contact_name: string; contact_email: string; business_name: string;
  max_businesses: string; max_users: string; monthly_token_cap: string; username: string; display_name: string; email: string; password: string };
const blankTenant: NewTenant = { name: "", plan: "", contact_name: "", contact_email: "", business_name: "", max_businesses: "", max_users: "", monthly_token_cap: "",
  username: "", display_name: "", email: "", password: "" };

function Tenants() {
  const overview = useAsync(() => api.get<Overview>("/api/platform/overview"));
  const list = useAsync(() => api.get<{ month: string; tenants: TenantRow[] }>("/api/platform/tenants"));
  const options = useAsync(() => api.get<{ email_enabled: boolean }>("/api/auth/options"));
  const [adding, setAdding] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const navigate = useNavigate();
  const o = overview.data, rows = list.data?.tenants ?? [];
  const add = async (f: NewTenant) => {
    try {
      const t = await api.post<{ id: string; name: string; invite_sent: boolean | null }>("/api/platform/tenants", {
        name: f.name.trim(), plan: f.plan.trim() || null, contact_name: f.contact_name.trim() || null, contact_email: f.contact_email.trim(),
        business_name: f.business_name.trim() || null, max_businesses: numOrNull(f.max_businesses), max_users: numOrNull(f.max_users), monthly_token_cap: numOrNull(f.monthly_token_cap),
        admin: { username: f.username.trim(), display_name: f.display_name.trim(), email: f.email.trim() || undefined, password: f.password || undefined } });
      if (t.invite_sent === false) setMsg({ kind: "error", text: `Added ${t.name}, but the admin’s invite email couldn’t be sent. Add another admin with a password on the tenant’s page.` });
      else navigate(`/platform/tenants/${t.id}`);
    } catch (e) { setMsg({ kind: "error", text: (e as Error).message }); }
  };
  return (
    <Page title="Tenants" sub={o ? `${monthName(o.month)} so far, all tenants` : undefined} actions={<Button onClick={() => setAdding((v) => !v)}>{adding ? "Cancel" : "Add tenant"}</Button>}>
      {msg && <Flash kind={msg.kind} onClose={() => setMsg(null)}>{msg.text}</Flash>}
      {(overview.error || list.error) && <Notice kind="error">{overview.error || list.error}</Notice>}
      {o && (
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-6">
          <Stat label="Tenants" value={n(o.active_tenants, 0)} sub={o.suspended_tenants ? `${o.suspended_tenants} suspended` : "all active"} />
          <Stat label="Businesses" value={n(o.businesses, 0)} sub={`${n(o.users, 0)} logins, ${n(o.active_users, 0)} active this month`} />
          <Stat label="Claude tokens" value={n(o.tokens, 0)} sub={`${n(o.receipts_read, 0)} receipts read`} />
          <Stat label="Estimated Claude cost" value={usd(o.cost_usd)} sub="from list prices; the invoice is the real figure" />
          <Stat label="Stored files" value={bytes(o.storage_bytes)} sub="receipts, recipe photos, logos" />
        </div>)}
      {o && o.near_cap.length > 0 && <Notice>Near or over the monthly token cap: {o.near_cap.map((t, i) => <span key={t.id}>{i > 0 && ", "}<Link className="underline" to={`/platform/tenants/${t.id}`}>{t.name}</Link> ({n(t.cap_used_pct, 0)}%)</span>)}. Once a tenant reaches its cap, receipts are stored but not read until next month.</Notice>}
      {adding && options.data && <TenantForm emailOn={options.data.email_enabled} onSave={add} onCancel={() => setAdding(false)} />}
      {list.loading && !list.data && <Loading />}
      {list.data && rows.length === 0 && <Empty>No tenants yet.</Empty>}
      {rows.length > 0 && (
        <div className="table-box"><table className="grid">
          <thead><tr><th>Tenant</th><th>Status</th><th className="r">Businesses</th><th className="r">Logins</th><th className="r">Active people</th>
            <th className="r">Receipts read</th><th className="r">Tokens</th><th className="r">Est. cost</th><th>Cap used</th><th>Last activity</th></tr></thead>
          <tbody>{rows.map((t) => (
            <tr key={t.id} className={t.status === "active" ? "" : "opacity-70"}>
              <td><Link className="font-medium hover:underline" to={`/platform/tenants/${t.id}`}>{t.name}</Link>{t.plan && <div className="text-xs text-muted">{t.plan}</div>}</td>
              <td className="text-sm whitespace-nowrap"><StatusChip status={t.status} /></td>
              <td className="r num">{t.businesses}{t.max_businesses != null && <span className="text-muted"> / {t.max_businesses}</span>}</td>
              <td className="r num">{t.users}{t.max_users != null && <span className="text-muted"> / {t.max_users}</span>}</td>
              <td className="r num">{t.active_users}</td>
              <td className="r num">{t.receipts_read}</td>
              <td className="r num">{n(t.tokens, 0)}</td>
              <td className="r num">{usd(t.cost_usd)}</td>
              <td><CapUse row={t} /></td>
              <td className="text-sm text-muted whitespace-nowrap">{t.last_activity ? dt(t.last_activity) : "never"}</td>
            </tr>))}
          </tbody>
        </table></div>)}
    </Page>
  );
}

function TenantForm({ emailOn, onSave, onCancel }: { emailOn: boolean; onSave: (f: NewTenant) => void; onCancel: () => void }) {
  const [f, setF] = useState<NewTenant>(blankTenant);
  const set = (k: keyof NewTenant) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const invite = emailOn && !!f.email.trim() && !f.password;
  const missing = !f.name.trim() ? "Enter the tenant’s name." : !f.display_name.trim() ? "Enter the admin’s name." : f.username.trim().length < 2 ? "Enter a username for the admin."
    : !invite && f.password.length < 8 ? (emailOn ? "Give the admin an email (they get an invite) or a password of 8 or more characters." : "Give the admin a password of 8 or more characters.")
    : [f.max_businesses, f.max_users, f.monthly_token_cap].some((v) => v.trim() !== "" && !/^[\d,\s]+$/.test(v)) ? "Limits are whole numbers; leave one empty for no limit." : "";
  return (
    <form className="card p-5 mb-6 bg-raised/30" onSubmit={(e) => { e.preventDefault(); if (!missing) onSave(f); }}>
      <div className="grid grid-cols-1 sm:grid-cols-4 gap-3 mb-4">
        <Field label="Tenant name"><input className={inputCls} autoFocus value={f.name} onChange={set("name")} /></Field>
        <Field label="Plan (optional)" hint="a label for your own records"><input className={inputCls} value={f.plan} onChange={set("plan")} /></Field>
        <Field label="Contact name (optional)"><input className={inputCls} value={f.contact_name} onChange={set("contact_name")} /></Field>
        <Field label="Contact email (optional)"><input className={inputCls} type="email" value={f.contact_email} onChange={set("contact_email")} /></Field>
        <Field label="First business (optional)" hint="or their admin adds it at first login"><input className={inputCls} placeholder="e.g. Main bar" value={f.business_name} onChange={set("business_name")} /></Field>
        <Field label="Max businesses" hint="empty: no limit"><input className={`${inputCls} num`} inputMode="numeric" value={f.max_businesses} onChange={set("max_businesses")} /></Field>
        <Field label="Max active logins" hint="empty: no limit"><input className={`${inputCls} num`} inputMode="numeric" value={f.max_users} onChange={set("max_users")} /></Field>
        <Field label="Claude tokens per month" hint="empty: no cap"><input className={`${inputCls} num`} inputMode="numeric" placeholder="e.g. 2,000,000" value={f.monthly_token_cap} onChange={set("monthly_token_cap")} /></Field>
      </div>
      <fieldset className="mb-4">
        <legend className="text-sm font-medium mb-2">Their first admin</legend>
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
          <Field label="Name"><input className={inputCls} value={f.display_name} onChange={set("display_name")} /></Field>
          <Field label="Username" hint="unique across all tenants"><input className={`${inputCls} mono`} autoCapitalize="none" value={f.username} onChange={set("username")} /></Field>
          <Field label="Email" hint={emailOn ? "they get a link to choose a password" : "for password resets"}><input className={inputCls} type="email" autoCapitalize="none" value={f.email} onChange={set("email")} /></Field>
          <Field label="Password" hint={emailOn ? "leave empty to send an invite" : "at least 8 characters"}><input className={inputCls} type="password" autoComplete="new-password" value={f.password} onChange={set("password")} /></Field>
        </div>
      </fieldset>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={!!missing}>Add tenant</Button><Button type="button" kind="quiet" onClick={onCancel}>Cancel</Button>
        {missing && <span className="text-sm text-muted">{missing}</span>}
      </div>
    </form>
  );
}

function TenantPage() {
  const { id } = useParams();
  const detail = useAsync(() => api.get<Detail>(`/api/platform/tenants/${id}`), [id]);
  const options = useAsync(() => api.get<{ email_enabled: boolean }>("/api/auth/options"));
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [editing, setEditing] = useState(false);
  const [addingAdmin, setAddingAdmin] = useState(false);
  const fail = (e: unknown) => setMsg({ kind: "error", text: (e as Error).message });
  if (!detail.data) return <Page title="Tenant">{detail.error ? <Notice kind="error">{detail.error}</Notice> : <Loading />}</Page>;
  const { tenant: t, businesses, users, months, daily } = detail.data;
  const suspend = async (on: boolean) => {
    if (on && !confirm(`Suspend ${t.name}? Its ${t.users} logins stop working at once and its nightly imports stop. Nothing is deleted, and you can reactivate it any time.`)) return;
    try { await api.post(`/api/platform/tenants/${t.id}/${on ? "suspend" : "reactivate"}`); setMsg({ kind: "ok", text: on ? `${t.name} is suspended.` : `${t.name} is active again.` }); detail.reload(); } catch (e) { fail(e); }
  };
  const setFood = async (b: Detail["businesses"][number], on: boolean) => {
    if (!confirm(on
      ? `Track food at ${b.name}? Food it sold stops being marked as taking no stock: every dish goes to the Recipes queue to be linked to stock items, like drinks.`
      : `Stop tracking food at ${b.name}? Recipes already made for food stay. Food with no recipe takes no stock again from the next sales import.`)) return;
    try {
      const r = await api.patch<{ food_released: number }>(`/api/platform/businesses/${b.id}`, { track_food: on });
      setMsg({ kind: "ok", text: on ? `${b.name} now tracks food.${r.food_released ? ` ${r.food_released} dishes went back to its Recipes queue.` : ""}` : `${b.name} no longer tracks food.` });
      detail.reload();
    } catch (e) { fail(e); }
  };
  const used = daily.filter((x) => x.calls > 0).reverse();
  return (
    <Page title={t.name} sub={<><Link className="underline" to="/platform">Tenants</Link> · <StatusChip status={t.status} />{t.plan ? ` · ${t.plan}` : ""} · since {d(t.created_at)}</>}
      actions={<>
        <Button kind="quiet" onClick={() => setEditing((v) => !v)}>{editing ? "Cancel" : "Edit and limits"}</Button>
        {t.status === "active" ? <Button kind="danger" onClick={() => suspend(true)}>Suspend</Button> : <Button onClick={() => suspend(false)}>Reactivate</Button>}
      </>}>
      {msg && <Flash kind={msg.kind} onClose={() => setMsg(null)}>{msg.text}</Flash>}
      {t.status !== "active" && <Notice kind="error">Suspended{t.suspended_at ? ` on ${dt(t.suspended_at)}` : ""}. Nobody from this tenant can log in.</Notice>}
      {editing && <TenantEdit tenant={t} onSaved={() => { setEditing(false); setMsg({ kind: "ok", text: "Saved." }); detail.reload(); }} onError={fail} />}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-6">
        <Stat label="Businesses" value={`${t.businesses}`} sub={`limit: ${limit(t.max_businesses)}`} />
        <Stat label="Active logins" value={`${t.users}`} sub={`limit: ${limit(t.max_users)} · ${t.active_users} active this month`} />
        <Stat label="Tokens this month" value={n(t.tokens, 0)} sub={t.monthly_token_cap != null ? `cap: ${n(t.monthly_token_cap, 0)} (${n(t.cap_used_pct, 1)}% used)` : "no cap"} />
        <Stat label="Est. cost this month" value={usd(t.cost_usd)} sub={`${t.receipts_read} receipts read${t.receipts_failed ? `, ${t.receipts_failed} failed` : ""}`} />
        <Stat label="Stored files" value={bytes(t.storage_bytes)} sub={t.last_activity ? `last activity ${dt(t.last_activity)}` : "no activity yet"} />
      </div>
      {(t.contact_name || t.contact_email || t.notes) && (
        <div className="card p-4 mb-6 text-sm"><span className="text-muted">Contact:</span> {[t.contact_name, t.contact_email].filter(Boolean).join(" · ") || "none"}{t.notes && <div className="mt-1 whitespace-pre-wrap">{t.notes}</div>}</div>)}

      <h2 className="font-semibold mb-2">Businesses</h2>
      {businesses.length === 0 ? <Empty>No business yet. Their admin adds the first one at login.</Empty> : (
        <div className="table-box mb-6"><table className="grid">
          <thead><tr><th>Business</th><th className="r">People</th><th className="r">Stock items</th><th className="r">Counts posted</th><th className="r">Receipts read</th>
            <th className="r">Tokens</th><th className="r">Est. cost</th><th className="r">Files</th><th>Swype</th><th>Food tracking</th><th>Last activity</th></tr></thead>
          <tbody>{businesses.map((b) => (
            <tr key={b.id} className={b.is_active ? "" : "opacity-60"}>
              <td><div className="font-medium">{b.name}{!b.is_active && <span className="text-xs text-muted font-normal"> · switched off</span>}</div><div className="text-xs text-muted">{[b.kind, b.city].filter(Boolean).join(" · ")}</div></td>
              <td className="r num">{b.members}</td><td className="r num">{n(b.items, 0)}</td><td className="r num">{b.counts_posted}</td><td className="r num">{b.receipts_read}</td>
              <td className="r num">{n(b.tokens, 0)}</td><td className="r num">{usd(b.cost_usd)}</td><td className="r num">{bytes(b.storage_bytes)}</td>
              <td className="text-sm">{b.swype ? "set" : <span className="text-faint">not set</span>}</td>
              <td className="text-sm whitespace-nowrap"><span className={b.track_food ? "text-accent font-medium" : "text-faint"}>{b.track_food ? "on" : "off"}</span>{" "}
                <button type="button" className="underline text-muted hover:text-ink" onClick={() => setFood(b, !b.track_food)}>{b.track_food ? "switch off" : "switch on"}</button></td>
              <td className="text-sm text-muted whitespace-nowrap">{b.last_activity ? dt(b.last_activity) : "never"}</td>
            </tr>))}
          </tbody>
        </table></div>)}
      <p className="text-xs text-muted -mt-4 mb-6">People counts members; admins reach every business. Food tracking: food sold is linked to stock by recipes, like drinks, instead of taking no stock. Receipts, tokens and cost are for {monthName(detail.data.month)}.</p>

      <div className="flex items-center justify-between mb-2"><h2 className="font-semibold">Logins</h2>
        <Button kind="quiet" onClick={() => setAddingAdmin((v) => !v)}>{addingAdmin ? "Cancel" : "Add an admin"}</Button></div>
      {addingAdmin && options.data && <AdminForm emailOn={options.data.email_enabled} label="Add admin" onSave={async (a) => {
        try { await api.post(`/api/platform/tenants/${t.id}/admins`, a); setAddingAdmin(false); setMsg({ kind: "ok", text: `Added ${a.display_name} as an admin of ${t.name}.` }); detail.reload(); } catch (e) { fail(e); } }} />}
      <div className="table-box mb-6"><table className="grid">
        <thead><tr><th>Name</th><th>Role</th><th>Status</th><th>Last login</th></tr></thead>
        <tbody>{users.map((u) => (
          <tr key={u.id} className={u.is_active ? "" : "opacity-60"}>
            <td><div className="font-medium">{u.display_name}</div><div className="mono text-xs text-muted">{u.username}{u.email ? ` · ${u.email}` : ""}</div></td>
            <td className="text-sm">{u.is_admin ? <span className="text-accent font-medium">Admin</span> : "Staff"}</td>
            <td className="text-sm">{u.is_active ? <span className="text-ok">Active</span> : <span className="text-muted">Switched off</span>}{u.mfa_enabled && <span className="text-muted"> · 2-step</span>}{u.invite_pending && <span className="text-muted"> · invited</span>}</td>
            <td className="text-sm text-muted whitespace-nowrap">{u.last_login_at ? dt(u.last_login_at) : "never"}</td>
          </tr>))}
        </tbody>
      </table></div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <section>
          <h2 className="font-semibold mb-2">Claude usage by month</h2>
          <div className="table-box"><table className="grid">
            <thead><tr><th>Month</th><th className="r">Receipts</th><th className="r">Input tokens</th><th className="r">Output tokens</th><th className="r">Est. cost</th></tr></thead>
            <tbody>{months.map((m) => (
              <tr key={m.month} className={m.calls ? "" : "text-faint"}><td>{monthName(m.month)}</td><td className="r num">{m.calls}</td><td className="r num">{n(m.input_tokens, 0)}</td>
                <td className="r num">{n(m.output_tokens, 0)}</td><td className="r num">{usd(m.cost_usd)}</td></tr>))}</tbody>
          </table></div>
        </section>
        <section>
          <h2 className="font-semibold mb-2">Last 30 days</h2>
          {used.length === 0 ? <Empty>No receipts read in the last 30 days.</Empty> : (
            <div className="table-box"><table className="grid">
              <thead><tr><th>Day</th><th className="r">Receipts</th><th className="r">Tokens</th><th className="r">Est. cost</th></tr></thead>
              <tbody>{used.map((x) => <tr key={x.day}><td>{d(`${x.day}T12:00:00`)}</td><td className="r num">{x.calls}</td><td className="r num">{n(x.input_tokens + x.output_tokens, 0)}</td><td className="r num">{usd(x.cost_usd)}</td></tr>)}</tbody>
            </table></div>)}
        </section>
      </div>
    </Page>
  );
}

function TenantEdit({ tenant: t, onSaved, onError }: { tenant: TenantRow; onSaved: () => void; onError: (e: unknown) => void }) {
  const [f, setF] = useState({ name: t.name, plan: t.plan ?? "", contact_name: t.contact_name ?? "", contact_email: t.contact_email ?? "", notes: t.notes ?? "",
    max_businesses: t.max_businesses == null ? "" : String(t.max_businesses), max_users: t.max_users == null ? "" : String(t.max_users),
    monthly_token_cap: t.monthly_token_cap == null ? "" : String(t.monthly_token_cap) });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setF({ ...f, [k]: e.target.value });
  const bad = !f.name.trim() || [f.max_businesses, f.max_users, f.monthly_token_cap].some((v) => v.trim() !== "" && !/^[\d,\s]+$/.test(v));
  const save = async () => {
    try {
      await api.patch(`/api/platform/tenants/${t.id}`, { name: f.name.trim(), plan: f.plan, contact_name: f.contact_name, contact_email: f.contact_email.trim(), notes: f.notes,
        max_businesses: numOrNull(f.max_businesses), max_users: numOrNull(f.max_users), monthly_token_cap: numOrNull(f.monthly_token_cap) });
      onSaved();
    } catch (e) { onError(e); }
  };
  return (
    <form className="card p-5 mb-6 bg-raised/30" onSubmit={(e) => { e.preventDefault(); if (!bad) save(); }}>
      <div className="grid grid-cols-1 sm:grid-cols-4 gap-3 mb-3">
        <Field label="Tenant name"><input className={inputCls} value={f.name} onChange={set("name")} /></Field>
        <Field label="Plan"><input className={inputCls} value={f.plan} onChange={set("plan")} /></Field>
        <Field label="Contact name"><input className={inputCls} value={f.contact_name} onChange={set("contact_name")} /></Field>
        <Field label="Contact email"><input className={inputCls} type="email" value={f.contact_email} onChange={set("contact_email")} /></Field>
        <Field label="Max businesses" hint="empty: no limit"><input className={`${inputCls} num`} inputMode="numeric" value={f.max_businesses} onChange={set("max_businesses")} /></Field>
        <Field label="Max active logins" hint="empty: no limit"><input className={`${inputCls} num`} inputMode="numeric" value={f.max_users} onChange={set("max_users")} /></Field>
        <Field label="Claude tokens per month" hint="empty: no cap. At the cap, receipts are stored but not read."><input className={`${inputCls} num`} inputMode="numeric" value={f.monthly_token_cap} onChange={set("monthly_token_cap")} /></Field>
      </div>
      <Field label="Notes"><textarea className={`${inputCls} h-20 py-2`} value={f.notes} onChange={set("notes")} /></Field>
      <div className="mt-3"><Button type="submit" disabled={bad}>Save</Button></div>
      <p className="text-xs text-muted mt-2">Lowering a limit below what the tenant already has removes nothing: it only stops them adding more.</p>
    </form>
  );
}

type AdminIn = { username: string; display_name: string; email?: string; password?: string };
function AdminForm({ emailOn, label, needPassword, onSave }: { emailOn: boolean; label: string; needPassword?: boolean; onSave: (a: AdminIn) => void }) {
  const [f, setF] = useState({ username: "", display_name: "", email: "", password: "" });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const invite = !needPassword && emailOn && !!f.email.trim() && !f.password;
  const bad = !f.display_name.trim() || f.username.trim().length < 2 || (!invite && f.password.length < 8);
  return (
    <form className="card p-4 mb-4 bg-raised/30 grid grid-cols-1 sm:grid-cols-5 gap-3 items-end" onSubmit={(e) => { e.preventDefault(); if (!bad) onSave({ username: f.username.trim(), display_name: f.display_name.trim(), email: f.email.trim() || undefined, password: f.password || undefined }); }}>
      <Field label="Name"><input className={inputCls} value={f.display_name} onChange={set("display_name")} /></Field>
      <Field label="Username"><input className={`${inputCls} mono`} autoCapitalize="none" value={f.username} onChange={set("username")} /></Field>
      <Field label="Email"><input className={inputCls} type="email" autoCapitalize="none" value={f.email} onChange={set("email")} /></Field>
      <Field label={!needPassword && emailOn ? "Password (or invite by email)" : "Password"}><input className={inputCls} type="password" autoComplete="new-password" value={f.password} onChange={set("password")} /></Field>
      <Button type="submit" disabled={bad}>{label}</Button>
    </form>
  );
}

// ---- Claude usage ---------------------------------------------------------------------------------------------

function Usage() {
  const overview = useAsync(() => api.get<Overview>("/api/platform/overview"));
  const rows = useAsync(() => api.get<{ total: number; rows: UsageRow[] }>("/api/platform/usage?limit=200"));
  const o = overview.data;
  const days = (o?.daily ?? []).filter((x) => x.calls > 0).reverse();
  return (
    <Page title="Claude usage" sub="Every receipt read by Claude, with the tokens it took and an estimated cost">
      {(overview.error || rows.error) && <Notice kind="error">{overview.error || rows.error}</Notice>}
      {!o && !overview.error && <Loading rows={3} />}
      {o && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
          <Stat label={`Tokens, ${monthName(o.month)}`} value={n(o.tokens, 0)} />
          <Stat label="Estimated cost" value={usd(o.cost_usd)} />
          <Stat label="Receipts read" value={n(o.receipts_read, 0)} sub={o.receipts_read ? `${usd(o.cost_usd / o.receipts_read)} per receipt` : undefined} />
          <Stat label="Tenants near their cap" value={`${o.near_cap.length}`} sub="80% or more of the monthly cap" />
        </div>)}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <section>
          <h2 className="font-semibold mb-2">By day, last 30 days</h2>
          {days.length === 0 ? <Empty>Nothing read in the last 30 days.</Empty> : (
            <div className="table-box"><table className="grid">
              <thead><tr><th>Day</th><th className="r">Receipts</th><th className="r">Tokens</th><th className="r">Est. cost</th></tr></thead>
              <tbody>{days.map((x) => <tr key={x.day}><td>{d(`${x.day}T12:00:00`)}</td><td className="r num">{x.calls}</td><td className="r num">{n(x.input_tokens + x.output_tokens, 0)}</td><td className="r num">{usd(x.cost_usd)}</td></tr>)}</tbody>
            </table></div>)}
          {o && <>
            <h2 className="font-semibold mt-6 mb-2">Prices used</h2>
            <div className="table-box"><table className="grid">
              <thead><tr><th>Model</th><th className="r">Input / 1M</th><th className="r">Output / 1M</th></tr></thead>
              <tbody>{Object.entries(o.prices).map(([m, [i, out]]) => <tr key={m}><td className="mono text-xs">{m}</td><td className="r num">{usd(i)}</td><td className="r num">{usd(out)}</td></tr>)}</tbody>
            </table></div>
            <p className="text-xs text-muted mt-2">List prices in USD per million tokens. Change or add models with AI_PRICES in .env. A model with no price counts tokens but costs $0 here.</p>
          </>}
        </section>
        <section className="lg:col-span-2">
          <h2 className="font-semibold mb-2">Latest calls{rows.data && rows.data.total > rows.data.rows.length ? ` (${rows.data.rows.length} of ${n(rows.data.total, 0)})` : ""}</h2>
          {rows.loading && !rows.data && <Loading />}
          {rows.data && rows.data.rows.length === 0 && <Empty>No receipts have been read yet. Each read appears here with the tenant and business it was for.</Empty>}
          {rows.data && rows.data.rows.length > 0 && (
            <div className="table-box"><table className="grid">
              <thead><tr><th>When</th><th>Tenant</th><th>Business</th><th>Model</th><th className="r">In</th><th className="r">Out</th><th className="r">Est. cost</th><th>Result</th></tr></thead>
              <tbody>{rows.data.rows.map((u) => (
                <tr key={u.id}><td className="text-sm whitespace-nowrap">{dt(u.at)}</td>
                  <td>{u.tenant_id ? <Link className="hover:underline" to={`/platform/tenants/${u.tenant_id}`}>{u.tenant}</Link> : <Nil />}</td>
                  <td>{u.business ?? <Nil />}</td><td className="mono text-xs">{u.model}</td>
                  <td className="r num">{n(u.input_tokens, 0)}</td><td className="r num">{n(u.output_tokens, 0)}</td><td className="r num">{usd(u.cost_usd)}</td>
                  <td className="text-sm">{u.ok ? <span className="text-ok">read</span> : <span className="text-danger">not read</span>}</td></tr>))}
              </tbody>
            </table></div>)}
        </section>
      </div>
    </Page>
  );
}

// ---- reports --------------------------------------------------------------------------------------------------

function Reports() {
  const [month, setMonth] = useState(thisMonth());
  const rep = useAsync(() => api.get<Report>(`/api/platform/reports/monthly?month=${month}`), [month]);
  const r = rep.data;
  const cell = (row: TenantRow, k: keyof TenantRow) => {
    const v = row[k];
    if (k === "cost_usd") return usd(Number(v));
    if (k === "storage_bytes") return bytes(Number(v));
    if (k === "cap_used_pct") return v == null ? "" : `${n(v as number, 1)}%`;
    if (k === "monthly_token_cap") return v == null ? "" : n(v as number, 0);
    return typeof v === "number" ? n(v, 0) : (v as string | null) ?? "";
  };
  const numeric = (k: string) => !["name", "status", "plan"].includes(k);
  return (
    <Page title="Monthly usage report" sub="One row per tenant for a calendar month (UTC): activity, Claude tokens and estimated cost. For billing and reviews."
      actions={<>
        <label className="flex items-center gap-2 text-sm"><span className="text-muted">Month</span>
          <input type="month" className={inputCls} value={month} max={thisMonth()} onChange={(e) => { if (e.target.value) setMonth(e.target.value); }} /></label>
        <a className="ctl inline-flex items-center h-9 px-3.5 rounded-control text-sm font-medium border border-line-strong hover:bg-raised" href={`/api/platform/reports/monthly?month=${month}&format=xlsx`}>Download XLSX</a>
        <a className="ctl inline-flex items-center h-9 px-3.5 rounded-control text-sm font-medium border border-line-strong hover:bg-raised" href={`/api/platform/reports/monthly?month=${month}&format=csv`}>Download CSV</a>
      </>}>
      {rep.error && <Notice kind="error">{rep.error}</Notice>}
      {rep.loading && !r && <Loading />}
      {r && r.rows.length === 0 && <Empty>No tenants.</Empty>}
      {r && r.rows.length > 0 && (
        <div className="table-box"><table className="grid">
          <thead><tr>{r.columns.map((c) => <th key={c.key} className={numeric(c.key) ? "r" : ""}>{c.label}</th>)}</tr></thead>
          <tbody>
            {r.rows.map((row) => <tr key={row.id}>{r.columns.map((c) => (
              <td key={c.key} className={numeric(c.key) ? "r num whitespace-nowrap" : "whitespace-nowrap"}>
                {c.key === "name" ? <Link className="font-medium hover:underline" to={`/platform/tenants/${row.id}`}>{row.name}</Link> : cell(row, c.key)}</td>))}</tr>)}
            <tr className="font-semibold">{r.columns.map((c) => (
              <td key={c.key} className={numeric(c.key) ? "r num whitespace-nowrap" : ""}>
                {c.key === "name" ? `All ${r.rows.length} tenants` : c.key in r.totals ? (c.key === "cost_usd" ? usd(r.totals[c.key]) : c.key === "storage_bytes" ? bytes(r.totals[c.key]) : n(r.totals[c.key], 0)) : ""}</td>))}</tr>
          </tbody>
        </table></div>)}
      <p className="text-sm text-muted mt-4 max-w-3xl">Businesses, active logins and storage are as of now; everything else is counted within {monthName(month)}. “People active” is the number of different logins that did anything that month. Costs are estimates from list prices.</p>
    </Page>
  );
}

// ---- settings: retention, platform admins, audit --------------------------------------------------------------

const RETENTION: [number, string][] = [[90, "3 months"], [180, "6 months"], [365, "1 year"], [730, "2 years"], [1825, "5 years"], [0, "Forever"]];
type PAdmin = { id: string; username: string; display_name: string; email: string | null; is_active: boolean; mfa_enabled: boolean; last_login_at: string | null };
type PEvent = { id: string; at: string; username: string | null; tenant: string | null; action: string; label: string; status: number | null; ip: string | null };

function PlatformSettings() {
  const me = useMe();
  const s = useAsync(() => api.get<{ audit_retention_days: number; audit_purge_last: string | null }>("/api/platform/settings"));
  const admins = useAsync(() => api.get<PAdmin[]>("/api/platform/admins"));
  const events = useAsync(() => api.get<{ total: number; events: PEvent[] }>("/api/platform/audit?platform_only=true&limit=50"));
  const [adding, setAdding] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [check, setCheck] = useState<string | null>(null);
  const fail = (e: unknown) => setMsg({ kind: "error", text: (e as Error).message });
  const setRetention = async (days: number) => {
    const was = s.data!.audit_retention_days;
    if (days !== 0 && (was === 0 || days < was) && !confirm(`Keep the audit log for ${days} days, for every tenant? Older entries are removed for good at the next daily clean-up.`)) return;
    try { await api.put("/api/platform/settings", { audit_retention_days: days }); setMsg({ kind: "ok", text: "Saved." }); s.reload(); events.reload(); } catch (e) { fail(e); }
  };
  const toggle = async (a: PAdmin) => {
    if (a.is_active && !confirm(`Switch off ${a.display_name}? They are logged out of the console.`)) return;
    try { await api.post(`/api/platform/admins/${a.id}/active?active=${!a.is_active}`); admins.reload(); events.reload(); } catch (e) { fail(e); }
  };
  const verify = async () => {
    try { const r = await api.get<{ ok: boolean; checked: number; broken_at: { seq: number; at: string } | null }>("/api/platform/audit/verify");
      setCheck(r.ok ? `No tampering found: all ${n(r.checked, 0)} entries check out.` : `Entry ${r.broken_at?.seq} (${dt(r.broken_at?.at)}) was changed or an entry before it was removed.`); } catch (e) { fail(e); }
  };
  return (
    <Page title="Platform settings">
      {msg && <Flash kind={msg.kind} onClose={() => setMsg(null)}>{msg.text}</Flash>}
      {(s.error || admins.error) && <Notice kind="error">{s.error || admins.error}</Notice>}
      <div className="card max-w-3xl p-5 mb-6 grid grid-cols-1 sm:grid-cols-2 gap-5">
        <Field label="Keep the audit log for" hint={`For every tenant. Older entries are removed once a day${s.data?.audit_purge_last ? ` (last run ${s.data.audit_purge_last})` : ""}.`}>
          {s.data ? (
            <select className={inputCls} value={s.data.audit_retention_days} onChange={(e) => setRetention(Number(e.target.value))}>
              {RETENTION.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
              {!RETENTION.some(([v]) => v === s.data!.audit_retention_days) && <option value={s.data.audit_retention_days}>{s.data.audit_retention_days} days</option>}
            </select>) : <div className="h-9" />}
        </Field>
        <Field label="Audit log integrity" hint="Recomputes the hash chain over every tenant’s entries.">
          <div className="flex flex-wrap items-center gap-3"><Button kind="quiet" type="button" onClick={verify}>Check for tampering</Button>{check && <span className="text-sm">{check}</span>}</div>
        </Field>
      </div>

      <div className="flex items-center justify-between mb-2 max-w-3xl"><h2 className="font-semibold">Platform admins</h2>
        <Button kind="quiet" onClick={() => setAdding((v) => !v)}>{adding ? "Cancel" : "Add platform admin"}</Button></div>
      {adding && <AdminForm emailOn={false} needPassword label="Add" onSave={async (a) => {
        try { await api.post("/api/platform/admins", a); setAdding(false); setMsg({ kind: "ok", text: `Added ${a.display_name}. They log in at the same login page.` }); admins.reload(); events.reload(); } catch (e) { fail(e); } }} />}
      <div className="table-box max-w-3xl mb-2"><table className="grid">
        <thead><tr><th>Name</th><th>Status</th><th>Last login</th><th /></tr></thead>
        <tbody>{(admins.data ?? []).map((a) => (
          <tr key={a.id} className={a.is_active ? "" : "opacity-60"}>
            <td><div className="font-medium">{a.display_name}{a.id === me?.id && <span className="text-xs text-faint"> · you</span>}</div><div className="mono text-xs text-muted">{a.username}{a.email ? ` · ${a.email}` : ""}</div></td>
            <td className="text-sm">{a.is_active ? <span className="text-ok">Active</span> : <span className="text-muted">Switched off</span>}{a.mfa_enabled && <span className="text-muted"> · 2-step</span>}</td>
            <td className="text-sm text-muted whitespace-nowrap">{a.last_login_at ? dt(a.last_login_at) : "never"}</td>
            <td className="r text-sm">{a.id !== me?.id && <button className={a.is_active ? "text-danger hover:underline" : "text-accent hover:underline"} onClick={() => toggle(a)}>{a.is_active ? "Switch off" : "Switch on"}</button>}</td>
          </tr>))}
        </tbody>
      </table></div>
      <p className="text-sm text-muted max-w-3xl mb-6">Platform admins see every tenant’s usage and its audit log (what was changed, by whom), but not its stock, counts or sales pages. Change your own password and turn on two-step login under <Link className="underline" to="/account">My account</Link>.</p>

      <div className="flex items-center justify-between mb-2 max-w-3xl"><h2 className="font-semibold">What was done in this console</h2>
        <Link className="text-sm underline text-muted hover:text-ink" to="/platform/audit">Whole audit log</Link></div>
      {events.data && events.data.events.length === 0 && <Empty>Nothing yet.</Empty>}
      {events.data && events.data.events.length > 0 && (
        <div className="table-box max-w-3xl"><table className="grid">
          <thead><tr><th>When</th><th>Who</th><th>What</th><th>Result</th></tr></thead>
          <tbody>{events.data.events.map((e) => (
            <tr key={e.id}><td className="text-sm whitespace-nowrap">{dt(e.at)}</td><td className="mono text-xs">{e.username}</td><td>{e.label}</td>
              <td className="text-sm">{e.status != null && e.status >= 400 ? <span className="text-danger">refused ({e.status})</span> : <span className="text-muted">done</span>}</td></tr>))}
          </tbody>
        </table></div>)}
    </Page>
  );
}
