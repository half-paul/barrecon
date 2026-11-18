# Handoff - where the build stands (2026-10-28)

## Deleting counts, items per sales import (added 2026-11-06)
- DELETE /api/counts/{id} on a posted count (inventory.delete_count) only sets status "deleted", after a confirm on the
  Counts page: rows stay, hidden unless "show superseded and deleted counts" is on, and everything that reads "posted"
  skips it. Earlier versions stay superseded. Drafts are still really removed. A superseded version is refused; a PIN
  session can only delete drafts. No restore screen. Re-running the legacy workbook leaves a deleted count deleted; a
  count sheet upload for that date posts a new count.
- Sales page: open a count week, then a day, to list what that import sold (GET
  /api/imports/swype-item-sales/periods/{from}/{to}/items). Keyed by period: a re-import replaces a period's rows.
  Imports -> Import history: click a "Swype sales" row for the same list (GET /api/imports/{batch_id}/items, needs imports
  view). Shared component lib/soldItems.tsx.

## Matching sold items and receipt SKUs (added 2026-11-05)
- services/recipe_match.py + Recipes -> "Match sold items": everything sold with no recipe, drafted for bulk approval
  (GET /api/recipes/match, POST /api/recipes/match/approve). A recipe is still the only Swype -> stock link; nothing
  saves until approved. CATEGORY_GROUPS limits candidates by Swype category. HOUSE holds the house pours, PINT_OZ 20,
  GUINNESS_OZ 16: every draft pour (Pint, Gls, Spec, Happy) is 20 oz except Guinness, confirmed by the bar 2026-11-05.
  An approved one-line recipe teaches rows with the same core name.
- 21 Swype Pop items were added as stock items by hand request (group Pop, each, GEN-<name>), no costs yet; event
  pop_stock_items in the audit log. Backup from just before: backups/pre-matching-2026-11-05.sql.gz.
- Recipes page (2026-11-05): four tabs, To link / Linked / No stock / By stock item. To link is the queue; "Draft pour
  recipes" and "Add recipe" are gone from the UI (Add unsold item covers a Swype # with no sales). POST
  /api/recipes/suggest-pours has no screen now; recipe_match replaced it.
- Food and coffee: recipe_match.apply_no_stock, run by every Item Sales apply and by POST /api/recipes/no-stock.
- Receipts: Claude reads a per-line sku (delivery_lines.receipt_sku, receipt_aliases.sku). suggest() order: remembered
  store + SKU, stock item with that SKU (any store; setting bcldb_stores removed 2026-11-06, the bar writes the store
  as "BCL" and it missed), remembered text. Same text with another SKU is not a match. Learned on post. Not yet tried
  on a real receipt. Lines containing "container deposit" (NEVER_STOCK) are marked not stock on read, and stay on the
  draft so the totals still add up. receipt_aliases is unique on store, text and SKU (uq_receipt_alias_line): BCL prints the
  same words for different products.

## Users, permissions, audit trail (added 2026-11-04)
- Tables `users` and `audit_events` (new tables; create_all adds them). On first start with no users, seed creates
  `admin` (is_admin) with ADMIN_PASSWORD; after that ADMIN_PASSWORD is not read. Login is username + password
  (username defaults to "admin" in the API so old clients/tests still work). Passwords: hashlib.scrypt.
- Session cookie = user id + session_version; each request reloads the user, so permission changes apply at once and
  password reset / switch off / "Log out" bumps session_version and kills every session.
- Permissions: app/auth.py AREAS. Admin = everything incl. user management (admin only). Others get per area
  "view" (GET) or "edit" (anything else); routers use `dependencies=[perm("area")]`. Imports router picks per route
  (sales / swype_items / imports). Open to any logged-in user: /api/search and its /search page (each section empty without view on its area, routers/search.py SECTION_AREA),
  GET /api/settings, /api/variance/current.
  Guards: can't demote/switch off the last active admin or yourself.
- Audit: app/audit.py AuditMiddleware records every non-GET /api request made by a logged-in user (route, path,
  status, IP, JSON body with secrets as ***, upload file names); 403s are action "denied". Also SENSITIVE_READS
  (exports, receipts, audit searches), logins/failed/blocked (routers/auth.py) and system jobs (audit.system_job).
  Pages: Setup -> Users (/users), Audit log (/audit?user=...), My account. Labels and links: audit.LABELS / LINKS; a
  new write route needs a LABELS entry or it shows as "POST /api/...".
- Field history: table audit_changes, written by a Session after_flush hook in the same transaction, linked by event_id
  (no FK: the event row is written after the response). Bulk delete/update go through do_orm_execute as one summary
  row, as does a flush of over 200 rows of a table. password_hash/pin_hash show as ***; binary columns are skipped.
