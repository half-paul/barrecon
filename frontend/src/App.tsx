import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, type CountDetail, type CountSession, type Me } from "./lib/api";
import { MeContext, PAGE_AREA, can, useMe } from "./lib/auth";
import { type CountBar, CountBarContext } from "./lib/countbar";
import { type Command, CommandPalette, SearchIcon, shortcutLabel } from "./lib/palette";
import { Button, inputCls } from "./lib/ui";
import Login from "./pages/Login";
import Home from "./pages/Home";
import Items from "./pages/Items";
import SwypeItems from "./pages/SwypeItems";
import Counts from "./pages/Counts";
import CountEntry from "./pages/CountEntry";
import Movements from "./pages/Movements";
import Imports from "./pages/Imports";
import Sales from "./pages/Sales";
import SettingsPage from "./pages/Settings";
import Recipes from "./pages/Recipes";
import Variance from "./pages/Variance";
import Groups from "./pages/Groups";
import DeliveryEntry from "./pages/DeliveryEntry";
import Deliveries from "./pages/Deliveries";
import DeliveryReview from "./pages/DeliveryReview";
import Users from "./pages/Users";
import Audit from "./pages/Audit";
import Account from "./pages/Account";
import Search from "./pages/Search";

// The weekly loop in the order it is done, then the things set up once and changed now and then.
const weekly = [["/", "Overview"], ["/counts", "Counts"], ["/deliveries", "Deliveries"], ["/sales", "Sales"], ["/variance", "Variance"]] as const;
const setup = [["/items", "Stock items"], ["/items/groups", "Item groups"], ["/swype-items", "Swype items"], ["/recipes", "Recipes"], ["/imports", "Imports"], ["/settings", "Settings"],
  ["/users", "Users"], ["/audit", "Audit log"]] as const;
const nav = [...weekly, ...setup];
/** Only the pages this login may open. */
const useAllowed = <T extends readonly [string, string]>(links: readonly T[]) => { const me = useMe(); return links.filter(([to]) => can(me, PAGE_AREA[to] ?? null)); };

function SetupMenu() {
  const items = useAllowed(setup);
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => setOpen(false), [loc.pathname]);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away); document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);
  if (!items.length) return null;
  const here = items.some(([to]) => loc.pathname === to || loc.pathname.startsWith(`${to}/`));
  return (
    <div ref={ref} className="relative">
      <button type="button" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((v) => !v)}
        className={`px-3 py-1.5 rounded-lg text-sm ${here ? "bg-raised text-ink font-medium" : "text-muted hover:text-ink"}`}>Setup ▾</button>
      {open && (
        <div role="menu" className="absolute z-50 mt-1 min-w-48 rounded-[12px] border border-line bg-surface shadow-lg p-1">
          {items.map(([to, label]) => (
            <NavLink key={to} to={to} end role="menuitem" className={({ isActive }) => `block px-3 py-2 rounded-lg text-sm ${isActive ? "bg-raised text-ink font-medium" : "text-muted hover:text-ink hover:bg-raised"}`}>{label}</NavLink>))}
        </div>)}
    </div>
  );
}

