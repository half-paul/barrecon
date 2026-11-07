from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..auth import perm
from ..db import get_db
from ..models import Delivery, DeliveryReceipt, StockMovement
from ..schemas import ReceiptDeliveryIn
from ..seed import default_location
from ..services import deliveries as dlv
from ..services import inventory as inv
from ..services import receipts

router = APIRouter(prefix="/api/deliveries", tags=["deliveries"], dependencies=[perm("deliveries")])


def _line(ln) -> dict:
    it = ln.item
    return {"position": ln.position, "text": ln.text, "receipt_sku": ln.receipt_sku, "receipt_qty": ln.receipt_qty, "line_total": ln.line_total,
            "item_id": ln.item_id, "packs": ln.packs, "skip": ln.skip, "matched_by": ln.matched_by,
            "item": None if it is None else {"id": it.id, "sku": it.sku, "name": it.name, "pack_name": it.pack_name,
                                             "pack_size": it.pack_size, "case_size": it.case_size,
                                             "stock_unit": it.stock_unit}}


def _utc(t):
    return inv.to_utc(t) if t else None  # SQLite hands datetimes back without their zone


def _summary(db: Session, d: Delivery, c: dict | None = None) -> dict:
    c = c or dlv.checks(db, d)
    stock = [ln for ln in d.lines if not ln.skip and ln.item_id]
    return {"id": d.id, "store": d.store, "occurred_at": _utc(d.occurred_at), "receipt_no": d.receipt_no,
            "total": d.total, "status": d.status, "source": d.source, "has_receipt": d.receipt is not None,
            "line_count": len(d.lines), "item_count": len({ln.item_id for ln in stock}),
            "spend": sum((ln.line_total for ln in stock if ln.line_total is not None), 0),
            "totals_ok": c["totals_ok"], "price_alerts": c["price_alerts"],
            "to_match": len(c["unmatched"]) + len(c["no_packs"]), "created_at": _utc(d.created_at)}


def _detail(db: Session, d: Delivery) -> dict:
    c = dlv.checks(db, d)
    return {**_summary(db, d, c), "store_address": d.store_address, "subtotal": d.subtotal, "tax": d.tax,
            "note": d.note, "read_error": d.read_error, "posted_at": _utc(d.posted_at),
            "receipt_type": d.receipt.content_type if d.receipt else None,
            "can_read": receipts.enabled() and d.receipt is not None,
            "lines": [_line(ln) for ln in d.lines], "checks": c}


def _get(db: Session, delivery_id: str) -> Delivery:
    d = db.get(Delivery, delivery_id)
    if d is None:
        raise HTTPException(404)
    return d


@router.get("")
def list_deliveries(db: Session = Depends(get_db), include_void: bool = False, limit: int = 200):
    stmt = select(Delivery)
    if not include_void:
        stmt = stmt.where(Delivery.status != "void")
    rows = db.scalars(stmt.order_by(Delivery.occurred_at.desc().nulls_first(), Delivery.created_at.desc())
                      .limit(limit)).all()
    return [_summary(db, d) for d in rows]


@router.get("/stores")
def stores(db: Session = Depends(get_db)):
    """Store names used before, for the store field's suggestions."""
    return sorted({s for s in db.scalars(select(Delivery.store).where(Delivery.store.is_not(None)))})


@router.get("/reading")
def reading():
    return {"enabled": receipts.enabled()}


def _read_into(db: Session, d: Delivery, data: bytes, content_type: str) -> None:
    try:
        dlv.apply_read(db, d, receipts.read(data, content_type))
    except receipts.ReceiptReadError as e:
        d.read_error = str(e)


@router.post("/receipt", status_code=201)
async def upload_receipt(file: UploadFile, db: Session = Depends(get_db)):
    """Start a delivery from a receipt: it is stored, read by Claude, and comes back as a draft to review."""
    data, ctype = await file.read(), file.content_type or ""
    try:
        receipts.check_file(data, ctype)
    except receipts.ReceiptReadError as e:
        raise HTTPException(400, str(e)) from None

    def work():
        d = Delivery(location_id=default_location(db).id, status="draft", source="receipt")
        d.receipt = DeliveryReceipt(content_type=ctype, file_name=file.filename, data=data)
        db.add(d)
        db.flush()
        _read_into(db, d, data, ctype)
        db.commit()
        db.refresh(d)
        return _detail(db, d)
    return await run_in_threadpool(work)