- Tamper evidence: audit_events carries seq/prev_hash/hash, an HMAC chain keyed by SESSION_SECRET (rotating it makes
  verify report old rows broken). GET /api/audit/verify. PostgreSQL trigger barrecon_audit_readonly refuses UPDATE and
  DELETE on both tables unless SET LOCAL barrecon.audit_purge = 'on' (only audit.purge does). Checked on real Postgres
  2026-11-04, SQLite has no trigger. Limits: the newest rows can be cut off unnoticed, and a DB owner can drop the trigger.
- Retention: setting audit_retention_days (default 365, 0 forever, min 30), services/maintenance.py once a day. Purges only
  a prefix of the chain so it still verifies. Retention and idle_logout_minutes (default 30) are admin-only in PUT /api/settings.
- Lockout: counted from audit_events: 5 fails per username or 20 per IP in 15 min -> 429 and a login_locked DataIssue;
  a good login or POST /api/users/{id}/unlock resets. Anyone can lock a known username out this way; admin unlocks it.
- PIN: users.pin_hash, 4-8 digits. PIN session (cookie "m") reaches PIN_AREAS (counts, deliveries) only and never admin
  powers. 10 wrong PINs since the last good login turn PIN login off until a password login or unlock.
- Idle: cookie re-signed at most once a minute (audit middleware adds the Set-Cookie), older than the setting is refused;
  browser also logs out on its own timer and pings /api/auth/me while someone works. Absolute limit stays 12 h.
- Frontend: `<Page area=...>` + lib/auth.tsx useReadOnly: primary/danger Button and FileButton grey out on view-only pages,
  a quiet Button that writes needs `edit`, raw <button>s are gated by hand. New write buttons must follow this.
- TRUST_PROXY=true is needed behind a reverse proxy, else every login shares the proxy's IP for the per-IP limit.
- Schema still by create_all + db.ADDED_COLUMNS; Alembic not added yet.

## CORRECTION 2026-10-30: Swype items are NOT inventory items
Inventory items (the Excel count list, own SKUs) and Swype items (what is rung in, Swype item #s) are
different things with different numbers. The ONLY link is a recipe: one Swype item -> one or more
inventory items, each with its own qty/unit (a cocktail has several, a pour or a bottle of beer one).
Sections below that describe "item linking", SWYPE-<code> items or adopt_sku are superseded.
- swype_item_links / SwypeItemLink removed from the code. New table swype_menu_items (SwypeMenuItem):
  the Swype menu (code, name, category). Sales report and Export Items CSV only upsert it; they never
  create, link or switch on inventory items. legacy_import no longer calls adopt_sku.
- Recipes page: menu rows carry suggested_item (guess only, never saved): the one item in the bottle
  code's recipe (00000331 for 00000331_02), else a name match ignoring SNG/DBL/6oz/BTL. suggest_pours uses
  the same guess; bottle codes are drafted first so their pours follow them.
- Items/search: swype_codes = Swype items whose current recipe uses the item.
- Variance: an item no recipe uses has status no_recipe (should use 0 + explained), no longer "standalone
  = sold on its own". A bottle of beer needs a recipe: Swype "Kokanee Btl" -> 1 each Kokanee.
- docs/Recipes-Summer-2026.xlsx still uses SWYPE-<code> SKUs; they must become inventory SKUs
  (e.g. SWYPE-00000380 -> 550715 Forty Creek). Not edited yet: the file has photos openpyxl would drop.
- Live Docker DB (checked 2026-10-30) still holds 364 active SWYPE- items, 224 count lines, 12 recipe
  lines, 2 movements on them, plus the old swype_item_links table. Needs a cleanup/re-key decision
  before deploying this code.


Spec: docs/bar-inventory-portal-spec.md v0.7. Decisions 1-9 at the top are locked.

## Done (Phase 1)
- Backend complete for Phase 1; `cd backend && pytest` -> 19 passed.
- Legacy importer verified against the real workbook over HTTP: 99 items, 21 weekly
  counts (June-Oct 2026), 315 deliveries, 43 review items. Re-running is a no-op.
- Frontend builds (npm run build) and was exercised against the API: Overview, Items,
  Counts, count entry, Deliveries & stock, Imports, Settings.
- UI restyled to the "Night Shift" direction (2026-10-28): dark theme tokens in
  frontend/src/index.css, top bar instead of sidebar, Cmd/Ctrl+K palette
  (lib/palette.tsx: pages, actions, item search -> /items?q=), "Start count" resumes the
  open draft or starts one at now, Overview rebuilt (stat cards, stock vs par bars,
  activity feed, variance placeholder). Inter + JetBrains Mono are bundled via
  @fontsource so the bar PC needs no internet for fonts.
- docker-compose.yml + backend/Dockerfile written but NOT yet built or run in Docker
  (the build environment had no Docker). First thing to do: `docker compose up -d --build`
  and click through once.

## Verify first
1. Docker build untested (see above). frontend/package-lock.json is included; if
   `npm ci` complains, regenerate with `npm install`.
2. Oct 2026 has a "Week 5"; with the count day set to Sunday there are only four
   Sundays, so week 5 is dated Oct 31 and flagged. Set Settings -> "Day of the weekly
   count" to the real day, delete the legacy count sessions, re-import.
3. Deliveries from the legacy "Ordered" column are dated to that week's count day and
   assume ordered = received. Fine for history; new deliveries get invoice numbers.
4. Pack/case sizes are category defaults (750 ml, 12/case; kegs 50 L; beer 24/flat).
   Confirm with the manager; docs/items-master-draft.xlsx lists each assumption.
5. No Alembic yet (create_all at startup). Add it before Phase 2 touches the schema.
6. Session cookie is not `secure` (localhost HTTP). Set COOKIE_SECURE=true behind TLS.

## Swype item sales (summary import, added 2026-10-28)
- Swype's "Item Sales" report is a per-item total for a date range: no ticket ids, discounts or
  modifiers, so it can't feed sales_lines. It lands in `swype_item_sales` (one row per period +
  Swype item #) via services/swype_item_sales.py and /api/imports/swype-item-sales (preview, apply,
  periods, delete period). Every subtotal/total is checked first; same period re-import replaces it;
  overlapping different periods are refused. Imports screen has a "Swype item sales" section.
