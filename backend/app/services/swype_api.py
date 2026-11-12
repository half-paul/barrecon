"""Pull the Item Sales report straight from the SwypePOS back office (RMC).

Same flow as scripts/csv_export_api/api_client.py: log in, POST the report form with ExportExcel=true
(form-encoded; a JSON body returns an empty excelKey), then download the .xlsx by its key. The file is
handed to swype_item_sales.apply unchanged, so it gets the same subtotal checks as an uploaded one.
Each business has its own Swype login (Setup -> Businesses); the password is kept encrypted.
"""
from __future__ import annotations

import html
import json
import re
from datetime import date

from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from ..models import Location
from . import secretbox

BASE_URL = "https://swypepos.com/RMC"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
}
# Fallback if the category list can't be read off the Export Item page.
ALL_CAT_IDS = [101, 109, 125, 126, 127, 194, 128, 129, 130, 131, 132, 133, 141, 143, 144, 177, 104, 102, 122, 146,
               105, 103, 134, 200, 135, 136, 137, 138, 139, 140, 142, 156, 159, 166, 168, 175, 106, 186, 191, 123,
               124, 173, 107, 108]


class SwypeError(RuntimeError):
    pass


class NoSales(SwypeError):
    """Swype answered but had nothing sold in the range (bar closed)."""


@dataclass(frozen=True)
class Creds:
    email: str
    password: str
    company: str
    store_id: int


def creds(db: Session) -> Creds | None:
    """The Swype login of the business this session works in, or None if it has none (or it can't be decrypted)."""
    loc = db.get(Location, db.info.get("business_id")) if db.info.get("business_id") else None
    pw = secretbox.open_(loc.swype_password) if loc else None
    if not loc or not loc.swype_email or not pw or not loc.swype_company:
        return None
    return Creds(loc.swype_email, pw, loc.swype_company, loc.swype_store_id or 1)


def configured(db: Session) -> bool:
    return creds(db) is not None


def _api(cr: Creds, controller: str, action: str) -> str:
    return (f"{BASE_URL}/Api/Router.aspx?controller={controller}&action={action}"
            f"&company={cr.company}&lang=en")


def _login(c: httpx.Client, cr: Creds) -> None:
    r = c.post(_api(cr, "User", "Login"), data={"Email": cr.email, "Password": cr.password,
                                                "VerifyId": "", "VerifyCode": ""})
    r.raise_for_status()
    outer = r.json()
    if outer.get("status") != "OK" or outer.get("result", {}).get("status") != "OK":
        raise SwypeError(f"Swype login failed ({outer.get('result', {}).get('status') or outer.get('error_message')})")


def _category_ids(c: httpx.Client, cr: Creds) -> list[int]:
    try:
        body = c.get(f"{BASE_URL}/{cr.company}/en/Import/ExportItem").text
        m = re.search(r'class="AllCategories"[^>]*value="([^"]*)"', body)
        ids = [int(x["CategoryId"]) for x in json.loads(html.unescape(m.group(1)))] if m else []
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        ids = []
    return ids or ALL_CAT_IDS


def fetch_item_sales(cr: Creds | None, date_from: date, date_to: date) -> bytes:
    """The Item Sales report (All Price Types, every category) for date_from..date_to inclusive, as .xlsx bytes."""
    if cr is None:
        raise SwypeError("Swype login is not set up for this business: an admin can add it under Setup, Businesses")
    try:
        with httpx.Client(headers={**HEADERS, "Referer": f"{BASE_URL}/{cr.company}/en/"},
                          timeout=90, follow_redirects=True) as c:
            _login(c, cr)
            form = {
                "TxnDateFrom": f"{date_from:%Y/%m/%d} 00:00:00",
                "TxnDateTo": f"{date_to:%Y/%m/%d} 23:59:59",
                "PriceType": "0",  # All Price Types; the importer refuses anything else
                "SortBy": "Item",
                "SortByType": "Descending",
                "SelectedCatList": "[]",
                "SelectedAllCatIdsList": json.dumps(_category_ids(c, cr)),
                "PrintCurrentSales": "false",
                "IncludeNonSales": "false",
                "StoreIdList": json.dumps([cr.store_id]),
                "ReportType": "Individual",
                "ExportExcel": "true",
                "ExportPdf": "false",
                "OperationType": "Normal",
            }
            r = c.post(_api(cr, "Report", "ItemSalesReport"), data=form)
            r.raise_for_status()
            result = r.json().get("result") or {}
            if result.get("status") == "NoData":
                raise NoSales(f"Swype has no sales from {date_from} to {date_to}")
            if result.get("status") != "OK" or not result.get("excelKey"):
                raise SwypeError(f"Swype did not produce the report ({result.get('status')})")
            dl = c.get(f"{BASE_URL}/download", params={"type": "d", "file": result["excelKey"]})
            dl.raise_for_status()
            return dl.content
    except httpx.HTTPError as e:
        raise SwypeError(f"could not reach Swype: {e}") from None
