"""Stock item groups. An item's group is its category string; item_groups holds the group's defaults."""
from __future__ import annotations

from collections import Counter
from decimal import Decimal

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from ..models import CountLine, InventoryItem, ItemGroup, StockMovement, Unit
from .inventory import InventoryError

PACK_FIELDS = ("stock_unit", "pack_name", "pack_size", "case_size")


def _items(db: Session, name: str) -> list[InventoryItem]:
    return list(db.scalars(select(InventoryItem).where(InventoryItem.category == name)))


def sync(db: Session) -> None:
    """Give every category used by an item a group row, with the defaults most of its items share."""
    known = set(db.scalars(select(ItemGroup.name)))
    by_cat: dict[str, list[InventoryItem]] = {}
    for it in db.scalars(select(InventoryItem).where(InventoryItem.category.is_not(None))):
        if it.category and it.category not in known:
            by_cat.setdefault(it.category, []).append(it)
    for name, items in by_cat.items():
        unit, pack_name, pack_size, case_size = Counter(
            (i.stock_unit, i.pack_name, Decimal(i.pack_size), Decimal(i.case_size)) for i in items).most_common(1)[0][0]
        db.add(ItemGroup(name=name, stock_unit=unit, pack_name=pack_name, pack_size=pack_size, case_size=case_size))
    if by_cat:
        db.flush()


def differs(g: ItemGroup, it: InventoryItem) -> bool:
    return (it.stock_unit != g.stock_unit or (it.pack_name or "") != (g.pack_name or "")
            or Decimal(it.pack_size) != Decimal(g.pack_size) or Decimal(it.case_size) != Decimal(g.case_size))


def _check(db: Session, data: dict) -> None:
    if "stock_unit" in data and data["stock_unit"] is not None and db.get(Unit, data["stock_unit"]) is None:
        raise InventoryError(f"unknown unit {data['stock_unit']}")


def create(db: Session, data: dict) -> ItemGroup:
    name = data["name"].strip()
    if not name:
        raise InventoryError("name is required")
    if db.query(ItemGroup).filter(ItemGroup.name == name).one_or_none():
        raise InventoryError(f"group {name} already exists")
    _check(db, data)
    g = ItemGroup(**{**data, "name": name})
    db.add(g)
    db.flush()
    return g


def update(db: Session, g: ItemGroup, data: dict) -> ItemGroup:
    """Change the defaults, or rename (which moves every item in the group to the new name)."""
    _check(db, data)
    new = (data.pop("name", None) or g.name).strip()
    for f in PACK_FIELDS:
        if f in data and (data[f] is not None or f == "pack_name"):
            setattr(g, f, data[f])
    if data.get("sheet_in_cases") is not None:
        g.sheet_in_cases = data["sheet_in_cases"]
    if new != g.name:
        if db.query(ItemGroup).filter(ItemGroup.name == new).one_or_none():
            raise InventoryError(f"group {new} already exists")
        old = g.name
        fresh = ItemGroup(name=new, stock_unit=g.stock_unit, pack_name=g.pack_name, pack_size=g.pack_size,
                          case_size=g.case_size, sheet_in_cases=g.sheet_in_cases, created_at=g.created_at)
        for it in _items(db, old):
            it.category = new
        db.delete(g)
        db.flush()
        db.add(fresh)
        g = fresh
    db.flush()
    return g


def delete(db: Session, g: ItemGroup, move_to: str | None) -> int:
    """Remove a group. Its items move to another group, or become ungrouped. Returns how many moved."""
    if move_to is not None and move_to != g.name and db.query(ItemGroup).filter(ItemGroup.name == move_to).one_or_none() is None:
        raise InventoryError(f"unknown group {move_to}")
    items = _items(db, g.name)
    for it in items:
        it.category = move_to if move_to != g.name else None
    db.delete(g)
    db.flush()
    return len(items)


def has_history(db: Session, item_id: str) -> bool:
    return bool(db.scalar(select(exists().where(CountLine.item_id == item_id)))
                or db.scalar(select(exists().where(StockMovement.item_id == item_id))))


def apply(db: Session, g: ItemGroup, fields: list[str]) -> dict:
    """Copy the group's defaults onto its items. Counts and movements are stored in the item's unit,
    so the unit of an item that already has any is left alone and reported as skipped."""
    bad = [f for f in fields if f not in PACK_FIELDS]
    if bad:
        raise InventoryError(f"cannot apply {', '.join(bad)}")
    updated, skipped = 0, []
    for it in _items(db, g.name):
        changed = False
        for f in fields:
            new = getattr(g, f)
            if getattr(it, f) == new or (f in ("pack_size", "case_size") and Decimal(getattr(it, f)) == Decimal(new)):
                continue
            if f == "stock_unit" and has_history(db, it.id):
                skipped.append(it.name)
                continue
            setattr(it, f, new)
            changed = True
        updated += changed
    db.flush()
    return {"updated": updated, "skipped_unit": skipped}