- Download with scripts/csv_export_api/api_client.py (`fetch_item_sales_excel`), one Mon-Sun week
  per count. Or from the portal (2026-10-29): Imports -> From/To dates -> "Import from Swype" calls
  POST /api/imports/swype-item-sales/fetch, which logs in to Swype (services/swype_api.py, the
  business's own Swype login from Setup -> Businesses, password encrypted; no longer in .env), pulls the report and runs the same apply; batch source "swype_api".
  Since 2026-11-03 the page imports ONE DAY AT A TIME: both "Import from Swype" (From/To) and "Import new
  sales" (day after the latest imported period through yesterday; today is still selling) loop the days
  in the browser, one POST /fetch per day (date_from == date_to), with a progress bar. A day Swype reports
  as NoData returns 200 {"status": "no_sales"} and is skipped; any other failure stops the loop and keeps
  the days already imported. So new periods are single days (never straddle a count); variance sums them.
  The old weekly fetch-new endpoint was removed. Removing sales: "Remove" per period row, or "Remove these
  dates" (DELETE /api/imports/swype-item-sales/periods?date_from&date_to: periods wholly inside; partly
  overlapping ones kept and reported). Their ImportBatches become status "removed" so the same file can
  be imported again. Import history rows for item_sales also have "Remove" (DELETE /api/imports/{batch_id}):
  deletes exactly the swype_item_sales rows carrying that import_batch_id. Swype menu items it added stay.
- Swype menu auto-off (swype_items.deactivate_unsold, run by every Item Sales apply): active items with no
  sale in the calendar month up to the LATEST imported sales day are switched off. Skipped until sales
  reach back a full month; items never in any sales report and added within that month keep a grace
  period. A later sale switches them back on. Manual switch-on of an unsold item is undone by the next import.
- Usability pass (2026-11-03):
  * Nav = weekly loop (Overview, Counts, Deliveries, Sales, Variance) + "Setup ▾" menu (Stock items, Item
    groups, Swype items, Recipes, Imports, Settings). Swype sales moved from Imports to /sales (pages/Sales.tsx):
    "Import N missing days" (since last count), new sales, date range, file upload, periods grouped by count week.
  * Overview "This week" checklist from GET /api/variance/current (opening count, deliveries, missing sales
    days, sold items without recipe, closing draft, last variance).
  * Nightly import: services/swype_auto.py, asyncio loop started in main.lifespan (SCHEDULER_ENABLED, off in
    tests). Checks every 10 min; runs once a day after setting swype_auto_import_hour (default 05:00) when
    swype_auto_import is on and the Swype login is set. Imports days after the latest one + days missing since
    the last count; first failure stops and raises a DataIssue "swype_auto_import_failed". Last result in
    setting swype_auto_last (shown on Sales). Batch source "swype_auto".
  * UI kit: Loading (skeleton), Flash (bottom toast; ok fades after 10 s, errors stay), light theme via
    :root[data-theme="light"] tokens + header toggle (localStorage "theme"). Start count asks for the date.
    Count page: search, Not counted/Counted filter, phone cards with +/- and tenths buttons (md:hidden).
- Count date changes (PATCH /api/counts/{id} {counted_at, reason?}): a draft just moves. A posted count is
  never rewritten: inventory.redate_count posts a copy (same lines) at the new time, note "Date changed from
  ...", and the original becomes superseded with superseded_by -> copy. Detail returns `versions` (chain via
  superseded_by); the count page shows them and a "Change date" form on posted counts. "Import new sales" needs one manual import first. Weeks 2026-06-08 .. 2026-10-25 (20 weeks) are imported in the Docker portal.