/** Phones and narrow tablets: every page behind one button, so the header stays a single row. */
function MobileMenu({ onLogout }: { onLogout: () => void }) {
  const me = useMe();
  const week = useAllowed(weekly), items = useAllowed(setup);
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname, loc.search]);
  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("keydown", esc); document.body.style.overflow = overflow; };
  }, [open]);
  const link = ({ isActive }: { isActive: boolean }) => `block px-3 py-3 rounded-lg text-[15px] ${isActive ? "bg-raised text-ink font-medium" : "text-muted hover:text-ink hover:bg-raised"}`;
  return (
    <div className="md:hidden">
      <button type="button" aria-label={open ? "Close menu" : "Open menu"} aria-expanded={open} aria-controls="mobile-menu" onClick={() => setOpen((v) => !v)}
        className="p-2.5 rounded-lg border border-line text-ink">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          {open ? <path d="M6 6l12 12M18 6 6 18" /> : <path d="M3 6h18M3 12h18M3 18h18" />}
        </svg>
      </button>
      {open && (
        // absolute, under the sticky header: its backdrop-blur would also anchor a fixed overlay to the header
        <div className="absolute inset-x-0 top-full h-[100dvh] z-40 bg-black/50" onClick={() => setOpen(false)}>
          <nav id="mobile-menu" aria-label="Main" onClick={(e) => e.stopPropagation()}
            className="bg-surface border-b border-line shadow-lg max-h-[calc(100dvh-4rem)] overflow-y-auto px-4 pt-2 pb-4">
            {week.map(([to, label]) => <NavLink key={to} to={to} end className={link}>{label}</NavLink>)}
            {items.length > 0 && <div className="label-caps px-3 pt-4 pb-1 text-[11px]">Setup</div>}
            {items.map(([to, label]) => <NavLink key={to} to={to} end className={link}>{label}</NavLink>)}
            <div className="flex items-center justify-between border-t border-line mt-3 pt-3 px-1">
              <ThemeToggle />
              <NavLink to="/account" className="text-sm text-muted hover:text-ink px-3 py-2 truncate">{me?.display_name}</NavLink>
              <button onClick={onLogout} className="text-sm text-muted hover:text-ink px-3 py-2">Switch user</button>
            </div>
          </nav>
        </div>)}
    </div>
  );
}

/** Logs out after the idle setting on a shared PC, and tells the server someone is still working when they type without saving. */
function useIdleLogout(minutes: number, onIdle: () => void, onExpired: () => void) {
  useEffect(() => {
    if (!minutes) return;
    let last = Date.now(), pinged = Date.now();
    const seen = () => { last = Date.now(); };
    const events = ["mousemove", "keydown", "touchstart", "scroll"];
    events.forEach((e) => window.addEventListener(e, seen, { passive: true }));
    const t = window.setInterval(() => {
      const now = Date.now();
      if (now - last >= minutes * 60000) onIdle();
      else if (last > pinged && now - pinged >= 120000) {
        pinged = now;
        api.get<{ user: Me | null }>("/api/auth/me").then((r) => { if (!r.user) onExpired(); }).catch(() => {});
      }
    }, 15000);
    return () => { events.forEach((e) => window.removeEventListener(e, seen)); window.clearInterval(t); };
  }, [minutes]); // eslint-disable-line react-hooks/exhaustive-deps
}

