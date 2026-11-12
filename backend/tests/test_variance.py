from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.models import CountSession, ImportBatch, SwypeItemSales
from app.services import inventory as inv
from app.services import recipes as rec
from app.services import variance

SUN1 = datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc)  # Sun Oct 19, 11 pm Vancouver
SUN2 = datetime(2026, 10, 27, 6, 0, tzinfo=timezone.utc)  # Sun Oct 26, 11 pm Vancouver
WEEK = (date(2026, 10, 20), date(2026, 10, 26))


def item(db, sku, name, unit="ml", size=750, cost=None):
    it, _ = inv.upsert_item(db, {"sku": sku, "name": name, "stock_unit": unit, "pack_size": Decimal(size),
                                 "pack_cost": cost})
    return it


def count(db, loc, at, lines):
    s = CountSession(location_id=loc.id, counted_at=at)
    db.add(s)
    db.flush()
    for it, packs in lines:
        full = Decimal(int(packs))
        inv.set_count_line(db, s, it, full, Decimal(str(packs)) - full)
    inv.post_count(db, s)
    return s


def sales(db, loc, rows, period=WEEK):
    b = ImportBatch(location_id=loc.id, source="swype_file", dataset="item_sales")
    db.add(b)
    db.flush()
    for code, name, qty in rows:
        db.add(SwypeItemSales(location_id=loc.id, period_from=period[0], period_to=period[1], department="Liquor",
                              category="Cocktails", item_code=code, description=name, qty=qty, total_qty=qty,
                              amount=0, net_amount=0, import_batch_id=b.id))
    db.flush()


def row_for(report, it):
    return next(r for r in report["rows"] if r["item_id"] == it.id)


def test_ten_cocktails_at_75ml_should_use_one_bottle(db, location):
    vodka = item(db, "VOD", "Smirnoff Vodka")
    rec.save(db, "00000561", name="Moscow Mule", lines=[{"item_id": vodka.id, "qty": 75, "unit": "ml"}])
    sales(db, location, [("00000561", "Moscow Mule", 10)])
    count(db, location, SUN1, [(vodka, 3)])
    count(db, location, SUN2, [(vodka, 2)])
    r = row_for(variance.run(db, location.id), vodka)
    assert r["expected"] == Decimal(750)
    assert r["expected"] / r["pack_size"] == 1
    assert r["actual"] == Decimal(750)
    assert r["variance"] == 0 and r["status"] == "ok"
    assert r["contributions"][0]["sold"] == 10 and r["contributions"][0]["portion"] == 75


def test_off_by_30_percent_is_flagged(db, location):
    vodka = item(db, "VOD", "Smirnoff Vodka", cost=Decimal(30))
    gin = item(db, "GIN", "Gordon's Gin")
    rec.save(db, "00000561", name="Moscow Mule", lines=[{"item_id": vodka.id, "qty": 75, "unit": "ml"}])
    rec.save(db, "00000331_01", name="Gordon's Gin SNG", lines=[{"item_id": gin.id, "qty": 1, "unit": "oz"}])
    sales(db, location, [("00000561", "Moscow Mule", 10), ("00000331_01", "Gordon's Gin SNG", 20)])
    count(db, location, SUN1, [(vodka, 3), (gin, 2)])
    # vodka: 1.3 bottles gone vs 1 expected (+30 %); gin: 20 x 28.41 ml = 568 ml expected, 0.9 bottle gone
    count(db, location, SUN2, [(vodka, 1.7), (gin, 1.1)])
    rep = variance.run(db, location.id)
    v, g = row_for(rep, vodka), row_for(rep, gin)
    assert v["variance"] == Decimal(225) and v["variance_pct"] == 30 and v["status"] == "off"
    assert v["variance_cost"] == Decimal(9)
    assert g["status"] == "ok" and abs(g["variance_pct"] - Decimal("18.8")) < 1
    assert rep["rows"][0]["item_id"] == vodka.id  # flagged rows first


