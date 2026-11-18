"""
SwypePOS caffebarney — Export Items to CSV / Item Sales to Excel
================================================================
Reverse-engineered from:
  https://swypepos.com/RMC/caffebarney/en/?p=Import-ExportItem
  https://swypepos.com/RMC/caffebarney/en/?p=Report-ItemSalesReport

Architecture notes
------------------

EXPORT ITEM (CSV) — fully client-side:
  1. The page embeds ALL item data as JSON in hidden <input> elements.
  2. Browser-side JavaScript (ExcelJS) generates the CSV locally.
  3. Only a fire-and-forget log call goes to the server after download.
  This script replicates that flow in Python (fetch page → parse JSON → write CSV).

ITEM SALES REPORT (Excel) — server-side:
  1. POST form data to /Api/Report/ItemSalesReport with ExportExcel=true.
  2. Server generates an .xlsx file, returns a key (excelKey).
  3. Download the file via GET /RMC/download?type=d&file={excelKey}.
  ⚠ Request MUST be form-encoded (application/x-www-form-urlencoded).
    JSON body causes the server to return an empty excelKey.
"""

from __future__ import annotations

import csv
import html
import io
import json
import os
import re
import sys
from datetime import datetime
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Credentials (SWYPE_EMAIL / SWYPE_PASSWORD exported in your shell) & config
# ---------------------------------------------------------------------------
BASE_URL = "https://swypepos.com/RMC"
COMPANY  = "caffebarney"
LANG     = "en"

LOGIN_EMAIL    = os.environ["SWYPE_EMAIL"]
LOGIN_PASSWORD = os.environ["SWYPE_PASSWORD"]

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         f"https://swypepos.com/RMC/{COMPANY}/{LANG}/",
}


# ---------------------------------------------------------------------------
# URL helpers (mirrors app.v2.js $app.getUrl logic)
# ---------------------------------------------------------------------------

def api_url(controller: str, action: str) -> str:
    """Build /Api/Router.aspx URL."""
    return (
        f"{BASE_URL}/Api/Router.aspx"
        f"?controller={controller}&action={action}"
        f"&company={COMPANY}&lang={LANG}"
    )


def view_url(path: str) -> str:
    """Build a /Main/ view URL.  path starts with e.g. 'Import/ExportItem'."""
    return f"{BASE_URL}/{COMPANY}/{LANG}/{path}"


# ---------------------------------------------------------------------------
# Session / auth
# ---------------------------------------------------------------------------

