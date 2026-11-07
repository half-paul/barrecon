# Bar Ingredient-Usage Reconciliation — Architecture & Data Model Spec

**Version:** 0.7 (supersedes 0.6)
**Date:** 2026-10-28
**Audience:** owner/manager (single role), developer

**Change from 0.4:** Google Sheets sync is deferred to a later phase; day 1 is portal entry with .xlsx upload. SwypePOS remains the source of sales only; inventory is tracked in the portal. Waste arrives through Swype as a discounted "spilled" line. Modifiers come as a separate Swype report. Single location, weekly counts (on demand allowed), oz as the recipe entry unit, Docker on a local PC.

### Decisions (locked)

| # | Decision | Design consequence |
|---|---|---|
| 1 | Inventory entry: portal from day 1; Google Sheets sync later | Counts and movements are entered in the portal; .xlsx upload (same tab layout as the future sheet) for bulk entry. Sheets read-only sync is Phase 5, designed in §6 so nothing changes in the schema. |
| 2 | Counts weekly, on demand allowed | Reconciliation period = count to count. Any extra count splits the week into two periods. Weekly reminder in the dashboard. |
| 3 | Modifiers are a separate Swype Excel report | Two-file sales import; modifier rows joined to sales lines by ticket/line key. See §5. |
| 4 | Waste = Swype billed item with discount "spilled" | Explained consumption is computed from the recipe of each spilled line. Portal waste movements are reserved for breakage/expiry of unopened stock. See §4.3. |
| 5 | One location | Location picker hidden; `location_id` kept in the schema with a single seeded row. |
| 6 | Recipe entry unit: oz, Canadian (imperial) | 1 oz = 28.4131 ml, matching Canada's standard-drink definition (1.5 oz = 43 ml) and BC Serving It Right. Stored in ml. Setting can be switched to US (29.5735) if the bar pours by US-marked jiggers; a steady ≈ +4 % spirits variance is the tell. |
| 7 | Runs on a local Windows PC in Docker; developed on a Mac | Docker Desktop + Compose on both; no OS-specific paths, scripts or line endings in the repo; local backups. See §8. |
| 8 | Only "spilled" counts as waste | `discount_classifications` seeded with that single row; everything else is `sale`. |
| 9 | Item master seeded from the bar's current spreadsheet | Sample received ("To Order Inventory List", monthly tabs × weekly On hand / Ordered / Sold columns). Findings and the one-time importer are in §6.5; a draft item master was generated from the Oct 2026 tab. |

---

## 1. Purpose and scope

For a period and location, compute the ingredient usage that items sold in SwypePOS *should* have produced (via recipes), compare it with the usage *measured* from the bar's own physical counts and stock movements, and show the variance per ingredient in quantity, percentage, and cost.

### Sources of truth

| Data | Source of truth | How it enters the portal |
|---|---|---|
| Menu items, sales lines | SwypePOS | Sales export (Excel/CSV) upload; API pull if Swype grants read access |
| Modifiers per sales line | SwypePOS | Separate modifier report upload, joined to sales lines |
| Spilled drinks (waste of made drinks) | SwypePOS | Sales lines carrying the "spilled" discount |
| Inventory items, pack sizes, costs | Portal | Entered in the portal, or .xlsx upload (`Items` tab); Google Sheet sync later |
| Physical counts | Portal | Entered in the portal, or .xlsx upload (`Counts` tab); Google Sheet sync later |
| Deliveries, transfers, supplier returns, breakage | Portal | Entered in the portal, or .xlsx upload (`Movements` tab); Google Sheet sync later |
| Recipes, versions, mapping rules | Portal | Portal UI only |

### In scope (v1)

- Inventory item master with units, pack sizes, costs, par levels.
- Physical count sessions (opening/closing) and stock movements, entered in the portal or uploaded as .xlsx (Google Sheets sync is a later phase).
- Versioned recipes linked to inventory items; modifier-driven sizes, doubles, substitutions.
- Sales import from Swype (CSV first).
- Expected usage, actual depletion, gross and unexplained variance.
- Reconciliation dashboard with drill-down, filters, flags, export.