export default function App() {
  const [user, setUser] = useState<Me | null | undefined>(undefined);
  const [loggedOut, setLoggedOut] = useState("");
  const [palette, setPalette] = useState(false);
  const navigate = useNavigate();
  useEffect(() => { api.get<{ user: Me | null }>("/api/auth/me").then((r) => setUser(r.user)).catch(() => setUser(null)); }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette((v) => !v); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Resume the open draft if there is one, otherwise ask when the stock is (or was) counted first.
  const [asking, setAsking] = useState(false);
  const [countBar, setCountBar] = useState<CountBar | null>(null);
  const startCount = async () => {
    const sessions = await api.get<CountSession[]>("/api/counts");
    const draft = sessions.find((s) => s.status === "draft");
    if (draft) navigate(`/counts/${draft.id}`); else setAsking(true);
  };
  const startCountSafe = () => { startCount().catch(() => navigate("/counts")); };
  const createCount = async (when: string) => {
    setAsking(false);
    try { const s = await api.post<CountDetail>("/api/counts", { counted_at: new Date(when).toISOString() }); navigate(`/counts/${s.id}`); }
    catch { navigate("/counts"); }
  };
  const logout = async () => { await api.post("/api/auth/logout"); setLoggedOut(""); setUser(null); navigate("/"); };
  const expired = () => { setLoggedOut("You were logged out. Log in again to carry on."); setUser(null); };
  useEffect(() => {
    window.addEventListener("auth:expired", expired);
    return () => window.removeEventListener("auth:expired", expired);
  }, []);
  useIdleLogout(user?.idle_logout_minutes ?? 0, () => {
    api.post("/api/auth/logout").catch(() => {});
    setLoggedOut(`Logged out after ${user?.idle_logout_minutes} minute${user?.idle_logout_minutes === 1 ? "" : "s"} without activity.`); setUser(null); navigate("/");
  }, expired);

  const me = user ?? null;
  const canCount = can(me, "counts", "edit");
  const commands = useMemo<Command[]>(() => [
    ...nav.filter(([to]) => can(me, PAGE_AREA[to] ?? null)).map(([to, label]) => ({ id: to, group: "Go to", label, run: () => navigate(to) })),
    ...(canCount ? [{ id: "count", group: "Actions", label: "Start or resume a count", run: startCountSafe }] : []),
    ...(can(me, "deliveries", "edit") ? [{ id: "delivery", group: "Actions", label: "Upload a receipt / record a delivery", run: () => navigate("/deliveries") }] : []),
    ...(can(me, "sales", "edit") ? [{ id: "sales", group: "Actions", label: "Import Swype sales", run: () => navigate("/sales") }] : []),
    ...(can(me, "imports", "edit") ? [{ id: "import", group: "Actions", label: "Import a workbook or sheet", run: () => navigate("/imports") }] : []),
    { id: "account", group: "Actions", label: "My account: password and PIN", run: () => navigate("/account") },
    { id: "logout", group: "Actions", label: "Switch user / log out", run: logout },
  ], [navigate, me]); // eslint-disable-line react-hooks/exhaustive-deps

  if (user === undefined) return null;
  if (!user) return <Login notice={loggedOut} onLogin={(u) => { setLoggedOut(""); setUser(u); }} />;
  const guard = (page: string, el: React.ReactNode) => can(user, PAGE_AREA[page] ?? null) ? el : <NoAccess />;
  return (
    <MeContext.Provider value={user}>
    <CountBarContext.Provider value={setCountBar}>
    <div className="min-h-screen flex flex-col">
      <header className="sticky top-0 z-40 bg-paper/90 backdrop-blur border-b border-line">
        <div className="max-w-[1280px] mx-auto px-4 sm:px-7 py-3 flex flex-wrap items-center gap-x-4 gap-y-2">
          <NavLink to="/" className="flex items-center gap-2 font-bold tracking-wide mr-2">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="var(--color-accent)" strokeWidth="1.8" aria-hidden><path d="M9 2h6v5c2 1 3 3 3 5v9a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1v-9c0-2 1-4 3-5z" /></svg>
            {/* two count buttons need the room on a phone; the header stays one row */}
            <span className={countBar ? "hidden sm:inline" : ""}>Bar stock</span>
          </NavLink>
          <nav aria-label="Main" className="hidden md:flex flex-wrap gap-1">
            {weekly.filter(([to]) => can(user, PAGE_AREA[to] ?? null)).map(([to, label]) => (
              <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => `px-3 py-1.5 rounded-lg text-sm ${isActive ? "bg-raised text-ink font-medium" : "text-muted hover:text-ink"}`}>{label}</NavLink>
            ))}
            <span aria-hidden className="w-px self-stretch bg-line mx-1" />
            <SetupMenu />
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <button onClick={() => setPalette(true)} className="hidden lg:flex items-center gap-2 bg-surface border border-line rounded-[10px] px-3 py-1.5 min-w-56 text-muted hover:border-line-strong">
              <SearchIcon /><span className="flex-1 text-left text-sm">Search the portal…</span>
              <kbd className="mono text-[11px] border border-line rounded px-1.5">{shortcutLabel}</kbd>
            </button>
            <button onClick={() => setPalette(true)} aria-label="Search" className="lg:hidden p-2.5 rounded-lg border border-line"><SearchIcon /></button>
            {countBar ? <>
              <Button onClick={countBar.post} disabled={countBar.busy}>Post count</Button>
              <Button kind="quiet" onClick={countBar.cancel} disabled={countBar.busy}>Cancel</Button>
            </> : canCount && <button onClick={startCountSafe} className="bg-accent text-accent-ink rounded-[10px] px-4 py-2 md:py-1.5 font-semibold text-sm hover:bg-accent-deep">Start count</button>}
            <span className="hidden md:contents">
              <ThemeToggle />
              <NavLink to="/account" title="My account" className="text-xs text-muted hover:text-ink px-1 max-w-32 truncate">{user.display_name}{user.session_mode === "pin" ? " · PIN" : ""}</NavLink>
              <button onClick={logout} title="Log out so someone else can log in" className="text-xs text-faint hover:text-ink px-1">Switch user</button>
            </span>
            <MobileMenu onLogout={logout} />
          </div>
        </div>
      </header>
      <main className="flex-1 min-w-0">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/items" element={guard("/items", <Items />)} />
          <Route path="/items/groups" element={guard("/items/groups", <Groups />)} />
          <Route path="/swype-items" element={guard("/swype-items", <SwypeItems />)} />
          <Route path="/counts" element={guard("/counts", <Counts />)} />
          <Route path="/counts/:id" element={guard("/counts", <CountEntry />)} />
          <Route path="/deliveries" element={guard("/deliveries", <Deliveries />)} />
          <Route path="/deliveries/:id" element={guard("/deliveries", <DeliveryReview />)} />
          <Route path="/movements" element={guard("/movements", <Movements />)} />
          <Route path="/movements/new" element={guard("/movements", <DeliveryEntry />)} />
          <Route path="/recipes" element={guard("/recipes", <Recipes />)} />
          <Route path="/variance" element={guard("/variance", <Variance />)} />
          <Route path="/sales" element={guard("/sales", <Sales />)} />
          <Route path="/imports" element={guard("/imports", <Imports />)} />
          <Route path="/settings" element={guard("/settings", <SettingsPage />)} />
          <Route path="/users" element={guard("/users", <Users />)} />
          <Route path="/audit" element={guard("/audit", <Audit />)} />
          <Route path="/account" element={<Account />} />
          <Route path="/search" element={<Search />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
      {asking && <StartCountDialog onStart={createCount} onCancel={() => setAsking(false)} />}
      <CommandPalette open={palette} onClose={() => setPalette(false)} commands={commands} />
    </div>
    </CountBarContext.Provider>
    </MeContext.Provider>
  );
}

function NoAccess() {
  return (
    <div className="px-4 sm:px-7 py-10 max-w-[1280px] mx-auto">
      <h1 className="text-[26px] font-semibold mb-2">No access</h1>
      <p className="text-muted">Your login can't open this page. Ask an admin to give you access under Setup → Users.</p>
    </div>
  );
}

const localNow = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);

