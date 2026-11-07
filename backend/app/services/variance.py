"""Variance between two posted counts: what recipes say was poured vs what the counts say is gone.

    should have used = Σ (Swype qty sold × recipe portion)  +  breakage/expired recorded in the portal
    actually used    = opening count + deliveries + transfers in − transfers out − returns − closing count
    variance         = actually used − should have used        (+ = more gone than sales explain)
    variance %       = variance / should have used

Swype items are not inventory items: sales reach inventory only through recipes, including a bottle of
beer (Swype "Kokanee Btl" -> 1 each of the counted Kokanee). An item that no recipe uses gets status
no_recipe: its usage can't be checked against sales until a recipe takes it.

An item is flagged when |variance %| reaches the threshold setting (30 % by default), or when stock went
missing with nothing sold that uses it.

Sales come from Swype's weekly Item Sales totals, which carry dates but no times. A sales period counts
toward a count window when it sits inside it by date (counts are taken at close of day). Days of the
window with no sales period on file, and sales periods that straddle a count, are reported, because
either makes "should have used" too low.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..models import (
    EXPLAINED_KINDS,
    INBOUND_KINDS,
    OUTBOUND_KINDS,
    CountSession,
    InventoryItem,
    StockMovement,
    SwypeItemSales,
)
from ..units import ConversionError
from . import inventory as inv
from . import recipes as rec
from . import settings_svc


class VarianceError(ValueError):
    pass


def local_date(d) -> date:
    return inv.to_utc(d).astimezone(ZoneInfo(settings.timezone)).date()


def posted_counts(db: Session, location_id: str) -> list[CountSession]:
    return list(db.scalars(select(CountSession).where(CountSession.location_id == location_id,
                                                      CountSession.status == "posted")
                           .order_by(CountSession.counted_at)))


def periods(db: Session, location_id: str) -> list[dict]:
    """Consecutive pairs of posted counts, newest first."""
    cs = posted_counts(db, location_id)
    return [{"opening_id": a.id, "closing_id": b.id, "opening_at": a.counted_at, "closing_at": b.counted_at}
            for a, b in zip(cs, cs[1:])][::-1]


def _days(a: date, b: date):
    while a <= b:
        yield a
        a += timedelta(days=1)


def run(db: Session, location_id: str, opening_id: str | None = None, closing_id: str | None = None) -> dict:
    if opening_id is None or closing_id is None:
        ps = periods(db, location_id)
        if not ps:
            raise VarianceError("variance needs two posted counts; post another count first")
        opening_id, closing_id = ps[0]["opening_id"], ps[0]["closing_id"]
    opening = db.get(CountSession, opening_id, options=[selectinload(CountSession.lines)])
    closing = db.get(CountSession, closing_id, options=[selectinload(CountSession.lines)])
    for s, what in ((opening, "opening"), (closing, "closing")):
        if s is None or s.location_id != location_id:
            raise VarianceError(f"{what} count not found")
        if s.status != "posted":
            raise VarianceError(f"the {what} count is not posted")
    t0, t1 = inv.to_utc(opening.counted_at), inv.to_utc(closing.counted_at)
    if t1 <= t0:
        raise VarianceError("the closing count must be after the opening count")
    d0, d1 = local_date(t0), local_date(t1)
    oz_ml = settings_svc.oz_ml(db)
    threshold = Decimal(settings_svc.get(db, "variance_threshold_pct"))

    # ---- sales in the window ----
    sales = db.scalars(select(SwypeItemSales).where(SwypeItemSales.location_id == location_id,
                                                    SwypeItemSales.period_to >= d0,
                                                    SwypeItemSales.period_from <= d1)).all()
    inside = [s for s in sales if s.period_from >= d0 and s.period_to <= d1]
    straddling = sorted({(s.period_from, s.period_to) for s in sales if s not in inside})
    sales_periods = sorted({(s.period_from, s.period_to) for s in inside})
    covered = {d for pf, pt in sales_periods for d in _days(pf, pt)}
    missing_days = [d for d in _days(d0 + timedelta(days=1), d1) if d not in covered]

    recipes = rec.load_all(db)
    in_recipe = {ln.item_id for r in recipes.values() if not r.ignored for v in r.versions for ln in v.lines}
    expected: dict[str, Decimal] = defaultdict(Decimal)
    contributions: dict[str, dict[tuple, dict]] = defaultdict(dict)  # one row per menu item and portion across the window
    unit_issues: dict[str, set[str]] = defaultdict(set)
    unmapped: dict[str, dict] = {}
    for s in inside:
        qty = Decimal(s.total_qty)
        if qty == 0:
            continue
        r = recipes.get(s.item_code)
        if r is not None and r.ignored:
            continue
        v = rec.version_at(r, s.period_from) if r else None
        if v is None or not v.lines:
            u = unmapped.setdefault(s.item_code, {"code": s.item_code, "name": s.description,
                                                  "category": s.category, "sold": Decimal(0)})
            u["sold"] += qty
            continue
        for ln in v.lines:
            try:
                portion = rec.to_stock_unit(ln.qty, ln.unit, ln.item, oz_ml)
            except ConversionError:
                unit_issues[ln.item_id].add(f"{s.description}: {ln.qty} {ln.unit} can't be converted to "
                                            f"{ln.item.stock_unit}")
                continue
            expected[ln.item_id] += portion * qty
            c = contributions[ln.item_id].setdefault((s.item_code, portion), {
                "code": s.item_code, "name": s.description, "sold": Decimal(0), "portion_qty": ln.qty,
                "portion_unit": ln.unit, "portion": portion, "used": Decimal(0)})
            c["sold"] += qty
            c["used"] += portion * qty

    # ---- movements in the window ----
    mv: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    any_movement = False
    for m in db.scalars(select(StockMovement).where(StockMovement.location_id == location_id,
                                                    StockMovement.status == "active",
                                                    StockMovement.occurred_at > t0,
                                                    StockMovement.occurred_at <= t1)):
        any_movement = True
        mv[m.item_id][m.kind] += Decimal(m.qty)

    open_q = {ln.item_id: Decimal(ln.qty) for ln in opening.lines}
    close_q = {ln.item_id: Decimal(ln.qty) for ln in closing.lines}
    ids = set(expected) | set(unit_issues) | {i for i in set(open_q) | set(close_q)
                                              if open_q.get(i) != close_q.get(i) or i in mv}
    ids |= {i for i in mv if any(mv[i][k] for k in (*INBOUND_KINDS, *OUTBOUND_KINDS, *EXPLAINED_KINDS))}
    items = {it.id: it for it in db.scalars(select(InventoryItem).where(InventoryItem.id.in_(ids)))}

    rows = []
    for item_id in ids:
        it = items[item_id]
        m = mv.get(item_id, {})
        received = sum((m.get(k, Decimal(0)) for k in INBOUND_KINDS), Decimal(0))
        removed = sum((m.get(k, Decimal(0)) for k in OUTBOUND_KINDS), Decimal(0))
        explained = sum((m.get(k, Decimal(0)) for k in EXPLAINED_KINDS), Decimal(0))
        o, c = open_q.get(item_id), close_q.get(item_id)
        exp = expected.get(item_id, Decimal(0))
        actual = None if o is None or c is None else o + received - removed - c
        no_recipe = item_id not in in_recipe
        should = exp + explained
        variance = None if actual is None else actual - should
        pct = None if variance is None or should == 0 else variance / should * 100
        if actual is None:
            status = "missing_opening" if o is None else "missing_closing"
        elif unit_issues.get(item_id):
            status = "unit_issue"
        elif no_recipe:
            status = "no_recipe"
        elif pct is not None:
            status = "off" if abs(pct) >= threshold else "ok"
        else:
            status = "off" if actual > 0 else "ok"  # stock gone with nothing sold that uses it
        unit_cost = it.unit_cost
        rows.append({
            "item_id": it.id, "sku": it.sku, "name": it.name, "category": it.category,
            "stock_unit": it.stock_unit, "pack_name": it.pack_name, "pack_size": it.pack_size,
            "opening": o, "received": received, "removed": removed, "closing": c,
            "expected": exp, "explained": explained, "should_use": should, "actual": actual,
            "no_recipe": no_recipe, "variance": variance, "variance_pct": pct, "status": status,
            "variance_cost": None if variance is None or unit_cost is None else variance * unit_cost,
            "issues": sorted(unit_issues.get(item_id, ())),
            "contributions": sorted(contributions.get(item_id, {}).values(), key=lambda x: -x["used"]),
        })
    order = {"off": 0, "unit_issue": 1, "missing_opening": 2, "missing_closing": 2, "no_recipe": 3, "ok": 4}
    rows.sort(key=lambda r: (order[r["status"]], -abs(r["variance_pct"] or 0), r["name"]))

    unmapped_rows = sorted(unmapped.values(), key=lambda u: -u["sold"])
    return {
        "opening": {"id": opening.id, "counted_at": opening.counted_at},
        "closing": {"id": closing.id, "counted_at": closing.counted_at},
        "threshold_pct": threshold,
        "sales_periods": [{"from": a, "to": b} for a, b in sales_periods],
        "straddling_periods": [{"from": a, "to": b} for a, b in straddling],
        "missing_sales_days": missing_days,
        "no_movements_recorded": not any_movement,
        "unmapped": unmapped_rows,
        "unmapped_qty": sum((u["sold"] for u in unmapped_rows), Decimal(0)),
        "summary": {s: sum(1 for r in rows if r["status"] == s) for s in order},
        "rows": rows,
    }


def expected_left(db: Session, location_id: str, counted_at) -> dict[str, Decimal]:
    """What should be on the shelf at `counted_at`: the last posted count before it, plus deliveries since,
    minus what recipes say was poured from Swype sales since, per item in stock units, never below zero.
    Uses the same sales and movement windows as run(). Empty when there is no earlier posted count."""
    t1 = inv.to_utc(counted_at)
    prev = [c for c in posted_counts(db, location_id) if inv.to_utc(c.counted_at) < t1]
    if not prev:
        return {}
    opening = prev[-1]
    t0 = inv.to_utc(opening.counted_at)
    left: dict[str, Decimal] = defaultdict(Decimal, {ln.item_id: Decimal(ln.qty) for ln in opening.lines})
    for m in db.scalars(select(StockMovement).where(StockMovement.location_id == location_id,
                                                    StockMovement.status == "active",
                                                    StockMovement.kind == "delivery",
                                                    StockMovement.occurred_at > t0,
                                                    StockMovement.occurred_at <= t1)):
        left[m.item_id] += Decimal(m.qty)
    d0, d1 = local_date(t0), local_date(t1)
    oz_ml = settings_svc.oz_ml(db)
    recipes = rec.load_all(db)
    for s in db.scalars(select(SwypeItemSales).where(SwypeItemSales.location_id == location_id,
                                                     SwypeItemSales.period_from >= d0,
                                                     SwypeItemSales.period_to <= d1)):
        qty = Decimal(s.total_qty)
        r = recipes.get(s.item_code)
        if qty == 0 or r is None or r.ignored:
            continue
        v = rec.version_at(r, s.period_from)
        for ln in (v.lines if v else ()):
            try:
                left[ln.item_id] -= rec.to_stock_unit(ln.qty, ln.unit, ln.item, oz_ml) * qty
            except ConversionError:
                continue
    return {k: max(q, Decimal(0)) for k, q in left.items()}


def current(db: Session, location_id: str, today: date | None = None) -> dict:
    """Where the period since the last posted count stands, for the weekly checklist: the opening count, an
    open draft, days with no Swype sales on file (up to yesterday), menu items sold with no recipe, and
    deliveries recorded. `last_variance` is the newest pair of posted counts, if any."""
    from datetime import datetime, timezone
    today = today or datetime.now(ZoneInfo(settings.timezone)).date()
    now = datetime.now(timezone.utc)
    posted = [c for c in posted_counts(db, location_id) if inv.to_utc(c.counted_at) <= now]
    draft = db.scalar(select(CountSession).where(CountSession.location_id == location_id,
                                                 CountSession.status == "draft")
                      .order_by(CountSession.created_at.desc()))
    ps = periods(db, location_id)
    out: dict = {"opening": None, "draft": {"id": draft.id, "counted_at": draft.counted_at} if draft else None,
                 "days": 0, "missing_sales_days": [], "unmapped": 0, "deliveries": 0,
                 "last_variance": {k: ps[0][k] for k in ("opening_at", "closing_at")} if ps else None}
    if not posted:
        return out
    opening = posted[-1]
    out["opening"] = {"id": opening.id, "counted_at": opening.counted_at}
    start, end = local_date(opening.counted_at) + timedelta(days=1), today - timedelta(days=1)
    out["days"] = max(0, (end - start).days + 1)
    sales = db.scalars(select(SwypeItemSales).where(SwypeItemSales.location_id == location_id,
                                                    SwypeItemSales.period_to >= start,
                                                    SwypeItemSales.period_from <= end)).all()
    covered = {d for s in sales for d in _days(s.period_from, s.period_to)}
    out["missing_sales_days"] = [d for d in _days(start, end) if d not in covered]
    recipes = rec.load_all(db)
    unmapped = set()
    for s in sales:
        if Decimal(s.total_qty) == 0:
            continue
        r = recipes.get(s.item_code)
        if r is not None and r.ignored:
            continue
        v = rec.version_at(r, s.period_from) if r else None
        if v is None or not v.lines:
            unmapped.add(s.item_code)
    out["unmapped"] = len(unmapped)
    out["deliveries"] = len(set(db.scalars(select(StockMovement.external_ref).where(
        StockMovement.location_id == location_id, StockMovement.status == "active",
        StockMovement.kind == "delivery", StockMovement.occurred_at > inv.to_utc(opening.counted_at)))))
    return out
