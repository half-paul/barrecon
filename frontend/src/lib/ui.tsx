import { type ReactNode, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AreaScope, useReadOnly } from "./auth";
import { GLOSSARY, type Term } from "./glossary";

/** Primary and danger buttons change things, so they switch off on a view-only page. A quiet button that changes things says so with `edit`. */
export function Button({ kind = "primary", className = "", edit, ...p }: React.ButtonHTMLAttributes<HTMLButtonElement> & { kind?: "primary" | "quiet" | "danger"; edit?: boolean }) {
  const blocked = useReadOnly() && (edit || kind !== "quiet");
  const base = "ctl inline-flex items-center justify-center gap-1 h-9 px-3.5 rounded-control text-sm font-medium whitespace-nowrap disabled:opacity-55 disabled:cursor-not-allowed";
  const k = kind === "primary" ? "bg-accent text-accent-ink font-semibold hover:bg-accent-deep" : kind === "danger" ? "bg-danger-soft text-danger hover:brightness-125" : "border border-line-strong text-ink hover:bg-raised";
  return <button className={`${base} ${k} ${className}`} {...p} disabled={p.disabled || blocked} title={blocked ? "View only" : p.title} />;
}

/** A small "?" that explains a term. Opens on hover, focus or tap; the bubble lives in a portal so a scrolling table can’t clip it. */
export function Help({ term, text }: { term?: Term; text?: string }) {
  const body = text ?? (term ? GLOSSARY[term] : "");
  const [pos, setPos] = useState<React.CSSProperties | null>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const id = useId();
  const open = () => {
    const r = btn.current?.getBoundingClientRect();
    if (!r) return;
    const w = Math.min(288, window.innerWidth - 32);
    const left = Math.max(16, Math.min(r.left + r.width / 2 - w / 2, window.innerWidth - w - 16));
    const up = window.innerHeight - r.bottom < 140;
    setPos({ position: "fixed", left, width: w, ...(up ? { bottom: window.innerHeight - r.top + 6 } : { top: r.bottom + 6 }) });
  };
  useEffect(() => {
    if (!pos) return;
    const close = () => setPos(null);
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    window.addEventListener("scroll", close, true);
    window.addEventListener("keydown", key);
    return () => { window.removeEventListener("scroll", close, true); window.removeEventListener("keydown", key); };
  }, [pos]);
  return (
    <>
      <button ref={btn} type="button" aria-label={`What is this? ${body}`} aria-describedby={pos ? id : undefined}
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); if (pos) setPos(null); else open(); }}
        onMouseEnter={open} onMouseLeave={() => setPos(null)} onFocus={open} onBlur={() => setPos(null)}
        className="inline-grid place-items-center w-4 h-4 ml-1 align-text-top rounded-full border border-line-strong text-[10px] leading-none font-semibold text-muted hover:text-ink hover:border-muted normal-case">?</button>
      {pos && createPortal(
        <div id={id} role="tooltip" style={pos} className="z-50 rounded-control border border-line bg-surface shadow-lg px-3 py-2 text-xs font-normal text-ink text-left normal-case whitespace-normal">{body}</div>,
        document.body)}
    </>
  );
}

export function Field({ label, children, hint }: { label: ReactNode; children: ReactNode; hint?: string }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-muted">{label}</span>
      {children}
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </label>
  );
}

export const inputCls = "ctl h-9 px-2.5 border border-edge rounded-control bg-paper text-ink hover:border-muted disabled:opacity-55";

/** An empty cell: nothing to see, but a screen reader still hears that it is empty. */
export const Nil = () => <span className="sr-only">none</span>;

/** A text box with a list to pick from. Unlike <datalist>, a box that already holds a pick lists every option again
 *  when tapped (a datalist filters down to the one already chosen), and it works the same on phones.
 *  Typing filters by every word; free text is still allowed. */