### Out of scope

- Any write to SwypePOS. The portal never changes anything in Swype.
- Purchase orders, supplier management, staff roles, multi-tenant.

---

## 2. Core concepts

| Concept | Description |
|---|---|
| **Inventory item** | An ingredient the bar stocks: name, stock unit (ml/g/each), pack size and cost, optional yield for count→volume (1 lemon = 30 ml). |
| **Count session** | A physical count at a point in time: one row per item with the counted quantity (entered as packs + partial, stored in stock unit). A count is immutable once posted. |
| **Stock movement** | Delivery, transfer in/out, supplier return, breakage/expiry, or other explained consumption, with a date and quantity. Each has a stable external reference (invoice no., sheet row id) for idempotent import. |
| **Swype menu item / modifier** | Sellable items and their modifiers as Swype reports them; mirrored locally. |
| **Sales line** | One sold line from Swype: item, modifiers, qty, `sold_at`. |
| **Recipe / recipe version** | Named build; versions are immutable with an effective window; sales use the version in effect at `sold_at`. |
| **Mapping rule** | Menu item (+ optional modifier) → recipe, with multiplier and substitutions. |
| **Reconciliation run** | Frozen result for (location, opening count, closing count). |
| **Data issue** | Visible flag: missing mapping, unit conversion impossible, missing opening/closing count, no movements recorded, duplicate import. |

Key difference from 0.1: the portal still does **not** maintain a running "stock on hand" that is depleted by sales. Stock on hand is only ever *counted*. Sales produce *expected* usage; counts and movements produce *actual* usage; the gap is the product.

---

## 3. Data model

PostgreSQL. Quantities `NUMERIC(14,4)` in the item's stock unit. Timestamps `TIMESTAMPTZ`.

### 3.1 Reference

```
locations        id UUID PK, name TEXT, swype_location_id TEXT NULL
units            code TEXT PK, dimension TEXT (volume|mass|count), to_base NUMERIC
```

Seeded units: ml 1, l 1000, oz 29.5735, dash 0.92, g 1, kg 1000, each 1.

### 3.2 Inventory master (portal-owned)

```
inventory_items
  id             UUID PK
  location_id    UUID FK NULL        -- NULL = shared across locations
  sku            TEXT UNIQUE         -- stable key used in the Google Sheet ('VOD-ABS-750')
  name           TEXT NOT NULL
  category       TEXT                -- spirits | beer | wine | mixer | garnish | dry
  stock_unit     TEXT FK units       -- unit stock is counted and reported in (ml, g, each)
  pack_name      TEXT                -- bottle | keg | case | can | bag
  pack_size      NUMERIC             -- stock units per pack (750 ml, 50 000 ml keg, 1 each)
  case_size      NUMERIC DEFAULT 1   -- packs per case/flat; used to read '2 case', '4 flats' on delivery entry
  pack_cost      NUMERIC             -- latest cost per pack
  yield_to_unit  TEXT FK units NULL  -- cross-dimension yield: 'each' → 'ml'
  yield_qty      NUMERIC NULL        -- 1 each = 30 ml
  par_level      NUMERIC NULL        -- stock units
  is_active      BOOLEAN DEFAULT true
  created_at, updated_at

inventory_item_costs                 -- cost history, so variance cost uses the cost in effect
  item_id FK, effective_from TIMESTAMPTZ, pack_cost NUMERIC, source TEXT (manual|delivery|sheet)
```

`unit_cost = pack_cost / pack_size`.

### 3.3 Counts (portal-owned)

```
count_sessions
  id            UUID PK
  location_id   UUID FK
  counted_at    TIMESTAMPTZ NOT NULL    -- the instant the count represents (e.g. close of business)
  status        TEXT   -- draft | posted
  source        TEXT   -- portal | sheet | xlsx
  external_ref  TEXT NULL                -- sheet count_date / file hash for idempotent import
  note          TEXT
  posted_at     TIMESTAMPTZ NULL
  UNIQUE (location_id, counted_at)

count_lines
  session_id FK, item_id FK  (PK)
  packs_full     NUMERIC      -- 3
  packs_partial  NUMERIC      -- 0.4  (tenths of a bottle, or weighed)
  qty            NUMERIC      -- derived: (packs_full + packs_partial) × pack_size, in stock_unit
  note           TEXT
```