- Item linking (services/swype_items.py, table swype_item_links): both the sales report import and
  the Export Items CSV import (/api/imports/swype-items) link every Swype item # to a portal item:
  bottle code first, pours (_01/_02/_3...) follow it, then name match ignoring SNG/DBL/6oz/BTL/
  Single Premium..., else a new item `SWYPE-<item #>`, unit "each". Sales-report imports switch on every item they list (new ones created active); CSV-created items start inactive. SKUs of matched items are
  never changed; legacy_import.apply calls adopt_sku so a SWYPE- item takes the workbook's real SKU.
  /api/imports/swype-items/sync-from-sales links codes already in swype_item_sales.
- Docker portal (2026-10-28): 900 items, all from Swype and inactive, 1040 Swype codes linked. The
  480 created from the CSV have no category (the CSV carries only a category code). Legacy workbook
  and counts are still not imported there.

## Recipes + variance (added 2026-10-28)
- Tables recipes / recipe_versions / recipe_lines (created by create_all; no existing table changed).
  One recipe per Swype item #: lines of inventory item + qty + unit (oz/ml/each/dash...), converted to the
  item's stock unit at report time. Save with effective_from=None corrects the version in effect; a date
  starts a new version so earlier weeks keep the old portions. `ignored` = sold item takes no tracked stock.
- services/recipes.py + /api/recipes (menu, PUT/DELETE /{code}, suggest-pours). suggest_pours drafts pours
  from Swype names: SNG/Single 1 oz, DBL/Double 2 oz, "6oz" 6 oz, Btl one pack, bare bottle code with
  _01/_02 siblings 1 oz. Never guesses cocktails or beer. Skips items counted in 'each'.
- services/variance.py + /api/variance (periods, report). Between two consecutive posted counts:
  should use = Swype total_qty x recipe portion + breakage/other_explained; used = opening + in - out -
  closing; flagged when |variance %| >= variance_threshold_pct (default now 30) or stock gone with no
  sales. Uses swype_item_sales periods that sit inside the count window by local date; reports missing
  days, periods straddling a count, and sold items with no recipe.
- Frontend: Recipes page (by drink / by ingredient, inline editor, pour drafts), Variance page (period
  picker, bottles or ml, per-item breakdown of which drinks used it), Overview variance card.
- Variance list (2026-11-05): not-counted items are left out unless added under "Add / remove items"; counted
  items can be removed there. Saved for everyone in settings variance_view_added / variance_view_removed.
- tests/test_variance.py: 10 cocktails x 75 ml = 1 bottle, 30 % flag, spec 4.4 breakage example,
  deliveries, unmapped, coverage, versions, unit issue, pour drafts, API. `pytest` -> 39 passed.
- Live Docker portal can't show variance yet: one posted count, and every item is a Swype-created item
  counted in 'each'. Needs: legacy workbook import (bottles in ml) or fix units on Items, a second count,
  then recipes.
- Spilled waste is not modelled: the Item Sales summary has no discount names. Needs the ticket-level
  export (Phase 2 below).

## Next: Phase 2 (spec sections 3.5-3.8 and 5)
- Tables: swype_menu_items, swype_modifiers, sales_lines, swype_modifier_lines,
  discount_classifications (seed: spilled -> waste).
- Versioned recipes (entered in oz, stored in ml) + mapping rules with multipliers and
  substitutions.
- Two-file Swype import (sales report + modifier report) with the join in section 5.4.
  BLOCKED on a sample week of both exports from the Swype back office.
- Then Phase 3: reconciliation engine (section 4) with the acceptance test in 4.4, dashboard.

## Conventions worth keeping
- Every write path goes through services/inventory.py; routers and importers never
  insert CountLine/StockMovement directly.
- Instants are stored in UTC (inventory.to_utc); SQLite drops tzinfo so naive == UTC.
- Imports validate the whole file, then write, or write nothing; an ImportBatch is
  recorded either way.

## Item groups and delivery sheet (added 2026-11-01)
- New table `item_groups` (name, stock_unit, pack_name, pack_size, case_size): group defaults. Items still point
  at a group by `inventory_items.category`; `GET /api/groups` backfills a row for every category in use.
  create_all adds the table on startup (new table only, no column changes). Page: /items/groups.
- Renaming a group moves its items; removing one moves them to another group or to none. "Set to group pack"
  (`POST /api/groups/{name}/apply`) leaves the unit of items that already have counts or movements.
- Deliveries are entered on a sheet like a count (/movements/new): one reference, all items, saved by
  `POST /api/movements/batch`, all lines or none. The unsaved sheet is kept in the browser's localStorage.
