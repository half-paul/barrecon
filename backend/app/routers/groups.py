from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import perm
from ..db import get_db
from ..models import InventoryItem, ItemGroup
from ..schemas import GroupApplyIn, GroupIn, GroupOut, GroupPatch
from ..services import groups as svc
from ..services import inventory as inv

router = APIRouter(prefix="/api/groups", tags=["groups"], dependencies=[perm("items")])


def _out(db: Session, g: ItemGroup) -> GroupOut:
    items = list(db.scalars(select(InventoryItem).where(InventoryItem.category == g.name)))
    o = GroupOut.model_validate(g)
    o.item_count = len(items)
    o.active_count = sum(1 for i in items if i.is_active)
    o.differing = sum(1 for i in items if svc.differs(g, i))
    return o


def _get(db: Session, name: str) -> ItemGroup:
    g = db.query(ItemGroup).filter(ItemGroup.name == name).one_or_none()
    if not g:
        raise HTTPException(404, f"no group {name}")
    return g


@router.get("", response_model=list[GroupOut])
def list_groups(db: Session = Depends(get_db)):
    svc.sync(db)
    db.commit()
    return [_out(db, g) for g in db.scalars(select(ItemGroup).order_by(ItemGroup.name))]


@router.post("", response_model=GroupOut, status_code=201)
def create_group(body: GroupIn, db: Session = Depends(get_db)):
    svc.sync(db)
    try:
        g = svc.create(db, body.model_dump())
    except inv.InventoryError as e:
        raise HTTPException(400, str(e)) from None
    db.commit()
    return _out(db, g)


@router.patch("/{name}", response_model=GroupOut)
def patch_group(name: str, body: GroupPatch, db: Session = Depends(get_db)):
    try:
        g = svc.update(db, _get(db, name), body.model_dump(exclude_unset=True))
    except inv.InventoryError as e:
        raise HTTPException(400, str(e)) from None
    db.commit()
    return _out(db, g)


@router.post("/{name}/apply")
def apply_group(name: str, body: GroupApplyIn, db: Session = Depends(get_db)):
    try:
        res = svc.apply(db, _get(db, name), body.fields)
    except inv.InventoryError as e:
        raise HTTPException(400, str(e)) from None
    db.commit()
    return res


@router.delete("/{name}")
def delete_group(name: str, move_to: str | None = None, db: Session = Depends(get_db)):
    try:
        moved = svc.delete(db, _get(db, name), move_to or None)
    except inv.InventoryError as e:
        raise HTTPException(400, str(e)) from None
    db.commit()
    return {"moved": moved}
