from datetime import date
from decimal import Decimal

from app.models import ImportBatch, SwypeItemSales
from app.services import recipes as rec
from app.services.performance import performance

from test_variance import SUN1, SUN2, WEEK, count, item


def sale(db, loc, code, category, net, period, department="Liquor", qty=10):
    b = ImportBatch(location_id=loc.id, source="swype_file", dataset="item_sales")
    db.add(b)
    db.flush()
    db.add(SwypeItemSales(location_id=loc.id, period_from=period[0], period_to=period[1], department=department,
                          category=category, item_code=code, description=code, qty=qty, total_qty=qty,
                          amount=net, net_amount=net, import_batch_id=b.id))
    db.flush()


def test_bar_performance(db, location):
    vodka = item(db, "VOD", "Smirnoff Vodka", cost=Decimal(30))  # $30 a 750 ml bottle
    rec.save(db, "00000561", name="Moscow Mule", lines=[{"item_id": vodka.id, "qty": 75, "unit": "ml"}])
    sale(db, location, "00000561", "Cocktails", Decimal(100), WEEK)
    sale(db, location, "00000900", "Food", Decimal(50), WEEK, department="Food", qty=1)
    sale(db, location, "00000561", "Cocktails", Decimal(80), (date(2026, 10, 13), date(2026, 10, 19)))
    count(db, location, SUN1, [(vodka, 3)])
    count(db, location, SUN2, [(vodka, 1.5)])  # 1.5 bottles used, 1 explained by 10 mules

    p = performance(db, location.id, today=date(2026, 10, 26))
    assert p["net_sales"] == Decimal(150)
    assert p["prior_net_sales"] == Decimal(80)
    assert [c["category"] for c in p["categories"]] == ["Cocktails", "Food"]
    bc = p["beverage_cost"]
    assert bc["used"] == Decimal(45) and bc["sales"] == Decimal(100)  # food left out of beverage cost
    assert bc["pct"] == Decimal(45)
    assert bc["variance_cost"] == Decimal(-15)  # half a bottle short
    assert p["prior_beverage_cost"] is None
    assert p["stock"]["value"] == Decimal(45)


def test_bar_performance_empty(client):
    r = client.get("/api/variance/performance")
    assert r.status_code == 200
    j = r.json()
    assert j["net_sales"] is None and j["beverage_cost"] is None and j["stock"] is None and j["categories"] == []
