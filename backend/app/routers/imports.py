from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Auth, perm
from ..db import get_db
from ..models import DataIssue, ImportBatch
from ..schemas import BatchOut, IssueOut
from ..seed import default_location
from ..services import legacy_import, settings_svc, swype_api, swype_item_sales, swype_items

router = APIRouter(prefix="/api/imports", tags=["imports"], dependencies=[Auth])
IMPORTS, SALES, SWYPE = perm("imports"), perm("sales"), perm("swype_items")


@router.get("", response_model=list[BatchOut], dependencies=[IMPORTS])
def list_batches(db: Session = Depends(get_db), limit: int = 100):
    return db.scalars(select(ImportBatch).order_by(ImportBatch.imported_at.desc()).limit(limit)).all()


@router.post("/legacy/preview", dependencies=[IMPORTS])
async def legacy_preview(file: UploadFile, db: Session = Depends(get_db)):
    data = await file.read()
    try:
        pv = legacy_import.preview(data, count_weekday=int(settings_svc.get(db, "count_weekday")))
    except Exception as e:  # noqa: BLE001 - surface parse problems to the user
        raise HTTPException(400, f"could not read workbook: {e}") from None
    return {
        "tabs": pv.tabs,
        "items": [{**i, "par_level": float(i["par_level"]) if i["par_level"] is not None else None,
                   "pack_size": float(i["pack_size"]), "case_size": float(i["case_size"]),
                   "last_on_hand": float(i["last_on_hand"]) if i["last_on_hand"] is not None else None} for i in pv.items],
        "count_sessions": [{"tab": s["tab"], "week": s["week"], "date": s["date"].isoformat(),
                            "date_fallback": s["date_fallback"], "lines": len(s["lines"])} for s in pv.count_sessions],
        "deliveries": len(pv.deliveries),
        "review": pv.review,
    }


@router.post("/legacy", dependencies=[IMPORTS])
async def legacy_apply(file: UploadFile, db: Session = Depends(get_db), include_counts: bool = True,
                       include_deliveries: bool = True):
    loc = default_location(db)
    data = await file.read()
    try:
        return legacy_import.apply(db, data, file.filename or "legacy.xlsx", loc.id,
                                   count_weekday=int(settings_svc.get(db, "count_weekday")),
                                   include_counts=include_counts, include_deliveries=include_deliveries)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        raise HTTPException(400, f"import failed, nothing written: {e}") from None


@router.post("/swype-item-sales/preview", dependencies=[SALES])
async def swype_item_sales_preview(file: UploadFile, db: Session = Depends(get_db)):
    try:
        return swype_item_sales.preview(await file.read(), db)
    except swype_item_sales.ReportError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/swype-item-sales", dependencies=[SALES])
async def swype_item_sales_apply(file: UploadFile, db: Session = Depends(get_db)):
    loc = default_location(db)
    r = swype_item_sales.apply(db, await file.read(), file.filename or "item_sales.xlsx", loc.id)
    if r["status"] != "ok":
        raise HTTPException(400, f"nothing imported: {r['error']}")
    return r


class SwypeFetchIn(BaseModel):
    date_from: date
    date_to: date


@router.get("/swype-item-sales/fetch", dependencies=[SALES])
def swype_item_sales_fetch_status(db: Session = Depends(get_db)):
    start, end = swype_item_sales.pending_range(db, default_location(db).id)
    last = settings_svc.get(db, "swype_auto_last")
    return {"configured": swype_api.configured(), "next_from": start, "through": end,
            "auto": {"enabled": settings_svc.get(db, "swype_auto_import") == "true",
                     "hour": int(settings_svc.get(db, "swype_auto_import_hour")),
                     "last": json.loads(last) if last else None}}


