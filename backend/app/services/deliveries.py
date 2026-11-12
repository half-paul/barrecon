"""Deliveries made from store receipts: filling a draft from what was read, remembering what each store's
receipt lines mean, checking the draft before it posts, and posting it as delivery movements.

A draft pre-fills from what the store's SKU meant before, then from the stock item with the printed SKU, at any
store, then from what the line's text meant before. Nothing reaches stock until someone reviews the lines and posts
it, and posting remembers each line, so a match fixed by hand wins next time.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, time
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Delivery, DeliveryLine, InventoryItem, InventoryItemCost, ReceiptAlias, StockMovement, utcnow
from . import inventory as inv
from . import settings_svc

TOTAL_TOLERANCE = Decimal("0.05")  # rounding on a receipt
CENT, TENTH_CENT = Decimal("0.01"), Decimal("0.0001")
NEVER_STOCK = ("container deposit",)  # receipt lines that stay for the totals but are never stock
MOVEMENT_SOURCE = {"receipt": "receipt", "sheet": "xlsx", "manual": "portal"}  # Delivery.source -> movement source


class DeliveryError(ValueError):
    pass


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9.]+", " ", (s or "").lower()).strip()


def store_key(store: str | None) -> str:
    """Branches of one chain share what they've learned: "BC Liquor Store #212" -> "bc liquor store"."""
    return re.sub(r"\s+", " ", re.sub(r"[0-9#.]+", " ", _norm(store))).strip()


def text_key(text: str | None) -> str:
    return re.sub(r"\s+", " ", _norm(text)).strip()


def sku_key(sku: str | None) -> str:
    """A product number to compare: "0010157" and "10157" are the same SKU."""
    return re.sub(r"[^a-z0-9]+", "", (sku or "").lower()).lstrip("0")