def test_spec_acceptance_breakage_is_explained(db, location):
    # spec 4.4: 3,000 ml -> 2,480 ml, 10 x 45 ml sold, 20 ml breakage
    vodka = item(db, "VOD", "Vodka", size=1000)
    rec.save(db, "V45", name="Vodka 45", lines=[{"item_id": vodka.id, "qty": 45, "unit": "ml"}])
    sales(db, location, [("V45", "Vodka 45", 10)])
    count(db, location, SUN1, [(vodka, 3)])
    inv.add_movement(db, location_id=location.id, item=vodka, kind="breakage_expired",
                     occurred_at=datetime(2026, 10, 22, tzinfo=timezone.utc), external_ref="drop", qty=Decimal(20))
    count(db, location, SUN2, [(vodka, 2.48)])
    r = row_for(variance.run(db, location.id), vodka)
    assert (r["expected"], r["actual"], r["should_use"], r["variance"]) == (450, 520, 470, 50)


def test_deliveries_count_as_received(db, location):
    vodka = item(db, "VOD", "Vodka")
    rec.save(db, "V", name="Vodka", lines=[{"item_id": vodka.id, "qty": 750, "unit": "ml"}])
    sales(db, location, [("V", "Vodka", 4)])
    count(db, location, SUN1, [(vodka, 1)])
    inv.add_movement(db, location_id=location.id, item=vodka, kind="delivery", packs=Decimal(6),
                     occurred_at=datetime(2026, 10, 22, tzinfo=timezone.utc), external_ref="INV-1")
    count(db, location, SUN2, [(vodka, 3)])
    r = row_for(variance.run(db, location.id), vodka)
    assert r["received"] == 4500 and r["actual"] == 3000 and r["status"] == "ok"


def test_unmapped_sales_and_gone_with_no_sales(db, location):
    vodka = item(db, "VOD", "Vodka")
    rec.save(db, "00000561", name="Moscow Mule", lines=[{"item_id": vodka.id, "qty": 75, "unit": "ml"}])
    sales(db, location, [("X1", "Mystery Drink", 7)])
    count(db, location, SUN1, [(vodka, 2)])
    count(db, location, SUN2, [(vodka, 1)])
    rep = variance.run(db, location.id)
    assert rep["unmapped"][0]["code"] == "X1" and rep["unmapped_qty"] == 7
    r = row_for(rep, vodka)
    assert r["variance_pct"] is None and r["status"] == "off"


def test_item_in_no_recipe_is_reported_not_assumed_sold(db, location):
    beer = item(db, "BUD", "Budweiser bottle", unit="each", size=24)
    sales(db, location, [("00000420", "Budweiser Btl", 30)])  # a Swype item is not the inventory item
    count(db, location, SUN1, [(beer, 3)])
    count(db, location, SUN2, [(beer, 1.5)])
    rep = variance.run(db, location.id)
    r = row_for(rep, beer)
    assert r["no_recipe"] and r["status"] == "no_recipe" and r["actual"] == 36 and r["should_use"] == 0
    assert rep["unmapped"][0]["code"] == "00000420"

    rec.save(db, "00000420", name="Budweiser Btl", lines=[{"item_id": beer.id, "qty": 1, "unit": "each"}])
    r = row_for(variance.run(db, location.id), beer)
    assert not r["no_recipe"] and r["should_use"] == 30 and r["variance"] == 6 and r["status"] == "ok"


def test_sales_coverage_is_reported(db, location):
    vodka = item(db, "VOD", "Vodka")
    sales(db, location, [("V", "Vodka", 1)], period=(date(2026, 10, 13), date(2026, 10, 19)))  # previous week
    count(db, location, SUN1, [(vodka, 1)])
    count(db, location, SUN2, [(vodka, 1)])
    rep = variance.run(db, location.id)
    assert rep["sales_periods"] == [] and len(rep["missing_sales_days"]) == 7


def test_recipe_versions_apply_from_their_date(db, location):
    vodka = item(db, "VOD", "Vodka")
    rec.save(db, "M", name="Mule", lines=[{"item_id": vodka.id, "qty": 1, "unit": "oz"}])
    rec.save(db, "M", name="Mule", lines=[{"item_id": vodka.id, "qty": 75, "unit": "ml"}],
             effective_from=date(2026, 10, 20))
    r = rec.load(db, "M")
    assert [ln.qty for ln in rec.version_at(r, date(2026, 10, 19)).lines] == [1]
    assert [ln.qty for ln in rec.version_at(r, date(2026, 10, 20)).lines] == [75]
    rec.save(db, "M", name="Mule", lines=[{"item_id": vodka.id, "qty": 60, "unit": "ml"}])  # correction
    r = rec.load(db, "M")
    assert len(r.versions) == 2 and r.versions[-1].lines[0].qty == 60