@router.post("/swype-item-sales/fetch", dependencies=[SALES])
def swype_item_sales_fetch(body: SwypeFetchIn, db: Session = Depends(get_db)):
    """Pull the Item Sales report for the dates from Swype and import it like an uploaded file. The page
    calls this one day at a time; a day with nothing sold (bar closed) comes back as status "no_sales"."""
    if body.date_to < body.date_from:
        raise HTTPException(400, "the 'to' date is before the 'from' date")
    if body.date_from > date.today():
        raise HTTPException(400, "the 'from' date is in the future")
    try:
        data = swype_api.fetch_item_sales(body.date_from, body.date_to)
    except swype_api.NoSales:
        return {"status": "no_sales", "period_from": body.date_from, "period_to": body.date_to}
    except swype_api.SwypeError as e:
        raise HTTPException(502, str(e)) from None
    r = swype_item_sales.apply(db, data, f"swype_item_sales_{body.date_from}_{body.date_to}.xlsx",
                               default_location(db).id, source="swype_api")
    if r["status"] != "ok":
        raise HTTPException(400, f"nothing imported: {r['error']}")
    return r


@router.get("/swype-item-sales/periods", dependencies=[SALES])
def swype_item_sales_periods(db: Session = Depends(get_db)):
    return swype_item_sales.periods(db, default_location(db).id)


@router.get("/swype-item-sales/periods/{period_from}/{period_to}/items", dependencies=[SALES])
def swype_item_sales_period_items(period_from: date, period_to: date, db: Session = Depends(get_db)):
    rows = swype_item_sales.period_items(db, default_location(db).id, period_from, period_to)
    if not rows:
        raise HTTPException(404, "no sales imported for that period")
    return rows


@router.delete("/swype-item-sales/periods", dependencies=[SALES])
def swype_item_sales_delete_range(date_from: date, date_to: date, db: Session = Depends(get_db)):
    """Remove the imported sales for every period wholly inside the dates (one day or many)."""
    if date_to < date_from:
        raise HTTPException(400, "the 'to' date is before the 'from' date")
    return swype_item_sales.delete_range(db, default_location(db).id, date_from, date_to)


@router.delete("/swype-item-sales/periods/{period_from}/{period_to}", dependencies=[SALES])
def swype_item_sales_delete(period_from: date, period_to: date, db: Session = Depends(get_db)):
    n = swype_item_sales.delete_period(db, default_location(db).id, period_from, period_to)
    if not n:
        raise HTTPException(404, "no sales imported for that period")
    return {"deleted": n}


@router.post("/swype-items/preview", dependencies=[SWYPE])
async def swype_items_preview(file: UploadFile, db: Session = Depends(get_db)):
    try:
        return swype_items.preview_csv(db, await file.read())
    except swype_items.CatalogueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/swype-items", dependencies=[SWYPE])
async def swype_items_apply(file: UploadFile, db: Session = Depends(get_db)):
    r = swype_items.apply_csv(db, await file.read(), file.filename or "export_items.csv", default_location(db).id)
    if r["status"] != "ok":
        raise HTTPException(400, f"nothing imported: {r['error']}")
    return r


@router.post("/swype-items/sync-from-sales", dependencies=[SWYPE])
def swype_items_sync_from_sales(db: Session = Depends(get_db)):
    return swype_items.sync_from_sales(db)


@router.get("/{batch_id}/items", dependencies=[IMPORTS])
def batch_items(batch_id: str, db: Session = Depends(get_db)):
    """The Swype items and quantities one Item Sales import brought in."""
    return swype_item_sales.batch_items(db, batch_id)


@router.delete("/{batch_id}", dependencies=[IMPORTS])
def remove_batch(batch_id: str, db: Session = Depends(get_db)):
    """Remove an Item Sales import together with the sales values it brought in."""
    n = swype_item_sales.remove_batch(db, batch_id)
    if n is None:
        raise HTTPException(400, "only an imported Item Sales file can be removed here")
    return {"deleted": n}


@router.get("/issues", response_model=list[IssueOut], dependencies=[IMPORTS])
def issues(db: Session = Depends(get_db), include_resolved: bool = False):
    stmt = select(DataIssue)
    if not include_resolved:
        stmt = stmt.where(DataIssue.resolved.is_(False))
    return db.scalars(stmt.order_by(DataIssue.created_at.desc())).all()


@router.post("/issues/{issue_id}/resolve", response_model=IssueOut, dependencies=[IMPORTS])
def resolve(issue_id: str, db: Session = Depends(get_db)):
    i = db.get(DataIssue, issue_id)
    if not i:
        raise HTTPException(404)
    i.resolved = True
    db.commit()
    return i
