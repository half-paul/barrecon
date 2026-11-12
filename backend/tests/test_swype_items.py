from decimal import Decimal

from app.models import InventoryItem, SwypeMenuItem
from app.services import inventory as inv
from app.services import recipes as rec
from app.services import swype_item_sales, swype_items
from tests.test_swype_item_sales import report

CSV = ("﻿Item Code,Category Code,Item Desc1,Status\r\n"
       "00000331,000020,Gordon's Gin,A\r\n"
       "00000331_01,000020,Gordon's Gin SNG,A\r\n"
       "00000005,000004,Shrooms on Toast,A\r\n").encode()


def _gin(db):
    return inv.upsert_item(db, {"sku": "12345", "name": "Gordons Gin", "category": "Spirits", "stock_unit": "ml",
                                "pack_size": Decimal(750)})[0]


def test_csv_fills_the_swype_menu_and_never_touches_inventory(db, location):
    _gin(db)
    r = swype_items.apply_csv(db, CSV, "export_items.csv", location.id)
    assert (r["new"], r["renamed"], r["unchanged"]) == (3, 0, 0)
    assert {m.code for m in db.query(SwypeMenuItem)} == {"00000331", "00000331_01", "00000005"}
    assert [it.sku for it in db.query(InventoryItem)] == ["12345"]

    r2 = swype_items.apply_csv(db, CSV.replace(b"Shrooms on Toast", b"Shrooms Toast") + b"00000006,000004,Toast Again,A\r\n",
                               "export_items2.csv", location.id)
    assert (r2["new"], r2["renamed"], r2["unchanged"]) == (1, 1, 2)
    assert db.get(SwypeMenuItem, (db.info["business_id"], "00000005")).name == "Shrooms Toast"


def test_sales_import_adds_menu_items_not_inventory_items(db, location):
    off = inv.upsert_item(db, {"sku": "999", "name": "Lager 20oz", "stock_unit": "each", "pack_size": Decimal(1),
                               "is_active": False})[0]
    pv = swype_item_sales.preview(report(), db)
    assert pv["menu"]["new"] == 3 and db.query(SwypeMenuItem).count() == 0  # preview writes nothing
    r = swype_item_sales.apply(db, report(), "wk.xlsx", location.id)
    assert r["menu"]["new"] == 3
    assert db.get(SwypeMenuItem, (db.info["business_id"], "00000400")).category == "Draft Beer"
    assert db.query(InventoryItem).count() == 1 and not off.is_active  # same name, still not linked or switched on


def test_codes_using_lists_swype_items_whose_recipes_take_the_item(client, db, location):
    gin = _gin(db)
    tonic = inv.upsert_item(db, {"sku": "T1", "name": "Tonic", "stock_unit": "ml", "pack_size": Decimal(1000)})[0]
    rec.save(db, "00000331_01", name="Gordon's Gin SNG", lines=[{"item_id": gin.id, "qty": 1, "unit": "oz"}])
    rec.save(db, "00000612", name="G&T", lines=[{"item_id": gin.id, "qty": 1.5, "unit": "oz"},
                                                 {"item_id": tonic.id, "qty": 4, "unit": "oz"}])
    db.commit()
    uses = {i: [c for c, _ in cs] for i, cs in rec.codes_using(db, [gin.id, tonic.id]).items()}
    assert uses == {gin.id: ["00000331_01", "00000612"], tonic.id: ["00000612"]}
    # the Stock Items page keeps inventory items and Swype items apart
    assert all("swype_codes" not in i for i in client.get("/api/items?include_inactive=true").json())


def test_rename_and_switch_off_survive_import_but_a_sale_switches_back_on(client, db, location):
    swype_item_sales.apply(db, report(), "wk1.xlsx", location.id)
    r = client.patch("/api/swype-items/00000400", json={"name": "House Lager 20oz", "is_active": False})
    assert r.status_code == 200 and r.json()["name"] == "House Lager 20oz" and not r.json()["is_active"]
    client.patch("/api/swype-items/00000330_01", json={"is_active": False})

    # next week: lager sells again, the vodka single does not
    swype_item_sales.apply(db, report("From 10/26/2026 to 11/01/2026\nAll Price Type"),
                           "wk2.xlsx", location.id)
    items = {i["code"]: i for i in client.get("/api/swype-items").json()}
    assert items["00000400"]["is_active"] and items["00000400"]["name"] == "House Lager 20oz"
    assert items["00000400"]["swype_name"] == "Lager 20oz" and items["00000400"]["sold"] == 20
    assert {r["code"]: r["name"] for r in rec.menu(db, location.id)}["00000400"] == "House Lager 20oz"

    # blank name goes back to Swype's; the code cannot be changed
    r = client.patch("/api/swype-items/00000400", json={"name": " ", "code": "X"})
    assert r.json()["name"] == "Lager 20oz" and not r.json()["renamed"] and r.json()["code"] == "00000400"
    assert client.patch("/api/swype-items/nope", json={"is_active": True}).status_code == 404


