"""One search box for the whole portal: items (incl. switched-off ones, and the Swype items whose recipes use them), Swype items,
recipes (name, ingredients, steps, notes), item groups, counts, deliveries/movements, receipts, imports and open review items.
Every word must match somewhere in a row. A section comes back empty unless the user can view its area."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import String, and_, case, cast, func, or_, select
from sqlalchemy.orm import Session

from ..auth import Auth, can, current_user
from ..db import get_db
from ..models import (CountLine, CountSession, DataIssue, Delivery, DeliveryLine, ImportBatch, InventoryItem, Recipe, StockMovement,
                      SwypeItemSales, SwypeMenuItem, User)
from ..services import recipes, swype_items

router = APIRouter(prefix="/api/search", tags=["search"], dependencies=[Auth])

MAX_LIMIT = 500  # per section; the Search page asks for this many

SECTION_AREA = {"items": "items", "swype_items": "swype_items", "recipes": "recipes", "groups": "items", "counts": "counts",
                "movements": "deliveries", "deliveries": "deliveries", "imports": "imports", "issues": "imports"}


def _words(q: str) -> list[str]:
    return [w for w in q.lower().split() if w][:6]


def _all_words(words: list[str], *cols):
    return and_(*[or_(*[func.lower(func.coalesce(c, "")).contains(w) for c in cols]) for w in words])


def _has_all(words: list[str], fields: list[str | None]) -> bool:
    low = [f.lower() for f in fields if f]
    return all(any(w in f for f in low) for w in words)


def _snippet(words: list[str], *texts: str | None, around: int = 30, width: int = 90) -> str | None:
    """The part of the first text holding a searched word, so a hit in long notes shows why it matched."""
    for text in texts:
        low = (text or "").lower()
        hits = [i for w in words if (i := low.find(w)) >= 0]
        if hits:
            start = max(0, min(hits) - around)
            part = " ".join(text[start:start + width].split())
            return ("…" if start else "") + part + ("…" if start + width < len(text) else "")
    return None


@router.get("")
def search(q: str = "", limit: int = 8, user: User = Depends(current_user), db: Session = Depends(get_db)):
    words = _words(q)
    if not words:
        return {k: [] for k in SECTION_AREA}
    limit = max(1, min(limit, MAX_LIMIT))

    # items: name, sku, group, or a Swype item # / drink name whose recipe uses the item
    phrase = " ".join(words)

    def rank(name):
        return case((name == phrase, 0), (name.startswith(phrase), 1), (name.contains(" " + phrase), 2), else_=3)

    hit_codes = set(db.scalars(select(Recipe.swype_code).where(_all_words(words, Recipe.swype_code, Recipe.name))))
    hit_codes |= set(db.scalars(select(SwypeItemSales.item_code).where(
        _all_words(words, SwypeItemSales.item_code, SwypeItemSales.description))))
    all_uses = recipes.codes_using(db, list(db.scalars(select(InventoryItem.id)))) if hit_codes else {}
    via_recipe = {i for i, cs in all_uses.items() if any(c in hit_codes for c, _ in cs)}
    item_rows = db.scalars(
        select(InventoryItem)
        .where(or_(_all_words(words, InventoryItem.name, InventoryItem.sku, InventoryItem.category),
                   InventoryItem.id.in_(via_recipe)))
        .order_by(rank(func.lower(InventoryItem.name)), InventoryItem.is_active.desc(), InventoryItem.name).limit(limit)).all()
    ids = [it.id for it in item_rows]
    uses = recipes.codes_using(db, ids)
    codes = {i: [c for c, _ in cs] for i, cs in uses.items()}
    # Swype items sold in the 7 days up to the latest imported day whose recipes use the item (drinks, not
    # stock units). Sales are imported one day at a time, so the week is summed.
    latest = db.scalar(select(func.max(SwypeItemSales.period_to)))
    sold: dict[str, float] = {}
    week: dict[str, float] = {}
    if latest is not None:
        week = dict(db.execute(select(SwypeItemSales.item_code, func.sum(SwypeItemSales.total_qty))
                               .where(SwypeItemSales.period_from >= latest - timedelta(days=6))
                               .group_by(SwypeItemSales.item_code)).all())
        for i, cs in codes.items():
            sold[i] = float(sum(week.get(c, 0) for c in cs))
    items = [{"id": it.id, "name": it.name, "sku": it.sku, "category": it.category, "is_active": it.is_active,
              "swype_codes": codes.get(it.id, []), "sold_last_week": sold.get(it.id)} for it in item_rows]

    all_recipes = recipes.load_all(db)
    shown_name = func.lower(func.coalesce(SwypeMenuItem.custom_name, SwypeMenuItem.name))
    swype = [{"code": m.code, "name": swype_items.display_name(m), "category": m.category, "is_active": m.is_active,
              "has_recipe": m.code in all_recipes, "sold_last_week": float(week.get(m.code, 0)) if latest is not None else None}
             for m in db.scalars(select(SwypeMenuItem)
                                 .where(_all_words(words, SwypeMenuItem.code, SwypeMenuItem.name, SwypeMenuItem.custom_name,
                                                   SwypeMenuItem.category))
                                 .order_by(rank(shown_name), SwypeMenuItem.is_active.desc(), shown_name).limit(limit))]

    # the same fields the Recipes page filters on, so following a hit there finds it again
    menu = {m.code: m for m in db.scalars(select(SwypeMenuItem))}
    recipe_hits = []
    for code, r in all_recipes.items():
        m = menu.get(code)
        v = recipes.current_version(r)
        names = [it.name for ln in v.lines for it in ln.items] if v else []
        if _has_all(words, [code, r.name, m and swype_items.display_name(m), m and m.category, r.steps, r.notes, *names]):
            in_name = _has_all(words, [r.name])
            recipe_hits.append((not in_name, r.ignored, r.name.lower(), code, {
                "code": code, "name": r.name, "category": m.category if m else None, "ignored": r.ignored, "ingredients": names,
                "snippet": None if in_name else _snippet(words, r.notes, r.steps)}))
    recipe_rows = [h[-1] for h in sorted(recipe_hits, key=lambda h: h[:4])[:limit]]

    groups = [{"name": name or "Uncategorised", "items": n, "active": int(a or 0)} for name, n, a in db.execute(
        select(InventoryItem.category, func.count(), func.sum(case((InventoryItem.is_active, 1), else_=0)))
        .where(_all_words(words, func.coalesce(InventoryItem.category, "uncategorised")))
        .group_by(InventoryItem.category).order_by(InventoryItem.category).limit(limit))]

    line_notes = dict(db.execute(select(CountLine.session_id, CountLine.note).where(_all_words(words, CountLine.note))).all())
    counts = [{"id": s.id, "counted_at": s.counted_at.isoformat(), "status": s.status, "source": s.source,
               "note": line_notes.get(s.id) or s.note}
              for s in db.scalars(select(CountSession)
                                  .where(or_(_all_words(words, CountSession.note, CountSession.external_ref, CountSession.status,
                                                        CountSession.source),
                                             CountSession.id.in_(line_notes)))
                                  .order_by(CountSession.counted_at.desc()).limit(limit))]

    movements = [{"id": m.id, "occurred_at": m.occurred_at.isoformat(), "kind": m.kind, "external_ref": m.external_ref,
                  "item": name, "status": m.status, "note": m.note, "delivery_id": m.delivery_id}
                 for m, name in db.execute(select(StockMovement, InventoryItem.name)
                                           .join(InventoryItem, InventoryItem.id == StockMovement.item_id)
                                           .where(_all_words(words, StockMovement.external_ref, StockMovement.note,
                                                             StockMovement.kind, InventoryItem.name))
                                           .order_by(StockMovement.occurred_at.desc()).limit(limit))]

    # by id: Postgres can't DISTINCT a row holding the JSON column, and a receipt can match on several lines
    delivery_ids = select(Delivery.id).outerjoin(DeliveryLine, DeliveryLine.delivery_id == Delivery.id).where(
        _all_words(words, Delivery.store, Delivery.store_address, Delivery.receipt_no, Delivery.note, DeliveryLine.text,
                   DeliveryLine.receipt_sku))
    deliveries = [{"id": x.id, "store": x.store, "receipt_no": x.receipt_no, "status": x.status, "note": x.note,
                   "occurred_at": x.occurred_at.isoformat() if x.occurred_at else None}
                  for x in db.scalars(select(Delivery).where(Delivery.id.in_(delivery_ids))
                                      .order_by(func.coalesce(Delivery.occurred_at, Delivery.created_at).desc()).limit(limit))]

    imports = [{"id": b.id, "file_name": b.file_name, "dataset": b.dataset, "status": b.status,
                "imported_at": b.imported_at.isoformat()}
               for b in db.scalars(select(ImportBatch)
                                   .where(_all_words(words, ImportBatch.file_name, ImportBatch.dataset, ImportBatch.source))
                                   .order_by(ImportBatch.imported_at.desc()).limit(limit))]

    issues = [{"id": i.id, "code": i.code, "detail": (i.detail or {}).get("detail")}
              for i in db.scalars(select(DataIssue).where(DataIssue.resolved.is_(False))
                                  .where(_all_words(words, DataIssue.code, cast(DataIssue.detail, String)))
                                  .order_by(DataIssue.created_at.desc()).limit(limit))]

    found = {"items": items, "swype_items": swype, "recipes": recipe_rows, "groups": groups, "counts": counts,
             "movements": movements, "deliveries": deliveries, "imports": imports, "issues": issues}
    return {k: rows if can(user, SECTION_AREA[k]) else [] for k, rows in found.items()}