A posted session is immutable. Corrections are a new session at the same `counted_at` that supersedes the old one (`superseded_by` pointer), so reruns remain reproducible. Deleting a posted count (Counts page, after a confirmation, password session only) changes no lines: it flags the session `deleted`, which like `superseded` keeps it as history and out of every calculation.

### 3.4 Stock movements (portal-owned)

```
stock_movements
  id            UUID PK
  location_id   UUID FK
  item_id       UUID FK
  kind          TEXT   -- delivery | transfer_in | transfer_out | supplier_return | breakage_expired | other_explained
  qty           NUMERIC NOT NULL        -- positive magnitude in stock_unit; sign comes from kind
  packs         NUMERIC NULL            -- as entered, for display
  pack_cost     NUMERIC NULL            -- deliveries: updates inventory_item_costs
  occurred_at   TIMESTAMPTZ NOT NULL
  external_ref  TEXT NOT NULL           -- invoice no. + line, or the sheet's row_id
  source        TEXT   -- portal | sheet | xlsx
  status        TEXT   -- active | superseded | retracted
  note          TEXT
  UNIQUE (location_id, item_id, kind, external_ref)
```

The unique constraint is what makes re-uploading last week's delivery sheet a no-op instead of a double-count.

### 3.5 Swype mirrors (read-only cache)

```
swype_menu_items   id, swype_menu_id TEXT UNIQUE, name, category, price, is_active, last_import_batch
swype_modifiers    id, swype_modifier_id TEXT UNIQUE, name, group_name
```

### 3.6 Sales lines (imported from Swype)

```
sales_lines
  id                 UUID PK
  location_id        UUID FK
  swype_sale_line_id TEXT UNIQUE          -- ticket + line no, or SHA-256 of (ticket, item, qty, sold_at, discount)
  ticket_id          TEXT
  line_no            INT NULL
  swype_menu_item_id UUID FK
  modifier_ids       UUID[]               -- filled by the modifier join (§5.4)
  qty                NUMERIC
  gross_amount       NUMERIC NULL
  discount_name      TEXT NULL            -- as exported: 'spilled', 'staff', 'comp', …
  discount_amount    NUMERIC NULL
  classification     TEXT NOT NULL        -- 'sale' | 'waste' | 'excluded'   (derived from discount_name via settings)
  sold_at            TIMESTAMPTZ
  import_batch_id    UUID FK

swype_modifier_lines                      -- staging for the separate modifier report
  id                 UUID PK
  ticket_id          TEXT
  line_no            INT NULL
  swype_menu_item_id UUID FK NULL         -- used for the fallback join when line_no is absent
  swype_modifier_id  UUID FK
  qty                NUMERIC DEFAULT 1
  sold_at            TIMESTAMPTZ NULL
  import_batch_id    UUID FK
  joined_sales_line  UUID FK sales_lines NULL   -- NULL = unjoined → data issue

discount_classifications                  -- settings table
  discount_name TEXT PK, classification TEXT   -- seeded: 'spilled' → waste; everything else → sale
```

`classification` rules: `sale` lines feed expected usage; `waste` lines feed explained consumption (the drink was made, then lost); `excluded` is for discounts that mean nothing was poured (e.g. a void that reached the export). Comps and staff drinks stay `sale` — the ingredients were consumed.

### 3.7 Recipes (versioned)

```
recipes                id, name, notes, is_active
recipe_versions        id, recipe_id FK, version_no INT, effective_from, effective_to NULL, created_at
                       UNIQUE (recipe_id, version_no); versions of one recipe never overlap
recipe_version_lines   id, recipe_version_id FK, item_id FK inventory_items, qty NUMERIC, unit FK units, position
```

