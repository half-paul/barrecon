"""Unit conversion. Base units: ml (volume), g (mass), each (count).

The ounce is a setting: Canadian/imperial 28.4131 ml by default, US 29.5735 ml.
Cross-dimension conversion (a lemon -> ml of juice) is only allowed through an
explicit yield on the inventory item; otherwise it is an error the caller must
surface, never a silent skip.
"""
from __future__ import annotations

from decimal import Decimal

OZ_IMPERIAL_ML = Decimal("28.4131")
OZ_US_ML = Decimal("29.5735")

# code -> (dimension, factor to base). 'oz' is resolved at runtime from settings.
BASE_UNITS: dict[str, tuple[str, Decimal]] = {
    "ml": ("volume", Decimal(1)),
    "l": ("volume", Decimal(1000)),
    "cl": ("volume", Decimal(10)),
    "oz": ("volume", OZ_IMPERIAL_ML),
    "dash": ("volume", Decimal("0.92")),
    "g": ("mass", Decimal(1)),
    "kg": ("mass", Decimal(1000)),
    "each": ("count", Decimal(1)),
}


class ConversionError(ValueError):
    pass


def unit_table(oz_ml: Decimal = OZ_IMPERIAL_ML) -> dict[str, tuple[str, Decimal]]:
    t = dict(BASE_UNITS)
    t["oz"] = ("volume", Decimal(oz_ml))
    return t


def convert(
    qty: Decimal,
    from_unit: str,
    to_unit: str,
    *,
    oz_ml: Decimal = OZ_IMPERIAL_ML,
    yield_to_unit: str | None = None,
    yield_qty: Decimal | None = None,
) -> Decimal:
    """Convert qty from from_unit to to_unit.

    yield_to_unit/yield_qty describe the target item's explicit yield
    (1 <to_unit> = yield_qty <yield_to_unit>), used only when dimensions differ.
    """
    table = unit_table(oz_ml)
    qty = Decimal(qty)
    try:
        fdim, ffac = table[from_unit]
        tdim, tfac = table[to_unit]
    except KeyError as e:
        raise ConversionError(f"unknown unit {e.args[0]!r}") from None
    if fdim == tdim:
        return qty * ffac / tfac
    if yield_to_unit and yield_qty:
        ydim, yfac = table[yield_to_unit]
        if ydim == fdim:
            # qty in from_unit -> yield units -> count of to_unit
            in_yield_units = qty * ffac / yfac
            return in_yield_units / Decimal(yield_qty)
    raise ConversionError(f"cannot convert {from_unit} to {to_unit} without a yield")