def _dec(v) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def _local_dt(date_s: str | None, time_s: str | None) -> datetime | None:
    """A receipt's printed local date and time as UTC; noon when only the date is printed."""
    if not date_s:
        return None
    try:
        day = datetime.strptime(date_s.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None
    t = time(12, 0)
    if time_s:
        try:
            t = datetime.strptime(time_s.strip()[:5], "%H:%M").time()
        except ValueError:
            pass
    return inv.to_utc(datetime.combine(day, t, tzinfo=ZoneInfo(settings.timezone)))


# ---------- filling a draft ----------

def apply_read(db: Session, d: Delivery, data: dict) -> None:
    """Replace the draft's header and lines with what was read off the receipt, then pre-fill from memory."""
    if d.status != "draft":
        raise DeliveryError("only a draft can be re-read")
    d.extracted = data
    d.read_error = None
    d.store = (data.get("store") or "").strip()[:200] or d.store
    d.store_address = (data.get("store_address") or "").strip()[:300] or d.store_address
    d.occurred_at = _local_dt(data.get("date"), data.get("time")) or d.occurred_at
    d.receipt_no = (data.get("receipt_no") or "").strip()[:100] or d.receipt_no
    d.subtotal, d.tax, d.total = _dec(data.get("subtotal")), _dec(data.get("tax")), _dec(data.get("total"))
    d.lines.clear()
    db.flush()
    for i, ln in enumerate(data.get("lines") or []):
        text = (ln.get("text") or "").strip()[:300]
        if not text:
            continue
        qty = _dec(ln.get("qty"))
        d.lines.append(DeliveryLine(position=i, text=text, receipt_sku=(ln.get("sku") or "").strip()[:64] or None,
                                    receipt_qty=qty if qty and qty > 0 else Decimal(1),
                                    line_total=_dec(ln.get("line_total"))))
    db.flush()
    suggest(db, d)


def _sku_alias(db: Session, skey: str, sku: str) -> ReceiptAlias | None:
    return db.scalar(select(ReceiptAlias).where(ReceiptAlias.store_key == skey, ReceiptAlias.sku == sku)
                     .order_by(ReceiptAlias.updated_at.desc()).limit(1))


def _text_alias(db: Session, skey: str, tkey: str, sku: str) -> ReceiptAlias | None:
    """A line text learned before. Same words with a different SKU are a different product."""
    rows = list(db.scalars(select(ReceiptAlias).where(ReceiptAlias.store_key == skey, ReceiptAlias.text_key == tkey)))
    # otherwise the same line text learned at other stores
    rows = rows or list(db.scalars(select(ReceiptAlias).where(ReceiptAlias.text_key == tkey)))
    rows = [a for a in rows if not sku or not a.sku or a.sku == sku]
    return rows[0] if rows and len({(a.item_id, a.packs_per_unit) for a in rows}) == 1 else None


def _stock_by_sku(db: Session) -> dict[str, InventoryItem]:
    """Stock items by SKU; active items win a clash."""
    out: dict[str, InventoryItem] = {}
    for it in sorted(db.scalars(select(InventoryItem)), key=lambda i: not i.is_active):
        out.setdefault(sku_key(it.sku), it)
    return out


def suggest(db: Session, d: Delivery) -> int:
    """Pre-fill lines nobody has touched: lines that are never stock, then what this store's SKU meant before,
    then the stock item with that SKU, then what the line's text meant before. Returns how many."""
    skey, n = store_key(d.store), 0
    by_sku = _stock_by_sku(db)
    for ln in d.lines:
        if ln.item_id or ln.skip or ln.matched_by:
            continue
        if any(t in text_key(ln.text) for t in NEVER_STOCK):
            ln.skip, ln.matched_by = True, "rule"
            n += 1
            continue
        sku = sku_key(ln.receipt_sku)
        a = _sku_alias(db, skey, sku) if sku else None
        if a is None and sku and sku in by_sku:
            ln.item_id, ln.packs, ln.matched_by = by_sku[sku].id, Decimal(ln.receipt_qty), "sku"
            n += 1
            continue
        a = a or _text_alias(db, skey, text_key(ln.text), sku)
        if a is None:
            continue
        if a.item_id is None:
            ln.skip = True
        elif db.get(InventoryItem, a.item_id) is not None:
            ln.item_id = a.item_id
            ln.packs = Decimal(ln.receipt_qty) * Decimal(a.packs_per_unit)
        else:
            continue
        ln.matched_by = "memory"
        n += 1
    db.flush()
    return n


def update(db: Session, d: Delivery, body) -> None:
    """Save the reviewed header and lines (a ReceiptDeliveryIn)."""
    if d.status != "draft":
        raise DeliveryError("this delivery is posted; reopen it to change it")
    for f in ("store", "store_address", "receipt_no", "subtotal", "tax", "total", "note"):
        v = getattr(body, f)
        setattr(d, f, (v.strip() or None) if isinstance(v, str) else v)
    d.occurred_at = inv.to_utc(body.occurred_at) if body.occurred_at else None
    old = {(ln.position, ln.text): ln for ln in d.lines}
    d.lines.clear()
    db.flush()
    for i, ln in enumerate(body.lines):
        if ln.item_id and db.get(InventoryItem, ln.item_id) is None:
            raise DeliveryError(f"line {i + 1}: unknown item")
        prev = old.get((i, ln.text))
        same = prev is not None and prev.item_id == ln.item_id and prev.skip == ln.skip
        d.lines.append(DeliveryLine(
            position=i, text=ln.text.strip(), receipt_sku=ln.receipt_sku or (prev.receipt_sku if prev else None),
            receipt_qty=ln.receipt_qty, line_total=ln.line_total,
            item_id=None if ln.skip else ln.item_id, packs=None if ln.skip else ln.packs, skip=ln.skip,
            matched_by=(prev.matched_by if same else "hand") if (ln.item_id or ln.skip) else None))
    db.flush()


# ---------- checks ----------

def _prev_pack_cost(db: Session, item: InventoryItem, before: datetime | None) -> Decimal | None:
    """The pack cost in effect before the purchase; the item's current cost when there's no earlier history."""
    c = None if before is None else db.scalar(
        select(InventoryItemCost).where(InventoryItemCost.item_id == item.id,
                                        InventoryItemCost.effective_from < inv.to_utc(before))
        .order_by(InventoryItemCost.effective_from.desc()).limit(1))
    if c is not None:
        return Decimal(c.pack_cost)
    return Decimal(item.pack_cost) if item.pack_cost is not None else None


def checks(db: Session, d: Delivery) -> dict:
    """What to look at before posting: lines still to match, whether the lines add up to the receipt's
    totals, pack costs that moved more than the alert setting, and a delivery that looks like this one."""
    threshold = Decimal(settings_svc.get(db, "price_alert_pct"))
    lines_sum = sum((Decimal(ln.line_total) for ln in d.lines if ln.line_total is not None), Decimal(0)).quantize(CENT)
    missing_amounts = sum(1 for ln in d.lines if ln.line_total is None)
    tax = Decimal(d.tax) if d.tax is not None else Decimal(0)
    total_diff = None if d.total is None else ((lines_sum + tax) - Decimal(d.total)).quantize(CENT)
    subtotal_diff = None if d.subtotal is None else (lines_sum - Decimal(d.subtotal)).quantize(CENT)
    unmatched = [ln.position for ln in d.lines if not ln.skip and not ln.item_id]
    no_packs = [ln.position for ln in d.lines if not ln.skip and ln.item_id and not (ln.packs and ln.packs > 0)]

    prices = []
    for ln in d.lines:
        if ln.skip or not ln.item_id or not ln.packs or ln.packs <= 0 or ln.line_total is None:
            continue
        cost = (Decimal(ln.line_total) / Decimal(ln.packs)).quantize(TENTH_CENT)
        prev = _prev_pack_cost(db, ln.item, d.occurred_at)
        pct = None if not prev else ((cost - prev) / prev * 100).quantize(Decimal("0.1"))
        prices.append({"position": ln.position, "item_id": ln.item_id, "pack_cost": cost, "previous": prev,
                       "change_pct": pct, "alert": pct is not None and abs(pct) >= threshold})

    dup = None
    if d.occurred_at is not None and d.status != "void":
        for o in db.scalars(select(Delivery).where(Delivery.id != d.id, Delivery.status != "void",
                                                   Delivery.occurred_at == inv.to_utc(d.occurred_at))):
            if store_key(o.store) == store_key(d.store) and (d.total is None or o.total is None or
                                                             Decimal(o.total) == Decimal(d.total)):
                dup = {"id": o.id, "status": o.status}
                break

    def off(x):
        return x is not None and abs(x) > TOTAL_TOLERANCE
    return {
        "lines_sum": lines_sum, "missing_amounts": missing_amounts,
        "total_diff": total_diff, "subtotal_diff": subtotal_diff,
        "totals_ok": not off(total_diff) and not off(subtotal_diff) and missing_amounts == 0,
        "unmatched": unmatched, "no_packs": no_packs,
        "prices": prices, "price_alerts": sum(1 for p in prices if p["alert"]),
        "threshold_pct": threshold, "possible_duplicate": dup,
    }


# ---------- posting ----------

def _ref(d: Delivery) -> str:
    """Unique per delivery, so two deliveries never supersede each other's movements."""
    return f"{d.receipt_no or 'receipt'} ({d.id[:8]})"[:200]


def post(db: Session, d: Delivery) -> int:
    """Write one delivery movement per item (lines of the same item are added up), learn what each line
    meant for this store, and mark the delivery posted. Returns the number of movements."""
    if d.status != "draft":
        raise DeliveryError("only a draft can be posted")
    if d.occurred_at is None:
        raise DeliveryError("set the date and time of the purchase")
    if not (d.store or "").strip():
        raise DeliveryError("set the store")
    c = checks(db, d)
    if c["unmatched"]:
        raise DeliveryError(f"{len(c['unmatched'])} line(s) need a stock item, or mark them as not stock")
    if c["no_packs"]:
        raise DeliveryError(f"{len(c['no_packs'])} line(s) need the number of packs")
    stock = [ln for ln in d.lines if not ln.skip]
    if not stock:
        raise DeliveryError("no line puts anything into stock")

    by_item: dict[str, list[DeliveryLine]] = defaultdict(list)
    for ln in stock:
        by_item[ln.item_id].append(ln)
    note = " · ".join(x for x in (d.store, f"receipt {d.receipt_no}" if d.receipt_no else None, d.note) if x)
    for item_id, lns in by_item.items():
        packs = sum((Decimal(ln.packs) for ln in lns), Decimal(0))
        cost = None
        if all(ln.line_total is not None for ln in lns):
            cost = (sum((Decimal(ln.line_total) for ln in lns), Decimal(0)) / packs).quantize(TENTH_CENT)
        m, _ = inv.add_movement(db, location_id=d.location_id, item=db.get(InventoryItem, item_id), kind="delivery",
                                occurred_at=d.occurred_at, external_ref=_ref(d), packs=packs, pack_cost=cost,
                                source=MOVEMENT_SOURCE.get(d.source, "receipt"), note=note[:1000])
        m.delivery_id = d.id
    if d.source == "receipt":  # sheet lines are stock item names, nothing a store prints
        _learn(db, d)
    d.status = "posted"
    d.posted_at = utcnow()
    db.flush()
    return len(by_item)


def _learn(db: Session, d: Delivery) -> None:
    skey = store_key(d.store)
    if not skey:
        return
    for ln in d.lines:
        tkey = text_key(ln.text)
        if not tkey or (not ln.skip and not ln.item_id):
            continue
        sku = sku_key(ln.receipt_sku) or None
        a = db.scalar(select(ReceiptAlias).where(ReceiptAlias.store_key == skey, ReceiptAlias.text_key == tkey,
                                                 ReceiptAlias.sku == sku))
        if a is None:
            a = ReceiptAlias(store_key=skey, text_key=tkey, sku=sku)
            db.add(a)
        a.item_id = None if ln.skip else ln.item_id
        a.packs_per_unit = Decimal(1) if ln.skip or not ln.receipt_qty else Decimal(ln.packs) / Decimal(ln.receipt_qty)
        db.flush()  # the session doesn't autoflush, and a later line can be this same product


def _retract(db: Session, d: Delivery) -> int:
    n = 0
    for m in db.scalars(select(StockMovement).where(StockMovement.delivery_id == d.id,
                                                    StockMovement.status == "active")):
        m.status = "retracted"
        m.external_ref = f"{m.external_ref}#retracted-{m.id[:8]}"
        n += 1
    db.flush()
    return n


def reopen(db: Session, d: Delivery) -> None:
    """Take a posted delivery back to a draft: its movements are retracted (kept as history)."""
    if d.status != "posted":
        raise DeliveryError("only a posted delivery can be reopened")
    _retract(db, d)
    d.status = "draft"
    d.posted_at = None
    db.flush()


# ---------- deliveries entered on a sheet ----------

SHEET_SOURCES = ("sheet",)  # the uploaded deliveries sheet and the quick sheet in the portal


def record_sheet(db: Session, *, location_id: str, source: str, occurred_at: datetime, ref: str,
                 store: str | None, note: str | None,
                 lines: list[tuple[InventoryItem, Decimal, Decimal | None]]) -> tuple[Delivery, str]:
    """Record a delivery entered on a sheet (item, packs, pack cost per line) and post it at once.

    The same reference on the same local day from the same kind of sheet is the same delivery: identical
    content is left alone ('duplicate'); changed content replaces its lines and posts again ('superseded'),
    the old movements kept as retracted history. A new one is 'inserted'."""
    if source not in SHEET_SOURCES:
        raise DeliveryError(f"unknown sheet source {source!r}")
    ref = (ref or "").strip()[:100]
    if not ref:
        raise DeliveryError("a delivery needs a reference (invoice number)")
    if not lines:
        raise DeliveryError("no line puts anything into stock")
    occurred_at = inv.to_utc(occurred_at)
    tz = ZoneInfo(settings.timezone)
    day = occurred_at.astimezone(tz).date()
    new = [(it, Decimal(p), None if c is None else (Decimal(p) * Decimal(c)).quantize(CENT)) for it, p, c in lines]
    store = (store or "").strip()[:200] or None
    existing = next((d for d in db.scalars(select(Delivery).where(
        Delivery.location_id == location_id, Delivery.source == source, Delivery.receipt_no == ref,
        Delivery.status != "void")) if d.occurred_at and inv.to_utc(d.occurred_at).astimezone(tz).date() == day), None)
    if existing is not None:
        def key(rows):
            return sorted((i, Decimal(p).normalize(), None if t is None else Decimal(t)) for i, p, t in rows)
        same = (existing.status == "posted" and inv.to_utc(existing.occurred_at) == occurred_at
                and (store is None or existing.store == store) and (existing.note or None) == (note or None)
                and key((ln.item_id, ln.packs, ln.line_total) for ln in existing.lines if not ln.skip)
                == key((it.id, p, t) for it, p, t in new))
        if same:
            return existing, "duplicate"
        if existing.status == "posted":
            reopen(db, existing)
        d, outcome = existing, "superseded"
    else:
        d = Delivery(location_id=location_id, source=source)
        db.add(d)
        outcome = "inserted"
    d.receipt_no, d.occurred_at, d.note = ref, occurred_at, (note or None)
    d.store = store or d.store or "Deliveries sheet"
    d.lines.clear()
    db.flush()
    for i, (it, p, t) in enumerate(new):
        d.lines.append(DeliveryLine(position=i, text=it.name[:300], receipt_sku=it.sku[:64], receipt_qty=p,
                                    line_total=t, item_id=it.id, packs=p, matched_by="sheet"))
    db.flush()
    post(db, d)
    return d, outcome


def void(db: Session, d: Delivery) -> None:
    if d.status == "void":
        return
    _retract(db, d)
    d.status = "void"
    db.flush()
