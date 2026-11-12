"""Recipe lines poured from any one of several items ("Mystery Wine": 6 oz of Box A or Box B or Box C), and the
pools they make on the variance report."""
from datetime import date
from decimal import Decimal

import pytest

from app.services import recipes as rec
from app.services import variance
from app.services.recipe_import import import_recipes

from test_recipe_import import workbook
from test_variance import SUN1, SUN2, count, item, row_for, sales


def boxes(db, size=4000):
    return [item(db, f"BOX{c}", f"Box {c}", size=size) for c in "ABC"]


def test_line_with_options_saves_and_reads_back(db):
    a, b, c = boxes(db)
    r = rec.save(db, "00000700", name="Mystery Wine",
                 lines=[{"item_ids": [a.id, b.id, a.id, c.id], "qty": 6, "unit": "oz"}])
    ln = r.versions[-1].lines[0]
    assert ln.item_ids == [a.id, b.id, c.id]  # the same item twice is once
    out = rec.recipe_out(rec.load(db, "00000700"), Decimal("28.4131"))
    line = out["lines"][0]
    assert line["item_id"] == a.id and [x["item_name"] for x in line["items"]] == ["Box A", "Box B", "Box C"]
    assert all(x["stock_qty"] == Decimal("170.4786") for x in line["items"])
    # saving again replaces the options
    rec.save(db, "00000700", name="Mystery Wine", lines=[{"item_ids": [c.id], "qty": 6, "unit": "oz"}])
    assert rec.load(db, "00000700").versions[-1].lines[0].item_ids == [c.id]
    assert rec.codes_using(db, [c.id]) == {c.id: [("00000700", "Mystery Wine")]}


def test_api_takes_item_ids(client, db):
    a, b, _ = boxes(db)
    db.commit()
    res = client.put("/api/recipes/00000700", json={"name": "Mystery Wine",
                                                    "lines": [{"item_ids": [a.id, b.id], "qty": 150, "unit": "ml"}]})
    assert res.status_code == 200, res.text
    assert [x["sku"] for x in res.json()["lines"][0]["items"]] == ["BOXA", "BOXB"]
    res = client.put("/api/recipes/00000701", json={"name": "Box A glass", "lines": [{"item_id": a.id, "qty": 150, "unit": "ml"}]})
    assert res.status_code == 200 and len(res.json()["lines"][0]["items"]) == 1
    assert client.put("/api/recipes/00000702", json={"name": "x", "lines": [{"item_ids": [], "qty": 1}]}).status_code == 400


def test_chained_options_make_one_pool(db, location):
    """The dashboard sample: Mystery Wine (A or B) and Wine Special (B or C) share Box B, so A, B and C are one pool."""
    a, b, c = boxes(db)
    tonic = item(db, "TON", "Tonic", size=1000)
    rec.save(db, "00000700", name="Mystery Wine", lines=[{"item_ids": [a.id, b.id], "qty": 150, "unit": "ml"}])
    rec.save(db, "00000701", name="Wine Special", lines=[{"item_ids": [b.id, c.id], "qty": 150, "unit": "ml"},
                                                         {"item_id": tonic.id, "qty": 100, "unit": "ml"}])
    sales(db, location, [("00000700", "Mystery Wine", 20), ("00000701", "Wine Special", 10)])
    count(db, location, SUN1, [(a, 1), (b, 1), (c, 1), (tonic, 5)])
    count(db, location, SUN2, [(a, 0.5), (b, 0.25), (c, 1), (tonic, 4)])  # 2000 + 3000 ml gone; 4500 sold
    rep = variance.run(db, location.id)
    assert {r["item_id"] for r in rep["rows"]} == {tonic.id}  # pool items are not per-item rows
    assert row_for(rep, tonic)["expected"] == 1000 and row_for(rep, tonic)["status"] == "ok"
    [p] = rep["pools"]
    assert p["name"] == "Box A, Box B and Box C" and p["unit"] == "ml"
    assert p["expected"] == 4500 and p["actual"] == 5000 and p["variance"] == 500
    assert p["opening"] == 12000 and p["closing"] == 7000
    assert p["status"] == "ok"  # 11 % is under the 30 % threshold
    assert [(x["name"], x["sold"], x["used"], x["uses"]) for x in p["recipes"]] == [
        ("Mystery Wine", 20, 3000, ["Box A", "Box B"]), ("Wine Special", 10, 1500, ["Box B", "Box C"])]
    assert [m["actual"] for m in p["members"]] == [2000, 3000, 0]
    assert rep["pool_summary"]["ok"] == 1


