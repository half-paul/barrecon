# Food and beverage inventory dashboard — AI implementation brief

## Task for the implementing AI

Build an ingredient-first inventory reconciliation dashboard for a restaurant and bar. Follow this specification and the accompanying interactive design reference. Use the existing project's technology stack when one exists; otherwise choose a simple, maintainable web stack and document your choice. Implement a working local demo using `sample-data.json`. Keep integrations behind adapters until actual provider interfaces and credentials are supplied.

Default design: **Blue ledger**. The other alternatives are **Terracotta cards** and **Indigo workspace**. They share one calculation model and dataset. Do not treat the designs as separate products or change calculations between designs. If implementing all three, use a design switcher with persistent local preference.

Deliver source code, setup instructions, the runnable demo, meaningful calculation tests, and a short list of integration assumptions. Do not stop at a screenshot or static mockup. Do not claim live POS connectivity.

## Files and how to use them

- `dashboard-designs.html`: self-contained, browser-openable reference with all three designs. It has no external dependencies and no live data connections.
- `sample-data.json`: machine-readable demo ingredients, menu items, recipe links, sales totals, movements, and physical closing counts.
- `expected-results.json`: independently checked expected calculation results for the fixture.
- This document is the product and implementation specification. Where the visual mockup is simplified, the calculation and data rules here take precedence.

## User goal

For each inventory item, immediately answer:

1. Which menu items used this ingredient?
2. How many units of each menu item were sold?
3. How much of this ingredient does one menu item require?
4. How much was theoretically used across all linked sales?
5. How much stock should be left?
6. How much stock is actually left, based on a physical count?
7. What is the difference?

Inventory is shared. For example, the same chicken stock supplies both a salad and a sandwich; the same Prosecco stock supplies a Spritz and a glass of Prosecco. Never create a separate stock balance for each menu item.

## Terminology and display

Use **Inventory item**, **Ingredient**, **Menu item**, **Units sold**, **Per item**, and **Recipe usage**. Food and beverages are menu-item categories. Do not use “cocktails” or “drinks sold” as generic headings.

Primary stock labels:

- **Should be left**: calculated expected closing stock.
- **Actually left**: measured physical closing stock, with source and timestamp.
- **Difference**: signed variance; also display “short”, “over”, or “matches”.

“Recipe usage” is estimated consumption based on sales and recipes. Do not label it actual consumption. A stock difference is not proof of theft, overpouring, or waste.

Each ingredient's expanded section shows a table:

| Menu item | Units sold | Per item | Recipe usage |
| --- | --- | --- | --- |
| Chicken Caesar salad | 30 | 150 g | 4.50 kg |
| Chicken sandwich | 20 | 180 g | 3.60 kg |
| Total ingredient usage | — | — | 8.10 kg |

The section header shows opening stock, received stock, expected remaining stock, actual remaining stock, and variance. If waste, transfers, or adjustments exist, show them in the balance detail; do not silently omit them from the explanation.

Default the ledger's ingredient sections to expanded. Allow collapse while retaining the stock comparisons in the header. Actual stock must never be hidden behind a hover interaction. Display unknown counts as “Not counted”; a missing count is not zero.

## Three visual options

### Blue ledger — recommended default

Cool blue background, white panels, navy text, cobalt accent. A compact grouped horizontal bar chart compares expected and actual remaining stock as percentages of the available stock for each ingredient. Below it, full-width ingredient sections show the shared balance and linked menu items. Best suited to routine checking and reconciliation.

Light colors: background `#f0f4fa`, panel `#ffffff`, text `#172b46`, muted `#5e7088`, border `#dbe3ee`, accent/actual `#275cc6`, expected `#95afdb`.

### Terracotta cards

Warm cream background with terracotta accent and brown text. Two-column ingredient cards on desktop, one column on mobile. Every card contains the balances, linked menu-item table, and a paired expected/actual stock bar. Best suited to visual scanning across a small or medium inventory.

Light colors: background `#f7f1e9`, panel `#fffcf7`, text `#443024`, muted `#7d6858`, border `#e5d8c8`, accent/actual `#aa4e2a`, expected `#d2bfa7`.

### Indigo workspace

Indigo accent, pale violet surfaces, and a two-pane layout. The ingredient list shows expected stock, actual stock, and variance for every ingredient. Selecting an ingredient opens its balances, full linked-menu table, paired stock bars, and horizontal bars showing each menu item's share of ingredient usage. Best suited to investigating an individual discrepancy.

Light colors: background `#f2f1fa`, panel `#fffefe`, text `#282647`, muted `#716c8d`, border `#dfdcee`, accent/actual `#6652bc`, expected `#b8afd6`.

