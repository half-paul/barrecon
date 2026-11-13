from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..auth import Auth, perm
from ..db import get_db
from ..tenancy import business_id
from ..services import performance, variance

router = APIRouter(prefix="/api/variance", tags=["variance"], dependencies=[Auth])
VARIANCE = perm("variance")


@router.get("/periods", dependencies=[VARIANCE])
def periods(db: Session = Depends(get_db)):
    return variance.periods(db, business_id(db))


@router.get("/counts", dependencies=[VARIANCE])
def counts(limit: int = Query(10, ge=1, le=100), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    return variance.count_page(db, business_id(db), limit, offset)


@router.get("/current")  # the Overview and Sales "this week" checklist, shown to everyone
def current(db: Session = Depends(get_db)):
    return variance.current(db, business_id(db))


@router.get("/performance", dependencies=[VARIANCE])  # the Overview's Bar Performance stats
def bar_performance(db: Session = Depends(get_db)):
    return performance.performance(db, business_id(db))


@router.get("", dependencies=[VARIANCE])
def report(opening: str | None = None, closing: str | None = None, db: Session = Depends(get_db)):
    try:
        return variance.run(db, business_id(db), opening, closing)
    except variance.VarianceError as e:
        raise HTTPException(400, str(e)) from None