def test_unsold_line_does_not_switch_an_item_back_on(db, location):
    swype_item_sales.apply(db, report(), "wk1.xlsx", location.id)
    swype_items.update(db, "00000400", is_active=False)
    db.commit()
    swype_item_sales.apply(db, report("From 10/26/2026 to 11/01/2026\nAll Price Type", draft_qty=0),
                           "wk2.xlsx", location.id)
    assert not db.get(SwypeMenuItem, (db.info["business_id"], "00000400")).is_active


def test_suggest_item_follows_bottle_recipe_then_name(db, location):
    gin = _gin(db)
    other = inv.upsert_item(db, {"sku": "G2", "name": "London Dry", "stock_unit": "ml", "pack_size": Decimal(750)})[0]
    rec.save(db, "00000331", name="Gordon's Gin", lines=[{"item_id": other.id, "qty": 1, "unit": "oz"}])
    by_key = swype_items.items_by_key(db)
    bases = rec.base_recipe_items(rec.load_all(db))
    assert swype_items.suggest_item("00000331_02", "Gordon's Gin DBL", by_key, bases) == (other, "bottle_recipe")
    assert swype_items.suggest_item("00000999", "Gordons Gin Double", by_key, bases) == (gin, "name")
    assert swype_items.suggest_item("00000612", "Caesar", by_key, bases) is None


def test_sold_counts_only_periods_overlapping_the_range(client, db, location):
    swype_item_sales.apply(db, report(), "wk1.xlsx", location.id)  # 10/19-10/25, 10 lager
    swype_item_sales.apply(db, report("From 10/26/2026 to 11/01/2026\nAll Price Type", draft_qty=7),
                           "wk2.xlsx", location.id)

    def lager(qs=""):
        return {i["code"]: i for i in client.get(f"/api/swype-items{qs}").json()}["00000400"]

    assert lager()["sold"] == 17
    assert lager("?from=2026-10-26")["sold"] == 7
    assert lager("?to=2026-10-25")["sold"] == 10
    assert lager("?from=2026-10-25&to=2026-10-26")["sold"] == 17  # touches both weeks
    out = lager("?from=2026-11-02")
    assert out["sold"] == 0 and out["last_sold"] == "2026-11-01"
    assert client.get("/api/swype-items?from=2026-11-02&to=2026-11-01").status_code == 422


def test_items_unsold_for_a_month_are_switched_off(db, location):
    import datetime as _d
    swype_items.apply_csv(db, CSV, "export_items.csv", location.id)
    db.get(SwypeMenuItem, (db.info["business_id"], "00000005")).created_at = _d.datetime(2026, 8, 1, tzinfo=_d.timezone.utc)  # long on the menu
    db.commit()
    sep = swype_item_sales.apply(db, report("From 09/01/2026 to 09/30/2026\nAll Price Type"), "sep.xlsx", location.id)
    assert sep["menu"]["deactivated"] == 0  # sales do not reach back a month yet
    octo = swype_item_sales.apply(db, report("From 10/01/2026 to 10/31/2026\nAll Price Type", draft_qty=0),
                                  "oct.xlsx", location.id)
    assert octo["menu"]["deactivated"] == 2
    active = {m.code: m.is_active for m in db.query(SwypeMenuItem)}
    assert active["00000400"] is False  # in September's sales, none sold in October
    assert active["00000005"] is False  # on the menu since August, never sold
    assert active["00000331"] is True  # added to the menu this month: waits for a first sale
    assert active["00000330_01"] and active["00000330_02"]

    nov = swype_item_sales.apply(db, report("From 11/01/2026 to 11/01/2026\nAll Price Type"), "nov.xlsx", location.id)
    assert nov["menu"]["reactivated"] == 1 and db.get(SwypeMenuItem, (db.info["business_id"], "00000400")).is_active
