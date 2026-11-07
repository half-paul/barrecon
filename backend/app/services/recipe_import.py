"""Recipes workbook import: a whole cocktail menu in one upload.

Tabs:
  Recipes  one row per ingredient: swype_code, drink, ingredient, sku, qty, unit, note.
           sku names the inventory item the portion comes out of. A row with no sku is an ingredient the
           bar doesn't track (juice, syrup, garnish, "top with soda"); it goes into the recipe's preparation
           steps. A row with no ingredient and only a note (glass, garnish, method) goes into the steps too,
           unless the note starts with "Note:", which makes it a recipe note: why the portions are what they
           are (a pre-batch scaled to the pour, a substitute). The note on a tracked ingredient is a recipe
           note as well. A drink with no tracked ingredient at all is saved as taking no stock.
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

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Unit
from ..units import BASE_UNITS, ConversionError
from . import inventory as inv
from . import recipes as rec
from . import settings_svc
from .xlsx_io import (ITEM_COLS, ImportResult, RowResult, _dec, _record_batch, _reject_duplicate_file, _rows,
                      _sheet, parse_item_rows, sha256)

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
            if inv.item_by_sku(db, sku) is None and sku not in file_skus:
                raise ValueError(f"row {n}: sku {sku} ({ingredient}) is not an item; add it to the Items tab or the Stock items page")
            if qty is None or qty <= 0:
                raise ValueError(f"row {n}: qty for {ingredient or sku} must be more than 0")
            if unit not in BASE_UNITS or unit not in units:
                raise ValueError(f"row {n}: unit {unit!r} unknown (use one of {sorted(BASE_UNITS)})")
            d["lines"].append({"sku": sku, "qty": qty, "unit": unit, "note": note})
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
            item = inv.item_by_sku(db, ln["sku"])
            lines.append({"item_id": item.id, "qty": ln["qty"], "unit": ln["unit"]})
            if ln["note"]:
                d["notes"].append(f"{item.name}: {ln['note']}")
        r = rec.save(db, code, name=d["name"], lines=lines, ignored=not lines,
                     steps="\n".join(d["steps"]) or None, notes="\n".join(d["notes"]) or None)
        warn = []
        for ln in r.versions[-1].lines if r.versions else []:
            try:
                rec.to_stock_unit(ln.qty, ln.unit, ln.item, oz_ml)
            except ConversionError:
                warn.append(f"{ln.item.name} is counted in '{ln.item.stock_unit}', so {ln.unit} can't be converted")
        msg = f"{code} {r.name}: " + (f"{len(lines)} tracked ingredient(s)" if lines else "takes no tracked stock")
        res.rows.append(RowResult(d["row"], "updated" if existed else "inserted", "; ".join([msg, *warn])))
        res.updated += existed
        res.inserted += not existed
    _record_batch(db, res, "xlsx", file_name, digest, len(rows) + len(item_rows))
    db.commit()
    return res
