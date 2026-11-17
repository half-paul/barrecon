"""Owner reports: pour cost and profitability, reorder suggestions, slow-moving and dead stock.

All three read the same inputs: Swype Item Sales (period summaries), recipes (what each Swype item # takes
out of stock), item costs and their history, posted counts and stock movements.

- A sales period belongs to a window when its last day falls inside it, so no report is split or counted
  twice. Daily usage divides what those periods' sales used by the days they cover.
- Usage of a line that can pour from several items ("6 oz of Box A or Box B") is split evenly between them.
  For reordering, items that stand in for each other in any recipe are one group (variance.pools_of), so
  their stock and demand are added up.
- Stock on hand is the estimate the count sheet prefills (variance.expected_left): last posted count plus
  deliveries, minus recorded removals and what recipes say was poured since.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Delivery, InventoryItem, InventoryItemCost, StockMovement, SwypeItemSales, SwypeMenuItem
from ..units import ConversionError, convert
from . import inventory as inv
from . import recipes as rec
from . import settings_svc
from . import swype_items
from . import variance

ZERO = Decimal(0)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
PERISHABLE = re.compile(r"beer|keg|draft|draught|cider|seltzer|wine|prosecco|champagne|sake|vermouth|juice|"
                        r"mixer|soda|tonic|garnish|fruit|lime|lemon|orange|mint|herb|cream|milk|dairy|egg|syrup|puree",
                        re.I)


def _today() -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


def _days(a: date, b: date) -> set[date]:
    return {a + timedelta(days=i) for i in range((b - a).days + 1)}


def _sales(db: Session, location_id: str, d0: date, d1: date) -> tuple[list[SwypeItemSales], int]:
    """Sales periods ending in d0..d1 and how many days they cover between them."""
    rows = list(db.scalars(select(SwypeItemSales).where(SwypeItemSales.location_id == location_id,
                                                        SwypeItemSales.period_to >= d0,
                                                        SwypeItemSales.period_to <= d1)))
    covered: set[date] = set()
    for pf, pt in {(r.period_from, r.period_to) for r in rows}:
        covered |= _days(pf, pt)
    return rows, len(covered)


def _portion(ln, it: InventoryItem, oz_ml: Decimal) -> Decimal | None:
    try:
        return rec.to_stock_unit(ln.qty, ln.unit, it, oz_ml)
    except ConversionError:
        return None


def _usage(rows: list[SwypeItemSales], recipes: dict, oz_ml: Decimal) -> tuple[dict[str, Decimal], dict[str, set[str]]]:
    """Item id -> stock units the sales used, and item id -> Swype codes that used it."""
    used: dict[str, Decimal] = defaultdict(Decimal)
    codes: dict[str, set[str]] = defaultdict(set)
    for s in rows:
        r = recipes.get(s.item_code)
        if r is None or r.ignored or not s.total_qty:
            continue
        v = rec.version_at(r, s.period_from)
        for ln in (v.lines if v else ()):
            items = ln.items
            for it in items:
                q = _portion(ln, it, oz_ml)
                if q is not None:
                    used[it.id] += q * Decimal(s.total_qty) / len(items)
                    codes[it.id].add(s.item_code)
    return used, codes


def _on_hand(db: Session, location_id: str) -> dict[str, Decimal] | None:
    """Estimated stock now per item (stock units), for items on the last posted count or delivered since; None
    when nothing has been counted yet. An item missing here is not known to be out, only not counted."""
    now = datetime.now(timezone.utc)
    posted = [c for c in variance.posted_counts(db, location_id) if inv.to_utc(c.counted_at) <= now]
    if not posted:
        return None
    last = posted[-1]
    known = {ln.item_id for ln in last.lines}
    known |= set(db.scalars(select(StockMovement.item_id).where(
        StockMovement.location_id == location_id, StockMovement.status == "active",
        StockMovement.kind == "delivery", StockMovement.occurred_at > inv.to_utc(last.counted_at))))
    left = variance.expected_left(db, location_id, now + timedelta(seconds=1))
    return {i: left.get(i, ZERO) for i in known}


def _items(db: Session) -> dict[str, InventoryItem]:
    return {it.id: it for it in db.scalars(select(InventoryItem))}


def _drink_names(db: Session, recipes: dict, rows: list[SwypeItemSales]):
    """Swype code -> the name to show: a custom name, else the recipe's, else the one on the sales report."""
    renamed = swype_items.custom_names(db)
    sold = {s.item_code: s.description for s in rows}
    return lambda c: renamed.get(c) or (recipes[c].name if c in recipes else None) or sold.get(c, c)


