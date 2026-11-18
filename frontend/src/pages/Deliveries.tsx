import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, type DeliveryDetail, type DeliverySummary, type ImportSummary, dt, n, cents } from "../lib/api";
import { Button, Combobox, Empty, FileButton, Flash, Loading, Nil, Notice, Page, useAsync } from "../lib/ui";

type DeliveredItem = { id: string; sku: string; name: string; pack_name: string | null; is_active: boolean };
const itemLabel = (it: { name: string; sku: string; is_active: boolean }) => `${it.name} · ${it.sku}${it.is_active ? "" : " (inactive)"}`;

// Every delivery, however it came in: a store receipt uploaded, read, reviewed and posted; one entered by hand;
// or rows of the deliveries sheet (uploaded, or typed on the quick sheet), posted as they are saved.

const MAX_SIDE = 2400;

/** Phone photos are often over the 5 MB the reader takes; shrink big ones to a JPEG that still reads well. */
async function shrink(f: File): Promise<File> {
  if (!f.type.startsWith("image/") || (f.size < 4_000_000 && f.type !== "image/heic")) return f;
  try {
    const bmp = await createImageBitmap(f);
    const k = Math.min(1, MAX_SIDE / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * k); c.height = Math.round(bmp.height * k);
    c.getContext("2d")!.drawImage(bmp, 0, 0, c.width, c.height);
    const blob = await new Promise<Blob | null>((ok) => c.toBlob(ok, "image/jpeg", 0.85));
    return blob ? new File([blob], f.name.replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" }) : f;
  } catch { return f; } // the browser can’t decode it: send as is and let the server say why
}

export const statusChip = (s: string) => (
  <span className={`text-xs px-2 py-0.5 rounded-full border ${s === "posted" ? "border-ok/40 text-ok bg-ok-soft" : s === "void" ? "border-line text-muted line-through" : "border-accent/40 bg-amber-soft"}`}>{s}</span>);

export default function Deliveries() {
  const nav = useNavigate();
  const [showVoid, setShowVoid] = useState(false);
  // find deliveries a stock item came in on; kept in the URL so going back from a delivery keeps the search
  const [params, setParams] = useSearchParams();
  const itemId = params.get("item") ?? "";
  const items = useAsync(() => api.get<DeliveredItem[]>("/api/deliveries/items"));
  const byLabel = useMemo(() => new Map((items.data ?? []).map((it) => [itemLabel(it), it])), [items.data]);
  const picked = (items.data ?? []).find((it) => it.id === itemId);
  const [itemText, setItemText] = useState("");
  useEffect(() => { if (picked) setItemText(itemLabel(picked)); }, [picked?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const onItemText = (v: string) => {
    setItemText(v);
    const it = byLabel.get(v);
    if (it) setParams({ item: it.id }, { replace: true });
    else if (itemId) setParams({}, { replace: true });
  };
  const list = useAsync(() => api.get<DeliverySummary[]>(
    `/api/deliveries?include_void=${showVoid}${itemId ? `&item_id=${encodeURIComponent(itemId)}` : ""}`), [showVoid, itemId]);
  const reading = useAsync(() => api.get<{ enabled: boolean }>("/api/deliveries/reading"));
  const [busy, setBusy] = useState<"" | "upload" | "manual">("");
  const [msg, setMsg] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [sheetBusy, setSheetBusy] = useState(false);

  const upload = async (f: File) => {
    setBusy("upload"); setMsg(null);
    try { const d = await api.upload<DeliveryDetail>("/api/deliveries/receipt", await shrink(f)); nav(`/deliveries/${d.id}`); }
    catch (e) { setMsg((e as Error).message); setBusy(""); }
  };
  const manual = async () => {
    setBusy("manual");
    try { const d = await api.post<DeliveryDetail>("/api/deliveries"); nav(`/deliveries/${d.id}`); }
    catch (e) { setMsg((e as Error).message); setBusy(""); }
  };
  const uploadSheet = async (f: File) => {
    setSheetBusy(true); setMsg(null); setOk(null);
    try {
      const r = await api.upload<ImportSummary>("/api/movements/import", f);
      if (r.status === "ok") setOk(`${f.name}: ${r.inserted} rows added, ${r.updated} updated, ${r.duplicate} already recorded.`);
      else setMsg(`Nothing imported from ${f.name}. ${r.rows.map((x) => (x.row ? `Row ${x.row}: ` : "") + x.message.replace(/^row \d+: /, "")).join(" · ")}`);
      list.reload();
    } catch (e) { setMsg((e as Error).message); } finally { setSheetBusy(false); }
  };
  const rows = list.data ?? [];
  const drafts = rows.filter((d) => d.status === "draft").length;

  return (
    <Page area="deliveries" title="Deliveries" sub={drafts ? `${drafts} to review` : undefined} actions={<>
      <a className="text-sm underline" href="/api/movements/template.xlsx">Download sheet</a>
      <FileButton label="Upload sheet" onFile={uploadSheet} busy={sheetBusy} />
      <Link className="text-sm underline" to="/deliveries/sheet">Quick sheet</Link>
      <Button kind="quiet" edit onClick={manual} disabled={!!busy}>Enter without receipt</Button>
      <label className={`inline-flex items-center px-3.5 py-1.5 rounded-control text-sm bg-accent text-accent-ink font-semibold hover:bg-accent-deep cursor-pointer ${busy ? "opacity-50 pointer-events-none" : ""}`}>
        {busy === "upload" ? (reading.data?.enabled ? "Reading receipt…" : "Uploading…") : "Upload receipt"}
        <input type="file" accept="image/*,application/pdf" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.currentTarget.value = ""; }} />
      </label>
    </>}>
      {msg && <Flash kind="error" onClose={() => setMsg(null)}>{msg}</Flash>}
      {ok && <Flash kind="ok" onClose={() => setOk(null)}>{ok}</Flash>}
      {reading.data && !reading.data.enabled && <Notice>Receipt reading is off: set <code>ANTHROPIC_API_KEY</code> on the server to have receipts read for you. You can still upload a receipt and type its lines next to it.</Notice>}
      {busy === "upload" && reading.data?.enabled && <Notice>Reading the receipt: store, date and time, and every line. This takes up to half a minute.</Notice>}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 mb-3">
        <Combobox className="w-full sm:w-96" aria-label="Find deliveries of a stock item" placeholder="Find deliveries of a stock item…"
          options={[...byLabel.keys()]} value={itemText} onChange={onItemText} />
        <label className="text-sm flex items-center gap-2"><input type="checkbox" checked={showVoid} onChange={(e) => setShowVoid(e.target.checked)} /> show voided</label>
      </div>
      {list.loading && !list.data && <Loading />}
      {list.error && <Notice kind="error">{list.error}</Notice>}
      {list.data && rows.length === 0 && (itemId
        ? <Empty>{picked ? picked.name : "This item"} is not on any {showVoid ? "" : "current "}delivery.</Empty>
        : <Empty>No deliveries yet. Upload a photo or PDF of a receipt and it is read into a delivery for you to check.</Empty>)}
      {itemId && rows.length > 0 && (
        <p className="text-sm text-muted mb-2">
          {rows.length} deliver{rows.length === 1 ? "y" : "ies"} with {picked?.name ?? "this item"}
          {" · "}{n(rows.filter((d) => d.status !== "void").reduce((s, d) => s + Number(d.item_match?.packs ?? 0), 0), 2)} {picked?.pack_name ?? "pack"}s
          {" · "}${n(rows.filter((d) => d.status !== "void").reduce((s, d) => s + Number(d.item_match?.spend ?? 0), 0), 2)}
          {showVoid && " (not counting voided)"}
        </p>)}
      {rows.length > 0 && (
        <div className="table-box"><table className="grid">
          <thead><tr><th>Bought</th><th>Store</th><th className="max-sm:hidden">Receipt</th>{itemId && <th className="r">This item</th>}<th className="r">Items</th><th className="r">Total</th><th>Status</th><th className="max-sm:hidden">Check</th></tr></thead>
          <tbody>{rows.map((d) => (
            <tr key={d.id} className={`cursor-pointer hover:bg-raised ${d.status === "void" ? "text-muted" : ""}`} onClick={() => nav(`/deliveries/${d.id}`)}>
              <td className="whitespace-nowrap"><Link to={`/deliveries/${d.id}`} onClick={(e) => e.stopPropagation()} className="font-medium">{d.occurred_at ? dt(d.occurred_at) : <span className="text-muted">no date yet</span>}</Link></td>
              <td>{d.store ?? <Nil />}{!d.has_receipt && <span className="block text-xs text-muted">{d.source === "sheet" ? "from sheet" : "no receipt"}</span>}</td>
              <td className="num text-muted max-sm:hidden">{d.receipt_no}</td>
              {itemId && <td className="r num" title={d.item_match?.lines.join("\n")}>
                {n(Number(d.item_match?.packs ?? 0), 2)}{d.item_match?.spend ? <span className="block text-xs text-muted">${n(Number(d.item_match.spend), 2)}</span> : null}
              </td>}
              <td className="r num">{d.item_count}<span className="text-muted"> / {d.line_count}</span></td>
              <td className="r num">{d.total != null ? `$${cents(d.total)}` : d.spend ? <span className="text-muted">${n(d.spend, 2)}</span> : ""}</td>
              <td>{statusChip(d.status)}</td>
              <td className="max-sm:hidden text-xs">
                {d.status === "draft" && d.to_match > 0 && <span className="text-danger mr-2">{d.to_match} to match</span>}
                {!d.totals_ok && <span className="text-danger mr-2">totals off</span>}
                {d.price_alerts > 0 && <span className="text-accent mr-2">{d.price_alerts} price change{d.price_alerts === 1 ? "" : "s"}</span>}
              </td>
            </tr>))}</tbody>
        </table></div>
      )}
    </Page>
  );
}
