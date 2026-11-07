"""Item master, counts and movements: the rules that every entry path shares."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    MOVEMENT_KINDS,
    CountLine,
    CountSession,
    InventoryItem,
    InventoryItemCost,
    StockMovement,
    utcnow,
)


class InventoryError(ValueError):
    pass


def to_utc(d: datetime) -> datetime:
    """Store every instant as UTC. SQLite drops tzinfo, so naive values are read back as UTC."""
    from datetime import timezone as _tz
    return (d if d.tzinfo else d.replace(tzinfo=_tz.utc)).astimezone(_tz.utc)


def _same_instant(a: datetime, b: datetime) -> bool:
    return to_utc(a) == to_utc(b)


ITEM_FIELDS = (
    "name", "category", "stock_unit", "pack_name", "pack_size", "case_size",
    "yield_to_unit", "yield_qty", "par_level", "is_active", "location_id",
)
NULLABLE_FIELDS = ("yield_to_unit", "yield_qty", "par_level", "category", "pack_name", "location_id")


# ---------- items ----------

def upsert_item(db: Session, data: dict, *, cost_source: str = "manual") -> tuple[InventoryItem, bool]:
    """Create or update by sku. Returns (item, created). A changed pack_cost adds a cost-history row."""
    sku = str(data.get("sku") or "").strip()
    if not sku:
        raise InventoryError("sku is required")
    item = db.scalar(select(InventoryItem).where(InventoryItem.sku == sku))
    created = item is None
    if created:
        item = InventoryItem(sku=sku)
        db.add(item)
    for f in ITEM_FIELDS:
        if f in data and (data[f] is not None or f in NULLABLE_FIELDS):
            setattr(item, f, data[f])
    if not item.name:
        raise InventoryError(f"{sku}: name is required")
    if not item.stock_unit:
        raise InventoryError(f"{sku}: stock_unit is required")
    if item.pack_size is None or Decimal(item.pack_size) <= 0:
        raise InventoryError(f"{sku}: pack_size must be > 0")
    if item.case_size is None or Decimal(item.case_size) <= 0:
        item.case_size = Decimal(1)
    if item.is_active is None:
        item.is_active = True
    if "pack_cost" in data and data["pack_cost"] is not None:
        new_cost = Decimal(str(data["pack_cost"]))
        if item.pack_cost is None or Decimal(item.pack_cost) != new_cost:
            item.pack_cost = new_cost
            db.add(InventoryItemCost(item=item, effective_from=utcnow(), pack_cost=new_cost, source=cost_source))
    db.flush()
    return item, created


def rename_sku(db: Session, item: InventoryItem, new_sku: str) -> None:
    """Change an item's SKU. Counts, movements and recipes point at item.id, so nothing else moves."""
    sku = str(new_sku or "").strip()
    if not sku:
        raise InventoryError("sku is required")
    if sku == item.sku:
        return
    other = item_by_sku(db, sku)
    if other is not None:
        raise InventoryError(f"sku {sku} already belongs to {other.name}")
    item.sku = sku
    db.flush()


def item_by_sku(db: Session, sku: str) -> InventoryItem | None:
    return db.scalar(select(InventoryItem).where(InventoryItem.sku == str(sku).strip()))


# ---------- counts ----------

def packs_to_qty(item: InventoryItem, packs_full: Decimal, packs_partial: Decimal) -> Decimal:
    return (Decimal(packs_full or 0) + Decimal(packs_partial or 0)) * Decimal(item.pack_size)


def set_count_line(db: Session, session: CountSession, item: InventoryItem,
                   packs_full: Decimal, packs_partial: Decimal, note: str | None = None) -> CountLine:
    if session.status != "draft":
        raise InventoryError("count session is not a draft")
    if Decimal(packs_full or 0) < 0 or Decimal(packs_partial or 0) < 0:
        raise InventoryError(f"{item.sku}: negative count")
    db.flush()
    line = db.get(CountLine, (session.id, item.id))
    if line is None:
        line = CountLine(session_id=session.id, item_id=item.id)
        session.lines.append(line)
    line.packs_full = Decimal(packs_full or 0)
    line.packs_partial = Decimal(packs_partial or 0)
    line.qty = packs_to_qty(item, line.packs_full, line.packs_partial)
    line.note = note
    return line


def active_session_at(db: Session, location_id: str, counted_at: datetime, *, include_deleted: bool = False) -> CountSession | None:
    counted_at = to_utc(counted_at)
    gone = ("superseded",) if include_deleted else ("superseded", "deleted")
    return db.scalar(
        select(CountSession).where(
            CountSession.location_id == location_id,
            CountSession.counted_at == counted_at,
            CountSession.status.not_in(gone),
        )
    )


def post_count(db: Session, session: CountSession) -> CountSession:
    """Post a draft. A posted session at the same counted_at is superseded, never overwritten."""
    if session.status != "draft":
        raise InventoryError("only drafts can be posted")
    db.flush()
    db.refresh(session)
    if not session.lines:
        raise InventoryError("count has no lines")
    prev = db.scalars(
        select(CountSession).where(
            CountSession.location_id == session.location_id,
            CountSession.counted_at == session.counted_at,
            CountSession.status == "posted",
            CountSession.id != session.id,
        )
    ).all()
    for p in prev:
        p.status = "superseded"
        p.superseded_by = session.id
    session.status = "posted"
    session.posted_at = utcnow()
    db.flush()
    return session


