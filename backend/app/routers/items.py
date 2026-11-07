from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import perm
from ..db import get_db
from ..models import CountLine, CountSession, InventoryItem, Unit
from ..schemas import CostOut, ItemIn, ItemOut, ItemPatch
from ..services import inventory as inv
from ..services import xlsx_io

router = APIRouter(prefix="/api/items", tags=["items"], dependencies=[perm("items")])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _with_last_count(db: Session, items: list[InventoryItem]) -> list[ItemOut]:
    # latest posted count line per item
    latest = (
        select(CountLine.item_id, func.max(CountSession.counted_at).label("at"))
        .join(CountSession, CountSession.id == CountLine.session_id)
        .where(CountSession.status == "posted")
        .group_by(CountLine.item_id)
        .subquery()
    )
    rows = db.execute(
        select(CountLine.item_id, CountLine.packs_full, CountLine.packs_partial, CountSession.counted_at)
        .join(CountSession, CountSession.id == CountLine.session_id)
        .join(latest, (latest.c.item_id == CountLine.item_id) & (latest.c.at == CountSession.counted_at))
        .where(CountSession.status == "posted")
    ).all()
    last = {r[0]: (r[1] + r[2], r[3]) for r in rows}
    out = []
    for it in items:
        o = ItemOut.model_validate(it)
        if it.id in last:
            o.last_counted_packs, o.last_counted_at = last[it.id]
        out.append(o)
    return out


@router.get("", response_model=list[ItemOut])
def list_items(db: Session = Depends(get_db), include_inactive: bool = False, q: str | None = None):
    stmt = select(InventoryItem)
    if not include_inactive:
        stmt = stmt.where(InventoryItem.is_active)
    if q:
        stmt = stmt.where(func.lower(InventoryItem.name).contains(q.lower()) | func.lower(InventoryItem.sku).contains(q.lower()))
    items = db.scalars(stmt.order_by(InventoryItem.category, InventoryItem.name)).all()
    return _with_last_count(db, items)


@router.get("/units")
def units(db: Session = Depends(get_db)):
    return [{"code": u.code, "dimension": u.dimension} for u in db.scalars(select(Unit).order_by(Unit.dimension, Unit.code))]


@router.get("/export.xlsx")
def export_items(db: Session = Depends(get_db)):
    return Response(xlsx_io.items_template(db), media_type=XLSX,
                    headers={"Content-Disposition": 'attachment; filename="items.xlsx"'})


@router.post("/import")
async def import_items(file: UploadFile, db: Session = Depends(get_db)):
    data = await file.read()
    try:
        res = xlsx_io.import_items(db, data, file.filename or "items.xlsx")
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return res.summary()


@router.post("", response_model=ItemOut, status_code=201)
def create_item(body: ItemIn, db: Session = Depends(get_db)):
    if inv.item_by_sku(db, body.sku):
        raise HTTPException(409, f"sku {body.sku} already exists")
    try:
        item, _ = inv.upsert_item(db, body.model_dump())
    except inv.InventoryError as e:
        raise HTTPException(400, str(e)) from None
    db.commit()
    return _with_last_count(db, [item])[0]


@router.get("/{item_id}", response_model=ItemOut)
def get_item(item_id: str, db: Session = Depends(get_db)):
    it = db.get(InventoryItem, item_id)
    if not it:
        raise HTTPException(404)
    return _with_last_count(db, [it])[0]


@router.patch("/{item_id}", response_model=ItemOut)
def patch_item(item_id: str, body: ItemPatch, db: Session = Depends(get_db)):
    it = db.get(InventoryItem, item_id)
    if not it:
        raise HTTPException(404)
    data = body.model_dump(exclude_unset=True)
    try:
        if "sku" in data:
            inv.rename_sku(db, it, data.pop("sku"))
        data["sku"] = it.sku
        inv.upsert_item(db, data)
    except inv.InventoryError as e:
        raise HTTPException(400, str(e)) from None
    db.commit()
    return _with_last_count(db, [it])[0]


@router.get("/{item_id}/costs", response_model=list[CostOut])
def item_costs(item_id: str, db: Session = Depends(get_db)):
    it = db.get(InventoryItem, item_id)
    if not it:
        raise HTTPException(404)
    return sorted(it.costs, key=lambda c: c.effective_from, reverse=True)
