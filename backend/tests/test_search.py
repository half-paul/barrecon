from decimal import Decimal

from app.models import CountLine, Delivery, DeliveryLine, InventoryItem
from app.services import legacy_import, swype_item_sales, swype_items
from app.services import recipes as rec
from tests.conftest import FIXTURE
from tests.test_swype_item_sales import report
from tests.test_swype_items import CSV
from tests.test_users import _login, _make


def test_search_spans_items_groups_counts_movements_imports(client, db, location):
    legacy_import.apply(db, FIXTURE.read_bytes(), "legacy.xlsx", location.id)
    swype_items.apply_csv(db, CSV, "export_items.csv", location.id)
    swype_item_sales.apply(db, report(), "wk.xlsx", location.id)
    gin = db.query(InventoryItem).filter_by(name="Gordons Gin").one()
    vodka = db.query(InventoryItem).filter(InventoryItem.name.like("Smirnoff%")).first()
    rec.save(db, "00000331_01", name="Gordon's Gin SNG", lines=[{"item_id": gin.id, "qty": 1, "unit": "oz"}])
    rec.save(db, "00000330_01", name="Smirnoff Vodka SNG", lines=[{"item_id": vodka.id, "qty": 1, "unit": "oz"}])
    rec.save(db, "00000330_02", name="Smirnoff Vodka DBL", lines=[{"item_id": vodka.id, "qty": 2, "unit": "oz"}])
    db.commit()

    r = client.get("/api/search", params={"q": "gordon"}).json()
    assert r["items"][0]["name"] == "Gordons Gin" and r["items"][0]["swype_codes"] == ["00000331_01"]

    # a Swype item # finds the inventory items its recipe uses; last week's sales summed over those codes
    r = client.get("/api/search", params={"q": "00000330_02"}).json()
    assert [i["id"] for i in r["items"]] == [vodka.id] and r["items"][0]["sold_last_week"] == 6

    assert any(g["name"] == "Draft beer" for g in client.get("/api/search", params={"q": "draft"}).json()["groups"])
    assert client.get("/api/search", params={"q": "oct 2026"}).json()["counts"]  # legacy count notes
    assert client.get("/api/search", params={"q": "legacy"}).json()["movements"]
    assert client.get("/api/search", params={"q": "export_items"}).json()["imports"]
    # every word must match
    assert client.get("/api/search", params={"q": "gordon lager"}).json()["items"] == []
    assert client.get("/api/search", params={"q": " "}).json()["items"] == []


def _portal(db, location):
    legacy_import.apply(db, FIXTURE.read_bytes(), "legacy.xlsx", location.id)
    swype_items.apply_csv(db, CSV, "export_items.csv", location.id)
    swype_item_sales.apply(db, report(), "wk.xlsx", location.id)
    gin = db.query(InventoryItem).filter_by(name="Gordons Gin").one()
    rec.save(db, "00000331_01", name="Gordon's Gin SNG", lines=[{"item_id": gin.id, "qty": 1, "unit": "oz"}],
             steps="Rocks glass, lime wedge", notes="Pre-batched for the zebra party")
    return gin


def test_search_finds_swype_items_recipes_and_notes(client, db, location):
    _portal(db, location)
    db.query(CountLine).first().note = "bottle cracked by walrus"
    delivery = Delivery(location_id=location.id, store="BC Liquor", receipt_no="R-77", note="short one case, quokka to credit")
    delivery.lines = [DeliveryLine(text="SMIRNOFF VODKA 1.14L", receipt_sku="123"), DeliveryLine(text="SMIRNOFF VODKA 750", receipt_sku="124")]
    db.add(delivery)
    db.commit()
    find = lambda q: client.get("/api/search", params={"q": q}).json()

    # a Swype item on its own, with or without a recipe
    r = find("00000330_02")
    assert r["swype_items"][0]["code"] == "00000330_02" and r["swype_items"][0]["sold_last_week"] == 2
    assert r["swype_items"][0]["has_recipe"] is False
    assert find("00000331_01")["swype_items"][0]["has_recipe"] is True

    # a recipe by name, by what's in it, by its steps and by its notes
    assert find("gordon's")["recipes"][0]["code"] == "00000331_01"
    assert find("gordons gin")["recipes"][0]["ingredients"] == ["Gordons Gin"]
    assert "lime wedge" in find("lime wedge")["recipes"][0]["snippet"]
    assert "zebra" in find("zebra")["recipes"][0]["snippet"]
    assert find("zebra lager")["recipes"] == []

    assert find("walrus")["counts"][0]["note"] == "bottle cracked by walrus"
    # a receipt by store, note and printed line; two matching lines still give one hit
    assert find("bc liquor")["deliveries"][0]["id"] == delivery.id
    assert find("quokka")["deliveries"][0]["receipt_no"] == "R-77"
    assert [x["id"] for x in find("smirnoff")["deliveries"]] == [delivery.id]


def test_search_only_returns_areas_the_user_can_view(client, db, location):
    _portal(db, location)
    db.commit()
    _make(client, "sam", {"counts": "view"})
    r = _login("sam", "pass-word-1").get("/api/search", params={"q": "gordon"}).json()
    assert r["recipes"] == [] and r["swype_items"] == [] and r["items"] == [] and r["issues"] == []
    _make(client, "kit", {"recipes": "view"})
    r = _login("kit", "pass-word-1").get("/api/search", params={"q": "zebra"}).json()
    assert r["recipes"] and r["items"] == []


def test_search_limit_is_per_section_and_capped(client, db, location):
    _portal(db, location)
    db.commit()
    find = lambda **p: client.get("/api/search", params={"q": "legacy", **p}).json()["movements"]
    assert len(find()) == 8
    assert len(find(limit=500)) > 50
    assert len(find(limit=100000)) == len(find(limit=500))
