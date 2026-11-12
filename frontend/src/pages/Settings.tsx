import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Settings } from "../lib/api";
import { useMe } from "../lib/auth";
import { Button, Field, Flash, Loading, Notice, Page, inputCls, useAsync } from "../lib/ui";

const RETENTION: [number, string][] = [[90, "3 months"], [180, "6 months"], [365, "1 year"], [730, "2 years"], [1825, "5 years"], [0, "Forever"]];
const days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export default function SettingsPage() {
  const me = useMe();
  const admin = !!me?.is_admin, business = me?.business;
  const s = useAsync(() => api.get<Settings>("/api/settings"));
  const [form, setForm] = useState<Settings | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { if (s.data) setForm(s.data); }, [s.data]);
  if (!form) return <Page title="Settings">{s.error ? <Notice kind="error">{s.error}</Notice> : <Loading rows={4} />}</Page>;
  // the variance chart’s item picks are saved from the Variance page; don’t overwrite them from here
  // how long the audit log is kept covers every tenant, so it is set in the platform console and only shown here
  const save = async () => {
    const { variance_chart_items: _c, variance_view_added: _a, variance_view_removed: _r, audit_retention_days: _d, ...rest } = form; await api.put("/api/settings", rest); setMsg("Saved."); s.reload();
  };
  const oz = Number(form.oz_ml);
  return (
    <Page area="settings" title="Settings" sub={business ? `For ${business.name}. Each business has its own settings.` : undefined}>
      {msg && <Flash kind="ok" onClose={() => setMsg(null)}>{msg}</Flash>}
      <div className="card grid grid-cols-1 sm:grid-cols-2 gap-5 max-w-3xl p-5">
        <Field label="One ounce is" hint={`Canada’s standard is the imperial ounce (28.41 ml): 26 oz per 750 ml bottle, as your workbook assumes. US-marked jiggers pour 29.57 ml.`}>
          <select className={inputCls} value={oz.toFixed(4)} onChange={(e) => setForm({ ...form, oz_ml: e.target.value })}>
            <option value="28.4131">28.41 ml (Canadian / imperial)</option>
            <option value="29.5735">29.57 ml (US)</option>
          </select>
        </Field>
        <Field label="Day of the weekly count" hint="Used to date counts imported from the old workbook and for the reminder.">
          <select className={inputCls} value={form.count_weekday} onChange={(e) => setForm({ ...form, count_weekday: Number(e.target.value) })}>{days.map((d, i) => <option key={d} value={i}>{d}</option>)}</select>
        </Field>
        <Field label="Flag differences above (%)" hint="Items whose usage differs from sales × recipe by this much are flagged on the Stock vs sales page.">
          <input className={`${inputCls} num`} value={form.variance_threshold_pct} onChange={(e) => setForm({ ...form, variance_threshold_pct: e.target.value })} />
        </Field>
        <Field label="Weekly count reminder">
          <label className="flex items-center gap-2 py-2"><input type="checkbox" checked={form.count_reminder_enabled} onChange={(e) => setForm({ ...form, count_reminder_enabled: e.target.checked })} /> show on the overview when a count is overdue</label>
        </Field>
        <Field label="Nightly Swype sales import" hint="Pulls every full day not imported yet, one day at a time. Needs this business’s Swype login (Setup, Businesses).">
          <div className="flex flex-wrap items-center gap-2 py-1">
            <label className="flex items-center gap-2"><input type="checkbox" checked={form.swype_auto_import} onChange={(e) => setForm({ ...form, swype_auto_import: e.target.checked })} /> import every night at</label>
            <select className={inputCls} aria-label="Hour of the nightly import" value={form.swype_auto_import_hour} disabled={!form.swype_auto_import}
              onChange={(e) => setForm({ ...form, swype_auto_import_hour: Number(e.target.value) })}>
              {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}</select>
          </div>
        </Field>
        <Field label="Audit log kept for" hint="Older entries are removed once a day. Set for the whole platform, not per business.">
          <div className="py-2">{RETENTION.find(([v]) => v === form.audit_retention_days)?.[1] ?? `${form.audit_retention_days} days`}</div>
        </Field>
        <Field label="Log out after (minutes without activity)" hint={`For the shared bar PC. 0 turns it off (sessions then end after 12 hours).${admin ? "" : " Only an admin can change this."}`}>
          <input className={`${inputCls} num`} inputMode="numeric" disabled={!admin} value={form.idle_logout_minutes} onChange={(e) => setForm({ ...form, idle_logout_minutes: Number(e.target.value.replace(/\D/g, "")) })} />
        </Field>
        <div className="col-span-full"><Button onClick={save}>Save settings</Button></div>
      </div>
      <div className="card max-w-3xl p-5 mt-6 flex flex-wrap items-center justify-between gap-3">
        <div><h2 className="font-semibold">Item groups</h2><p className="text-sm text-muted">Add, rename or remove groups, and set each group’s counted unit (ml, each) and pack size.</p></div>
        <Link className="text-sm underline" to="/items/groups">Manage item groups</Link>
      </div>
    </Page>
  );
}