/** Asks for the count's date and time before creating it; now by default, earlier to back-date. */
function StartCountDialog({ onStart, onCancel }: { onStart: (when: string) => void; onCancel: () => void }) {
  const [when, setWhen] = useState(localNow);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [onCancel]);
  return (
    <div className="fixed inset-0 z-50 bg-black/50 grid place-items-center p-4" onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <form role="dialog" aria-modal="true" aria-labelledby="start-count" className="card p-5 w-full max-w-sm bg-surface"
        onSubmit={(e) => { e.preventDefault(); if (when) onStart(when); }}>
        <h2 id="start-count" className="font-semibold mb-1">Start a count</h2>
        <p className="text-sm text-muted mb-4">When is the stock counted? Pick an earlier time to enter a count done before.</p>
        <label className="flex flex-col gap-1 text-sm mb-4"><span className="text-muted">Counted at</span>
          <input autoFocus type="datetime-local" className={inputCls} value={when} max={localNow()} onChange={(e) => setWhen(e.target.value)} /></label>
        <div className="flex justify-end gap-2">
          <Button type="button" kind="quiet" onClick={onCancel}>Cancel</Button>
          <Button type="submit" disabled={!when}>Start count</Button>
        </div>
      </form>
    </div>
  );
}

/** Dark ("Night Shift", default) or light ("Day Shift"); remembered in this browser only. */
function ThemeToggle() {
  const [light, setLight] = useState(() => document.documentElement.dataset.theme === "light");
  const flip = () => {
    const next = !light;
    setLight(next);
    if (next) document.documentElement.dataset.theme = "light"; else delete document.documentElement.dataset.theme;
    try { localStorage.setItem("theme", next ? "light" : "dark"); } catch { /* private window: just not remembered */ }
  };
  return (
    <button type="button" onClick={flip} aria-label={light ? "Switch to dark theme" : "Switch to light theme"} title={light ? "Dark theme" : "Light theme"}
      className="p-2 rounded-lg text-muted hover:text-ink border border-transparent hover:border-line">
      {light
        ? <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></svg>
        : <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>}
    </button>
  );
}