def test_oz_portion_of_item_counted_each_is_a_unit_issue(db, location):
    lime = item(db, "LIME", "Lime", unit="each", size=1)
    rec.save(db, "M", name="Mule", lines=[{"item_id": lime.id, "qty": 1, "unit": "oz"}])
    sales(db, location, [("M", "Mule", 5)])
    count(db, location, SUN1, [(lime, 10)])
    count(db, location, SUN2, [(lime, 5)])
    r = row_for(variance.run(db, location.id), lime)
    assert r["status"] == "unit_issue" and r["issues"]


def test_suggest_pours(db, location):
    gin = item(db, "GIN", "Gordon's Gin")
    red = item(db, "RED", "House Red")
    item(db, "SW", "Smirnoff Vodka", unit="each", size=1)
    sales(db, location, [("00000331", "Gordon's Gin", 3), ("00000331_01", "Gordon's Gin SNG", 2),
                         ("00000331_02", "Gordon's Gin DBL", 1), ("00000696", "House Red 6oz", 4),
                         ("00000699", "House Red Btl", 1), ("00000330_02", "Smirnoff Vodka DBL", 1),
                         ("00000561", "Moscow Mule", 9)])
    s = rec.suggest_pours(db, location.id, single_oz=Decimal(1), double_oz=Decimal(2), apply=True)
    got = {x["code"]: (x["qty"], x["unit"]) for x in s["ready"]}
    assert got == {"00000331": (1, "oz"), "00000331_01": (1, "oz"), "00000331_02": (2, "oz"),
                   "00000696": (6, "oz"), "00000699": (750, "ml")}
    assert [x["code"] for x in s["skipped"]] == ["00000330_02"]  # Smirnoff is counted in 'each'
    assert {x["code"]: x["matched_by"] for x in s["ready"]}["00000331_02"] == "bottle_recipe"
    assert rec.load(db, "00000331_02").versions[0].lines[0].qty == 2
    assert "00000561" not in got  # cocktails are never guessed


def test_api_round_trip(client, db, location):
    vodka = item(db, "VOD", "Vodka")
    db.commit()
    r = client.put("/api/recipes/00000561", json={"name": "Moscow Mule", "lines": [{"item_id": vodka.id, "qty": 2.5, "unit": "oz"}]})
    assert r.status_code == 200, r.text
    assert abs(float(r.json()["lines"][0]["stock_qty"]) - 71.03) < 0.01
    assert client.put("/api/recipes/X", json={"name": "X", "lines": []}).status_code == 400
    assert client.get("/api/variance").status_code == 400  # no counts yet
    sales(db, location, [("00000561", "Moscow Mule", 10)])
    count(db, location, SUN1, [(vodka, 2)])
    count(db, location, SUN2, [(vodka, 1)])
    db.commit()
    rep = client.get("/api/variance").json()
    assert rep["rows"][0]["name"] == "Vodka"
    m = client.get("/api/recipes/menu").json()
    assert m[0]["recipe"]["lines"][0]["item_name"] == "Vodka" and float(m[0]["sold"]) == 10
    assert len(client.get("/api/variance/periods").json()) == 1


@pytest.fixture(autouse=True)
def _tz(monkeypatch):
    monkeypatch.setattr(variance.settings, "timezone", "America/Vancouver")


def test_current_period_checklist(client):
    import datetime as _d
    r = client.get("/api/variance/current").json()
    assert r["opening"] is None and r["last_variance"] is None
    item = client.post("/api/items", json={"sku": "67", "name": "Smirnoff Red", "category": "Spirits", "stock_unit": "ml",
                                           "pack_name": "bottle", "pack_size": 750, "case_size": 12}).json()
    when = (_d.datetime.now(_d.timezone.utc) - _d.timedelta(days=4)).isoformat()
    s = client.post("/api/counts", json={"counted_at": when}).json()
    client.put(f"/api/counts/{s['id']}/lines", json=[{"item_id": item["id"], "packs_full": 1, "packs_partial": 0}])
    client.post(f"/api/counts/{s['id']}/post")
    r = client.get("/api/variance/current").json()
    assert r["opening"]["id"] == s["id"] and r["days"] in (3, 4) and len(r["missing_sales_days"]) == r["days"]
    assert r["deliveries"] == 0 and r["unmapped"] == 0 and r["draft"] is None


