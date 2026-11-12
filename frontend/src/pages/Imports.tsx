import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type Batch, type Issue, type MenuSync, datasetName, dt, menuText } from "../lib/api";
import { AreaScope, useReadOnly } from "../lib/auth";
import { SoldItems } from "../lib/soldItems";
import { Button, Empty, FileButton, Flash, Notice, Page, useAsync } from "../lib/ui";

type Preview = { tabs: string[]; items: { sku: string; name: string; category: string; last_on_hand: number | null; sku_from_sheet: boolean }[]; count_sessions: { tab: string; week: number; date: string; date_fallback: boolean; lines: number }[]; deliveries: number; review: { code: string; detail: string }[] };
type CataloguePreview = MenuSync & { items: number };

type Applied = { items_created: number; items_updated: number; count_sessions_created: number; deliveries_created: number; deliveries_duplicate: number; review: unknown[] };

export default function Imports() {
  const viewOnly = useReadOnly("imports");
  const batches = useAsync(() => api.get<Batch[]>("/api/imports"));
  const issues = useAsync(() => api.get<Issue[]>("/api/imports/issues"));
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [opts, setOpts] = useState({ counts: true, deliveries: true });
  const [openBatch, setOpenBatch] = useState<string | null>(null);

  const choose = async (f: File) => {
    setBusy(true); setPreview(null); setFile(f);
    try { setPreview(await api.upload<Preview>("/api/imports/legacy/preview", f)); }
    catch (e) { setMsg({ kind: "error", text: (e as Error).message }); } finally { setBusy(false); }
  };
  const apply = async () => {
    if (!file) return;
    setBusy(true);
    try {
      const r = await api.upload<Applied>(`/api/imports/legacy?include_counts=${opts.counts}&include_deliveries=${opts.deliveries}`, file);
      setMsg({ kind: "ok", text: `Imported: ${r.items_created} new items, ${r.items_updated} updated, ${r.count_sessions_created} weekly counts, ${r.deliveries_created} deliveries (${r.deliveries_duplicate} already present). ${r.review.length} things to review below.` });
      setPreview(null); setFile(null); batches.reload(); issues.reload();
    } catch (e) { setMsg({ kind: "error", text: (e as Error).message }); } finally { setBusy(false); }
  };
  const [catFile, setCatFile] = useState<File | null>(null);
  const [catPreview, setCatPreview] = useState<CataloguePreview | null>(null);
  const chooseCatalogue = async (f: File) => {
    setBusy(true); setCatPreview(null); setCatFile(f);
    try { setCatPreview(await api.upload<CataloguePreview>("/api/imports/swype-items/preview", f)); }
    catch (e) { setMsg({ kind: "error", text: (e as Error).message }); setCatFile(null); } finally { setBusy(false); }
  };
  const applyCatalogue = async () => {
    if (!catFile) return;
    setBusy(true);
    try {
      const r = await api.upload<CataloguePreview>("/api/imports/swype-items", catFile);
      setMsg({ kind: "ok", text: `Swype items: ${r.items} in file; ${menuText(r)}.` });
      setCatPreview(null); setCatFile(null); batches.reload();
    } catch (e) { setMsg({ kind: "error", text: (e as Error).message }); batches.reload(); } finally { setBusy(false); }
  };

  const removeBatch = async (b: Batch) => {
    if (!confirm(`Remove this Item Sales import (${b.file_name}) and the sales it brought in? Stock vs sales for those dates will have no sales until they are imported again.`)) return;
    setBusy(true);
    try {
      const r = await api.del<{ deleted: number }>(`/api/imports/${b.id}`);
      setMsg({ kind: "ok", text: r.deleted ? `Removed ${b.file_name}: ${r.deleted} item sales lines deleted.` : `Removed ${b.file_name}. Its sales had already been replaced by a later import of the same dates, so no values changed.` });
      batches.reload();
    } catch (e) { setMsg({ kind: "error", text: (e as Error).message }); } finally { setBusy(false); }
  };
  const resolve = async (i: Issue) => { await api.post(`/api/imports/issues/${i.id}/resolve`); issues.reload(); };

  return (
    <Page area="imports" title="Imports">
      {msg && <Flash kind={msg.kind} onClose={() => setMsg(null)}>{msg.text}</Flash>}
      <section className="card mb-8 p-5">
        <h2 className="font-semibold mb-1">Your existing inventory workbook</h2>
        <p className="text-sm text-muted mb-3 max-w-[75ch]">The monthly "To Order Inventory List" file. Items, par levels and every weekly "On hand" column come in as posted counts; orders that can be read ("2 case", "3 bottles") come in as deliveries; everything else is listed for you to decide. Nothing is written until you confirm the preview. Running it again later is safe: existing counts and deliveries are left alone.</p>
        <FileButton label={file ? `Choose a different file` : "Choose workbook"} onFile={choose} busy={busy} />
        {preview && (
          <div className="mt-4 text-sm">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-3">
              <div><div className="text-muted">Tabs</div><div>{preview.tabs.join(", ")}</div></div>
              <div><div className="text-muted">Items</div><div className="num">{preview.items.length} <span className="text-muted">({preview.items.filter((i) => !i.sku_from_sheet).length} without a SKU)</span></div></div>
              <div><div className="text-muted">Weekly counts</div><div className="num">{preview.count_sessions.length} <span className="text-muted">{preview.count_sessions.some((s) => s.date_fallback) ? "(some dated to month end)" : ""}</span></div></div>
              <div><div className="text-muted">Readable orders</div><div className="num">{preview.deliveries}</div></div>
            </div>
            {preview.review.length > 0 && (
              <details className="mb-3"><summary className="cursor-pointer">{preview.review.length} things that need a look</summary>
                <ul className="list-disc pl-5 mt-1 text-muted">{preview.review.map((r, i) => <li key={i}>{r.detail}</li>)}</ul></details>
            )}
            <div className="flex flex-wrap items-center gap-4">
              <label className="flex items-center gap-1"><input type="checkbox" checked={opts.counts} onChange={(e) => setOpts({ ...opts, counts: e.target.checked })} /> import weekly counts</label>
              <label className="flex items-center gap-1"><input type="checkbox" checked={opts.deliveries} onChange={(e) => setOpts({ ...opts, deliveries: e.target.checked })} /> import readable orders as deliveries</label>
              <Button onClick={apply} disabled={busy}>Import</Button>
            </div>
          </div>
        )}
      </section>

      <Notice>Swype item sales now have their own page: <Link className="underline" to="/sales">Sales</Link>.</Notice>

      <AreaScope area="swype_items"><section className="card mb-8 p-5">
        <h2 className="font-semibold mb-1">Swype item list</h2>
        <p className="text-sm text-muted mb-3 max-w-[75ch]">The CSV from Swype → Import/Export → Export Item. Adds every Swype item # to the Swype menu so it can get a recipe before it first sells. Swype items are not stock items and have their own numbers: nothing here creates or changes a stock item. A recipe is what links a Swype item to the stock items it uses (one or several, each with its own portion). Sales imports add the items they list the same way.</p>
        <FileButton label={catFile ? "Choose a different file" : "Choose item CSV"} accept=".csv" onFile={chooseCatalogue} busy={busy} />
        {catPreview && (
          <div className="mt-4 text-sm">
            <p className="mb-3">{catPreview.items} items in file: {menuText(catPreview)}.</p>
            {catPreview.new_items.length > 0 && (
              <details className="mb-3"><summary className="cursor-pointer">{catPreview.new_items.length} new Swype items</summary>
                <p className="text-muted mt-1">{catPreview.new_items.join(", ")}</p></details>
            )}
            <Button onClick={applyCatalogue} disabled={busy}>Import</Button>
          </div>
        )}
      </section></AreaScope>

      <section className="mb-8">
        <h2 className="font-semibold mb-2">To review</h2>
        {issues.data && issues.data.length === 0 && <Empty>Nothing waiting.</Empty>}
        {issues.data && issues.data.length > 0 && (
          <div className="table-box"><table className="grid">
            <thead><tr><th>What</th><th>Detail</th><th>Raised</th><th></th></tr></thead>
            <tbody>{issues.data.map((i) => (
              <tr key={i.id}><td className="whitespace-nowrap">{i.code.replace(/_/g, " ")}</td><td>{i.detail?.detail}</td><td className="text-muted whitespace-nowrap">{dt(i.created_at)}</td>
                <td className="r"><button hidden={viewOnly} className="text-xs underline" onClick={() => resolve(i)}>done</button></td></tr>))}</tbody>
          </table></div>
        )}
      </section>

      <section>
        <h2 className="font-semibold mb-2">Import history</h2>
        {batches.data && batches.data.length === 0 && <Empty>No imports yet.</Empty>}
        {batches.data && batches.data.length > 0 && (
          <div className="table-box"><table className="grid">
            <thead><tr><th>When</th><th>What</th><th>File</th><th className="r">Rows</th><th className="r">New</th><th className="r">Duplicate</th><th className="r">Errors</th><th>Result</th><th></th></tr></thead>
            <tbody>{batches.data.flatMap((b) => [
              <tr key={b.id}><td className="whitespace-nowrap">{dt(b.imported_at)}</td>
                <td className="whitespace-nowrap">{b.dataset === "item_sales" && b.status === "ok"
                  ? <button type="button" className="underline-offset-2 hover:underline" aria-expanded={openBatch === b.id} onClick={() => setOpenBatch(openBatch === b.id ? null : b.id)}>
                      <span aria-hidden className="inline-block w-4 text-muted">{openBatch === b.id ? "▾" : "▸"}</span>{datasetName(b.dataset)}</button>
                  : datasetName(b.dataset)}</td><td className="text-muted">{b.file_name}</td>
                <td className="r num">{b.rows_received}</td><td className="r num">{b.rows_inserted}</td><td className="r num">{b.rows_duplicate}</td><td className="r num">{b.rows_error}</td>
                <td>{b.status === "ok" ? "imported" : b.status === "removed" ? <span className="text-muted">removed</span> : <span className="text-danger" title={b.error_log?.map((e) => e.message).join("\n")}>rejected</span>}</td>
                <td className="r">{b.dataset === "item_sales" && b.status === "ok" && <Button kind="quiet" edit onClick={() => removeBatch(b)} disabled={busy}>Remove</Button>}</td></tr>,
              ...(openBatch === b.id ? [<tr key={`${b.id}:items`}><td colSpan={9} className="pl-6"><SoldItems url={`/api/imports/${b.id}/items`} /></td></tr>] : []),
            ])}</tbody>
          </table></div>
        )}
      </section>
    </Page>
  );
}