# ---------- 1. pour cost and profitability ----------

def _cost_at(history: dict[str, list[tuple[datetime, Decimal]]], it: InventoryItem, when: datetime | None) -> Decimal | None:
    """Unit cost (per stock unit) of an item at `when` (None: today's cost) from its pack cost history."""
    pack = it.pack_cost
    if when is not None:
        pack = None
        for at, cost in history.get(it.id, ()):
            if at <= when:
                pack = cost
    if pack is None or not it.pack_size:
        return None
    return Decimal(pack) / Decimal(it.pack_size)


def _serve_cost(version, history, oz_ml: Decimal, when: datetime | None) -> tuple[Decimal | None, list[str]]:
    """Ingredient cost of one serve; None when a portion has no cost. A multi-item line costs their average."""
    total, missing = ZERO, []
    for ln in version.lines:
        costs = []
        for it in ln.items:
            q, c = _portion(ln, it, oz_ml), _cost_at(history, it, when)
            if q is not None and c is not None:
                costs.append(q * c)
        if not costs:
            missing.append(" / ".join(it.name for it in ln.items))
            continue
        total += sum(costs, ZERO) / len(costs)
    return (None if missing else total), missing


def pour_cost(db: Session, location_id: str, *, days: int = 28, target_pct: Decimal = Decimal(25),
              rise_pct: Decimal = Decimal(5), compare_days: int = 90, today: date | None = None) -> dict:
    today = today or _today()
    d0 = today - timedelta(days=days - 1)
    rows, covered = _sales(db, location_id, d0, today)
    oz_ml = settings_svc.oz_ml(db)
    recipes = rec.load_all(db)
    history: dict[str, list[tuple[datetime, Decimal]]] = defaultdict(list)
    for c in db.scalars(select(InventoryItemCost).order_by(InventoryItemCost.effective_from)):
        history[c.item_id].append((inv.to_utc(c.effective_from), Decimal(c.pack_cost)))
    then = datetime.combine(today - timedelta(days=compare_days), datetime.max.time(), tzinfo=timezone.utc)
    menu = {m.code: m for m in db.scalars(select(SwypeMenuItem))}

    by_code: dict[str, dict] = {}
    for s in rows:
        o = by_code.setdefault(s.item_code, {"code": s.item_code, "name": s.description, "category": s.category,
                                             "department": s.department, "sold": ZERO, "revenue": ZERO})
        o["sold"] += Decimal(s.total_qty)
        o["revenue"] += Decimal(s.net_amount)
    renamed = swype_items.custom_names(db)

    out, no_recipe = [], []
    for code, o in by_code.items():
        o["name"] = renamed.get(code) or o["name"]
        r = recipes.get(code)
        v = rec.current_version(r) if r and not r.ignored else None
        if v is None or not v.lines:
            if o["revenue"] and o["department"] != "Food" and not (r and r.ignored):
                no_recipe.append({k: o[k] for k in ("code", "name", "category", "sold", "revenue")})
            continue
        cost, missing = _serve_cost(v, history, oz_ml, None)
        before, _ = _serve_cost(v, history, oz_ml, then)
        price = o["revenue"] / o["sold"] if o["sold"] else None
        m = menu.get(code)
        menu_price = m.price if m and m.price is not None else (m.catalogue_price if m else None)
        pct = cost / price * 100 if cost is not None and price else None
        change = (cost / before - 1) * 100 if cost is not None and before else None
        flags = []
        if pct is not None and pct >= target_pct:
            flags.append("high_cost")
        if change is not None and change >= rise_pct:
            flags.append("cost_rising")
        if price is not None and menu_price and price < Decimal(menu_price) * Decimal("0.9"):
            flags.append("under_menu_price")
        if price is not None and cost is not None and price <= cost:
            flags.append("loss")
        if missing:
            flags.append("missing_cost")
        out.append({**o, "price": price, "menu_price": menu_price, "serve_cost": cost, "serve_cost_before": before,
                    "cost_change_pct": change, "pour_cost_pct": pct,
                    "margin": None if price is None or cost is None else price - cost,
                    "theoretical_cost": None if cost is None else cost * o["sold"],
                    "gross_profit": None if cost is None else o["revenue"] - cost * o["sold"],
                    "missing_cost": missing, "flags": flags})
    out.sort(key=lambda x: (-(x["pour_cost_pct"] or Decimal(-1)), x["name"]))

    # ingredients whose pack cost rose, and the drinks they go into
    rising = []
    using = rec.codes_using(db, list(history))
    for item_id, h in history.items():
        it = db.get(InventoryItem, item_id)
        now_c, then_c = _cost_at(history, it, None), _cost_at(history, it, then)
        if it is None or not now_c or not then_c:
            continue
        ch = (now_c / then_c - 1) * 100
        if ch >= rise_pct:
            rising.append({"item_id": it.id, "name": it.name, "pack_name": it.pack_name, "pack_cost": it.pack_cost,
                           "pack_cost_before": then_c * Decimal(it.pack_size), "change_pct": ch,
                           "drinks": [n for _, n in using.get(it.id, [])]})
    rising.sort(key=lambda x: -x["change_pct"])

    costed = [x for x in out if x["theoretical_cost"] is not None]
    rev = sum((x["revenue"] for x in costed), ZERO)
    cost_total = sum((x["theoretical_cost"] for x in costed), ZERO)
    return {"from": d0, "to": today, "days_covered": covered, "target_pct": target_pct, "rise_pct": rise_pct,
            "compare_from": then.date(),
            "summary": {"revenue": rev, "theoretical_cost": cost_total, "gross_profit": rev - cost_total,
                        "pour_cost_pct": cost_total / rev * 100 if rev else None,
                        "drinks": len(out), "flagged": sum(1 for x in out if x["flags"]),
                        "uncosted_revenue": sum((x["revenue"] for x in out if x["theoretical_cost"] is None), ZERO),
                        "no_recipe_revenue": sum((x["revenue"] for x in no_recipe), ZERO)},
            "rows": out, "rising": rising, "no_recipe": sorted(no_recipe, key=lambda x: -x["revenue"])}


