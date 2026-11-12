export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

/** The business this tab is showing. Sent with every request, so a tab left on another business (it was switched
 *  in a different tab) is refused instead of writing into the wrong one; the page then reloads into the new one. */
let shownBusiness: string | null = null;
export const showBusiness = (id: string | null) => { shownBusiness = id; };
/** Told to the other tabs through localStorage, so they reload into the business just picked. */
const SWITCHED = "business-switched";
export function announceSwitch() { try { localStorage.setItem(SWITCHED, String(Date.now())); } catch { /* private window: other tabs reload on their next request instead */ } }
export function onSwitchElsewhere(fn: () => void) {
  const h = (e: StorageEvent) => { if (e.key === SWITCHED) fn(); };
  window.addEventListener("storage", h);
  return () => window.removeEventListener("storage", h);
}

async function req<T>(method: string, url: string, body?: unknown, form?: FormData): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (shownBusiness && !url.startsWith("/api/auth/")) headers["X-Business"] = shownBusiness;
  const r = await fetch(url, {
    method,
    headers,
    body: form ?? (body !== undefined ? JSON.stringify(body) : undefined),
    credentials: "same-origin",
  });
  if (r.status === 204) return undefined as T;
  const text = await r.text();
  let data: unknown = text;
  try { data = text ? JSON.parse(text) : null; } catch { /* keep text */ }
  // a web page instead of data: the server doesn't have this endpoint (an older build). Fail like an error, don't hand
  // HTML to code expecting an object.
  if (r.ok && typeof data === "string" && /^\s*<(!doctype|html)/i.test(data)) throw new ApiError(502, "The server is out of date for this page. Reload, or ask an admin to update it.");
  if (r.status === 409 && r.headers.get("X-Business-Changed")) { window.location.reload(); return new Promise<T>(() => {}); }
  if (!r.ok) {
    const d = data as { detail?: unknown };
    const msg = typeof d?.detail === "string" ? d.detail : Array.isArray(d?.detail) ? JSON.stringify(d.detail) : r.statusText;
    // the session ended (idle, switched off, password reset): back to the login screen
    if (r.status === 401 && !url.startsWith("/api/auth/")) window.dispatchEvent(new CustomEvent("auth:expired", { detail: msg }));
    throw new ApiError(r.status, msg);
  }
  return data as T;
}

export const api = {
  get: <T>(url: string) => req<T>("GET", url),
  post: <T>(url: string, body?: unknown) => req<T>("POST", url, body),
  put: <T>(url: string, body?: unknown) => req<T>("PUT", url, body),
  patch: <T>(url: string, body?: unknown) => req<T>("PATCH", url, body),
  del: <T>(url: string) => req<T>("DELETE", url),
  upload: <T>(url: string, file: File | Blob, method = "POST", name?: string) => { const f = new FormData(); f.append("file", file, name); return req<T>(method, url, undefined, f); },
};