Saving a recipe closes the current version and opens a new one. Past runs keep pointing at the version they used.

### 3.8 Mapping rules

```
menu_item_mappings
  id, swype_menu_item_id FK, swype_modifier_id FK NULL, recipe_id FK,
  multiplier NUMERIC DEFAULT 1.0, status TEXT (mapped|ignored)
  UNIQUE (swype_menu_item_id, swype_modifier_id)

mapping_substitutions
  id, mapping_id FK, from_item_id FK, to_item_id FK, qty_factor NUMERIC DEFAULT 1.0
```

Resolution per sales line: base mapping (modifier NULL) × product of matching modifier multipliers, then apply matching substitutions. No base mapping → `missing_mapping` issue; qty reported under "unmapped sales".

### 3.9 Imports

```
import_batches
  id, location_id, source TEXT (swype_file|swype_api|sheet|xlsx|portal),
  dataset TEXT (sales|modifiers|items|counts|movements),
  file_name, file_sha256, period_from, period_to,
  rows_received, rows_inserted, rows_duplicate, rows_error, error_log JSONB, imported_at
```

`file_sha256` rejects the identical file twice; row-level keys reject the same row from a re-edited file.

### 3.10 Reconciliation results

```
reconciliation_runs
  id, location_id, opening_session_id FK, closing_session_id FK,
  period_from (= opening.counted_at), period_to (= closing.counted_at),
  run_at, status TEXT (complete|incomplete), summary JSONB, input_batches UUID[]

reconciliation_rows   (run_id, item_id) PK
  unit, opening_qty, deliveries_qty, transfers_in_qty, transfers_out_qty, supplier_returns_qty, closing_qty,
  expected_qty, actual_qty, gross_variance_qty, gross_variance_pct NULL,
  explained_qty, unexplained_qty, unit_cost, unexplained_cost,
  completeness TEXT (complete|missing_opening|missing_closing|no_movements_recorded),
  issues JSONB

reconciliation_contributions   run_id, item_id, swype_menu_item_id, recipe_version_id, qty_sold, expected_qty

data_issues   id, run_id NULL, code, entity, entity_id, detail JSONB, resolved BOOLEAN
```

---

## 4. Calculation engine

A run is defined by (location, opening count session, closing count session). Pure function of posted data; results frozen.

### 4.1 Expected usage

```
for sales_line where period_from < sold_at ≤ period_to and classification = 'sale':
    rule = resolve_mapping(menu_item, modifiers)
    if none: issue(missing_mapping); unmapped_sales += qty; continue
    version = recipe_version_at(rule.recipe, sold_at)
    if none: issue(no_recipe_version_at_date); continue
    for line in version.lines (after substitutions):
        q = convert(line.qty, line.unit → item.stock_unit, via yield if cross-dimension)
        if not convertible: issue(unit_conversion_missing); continue
        expected[item] += q × rule.multiplier × sales_line.qty
        contribution[item, menu_item, version] += q × rule.multiplier × sales_line.qty
```

### 4.2 Actual depletion

```
opening  = opening_session.lines[item].qty          # missing → row completeness = missing_opening, actual = NULL
closing  = closing_session.lines[item].qty          # missing → missing_closing, actual = NULL
received = Σ delivery + Σ transfer_in   where period_from < occurred_at ≤ period_to
removed  = Σ transfer_out + Σ supplier_return       (same window)
actual   = opening + received − removed − closing
```

- Breakage / other_explained are **not** in this formula; they are already inside the opening→closing delta and are subtracted once in 4.3.
- If the location has zero movements of any kind in the window, the run is marked `no_movements_recorded` (a warning, since a quiet week with no deliveries is possible; the manager confirms).
- Both counts are physical by definition here, so the dashboard label is "actual depletion".

### 4.3 Variance

```
gross_variance      = actual − expected                       (+ = used more than recipes predict)
gross_variance_pct  = expected == 0 ? NULL ("N/A") : gross_variance / expected × 100
explained           = spilled_usage + Σ breakage_expired + Σ other_explained in window
unexplained         = gross_variance − explained
unexplained_cost    = unexplained × unit_cost in effect at period_to
```

