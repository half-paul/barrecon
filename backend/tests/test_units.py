from decimal import Decimal

import pytest

from app.units import OZ_IMPERIAL_ML, OZ_US_ML, ConversionError, convert


def test_oz_default_is_canadian_imperial():
    assert convert(Decimal(1), "oz", "ml") == OZ_IMPERIAL_ML


def test_26oz_bottle_is_750ml_at_imperial_not_us():
    assert abs(convert(Decimal(26), "oz", "ml") - 750) < 12
    assert abs(convert(Decimal(26), "oz", "ml", oz_ml=OZ_US_ML) - 750) > 18


def test_same_dimension_and_identity():
    assert convert(Decimal("1.5"), "l", "ml") == 1500
    assert convert(Decimal(5), "ml", "ml") == 5
    assert convert(Decimal(2), "kg", "g") == 2000
    assert convert(Decimal(2), "lb", "g") == Decimal("907.18474")
    assert abs(convert(Decimal(1), "kg", "lb") - Decimal("2.20462")) < Decimal("0.00001")


def test_cross_dimension_requires_yield():
    with pytest.raises(ConversionError):
        convert(Decimal(30), "ml", "each")
    # 1 lemon = 30 ml juice -> 60 ml of juice = 2 lemons
    assert convert(Decimal(60), "ml", "each", yield_to_unit="ml", yield_qty=Decimal(30)) == 2


def test_unknown_unit():
    with pytest.raises(ConversionError):
        convert(Decimal(1), "pint", "ml")