export type Item = {
  id: string; sku: string; name: string; category: string | null; stock_unit: string; pack_name: string | null;
  pack_size: string; case_size: string; pack_cost: string | null; unit_cost: string | null; yield_to_unit: string | null;
  yield_qty: string | null; par_level: string | null; is_active: boolean; updated_at: string;
  last_counted_packs: string | null; last_counted_at: string | null;
};
export type CountSession = { id: string; counted_at: string; status: string; source: string; note: string | null; posted_at: string | null; superseded_by: string | null; line_count: number };
export type CountLine = { item_id: string; sku: string; name: string; category: string | null; pack_name: string | null; stock_unit: string; pack_size: string; packs_full: string | null; packs_partial: string | null; qty: string | null; note: string | null };
export type CountDetail = CountSession & { lines: CountLine[]; versions: CountSession[] };
export type Group = { name: string; stock_unit: string; pack_name: string | null; pack_size: string; case_size: string; sheet_in_cases: boolean; item_count: number; active_count: number; differing: number };
export type Movement = { id: string; item_id: string; sku: string; name: string; kind: string; qty: string; packs: string | null; pack_cost: string | null; occurred_at: string; external_ref: string; source: string; status: string; note: string | null; delivery_id: string | null };
export type DeliverySummary = {
  id: string; store: string | null; occurred_at: string | null; receipt_no: string | null; total: number | null; status: "draft" | "posted" | "void";
  source: string; has_receipt: boolean; line_count: number; item_count: number; spend: number; totals_ok: boolean; price_alerts: number; to_match: number; created_at: string;
};
export type DeliveryLine = {
  position: number; text: string; receipt_sku: string | null; receipt_qty: number; line_total: number | null; item_id: string | null; packs: number | null; skip: boolean; matched_by: string | null;
  item: { id: string; sku: string; name: string; pack_name: string | null; pack_size: string; case_size: string; stock_unit: string } | null;
};
export type DeliveryPrice = { position: number; item_id: string; pack_cost: number; previous: number | null; change_pct: number | null; alert: boolean };
export type DeliveryDetail = DeliverySummary & {
  store_address: string | null; subtotal: number | null; tax: number | null; note: string | null; read_error: string | null; posted_at: string | null;
  receipt_type: string | null; can_read: boolean; lines: DeliveryLine[];
  checks: { lines_sum: number; missing_amounts: number; total_diff: number | null; subtotal_diff: number | null; totals_ok: boolean; unmatched: number[]; no_packs: number[];
    prices: DeliveryPrice[]; price_alerts: number; threshold_pct: number; possible_duplicate: { id: string; status: string } | null };
};
export type Batch = { id: string; source: string; dataset: string; file_name: string | null; rows_received: number; rows_inserted: number; rows_duplicate: number; rows_error: number; status: string; error_log: { row: number; status: string; message: string }[] | null; imported_at: string };
export type Issue = { id: string; code: string; entity: string | null; detail: { detail?: string } | null; resolved: boolean; created_at: string };
export type ChartStyle = "bars" | "gap" | "difference";
/** This login's own choices in the current business (GET/PUT /api/settings/me). */
export type UserPrefs = { variance_chart_style: ChartStyle };
export type Settings = { oz_ml: string; variance_threshold_pct: string; count_weekday: number; count_reminder_enabled: boolean; variance_chart_items: string[]; variance_view_added: string[]; variance_view_removed: string[]; swype_auto_import: boolean; swype_auto_import_hour: number; idle_logout_minutes: number; audit_retention_days: number };
export type MenuSync = { new: number; renamed: number; unchanged: number; reactivated: number; deactivated?: number; new_items: string[] };
export type SwypeItem = { code: string; name: string; swype_name: string; renamed: boolean; category: string | null; is_active: boolean; source: string; sold: number; last_sold: string | null };
export type ImportSummary = { dataset: string; status: string; inserted: number; updated: number; duplicate: number; errors: number; sessions_created: string[]; rows: { row: number; status: string; message: string }[] };

export const n = (v: string | number | null | undefined, dp = 2) => v == null || v === "" ? "" : Number(v).toLocaleString("en-CA", { maximumFractionDigits: dp });
export const dt = (v: string | null | undefined) => v ? new Date(v).toLocaleString("en-CA", { dateStyle: "medium", timeStyle: "short" }) : "";
export const d = (v: string | null | undefined) => v ? new Date(v).toLocaleDateString("en-CA", { dateStyle: "medium" }) : "";
export function groupBy<T>(rows: T[], key: (r: T) => string): [string, T[]][] {
  const m = new Map<string, T[]>();
  for (const r of rows) { const k = key(r); if (!m.has(k)) m.set(k, []); m.get(k)!.push(r); }
  return [...m.entries()];
}

