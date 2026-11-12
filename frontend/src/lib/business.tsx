import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { type Business, announceSwitch, api, logoUrl } from "./api";
import { useMe } from "./auth";

/** A business's logo, or its initials in a tile when it has none. */
export function BusinessMark({ business, size = 28 }: { business: Pick<Business, "id" | "name" | "logo_at">; size?: number }) {
  const src = logoUrl(business);
  if (src) return <img src={src} alt="" style={{ height: size, maxWidth: size * 2.6 }} className="object-contain rounded-[4px] shrink-0" />;
  const initials = business.name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("");
  return (
    <span aria-hidden style={{ width: size, height: size, fontSize: size * 0.42 }}
      className="grid place-items-center rounded-control bg-raised border border-line text-accent font-semibold shrink-0">{initials || "?"}</span>
  );
}

/** Work in another business: the server remembers it in the session, then the whole page starts again there. */
export async function switchBusiness(id: string) {
  await api.post("/api/auth/business", { business_id: id });
  announceSwitch();
  window.location.assign("/");
}

/** The business this page belongs to: logo and name, on every page. With more than one to work in, it opens the list. */
export function BusinessSwitcher({ compact }: { compact?: boolean }) {
  const me = useMe();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away); document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);
  const b = me?.business;
  if (!me || !b) return null;
  const many = me.businesses.length > 1;
  // a logo already says which business this is: on a narrow header the name gives way to it (it stays in the tooltip and the list)
  const name = <span className={`font-semibold truncate max-w-36 ${b.logo_at ? "hidden xl:inline" : compact ? "hidden sm:inline" : ""}`}>{b.name}</span>;
  if (!many && !me.is_admin) return <Link to="/" className="flex items-center gap-2 mr-2 min-w-0" title={b.name}><BusinessMark business={b} />{name}</Link>;
  const go = async (id: string) => { setBusy(true); try { await switchBusiness(id); } catch { setBusy(false); setOpen(false); } };
  return (
    <div ref={ref} className="relative mr-2 flex items-center gap-1 min-w-0">
      <Link to="/" className="flex items-center gap-2 min-w-0" title={`${b.name}: overview`}><BusinessMark business={b} />{name}</Link>
      <button type="button" aria-haspopup="menu" aria-expanded={open} aria-label={many ? "Switch business" : "Businesses"} disabled={busy}
        onClick={() => setOpen((v) => !v)} className="p-1.5 rounded-control text-muted hover:text-ink hover:bg-raised">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden><path d="m6 9 6 6 6-6" /></svg>
      </button>
      {open && (
        <div role="menu" className="absolute left-0 top-full z-50 mt-2 w-72 max-w-[calc(100vw-2rem)] rounded-control border border-line bg-surface shadow-lg p-1">
          {me.tenant && <div className="label px-3 pt-2 pb-1 text-xs truncate">{me.tenant.name}</div>}
          {me.businesses.map((x) => (
            <button key={x.id} role="menuitemradio" aria-checked={x.id === b.id} disabled={busy} onClick={() => x.id === b.id ? setOpen(false) : go(x.id)}
              className={`w-full flex items-center gap-3 px-3 py-2 rounded-control text-left text-sm ${x.id === b.id ? "bg-raised font-medium" : "hover:bg-raised"}`}>
              <BusinessMark business={x} size={24} />
              <span className="min-w-0 flex-1"><span className="block truncate">{x.name}</span>{x.city && <span className="block text-xs text-muted truncate">{x.city}</span>}</span>
              {x.id === b.id && <span className="text-xs text-accent">here</span>}
            </button>))}
          {me.is_admin && <Link to="/businesses" role="menuitem" onClick={() => setOpen(false)} className="block px-3 py-2 mt-1 border-t border-line text-sm text-muted hover:text-ink">Manage businesses</Link>}
        </div>)}
    </div>
  );
}
