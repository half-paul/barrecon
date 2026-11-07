"""One-time importer for the bar's existing "To Order Inventory List" workbook.

Layout: one tab per month ("June 2026"), a header row 'Item | SKU | On hand | Ordered | Sold'
repeated per week, section header rows (Wine, Draft, ...) with nothing else on the row,
par levels embedded in names ("Forty Creek (par 4)"), and a "Feature Cocktail Sales"
block that is sales data, not stock.

Produces: items (upsert by SKU, else by normalised name), one posted count session per
weekly "On hand" column, delivery movements from parseable "Ordered" cells, and a list of
review items for everything it would otherwise have to guess.
"""
from __future__ import annotations

import calendar
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from openpyxl import load_workbook
from sqlalchemy.orm import Session

from ..config import settings
from ..models import CountSession, ImportBatch, InventoryItem, DataIssue
from . import inventory as inv
from .xlsx_io import sha256

SECTION_CATEGORY = {
    "popular spirits": "Spirits", "\\": "Spirits", "spirits": "Spirits",
    "scotch": "Scotch & whisky", "wine": "Wine", "boxed wine": "Boxed wine",
    "bottled beer": "Bottled beer", "draft": "Draft beer",
}
SKIP_SECTIONS = {"feature cocktail sales"}

CATEGORY_DEFAULTS = {
    "Spirits": dict(stock_unit="ml", pack_name="bottle", pack_size=Decimal(750), case_size=Decimal(12)),
    "Scotch & whisky": dict(stock_unit="ml", pack_name="bottle", pack_size=Decimal(750), case_size=Decimal(12)),
    "Wine": dict(stock_unit="ml", pack_name="bottle", pack_size=Decimal(750), case_size=Decimal(12)),
    "Boxed wine": dict(stock_unit="ml", pack_name="box", pack_size=Decimal(4000), case_size=Decimal(1)),
    "Bottled beer": dict(stock_unit="each", pack_name="bottle/can", pack_size=Decimal(1), case_size=Decimal(24)),
    "Draft beer": dict(stock_unit="ml", pack_name="keg", pack_size=Decimal(50000), case_size=Decimal(1)),
}

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS["sept"] = 9

PAR_RE = re.compile(r"\s*\((?:par|Par|PAR)\s*(\d+(?:\.\d+)?)\)")
ORDER_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:x\s*)?(cases?|flats?|bottles?|btls?|bttles?|bottkes|kegs?)?\s*$", re.I)


def norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def slug(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", s.upper()).strip("-")


@dataclass
class LegacyPreview:
    tabs: list[str] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)
    count_sessions: list[dict] = field(default_factory=list)
    deliveries: list[dict] = field(default_factory=list)
    review: list[dict] = field(default_factory=list)


def _tab_month(title: str) -> tuple[int, int] | None:
    m = re.match(r"\s*([A-Za-z]+)\.?\s+(\d{4})", title)
    if not m or m.group(1).lower() not in MONTHS:
        return None
    return int(m.group(2)), MONTHS[m.group(1).lower()]


def week_date(year: int, month: int, week_no: int, weekday: int) -> tuple[date, bool]:
    """The week_no-th occurrence of weekday in the month; falls back to month end (flagged)."""
    days = [d for d in range(1, calendar.monthrange(year, month)[1] + 1) if date(year, month, d).weekday() == weekday]
    if week_no <= len(days):
        return date(year, month, days[week_no - 1]), False
    return date(year, month, calendar.monthrange(year, month)[1]), True


def parse_order(text) -> tuple[Decimal | None, str | None, str | None]:
    """'2 case' -> (2, 'case', None); '5 bottkes' -> (5, 'bottle', None); free text -> (None, None, reason)."""
    if text is None:
        return None, None, None
    if isinstance(text, (int, float, Decimal)):
        return Decimal(str(text)), "pack", None
    s = str(text).strip()
    if not s:
        return None, None, None
    if "never" in s.lower() or "not rec" in s.lower():
        return None, None, "marked as not received"
    m = ORDER_RE.match(s)
    if not m:
        return None, None, "free text"
    n = Decimal(m.group(1))
    unit = (m.group(2) or "pack").lower()
    if unit.startswith("case") or unit.startswith("flat"):
        return n, "case", None
    if unit.startswith("keg"):
        return n, "pack", None
    return n, "pack", None