# ---------- 2. purchasing and reorder ----------

def _suppliers(db: Session, location_id: str) -> dict[str, dict]:
    """Item id -> where and at what pack cost it was last delivered."""
    out: dict[str, dict] = {}
    q = (select(StockMovement, Delivery.store).outerjoin(Delivery, Delivery.id == StockMovement.delivery_id)
         .where(StockMovement.location_id == location_id, StockMovement.kind == "delivery",
                StockMovement.status == "active").order_by(StockMovement.occurred_at))
    for m, store in db.execute(q):
        out[m.item_id] = {"store": store, "at": m.occurred_at, "pack_cost": m.pack_cost}
    return out


def reorder(db: Session, location_id: str, *, days: int = 28, lead_days: int = 3, cover_days: int = 7,
            demand_pct: Decimal = ZERO, today: date | None = None) -> dict:
    today = today or _today()
    d0 = today - timedelta(days=days - 1)
    rows, covered = _sales(db, location_id, d0, today)
    oz_ml = settings_svc.oz_ml(db)
    recipes = rec.load_all(db)
    used, codes = _usage(rows, recipes, oz_ml)
    on_hand = _on_hand(db, location_id)
    items = _items(db)
    sup = _suppliers(db, location_id)
    drink = _drink_names(db, recipes, rows)
    pool_of = variance.pools_of(recipes, today - timedelta(days=days), today)
    factor = 1 + demand_pct / 100
    horizon = lead_days + cover_days

    groups: dict[str, list[InventoryItem]] = defaultdict(list)
    for it in items.values():
        if it.is_active and (it.id in used or it.par_level):
            groups[pool_of.get(it.id, it.id)].append(it)

    out = []
    for key, members in groups.items():
        members.sort(key=lambda it: it.name.lower())
        members.sort(key=lambda it: inv.to_utc(sup[it.id]["at"]) if it.id in sup else EPOCH, reverse=True)
        buy = members[0]  # the one delivered most recently; the others stand in for it
        size = Decimal(buy.pack_size or 1)
        try:  # members can be counted in different units: work in the buying item's unit
            conv = {it.id: (Decimal(1) if it.stock_unit == buy.stock_unit else
                            convert(Decimal(1), it.stock_unit, buy.stock_unit, oz_ml=oz_ml))
                    for it in members}
        except ConversionError:
            conv = {it.id: Decimal(1) for it in members}
        use = sum((used.get(it.id, ZERO) * conv[it.id] for it in members), ZERO)
        daily = use / covered * factor if covered else ZERO
        known = [it for it in members if on_hand is not None and it.id in on_hand]
        have = sum((on_hand[it.id] * conv[it.id] for it in known), ZERO) if known else None
        par = sum((Decimal(it.par_level) * Decimal(it.pack_size or 1) * conv[it.id] for it in members if it.par_level), ZERO)
        need = max(daily * horizon, par)
        short = need - (have or ZERO)
        packs = max(math.ceil(short / size), 0) if size and short > 0 else 0
        case = Decimal(buy.case_size or 1)
        days_left = have / daily if have is not None and daily else None
        status = ("no_stock_data" if have is None else
                  "stockout" if have <= 0 and (daily or par) else
                  "before_delivery" if days_left is not None and days_left < lead_days else
                  "reorder" if packs else
                  "excess" if daily and have > daily * horizon * 3 else "ok")
        drinks = sorted({drink(c) for it in members for c in codes.get(it.id, ())})
        s = sup.get(buy.id, {})
        pack_cost = s.get("pack_cost") or buy.pack_cost
        out.append({"key": key, "item_id": buy.id, "sku": buy.sku, "name": buy.name, "category": buy.category,
                    "stock_unit": buy.stock_unit, "pack_name": buy.pack_name, "pack_size": buy.pack_size,
                    "case_size": buy.case_size, "substitutes": [{"item_id": it.id, "name": it.name} for it in members[1:]],
                    "used": use, "daily": daily, "on_hand": have, "on_hand_packs": None if have is None else have / size,
                    "par_packs": par / size if par else None, "need": need, "days_left": days_left,
                    "packs": packs, "cases": math.ceil(packs / case) if packs and case > 1 else None,
                    "pack_cost": pack_cost, "line_cost": None if pack_cost is None else Decimal(pack_cost) * packs,
                    "supplier": s.get("store"), "last_delivered": s.get("at"), "drinks": drinks, "status": status})
    order = {"stockout": 0, "before_delivery": 1, "reorder": 2, "no_stock_data": 3, "excess": 4, "ok": 5}
    out.sort(key=lambda x: (order[x["status"]], x["category"] or "~", x["name"]))
    return {"from": d0, "to": today, "days_covered": covered, "lead_days": lead_days, "cover_days": cover_days,
            "demand_pct": demand_pct, "counted": on_hand is not None,
            "summary": {"lines": sum(1 for x in out if x["packs"]),
                        "cost": sum((x["line_cost"] or ZERO for x in out if x["packs"]), ZERO),
                        "at_risk": sum(1 for x in out if x["status"] in ("stockout", "before_delivery")),
                        "excess": sum(1 for x in out if x["status"] == "excess")},
            "rows": out}