def test_expected_left_is_last_count_plus_deliveries_minus_sold(db, location):
    from app.routers.counts import _packs
    vodka = item(db, "VOD", "Smirnoff Vodka")
    beer = item(db, "KOK", "Kokanee", unit="each", size=24)
    idle = item(db, "RUM", "Bacardi")
    rec.save(db, "00000561", name="Moscow Mule", lines=[{"item_id": vodka.id, "qty": 75, "unit": "ml"}])
    rec.save(db, "00000900", name="Kokanee Btl", lines=[{"item_id": beer.id, "qty": 1, "unit": "each"}])
    sales(db, location, [("00000561", "Moscow Mule", 13), ("00000900", "Kokanee Btl", 30), ("X", "No recipe", 5)])
    count(db, location, SUN1, [(vodka, 3), (beer, 2), (idle, 1.5)])
    left = variance.expected_left(db, location.id, SUN2)
    assert left[vodka.id] == Decimal(2250 - 975)  # 1.7 bottles
    assert left[beer.id] == Decimal(18)
    assert left[idle.id] == Decimal(1125)
    assert _packs(vodka, left[vodka.id]) == (1, Decimal("0.7"))
    assert _packs(beer, left[beer.id]) == (0, Decimal("0.75"))
    assert variance.expected_left(db, location.id, SUN1) == {}  # nothing posted before the first count
    gin = item(db, "GIN", "Gordon's Gin")  # not in the last count, only delivered
    for it, ref in ((vodka, "INV-1"), (gin, "INV-1")):
        inv.add_movement(db, location_id=location.id, item=it, kind="delivery", packs=Decimal(2),
                         occurred_at=datetime(2026, 10, 22, tzinfo=timezone.utc), external_ref=ref)
    inv.add_movement(db, location_id=location.id, item=vodka, kind="delivery", packs=Decimal(5),
                     occurred_at=SUN1, external_ref="INV-0")  # at the last count: already in it
    left = variance.expected_left(db, location.id, SUN2)
    assert left[vodka.id] == Decimal(2250 + 1500 - 975) and left[gin.id] == Decimal(1500)
    sales(db, location, [("00000561", "Moscow Mule", 100)], period=(date(2026, 10, 20), date(2026, 10, 21)))
    assert variance.expected_left(db, location.id, SUN2)[vodka.id] == 0  # never below zero



def test_from_to_spans_counts_in_between(client, db, location):
    from datetime import timedelta
    vodka = item(db, "VOD", "Smirnoff Vodka", cost=Decimal(30))
    rec.save(db, "00000561", name="Moscow Mule", lines=[{"item_id": vodka.id, "qty": 75, "unit": "ml"}])
    week2 = (WEEK[0] + timedelta(days=7), WEEK[1] + timedelta(days=7))
    sales(db, location, [("00000561", "Moscow Mule", 10)])
    sales(db, location, [("00000561", "Moscow Mule", 10)], period=week2)
    a = count(db, location, SUN1, [(vodka, 3)])
    b = count(db, location, SUN2, [(vodka, 5)])  # a bad middle count: ignored across the span
    c = count(db, location, SUN2 + timedelta(days=7), [(vodka, 1)])
    r = variance.run(db, location.id, a.id, c.id)
    v = row_for(r, vodka)
    assert r["between_counts"] == 1
    assert v["opening"] == 2250 and v["closing"] == 750 and v["expected"] == 1500 and v["variance"] == 0
    assert variance.run(db, location.id, a.id, b.id)["between_counts"] == 0
    with pytest.raises(variance.VarianceError, match="later"):
        variance.run(db, location.id, c.id, a.id)
    with pytest.raises(variance.VarianceError, match="different"):
        variance.run(db, location.id, a.id, a.id)
    db.commit()  # the API's sessions share this connection; their rollback would drop uncommitted rows
    page = client.get("/api/variance/counts?limit=2").json()
    assert page["total"] == 3 and [x["id"] for x in page["counts"]] == [c.id, b.id]
    assert [x["id"] for x in client.get("/api/variance/counts?limit=2&offset=2").json()["counts"]] == [a.id]
    assert client.get(f"/api/variance?opening={c.id}&closing={a.id}").status_code == 400
