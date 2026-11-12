"""Drafting recipes for what sold without one, for someone to approve in bulk on the Recipes page.

A recipe stays the only link between a Swype item and stock: nothing here saves a match by itself. Only a
Swype category's stock groups are candidates ("Happy Red 9oz" is wine, so never Smirnoff Red), and names are
compared after dropping what Swype adds to them (Happy, Spec, Pint, Gls, SNG, DBL, 6oz...), so "core" is what is
left. A core is looked up in this order:

1. the recipe of the bottle-level code (00000331 for 00000331_02)
2. a one-line recipe already saved for the same core: approving "Happy Gin DBL" teaches "Spec Gin"
3. the house pours (HOUSE)
4. the words of stock item names, tolerating typos ("Glenfiddich" / "Glenffiddich")

A cocktail with the same core as one that has a recipe ("Shaft", "Happy Shaft") gets a copy of it to check.

Food and coffee take no stock, so they get no-stock recipes without asking (apply_no_stock).
"""
from __future__ import annotations

import re
from decimal import Decimal
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import InventoryItem
from ..units import BASE_UNITS
from . import recipes as rec
from . import swype_items
from .recipes import BOTTLE_RE, DOUBLE_RE, OZ_RE, SINGLE_RE
from .swype_items import base_code

PINT_OZ, GUINNESS_OZ = Decimal(20), Decimal(16)
SINGLE_OZ, DOUBLE_OZ = Decimal(1), Decimal(2)
NO_STOCK_NOTE = "Takes no stock (food and coffee)"

WINE = {"Wine", "Boxed wine"}
SPIRITS = {"Spirits", "Scotch & whisky"}
BEER = {"Draft beer", "Bottled beer"}
CATEGORY_GROUPS = {
    "draft beer": {"Draft beer"}, "bottled beer": {"Bottled beer"}, "pop": {"Pop", "Bottled beer"},  # the 0% beers ring as pop
    "red wine": WINE, "white wine": WINE, "rose & bubbles": WINE,
    "booze": SPIRITS, "scotch & irish": SPIRITS, "brunch drinks": SPIRITS | WINE,
    "smaller spec drinks": SPIRITS | WINE | BEER,
}
HOUSE = {  # what a generic name pours; stock item names, by core
    "gin": "Gordons Gin", "vodka": "Smirnoff Red", "smirnoff vodka": "Smirnoff Red", "tequila": "Cazadores Blanco",
    "whisky": "Jameson", "whiskey": "Jameson", "white rum": "Captains White", "dark rum": "Captains Dark",
    "spice rum": "Captains Spice", "spiced rum": "Captains Spice",
    "red": "Laurent Miquel Red", "white": "Laurent Miquel White", "rose": "Laurent Miquel Rose",
    "prosecco": "Fiol", "prossecco": "Fiol",
}
NOISE = {"happy", "spec", "brunch", "house", "pint", "gls", "glass", "sng", "dbl", "btl", "bottle", "single", "double",
         "premium", "delux", "deluxe"}
GENERIC = {"vodka", "gin", "rum", "tequila", "whisky", "whiskey", "lager"}  # say nothing once a brand is named
ABBREVIATIONS = {"rt": ["red", "truck"]}
SIZE_RE = re.compile(r"\b\d+(?:\.\d+)?\s*oz\b")
GUINNESS_RE = re.compile(r"guin+es", re.I)


def core(name: str) -> list[str]:
    """The words that name the product: "Happy Gordon's Gin DBL" -> ["gordons", "gin"]."""
    s = SIZE_RE.sub(" ", name.lower().replace("'", "").replace("’", ""))
    out: list[str] = []
    for t in re.findall(r"[a-z0-9&]+", s):
        if t != "&" and t not in NOISE:
            out += ABBREVIATIONS.get(t, [t])
    return out


def _same(x: str, y: str) -> bool:
    return x == y or SequenceMatcher(None, x, y).ratio() >= 0.8 or (len(x) >= 4 and y.startswith(x))


def score(words: list[str], item_words: list[str]) -> float:
    """1 when every word of the core is in the item's name; 0.9 when the item's whole name starts the core,
    so the core says more than the item does ("Jager Bomb" and Jager)."""
    c = [w for w in words if w not in GENERIC]  # only a generic word left ("Brunch Lager") names no brand
    it = [w for w in item_words if w not in GENERIC] or item_words
    if not c or not it:
        return 0
    joined, item_joined = "".join(c), "".join(it)
    if len(c) > 1 and any(SequenceMatcher(None, joined, y).ratio() >= 0.9 for y in it):
        return 1  # "strange fellows" and "Strangefellews Talisman"
    s = sum(any(_same(x, y) for y in it) for x in c) / len(c)
    if s < 1 and len(item_joined) >= 4 and joined.startswith(item_joined):
        return max(s, 0.9)
    return s


def is_no_stock(row: dict) -> bool:
    return row.get("department") == "Food" or (row.get("category") or "").lower() == "coffee"


