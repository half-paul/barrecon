"""The Swype menu: every Swype item # seen in an import, kept apart from inventory items.

Swype items (what is rung in) and inventory items (what is counted) are different things with different
numbers: "Caesar" 00000612 is not an inventory item, and "Smirnoff Vodka DBL" 00000330_02 is a portion of
the inventory item "Smirnoff" (SKU 1123), not the item itself. The only bridge is a recipe, so imports here
never create, link or switch on inventory items.

Both the Item Sales report and the Export Items CSV feed swype_menu_items: new codes are added, a changed
name or category is updated (latest wins), nothing is removed. A rename made here (custom_name) is ours and
survives imports. An item switched off here comes back on when a sales import shows it sold. After every
sales import, items with no sales in the month up to the latest imported day are switched off
(deactivate_unsold).

suggest_item guesses which inventory item a pour-style Swype item pours from, for recipe drafts only: the
item in the recipe of its bottle-level code (00000331 for 00000331_02) when that recipe has one line, else
an inventory item whose name matches once SNG/DBL/6oz/BTL... are dropped. Nothing is saved until a recipe is.
"""
from __future__ import annotations

import calendar
import csv
import io
import re
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import ImportBatch, InventoryItem, SwypeItemSales, SwypeMenuItem
from . import inventory as inv
from .xlsx_io import sha256

DATASET = "swype_items"
SUFFIX_RE = re.compile(r"\s+(?:SNG|DBL|BTL|Bottle|\d+\s*oz|(?:Single|Double)(?:\s+(?:Premium|Delux|Deluxe))?)\s*$", re.I)
CSV_CODE, CSV_NAME = "Item Code", "Item Desc1"


class CatalogueError(ValueError):
    pass


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def match_key(name: str) -> str:
    return norm(SUFFIX_RE.sub("", name))


def base_code(code: str) -> str:
    return code.split("_", 1)[0]


class MenuItemError(ValueError):
    pass


def sync(db: Session, rows: list[dict], *, source: str) -> dict:
    """rows: {"code", "name", "category" (may be None), "sold" (optional)}. Upserts swype_menu_items; a sold
    row switches its item back on. Flushes, does not commit."""
    known = {m.code: m for m in db.scalars(select(SwypeMenuItem))}
    out = {"new": 0, "renamed": 0, "unchanged": 0, "reactivated": 0, "new_items": []}
    seen: set[str] = set()
    for r in rows:
        code, name, cat = r["code"], r["name"].strip(), r.get("category")
        if code in seen or not name:
            continue
        seen.add(code)
        m = known.get(code)
        if m is None:
            db.add(SwypeMenuItem(code=code, name=name, category=cat, source=source))
            out["new"] += 1
            out["new_items"].append(name)
            continue
        if r.get("sold") and not m.is_active:
            m.is_active = True
            out["reactivated"] += 1
        changed = m.name != name or (cat and m.category != cat)
        m.name = name
        m.category = cat or m.category
        out["renamed" if changed else "unchanged"] += 1
    db.flush()
    return out


def month_before(d: date) -> date:
    y, m = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def deactivate_unsold(db: Session, location_id: str) -> int:
    """Switch off active items with nothing sold in the month up to the latest imported sales day (not today,
    so a late import does not switch everything off). Only runs once the imported sales reach back a full
    month. An item that has never been in any sales report and was added to the menu within that month is
    left alone, so a new item can wait for its first sale. A later sale switches an item back on (sync).
    Flushes, does not commit. Returns how many."""
    latest, earliest = db.execute(select(func.max(SwypeItemSales.period_to), func.min(SwypeItemSales.period_from))
                                  .where(SwypeItemSales.location_id == location_id)).one()
    if latest is None:
        return 0
    since = month_before(latest)
    if earliest > since:
        return 0
    sold = set(db.scalars(select(SwypeItemSales.item_code).where(
        SwypeItemSales.location_id == location_id, SwypeItemSales.period_to > since, SwypeItemSales.total_qty > 0)))
    in_sales = set(db.scalars(select(SwypeItemSales.item_code).where(SwypeItemSales.location_id == location_id)))
    added_before = datetime.combine(since, time(0, 0), tzinfo=ZoneInfo(settings.timezone)).astimezone(timezone.utc)
    n = 0
    for m in db.scalars(select(SwypeMenuItem).where(SwypeMenuItem.is_active)):
        new = m.code not in in_sales and inv.to_utc(m.created_at) >= added_before
        if m.code not in sold and not new:
            m.is_active = False
            n += 1
    db.flush()
    return n


def display_name(m: SwypeMenuItem) -> str:
    return m.custom_name or m.name


def custom_names(db: Session) -> dict[str, str]:
    return dict(db.execute(select(SwypeMenuItem.code, SwypeMenuItem.custom_name)
                           .where(SwypeMenuItem.custom_name.is_not(None))).all())


