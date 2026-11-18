from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..auth import perm
from ..db import get_db
from ..tenancy import business_id
from ..services import reports

router = APIRouter(prefix="/api/reports", tags=["reports"], dependencies=[perm("reports")])


@router.get("/pour-cost")
def pour_cost(days: int = Query(28, ge=1, le=366), target: Decimal = Query(Decimal(25), ge=0, le=100),
              rise: Decimal = Query(Decimal(5), ge=0, le=1000), compare: int = Query(90, ge=1, le=730),
              db: Session = Depends(get_db)):
    return reports.pour_cost(db, business_id(db), days=days, target_pct=target, rise_pct=rise, compare_days=compare)


@router.get("/reorder")
def reorder(days: int = Query(28, ge=1, le=366), lead: int = Query(3, ge=0, le=90), cover: int = Query(7, ge=1, le=180),
            demand: Decimal = Query(Decimal(0), ge=-90, le=500), db: Session = Depends(get_db)):
    return reports.reorder(db, business_id(db), days=days, lead_days=lead, cover_days=cover, demand_pct=demand)


@router.get("/slow-moving")
def slow_moving(days: int = Query(90, ge=7, le=730), slow: int = Query(90, ge=7, le=730),
                perish: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    return reports.slow_moving(db, business_id(db), days=days, slow_days=slow, perish_days=perish)


@router.get("/item-sales")
def item_sales(date_from: date | None = Query(None, alias="from"), date_to: date | None = Query(None, alias="to"),
               db: Session = Depends(get_db)):
    return reports.item_sales(db, business_id(db), date_from, date_to)