type Num = string | number;
export type RecipeLineItem = { item_id: string; sku: string; item_name: string; stock_unit: string; pack_name: string | null; pack_size: Num; stock_qty: Num | null; packs: Num | null; error: string | null };
/** The first item's fields at the top level; `items` lists every item the portion can come out of (any one of them, no main one). */
export type RecipeLine = RecipeLineItem & { qty: Num; unit: string; items: RecipeLineItem[] };
export type Recipe = { id: string; swype_code: string; name: string; ignored: boolean; steps: string | null; notes: string | null; photo_at: string | null; effective_from: string | null; lines: RecipeLine[]; versions: { effective_from: string; lines: number }[] };
export type MenuRow = { code: string; name: string; department: string | null; category: string | null; sold: Num; weeks: number; last_sold: string | null;
  suggested_item: { id: string; name: string; sku: string; stock_unit: string; how: "bottle_recipe" | "name" } | null; recipe: Recipe | null };
export type MatchItem = { id: string; name: string; sku: string; stock_unit: string };
export type MatchLine = MatchItem & { qty: Num | null; unit: string; item_ids?: string[]; options?: MatchItem[] };
export type MatchRow = { code: string; name: string; category: string | null; sold: Num; lines: MatchLine[]; candidates: MatchItem[];
  how: "bottle_recipe" | "approved" | "house" | "name" | "copy" | null; check: string | null; clear: boolean };
export type MatchQueue = { rows: MatchRow[]; no_stock: number };
export type VariancePeriod = { opening_id: string; closing_id: string; opening_at: string; closing_at: string };
export type Contribution = { code: string; name: string; sold: Num; portion_qty: Num; portion_unit: string; portion: Num; used: Num };
export type VarianceStatus = "off" | "ok" | "unit_issue" | "missing_opening" | "missing_closing" | "no_recipe";
export type VarianceRow = { item_id: string; sku: string; name: string; category: string | null; stock_unit: string; pack_name: string | null; pack_size: Num;
  opening: Num | null; received: Num; removed: Num; closing: Num | null; expected: Num; explained: Num; should_use: Num; actual: Num | null; no_recipe: boolean;
  variance: Num | null; variance_pct: Num | null; variance_cost: Num | null; status: VarianceStatus; issues: string[]; contributions: Contribution[] };
export type PostedCount = { id: string; counted_at: string };
export type CountPage = { total: number; counts: PostedCount[] };
export type VarianceReport = { opening: PostedCount; closing: PostedCount; between_counts: number; threshold_pct: Num;
  sales_periods: { from: string; to: string }[]; straddling_periods: { from: string; to: string }[]; missing_sales_days: string[];
  no_movements_recorded: boolean; unmapped: { code: string; name: string; category: string | null; sold: Num }[]; unmapped_qty: Num;
  summary: Record<VarianceStatus, number>; rows: VarianceRow[]; pool_summary: Record<VarianceStatus, number>; pools: VariancePool[] };
export type PoolMember = { item_id: string; sku: string; name: string; category: string | null; stock_unit: string; pack_name: string | null; pack_size: Num;
  opening: Num | null; received: Num; removed: Num; closing: Num | null; explained: Num; actual: Num | null; actual_in_unit: Num | null };
export type PoolRecipe = Contribution & { uses: string[] };
/** Items that stand in for each other in recipes ("6 oz of Box A or Box B"), checked together in `unit`. */
export type VariancePool = { id: string; name: string; unit: string; item_ids: string[]; opening: Num; received: Num; removed: Num; closing: Num;
  expected: Num; explained: Num; should_use: Num; actual: Num | null; variance: Num | null; variance_pct: Num | null; variance_cost: Num | null;
  status: VarianceStatus; issues: string[]; members: PoolMember[]; recipes: PoolRecipe[] };