All designs have corresponding dark appearance tokens in the reference HTML. Preserve readable contrast, native focus indicators, text labels on status, and responsive layouts. Use approximately 14 px body text, 11–12 px secondary labels, 28 px page titles, restrained rounding, and tabular figures for quantities.

## Chart rules

- Expected and actual must have consistent encodings and direct numeric labels.
- For the cross-ingredient overview, use a normalized scale: remaining / available × 100. Use the same 0–100% scale for all demo ingredients.
- Each card's quantitative bars use that ingredient's available quantity as the common denominator for both expected and actual. State the basis in accessible text. Never normalize each bar independently.
- Usage contribution bars show each linked menu item's recipe usage divided by that ingredient's total recipe usage.
- Do not sum kilograms, litres, and pieces into a “total inventory” quantity. Do not compare raw mixed units on one common axis.
- For zero available stock, percentages are unavailable. If actual or expected exceeds available, or expected is negative, expose a data warning and use an expanded/signed scale; never silently clip the value.
- No decorative trend chart without dated historical data. No fabricated cost, margin, forecasting, or replenishment metrics.

## Calculation contract

Calculate per ingredient, location, and count-bounded reporting interval. Use precise decimal arithmetic or appropriate scaled integers. Round for display only.

```text
usage_by_menu_item = sum(fulfilled quantity × effective recipe quantity)
recipe_usage = sum(usage_by_menu_item across all menu items)
available = opening + received + transfer_in - transfer_out + signed_adjustments
expected_closing = available - recipe_usage - recorded_waste
variance = physical_closing_count - expected_closing
```

Variance < 0 means short; variance > 0 means over. Use ingredient-specific counting tolerances for “matches”; retain the numeric difference even when within tolerance. Demo tolerances are zero because quantities are exact fixtures.

For chicken in the demo: 10 kg opening + 5 kg received − 8.1 kg recipe usage = 6.9 kg expected; 6.5 kg counted = 0.4 kg short.

Do not add linked sales quantities across ingredient sections to derive total menu units sold: the same sale appears under multiple ingredients. Calculate total sales directly from unique sale records (130 units in the fixture).

Avoid double subtraction. Physical counts establish checkpoints and variances; a closing count must not also be inserted as a stock adjustment in the interval ending at that count. Recorded waste must not duplicate consumption already included through a fulfilled sale.

## Units, recipes, and food preparation

- Canonical stock units: `g` for mass, `mL` for volume, and `each` for count. Display kg, L, and pieces where appropriate.
- Convert case/bottle/bag purchasing units using item-specific pack quantities: for example, a case of 12 × 750 mL bottles adds 9,000 mL.
- Do not convert mass to volume without an ingredient-specific density. Do not infer piece weights or cooking yields.
- The demo uses an explicit **house pour of 30 mL per oz**. This is a configurable serving assumption, not a claim that every fluid-ounce standard equals 30 mL. Production must distinguish the venue's serving measure from formal unit conversions.
- Demo food recipe quantities are raw inventory-equivalent quantities. In production, distinguish raw, prepared, and cooked ingredients. Record yield conversion explicitly rather than applying guessed shrinkage.
- Support versioned recipe ingredients and effective dates. Historical sales use the recipe active at fulfillment time, including applicable modifiers and portion sizes.
- For batch recipes, use one consistent strategy: deplete raw ingredients at batch production and deplete prepared stock on sale, OR expand recipes to raw-equivalent usage. Never apply both to the same stock movement.
- Demo recipes intentionally contain only tracked ingredients. Production should flag incomplete recipe mappings. No mapping must not silently mean zero consumption.

## Suggested production data model

```typescript
type BaseUnit = 'g' | 'mL' | 'each';
type Ingredient = {
  id: string; name: string; baseUnit: BaseUnit;
  countTolerance: string; // Decimal quantity in base units
};
type MenuItem = { id: string; name: string; category: string; posItemId: string };
type RecipeVersion = {
  id: string; menuItemId: string; effectiveFrom: string; effectiveTo: string | null;
};
type RecipeLine = {
  recipeVersionId: string; ingredientId: string; quantityBase: string;
};
type SaleLine = {
  sourceId: string; locationId: string; menuItemId: string;
  fulfilledAt: string; quantity: string; recipeVersionId: string;
  fulfillmentStatus: 'fulfilled' | 'cancelled_before_preparation';
  modifiers: unknown[];
};
type StockMovement = {
  sourceId: string; ingredientId: string; locationId: string; at: string;
  kind: 'receipt' | 'transfer_in' | 'transfer_out' | 'waste' | 'adjustment';
  quantityBase: string; reason: string;
};
type StockCount = {
  id: string; ingredientId: string; locationId: string;
  countedAt: string; quantityBase: string; source: string;
};
```

