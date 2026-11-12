"""Recipes: what each Swype item # takes out of inventory.

A recipe belongs to one Swype item # (the thing rung in: a cocktail, "Smirnoff Vodka DBL", "House Red 6oz")
and lists portions of inventory items, entered in oz/ml/each and converted to the item's stock unit when a
report runs. Saving either corrects the version in effect (effective_from=None) or starts a new version
from a date, so a recipe change does not rewrite past weeks.

A recipe is the only link between a Swype item and inventory: Swype items are never inventory items, and
one Swype item can take several inventory items (a cocktail) or one (a pour, a bottle of beer). A line can
also name several items the portion may come out of ("Mystery Wine": 6 oz of Box A or Box B or Box C).

Pours follow a naming pattern in Swype, so suggest_pours can draft their recipes: SNG/Single -> one
single pour of the bottle it most likely pours from (swype_items.suggest_item), DBL/Double -> a double, "6oz" -> 6 oz, Btl -> one whole bottle, and the
bare bottle code (00000330 "Smirnoff Vodka") -> a single when the same code also has _01/_02 pours.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import InventoryItem, Recipe, RecipeLine, RecipeLineOption, RecipePhoto, RecipeVersion, SwypeItemSales, SwypeMenuItem, Unit
from ..units import BASE_UNITS, ConversionError, convert
from . import settings_svc
from . import swype_items
from .swype_items import base_code

PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
PHOTO_MAX_BYTES = 5 * 1024 * 1024
RECIPE_EPOCH = date(2000, 1, 1)  # effective_from of a first version: applies to every week on file
DOUBLE_RE = re.compile(r"\b(?:DBL|Double)\b", re.I)
SINGLE_RE = re.compile(r"\b(?:SNG|Single)\b", re.I)
OZ_RE = re.compile(r"(\d+(?:\.\d+)?)\s*oz\s*$", re.I)
BOTTLE_RE = re.compile(r"\b(?:BTL|Bottle)\s*$", re.I)


class RecipeError(ValueError):
    pass


def version_at(recipe: Recipe, day: date) -> RecipeVersion | None:
    """The version in effect on a local date (versions are ordered by effective_from)."""
    found = None
    for v in recipe.versions:
        if v.effective_from <= day:
            found = v
    return found


def current_version(recipe: Recipe) -> RecipeVersion | None:
    return recipe.versions[-1] if recipe.versions else None


def to_stock_unit(line_qty: Decimal, unit: str, item: InventoryItem, oz_ml: Decimal) -> Decimal:
    """One portion in the item's stock unit. Raises ConversionError (oz of an item counted in 'each')."""
    return convert(Decimal(line_qty), unit, item.stock_unit, oz_ml=oz_ml,
                   yield_to_unit=item.yield_to_unit, yield_qty=item.yield_qty)


def item_portion(it: InventoryItem, qty: Decimal, unit: str, oz_ml: Decimal) -> dict:
    out = {"item_id": it.id, "sku": it.sku, "item_name": it.name, "stock_unit": it.stock_unit,
           "pack_name": it.pack_name, "pack_size": it.pack_size, "stock_qty": None, "packs": None, "error": None}
    try:
        q = to_stock_unit(qty, unit, it, oz_ml)
        out["stock_qty"] = q
        out["packs"] = q / Decimal(it.pack_size) if it.pack_size else None
    except ConversionError:
        out["error"] = (f"{it.name} is counted in '{it.stock_unit}', so {unit} can't be converted; "
                        f"change the item's 'Counted in' unit or enter the portion in {it.stock_unit}")
    return out


def line_out(line: RecipeLine, oz_ml: Decimal) -> dict:
    """The first item's fields at the top level (one-item lines read as before), and every item, first one
    included, in `items`. A line with more than one item is poured from any one of them."""
    items = [item_portion(it, line.qty, line.unit, oz_ml) for it in line.items]
    errors = [x["error"] for x in items if x["error"]]
    return {**items[0], "qty": line.qty, "unit": line.unit, "items": items, "error": errors[0] if errors else None}


def recipe_out(r: Recipe, oz_ml: Decimal) -> dict:
    v = current_version(r)
    return {"id": r.id, "swype_code": r.swype_code, "name": r.name, "ignored": r.ignored, "steps": r.steps, "notes": r.notes,
            "photo_at": r.photo.updated_at if r.photo else None,
            "effective_from": v.effective_from if v else None,
            "lines": [line_out(ln, oz_ml) for ln in v.lines] if v else [],
            "versions": [{"effective_from": x.effective_from, "lines": len(x.lines)} for x in r.versions]}


def _with_lines():
    lines = selectinload(Recipe.versions).selectinload(RecipeVersion.lines)
    return lines.selectinload(RecipeLine.item), lines.selectinload(RecipeLine.options).joinedload(RecipeLineOption.item)


def load(db: Session, code: str) -> Recipe | None:
    return db.scalar(select(Recipe).where(Recipe.swype_code == code).options(*_with_lines()))


