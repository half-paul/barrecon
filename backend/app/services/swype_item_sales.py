"""Importer for Swype's "Item Sales" report (Report -> Item Sales Report -> Excel).

Layout: a title block with "From MM/DD/YYYY to MM/DD/YYYY" in column A, a header row starting
'Item #', then per department (column A: Food, Beverage, Liquor) per category (column B: "Draft Beer")
one row per item: B = item # ("00000330_02"), C = description, E = qty, F = combo qty, I = amount,
L = net amount, M = promo qty, N = free qty, O = total qty. Each category ends with a
"<name>     Subtotal" row, each department with "<name>    Total", and the file with "Grand Total".

The report is a per-item summary for the whole date range: no ticket ids, no discounts, no modifiers.
Validation checks every subtotal, total and the grand total against the item rows; any mismatch rejects
the file and nothing is written.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from openpyxl import load_workbook
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import ImportBatch, SwypeItemSales
from . import recipe_match, swype_items
from .xlsx_io import sha256

DATASET = "item_sales"
PERIOD_RE = re.compile(r"From\s+(\d{1,2}/\d{1,2}/\d{4})\s+to\s+(\d{1,2}/\d{1,2}/\d{4})", re.I)
ITEM_CODE_RE = re.compile(r"^\d+(?:_\d+)?$")
SUBTOTAL_RE = re.compile(r"^(.*?)\s+Subtotal$")
TOTAL_RE = re.compile(r"^(.*?)\s+Total$")
# zero-based columns in the export
C_DEPT, C_CODE, C_DESC, C_QTY, C_COMBO, C_AMT, C_NET, C_PROMO, C_FREE, C_TOTAL = 0, 1, 2, 4, 5, 8, 11, 12, 13, 14
SUMMED = ("qty", "combo_qty", "amount", "net_amount", "promo_qty", "free_qty", "total_qty")


class ReportError(ValueError):
    pass


@dataclass
class Parsed:
    period_from: date
    period_to: date
    price_type: str
    lines: list[dict] = field(default_factory=list)
    totals: dict[str, Decimal] = field(default_factory=dict)  # grand total row


def _cell(r: tuple, i: int):
    v = r[i] if i < len(r) else None
    return v.strip() if isinstance(v, str) else v


def _num(v, what: str, row: int) -> Decimal:
    if v in (None, ""):
        return Decimal(0)
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        raise ReportError(f"row {row}: {what} is not a number ({v!r})") from None


def _sums(r: tuple, row: int) -> dict[str, Decimal]:
    return {"qty": _num(_cell(r, C_QTY), "Qty", row), "combo_qty": _num(_cell(r, C_COMBO), "Combo Qty", row),
            "amount": _num(_cell(r, C_AMT), "Amt.", row), "net_amount": _num(_cell(r, C_NET), "Net Amt.", row),
            "promo_qty": _num(_cell(r, C_PROMO), "Promo Qty", row), "free_qty": _num(_cell(r, C_FREE), "Free Qty", row),
            "total_qty": _num(_cell(r, C_TOTAL), "Total Qty", row)}


def _check(label: str, expected: dict[str, Decimal], lines: list[dict], row: int) -> None:
    for k in SUMMED:
        got = sum((ln[k] for ln in lines), Decimal(0))
        if abs(got - expected[k]) > Decimal("0.01"):
            raise ReportError(f"row {row}: {label} {k} is {expected[k]} but the item rows add up to {got}")


def parse(data: bytes) -> Parsed:
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001
        raise ReportError(f"not an Excel file: {e}") from None
    rows = list(wb.active.iter_rows(values_only=True))
    if not any(_cell(r, 0) == "Item Sales" for r in rows[:5]):
        raise ReportError("this is not a Swype Item Sales report (no 'Item Sales' title)")
    period = None
    header_at = None
    for i, r in enumerate(rows):
        a = _cell(r, 0)
        if isinstance(a, str) and (m := PERIOD_RE.search(a)):
            period = m, a
        if a == "Item #":
            header_at = i
            break
    if not period:
        raise ReportError("could not find the 'From ... to ...' date range in the header")
    if header_at is None:
        raise ReportError("could not find the 'Item #' header row")
    m, text = period
    pf, pt = (datetime.strptime(s, "%m/%d/%Y").date() for s in m.groups())
    price_type = text[m.end():].strip() or "All Price Type"
    out = Parsed(period_from=pf, period_to=pt, price_type=price_type)

    dept = cat = None
    dept_lines: list[dict] = []
    cat_lines: list[dict] = []
    seen: set[str] = set()
    for i in range(header_at + 1, len(rows)):
        r, n = rows[i], i + 1
        a, b, c = _cell(r, C_DEPT), _cell(r, C_CODE), _cell(r, C_DESC)
        if c == "Grand Total":
            out.totals = _sums(r, n)
            _check("Grand Total", out.totals, out.lines, n)
            break
        if a:  # department header
            dept, dept_lines = str(a), []
            continue
        if not b:
            continue
        b = str(b)
        if ITEM_CODE_RE.match(b):
            if dept is None or cat is None:
                raise ReportError(f"row {n}: item {b} appears before any department/category heading")
            if b in seen:
                raise ReportError(f"row {n}: item {b} is listed twice")
            seen.add(b)
            ln = {"department": dept, "category": cat, "item_code": b, "description": str(c or ""), **_sums(r, n)}
            out.lines.append(ln)
            dept_lines.append(ln)
            cat_lines.append(ln)
        elif sm := SUBTOTAL_RE.match(b):
            _check(f"'{sm.group(1)}' subtotal", _sums(r, n), cat_lines, n)
            cat = None
        elif tm := TOTAL_RE.match(b):
            _check(f"'{tm.group(1)}' total", _sums(r, n), dept_lines, n)
            dept = None
        else:  # category header
            cat, cat_lines = b, []
    else:
        raise ReportError("no 'Grand Total' row: the file looks truncated")
    return out


def _period_bounds(pf: date, pt: date) -> tuple[datetime, datetime]:
    tz = ZoneInfo(settings.timezone)
    return (datetime.combine(pf, time(0, 0), tzinfo=tz).astimezone(timezone.utc),
            datetime.combine(pt, time(23, 59, 59), tzinfo=tz).astimezone(timezone.utc))


def _item_rows(p: Parsed) -> list[dict]:
    return [{"code": ln["item_code"], "name": ln["description"], "category": ln["category"],
             "sold": ln["total_qty"] > 0} for ln in p.lines]


def preview(data: bytes, db: Session | None = None) -> dict:
    p = parse(data)
    depts: dict[str, dict] = {}
    for ln in p.lines:
        d = depts.setdefault(ln["department"], {"department": ln["department"], "items": 0, "qty": Decimal(0), "net_amount": Decimal(0)})
        d["items"] += 1
        d["qty"] += ln["total_qty"]
        d["net_amount"] += ln["net_amount"]
    return {"period_from": p.period_from.isoformat(), "period_to": p.period_to.isoformat(), "price_type": p.price_type,
            "items": len(p.lines), "total_qty": float(p.totals["total_qty"]), "net_amount": float(p.totals["net_amount"]),
            "departments": [{**d, "qty": float(d["qty"]), "net_amount": float(d["net_amount"])} for d in depts.values()],
            "menu": swype_items.dry_run(db, _item_rows(p), source="sales") if db is not None else None}


def apply(db: Session, data: bytes, file_name: str, location_id: str, *, source: str = "swype_file") -> dict:
    """Validate the whole file, then replace that period's rows. An ImportBatch is recorded either way."""
    digest = sha256(data)
    batch = ImportBatch(location_id=location_id, source=source, dataset=DATASET, file_name=file_name, file_sha256=digest)

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
        p = parse(data)
    except ReportError as e:
        return reject(str(e))
    if p.price_type.lower() != "all price type":
        return reject(f"report was run for '{p.price_type}' only; run it with All Price Types")
    batch.period_from, batch.period_to = _period_bounds(p.period_from, p.period_to)
    batch.rows_received = len(p.lines)

    overlap = db.execute(select(SwypeItemSales.period_from, SwypeItemSales.period_to).where(
        SwypeItemSales.location_id == location_id, SwypeItemSales.period_from <= p.period_to,
        SwypeItemSales.period_to >= p.period_from).distinct()).all()
    clash = [(a, b) for a, b in overlap if (a, b) != (p.period_from, p.period_to)]
    if clash:
        a, b = clash[0]
        return reject(f"{p.period_from} to {p.period_to} overlaps the already imported period {a} to {b}; "
                      "import the same weeks, or delete that period first")

    old = {r.item_code: r for r in db.scalars(select(SwypeItemSales).where(
        SwypeItemSales.location_id == location_id, SwypeItemSales.period_from == p.period_from,
        SwypeItemSales.period_to == p.period_to))}
    inserted = updated = unchanged = 0
    for ln in p.lines:
        o = old.get(ln["item_code"])
        if o is None:
            inserted += 1
        elif all(Decimal(getattr(o, k)) == ln[k] for k in SUMMED) and o.description == ln["description"]:
            unchanged += 1
        else:
            updated += 1
    removed = len(set(old) - {ln["item_code"] for ln in p.lines})
    db.add(batch)
    db.flush()
    db.execute(delete(SwypeItemSales).where(SwypeItemSales.location_id == location_id,
                                            SwypeItemSales.period_from == p.period_from,
                                            SwypeItemSales.period_to == p.period_to))
    for ln in p.lines:
        db.add(SwypeItemSales(location_id=location_id, period_from=p.period_from, period_to=p.period_to,
                              import_batch_id=batch.id, **ln))
    menu = swype_items.sync(db, _item_rows(p), source="sales")
    menu["deactivated"] = swype_items.deactivate_unsold(db, location_id)
    menu["no_stock"] = recipe_match.apply_no_stock(db, location_id)
    menu["prices_changed"] = swype_items.refresh_prices(db)
    menu.update(swype_items.price_alerts(db))
    batch.rows_inserted = inserted + updated
    batch.rows_duplicate = unchanged
    db.commit()
    return {"batch_id": batch.id, "status": "ok", "period_from": p.period_from.isoformat(),
            "period_to": p.period_to.isoformat(), "items": len(p.lines), "inserted": inserted, "updated": updated,
            "unchanged": unchanged, "removed": removed, "net_amount": float(p.totals["net_amount"]),
            "menu": menu}