def item_out(m: SwypeMenuItem, sold: dict | None = None) -> dict:
    return {"code": m.code, "name": display_name(m), "swype_name": m.name, "renamed": m.custom_name is not None,
            "category": m.category, "is_active": m.is_active, "source": m.source,
            "sold": float(sold["sold"]) if sold else 0.0, "last_sold": sold["last_sold"] if sold else None}


def update(db: Session, code: str, *, name: str | None = None, is_active: bool | None = None) -> SwypeMenuItem:
    """Rename (blank or Swype's own name clears the rename) and/or switch on/off. The code never changes."""
    m = db.query(SwypeMenuItem).filter(SwypeMenuItem.code == code).one_or_none()
    if m is None:
        raise MenuItemError(f"no Swype item {code}")
    if name is not None:
        name = name.strip()
        m.custom_name = None if not name or name == m.name else name
    if is_active is not None:
        m.is_active = is_active
    db.flush()
    return m


def dry_run(db: Session, rows: list[dict], *, source: str) -> dict:
    sp = db.begin_nested()
    try:
        return sync(db, rows, source=source)
    finally:
        sp.rollback()


def sync_from_sales(db: Session) -> dict:
    """Add every item # already sitting in swype_item_sales to the menu (sales imported before the menu existed)."""
    rows = {}
    for code, name, cat in db.execute(select(SwypeItemSales.item_code, SwypeItemSales.description,
                                             SwypeItemSales.category).order_by(SwypeItemSales.period_from)):
        rows[code] = {"code": code, "name": name, "category": cat}  # latest name wins
    r = sync(db, list(rows.values()), source="sales")
    db.commit()
    return r


def suggest_item(code: str, name: str, items_by_key: dict[str, InventoryItem],
                 base_recipe_item: dict[str, InventoryItem]) -> tuple[InventoryItem, str] | None:
    """(inventory item, how) a pour-style Swype item most likely pours from, or None. A guess for drafts only."""
    base = base_code(code)
    if base != code and base in base_recipe_item:
        return base_recipe_item[base], "bottle_recipe"
    for k in (match_key(name), norm(name)):
        if k and (it := items_by_key.get(k)) is not None:
            return it, "name"
    return None


def items_by_key(db: Session) -> dict[str, InventoryItem]:
    """Inventory items by normalised name; active items win a clash."""
    out: dict[str, InventoryItem] = {}
    for it in sorted(db.scalars(select(InventoryItem)), key=lambda i: not i.is_active):
        for k in (norm(it.name), match_key(it.name)):
            out.setdefault(k, it)
    return out


# ---------- Export Items CSV (Swype -> Import/Export -> Export Item) ----------

def parse_csv(data: bytes) -> list[dict]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    reader = csv.DictReader(io.StringIO(text))
    cols = [c.strip() for c in (reader.fieldnames or [])]
    if CSV_CODE not in cols or CSV_NAME not in cols:
        raise CatalogueError(f"not a Swype Export Items file: needs '{CSV_CODE}' and '{CSV_NAME}' columns (found: {cols[:6]})")
    rows, seen = [], set()
    for n, r in enumerate(reader, start=2):
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
        code, name = r[CSV_CODE], r[CSV_NAME]
        if not code and not name:
            continue
        if not code or not name:
            raise CatalogueError(f"row {n}: item code and description are both required")
        if code in seen:
            raise CatalogueError(f"row {n}: item code {code} is listed twice")
        seen.add(code)
        rows.append({"code": code, "name": name, "category": None})
    if not rows:
        raise CatalogueError("the file has no items")
    return rows


def preview_csv(db: Session, data: bytes) -> dict:
    rows = parse_csv(data)
    r = dry_run(db, rows, source="csv")
    return {"items": len(rows), **r}


def apply_csv(db: Session, data: bytes, file_name: str, location_id: str) -> dict:
    digest = sha256(data)
    batch = ImportBatch(location_id=location_id, source="swype_file", dataset=DATASET, file_name=file_name,
                        file_sha256=digest)

    def reject(msg: str) -> dict:
        batch.status = "rejected"
        batch.error_log = [{"row": 0, "status": "error", "message": msg}]
        db.add(batch)
        db.commit()
        return {"batch_id": batch.id, "status": "rejected", "error": msg}

    if db.scalar(select(ImportBatch).where(ImportBatch.dataset == DATASET, ImportBatch.file_sha256 == digest,
                                           ImportBatch.status == "ok")):
        return reject("this exact file was already imported")
    try:
        rows = parse_csv(data)
    except CatalogueError as e:
        return reject(str(e))
    db.add(batch)
    r = sync(db, rows, source="csv")
    batch.rows_received = len(rows)
    batch.rows_inserted = r["new"] + r["renamed"]
    batch.rows_duplicate = r["unchanged"]
    db.commit()
    return {"batch_id": batch.id, "status": "ok", "items": len(rows), **r}