export function Combobox({ value, onChange, options, placeholder, className = "", "aria-label": ariaLabel }:
  { value: string; onChange: (v: string) => void; options: string[]; placeholder?: string; className?: string; "aria-label"?: string }) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState(false); // filter only once typing starts, so a filled box still lists everything
  const [sel, setSel] = useState(-1);
  const [pos, setPos] = useState<React.CSSProperties>({});
  const box = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const id = useId();
  const words = typed ? value.toLowerCase().split(/\s+/).filter(Boolean) : [];
  const shown = options.filter((o) => words.every((w) => o.toLowerCase().includes(w)));

  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => { if (!box.current?.contains(e.target as Node) && !list.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [open]);
  // the list lives in a portal at fixed position, so a scrolling table (overflow: auto) can’t clip it;
  // it follows the box on scroll/resize and opens upward when there’s no room below
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const r = input.current?.getBoundingClientRect();
      if (!r) return;
      const below = window.innerHeight - r.bottom - 8, above = r.top - 8;
      const up = below < 160 && above > below;
      setPos({ position: "fixed", left: r.left, width: r.width, maxHeight: Math.min(256, up ? above : below),
        ...(up ? { bottom: window.innerHeight - r.top + 4 } : { top: r.bottom + 4 }) });
    };
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => { window.removeEventListener("scroll", place, true); window.removeEventListener("resize", place); };
  }, [open]);
  // keep the highlighted option, or else the current pick, in view
  useEffect(() => {
    if (!open || !list.current) return;
    const i = sel >= 0 ? sel : shown.indexOf(value);
    (list.current.children[i] as HTMLElement | undefined)?.scrollIntoView({ block: "nearest" });
  }, [open, sel]); // eslint-disable-line react-hooks/exhaustive-deps

  const show = () => { setOpen(true); setTyped(false); setSel(-1); };
  const pick = (o: string) => { onChange(o); setOpen(false); setTyped(false); setSel(-1); };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); if (!open) show(); else setSel((x) => Math.min(shown.length - 1, x + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel((x) => Math.max(0, x - 1)); }
    else if (e.key === "Enter" && open && sel >= 0 && shown[sel]) { e.preventDefault(); pick(shown[sel]); }
    else if (e.key === "Escape" && open) { e.preventDefault(); e.stopPropagation(); setOpen(false); }
    else if (e.key === "Tab") setOpen(false);
  };

  return (
    <div ref={box} className={`relative ${className}`}>
      <input ref={input} role="combobox" aria-label={ariaLabel} aria-expanded={open} aria-controls={id} aria-autocomplete="list"
        aria-activedescendant={open && sel >= 0 ? `${id}-${sel}` : undefined} autoComplete="off"
        className={`${inputCls} w-full ${value ? "pr-8" : ""}`} value={value} placeholder={placeholder}
        onFocus={show} onClick={() => { if (!open) show(); }} onKeyDown={onKey}
        onChange={(e) => { onChange(e.target.value); setTyped(true); setOpen(true); setSel(-1); }} />
      {value && (
        <button type="button" tabIndex={-1} aria-label="Clear" onClick={() => { onChange(""); show(); input.current?.focus(); }}
          className="absolute right-1 top-1/2 -translate-y-1/2 w-7 h-7 grid place-items-center text-muted hover:text-ink">✕</button>)}
      {open && createPortal(
        <ul ref={list} id={id} role="listbox" style={pos} className="z-50 overflow-y-auto rounded-control border border-line bg-surface shadow-lg py-1">
          {shown.length === 0 && <li className="px-3 py-2 text-sm text-muted">Nothing matches.</li>}
          {shown.map((o, i) => (
            // mousedown would blur the box before the click lands
            <li key={o} id={`${id}-${i}`} role="option" aria-selected={o === value} onMouseDown={(e) => e.preventDefault()} onClick={() => pick(o)}
              onMouseEnter={() => setSel(i)}
              className={`px-3 py-2.5 sm:py-1.5 text-sm cursor-pointer ${i === sel ? "bg-raised" : ""} ${o === value ? "text-accent font-medium" : ""}`}>{o}</li>))}
        </ul>, document.body)}
    </div>
  );
}

export function Page({ title, sub, actions, children, area }: { title: string; sub?: ReactNode; actions?: ReactNode; children: ReactNode; area?: string }) {
  const viewOnly = useReadOnly(area ?? "") && !!area;
  return (
    <AreaScope area={area}>
    <div className="px-4 sm:px-7 py-5 sm:py-7 max-w-[1280px] mx-auto">
      <div className="flex items-end justify-between mb-5 sm:mb-6 gap-x-6 gap-y-3 flex-wrap">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <h1 className="text-2xl font-semibold leading-tight max-w-none">{title}</h1>
            {viewOnly && <span className="text-xs rounded-chip border border-line-strong px-2 py-0.5 text-muted">View only</span>}
          </div>
          {sub && <div className="text-muted mt-1">{sub}</div>}
        </div>
        <div className="flex gap-2 items-center flex-wrap">{actions}</div>
      </div>
      {children}
    </div>
    </AreaScope>
  );
}

export function Notice({ kind = "info", children }: { kind?: "info" | "error" | "ok"; children: ReactNode }) {
  const c = kind === "error" ? "bg-danger-soft text-danger border-danger/30" : kind === "ok" ? "bg-ok-soft text-ok border-ok/30" : "bg-amber-soft text-ink border-accent/30";
  return <div className={`rounded-control border px-3 py-2 text-sm mb-4 ${c}`}>{children}</div>;
}

/** Placeholder rows while a page’s data loads, so an empty page is never mistaken for "no data". */
export function Loading({ rows = 6, label = "Loading…" }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-live="polite" className="card p-4 mb-4">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="h-4 my-3 rounded bg-raised animate-pulse motion-reduce:animate-none" style={{ width: `${90 - ((i * 17) % 35)}%` }} />))}
    </div>
  );
}