Enforce unique provider/source IDs to prevent duplicate imports. Persist raw imported records separately from derived reconciliation. Keep stock-count lineage and adjustment audit records.

The fixture is a simplified reporting model with aggregate sales, not the production transaction schema. Implement an adapter from transaction records to the reporting model. The actual inventory source may be SwypePOS, but no API capability has been verified or assumed. Start with the JSON fixture and a documented CSV adapter contract; add a provider adapter only when its documented interface or export format is supplied.

## Time and data-quality behavior

- Define a service interval using the location timezone, with inclusive start and exclusive end. A restaurant's service day can cross midnight.
- Reconcile a physical count only against movements and fulfilled items through that count's timestamp. Never compare today's partial sales against last week's count as though both cover the same interval.
- If count time differs from the selected report end, show the actual count timestamp and either reconcile through that timestamp or clearly flag the mismatch.
- Refunds do not automatically put food or beverages back on the shelf. Cancelled-before-preparation items do not consume ingredients. Fulfilled comped items and staff meals still consume ingredients; represent them explicitly without confusing them with sold quantities.
- Show “Not counted” and no variance when a physical count is absent. Show receipt/sales/count freshness independently. Flag duplicate imports, missing mappings, incompatible units, and negative expected stock.

## Functional requirements

1. Load the sample fixture and derive the dashboard rather than hardcoding calculated values.
2. Show all ingredient-to-menu relationships and correct quantities.
3. Support ingredient collapse in the ledger and ingredient selection in the workspace, with keyboard access.
4. On small screens, stack cards and panes. Confine horizontal scrolling to genuinely wide tables.
5. Show the same values in all three designs. Keep UI state separate from calculations.
6. In production, allow location and count-bounded period selection; clearly distinguish these unconnected controls from the demo's fixed interval.
7. For the implementation, provide ingredient search and filters for Short / Over / Matches / Not counted, but keep the first release focused on reconciliation.
8. Render imported names as text, not unsanitized HTML. The reference's embedded static strings are trusted demo content; do not copy its template interpolation as an untrusted-data rendering strategy.
9. Keep integration secrets on the server. Apply location authorization to every server query and use read-only reporting by default. Editing recipes, movements, and counts is separate, auditable functionality.

## Acceptance checks

| Ingredient | Recipe usage | Expected left | Actual left | Signed variance |
| --- | ---: | ---: | ---: | ---: |
| Chicken | 8.10 kg | 6.90 kg | 6.50 kg | −0.40 kg |
| Tomato | 6.10 kg | 3.90 kg | 4.00 kg | +0.10 kg |
| Bread slices | 90 pcs | 30 pcs | 30 pcs | 0 pcs |
| Prosecco | 5.85 L | 3.15 L | 3.00 L | −0.15 L |
| Aperol | 2.40 L | 1.30 L | 1.15 L | −0.15 L |
| Soda | 4.00 L | 4.00 L | 4.00 L | 0.00 L |

- Unique menu sales total 130; status counts are 3 short, 1 over, and 2 matches.
- Prosecco must show Aperol Spritz (40 sold × 90 mL = 3.60 L) AND Prosecco glass (15 sold × 150 mL = 2.25 L).
- Tomato must show three linked menu items; chicken and bread each show two.
- Changing the sandwich count from 20 to 21 adds 180 g chicken, 60 g tomato, and 2 bread slices to recipe usage, and decrements expected stock accordingly. Actual physical counts remain unchanged.
- Adding 500 g recorded chicken waste changes chicken expected stock to 6.40 kg; against the same 6.50 kg physical count, variance becomes +0.10 kg.
- Null count gives “Not counted”; zero count is valid and gives a numeric variance.
- Reimporting a sale with the same provider/source identity does not double-count usage.
- Recipe changes midway through a reporting interval apply to the appropriate sales only.
- Reject a recipe line in mL for a mass-only ingredient unless a configured conversion exists.
- Graph percentages agree with quantities, empty denominators are handled, and no chart mixes incompatible units.
- Validate keyboard interaction, readable light/dark contrast, and responsive behavior at 360, 768, and 1280 px.

## Boundaries and provenance

This is an implementation proposal derived from the user's requirements and synthetic demonstration data. It contains no live restaurant information and makes no assertion about any POS vendor's current API. The visual reference is a functional mockup, not a production integration, accounting system, or completed inventory ledger backend.
