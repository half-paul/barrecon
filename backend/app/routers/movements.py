from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import perm
from ..db import get_db
from ..models import MOVEMENT_KINDS, InventoryItem, StockMovement
from ..schemas import DeliveryIn, MovementIn, MovementOut
from ..tenancy import business_id
from ..services import deliveries as dlv
from ..services import inventory as inv
from ..services import xlsx_io

router = APIRouter(prefix="/api/movements", tags=["movements"], dependencies=[perm("deliveries")])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _out(m: StockMovement) -> MovementOut:
    return MovementOut(id=m.id, item_id=m.item_id, sku=m.item.sku, name=m.item.name, kind=m.kind, qty=m.qty,
                       packs=m.packs, pack_cost=m.pack_cost, occurred_at=m.occurred_at, external_ref=m.external_ref,
                       source=m.source, status=m.status, note=m.note, delivery_id=m.delivery_id)


@router.get("", response_model=list[MovementOut])
def list_movements(db: Session = Depends(get_db), include_inactive: bool = False, limit: int = 500):
    stmt = select(StockMovement)
    if not include_inactive:
        stmt = stmt.where(StockMovement.status == "active")
    rows = db.scalars(stmt.order_by(StockMovement.occurred_at.desc()).limit(limit)).all()
    return [_out(m) for m in rows]


@router.get("/kinds")
def kinds():
    return [{"kind": k, "label": {
        "delivery": "Delivery", "transfer_in": "Transfer in", "transfer_out": "Transfer out",
        "supplier_return": "Supplier return",
        "breakage_expired": "Breakage / expired (not spills: ring those in Swype)",
        "other_explained": "Other explained consumption"}[k]} for k in MOVEMENT_KINDS]


@router.get("/template.xlsx")
def template(db: Session = Depends(get_db)):
    return Response(xlsx_io.movements_template(db), media_type=XLSX,
                    headers={"Content-Disposition": 'attachment; filename="movements.xlsx"'})


@router.post("/import")
async def import_movements(file: UploadFile, db: Session = Depends(get_db)):
    loc = business_id(db)
    try:
        res = xlsx_io.import_movements(db, await file.read(), file.filename or "movements.xlsx", loc)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return res.summary()


@router.post("", status_code=201)
def create(body: MovementIn, db: Session = Depends(get_db)):
    loc = business_id(db)
    it = db.get(InventoryItem, body.item_id)
    if not it:
        raise HTTPException(400, "unknown item")
    try:
        m, outcome = inv.add_movement(db, location_id=loc, item=it, kind=body.kind, occurred_at=body.occurred_at,
                                      external_ref=body.external_ref, packs=body.packs, qty=body.qty,
                                      pack_cost=body.pack_cost, note=body.note, source="portal")
    except inv.InventoryError as e:
        raise HTTPException(400, str(e)) from None
    db.commit()
    db.refresh(m)
    return {"outcome": outcome, "movement": _out(m)}


@router.post("/batch")
def create_batch(body: DeliveryIn, db: Session = Depends(get_db)):
    """One sheet for many items: every line is saved, or none is. A delivery becomes a posted delivery
    (listed under Deliveries); saving the same reference on the same day again updates it. Other kinds are
    recorded as one stock movement per line."""
    loc = business_id(db)
    ids = [ln.item_id for ln in body.lines]
    if len(set(ids)) != len(ids):
        raise HTTPException(400, "an item appears twice on this delivery")
    items = {i: db.get(InventoryItem, i) for i in ids}
    if missing := [i for i, it in items.items() if it is None]:
        raise HTTPException(400, f"unknown item {missing[0]}")
    try:
        if body.kind == "delivery":
            d, outcome = dlv.record_sheet(db, location_id=loc, source="sheet", occurred_at=body.occurred_at,
                                          ref=body.external_ref, store=body.store, note=body.note,
                                          lines=[(items[ln.item_id], ln.packs, ln.pack_cost) for ln in body.lines])
            db.commit()
            return {"outcome": outcome, "delivery_id": d.id, "lines": len(body.lines)}
        outcomes = {"inserted": 0, "duplicate": 0, "superseded": 0}
        for ln in body.lines:
            _, outcome = inv.add_movement(db, location_id=loc, item=items[ln.item_id], kind=body.kind,
                                          occurred_at=body.occurred_at, external_ref=body.external_ref,
                                          packs=ln.packs, note=body.note, source="portal")
            outcomes[outcome] += 1
    except (inv.InventoryError, dlv.DeliveryError) as e:
        db.rollback()
        raise HTTPException(400, str(e)) from None
    db.commit()
    return outcomes


@router.post("/{movement_id}/retract", response_model=MovementOut)
def retract(movement_id: str, db: Session = Depends(get_db)):
    m = db.get(StockMovement, movement_id)
    if not m:
        raise HTTPException(404)
    if m.delivery_id and m.status == "active":
        raise HTTPException(400, "this line belongs to a delivery; reopen or void the delivery instead")
    m.status = "retracted"
    m.external_ref = f"{m.external_ref}#retracted-{m.id[:8]}"
    db.commit()
    db.refresh(m)
    return _out(m)