@router.post("", status_code=201)
def create_manual(db: Session = Depends(get_db)):
    """A delivery with no receipt (or the receipt added later), entered by hand."""
    d = Delivery(location_id=default_location(db).id, status="draft", source="manual")
    db.add(d)
    db.commit()
    db.refresh(d)
    return _detail(db, d)


@router.get("/{delivery_id}")
def get_delivery(delivery_id: str, db: Session = Depends(get_db)):
    return _detail(db, _get(db, delivery_id))


@router.get("/{delivery_id}/receipt")
def get_receipt(delivery_id: str, db: Session = Depends(get_db)):
    d = _get(db, delivery_id)
    if d.receipt is None:
        raise HTTPException(404)
    return Response(d.receipt.data, media_type=d.receipt.content_type, headers={"Cache-Control": "private, max-age=86400"})


@router.put("/{delivery_id}/receipt")
async def put_receipt(delivery_id: str, file: UploadFile, db: Session = Depends(get_db)):
    """Attach or replace the receipt file. It isn't read until asked (POST /read), so typed lines stay."""
    d = _get(db, delivery_id)
    if d.status != "draft":
        raise HTTPException(400, "reopen the delivery to change its receipt")
    data, ctype = await file.read(), file.content_type or ""
    try:
        receipts.check_file(data, ctype)
    except receipts.ReceiptReadError as e:
        raise HTTPException(400, str(e)) from None
    if d.receipt is None:
        d.receipt = DeliveryReceipt(content_type=ctype, file_name=file.filename, data=data)
    else:
        d.receipt.content_type, d.receipt.file_name, d.receipt.data = ctype, file.filename, data
    db.commit()
    db.refresh(d)
    return _detail(db, d)


@router.post("/{delivery_id}/read")
def reread(delivery_id: str, db: Session = Depends(get_db)):
    """Read the receipt again, replacing the header and every line."""
    d = _get(db, delivery_id)
    if d.status != "draft":
        raise HTTPException(400, "reopen the delivery to read it again")
    if d.receipt is None:
        raise HTTPException(400, "this delivery has no receipt")
    _read_into(db, d, d.receipt.data, d.receipt.content_type)
    db.commit()
    db.refresh(d)
    return _detail(db, d)


@router.put("/{delivery_id}")
def save(delivery_id: str, body: ReceiptDeliveryIn, db: Session = Depends(get_db)):
    d = _get(db, delivery_id)
    try:
        dlv.update(db, d, body)
    except dlv.DeliveryError as e:
        db.rollback()
        raise HTTPException(400, str(e)) from None
    db.commit()
    db.refresh(d)
    return _detail(db, d)


def _act(db: Session, d: Delivery, fn) -> dict:
    try:
        fn(db, d)
    except (dlv.DeliveryError, ValueError) as e:
        db.rollback()
        raise HTTPException(400, str(e)) from None
    db.commit()
    db.refresh(d)
    return _detail(db, d)


@router.post("/{delivery_id}/post")
def post(delivery_id: str, db: Session = Depends(get_db)):
    return _act(db, _get(db, delivery_id), dlv.post)


@router.post("/{delivery_id}/reopen")
def reopen(delivery_id: str, db: Session = Depends(get_db)):
    return _act(db, _get(db, delivery_id), dlv.reopen)


@router.post("/{delivery_id}/void")
def void(delivery_id: str, db: Session = Depends(get_db)):
    return _act(db, _get(db, delivery_id), dlv.void)


@router.delete("/{delivery_id}", status_code=204)
def delete_draft(delivery_id: str, db: Session = Depends(get_db)):
    d = _get(db, delivery_id)
    if d.status != "draft" or d.posted_at is not None:
        raise HTTPException(400, "only a draft can be deleted; void a posted delivery instead")
    if db.scalar(select(Delivery.id).where(Delivery.id == d.id).where(
            Delivery.id.in_(select(StockMovement.delivery_id)))):
        raise HTTPException(400, "this delivery was posted before; void it instead so its history stays")
    db.delete(d)
    db.commit()
