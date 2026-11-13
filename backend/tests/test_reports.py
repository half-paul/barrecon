from datetime import date, datetime, timezone
from decimal import Decimal

from app.models import InventoryItemCost
from app.services import recipes as rec
from app.services import reports

from test_performance import sale
from test_variance import SUN1, SUN2, WEEK, count, item

TODAY = date(2026, 10, 26)


def setup(db, location):
    vodka = item(db, "VOD", "Smirnoff Vodka", cost=Decimal(30))
    db.add(InventoryItemCost(item_id=vodka.id, effective_from=datetime(2026, 6, 1, tzinfo=timezone.utc),
                             pack_cost=Decimal(20), source="manual"))
    gin = item(db, "GIN", "Tanqueray Gin", cost=Decimal(40))
    rec.save(db, "00000561", name="Moscow Mule", lines=[{"item_id": vodka.id, "qty": 75, "unit": "ml"}])
    rec.save(db, "00000999", name="Gin & Tonic", lines=[{"item_id": gin.id, "qty": 30, "unit": "ml"}])
    sale(db, location, "00000561", "Cocktails", Decimal(100), WEEK)
    sale(db, location, "00000777", "Cocktails", Decimal(40), WEEK, qty=4)  # no recipe
    count(db, location, SUN1, [(vodka, 3), (gin, 2)])
    count(db, location, SUN2, [(vodka, 1.5), (gin, 2)])
    db.flush()
    return vodka, gin


def test_pour_cost(db, location):
    vodka, _ = setup(db, location)
    r = reports.pour_cost(db, location.id, today=TODAY)
    mule = next(x for x in r["rows"] if x["code"] == "00000561")
    assert mule["price"] == Decimal(10) and mule["serve_cost"] == Decimal(3)
    assert mule["pour_cost_pct"] == Decimal(30) and mule["gross_profit"] == Decimal(70)
    assert mule["serve_cost_before"] == Decimal(2) and mule["cost_change_pct"] == Decimal(50)
    assert set(mule["flags"]) == {"high_cost", "cost_rising"}
    assert [x["code"] for x in r["no_recipe"]] == ["00000777"]
    assert r["summary"]["pour_cost_pct"] == Decimal(30) and r["summary"]["no_recipe_revenue"] == Decimal(40)
    assert r["rising"][0]["item_id"] == vodka.id and r["rising"][0]["drinks"] == ["Moscow Mule"]


def test_reorder(db, location):
    vodka, gin = setup(db, location)
    r = reports.reorder(db, location.id, today=TODAY, lead_days=3, cover_days=14)
    v = next(x for x in r["rows"] if x["item_id"] == vodka.id)
    assert v["on_hand"] == Decimal(1125)  # 1.5 bottles counted on the 26th, nothing sold since
    assert v["packs"] == 1 and v["status"] == "reorder" and v["drinks"] == ["Moscow Mule"]
    assert all(x["item_id"] != gin.id for x in r["rows"])  # nothing sold, no par: nothing to order
    r = reports.reorder(db, location.id, today=TODAY, lead_days=3, cover_days=7)
    assert next(x for x in r["rows"] if x["item_id"] == vodka.id)["packs"] == 0
    r = reports.reorder(db, location.id, today=TODAY, lead_days=3, cover_days=7, demand_pct=Decimal(100))
    assert next(x for x in r["rows"] if x["item_id"] == vodka.id)["packs"] == 2  # an event doubling demand


def test_slow_moving(db, location):
    vodka, gin = setup(db, location)
    r = reports.slow_moving(db, location.id, today=TODAY)
    assert [x["item_id"] for x in r["rows"]] == [gin.id]
    g = r["rows"][0]
    assert g["status"] == "dead" and g["value"] == Decimal(80)
    assert r["summary"]["tied_up"] == Decimal(80)


def test_reports_api(client):
    for path in ("pour-cost", "reorder", "slow-moving"):
        assert client.get(f"/api/reports/{path}").status_code == 200