def preview(data: bytes, *, count_weekday: int = 6) -> LegacyPreview:
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    pv = LegacyPreview()
    known: dict[str, dict] = {}  # key (sku or norm name) -> item dict
    for ws in wb.worksheets:
        ym = _tab_month(ws.title)
        if not ym:
            pv.review.append({"code": "tab_skipped", "detail": f"tab {ws.title!r} is not 'Month YYYY'"})
            continue
        year, month = ym
        pv.tabs.append(ws.title)
        rows = list(ws.iter_rows(values_only=True))
        hdr_i = next((i for i, r in enumerate(rows) if r and r[0] == "Item"), None)
        if hdr_i is None:
            pv.review.append({"code": "tab_skipped", "detail": f"tab {ws.title!r}: no 'Item' header row"})
            continue
        hdr = rows[hdr_i]
        weeks = []  # (week_no, onhand_col, ordered_col)
        wk = 0
        for i, h in enumerate(hdr):
            if h == "On hand":
                wk += 1
                ordered_col = i + 1 if i + 1 < len(hdr) and hdr[i + 1] == "Ordered" else None
                weeks.append((wk, i, ordered_col))
        sessions = {}
        for wk_no, _, _ in weeks:
            d, fallback = week_date(year, month, wk_no, count_weekday)
            sessions[wk_no] = {"tab": ws.title, "week": wk_no, "date": d, "date_fallback": fallback, "lines": []}
        section = "Spirits"
        skip = False
        for r in rows[hdr_i + 1:]:
            name = r[0]
            if name is None:
                continue
            name = str(name).strip()
            if all(v is None or (isinstance(v, str) and not v.strip()) for v in r[1:]):
                key = name.lower()
                if key in SKIP_SECTIONS:
                    skip = True
                    continue
                if key in SECTION_CATEGORY:
                    section, skip = SECTION_CATEGORY[key], False
                    continue
                if skip:
                    continue  # blank cocktail rows inside the sales block
                clean_probe = PAR_RE.sub("", name).strip()
                if any(norm_name(v["name"]) == norm_name(clean_probe) for v in known.values()):
                    continue  # a known item with nothing recorded this month
                pv.review.append({"code": "unknown_section", "detail": f"{ws.title}: header {name!r} treated as category"})
                section, skip = name, False
                continue
            if skip:
                continue
            par = None
            m = PAR_RE.search(name)
            if m:
                par = Decimal(m.group(1))
            clean = PAR_RE.sub("", name).strip()
            raw_sku = r[1] if len(r) > 1 else None
            sku = str(int(raw_sku)) if isinstance(raw_sku, (int, float)) and raw_sku and raw_sku > 1 else None
            key = f"sku:{sku}" if sku else f"name:{norm_name(clean)}"
            if key not in known and not sku:
                # try to match an earlier item by normalised name even if it later gained a SKU
                pass
            item = known.get(key)
            if item is None:
                # match by normalised name across keys
                for k, v in known.items():
                    if norm_name(v["name"]) == norm_name(clean):
                        item = v
                        if sku and not v["sku_from_sheet"]:
                            v["sku"], v["sku_from_sheet"] = sku, True
                        break
            if item is None:
                cat = section if section in CATEGORY_DEFAULTS else "Spirits"
                item = {"sku": sku or f"GEN-{slug(clean)}", "sku_from_sheet": bool(sku), "name": clean,
                        "category": section, "par_level": par, "first_seen": ws.title, "last_on_hand": None,
                        **CATEGORY_DEFAULTS[cat]}
                known[key] = item
                if not sku:
                    pv.review.append({"code": "no_sku", "detail": f"{clean}: no SKU in sheet, key {item['sku']}"})
            elif par is not None and item["par_level"] is None:
                item["par_level"] = par
            for wk_no, oh_col, ord_col in weeks:
                v = r[oh_col] if oh_col < len(r) else None
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    sessions[wk_no]["lines"].append({"item": item, "packs": Decimal(str(v))})
                    item["last_on_hand"] = Decimal(str(v))
                elif isinstance(v, str) and v.strip():
                    pv.review.append({"code": "on_hand_unparsed", "detail": f"{ws.title} wk{wk_no} {clean}: on hand {v!r} (formula without cached value?)"})
                if ord_col is not None:
                    o = r[ord_col] if ord_col < len(r) else None
                    n, unit, why = parse_order(o)
                    if why:
                        pv.review.append({"code": "order_unparsed", "detail": f"{ws.title} wk{wk_no} {clean}: ordered {o!r} ({why})"})
                    elif n is not None:
                        pv.deliveries.append({"item": item, "name": clean, "tab": ws.title, "week": wk_no,
                                              "date": sessions[wk_no]["date"], "n": n, "unit": unit, "raw": str(o)})
        pv.count_sessions.extend(s for s in sessions.values() if s["lines"])
    pv.items = list(known.values())
    return pv


