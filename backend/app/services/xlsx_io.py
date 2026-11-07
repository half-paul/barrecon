"""Workbook templates and imports for Items / Counts / Movements.

Validate everything, then write everything, or write nothing. Row-level results are
returned so the UI can show exactly what happened to each row.
"""
from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import MOVEMENT_KINDS, CountSession, ImportBatch, InventoryItem, Unit
from . import inventory as inv

ITEM_COLS = ["sku", "name", "category", "stock_unit", "pack_name", "pack_size", "case_size",
             "pack_cost", "yield_to_unit", "yield_qty", "par_level", "active"]
COUNT_COLS = ["count_date", "sku", "name", "pack_name", "packs_full", "packs_partial", "note"]
MOVE_COLS = ["row_id", "date", "sku", "kind", "packs", "qty", "pack_cost", "external_ref", "note"]

HEADER_FONT = Font(name="Arial", bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")
FONT = Font(name="Arial")


@dataclass
class RowResult:
    row: int
    status: str  # ok | inserted | updated | duplicate | superseded | error
    message: str = ""


@dataclass
class ImportResult:
    dataset: str
    batch_id: str | None = None
    status: str = "ok"  # ok | rejected
    rows: list[RowResult] = field(default_factory=list)
    inserted: int = 0
    updated: int = 0
    duplicate: int = 0
    errors: int = 0
    sessions_created: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "dataset": self.dataset, "batch_id": self.batch_id, "status": self.status,
            "inserted": self.inserted, "updated": self.updated, "duplicate": self.duplicate,
            "errors": self.errors, "sessions_created": self.sessions_created,
            "rows": [r.__dict__ for r in self.rows],
        }


# ---------- helpers ----------

def _style_header(ws, cols: list[str], widths: dict[str, int] | None = None):
    ws.append(cols)
    for c in ws[1]:
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
    for i, col in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(col, 14)
    ws.freeze_panes = "A2"


def _dec(v, field: str, row: int, *, allow_blank=False) -> Decimal | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        if allow_blank:
            return None
        raise ValueError(f"row {row}: {field} is required")
    try:
        return Decimal(str(v).strip().replace(",", ""))
    except InvalidOperation:
        raise ValueError(f"row {row}: {field} is not a number ({v!r})") from None


def _bool(v, default=True) -> bool:
    if v is None or (isinstance(v, str) and not v.strip()):
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "y", "x")


def _date(v, field: str, row: int) -> datetime:
    tz = ZoneInfo(settings.timezone)
    if isinstance(v, datetime):
        d = v if v.tzinfo else v.replace(tzinfo=tz)
        return d.astimezone(timezone.utc)
    if isinstance(v, date):
        return datetime.combine(v, time(23, 59), tzinfo=tz).astimezone(timezone.utc)
    if isinstance(v, str) and v.strip():
        s = v.strip()
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                d = datetime.strptime(s, fmt)
                if fmt == "%Y-%m-%d" or "/" in fmt:
                    d = d.replace(hour=23, minute=59)
                return d.replace(tzinfo=tz).astimezone(timezone.utc)
            except ValueError:
                continue
    raise ValueError(f"row {row}: {field} is not a date ({v!r}); use YYYY-MM-DD")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rows(ws, cols: list[str]) -> list[tuple[int, dict]]:
    """Read header-mapped rows. Header match is case-insensitive; extra columns ignored."""
    it = ws.iter_rows(values_only=True)
    header = next(it, None)
    if not header:
        raise ValueError(f"{ws.title}: empty sheet")
    idx = {str(h).strip().lower(): i for i, h in enumerate(header) if h is not None}
    missing = [c for c in cols if c not in idx]
    if missing:
        raise ValueError(f"{ws.title}: missing columns {missing}")
    out = []
    for n, r in enumerate(it, start=2):
        if r is None or all(v is None or (isinstance(v, str) and not v.strip()) for v in r):
            continue
        out.append((n, {c: r[idx[c]] if idx[c] < len(r) else None for c in cols}))
    return out


def _sheet(wb, name: str):
    for n in wb.sheetnames:
        if n.strip().lower() == name.lower():
            return wb[n]
    raise ValueError(f"workbook has no '{name}' tab (found: {wb.sheetnames})")


