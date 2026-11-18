import { Fragment, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type Area, type AuditChange, type AuditEvent, dt } from "../lib/api";
import { useMe } from "../lib/auth";
import { Button, Empty, Field, inputCls, Loading, Nil, Notice, Page, useAsync } from "../lib/ui";

type Person = { id: string; username: string; display_name: string; is_active: boolean; deleted?: boolean };
type Place = { id: string; name: string; businesses: { id: string; name: string }[] };
type Row = AuditEvent & { tenant?: string | null; business?: string | null };
const PAGE = 100;
const ACTIONS: Record<AuditEvent["action"], string> = { change: "Change", read: "Sensitive read", denied: "Refused", login: "Login", login_failed: "Failed login",
  login_blocked: "Blocked login", logout: "Logout", system: "System job", mfa_setup_started: "Two-step setup", mfa_email_sent: "Recovery code emailed",
  mfa_email_recovery: "Two-step recovery", reset_requested: "Reset requested", password_reset: "Password reset", invite_accepted: "Invite accepted" };

/** One business's log (tenant side), or with `platform` the console's: every tenant and business, and what belongs to neither. */
export default function Audit({ platform = false }: { platform?: boolean }) {
  const me = useMe();
  const base = platform ? "/api/platform/audit" : "/api/audit";
  const [sp, setSp] = useSearchParams();
  const f = { user: sp.get("user") ?? "", area: sp.get("area") ?? "", action: sp.get("action") ?? "", from: sp.get("from") ?? "", to: sp.get("to") ?? "", q: sp.get("q") ?? "",
    tenant: sp.get("tenant") ?? "", business: sp.get("business") ?? "" };
  const [page, setPage] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  const [check, setCheck] = useState<{ ok: boolean; checked: number; broken_at: { seq: number; at: string } | null } | "busy" | null>(null);
  const set = (k: keyof typeof f, v: string) => { const n = new URLSearchParams(sp); if (v) n.set(k, v); else n.delete(k); if (k === "tenant") n.delete("business"); setSp(n, { replace: true }); setPage(0); };

  const people = useAsync(() => platform ? Promise.resolve([] as Person[]) : api.get<Person[]>("/api/audit/users"), [platform]);
  const places = useAsync(() => platform ? api.get<Place[]>("/api/platform/audit/filters") : Promise.resolve([] as Place[]), [platform]);
  const areas = useAsync(() => api.get<Area[]>("/api/auth/areas"));
  const qs = new URLSearchParams({ limit: String(PAGE), offset: String(page * PAGE) });
  if (f.user && !platform) qs.set("user_id", f.user);
  if (f.tenant && platform) qs.set("tenant_id", f.tenant);
  if (f.business && platform) qs.set("business_id", f.business);
  if (f.area) qs.set("area", f.area);
  if (f.action) qs.set("action", f.action);
  if (f.from) qs.set("date_from", f.from);
  if (f.to) qs.set("date_to", f.to);
  if (f.q) qs.set("q", f.q);
  const events = useAsync(() => api.get<{ total: number; events: Row[] }>(`${base}?${qs}`), [base, qs.toString()]);

  const areaLabel = (k: string | null) => k === "users" ? "Users" : k === "platform" ? "Platform console" : areas.data?.find((a) => a.key === k)?.label ?? k ?? "";
  const who = people.data?.find((p) => p.id === f.user);
  const total = events.data?.total ?? 0;
  const rows = events.data?.events ?? [];
  const verify = async () => { setCheck("busy"); try { setCheck(await api.get(`${base}/verify`)); } catch { setCheck(null); } };

  return (
    <Page title="Audit log" sub={platform ? "Every tenant and every business: each change, login and refused attempt, newest first"
      : who ? `Everything ${who.display_name} did in ${me?.business?.name ?? "this business"}` : `${me?.business?.name ?? "This business"}: every change, login and refused attempt, newest first`}
      actions={<Button kind="quiet" onClick={verify} disabled={check === "busy"}>{check === "busy" ? "Checking…" : "Check for tampering"}</Button>}>
      {check && check !== "busy" && (check.ok
        ? <Notice kind="ok">Intact: {check.checked} entries checked, none edited or removed.</Notice>
        : <Notice kind="error">Entry #{check.broken_at?.seq} ({dt(check.broken_at?.at)}) was edited, or one next to it was removed, after it was written. {check.checked} entries before it are intact.</Notice>)}
      <div className="card p-4 mb-4 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        {platform ? <>
          <Field label="Tenant"><select className={inputCls} value={f.tenant} onChange={(e) => set("tenant", e.target.value)}>
            <option value="">All tenants</option>
            {places.data?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            <option value="none">No tenant (console, unknown logins)</option>
          </select></Field>
          <Field label="Business"><select className={inputCls} value={f.business} onChange={(e) => set("business", e.target.value)}>
            <option value="">All businesses</option>
            {places.data?.filter((t) => !f.tenant || t.id === f.tenant).map((t) => f.tenant
              ? t.businesses.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)
              : <optgroup key={t.id} label={t.name}>{t.businesses.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</optgroup>)}
            <option value="none">No business</option>
          </select></Field>
        </> : <Field label="Person"><select className={inputCls} value={f.user} onChange={(e) => set("user", e.target.value)}>
          <option value="">Everyone</option>
          {people.data?.map((p) => <option key={p.id} value={p.id}>{p.display_name}{p.deleted ? " (deleted)" : p.is_active ? "" : " (off)"}</option>)}
        </select></Field>}
        <Field label="Area"><select className={inputCls} value={f.area} onChange={(e) => set("area", e.target.value)}>
          <option value="">All</option>
          {areas.data?.map((a) => <option key={a.key} value={a.key}>{a.label}</option>)}
          <option value="users">Users</option>
          {platform && <option value="platform">Platform console</option>}
        </select></Field>
        <Field label="What"><select className={inputCls} value={f.action} onChange={(e) => set("action", e.target.value)}>
          <option value="">All</option>
          {Object.entries(ACTIONS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select></Field>
        <Field label="From"><input type="date" className={inputCls} value={f.from} onChange={(e) => set("from", e.target.value)} /></Field>
        <Field label="To"><input type="date" className={inputCls} value={f.to} onChange={(e) => set("to", e.target.value)} /></Field>
        <Field label={platform ? "Search path or login" : "Search path"}><input className={inputCls} placeholder="counts, recipes…" defaultValue={f.q} onKeyDown={(e) => { if (e.key === "Enter") set("q", e.currentTarget.value.trim()); }}
          onBlur={(e) => { if (e.currentTarget.value.trim() !== f.q) set("q", e.currentTarget.value.trim()); }} /></Field>
      </div>
      {events.loading && !events.data && <Loading />}
      {events.error && <Notice kind="error">{events.error}</Notice>}
      {events.data && rows.length === 0 && <Empty>Nothing recorded for these filters.</Empty>}
      {rows.length > 0 && (
        <div className="table-box"><table className="grid">
          <thead><tr><th>When</th>{platform && <th>Tenant · business</th>}<th>Who</th><th>What</th><th>Area</th><th className="r">Result</th><th /></tr></thead>
          <tbody>{rows.map((e) => (
            <Fragment key={e.id}>
              <tr>
                <td className="whitespace-nowrap text-sm">{dt(e.at)}</td>
                {platform && <td className="text-sm">{e.tenant ? <>{e.tenant}{e.business && <span className="text-muted"> · {e.business}</span>}</> : <Nil />}</td>}
                <td className="text-sm">{e.username ?? <Nil />}</td>
                <td className="text-sm"><Badge e={e} />{e.changes > 0 && <span className="text-xs text-faint"> · {e.changes} row{e.changes === 1 ? "" : "s"}</span>}</td>
                <td className="text-sm text-muted">{areaLabel(e.area)}</td>
                <td className={`r mono text-xs ${e.status && e.status >= 400 ? "text-danger" : "text-muted"}`}>{e.status ?? ""}</td>
                <td className="r whitespace-nowrap">{e.link && <Link className="text-sm text-muted hover:text-ink underline mr-3" to={e.link}>Open</Link>}{(e.detail || e.ip || e.changes > 0) && <button className="text-sm text-muted hover:text-ink underline" onClick={() => setOpen(open === e.id ? null : e.id)}>{open === e.id ? "Hide" : "Details"}</button>}</td>
              </tr>
              {open === e.id && (
                <tr className="bg-raised/40"><td colSpan={platform ? 7 : 6}>
                  <div className="text-xs text-muted mb-2"><span className="mono break-all">{e.method} {e.path}</span>{e.ip && <> · from {e.ip}</>}</div>
                  {e.changes > 0 && <Changes id={e.id} base={base} />}
                  {e.detail && <details className="text-xs" open={e.changes === 0}><summary className="text-muted cursor-pointer">Request</summary>
                    <pre className="mono whitespace-pre-wrap break-all max-h-80 overflow-auto mt-1">{JSON.stringify(e.detail, null, 2)}</pre></details>}
                </td></tr>)}
            </Fragment>))}
          </tbody>
        </table></div>
      )}
      {total > PAGE && (
        <div className="flex items-center gap-3 mt-4 text-sm">
          <Button kind="quiet" disabled={page === 0} onClick={() => setPage(page - 1)}>Newer</Button>
          <span className="text-muted">{page * PAGE + 1} to {Math.min(total, (page + 1) * PAGE)} of {total}</span>
          <Button kind="quiet" disabled={(page + 1) * PAGE >= total} onClick={() => setPage(page + 1)}>Older</Button>
        </div>)}
      <p className="text-sm text-muted mt-4 max-w-3xl">Passwords, PINs and keys are replaced with ***. Uploads show the file name only. Entries can’t be edited or deleted from the portal; old ones are removed after the retention period{platform ? " under Settings" : " the platform sets"}.
        {!platform && <> This is the log of {me?.business?.name ?? "this business"} only; switch business to read another one’s.{me?.is_admin ? " As an admin you also see entries that belong to no business, such as failed logins." : ""}</>}</p>
    </Page>
  );
}

function Badge({ e }: { e: AuditEvent }) {
  const bad = e.action === "denied" || e.action === "login_failed" || e.action === "login_blocked" || (e.status ?? 0) >= 400;
  return <span className={bad ? "text-danger" : e.action === "change" ? "text-ink" : "text-muted"}>{e.label}{e.action === "change" && (e.status ?? 0) >= 400 ? " (failed)" : ""}</span>;
}

const show = (v: unknown) => v === null || v === undefined ? "empty" : typeof v === "object" ? JSON.stringify(v) : String(v);
const OPS: Record<string, string> = { insert: "Added", update: "Changed", delete: "Deleted", bulk_update: "Bulk change", bulk_delete: "Bulk delete" };

/** What the request changed, field by field. */
function Changes({ id, base }: { id: string; base: string }) {
  const c = useAsync(() => api.get<AuditChange[]>(`${base}/${id}/changes`), [id]);
  if (c.error) return <Notice kind="error">{c.error}</Notice>;
  if (!c.data) return <div className="text-xs text-muted mb-2">Loading…</div>;
  return (
    <div className="mb-3 flex flex-col gap-2 max-h-96 overflow-auto">
      {c.data.map((r, i) => {
        const fields = Object.entries(r.changes ?? {});
        const pairs = fields.every(([, v]) => Array.isArray(v) && v.length === 2);
        return (
          <div key={i} className="text-xs">
            <div className="text-muted mb-0.5">{OPS[r.op] ?? r.op} <span className="mono">{r.table}</span>{r.row_id && <span className="mono text-faint"> {r.row_id}</span>}</div>
            {pairs ? <table className="mono"><tbody>{fields.map(([k, v]) => {
              const [before, after] = v as [unknown, unknown];
              return <tr key={k}><td className="pr-3 text-muted align-top">{k}</td>
                <td className="pr-2 align-top break-all text-danger">{r.op === "insert" ? "" : show(before)}</td>
                <td className="align-top break-all text-ok">{r.op === "delete" ? "" : `${r.op === "insert" ? "" : "to "}${show(after)}`}</td></tr>;
            })}</tbody></table> : <div className="mono break-all">{fields.map(([k, v]) => `${k}: ${show(v)}`).join(" · ")}</div>}
          </div>);
      })}
    </div>
  );
}