def redate_count(db: Session, session: CountSession, counted_at: datetime, note: str) -> CountSession:
    """Move a posted count to another date and time without rewriting it: a copy with the same lines is
    posted at the new time and the original is superseded by it, so the old version stays as history."""
    if session.status != "posted":
        raise InventoryError("only a posted count can be moved this way")
    counted_at = to_utc(counted_at)
    if _same_instant(counted_at, session.counted_at):
        raise InventoryError("the count is already at that date and time")
    new = CountSession(location_id=session.location_id, counted_at=counted_at, source=session.source,
                       external_ref=session.external_ref, note=note, status="draft")
    db.add(new)
    db.flush()
    for ln in session.lines:
        db.add(CountLine(session_id=new.id, item_id=ln.item_id, packs_full=ln.packs_full,
                         packs_partial=ln.packs_partial, qty=ln.qty, note=ln.note))
    post_count(db, new)
    session.status = "superseded"
    session.superseded_by = new.id
    db.flush()
    return new


def count_versions(db: Session, session: CountSession) -> list[CountSession]:
    """Every version linked to this count through superseded_by (earlier ones it replaced, later ones that
    replaced it), oldest first."""
    seen = {session.id: session}
    todo = [session]
    while todo:
        s = todo.pop()
        linked = list(db.scalars(select(CountSession).where(CountSession.superseded_by == s.id)))
        if s.superseded_by:
            linked.append(db.get(CountSession, s.superseded_by))
        for x in linked:
            if x is not None and x.id not in seen:
                seen[x.id] = x
                todo.append(x)
    return sorted(seen.values(), key=lambda x: x.posted_at or x.created_at)


def delete_count(db: Session, session: CountSession) -> None:
    """A draft was never official, so it just goes. A posted count stays in the database as it is, flagged "deleted":
    like a superseded one it is kept as history and nothing counts it (only "posted" feeds variance and on-hand)."""
    if session.status == "draft":
        db.delete(session)
    elif session.status == "posted":
        session.status = "deleted"
    elif session.status == "superseded":
        raise InventoryError("this is an old version of a count; delete the current version instead")
    else:
        raise InventoryError("this count is already deleted")
    db.flush()


# ---------- movements ----------

def add_movement(db: Session, *, location_id: str, item: InventoryItem, kind: str,
                 occurred_at: datetime, external_ref: str, packs: Decimal | None = None,
                 qty: Decimal | None = None, pack_cost: Decimal | None = None,
                 source: str = "portal", note: str | None = None,
                 import_batch_id: str | None = None) -> tuple[StockMovement, str]:
    """Insert a movement. Returns (movement, 'inserted'|'duplicate'|'superseded').

    Idempotent on (location, item, kind, external_ref): identical content is a no-op,
    changed content supersedes the previous row (kept for audit).
    """
    if kind not in MOVEMENT_KINDS:
        raise InventoryError(f"unknown movement kind {kind!r}")
    occurred_at = to_utc(occurred_at)
    ref = str(external_ref or "").strip()
    if not ref:
        raise InventoryError(f"{item.sku}: external_ref is required (invoice no., row id)")
    if qty is None:
        if packs is None:
            raise InventoryError(f"{item.sku}: packs or qty required")
        qty = Decimal(packs) * Decimal(item.pack_size)
    qty = Decimal(qty)
    if qty <= 0:
        raise InventoryError(f"{item.sku}: quantity must be > 0")
    existing = db.scalar(
        select(StockMovement).where(
            StockMovement.location_id == location_id,
            StockMovement.item_id == item.id,
            StockMovement.kind == kind,
            StockMovement.external_ref == ref,
            StockMovement.status == "active",
        )
    )
    if existing:
        same_cost = (existing.pack_cost is None and pack_cost is None) or (
            existing.pack_cost is not None and pack_cost is not None
            and Decimal(existing.pack_cost) == Decimal(pack_cost)
        )
        if Decimal(existing.qty) == qty and _same_instant(existing.occurred_at, occurred_at) and same_cost:
            return existing, "duplicate"
        existing.status = "superseded"
        existing.external_ref = f"{ref}#superseded-{existing.id[:8]}"
        db.flush()
        outcome = "superseded"
    else:
        outcome = "inserted"
    mv = StockMovement(
        location_id=location_id, item_id=item.id, kind=kind, qty=qty, packs=packs,
        pack_cost=pack_cost, occurred_at=occurred_at, external_ref=ref,
        source=source, note=note, import_batch_id=import_batch_id,
    )
    db.add(mv)
    if kind == "delivery" and pack_cost is not None:
        pc = Decimal(pack_cost)
        if item.pack_cost is None or Decimal(item.pack_cost) != pc:
            item.pack_cost = pc
            db.add(InventoryItemCost(item=item, effective_from=occurred_at, pack_cost=pc, source="delivery"))
    db.flush()
    return mv, outcome