def load_all(db: Session) -> dict[str, Recipe]:
    rs = db.scalars(select(Recipe).options(*_with_lines()))
    return {r.swype_code: r for r in rs}


def save(db: Session, code: str, *, name: str, lines: list[dict], effective_from: date | None = None,
         ignored: bool = False, steps: str | None = None, notes: str | None = None) -> Recipe:
    """lines: {"item_ids": [...], "qty", "unit"} (or one "item_id"). Several item_ids make a line poured from
    any one of them. effective_from None corrects the version in effect (or creates the first one); a date
    starts a new version from that day, or replaces the one already on that day."""
    code = code.strip()
    if not code:
        raise RecipeError("Swype item # is required")
    clean = []
    for i, ln in enumerate(lines):
        ids = [x for x in (ln.get("item_ids") or [ln.get("item_id")]) if x]
        items = []
        for item_id in dict.fromkeys(ids):  # same item twice is once
            item = db.get(InventoryItem, item_id)
            if item is None:
                raise RecipeError(f"line {i + 1}: pick an inventory item")
            items.append(item)
        if not items:
            raise RecipeError(f"line {i + 1}: pick an inventory item")
        names = " or ".join(it.name for it in items)
        qty = Decimal(str(ln.get("qty") or 0))
        if qty <= 0:
            raise RecipeError(f"line {i + 1} ({names}): portion must be more than 0")
        unit = ln.get("unit") or "oz"
        if unit not in BASE_UNITS or db.get(Unit, unit) is None:
            raise RecipeError(f"line {i + 1}: unknown unit {unit!r}")
        clean.append((items, qty, unit))
    if not clean and not ignored:
        raise RecipeError("add at least one ingredient, or mark the item as taking no stock")
    r = load(db, code)
    if r is None:
        r = Recipe(swype_code=code, name=name or code)
        db.add(r)
    r.name, r.ignored, r.steps, r.notes = name or r.name, ignored, steps, notes
    if effective_from is None:
        v = current_version(r)
        if v is None:
            v = RecipeVersion(effective_from=RECIPE_EPOCH)
            r.versions.append(v)
    else:
        v = next((x for x in r.versions if x.effective_from == effective_from), None)
        if v is None:
            v = RecipeVersion(effective_from=effective_from)
            r.versions.append(v)
            r.versions.sort(key=lambda x: x.effective_from)
    v.lines.clear()
    for pos, (items, qty, unit) in enumerate(clean):
        v.lines.append(RecipeLine(item=items[0], qty=qty, unit=unit, position=pos,
                                  options=[RecipeLineOption(item=it, position=k) for k, it in enumerate(items[1:], 1)]))
    db.flush()
    return r


def delete(db: Session, code: str) -> bool:
    r = load(db, code)
    if r is None:
        return False
    db.delete(r)
    db.flush()
    return True


def set_photo(db: Session, r: Recipe, data: bytes, content_type: str) -> RecipePhoto:
    if content_type not in PHOTO_TYPES:
        raise RecipeError("photo must be a JPEG, PNG, WebP or GIF image")
    if not data:
        raise RecipeError("photo is empty")
    if len(data) > PHOTO_MAX_BYTES:
        raise RecipeError(f"photo is over {PHOTO_MAX_BYTES // (1024 * 1024)} MB")
    if r.photo is None:
        r.photo = RecipePhoto(content_type=content_type, data=data)
    else:
        r.photo.content_type, r.photo.data = content_type, data
    db.flush()
    return r.photo