/** The result of an action, pinned to the bottom of the screen so it is seen wherever the button was.
 *  Confirmations fade after 10 s; errors stay until closed. */
export function Flash({ kind, children, onClose }: { kind: "ok" | "error"; children: ReactNode; onClose: () => void }) {
  useEffect(() => {
    if (kind !== "ok") return;
    const t = window.setTimeout(onClose, 10000);
    return () => window.clearTimeout(t);
  }, [kind, children]); // eslint-disable-line react-hooks/exhaustive-deps
  const c = kind === "error" ? "bg-danger-soft text-danger border-danger/40" : "bg-ok-soft text-ok border-ok/40";
  return (
    <div role={kind === "error" ? "alert" : "status"} className={`fixed z-50 bottom-4 inset-x-4 sm:left-auto sm:right-6 sm:max-w-md rounded-control border shadow-lg px-4 py-3 text-sm flex gap-3 items-start ${c}`}>
      <div className="flex-1 min-w-0 break-words">{children}</div>
      <button type="button" onClick={onClose} aria-label="Close message" className="shrink-0 -m-1 p-1 opacity-70 hover:opacity-100">✕</button>
    </div>
  );
}

/** A photo shown full size over the page. Esc, the ✕ or a tap outside the photo closes it. */
export function Lightbox({ src, alt, caption, onClose }: { src: string; alt: string; caption?: ReactNode; onClose: () => void }) {
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", key);
    return () => { window.removeEventListener("keydown", key); document.body.style.overflow = overflow; };
  }, [onClose]);
  return (
    <div role="dialog" aria-modal="true" aria-label={alt} onClick={onClose}
      className="fixed inset-0 z-50 bg-scrim/90 flex flex-col items-center justify-center p-3 sm:p-8">
      <button type="button" onClick={onClose} aria-label="Close photo" autoFocus
        className="absolute top-2 right-2 sm:top-4 sm:right-4 w-11 h-11 rounded-full bg-scrim/60 text-on-scrim text-xl hover:bg-scrim/90">✕</button>
      <img src={src} alt={alt} onClick={(e) => e.stopPropagation()}
        className="max-w-full max-h-[calc(100dvh-6rem)] object-contain rounded-control shadow-2xl" />
      {caption && <div className="mt-3 text-sm text-on-scrim text-center" onClick={(e) => e.stopPropagation()}>{caption}</div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="card text-muted px-5 py-8">{children}</div>;
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [state, set] = useState<{ data?: T; error?: string; loading: boolean }>({ loading: true });
  const run = () => { set((s) => ({ ...s, loading: true })); fn().then((data) => set({ data, loading: false })).catch((e) => set({ error: String(e.message ?? e), loading: false })); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(run, deps);
  return { ...state, reload: run };
}

export function FileButton({ label, accept = ".xlsx", onFile, busy }: { label: string; accept?: string; onFile: (f: File) => void; busy?: boolean }) {
  const blocked = useReadOnly();
  return (
    <label title={blocked ? "View only" : undefined} className={`ctl inline-flex items-center h-9 px-3.5 rounded-control text-sm font-medium whitespace-nowrap border border-line-strong cursor-pointer hover:bg-raised ${busy || blocked ? "opacity-55 pointer-events-none" : ""}`}>
      {busy ? "Uploading…" : label}
      <input type="file" accept={accept} className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.currentTarget.value = ""; }} />
    </label>
  );
}

/** A bottle (or keg) silhouette filled to the partial amount. The one visual device in the app. */
export function Bottle({ fraction, keg }: { fraction: number; keg?: boolean }) {
  const f = Math.max(0, Math.min(1, isNaN(fraction) ? 0 : fraction));
  return (
    <svg width="14" height="28" viewBox="0 0 14 28" aria-hidden className="shrink-0">
      <defs><clipPath id={keg ? "kegclip" : "bclip"}>{keg ? <rect x="1" y="2" width="12" height="24" rx="2" /> : <path d="M5 1h4v6c2 1 4 3 4 6v13a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V13c0-3 2-5 4-6z" />}</clipPath></defs>
      <g clipPath={keg ? "url(#kegclip)" : "url(#bclip)"}>
        <rect x="0" y="0" width="14" height="28" fill="var(--color-line)" />
        <rect className="fill" x="0" y={28 - 28 * f} width="14" height={28 * f} fill="var(--color-amber)" />
      </g>
      {keg ? <rect x="1" y="2" width="12" height="24" rx="2" fill="none" stroke="var(--color-muted)" strokeWidth="1.2" />
           : <path d="M5 1h4v6c2 1 4 3 4 6v13a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V13c0-3 2-5 4-6z" fill="none" stroke="var(--color-muted)" strokeWidth="1.2" />}
    </svg>
  );
}