`spilled_usage[item]` is computed exactly like expected usage (§4.1) but over sales lines with `classification = 'waste'`: the same mapping, recipe version, multiplier and substitution rules apply. So a spilled Double Vodka Soda explains 90 ml of vodka, not a flat amount.

Double-count guard: Swype "spilled" lines are the **only** place drink-level waste is recorded. Portal movements of kind `breakage_expired` are for stock lost before it was poured — a dropped bottle, an expired keg — and the Movements screen and sheet say so in the kind's label ("Breakage / expired (not spills — ring those in Swype)").

### 4.4 Acceptance example (first automated test)

Opening count 3,000 ml vodka; no deliveries/transfers; closing count 2,480 ml; 10 cocktails sold at 45 ml each; waste 20 ml (in the test, a portal `breakage_expired` movement of 20 ml; a second variant uses a Swype line with discount "spilled" for a 20 ml-vodka item and expects the same result).

| Output | Value |
|---|---|
| Expected usage | 450 ml |
| Actual depletion | 520 ml |
| Gross variance | +70 ml (+15.6 %) |
| Unexplained variance | +50 ml |

Also asserted: uploading the same sales CSV twice does not change expected usage; uploading the same delivery sheet twice does not change actual depletion; no outbound call to Swype occurs during a run.

---

## 5. SwypePOS integration (sales only)

### 5.1 What the portal needs from Swype

Two exports per period, both Excel/CSV from the Swype back office:

1. **Item-level sales report** — ticket id, line no (if present), menu item id/name, quantity, gross amount, discount name and amount, timestamp. The discount name is how waste is detected ("spilled").
2. **Modifier report** — ticket id, line no (if present), menu item, modifier name/id, quantity, timestamp.

The menu catalogue (items, modifiers) is derived from these two files; unseen items appear as "new, unmapped" after each import.

Known: Swype's back office has sales history by date range with unit sales and audit reports for voids/discounts/returns. No public API; a partner API exists (Deliverect, GetOrder). Request read-only access via info@swypepos.com; the two-file import is the baseline either way.

### 5.2 Verify (Phase 0) — needs one week's pair of exports

- Does each file carry a ticket id, and a line number? With both, the join is exact. With ticket id only, the fallback join is (ticket, menu item) and a ticket with two of the same item and one modifier is ambiguous → flagged, not guessed.
- Is "Double" a modifier or a separate menu item? (Either works; it only changes mapping rows.)
- Is a 100 %-discounted "spilled" line exported with its original quantity, and does the discount column carry the name "spilled" verbatim? Case-insensitive match; configurable in Settings.
- Are voids/refunds already netted out, or exported as negative lines / a separate section?
- Timezone of timestamps (assume America/Vancouver unless the file says otherwise).

### 5.3 Adapter

```python
class SwypeSalesSource(Protocol):          # no write methods exist
    def fetch_sales(self, since, until) -> list[SaleLine]            # with discount name
    def fetch_modifiers(self, since, until) -> list[ModifierLine]
```

| Adapter | Status |
|---|---|
| `FileSwypeSource` | Build first. Accepts .xlsx or .csv for each report; saved column mapping per report; idempotent on (ticket, line no) or row hash; file SHA-256 rejects an identical re-upload. |
| `ApiSwypeSource` | Only if Swype grants read access. Scheduled pull every 15 min; same DTOs. |

### 5.4 Modifier join

Runs after both files for a period are imported (either order):

1. Exact: `(ticket_id, line_no)` when both files have line numbers.
2. Fallback: `(ticket_id, swype_menu_item_id)` when the sales line for that ticket+item is unique.
3. Otherwise the modifier row stays unjoined → `data_issue(modifier_unjoined)`; the sales line is costed on its base recipe and flagged, so the expected-usage total is visibly a floor, not silently wrong.

Imports for a period are marked "sales only" until a modifier file for the same date range has been imported; the dashboard header shows this.

