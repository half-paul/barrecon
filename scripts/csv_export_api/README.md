# SwypePOS caffebarney — Export Items CSV

Reverse-engineered Python client for
`https://swypepos.com/RMC/caffebarney/en/?p=Import-ExportItem`

## How it works

The "Export Item (CSV)" feature is **fully client-side**:

1. Browser GETs `/RMC/caffebarney/en/Import/ExportItem`
2. Server embeds all item data as JSON inside hidden `<input>` elements on the page
3. Browser-side JavaScript (ExcelJS) builds the CSV locally and downloads it
4. A fire-and-forget POST to `/Api/Item/ExportFileLog` logs the event

This script replicates that flow in Python — no dedicated server-side CSV endpoint exists.

## APIs discovered

| # | Method | URL | Purpose |
|---|--------|-----|---------|
| 1 | POST | `/RMC/Api/Router.aspx?controller=User&action=Login&company=caffebarney&lang=en` | Login — returns session cookie |
| 2 | GET  | `/RMC/caffebarney/en/Import/ExportItem` | Export Item page — all item data embedded as JSON |
| 3 | POST | `/RMC/Api/Router.aspx?controller=Item&action=ExportFileLog&company=caffebarney&lang=en` | Log the CSV export (fire-and-forget) |
| 4 | POST | `/RMC/Api/Router.aspx?controller=Report&action=ItemSalesReport&company=caffebarney&lang=en` | Generate Item Sales Excel — returns `excelKey` |
| 5 | GET  | `/RMC/download?type=d&file={excelKey}` | Download the generated `.xlsx` file |

## Authentication

- **Method**: Form-encoded POST to the Login API
- **Credentials**: `SWYPE_EMAIL` and `SWYPE_PASSWORD` environment variables (load with `set -a; . ./.env; set +a`)
- **Session**: `ASP.NET_SessionId` cookie (set by server on successful login)
- **Re-auth**: `with_reauth()` helper re-logs in automatically on 401/session-expired

> ⚠️  Must use `Content-Type: application/x-www-form-urlencoded` for login.
> JSON body returns `result.status: "FIRST_LOGIN"` and no session cookie.

## Quick start

```bash
pip install requests

# Export item catalogue to CSV
python api_client.py --mode items-csv

# Export Item Sales Report to Excel (last 365 days)
python api_client.py --mode sales-excel

# Export Item Sales Report for a specific date range
python api_client.py --mode sales-excel --from-date 2024-01-01 --to-date 2024-12-31
```

## Usage examples

### Export all columns (default)

```python
from api_client import export_items_csv

csv_text = export_items_csv(
    output_path="items.csv",
    include_all_cols=True,
)
```

### Export specific columns

```python
from api_client import export_items_csv, AVAILABLE_COLUMNS

csv_text = export_items_csv(
    output_path="items_slim.csv",
    selected_col_codes=["ItemCode", "ItemDesc1", "Price", "Status"],
)
```

### Lower-level access to raw data

```python
from api_client import create_session, login, fetch_export_page_data, generate_csv

session = create_session()
login(session)
data = fetch_export_page_data(session)

# data keys: items, item_prices, categories, price_types, item_cols
print(f"{len(data['items'])} items fetched")

csv_text = generate_csv(data, include_all_cols=True)
```

## Available columns

| Code | CSV Header |
|------|-----------|
| `ItemCode` | Item Code |
| `CategoryId` | Category Code (resolved to CategoryCode string) |
| `ItemDesc1` | Item Desc1 |
| `Status` | Status |
| `SellingUnit` | Selling Unit |
| `MinOrderQty` | Minimum Order Qty |
| `NeedBag` | Need Package |
| `FractionalQty` | Allow Fractional Qty |
| `HideSelfOrder` | Hide in OLO / Self Ordering |
| `Price` | Default Price |
| `MinPrice` | Default Minimum Price |
| `Price1` | Single Price (PriceTypeId=1) |
| `MinPrice1` | Single Minimum Price (PriceTypeId=1) |
| `Memo1` | Memo Desc1 |
| `Memo2` | Memo Desc2 |
| `Memo3` | Memo Desc3 |
| `KitchenMemo` | Kitchen Memo |

## Output

- UTF-8 with BOM (`﻿`) — matches the browser's `writeBOM: true` setting
- Filters to **normal items only** (`ItemType='N'`, `IsModifier=false`) — 895 of 1446 total items
- 896 rows total (1 header + 895 data rows) for the caffebarney dataset

### Export Item Sales Report to Excel

```python
from api_client import export_item_sales_excel
from datetime import datetime

# Last 365 days (default)
xlsx_bytes = export_item_sales_excel(output_path="item_sales.xlsx")

# Custom date range
xlsx_bytes = export_item_sales_excel(
    output_path="item_sales_2024.xlsx",
    date_from=datetime(2024, 1, 1),
    date_to=datetime(2024, 12, 31, 23, 59, 59),
)

# Include items with zero sales
xlsx_bytes = export_item_sales_excel(
    output_path="item_sales_all.xlsx",
    include_non_sales=True,
)
```

### Lower-level: generate without saving

```python
from api_client import create_session, login, fetch_item_sales_excel
from datetime import datetime

session = create_session()
login(session)
xlsx_bytes = fetch_item_sales_excel(
    session,
    date_from=datetime(2024, 1, 1),
    date_to=datetime(2024, 12, 31, 23, 59, 59),
    sort_by="Amount",          # sort by sales amount
    sort_by_type="Descending",
    include_non_sales=False,
)
with open("report.xlsx", "wb") as f:
    f.write(xlsx_bytes)
```

## Item Sales Report parameters

| Parameter | Default | Options |
|-----------|---------|---------|
| `price_type` | `0` (All Types) | `0`=All, `1`=Single, `-2`=Special, `-3`=Open |
| `sort_by` | `"Item"` | `"Item"`, `"Amount"`, `"Qty"`, `"Desc"` |
| `sort_by_type` | `"Descending"` | `"Descending"`, `"Ascending"` |
| `report_type` | `"Individual"` | `"Individual"`, `"Consolidate"`, `"Comparison"` |
| `include_non_sales` | `False` | include items with zero sales |
| `print_current_sales` | `False` | use server's current business date |
| `selected_cat_ids` | `None` (all) | list of CategoryId ints to filter |
| `store_ids` | `[1]` (Caffe Barney) | list of StoreId ints |

## Caveats

- Sessions expire on the server side (ASP.NET session); script re-authenticates automatically
- The export page is a large HTML payload (~2.4 MB) — first call for items-csv takes a few seconds
- Credentials come from `.env`; update `SWYPE_PASSWORD` there if the password changes
- No CAPTCHA on login as of the time of reverse engineering; if one appears, `GetImageVerify` must be called first to get a `VerifyId`/`VerifyCode` pair
- **Item Sales API requires form-encoded POST** (`application/x-www-form-urlencoded`). A JSON body causes the server to return `excelKey: ""` (silent failure)
- `excelKey` filenames are time-scoped — download immediately after the POST; the server may delete stale files
