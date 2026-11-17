"""Recipes workbook import: a whole cocktail menu in one upload.

Tabs:
  Recipes  one row per ingredient: swype_code, drink, ingredient, sku, qty, unit, note.
           sku names the inventory item the portion comes out of. A row with no sku is an ingredient the
           bar doesn't track (juice, syrup, garnish, "top with soda"); it goes into the recipe's preparation
           steps. A row with no ingredient and only a note (glass, garnish, method) goes into the steps too,
           unless the note starts with "Note:", which makes it a recipe note: why the portions are what they
           are (a pre-batch scaled to the pour, a substitute). The note on a tracked ingredient is a recipe
           note as well. A drink with no tracked ingredient at all is saved as taking no stock.
           Several skus split by "|" ("BOXA | BOXB | BOXC") make a portion that comes out of any one of
           them (a "Mystery Wine" poured from whichever box is open).
  Items    optional, same columns as Items.xlsx: bottles the recipes need that the portal doesn't have yet.
           Upserted before the recipes are saved.

Any other tab (photos, the original spec sheet) is ignored. Validate everything, then write everything,
or write nothing. Recipes are saved as corrections of the version in effect (or a first version), like
saving them one by one on the Recipes page.
"""
from __future__ import annotations

import io
import re
from decimal import Decimal

from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import InventoryItem, Unit
from ..units import BASE_UNITS, ConversionError
from . import inventory as inv
from . import recipes as rec
from . import settings_svc
from .xlsx_io import (FONT, ITEM_COLS, ImportResult, RowResult, _bytes, _dec, _record_batch,
                      _reject_duplicate_file, _rows, _sheet, _style_header, parse_item_rows, sha256)

RECIPE_COLS = ["swype_code", "drink", "ingredient", "sku", "qty", "unit", "note"]
NOTE_RE = re.compile(r"^notes?\s*:\s*", re.I)


def _code(v) -> str:
    """Swype item #s are 8 digits with leading zeros; Excel turns a typed 00000561 into 561."""
    if isinstance(v, (int, float)):
        return str(int(v)).zfill(8)
    return str(v or "").strip()


def _text(v) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip() if v is not None else ""


def _untracked(ingredient: str, qty: Decimal | None, unit: str, note: str) -> str:
    amount = f"{qty.normalize():f} {unit}".strip() if qty is not None else ""
    return " ".join(x for x in (ingredient, amount, f"({note})" if note else "") if x)


SAMPLE_RECIPES = [
    # one row per ingredient; the Margarita shows every kind of row
    ["00000561", "Margarita", "Tequila", "EXAMPLE-TEQUILA", 1.5, "oz", None],
    ["00000561", "Margarita", "Triple sec", "EXAMPLE-TRIPLESEC", 0.75, "oz", None],
    ["00000561", "Margarita", "Lime juice", None, 0.75, "oz", "fresh squeezed"],
    ["00000561", "Margarita", None, None, None, None, "Rocks glass, salt rim. Shake and strain over ice."],
    ["00000561", "Margarita", None, None, None, None, "Note: house spec, 1.5 oz pour"],
    ["00000562", "House Wine (6 oz)", "Red wine", "EXAMPLE-WINE-A | EXAMPLE-WINE-B", 6, "oz", None],
    ["00000563", "Virgin Mojito", "Mint, lime, soda", None, None, None, "takes no tracked stock"],
]
SAMPLE_ITEMS = [
    ["EXAMPLE-TEQUILA", "Example Tequila 750", "Spirits", "ml", "bottle", 750, 12, 32.5, None, None, 2, True],
    ["EXAMPLE-TRIPLESEC", "Example Triple Sec 750", "Liqueurs", "ml", "bottle", 750, 12, 18.0, None, None, 1, True],
    ["EXAMPLE-WINE-A", "Example Red Box A 4L", "Wine", "ml", "box", 4000, 1, 45.0, None, None, 1, True],
    ["EXAMPLE-WINE-B", "Example Red Box B 4L", "Wine", "ml", "box", 4000, 1, 45.0, None, None, 1, True],
]
SAMPLE_LEGEND = [
    "Recipes: one row per ingredient. Rows with the same swype_code make one drink. Replace the example rows with your own.",
    "swype_code: the Swype item # (8 digits, keep the leading zeros; the column is formatted as text).",
    "sku: the stock item the portion is poured from (see the 'Stock items' tab for what the portal already has).",
    "  Several skus split by | (\"BOXA | BOXB\") = the portion comes out of whichever one is open.",
    "  No sku = an ingredient the bar doesn't count (juice, syrup, garnish); it goes into the preparation steps.",
    "qty + unit: the portion. unit is oz when blank. Units: " + ", ".join(sorted(BASE_UNITS)) + ".",
    "A row with only a note (glass, garnish, method) goes into the preparation steps.",
    "  A note starting with 'Note:' is a recipe note instead (why the portions are what they are).",
    "A drink with no row that has a sku is saved as taking no tracked stock.",
    "Items tab (optional): bottles the recipes need that the portal doesn't have yet, same columns as the Stock items sheet.",
    "  Delete the EXAMPLE rows there, or they will be created as items.",
    "Any other tab (like 'Stock items' and this one) is ignored on upload. A file is all-or-nothing: one bad row rejects it.",
]