---

## 6. Spreadsheet import — .xlsx now, Google Sheets sync later

**v1 ships the .xlsx path only:** the manager downloads a pre-filled workbook from the portal (Items / Counts / Movements tabs), fills it, uploads it. The parser, validation and idempotency rules below apply to that upload exactly as they will to the Sheets sync. The Google Sheets integration (§6.1, scheduled sync) is a later phase.

When it arrives: the bar's existing Google Sheet becomes the manager's working document. The portal **reads** it; the sheet is a transport, not the ledger. Every sync becomes an `import_batch`; rows become immutable count lines or movements keyed by `external_ref`. Portal entry and sheet entry coexist: a count can be typed into either, and the same idempotency rules apply to both.

### 6.1 Access

- Google Cloud service account with the Sheets API enabled; the manager shares the spreadsheet with the service-account email as **Viewer**. Credentials JSON lives in the Docker env, never in the repo.
- Read-only by construction: the client is created with the `spreadsheets.readonly` scope, so even a bug cannot write to the sheet.
- Sync triggers: "Sync now" button, and a schedule (default hourly, plus automatically before any reconciliation run).
- Fallback: the same three tabs downloaded as .xlsx and uploaded, for when the PC is offline or the API is not set up yet. Identical parser.

### 6.2 Sheet layout (the portal can generate this layout into a new sheet, or adopt the existing one via column mapping)

| Tab | Columns | Notes |
|---|---|---|
| `Items` | sku, name, category, stock_unit, pack_name, pack_size, case_size, pack_cost, yield_to_unit, yield_qty, par_level, active | `sku` is the stable key. New sku = new item; changed cost = new cost-history row. |
| `Counts` | count_date, sku, packs_full, packs_partial, note | One row per item per count. All rows sharing a `count_date` form one count session. A new `count_date` = an on-demand count. |
| `Movements` | row_id, date, sku, kind, packs, pack_cost, external_ref, note | `row_id` is an ARRAYFORMULA-generated or manually filled UUID/short id; `external_ref` is the invoice number (deliveries) or free text. Idempotency key is `(sku, kind, row_id)`; `external_ref` is shown for audit. |