def codes_using(db: Session, item_ids: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Inventory item id -> [(Swype item #, recipe name)] whose current recipe takes it, by code."""
    out: dict[str, list[tuple[str, str]]] = {}
    wanted = set(item_ids)
    for code, r in sorted(load_all(db).items()):
        v = current_version(r)
        if r.ignored or v is None:
            continue
        for item_id in {i for ln in v.lines for i in ln.item_ids} & wanted:
            out.setdefault(item_id, []).append((code, r.name))
    return out


# ---------- the menu: every Swype item # that has sold, with its recipe ----------

def sold_codes(db: Session, location_id: str, date_from: date | None = None, date_to: date | None = None) -> list[dict]:
    """One row per Swype item # in the imported sales: latest name/category, total sold, weeks on file.
    With date_from/date_to, sold and weeks count only imported periods overlapping that range (a period is
    a whole report, never split); name, category and last_sold still come from every period."""
    rows = db.execute(
        select(SwypeItemSales.item_code, SwypeItemSales.description, SwypeItemSales.department,
               SwypeItemSales.category, SwypeItemSales.total_qty, SwypeItemSales.period_from,
               SwypeItemSales.period_to)
        .where(SwypeItemSales.location_id == location_id)
        .order_by(SwypeItemSales.period_to)
    ).all()
    out: dict[str, dict] = {}
    for code, desc, dept, cat, qty, pf, pt in rows:
        o = out.setdefault(code, {"code": code, "sold": Decimal(0), "weeks": 0})
        o.update(name=desc, department=dept, category=cat, last_sold=pt)  # latest name wins
        if (date_from is None or pt >= date_from) and (date_to is None or pf <= date_to):
            o["sold"] += Decimal(qty)
            o["weeks"] += 1
    return list(out.values())


def base_recipe_items(recipes: dict[str, Recipe]) -> dict[str, InventoryItem]:
    """Bottle-level code -> the one inventory item its current recipe pours, so its _01/_02 pours follow it."""
    out = {}
    for code, r in recipes.items():
        v = current_version(r)
        if "_" not in code and not r.ignored and v is not None and len(v.lines) == 1 and not v.lines[0].options:
            out[code] = v.lines[0].item
    return out


def menu(db: Session, location_id: str) -> list[dict]:
    """Every Swype item that sold, is on the Swype menu, or has a recipe, with its recipe. suggested_item is a
    guess at the bottle a pour comes out of, for prefilling the editor; it is not saved anywhere."""
    oz_ml = settings_svc.oz_ml(db)
    recipes = load_all(db)
    rows = sold_codes(db, location_id)
    known = {r["code"] for r in rows}
    for m in db.scalars(select(SwypeMenuItem)):  # on the menu, nothing sold on file
        if m.code not in known:
            known.add(m.code)
            rows.append({"code": m.code, "name": m.name, "department": None, "category": m.category,
                         "sold": Decimal(0), "weeks": 0, "last_sold": None})
    for code, r in recipes.items():  # recipes for codes not seen in any import yet
        if code not in known:
            rows.append({"code": code, "name": r.name, "department": None, "category": None, "sold": Decimal(0),
                         "weeks": 0, "last_sold": None})
    renamed = swype_items.custom_names(db)
    by_key = swype_items.items_by_key(db)
    bases = base_recipe_items(recipes)
    for row in rows:
        r = recipes.get(row["code"])
        row["recipe"] = recipe_out(r, oz_ml) if r else None
        g = None if r else swype_items.suggest_item(row["code"], row["name"], by_key, bases)
        row["suggested_item"] = ({"id": g[0].id, "name": g[0].name, "sku": g[0].sku,
                                  "stock_unit": g[0].stock_unit, "how": g[1]} if g else None)
        row["name"] = renamed.get(row["code"], row["name"])  # after the guess: it reads Swype's SNG/DBL names
    return sorted(rows, key=lambda r: (r["department"] or "~", r["category"] or "", r["name"]))


# ---------- drafting pour recipes from Swype names ----------

def pour_portion(code: str, name: str, family_has_pours: bool, single_oz: Decimal,
                 double_oz: Decimal) -> tuple[str, Decimal | None] | None:
    """(kind, oz) for a pour-style name; oz None means one whole pack. None when the name says nothing."""
    if BOTTLE_RE.search(name):
        return "bottle", None
    if m := OZ_RE.search(name):
        return f"{m.group(1)} oz", Decimal(m.group(1))
    if DOUBLE_RE.search(name):
        return "double", double_oz
    if SINGLE_RE.search(name):
        return "single", single_oz
    if "_" not in code and family_has_pours:
        return "single", single_oz
    return None


def suggest_pours(db: Session, location_id: str, *, single_oz: Decimal, double_oz: Decimal,
                  apply: bool = False) -> dict:
    recipes = load_all(db)
    have = set(recipes)
    by_key = swype_items.items_by_key(db)
    bases = base_recipe_items(recipes)
    rows = sold_codes(db, location_id)
    families = {base_code(r["code"]) for r in rows if "_" in r["code"]}
    ready, skipped = [], []
    for r in sorted(rows, key=lambda r: "_" in r["code"]):  # bottle codes first: their pours follow them
        code = r["code"]
        if code in have:
            continue
        p = pour_portion(code, r["name"], base_code(code) in families, single_oz, double_oz)
        if p is None:
            continue
        kind, oz = p
        g = swype_items.suggest_item(code, r["name"], by_key, bases)
        if g is None:
            skipped.append({"code": code, "name": r["name"],
                            "reason": "no inventory item matches the name; write the recipe of its bottle code "
                                      f"{base_code(code)} first, or pick the bottle in the editor"})
            continue
        item, how = g
        if oz is None:
            qty, unit = Decimal(item.pack_size), item.stock_unit
        elif BASE_UNITS.get(item.stock_unit, ("",))[0] != "volume":
            skipped.append({"code": code, "name": r["name"], "item_name": item.name,
                            "reason": f"{item.name} is counted in '{item.stock_unit}'; set it to ml (and its bottle "
                                      f"size) on the Stock items page first"})
            continue
        else:
            qty, unit = oz, "oz"
        ready.append({"code": code, "name": r["name"], "kind": kind, "item_id": item.id, "item_name": item.name,
                      "matched_by": how, "qty": qty, "unit": unit, "sold": r["sold"]})
        if "_" not in code:
            bases.setdefault(code, item)
    if apply:
        for s in ready:
            save(db, s["code"], name=s["name"], lines=[{"item_id": s["item_id"], "qty": s["qty"], "unit": s["unit"]}])
        db.commit()
    return {"ready": ready, "skipped": skipped, "applied": apply}