def create_session() -> requests.Session:
    """Return a requests.Session with shared headers."""
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def login(session: requests.Session) -> dict:
    """
    Authenticate with the SwypePOS API.

    Returns the parsed result dict on success, raises RuntimeError on failure.
    Session cookie (ASP.NET_SessionId) is stored in *session* automatically.
    """
    url = api_url("User", "Login")
    resp = session.post(
        url,
        data={
            "Email":      LOGIN_EMAIL,
            "Password":   LOGIN_PASSWORD,
            "VerifyId":   "",
            "VerifyCode": "",
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
        timeout=30,
    )
    resp.raise_for_status()

    outer = resp.json()
    if outer.get("status") != "OK":
        raise RuntimeError(f"Login API error: {outer}")

    result = outer.get("result", {})
    if result.get("status") not in ("OK",):
        raise RuntimeError(
            f"Login failed — result status: {result.get('status')!r}  "
            f"(server message: {outer.get('error_message', '')})"
        )

    return result


def ensure_authenticated(session: requests.Session) -> None:
    """Login if the session has no auth cookie yet."""
    if not session.cookies.get("ASP.NET_SessionId"):
        login(session)


# ---------------------------------------------------------------------------
# Fetch & parse export page
# ---------------------------------------------------------------------------

def _parse_hidden_json(html_text: str, css_class: str) -> list | None:
    """Extract and JSON-parse the value of a hidden input with *css_class*."""
    pattern = rf'class="{re.escape(css_class)}"[^>]*value="([^"]*)"'
    m = re.search(pattern, html_text)
    if not m:
        return None
    raw = html.unescape(m.group(1))
    return json.loads(raw)


def fetch_export_page_data(session: requests.Session) -> dict:
    """
    Fetch the Export Item page and return the embedded data dicts:
      - items       : list of item records
      - item_prices : list of price records
      - categories  : list of category records
      - price_types : list of price type records
      - item_cols   : list of exportable column definitions
    Raises RuntimeError if the page isn't accessible (session expired, etc.).
    """
    url = view_url("Import/ExportItem")
    resp = session.get(url, timeout=30)
    resp.raise_for_status()

    body = resp.text

    # Detect unauthenticated redirect / error page
    if "UNLOGIN" in body or "Sign In" in body and "AllItems" not in body:
        raise RuntimeError("Session expired or not authenticated.")

    items       = _parse_hidden_json(body, "AllItems")
    item_prices = _parse_hidden_json(body, "AllItemPrices")
    categories  = _parse_hidden_json(body, "AllCategories")
    price_types = _parse_hidden_json(body, "AllPriceTypes")
    item_cols   = _parse_hidden_json(body, "ItemColList")

    if items is None:
        raise RuntimeError("Could not parse AllItems from the export page. "
                           "Check authentication or page structure.")

    return {
        "items":       items,
        "item_prices": item_prices or [],
        "categories":  categories  or [],
        "price_types": price_types or [],
        "item_cols":   item_cols   or [],
    }


# ---------------------------------------------------------------------------
# CSV generation  (mirrors btnExport_Click JS logic)
# ---------------------------------------------------------------------------

def _build_category_map(categories: list[dict]) -> dict[int, str]:
    """Map CategoryId → CategoryCode."""
    return {c["CategoryId"]: c.get("CategoryCode", "") for c in categories}


def _build_price_index(item_prices: list[dict]) -> dict[tuple, list[dict]]:
    """Index prices by (ItemId, PriceTypeId)."""
    idx: dict[tuple, list[dict]] = {}
    for p in item_prices:
        key = (p["ItemId"], p["PriceTypeId"])
        idx.setdefault(key, []).append(p)
    return idx


def _col_value(
    item: dict,
    col: dict,
    cat_map: dict[int, str],
    price_idx: dict[tuple, list[dict]],
) -> str:
    """
    Resolve the CSV cell value for one (item, column) pair.
    Mirrors the JS forEach logic exactly.
    """
    code = col["Code"]

    # CategoryId → CategoryCode
    if code == "CategoryId":
        return cat_map.get(item.get("CategoryId"), "")

    # Price-type columns: Price1, MinPrice1, Price2, MinPrice2 …
    if code not in ("Price", "MinPrice") and (
        code.startswith("Price") or code.startswith("MinPrice")
    ):
        m = re.match(r"^(Price|MinPrice)(\d+)$", code)
        if m:
            col_key      = m.group(1)       # "Price" or "MinPrice"
            price_type_id = int(m.group(2))
            rows = price_idx.get((item["ItemId"], price_type_id), [])
            if rows:
                v = rows[0].get(col_key)
                return str(v) if v is not None else ""
        return ""

    # Boolean → 1 / ""
    v = item.get(code)
    if isinstance(v, bool):
        return "1" if v else ""

    return str(v) if v is not None else ""


def generate_csv(
    data: dict,
    selected_cols: Optional[list[dict]] = None,
    *,
    include_all_cols: bool = False,
) -> str:
    """
    Generate the CSV string from *data* (as returned by fetch_export_page_data).

    Parameters
    ----------
    selected_cols
        List of column dicts (with 'Code' and 'Name') to include.
        Defaults to all columns in ItemColList when None.
    include_all_cols
        If True, ignores *selected_cols* and exports every available column.

    Returns
    -------
    str  — UTF-8 CSV text (with BOM, matching the browser's writeBOM:true).
    """
    items       = data["items"]
    item_prices = data["item_prices"]
    categories  = data["categories"]
    item_cols   = data["item_cols"]

    if include_all_cols or selected_cols is None:
        selected_cols = item_cols

    # Build lookup structures
    cat_map   = _build_category_map(categories)
    price_idx = _build_price_index(item_prices)

    # Filter: normal items only (ItemType='N', not a modifier)
    normal_items = [i for i in items if i.get("ItemType") == "N" and not i.get("IsModifier")]

    # Write CSV
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")

    # Header row
    writer.writerow([c["Name"] for c in selected_cols])

    # Data rows
    for item in normal_items:
        row = [_col_value(item, col, cat_map, price_idx) for col in selected_cols]
        writer.writerow(row)

    return "﻿" + buf.getvalue()   # BOM prefix, matching writeBOM: true


# ---------------------------------------------------------------------------
# Log the export (mirrors post-download API call)
# ---------------------------------------------------------------------------

def log_export(session: requests.Session) -> None:
    """POST to ExportFileLog (fire-and-forget logging; failure is non-fatal)."""
    url = api_url("Item", "ExportFileLog")
    try:
        session.post(
            url,
            json={},
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=10,
        )
    except Exception:
        pass  # Non-fatal


# ---------------------------------------------------------------------------
# High-level convenience function
# ---------------------------------------------------------------------------

def export_items_csv(
    output_path: Optional[str] = None,
    selected_col_codes: Optional[list[str]] = None,
    *,
    include_all_cols: bool = False,
    log: bool = True,
) -> str:
    """
    Full pipeline: login → fetch → generate CSV → (optionally) save to file.

    Parameters
    ----------
    output_path
        If provided, write the CSV to this file path.
    selected_col_codes
        List of column Code strings to include, e.g. ['ItemCode', 'ItemDesc1'].
        Pass None (default) to include all available columns.
    include_all_cols
        If True, export every column defined in ItemColList.
    log
        If True, call the ExportFileLog endpoint after generation.

    Returns
    -------
    str — the generated CSV text.
    """
    session = create_session()

    # Authenticate
    print("Logging in…", file=sys.stderr)
    login(session)

    # Fetch export page data
    print("Fetching export page data…", file=sys.stderr)
    data = fetch_export_page_data(session)
    print(
        f"  Items: {len(data['items'])}, "
        f"Normal items: {sum(1 for i in data['items'] if i.get('ItemType')=='N' and not i.get('IsModifier'))}, "
        f"Categories: {len(data['categories'])}, "
        f"PriceTypes: {len(data['price_types'])}",
        file=sys.stderr,
    )

    # Resolve column selection
    if selected_col_codes is not None:
        col_map = {c["Code"]: c for c in data["item_cols"]}
        selected_cols = [col_map[code] for code in selected_col_codes if code in col_map]
    else:
        selected_cols = None  # → all cols inside generate_csv

    # Generate CSV
    print("Generating CSV…", file=sys.stderr)
    csv_text = generate_csv(data, selected_cols, include_all_cols=include_all_cols)

    # Log
    if log:
        log_export(session)

    # Save to file
    if output_path:
        with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
            f.write(csv_text.lstrip("﻿"))  # utf-8-sig writes BOM automatically
        print(f"Saved → {output_path}", file=sys.stderr)

    return csv_text


# ---------------------------------------------------------------------------
# Re-authentication helper (for long-running or retry scenarios)
# ---------------------------------------------------------------------------

def with_reauth(fn, session: requests.Session, *args, **kwargs):
    """
    Call fn(session, *args, **kwargs).  If it raises a RuntimeError matching
    'Session expired', re-login and try once more.
    """
    try:
        return fn(session, *args, **kwargs)
    except RuntimeError as e:
        if "expired" in str(e).lower() or "authenticated" in str(e).lower():
            print("Re-authenticating…", file=sys.stderr)
            login(session)
            return fn(session, *args, **kwargs)
        raise


# ---------------------------------------------------------------------------
# Available column codes (for reference)
# ---------------------------------------------------------------------------
AVAILABLE_COLUMNS = [
    "ItemCode",        # Item Code
    "CategoryId",      # Category Code  (resolved to CategoryCode in output)
    "ItemDesc1",       # Item Desc1
    "Status",          # Status
    "SellingUnit",     # Selling Unit
    "MinOrderQty",     # Minimum Order Qty
    "NeedBag",         # Need Package
    "FractionalQty",   # Allow Fractional Qty
    "HideSelfOrder",   # Hide in OLO / Self Ordering
    "Price",           # Default Price
    "MinPrice",        # Default Minimum Price
    "Price1",          # Single Price          (PriceTypeId=1)
    "MinPrice1",       # Single Minimum Price  (PriceTypeId=1)
    "Memo1",           # Memo Desc1
    "Memo2",           # Memo Desc2
    "Memo3",           # Memo Desc3
    "KitchenMemo",     # Kitchen Memo
]


# ---------------------------------------------------------------------------
# Item Sales Report — server-side Excel export
# ---------------------------------------------------------------------------

#: All category IDs for caffebarney (hardcoded from page; refreshed by
#: fetch_export_page_data / fetch_item_sales_page_data if needed).
ALL_CAT_IDS: list[int] = [
    101, 109, 125, 126, 127, 194, 128, 129, 130, 131, 132, 133,
    141, 143, 144, 177, 104, 102, 122, 146, 105, 103, 134, 200,
    135, 136, 137, 138, 139, 140, 142, 156, 159, 166, 168, 175,
    106, 186, 191, 123, 124, 173, 107, 108,
]

#: Default store ID for caffebarney (StoreId=1, "Caffe Barney").
DEFAULT_STORE_ID: int = 1


def _sv_date(dt: datetime) -> str:
    """Format a datetime as SmartView expects: YYYY/MM/DD HH:MM:SS."""
    return dt.strftime("%Y/%m/%d %H:%M:%S")


def fetch_item_sales_excel(
    session: requests.Session,
    date_from: datetime,
    date_to: datetime,
    *,
    price_type: int = 0,
    sort_by: str = "Item",
    sort_by_type: str = "Descending",
    report_type: str = "Individual",
    selected_cat_ids: Optional[list[int]] = None,
    all_cat_ids: Optional[list[int]] = None,
    store_ids: Optional[list[int]] = None,
    include_non_sales: bool = False,
    print_current_sales: bool = False,
) -> bytes:
    """
    Generate and download the Item Sales Report as an Excel (.xlsx) file.

    Flow
    ----
    1. POST form data to /Api/Report/ItemSalesReport with ExportExcel=true.
    2. Server generates the file, returns ``excelKey`` in the response.
    3. GET /RMC/download?type=d&file={excelKey} → raw .xlsx bytes.

    Parameters
    ----------
    session
        Authenticated requests.Session (must have ASP.NET_SessionId cookie).
    date_from / date_to
        Inclusive date range for the report.
    price_type
        0 = All Types, 1 = Single, -2 = Special Price, -3 = Open Price.
    sort_by
        "Item" | "Amount" | "Qty" | "Desc"
    sort_by_type
        "Descending" | "Ascending"
    report_type
        "Individual" | "Consolidate" | "Comparison"
    selected_cat_ids
        Category IDs to filter by; ``None`` / empty → all categories.
    all_cat_ids
        Full list of category IDs (used by the server for totals).
        Defaults to ``ALL_CAT_IDS``.
    store_ids
        Store IDs to include; defaults to ``[DEFAULT_STORE_ID]``.
    include_non_sales
        Include items with zero sales in the report.
    print_current_sales
        If True, the server uses the current business date (ignores
        date_from / date_to on the server side).

    Returns
    -------
    bytes — raw .xlsx content ready to write to a file.

    Raises
    ------
    RuntimeError
        If the API returns NoData or an empty excelKey.
    """
    url = api_url("Report", "ItemSalesReport")

    _all_cats  = all_cat_ids  if all_cat_ids  is not None else ALL_CAT_IDS
    _stores    = store_ids    if store_ids    is not None else [DEFAULT_STORE_ID]
    _sel_cats  = selected_cat_ids if selected_cat_ids is not None else []

    post_data = {
        "TxnDateFrom":           _sv_date(date_from),
        "TxnDateTo":             _sv_date(date_to),
        "PriceType":             str(price_type),
        "SortBy":                sort_by,
        "SortByType":            sort_by_type,
        "SelectedCatList":       json.dumps(_sel_cats),
        "SelectedAllCatIdsList": json.dumps(_all_cats),
        "PrintCurrentSales":     "true" if print_current_sales else "false",
        "IncludeNonSales":       "true" if include_non_sales   else "false",
        "StoreIdList":           json.dumps(_stores),
        "ReportType":            report_type,
        "ExportExcel":           "true",
        "ExportPdf":             "false",
        "OperationType":         "Normal",
    }

    resp = session.post(
        url,
        data=post_data,                              # MUST be form-encoded
        headers={"X-Requested-With": "XMLHttpRequest"},
        timeout=60,
    )
    resp.raise_for_status()

    outer  = resp.json()
    result = outer.get("result", {})

    if result.get("status") == "NoData":
        raise RuntimeError(
            "ItemSalesReport returned NoData — no transactions in the given date range."
        )
    if result.get("status") != "OK" or not result.get("excelKey"):
        raise RuntimeError(
            f"ItemSalesReport failed — result: {result}  "
            f"(outer: {outer.get('error_message', '')})"
        )

    excel_key = result["excelKey"]
    print(f"  excelKey: {excel_key}", file=sys.stderr)

    dl = session.get(
        f"https://swypepos.com/RMC/download?type=d&file={excel_key}",
        timeout=60,
    )
    dl.raise_for_status()
    return dl.content


def export_item_sales_excel(
    output_path: Optional[str] = None,
    *,
    date_from: Optional[datetime] = None,
    date_to:   Optional[datetime] = None,
    **kwargs,
) -> bytes:
    """
    Full pipeline: login → generate Item Sales Excel → save to file.

    Parameters
    ----------
    output_path
        If provided, write the .xlsx bytes to this path.
    date_from / date_to
        Date range for the report.  Defaults to the last 365 days.
    **kwargs
        Any keyword argument accepted by ``fetch_item_sales_excel``.

    Returns
    -------
    bytes — raw .xlsx content.
    """
    from datetime import timedelta

    if date_to   is None:
        date_to   = datetime.now().replace(hour=23, minute=59, second=59, microsecond=0)
    if date_from is None:
        date_from = (date_to - timedelta(days=365)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    session = create_session()
    print("Logging in…", file=sys.stderr)
    login(session)

    print(
        f"Generating Item Sales Report "
        f"{date_from.strftime('%Y-%m-%d')} → {date_to.strftime('%Y-%m-%d')}…",
        file=sys.stderr,
    )
    xlsx_bytes = fetch_item_sales_excel(session, date_from, date_to, **kwargs)

    if output_path:
        with open(output_path, "wb") as f:
            f.write(xlsx_bytes)
        print(f"Saved → {output_path}  ({len(xlsx_bytes):,} bytes)", file=sys.stderr)

    return xlsx_bytes


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    from datetime import timedelta

    parser = argparse.ArgumentParser(description="SwypePOS caffebarney exporter")
    parser.add_argument(
        "--mode",
        choices=["items-csv", "sales-excel"],
        default="items-csv",
        help="items-csv: export item catalogue to CSV  |  "
             "sales-excel: export Item Sales Report to Excel",
    )
    parser.add_argument("--from-date", default=None, help="YYYY-MM-DD (sales-excel only)")
    parser.add_argument("--to-date",   default=None, help="YYYY-MM-DD (sales-excel only)")
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

    if args.mode == "items-csv":
        out_file = f"export_items_{timestamp}.csv"
        csv_text = export_items_csv(output_path=out_file, include_all_cols=True)
        lines = csv_text.splitlines()
        print(f"\nCSV preview (first 5 rows):")
        for line in lines[:6]:
            print(" ", line[:120])
        print(f"\nTotal rows (including header): {len(lines)}")
        print(f"File written: {out_file}")

    elif args.mode == "sales-excel":
        out_file = f"item_sales_{timestamp}.xlsx"

        df = None
        dto = None
        if args.from_date:
            df = datetime.strptime(args.from_date, "%Y-%m-%d")
        if args.to_date:
            dto = datetime.strptime(args.to_date, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59
            )

        xlsx = export_item_sales_excel(
            output_path=out_file,
            date_from=df,
            date_to=dto,
        )
        print(f"\nFile written: {out_file}  ({len(xlsx):,} bytes)")
