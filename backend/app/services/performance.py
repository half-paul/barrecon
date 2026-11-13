"""Bar Performance: the Overview's owner stats.

- Net sales: Swype Item Sales net amounts for the last 7 days, against the 7 days before. Sales are period
  summaries, so a period belongs to the week its last day falls in (never counted in both weeks).
- Revenue by category: the same last-7-days periods, grouped by Swype category.
- Beverage cost: what the newest pair of posted counts says was used (opening + received - removed - closing,
  at today's unit cost) over the non-Food net sales of the periods inside that span. The pair before gives
  the comparison.
- Inventory variance: the newest pair's net variance cost (negative = stock short of what sales explain).
- Inventory at cost: the newest posted count at today's unit costs.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..models import CountSession, InventoryItem, SwypeItemSales
from . import inventory as inv
from . import variance


def _week_sales(rows: list[SwypeItemSales], d0: date, d1: date) -> list[SwypeItemSales]:
    return [r for r in rows if d0 <= r.period_to <= d1]


def _usage(db: Session, location_id: str, period: dict) -> dict | None:
    """Cost used and beverage sales over one pair of posted counts."""
    try:
        rep = variance.run(db, location_id, period["opening_id"], period["closing_id"])
    except variance.VarianceError:
        return None
    actual: dict[str, Decimal] = {}
    for r in rep["rows"]:
        if r["actual"] is not None:
            actual[r["item_id"]] = r["actual"]
    for p in rep["pools"]:
        for m in p["members"]:
            if m["actual"] is not None:
                actual[m["item_id"]] = m["actual"]
    costs = {it.id: it.unit_cost for it in db.scalars(select(InventoryItem).where(InventoryItem.id.in_(set(actual))))}
    used = sum((q * costs[i] for i, q in actual.items() if costs.get(i) is not None), Decimal(0))
    var = [x["variance_cost"] for x in (*rep["rows"], *rep["pools"]) if x["variance_cost"] is not None]
    d0 = variance.local_date(inv.to_utc(rep["opening"]["counted_at"]))
    d1 = variance.local_date(inv.to_utc(rep["closing"]["counted_at"]))
    sales = db.scalars(select(SwypeItemSales).where(SwypeItemSales.location_id == location_id,
                                                    SwypeItemSales.period_from >= d0,
                                                    SwypeItemSales.period_to <= d1,
                                                    SwypeItemSales.department != "Food")).all()
    bev = sum((s.net_amount for s in sales), Decimal(0))
    return {"from": rep["opening"]["counted_at"], "to": rep["closing"]["counted_at"], "used": used,
            "sales": bev, "pct": used / bev * 100 if bev else None,
            # variance is "actual - should"; flip it so a shortfall reads as a negative dollar amount
            "variance_cost": -sum(var, Decimal(0)) if var else None}


def _stock_value(db: Session, location_id: str) -> dict | None:
    now = datetime.now(timezone.utc)
    posted = [c for c in variance.posted_counts(db, location_id) if inv.to_utc(c.counted_at) <= now]
    if not posted:
        return None
    last = db.get(CountSession, posted[-1].id, options=[selectinload(CountSession.lines)])
    items = {it.id: it for it in db.scalars(select(InventoryItem).where(
        InventoryItem.id.in_([ln.item_id for ln in last.lines])))}
    value, uncosted = Decimal(0), 0
    for ln in last.lines:
        c = items[ln.item_id].unit_cost if ln.item_id in items else None
        if c is None:
            uncosted += 1 if ln.qty else 0
        else:
            value += ln.qty * c
    return {"value": value, "counted_at": last.counted_at, "uncosted": uncosted}


def performance(db: Session, location_id: str, today: date | None = None) -> dict:
    today = today or datetime.now(ZoneInfo(settings.timezone)).date()
    w0, w1 = today - timedelta(days=6), today
    p0, p1 = today - timedelta(days=13), today - timedelta(days=7)
    rows = db.scalars(select(SwypeItemSales).where(SwypeItemSales.location_id == location_id,
                                                   SwypeItemSales.period_to >= p0,
                                                   SwypeItemSales.period_to <= w1)).all()
    week, prior = _week_sales(rows, w0, w1), _week_sales(rows, p0, p1)
    by_cat: dict[str, Decimal] = defaultdict(Decimal)
    for r in week:
        by_cat[r.category] += r.net_amount
    ps = variance.periods(db, location_id)
    now_u = _usage(db, location_id, ps[0]) if ps else None
    prev_u = _usage(db, location_id, ps[1]) if len(ps) > 1 else None
    return {
        "week": {"from": w0, "to": w1},
        "net_sales": sum((r.net_amount for r in week), Decimal(0)) if week else None,
        "prior_net_sales": sum((r.net_amount for r in prior), Decimal(0)) if prior else None,
        "sales_periods": sorted({(r.period_from, r.period_to) for r in week}),
        "categories": [{"category": k, "net": v} for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1])],
        "beverage_cost": now_u,
        "prior_beverage_cost": prev_u,
        "stock": _stock_value(db, location_id),
    }