/** A date-only value ("2026-10-20") shown as that calendar day, not shifted by the browser's timezone. */
export const day = (v: string | null | undefined) => v ? new Date(`${v}T00:00`).toLocaleDateString("en-CA", { dateStyle: "medium" }) : "";
/** Consecutive YYYY-MM-DD days joined into "a – b" ranges. */
export function dayRanges(days: string[]) {
  const next = (x: string) => { const t = new Date(`${x}T12:00:00`); t.setDate(t.getDate() + 1); return t.toLocaleDateString("en-CA"); };
  const out: [string, string][] = [];
  for (const x of days) {
    const last = out[out.length - 1];
    if (last && next(last[1]) === x) last[1] = x; else out.push([x, x]);
  }
  return out.map(([a, b]) => a === b ? day(a) : `${day(a)} to ${day(b)}`).join(", ");
}
export type CurrentPeriod = {
  opening: { id: string; counted_at: string } | null; draft: { id: string; counted_at: string } | null;
  days: number; missing_sales_days: string[]; unmapped: number; deliveries: number;
  last_variance: { opening_at: string; closing_at: string } | null;
};
export const menuText = (m: MenuSync) => `${m.new} new Swype items, ${m.renamed} renamed, ${m.unchanged} unchanged${m.reactivated ? `, ${m.reactivated} switched back on (sold)` : ""}${m.deactivated ? `, ${m.deactivated} switched off (nothing sold in a month)` : ""} (no stock items are created; recipes link Swype items to stock)`;
/** Readable names for the import history. */
const DATASETS: Record<string, string> = {
  legacy: "Inventory workbook", item_sales: "Swype sales", swype_items: "Swype item list", items: "Stock items sheet",
  recipes: "Recipes sheet", counts: "Count sheet", movements: "Deliveries sheet",
};
export const datasetName = (k: string) => DATASETS[k] ?? k;

export type Level = "view" | "edit";
/** A business of the tenant (a bar, a restaurant). logo_at is null without a logo, and changes when the logo does. */
export type Business = { id: string; name: string; kind: string | null; address: string | null; city: string | null; phone: string | null;
  email: string | null; website: string | null; is_active: boolean; logo_at: string | null;
  /** food sold is linked to stock by recipes, like drinks; switched on in the platform console */
  track_food?: boolean };
export type BusinessAdmin = Business & { swype_email: string | null; swype_company: string | null; swype_store_id: number | null;
  swype_password_set: boolean; members: number; created_at: string | null; copied?: { items: number; groups: number; recipes: number } | null };
export type Member = { user_id: string; username: string; display_name: string; is_admin: boolean; is_active: boolean; member: boolean; permissions: Record<string, Level> };
export const logoUrl = (b: Pick<Business, "id" | "logo_at">) => b.logo_at ? `/api/businesses/${b.id}/logo?v=${encodeURIComponent(b.logo_at)}` : null;
/** `permissions` are this login's levels in `business`, the one the session is working in. */
export type Me = { id: string; username: string; display_name: string; is_admin: boolean; is_platform_admin: boolean; permissions: Record<string, Level>;
  session_mode: "password" | "pin"; has_pin: boolean; email: string | null; mfa_enabled: boolean; mfa_required: boolean; recovery_codes_left: number; idle_logout_minutes: number;
  tenant: { id: string; name: string } | null; business: Business | null; businesses: Business[] };
export type Area = { key: string; label: string; levels: Level[] };
/** `access` is business id -> levels there; `permissions` is the entry for the business the admin is working in. */
export type UserRow = { id: string; username: string; display_name: string; is_admin: boolean; is_active: boolean; permissions: Record<string, Level>;
  access: Record<string, Record<string, Level>>;
  email: string | null; has_pin: boolean; locked: boolean; mfa_enabled: boolean; invite_pending: boolean; invite_sent?: boolean | null; last_login_at: string | null; password_changed_at: string | null; created_at: string };
export type AuditEvent = { id: string; at: string; user_id: string | null; username: string | null; action: "login" | "login_failed" | "login_blocked" | "logout" | "change" | "denied" | "read" | "system" | "mfa_setup_started" | "mfa_email_sent" | "mfa_email_recovery" | "reset_requested" | "password_reset" | "invite_accepted";
  area: string | null; method: string | null; route: string | null; path: string | null; status: number | null; ip: string | null;
  detail: Record<string, unknown> | null; label: string; link: string | null; changes: number };
export type AuditChange = { table: string; row_id: string | null; op: string; changes: Record<string, unknown> | null };