def portion(name: str, item: InventoryItem) -> tuple[Decimal | None, str, str | None]:
    """(qty, unit, what to check) for one sold. qty None means the name doesn't give the size."""
    if item.stock_unit == "each" or BOTTLE_RE.search(name):
        return Decimal(item.pack_size), item.stock_unit, None
    if BASE_UNITS.get(item.stock_unit, ("",))[0] != "volume":
        return None, item.stock_unit, f"{item.name} is counted in '{item.stock_unit}'"
    if m := OZ_RE.search(name):
        return Decimal(m.group(1)), "oz", None
    if item.category == "Draft beer":  # Pint, Gls, Spec and Happy all pour the same
        return (GUINNESS_OZ if GUINNESS_RE.search(item.name) else PINT_OZ), "oz", None
    if DOUBLE_RE.search(name):
        return DOUBLE_OZ, "oz", None
    if SINGLE_RE.search(name):
        return SINGLE_OZ, "oz", None
    if item.category in SPIRITS:
        return SINGLE_OZ, "oz", "no size in the name; taken as a single"
    return None, "oz", "pour size"


def _item_out(it: InventoryItem) -> dict:
    return {"id": it.id, "name": it.name, "sku": it.sku, "stock_unit": it.stock_unit}


def _learned(recipes: dict) -> tuple[dict[str, InventoryItem], dict[str, list]]:
    """Core -> the item its one-line recipes take (when they agree), and core -> a recipe of several lines."""
    single: dict[str, set] = {}
    items: dict[str, InventoryItem] = {}
    copies: dict[str, list] = {}
    for code, r in sorted(recipes.items()):
        v = rec.current_version(r)
        if r.ignored or v is None:
            continue
        key = " ".join(core(r.name))
        if len(v.lines) == 1 and not v.lines[0].options:
            single.setdefault(key, set()).add(v.lines[0].item_id)
            items[key] = v.lines[0].item
        elif v.lines:
            copies.setdefault(key, v.lines)
    return {k: items[k] for k, ids in single.items() if len(ids) == 1}, copies


def _guess(row: dict, stock: list[InventoryItem], bases: dict, learned: dict) -> tuple[list[InventoryItem], str | None, bool]:
    """(candidates, how, clear). Clear: one item, found with nothing left over."""
    groups = CATEGORY_GROUPS.get((row.get("category") or "").lower())
    words = core(row["name"])
    key = " ".join(words)
    base = base_code(row["code"])
    if base != row["code"] and base in bases:
        return [bases[base]], "bottle_recipe", True
    if groups is None:
        return [], None, False
    if key in learned and learned[key].category in groups:
        return [learned[key]], "approved", True
    cand = [i for i in stock if i.category in groups]
    if key in HOUSE:
        hit = [i for i in cand if swype_items.norm(i.name) == swype_items.norm(HOUSE[key])]
        if hit:
            return hit, "house", True
    # the shortest name wins a tie: "Coke" is Coke, not Diet Coke
    scored = sorted(((score(words, core(i.name)), len(core(i.name)), i) for i in cand), key=lambda x: (-x[0], x[1], x[2].name))
    scored = [x for x in scored if x[0] >= 0.5][:3]
    if not scored:
        return [], None, False
    clear = scored[0][0] == 1 and (len(scored) == 1 or scored[1][:2] != scored[0][:2])
    return [x[2] for x in scored], "name", clear


def queue(db: Session, location_id: str) -> dict:
    """Every sold item with no recipe, best sellers first, with a draft where one can be made. `clear` rows are
    the ones to approve as they stand; the rest need an item, a size or a look."""
    recipes = rec.load_all(db)
    stock = list(db.scalars(select(InventoryItem).where(InventoryItem.is_active)))
    bases = rec.base_recipe_items(recipes)
    learned, copies = _learned(recipes)
    renamed = swype_items.custom_names(db)
    rows, no_stock = [], 0
    for r in sorted(rec.sold_codes(db, location_id), key=lambda r: -r["sold"]):
        if r["code"] in recipes:
            continue
        if is_no_stock(r):
            no_stock += 1
            continue
        out = {"code": r["code"], "name": renamed.get(r["code"], r["name"]), "category": r["category"],
               "sold": r["sold"], "lines": [], "candidates": [], "how": None, "check": None, "clear": False}
        rows.append(out)
        lines = copies.get(" ".join(core(r["name"])))
        if lines is not None:
            out.update(how="copy", check="copied from a drink of the same name; check the portions",
                       lines=[{**_item_out(ln.item), "item_ids": ln.item_ids, "options": [_item_out(it) for it in ln.items[1:]],
                              "qty": ln.qty, "unit": ln.unit} for ln in lines])
            continue
        cand, how, clear = _guess(r, stock, bases, learned)
        if not cand:
            continue
        qty, unit, check = portion(r["name"], cand[0])
        check = check or (None if clear else "check the item")
        out.update(how=how, check=check, clear=clear and qty is not None and check is None,
                   candidates=[_item_out(i) for i in cand],
                   lines=[{**_item_out(cand[0]), "qty": qty, "unit": unit}])
    return {"rows": rows, "no_stock": no_stock}


def approve(db: Session, rows: list[dict]) -> int:
    """Save reviewed rows as recipes: all or none (the caller commits). A code that got a recipe meanwhile
    keeps it. Returns how many were saved."""
    have = rec.load_all(db)
    n = 0
    for r in rows:
        if r["code"] not in have:
            rec.save(db, r["code"], name=r["name"], lines=r["lines"], ignored=r["ignored"])
            n += 1
    return n


def apply_no_stock(db: Session, location_id: str) -> int:
    """Give every sold food and coffee item a no-stock recipe. Flushes, does not commit. Returns how many."""
    have = rec.load_all(db)
    n = 0
    for r in rec.sold_codes(db, location_id):
        if r["code"] not in have and is_no_stock(r):
            rec.save(db, r["code"], name=r["name"], lines=[], ignored=True, notes=NO_STOCK_NOTE)
            n += 1
    return n