def _reject_duplicate_file(db: Session, dataset: str, digest: str) -> ImportBatch | None:
    return db.scalar(select(ImportBatch).where(
        ImportBatch.dataset == dataset, ImportBatch.file_sha256 == digest, ImportBatch.status == "ok"))


# ---------- templates ----------

def items_template(db: Session) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Items"
    _style_header(ws, ITEM_COLS, {"sku": 14, "name": 30, "category": 16})
    for it in db.scalars(select(InventoryItem).order_by(InventoryItem.category, InventoryItem.name)):
        ws.append([it.sku, it.name, it.category, it.stock_unit, it.pack_name, float(it.pack_size),
                   float(it.case_size), float(it.pack_cost) if it.pack_cost is not None else None,
                   it.yield_to_unit, float(it.yield_qty) if it.yield_qty is not None else None,
                   float(it.par_level) if it.par_level is not None else None, bool(it.is_active)])
    if ws.max_row == 1:
        ws.append(["EXAMPLE-SKU", "Example Vodka 750", "Spirits", "ml", "bottle", 750, 12, 32.5, None, None, 4, True])
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.font = FONT
    units = ",".join(u.code for u in db.scalars(select(Unit)))
    dv = DataValidation(type="list", formula1=f'"{units}"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"D2:D{max(ws.max_row, 500)}")
    _legend(wb, "Items: one row per inventory item. sku is the stable key (never change it). "
                "pack_size is in stock_unit (750 for a 750 ml bottle; 1 for beer counted by the can). "
                "case_size = packs per case/flat. A changed pack_cost is recorded as cost history.")
    return _bytes(wb)


def counts_template(db: Session, count_date: date | None = None) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Counts"
    _style_header(ws, COUNT_COLS, {"count_date": 12, "sku": 14, "name": 30, "note": 30})
    d = (count_date or date.today()).isoformat()
    for it in db.scalars(select(InventoryItem).where(InventoryItem.is_active).order_by(InventoryItem.category, InventoryItem.name)):
        ws.append([d, it.sku, it.name, it.pack_name, None, None, None])
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.font = FONT
        row[4].fill = INPUT_FILL
        row[5].fill = INPUT_FILL
    _legend(wb, "Counts: fill packs_full (whole bottles/kegs/units) and packs_partial (e.g. 0.4 of a bottle) "
                "for every item, on the yellow cells. All rows with the same count_date become one count session. "
                "Rows left blank are treated as not counted (the item is excluded from that count), not as zero. "
                "A second upload for a count_date that is already posted creates a correcting count.")
    return _bytes(wb)


def movements_template(db: Session) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Movements"
    _style_header(ws, MOVE_COLS, {"row_id": 12, "date": 12, "sku": 14, "kind": 18, "external_ref": 20, "note": 30})
    ws.append(["D-0001", date.today().isoformat(), "EXAMPLE-SKU", "delivery", 12, None, 32.5, "INV-12345", "example row - delete"])
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.font = FONT
    dv = DataValidation(type="list", formula1='"' + ",".join(MOVEMENT_KINDS) + '"', allow_blank=False)
    ws.add_data_validation(dv)
    dv.add("D2:D1000")
    _legend(wb, "Movements: one row per delivery / transfer / supplier return / breakage. row_id must be unique "
                "and never reused (D-0001, D-0002, ...): it is how re-uploads are matched. Give packs OR qty "
                "(qty is in the item's stock unit). kinds: " + ", ".join(MOVEMENT_KINDS) +
                ". Spilled drinks are NOT entered here: ring them in Swype with the 'spilled' discount.")
    return _bytes(wb)


def _legend(wb, text: str):
    ws = wb.create_sheet("Legend")
    ws["A1"] = text
    ws["A1"].font = FONT
    ws.column_dimensions["A"].width = 140


def _bytes(wb: Workbook) -> bytes:
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


# ---------- imports ----------

def import_items(db: Session, data: bytes, file_name: str) -> ImportResult:
    res = ImportResult("items")
    digest = sha256(data)
    if _reject_duplicate_file(db, "items", digest):
        res.status = "rejected"
        res.rows.append(RowResult(0, "error", "this exact file was already imported"))
        return res
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    rows = _rows(_sheet(wb, "Items"), ITEM_COLS)
    parsed, errors = parse_item_rows(db, rows)
    if errors:
        res.status = "rejected"
        res.rows = errors
        res.errors = len(errors)
        _record_batch(db, res, "xlsx", file_name, digest, len(rows))
        return res
    for n, d in parsed:
        _, created = inv.upsert_item(db, d, cost_source="sheet")
        res.rows.append(RowResult(n, "inserted" if created else "updated", d["sku"]))
        res.inserted += created
        res.updated += not created
    _record_batch(db, res, "xlsx", file_name, digest, len(rows))
    db.commit()
    return res


def parse_item_rows(db: Session, rows: list[tuple[int, dict]]) -> tuple[list[tuple[int, dict]], list[RowResult]]:
    """Items-tab rows -> upsert_item dicts, plus one error per bad row."""
    parsed, errors = [], []
    units = {u.code for u in db.scalars(select(Unit))}
    seen = set()
    for n, r in rows:
        try:
            sku = str(r["sku"] or "").strip()
            if not sku:
                raise ValueError(f"row {n}: sku is required")
            if sku.endswith(".0"):
                sku = sku[:-2]
            if sku in seen:
                raise ValueError(f"row {n}: duplicate sku {sku} in file")
            seen.add(sku)
            su = str(r["stock_unit"] or "").strip().lower()
            if su not in units:
                raise ValueError(f"row {n}: stock_unit {su!r} unknown (use one of {sorted(units)})")
            ytu = str(r["yield_to_unit"]).strip().lower() if r["yield_to_unit"] else None
            if ytu and ytu not in units:
                raise ValueError(f"row {n}: yield_to_unit {ytu!r} unknown")
            parsed.append((n, dict(
                sku=sku, name=str(r["name"] or "").strip(), category=(str(r["category"]).strip() if r["category"] else None),
                stock_unit=su, pack_name=(str(r["pack_name"]).strip() if r["pack_name"] else None),
                pack_size=_dec(r["pack_size"], "pack_size", n), case_size=_dec(r["case_size"], "case_size", n, allow_blank=True) or Decimal(1),
                pack_cost=_dec(r["pack_cost"], "pack_cost", n, allow_blank=True), yield_to_unit=ytu,
                yield_qty=_dec(r["yield_qty"], "yield_qty", n, allow_blank=True),
                par_level=_dec(r["par_level"], "par_level", n, allow_blank=True), is_active=_bool(r["active"]),
            )))
            if not parsed[-1][1]["name"]:
                raise ValueError(f"row {n}: name is required")
        except ValueError as e:
            errors.append(RowResult(n, "error", str(e)))
    return parsed, errors


def import_counts(db: Session, data: bytes, file_name: str, location_id: str) -> ImportResult:
    res = ImportResult("counts")
    digest = sha256(data)
    if _reject_duplicate_file(db, "counts", digest):
        res.status = "rejected"
        res.rows.append(RowResult(0, "error", "this exact file was already imported"))
        return res
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    rows = _rows(_sheet(wb, "Counts"), COUNT_COLS)
    by_date: dict[datetime, list] = {}
    errors = []
    for n, r in rows:
        try:
            pf = _dec(r["packs_full"], "packs_full", n, allow_blank=True)
            pp = _dec(r["packs_partial"], "packs_partial", n, allow_blank=True)
            if pf is None and pp is None:
                continue  # not counted
            sku = str(r["sku"] or "").strip()
            if sku.endswith(".0"):
                sku = sku[:-2]
            item = inv.item_by_sku(db, sku)
            if not item:
                raise ValueError(f"row {n}: unknown sku {sku!r}")
            d = _date(r["count_date"], "count_date", n)
            if d > datetime.now(timezone.utc):
                raise ValueError(f"row {n}: count_date is in the future")
            if (pf or 0) < 0 or (pp or 0) < 0:
                raise ValueError(f"row {n}: negative count")
            by_date.setdefault(d, []).append((n, item, pf or Decimal(0), pp or Decimal(0), r["note"]))
        except ValueError as e:
            errors.append(RowResult(n, "error", str(e)))
    if errors or not by_date:
        res.status = "rejected"
        res.rows = errors or [RowResult(0, "error", "no counted rows found")]
        res.errors = len(res.rows)
        _record_batch(db, res, "xlsx", file_name, digest, len(rows), location_id)
        return res
    for d, lines in sorted(by_date.items()):
        sess = CountSession(location_id=location_id, counted_at=d, status="draft", source="xlsx",
                            external_ref=f"{file_name}#{d.date().isoformat()}")
        db.add(sess)
        db.flush()
        seen_items = set()
        for n, item, pf, pp, note in lines:
            if item.id in seen_items:
                errors.append(RowResult(n, "error", f"row {n}: {item.sku} appears twice for {d.date()}"))
                continue
            seen_items.add(item.id)
            inv.set_count_line(db, sess, item, pf, pp, note)
            res.rows.append(RowResult(n, "inserted", f"{item.sku} @ {d.date()}"))
            res.inserted += 1
        if errors:
            db.rollback()
            res.status = "rejected"
            res.rows = errors
            res.errors = len(errors)
            _record_batch(db, res, "xlsx", file_name, digest, len(rows), location_id)
            return res
        prev = inv.active_session_at(db, location_id, d)
        inv.post_count(db, sess)
        res.sessions_created.append(sess.id)
        if prev and prev.id != sess.id:
            res.rows.append(RowResult(0, "superseded", f"count at {d.date()} replaced the previous one"))
    _record_batch(db, res, "xlsx", file_name, digest, len(rows), location_id)
    db.commit()
    return res


def import_movements(db: Session, data: bytes, file_name: str, location_id: str) -> ImportResult:
    res = ImportResult("movements")
    digest = sha256(data)
    if _reject_duplicate_file(db, "movements", digest):
        res.status = "rejected"
        res.rows.append(RowResult(0, "error", "this exact file was already imported"))
        return res
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    rows = _rows(_sheet(wb, "Movements"), MOVE_COLS)
    parsed, errors, seen = [], [], set()
    for n, r in rows:
        try:
            rid = str(r["row_id"] or "").strip()
            if not rid:
                raise ValueError(f"row {n}: row_id is required")
            if rid in seen:
                raise ValueError(f"row {n}: duplicate row_id {rid}")
            seen.add(rid)
            sku = str(r["sku"] or "").strip()
            if sku.endswith(".0"):
                sku = sku[:-2]
            item = inv.item_by_sku(db, sku)
            if not item:
                raise ValueError(f"row {n}: unknown sku {sku!r}")
            kind = str(r["kind"] or "").strip().lower()
            if kind not in MOVEMENT_KINDS:
                raise ValueError(f"row {n}: kind {kind!r} must be one of {MOVEMENT_KINDS}")
            packs = _dec(r["packs"], "packs", n, allow_blank=True)
            qty = _dec(r["qty"], "qty", n, allow_blank=True)
            if packs is None and qty is None:
                raise ValueError(f"row {n}: packs or qty required")
            note = ((f"{r['external_ref']} " if r["external_ref"] else "") + str(r["note"] or "")).strip() or None
            parsed.append((n, dict(item=item, kind=kind, occurred_at=_date(r["date"], "date", n), external_ref=rid,
                                   packs=packs, qty=qty, pack_cost=_dec(r["pack_cost"], "pack_cost", n, allow_blank=True),
                                   note=note)))
        except ValueError as e:
            errors.append(RowResult(n, "error", str(e)))
    if errors:
        res.status = "rejected"
        res.rows = errors
        res.errors = len(errors)
        _record_batch(db, res, "xlsx", file_name, digest, len(rows), location_id)
        return res
    batch = _record_batch(db, res, "xlsx", file_name, digest, len(rows), location_id)
    for n, d in parsed:
        _, outcome = inv.add_movement(db, location_id=location_id, source="xlsx", import_batch_id=batch.id, **d)
        res.rows.append(RowResult(n, outcome, d["external_ref"]))
        if outcome == "inserted":
            res.inserted += 1
        elif outcome == "duplicate":
            res.duplicate += 1
        else:
            res.updated += 1
    batch.rows_inserted, batch.rows_duplicate = res.inserted, res.duplicate
    db.commit()
    return res


def _record_batch(db: Session, res: ImportResult, source: str, file_name: str, digest: str,
                  received: int, location_id: str | None = None) -> ImportBatch:
    b = ImportBatch(location_id=location_id, source=source, dataset=res.dataset, file_name=file_name,
                    file_sha256=digest, rows_received=received, rows_inserted=res.inserted,
                    rows_duplicate=res.duplicate, rows_error=res.errors, status=res.status,
                    error_log=[r.__dict__ for r in res.rows if r.status == "error"] or None)
    db.add(b)
    db.flush()
    res.batch_id = b.id
    if res.status == "rejected":
        db.commit()
    return b