def sample_workbook(db: Session) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Recipes"
    _style_header(ws, RECIPE_COLS, {"swype_code": 12, "drink": 22, "ingredient": 22, "sku": 34, "note": 50})
    for r in SAMPLE_RECIPES:
        ws.append(r)
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.font = FONT
    for row in range(2, 1001):
        ws.cell(row, 1).number_format = "@"
    units = sorted(BASE_UNITS)
    dv = DataValidation(type="list", formula1='"' + ",".join(units) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add("F2:F1000")

    it = wb.create_sheet("Items")
    _style_header(it, ITEM_COLS, {"sku": 20, "name": 30, "category": 16})
    for r in SAMPLE_ITEMS:
        it.append(r)
    for row in it.iter_rows(min_row=2):
        for c in row:
            c.font = FONT

    ref = wb.create_sheet("Stock items")
    _style_header(ref, ["sku", "name", "category", "stock_unit"], {"sku": 20, "name": 36, "category": 18})
    for item in db.scalars(select(InventoryItem).where(InventoryItem.is_active.is_(True))
                           .order_by(InventoryItem.category, InventoryItem.name)):
        ref.append([item.sku, item.name, item.category, item.stock_unit])

    lg = wb.create_sheet("Legend")
    lg.column_dimensions["A"].width = 140
    for i, line in enumerate(SAMPLE_LEGEND, 1):
        lg.cell(i, 1, line).font = FONT
    return _bytes(wb)


def import_recipes(db: Session, data: bytes, file_name: str) -> ImportResult:
    res = ImportResult("recipes")
    digest = sha256(data)
    if _reject_duplicate_file(db, "recipes", digest):
        res.status = "rejected"
        res.rows.append(RowResult(0, "error", "this exact file was already imported"))
        return res
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    rows = _rows(_sheet(wb, "Recipes"), RECIPE_COLS)
    item_rows = _rows(_sheet(wb, "Items"), ITEM_COLS) if any(n.strip().lower() == "items" for n in wb.sheetnames) else []
    new_items, errors = parse_item_rows(db, item_rows)
    file_skus = {d["sku"]: d for _, d in new_items}
    units = {u.code for u in db.scalars(select(Unit))}

    drinks: dict[str, dict] = {}
    for n, r in rows:
        try:
            code = _code(r["swype_code"])
            if not code:
                raise ValueError(f"row {n}: swype_code is required")
            d = drinks.setdefault(code, {"row": n, "name": "", "lines": [], "steps": [], "notes": []})
            d["name"] = d["name"] or _text(r["drink"])
            ingredient, sku, note = _text(r["ingredient"]), _text(r["sku"]), _text(r["note"])
            qty = _dec(r["qty"], "qty", n, allow_blank=True)
            unit = _text(r["unit"]).lower() or ("oz" if qty is not None else "")
            if not sku:
                if ingredient:
                    d["steps"].append(_untracked(ingredient, qty, unit, note))
                elif NOTE_RE.match(note):
                    d["notes"].append(NOTE_RE.sub("", note))
                elif note:
                    d["steps"].append(note)
                continue
            skus = list(dict.fromkeys(x.strip() for x in sku.split("|") if x.strip()))
            for one in skus:
                if inv.item_by_sku(db, one) is None and one not in file_skus:
                    raise ValueError(f"row {n}: sku {one} ({ingredient}) is not an item; add it to the Items tab or the Stock items page")
            if qty is None or qty <= 0:
                raise ValueError(f"row {n}: qty for {ingredient or sku} must be more than 0")
            if unit not in BASE_UNITS or unit not in units:
                raise ValueError(f"row {n}: unit {unit!r} unknown (use one of {sorted(BASE_UNITS)})")
            d["lines"].append({"skus": skus, "qty": qty, "unit": unit, "note": note})
        except ValueError as e:
            errors.append(RowResult(n, "error", str(e)))
    if not rows:
        errors.append(RowResult(0, "error", "Recipes tab has no rows"))
    if errors:
        res.status = "rejected"
        res.rows = errors
        res.errors = len(errors)
        _record_batch(db, res, "xlsx", file_name, digest, len(rows) + len(item_rows))
        return res

    for n, d in new_items:
        _, created = inv.upsert_item(db, d, cost_source="sheet")
        res.rows.append(RowResult(n, "inserted" if created else "updated", f"item {d['sku']} {d['name']}"))
    oz_ml = settings_svc.oz_ml(db)
    for code, d in drinks.items():
        existed = rec.load(db, code) is not None
        lines = []
        for ln in d["lines"]:
            items = [inv.item_by_sku(db, s) for s in ln["skus"]]
            lines.append({"item_ids": [it.id for it in items], "qty": ln["qty"], "unit": ln["unit"]})
            if ln["note"]:
                d["notes"].append(f"{' or '.join(it.name for it in items)}: {ln['note']}")
        r = rec.save(db, code, name=d["name"], lines=lines, ignored=not lines,
                     steps="\n".join(d["steps"]) or None, notes="\n".join(d["notes"]) or None)
        warn = []
        for ln in r.versions[-1].lines if r.versions else []:
            for it in ln.items:
                try:
                    rec.to_stock_unit(ln.qty, ln.unit, it, oz_ml)
                except ConversionError:
                    warn.append(f"{it.name} is counted in '{it.stock_unit}', so {ln.unit} can't be converted")
        msg = f"{code} {r.name}: " + (f"{len(lines)} tracked ingredient(s)" if lines else "takes no tracked stock")
        res.rows.append(RowResult(d["row"], "updated" if existed else "inserted", "; ".join([msg, *warn])))
        res.updated += existed
        res.inserted += not existed
    _record_batch(db, res, "xlsx", file_name, digest, len(rows) + len(item_rows))
    db.commit()
    return res
