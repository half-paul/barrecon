import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type CountDetail, type CountSession, type ImportSummary, dt } from "../lib/api";
import { useReadOnly } from "../lib/auth";
import { Button, Empty, FileButton, Flash, Loading, Notice, Page, inputCls, useAsync } from "../lib/ui";

export default function Counts() {
  const viewOnly = useReadOnly("counts");
  const nav = useNavigate();
  const [showAll, setShowAll] = useState(false);
  const sessions = useAsync(() => api.get<CountSession[]>(`/api/counts?include_superseded=${showAll}`), [showAll]);
  const [when, setWhen] = useState(() => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16));
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const start = async () => {
    try { const s = await api.post<CountDetail>("/api/counts", { counted_at: new Date(when).toISOString() }); nav(`/counts/${s.id}`); }
    catch (e) { setMsg({ kind: "error", text: (e as Error).message }); }
  };
  const upload = async (f: File) => {
    setBusy(true);
    try {
      const r = await api.upload<ImportSummary>("/api/counts/import", f);
      setMsg(r.status === "ok" ? { kind: "ok", text: `Posted ${r.sessions_created.length} count(s) from ${f.name}, ${r.inserted} lines.` }
                               : { kind: "error", text: `Nothing imported. ${r.rows.map((x) => x.message).join(" · ")}` });
      sessions.reload();
    } catch (e) { setMsg({ kind: "error", text: (e as Error).message }); } finally { setBusy(false); }
  };
  const remove = async (s: CountSession) => {
    const posted = s.status !== "draft";
    if (!confirm(posted
      ? `Delete the posted count from ${dt(s.counted_at)} (${s.line_count} item${s.line_count === 1 ? "" : "s"})? Stock vs sales and on-hand will stop using it. It stays in the records, marked deleted, under "show superseded and deleted counts".`
      : `Delete the draft count from ${dt(s.counted_at)}?`)) return;
    try {
      await api.del(`/api/counts/${s.id}`);
      setMsg({ kind: "ok", text: `Deleted the count from ${dt(s.counted_at)}` });
      sessions.reload();
    } catch (e) { setMsg({ kind: "error", text: (e as Error).message }); }
  };

  return (
    <Page area="counts" title="Counts" actions={<>
      <a className="text-sm underline" href="/api/counts/template.xlsx">Download count sheet</a>
      <FileButton label="Upload count sheet" onFile={upload} busy={busy} />
      <label className="flex items-center gap-2 text-sm" title="When the stock was counted. Pick an earlier date to back-date a count.">
        <span className="text-muted">Counted at</span>
        <input type="datetime-local" className={inputCls} value={when} onChange={(e) => setWhen(e.target.value)} />
      </label>
      <Button onClick={start} disabled={!when}>Start a count</Button>
    </>}>
      {msg && <Flash kind={msg.kind} onClose={() => setMsg(null)}>{msg.text}</Flash>}
      <label className="text-sm flex items-center gap-1 mb-3"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> show superseded and deleted counts</label>
      {sessions.loading && !sessions.data && !sessions.error && <Loading />}
      {sessions.error && <Notice kind="error">{sessions.error}</Notice>}
      {sessions.data && sessions.data.length === 0 && <Empty>No counts yet. Start one, or upload a filled count sheet.</Empty>}
      {sessions.data && sessions.data.length > 0 && (
        <div className="table-box"><table className="grid">
          <thead><tr><th>Counted at</th><th>Status</th><th className="max-sm:hidden">Source</th><th className="r">Items</th><th className="max-sm:hidden">Note</th><th></th></tr></thead>
          <tbody>{sessions.data.map((s) => (
            <tr key={s.id} className={s.status === "superseded" || s.status === "deleted" ? "text-muted" : ""}>
              <td><Link className="underline" to={`/counts/${s.id}`}>{dt(s.counted_at)}</Link></td>
              <td>{s.status === "draft" ? <span className="text-amber font-medium">draft</span> : s.status}</td>
              <td className="max-sm:hidden">{s.source}</td><td className="r num">{s.line_count}</td><td className="text-muted max-sm:hidden">{s.note}</td>
              <td className="r">{(s.status === "draft" || s.status === "posted") && !viewOnly && <button className="text-danger text-xs" onClick={() => remove(s)}>delete</button>}</td>
            </tr>))}</tbody>
        </table></div>
      )}
    </Page>
  );
}