def periods(db: Session, location_id: str) -> list[dict]:
    rows = db.execute(select(SwypeItemSales.period_from, SwypeItemSales.period_to, func.count(),
                             func.sum(SwypeItemSales.total_qty), func.sum(SwypeItemSales.net_amount))
                      .where(SwypeItemSales.location_id == location_id)
                      .group_by(SwypeItemSales.period_from, SwypeItemSales.period_to)
                      .order_by(SwypeItemSales.period_from)).all()
    return [{"period_from": a.isoformat(), "period_to": b.isoformat(), "items": n, "total_qty": float(q or 0),
             "net_amount": float(amt or 0)} for a, b, n, q, amt in rows]


def _items(db: Session, *where) -> list[dict]:
    """Sales rows item by item: by department and category, biggest sellers first."""
    renamed = swype_items.custom_names(db)
    rows = db.scalars(select(SwypeItemSales).where(*where).order_by(
        SwypeItemSales.department, SwypeItemSales.category, SwypeItemSales.total_qty.desc(), SwypeItemSales.description))
    return [{"code": r.item_code, "name": renamed.get(r.item_code, r.description), "department": r.department,
             "category": r.category, "total_qty": float(r.total_qty), "net_amount": float(r.net_amount)} for r in rows]


def period_items(db: Session, location_id: str, period_from: date, period_to: date) -> list[dict]:
    """What one imported period sold. A re-import replaces a period's rows, so this is always the import in effect."""
    return _items(db, SwypeItemSales.location_id == location_id, SwypeItemSales.period_from == period_from,
                  SwypeItemSales.period_to == period_to)