The `kind` column is validated against: delivery, transfer_in, transfer_out, supplier_return, breakage_expired, other_explained. (The sheet's data-validation dropdown is set from the same list.) There is deliberately no "spill" kind — spills are rung in Swype.

### 6.3 Sync rules

- Validate every tab fully before writing anything: unknown sku, bad kind, non-numeric qty, duplicate `row_id`, a `count_date` in the future → the sync fails with a row-by-row list, and the previous good state stands.
- `Counts`: a `count_date` with no posted session → new session, posted automatically (the sheet is the manager's posting action). A `count_date` whose rows changed since the last sync → a superseding session, with the diff shown on the Counts screen. Unchanged → no-op.
- `Movements`: new `row_id` → insert; existing `row_id` with changed cells → superseding movement (old one marked superseded, kept for audit); deleted row → movement marked `retracted`, never physically deleted.
- `Items`: upsert by sku; deactivation via `active = FALSE`, never deletion.
- Each sync stores the sheet's revision/modified time; an unchanged sheet is a one-line "nothing new" batch.

### 6.5 One-time import of the bar's existing workbook

The bar's current inventory workbook ("To Order Inventory List") has one tab per month and, per week, three columns per item: **On hand** (packs, decimal — 4.7 bottles, 2.25 kegs, 42 beers), **Ordered** (free text: `2 case`, `5 bottles`, `4 flats - mojito`, `3x30l - solaris summer ale`, `2 case* never received`) and **Sold** (hand-computed from Swype, as ounces ÷ 26 for spirits and wine, pours ÷ 110 for draft). Items are grouped under section headers (spirits, wine, boxed wine, bottled beer, scotch, draft) with par levels embedded in names (`Forty Creek (par 4)`), BCLDB SKUs for most but not all items, spelling drift between months (`Kaluah` → `Kahlua`), and a "Feature Cocktail Sales" block that is sales data, not stock.

What this tells the design:

- The bar already counts **weekly** and already does the expected-vs-actual comparison by hand. The portal replaces the `Sold` column and the hand formulas; the `On hand` column is exactly a count session.
- Conventions: **26 oz per 750 ml bottle** (confirms the imperial ounce — 26 × 28.41 = 739 ml; US would be 769), **110 pours per 50 L keg** (≈ 454 ml per pour), wine by the glass at **6 oz and 9 oz**. These become the seed recipes for draft pints and wine pours.
- Kegs come in 30 L and 50 L; beer arrives in cases or flats; `case_size` is needed per item to make sense of orders.

`LegacyWorkbookImporter` (Phase 1, run once, then kept for re-runs if the manager edits the old book):

| Reads | Produces |
|---|---|
| Section headers | `category` |
| Item name | `name` (par stripped), `par_level` from `(par N)` |
| SKU column | `sku`; items without one get `GEN-<slug>` and a review flag |
| Every weekly `On hand` column, every tab | One **count session per week** (June → Oct 2026 = ~20 sessions of history), `counted_at` = the Sunday of that week unless told otherwise |
| `Ordered` column | Parsed into `delivery` movements when it matches `N case(s)`, `N flat(s)`, `N bottle(s)`, `N` or `Nx30l`; anything else (`never received`, product names) is kept as a review item, never guessed |
| `Sold` column, cocktail block | Ignored (replaced by Swype sales) |

Name matching across tabs is by SKU first, then by normalized name; unmatched names are offered as "same as X?" on a review screen before anything is written. The draft item master generated from the Oct 2026 tab (96 items: 43 spirits, 15 wine, 5 boxed wine, 10 bottled beer, 9 scotch/whisky, 14 draft; 25 without a BCLDB SKU) carries assumed pack and case sizes highlighted for confirmation.

### 6.4 Writing back (optional, later)

A reconciliation result could be pushed to a `Reconciliation` tab. Not in v1; it would need write scope and would be the only write the portal performs anywhere.

---

## 7. Screens

1. **Dashboard** — pick location + opening/closing counts (or a date range that snaps to the nearest posted counts). Table from §3.10, one row per ingredient; sortable, filterable; highlights for |unexplained %| above threshold (default 10 %), incomplete rows, issues. Header: last sales import, last count, label "actual depletion". Export.
2. **Row drill-down** — menu items and recipe versions behind expected usage; the counts and movements behind actual.
3. **Inventory items** — master list, inline edit, cost history, .xlsx download/upload.
4. **Counts** — start a count (all active items pre-listed), enter packs full/partial, post; or upload a count sheet. History of sessions.
5. **Movements** — enter deliveries/transfers/returns/breakage by item and packs, or upload the Movements tab.
6. **Recipes** — editor creating a new version on save; version history; live unit check against the item's stock unit.
7. **Mapping** — Swype items × modifiers; filter to unmapped items that sold; multipliers, substitutions, ignore.
8. **Imports & issues** — batch history, errors, open data issues, "re-run affected periods".
9. **Settings** — ounce definition, variance threshold, Swype report column mappings, discount classifications (Google Sheet connection added in Phase 5).

---

## 8. Architecture

```
┌───────────────┐   ┌─────────────────────────────────┐   ┌────────────┐
│ Browser (SPA) │──▶│ API (FastAPI)                    │──▶│ PostgreSQL │
└───────────────┘   │  - inventory, counts, movements  │   └────────────┘
                    │  - recipes, mappings             │
                    │  - Google Sheets sync (read-only)│◀── Google Sheet (Items / Counts / Movements)
                    │  - xlsx fallback (openpyxl)      │◀── .xlsx upload
                    │  - reconciliation engine         │
                    │  - SwypeSalesSource adapter ◀────┼──── SwypePOS sales + modifier reports (xlsx/csv)
                    │  - scheduler (APScheduler)       │
                    └─────────────────────────────────┘
```

| Layer | Choice |
|---|---|
| API | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic; openpyxl (google-api-python-client added in Phase 5) |
| DB | PostgreSQL 16 |
| Frontend | React + Vite + TypeScript, Tailwind, TanStack Table, Recharts; served as static files by the API container |
| Auth | Single admin login, session cookie; app bound to `localhost` by default |
| Units | Stored in ml/g/each. Recipe editor defaults to oz; `oz_ml` setting = 28.4131 (Canadian/imperial, default) or 29.5735 (US). |
| Jobs | APScheduler in-process: weekly count reminder (hourly Sheets sync added with Phase 5) |
| Packaging | Docker Compose (`api`, `db`) on Docker Desktop, Windows target and macOS dev: `.gitattributes` forces LF, named volumes (no bind-mounted DB), `.env` for config; one `docker compose up -d` on either OS |
| Backups | nightly `pg_dump` to a host-mounted `./backups` folder, path set in `.env` (on the Windows PC this can sit inside a Google Drive for Desktop folder so it syncs off the machine) |
| Tests | pytest; §4.4 example (both waste variants), idempotency tests for every import path, unit-conversion and version-at-date property tests |

Outbound network from the container: none in v1. Google Sheets API from Phase 5; Swype's API only if it is ever granted.

### API surface

```
POST  /auth/login
GET   /locations
GET/POST/PATCH  /items                 GET /items/export.xlsx   POST /items/import
GET/POST        /counts                POST /counts/{id}/post    GET /counts/template.xlsx   POST /counts/import
GET/POST        /movements             GET /movements/template.xlsx   POST /movements/import
POST  /sheets/sync   GET /sheets/status            (Phase 5)
GET/POST/PATCH  /recipes, /recipes/{id}/versions
GET/PUT         /mappings, /mappings/{id}/substitutions
GET   /swype/menu-items, /swype/modifiers
POST  /imports/sales (csv)             POST /sync/sales (api)    GET /imports
POST  /reconciliations {location, opening_session, closing_session}
GET   /reconciliations/{id}, /rows, /rows/{item}/contributions, /export?format=csv|xlsx
GET/PATCH  /issues
```

---

## 9. Build plan

| Phase | Deliverable | Gate |
|---|---|---|
| **0 — Samples** | One week's Swype sales + modifier exports; answers to §5.2. Inventory workbook: received. | Manager |
| **1 — Inventory** | Compose stack, schema, item master, count sessions, movements; .xlsx templates with validation and idempotent import; cost history; `LegacyWorkbookImporter` (§6.5) loading the item master and five months of weekly counts. | Confirmed pack/case sizes from the draft item master |
| **2 — Recipes + sales** | Versioned recipes (oz entry), mapping rules, two-file Swype import, modifier join, discount classification, catalogue mirror. | 0, 1 |
| **3 — Engine + dashboard** | Reconciliation engine (§4), §4.4 tests passing, dashboard, drill-down, export, issues, weekly reminder. | 2 |
| **4 — Hardening** | Superseding counts/movements, re-run on recipe/mapping change, thresholds, batch audit per run, backup job. | 3 |
| **5 — Google Sheets sync** | Read-only service-account sync of Items / Counts / Movements (§6), scheduled + on demand, replacing manual .xlsx upload. | 1, 4 |
| **6 — Swype API source** | `ApiSwypeSource` + scheduled sales pull, only if Swype grants read access. | 0 |

Phase 1 is independently useful: a clean item master, count screen and delivery log before any Swype data arrives. Phases 1–3 are the prototype target.

---

## 10. Remaining questions

1. (Phase 5) Adopt the existing Google Sheet's tabs via column mapping, or create a fresh sheet in the §6.2 layout? [decide when Phase 5 starts]
2. Are the bar's jiggers/pourers marked in imperial or US ounces? The workbook's 26 oz/bottle convention points to imperial; the variance report will confirm.
3. Pack and case sizes per item — highlighted in the draft item master; mainly which spirits are 1.14 L, which taps run 30 L kegs, and whether beer flats are 24 or 12.
4. Which day of the week the count is taken (needed to date the historical count sessions).