# ---------- 3. slow-moving and dead stock ----------

def slow_moving(db: Session, location_id: str, *, days: int = 90, slow_days: int = 90, perish_days: int = 30,
                today: date | None = None) -> dict:
    today = today or _today()
    d0 = today - timedelta(days=days - 1)
    rows, covered = _sales(db, location_id, d0, today)
    oz_ml = settings_svc.oz_ml(db)
    recipes = rec.load_all(db)
    used, codes = _usage(rows, recipes, oz_ml)
    on_hand = _on_hand(db, location_id)
    items = _items(db)
    in_recipe = {i for r in recipes.values() if not r.ignored and (v := rec.current_version(r)) for ln in v.lines
                 for i in ln.item_ids}
    sup = _suppliers(db, location_id)

    # the last day anything that pours an item sold, over all sales on file
    last_sold: dict[str, date] = {}
    code_items: dict[str, set[str]] = defaultdict(set)
    for code, r in recipes.items():
        if not r.ignored:
            for v in r.versions:
                for ln in v.lines:
                    code_items[code] |= set(ln.item_ids)
    for code, pt in db.execute(select(SwypeItemSales.item_code, SwypeItemSales.period_to)
                               .where(SwypeItemSales.location_id == location_id, SwypeItemSales.total_qty > 0)):
        for i in code_items.get(code, ()):
            if i not in last_sold or pt > last_sold[i]:
                last_sold[i] = pt
    drink = _drink_names(db, recipes, rows)

    out = []
    for item_id, have in (on_hand or {}).items():
        it = items.get(item_id)
        if it is None or have <= 0:
            continue
        unit_cost = it.unit_cost
        value = None if unit_cost is None else have * unit_cost
        daily = used.get(item_id, ZERO) / covered if covered else ZERO
        cover = have / daily if daily else None
        last = last_sold.get(item_id)
        perishable = bool(PERISHABLE.search(f"{it.category or ''} {it.name}"))
        if item_id not in in_recipe:
            status, action = "no_recipe", "No recipe pours it, so sales can't use it up: add it to a recipe or stop buying it"
        elif not daily:
            status, action = "dead", "Not sold in this window: stop reordering, then promote it or take it off the menu"
        elif cover is not None and cover > slow_days:
            status, action = "slow", "Stop reordering until it runs down; promote the drinks that use it"
        else:
            status, action = "ok", ""
        expiry = perishable and (cover is None or cover > perish_days)
        if expiry and status == "ok":
            status, action = "expiry", f"Perishable with more than {perish_days} days on hand: use it up or promote it"
        if status == "ok":
            continue
        drinks = sorted({drink(c) for c in codes.get(item_id, ())})
        out.append({"item_id": it.id, "sku": it.sku, "name": it.name, "category": it.category,
                    "stock_unit": it.stock_unit, "pack_name": it.pack_name, "pack_size": it.pack_size,
                    "on_hand": have, "on_hand_packs": have / Decimal(it.pack_size) if it.pack_size else None,
                    "value": value, "daily": daily, "days_cover": cover, "last_sold": last,
                    "days_since_sold": (today - last).days if last else None,
                    "last_delivered": sup.get(item_id, {}).get("at"), "perishable": perishable, "expiry_risk": expiry,
                    "status": status, "action": action, "drinks": drinks})
    order = {"dead": 0, "no_recipe": 1, "slow": 2, "expiry": 3}
    out.sort(key=lambda x: (order[x["status"]], -(x["value"] or ZERO)))
    tied = defaultdict(lambda: ZERO)
    for x in out:
        tied[x["status"]] += x["value"] or ZERO
    stock_value = sum(((on_hand or {}).get(i, ZERO) * (it.unit_cost or ZERO) for i, it in items.items()
                       if (on_hand or {}).get(i, ZERO) > 0), ZERO)
    return {"from": d0, "to": today, "days_covered": covered, "slow_days": slow_days, "perish_days": perish_days,
            "counted": on_hand is not None,
            "summary": {"tied_up": sum(tied.values(), ZERO), "by_status": dict(tied), "stock_value": stock_value,
                        "items": len(out), "uncosted": sum(1 for x in out if x["value"] is None)},
            "rows": out}


