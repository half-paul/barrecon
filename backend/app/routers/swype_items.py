from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import perm
from ..db import get_db
from ..models import SwypeMenuItem
from ..tenancy import business_id
from ..services import recipes as rec
from ..services import swype_items as si

router = APIRouter(prefix="/api/swype-items", tags=["swype-items"], dependencies=[perm("swype_items")])


class SwypeItemPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)  # blank = back to Swype's name
    is_active: bool | None = None


def _sold(db: Session, date_from: date | None = None, date_to: date | None = None) -> dict[str, dict]:
    return {r["code"]: r for r in rec.sold_codes(db, business_id(db), date_from, date_to)}


@router.get("")
def list_swype_items(date_from: date | None = Query(default=None, alias="from"),
                     date_to: date | None = Query(default=None, alias="to"), db: Session = Depends(get_db)):
    """sold counts the imported sales periods overlapping from..to (both optional, inclusive)."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "'from' is after 'to'")
    sold = _sold(db, date_from, date_to)
    items = db.scalars(select(SwypeMenuItem).order_by(SwypeMenuItem.category, SwypeMenuItem.name)).all()
    return [si.item_out(m, sold.get(m.code)) for m in items]


@router.patch("/{code}")
def patch_swype_item(code: str, body: SwypeItemPatch, db: Session = Depends(get_db)):
    try:
        m = si.update(db, code, **body.model_dump(exclude_unset=True))
    except si.MenuItemError as e:
        raise HTTPException(404, str(e)) from None
    db.commit()
    return si.item_out(m, _sold(db).get(code))