def test_item_sold_alone_counts_toward_its_pool(db, location):
    a, b, _ = boxes(db)
    rec.save(db, "00000700", name="Mystery Wine", lines=[{"item_ids": [a.id, b.id], "qty": 6, "unit": "oz"}])
    rec.save(db, "00000701", name="Box A glass", lines=[{"item_id": a.id, "qty": 150, "unit": "ml"}])
    sales(db, location, [("00000700", "Mystery Wine", 10), ("00000701", "Box A glass", 4)])
    count(db, location, SUN1, [(a, 1), (b, 1)])
    count(db, location, SUN2, [(a, 0), (b, 1)])
    [p] = variance.run(db, location.id)["pools"]
    oz6 = Decimal(6) * Decimal("28.4131")
    assert p["expected"] == oz6 * 10 + 600
    assert {x["name"]: x["uses"] for x in p["recipes"]} == {"Mystery Wine": ["Box A", "Box B"], "Box A glass": ["Box A"]}
    assert p["status"] == "off"  # 4000 gone vs ~2305 sold


def test_pool_converts_mixed_units(db, location):
    a = item(db, "BOXA", "Box A", unit="l", size=4)
    b = item(db, "BOXB", "Box B", unit="ml", size=4000)
    rec.save(db, "00000700", name="Mystery Wine", lines=[{"item_ids": [a.id, b.id], "qty": 200, "unit": "ml"}])
    sales(db, location, [("00000700", "Mystery Wine", 10)])
    count(db, location, SUN1, [(a, 1), (b, 1)])
    count(db, location, SUN2, [(a, 0.75), (b, 0.75)])
    [p] = variance.run(db, location.id)["pools"]
    assert p["unit"] == "ml" and p["actual"] == 2000 and p["expected"] == 2000 and p["variance"] == 0


def test_pool_counted_at_one_end_only(db, location):
    a, b, _ = boxes(db)
    rec.save(db, "00000700", name="Mystery Wine", lines=[{"item_ids": [a.id, b.id], "qty": 150, "unit": "ml"}])
    sales(db, location, [("00000700", "Mystery Wine", 10)])
    count(db, location, SUN1, [(a, 1), (b, 1)])
    count(db, location, SUN2, [(a, 0.5)])
    [p] = variance.run(db, location.id)["pools"]
    assert p["status"] == "missing_closing" and p["actual"] is None


def test_options_only_pool_while_in_effect(db, location):
    a, b, _ = boxes(db)
    rec.save(db, "00000700", name="Mystery Wine", lines=[{"item_ids": [a.id, b.id], "qty": 150, "unit": "ml"}])
    rec.save(db, "00000700", name="Mystery Wine", lines=[{"item_id": a.id, "qty": 150, "unit": "ml"}],
             effective_from=date(2026, 10, 1))  # before the window: only Box A now
    sales(db, location, [("00000700", "Mystery Wine", 10)])
    count(db, location, SUN1, [(a, 1), (b, 1)])
    count(db, location, SUN2, [(a, 0.625), (b, 1)])
    rep = variance.run(db, location.id)
    assert rep["pools"] == []
    assert row_for(rep, a)["expected"] == 1500 and row_for(rep, a)["status"] == "ok"


def test_expected_left_splits_by_stock_on_hand(db, location):
    a, b, c = boxes(db)
    rec.save(db, "00000700", name="Mystery Wine", lines=[{"item_ids": [a.id, b.id, c.id], "qty": 100, "unit": "ml"}])
    sales(db, location, [("00000700", "Mystery Wine", 30)])
    count(db, location, SUN1, [(a, 1), (b, 0.5)])  # Box C: none on hand, so none poured from it
    left = variance.expected_left(db, location.id, SUN2)
    assert left[a.id] == 4000 - 2000 and left[b.id] == 2000 - 1000
    assert left.get(c.id, 0) == 0


def test_import_pipe_separated_skus(db):
    boxes(db)
    data = workbook([("00000700", "Mystery Wine", "House wine", "BOXA | BOXB|BOXC", 6, "oz", None)])
    res = import_recipes(db, data, "menu.xlsx")
    assert res.status != "rejected", [r.message for r in res.rows]
    ln = rec.load(db, "00000700").versions[-1].lines[0]
    assert [it.sku for it in ln.items] == ["BOXA", "BOXB", "BOXC"] and ln.qty == 6
    bad = import_recipes(db, workbook([("00000701", "x", "y", "BOXA|NOPE", 1, "oz", None)]), "bad.xlsx")
    assert bad.status == "rejected" and "NOPE" in bad.rows[0].message


@pytest.mark.parametrize("what", [["items", "recipes"]])
def test_business_copy_keeps_options(db, what):
    from app.routers.businesses import _copy_rows
    from app.models import Location, Recipe
    a, b, _ = boxes(db)
    rec.save(db, "00000700", name="Mystery Wine", lines=[{"item_ids": [a.id, b.id], "qty": 150, "unit": "ml"}])
    db.flush()
    src = a.location_id
    dst = Location(name="Second bar", tenant_id=db.get(Location, src).tenant_id)
    db.add(dst)
    db.flush()
    db.info["also_business"] = dst.id
    _copy_rows(db, src, dst.id, what, {"items": 0, "groups": 0, "recipes": 0})
    db.flush()
    db.info.pop("also_business")
    new = db.query(Recipe).filter(Recipe.location_id == dst.id).execution_options(unscoped=True).one()
    assert len(new.versions[0].lines[0].item_ids) == 2
    assert set(new.versions[0].lines[0].item_ids).isdisjoint({a.id, b.id})