# ---------- 4. item sales ----------

def item_sales(db: Session, location_id: str, d0: date | None = None, d1: date | None = None) -> dict:
    """What sold between two dates: one bucket per Swype sales period (the import granularity), per-item totals,
    and each item's amount in every bucket so the chart can show a single item. Periods belong to the window
    their last day falls in, as in the other reports. `bounds` is the full range of sales on file, so the UI
    can zoom back out."""
    d1 = d1 or _today()
    d0 = d0 or d1 - timedelta(days=89)
    if d0 > d1:
        d0, d1 = d1, d0
    rows, covered = _sales(db, location_id, d0, d1)
    periods = sorted({(r.period_from, r.period_to) for r in rows})
    index = {p: i for i, p in enumerate(periods)}
    name = _drink_names(db, rec.load_all(db), rows)

    by_item: dict[str, dict] = {}
    buckets = [{"from": pf, "to": pt, "qty": ZERO, "net": ZERO} for pf, pt in periods]
    for r in rows:
        qty, net, gross = Decimal(r.total_qty), Decimal(r.net_amount), Decimal(r.amount)
        i = index[(r.period_from, r.period_to)]
        buckets[i]["qty"] += qty
        buckets[i]["net"] += net
        x = by_item.get(r.item_code)
        if x is None:
            x = by_item[r.item_code] = {"code": r.item_code, "name": None, "category": r.category,
                                        "department": r.department, "qty": ZERO, "paid_qty": ZERO,
                                        "promo_qty": ZERO, "free_qty": ZERO, "gross": ZERO, "net": ZERO,
                                        "series_qty": [ZERO] * len(periods), "series_net": [ZERO] * len(periods)}
        x["qty"] += qty
        x["paid_qty"] += Decimal(r.qty)
        x["promo_qty"] += Decimal(r.promo_qty or 0)
        x["free_qty"] += Decimal(r.free_qty or 0)
        x["gross"] += gross
        x["net"] += net
        x["series_qty"][i] += qty
        x["series_net"][i] += net

    total_net = sum((b["net"] for b in buckets), ZERO)
    total_qty = sum((b["qty"] for b in buckets), ZERO)
    out = []
    for x in by_item.values():
        x["name"] = name(x["code"])
        x["avg_price"] = x["net"] / x["qty"] if x["qty"] else None
        x["share_pct"] = x["net"] / total_net * 100 if total_net else None
        out.append(x)
    out.sort(key=lambda x: (-x["net"], -x["qty"], x["name"]))

    first, last = db.execute(select(func.min(SwypeItemSales.period_from), func.max(SwypeItemSales.period_to))
                             .where(SwypeItemSales.location_id == location_id)).one()
    return {"from": d0, "to": d1, "days_covered": covered, "bounds": {"from": first, "to": last},
            "summary": {"qty": total_qty, "net": total_net, "gross": sum((x["gross"] for x in out), ZERO),
                        "items": len(out), "avg_price": total_net / total_qty if total_qty else None,
                        "per_day": total_net / covered if covered else None},
            "periods": buckets, "rows": out}