def batch_items(db: Session, batch_id: str) -> list[dict]:
    """What one import brought in; empty once a later import of the same dates replaced it or it was removed."""
    return _items(db, SwypeItemSales.import_batch_id == batch_id)


def _remove(db: Session, *where) -> int:
    """Delete the matching sales rows and mark their import batches "removed", so the same file can be
    imported again later (the duplicate-file check only looks at batches that are still "ok")."""
    batch_ids = set(db.scalars(select(SwypeItemSales.import_batch_id).where(*where).distinct()))
    n = db.execute(delete(SwypeItemSales).where(*where)).rowcount
    for b in db.scalars(select(ImportBatch).where(ImportBatch.id.in_(batch_ids))):
        b.status = "removed"
    swype_items.refresh_prices(db)  # an item's latest period may be gone
    db.commit()
    return n


def delete_period(db: Session, location_id: str, period_from: date, period_to: date) -> int:
    return _remove(db, SwypeItemSales.location_id == location_id, SwypeItemSales.period_from == period_from,
                   SwypeItemSales.period_to == period_to)


def remove_batch(db: Session, batch_id: str) -> int | None:
    """Undo one Item Sales import: delete exactly the sales rows it wrote and mark it "removed". Rows a later
    re-import of the same period replaced already belong to that newer import, so they stay.
    None when the batch is not an imported Item Sales file."""
    b = db.get(ImportBatch, batch_id)
    if b is None or b.dataset != DATASET or b.status != "ok":
        return None
    b.status = "removed"  # also when a re-import already took over all its rows
    return _remove(db, SwypeItemSales.import_batch_id == batch_id)


def delete_range(db: Session, location_id: str, date_from: date, date_to: date) -> dict:
    """Delete every imported period that lies wholly inside date_from..date_to. Periods that only partly
    overlap (e.g. a week sticking out of the range) are kept and listed so the user can widen the range."""
    inside = (SwypeItemSales.location_id == location_id, SwypeItemSales.period_from >= date_from,
              SwypeItemSales.period_to <= date_to)
    periods_n = db.scalar(select(func.count()).select_from(
        select(SwypeItemSales.period_from, SwypeItemSales.period_to).where(*inside).distinct().subquery()))
    partial = db.execute(select(SwypeItemSales.period_from, SwypeItemSales.period_to).where(
        SwypeItemSales.location_id == location_id, SwypeItemSales.period_from <= date_to,
        SwypeItemSales.period_to >= date_from,
        or_(SwypeItemSales.period_from < date_from, SwypeItemSales.period_to > date_to)).distinct()).all()
    n = _remove(db, *inside)
    return {"deleted": n, "periods": periods_n,
            "kept_partial": [{"period_from": a.isoformat(), "period_to": b.isoformat()} for a, b in sorted(partial)]}


def pending_range(db: Session, location_id: str, today: date | None = None) -> tuple[date | None, date]:
    """(first day not yet imported, last full day) for an import since the last one. Today is left out
    because its sales are still coming in. The first day is None when nothing has been imported yet."""
    today = today or datetime.now(ZoneInfo(settings.timezone)).date()
    last = db.scalar(select(func.max(SwypeItemSales.period_to)).where(SwypeItemSales.location_id == location_id))
    return (last + timedelta(days=1) if last else None), today - timedelta(days=1)