def apply(db: Session, data: bytes, file_name: str, location_id: str, *, count_weekday: int = 6,
          include_counts: bool = True, include_deliveries: bool = True) -> dict:
    digest = sha256(data)
    pv = preview(data, count_weekday=count_weekday)
    batch = ImportBatch(location_id=location_id, source="legacy", dataset="legacy", file_name=file_name,
                        file_sha256=digest, rows_received=len(pv.items))
    db.add(batch)
    db.flush()
    tz = ZoneInfo(settings.timezone)
    created = updated = 0
    for it in pv.items:
        d = {k: it[k] for k in ("sku", "name", "category", "stock_unit", "pack_name", "pack_size", "case_size", "par_level")}
        d["is_active"] = it["last_on_hand"] is not None
        _, c = inv.upsert_item(db, d, cost_source="legacy")
        created += c
        updated += not c
    sessions_created = 0
    if include_counts:
        for s in pv.count_sessions:
            counted_at = datetime.combine(s["date"], time(23, 59), tzinfo=tz).astimezone(timezone.utc)
            if inv.active_session_at(db, location_id, counted_at, include_deleted=True):
                continue  # already imported; legacy counts are never re-posted over existing ones, and a deleted one stays deleted
            sess = CountSession(location_id=location_id, counted_at=counted_at, status="draft", source="legacy",
                                external_ref=f"{file_name}#{s['tab']}#wk{s['week']}",
                                note=f"Imported from {s['tab']} week {s['week']}" + (" (date fell back to month end)" if s["date_fallback"] else ""))
            db.add(sess)
            db.flush()
            for ln in s["lines"]:
                item = inv.item_by_sku(db, ln["item"]["sku"])
                inv.set_count_line(db, sess, item, Decimal(0), ln["packs"])
            inv.post_count(db, sess)
            sessions_created += 1
    deliveries = dup = 0
    if include_deliveries:
        for dv in pv.deliveries:
            item = inv.item_by_sku(db, dv["item"]["sku"])
            packs = dv["n"] * (Decimal(item.case_size) if dv["unit"] == "case" else Decimal(1))
            occurred = datetime.combine(dv["date"], time(12, 0), tzinfo=tz).astimezone(timezone.utc)
            _, outcome = inv.add_movement(db, location_id=location_id, item=item, kind="delivery", occurred_at=occurred,
                                          external_ref=f"legacy:{dv['tab']}:wk{dv['week']}", packs=packs, source="legacy",
                                          note=f"Ordered column: {dv['raw']}", import_batch_id=batch.id)
            deliveries += outcome == "inserted"
            dup += outcome == "duplicate"
    for rv in pv.review:
        db.add(DataIssue(code=rv["code"], entity="legacy", detail=rv, import_batch_id=batch.id))
    batch.rows_inserted = created
    batch.rows_duplicate = dup
    batch.error_log = pv.review or None
    db.commit()
    return {"batch_id": batch.id, "tabs": pv.tabs, "items_created": created, "items_updated": updated,
            "count_sessions_created": sessions_created, "deliveries_created": deliveries,
            "deliveries_duplicate": dup, "review": pv.review}
