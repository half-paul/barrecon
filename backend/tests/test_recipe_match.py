from datetime import date
from decimal import Decimal

import pytest

from app.models import ImportBatch, SwypeItemSales
from app.seed import default_location
from app.services import recipe_match as rm


def item(client, sku, name, group, unit="ml", size=750):
    r = client.post("/api/items", json={"sku": sku, "name": name, "category": group, "stock_unit": unit, "pack_size": size})
    assert r.status_code in (200, 201), r.text
    return r.json()


def sold(db, *rows):
    loc = default_location(db).id
    batch = ImportBatch(location_id=loc, source="swype_file", dataset="item_sales", file_name="t.xlsx", file_sha256="x")
    db.add(batch)
    db.flush()
    for code, name, dept, cat, qty in rows:
        db.add(SwypeItemSales(location_id=loc, period_from=date(2026, 10, 5), period_to=date(2026, 10, 5), item_code=code,
                              description=name, department=dept, category=cat, qty=Decimal(qty), total_qty=Decimal(qty),
                              amount=Decimal(0), net_amount=Decimal(0), import_batch_id=batch.id))
    db.commit()


def queue(client):
    return {r["name"]: r for r in client.get("/api/recipes/match").json()["rows"]}


@pytest.fixture()
def stock(client):
    return {n: item(client, s, n, g, *a) for s, n, g, *a in [
        ("1", "Red Truck Lager", "Draft beer", "ml", 50000), ("2", "Guiness", "Draft beer", "ml", 50000),
        ("3", "Red Truck Lite", "Bottled beer", "each", 1), ("4", "Jager", "Spirits"), ("5", "Smirnoff Red", "Spirits"),
        ("6", "Smirnoff Vanilla", "Spirits"), ("7", "Gordons Gin", "Spirits"), ("8", "Laurent Miquel Red", "Wine"),
        ("9", "Glenffiddich 12", "Scotch & whisky"), ("10", "Main St Pilsner", "Draft beer", "ml", 50000),
        ("11", "Main St Kingpin", "Draft beer", "ml", 50000), ("12", "Rewind lazer lite lager", "Draft beer", "ml", 50000),
        ("13", "Cazadores Blanco", "Spirits"), ("14", "Casamigos Blanco", "Spirits"), ("15", "Casamigos Reposado", "Spirits"),
        ("16", "Phillips Blue Buck", "Draft beer", "ml", 50000), ("17", "Phillips Dino Sour", "Draft beer", "ml", 50000)]}


def test_group_gate_keeps_lookalikes_apart(client, db, stock):
    sold(db, ("a", "RT Lager", "Liquor", "Draft Beer", 5), ("b", "Happy Red 9oz", "Liquor", "Red Wine", 5),
         ("c", "Happy Red Truck", "Liquor", "Draft Beer", 5))
    q = queue(client)
    assert q["RT Lager"]["lines"][0]["name"] == "Red Truck Lager"
    assert q["Happy Red Truck"]["lines"][0]["name"] == "Red Truck Lager"
    assert q["Happy Red 9oz"]["lines"][0]["name"] == "Laurent Miquel Red" and q["Happy Red 9oz"]["how"] == "house"


def test_portions(client, db, stock):
    sold(db, ("a", "Happy Guinness", "Liquor", "Draft Beer", 5), ("b", "RT Lager", "Liquor", "Draft Beer", 5),
         ("c", "RT Lager Gls", "Liquor", "Draft Beer", 5), ("h", "Spec Guinness", "Liquor", "Smaller Spec Drinks", 5), ("d", "Red Truck Lite Lager", "Liquor", "Bottled Beer", 5),
         ("e", "Happy Gin DBL", "Liquor", "Brunch Drinks", 5), ("f", "House Red 6oz", "Liquor", "Red Wine", 5),
         ("g", "Happy Vodka", "Liquor", "Brunch Drinks", 5))
    q = queue(client)
    portion = lambda n: (Decimal(str(q[n]["lines"][0]["qty"])), q[n]["lines"][0]["unit"])
    assert portion("Happy Guinness") == (16, "oz") and portion("RT Lager") == (20, "oz")
    assert portion("RT Lager Gls") == (20, "oz") and q["RT Lager Gls"]["clear"]
    assert portion("Spec Guinness") == (16, "oz")
    assert portion("Red Truck Lite Lager") == (1, "each")
    assert portion("Happy Gin DBL") == (2, "oz") and portion("House Red 6oz") == (6, "oz")
    assert portion("Happy Vodka") == (1, "oz") and not q["Happy Vodka"]["clear"]  # no size in the name


def test_ties_and_spelling(client, db, stock):
    sold(db, ("a", "Main st. gls", "Liquor", "Draft Beer", 5), ("b", "Glenfiddich 12 Double Premium", "Liquor", "Scotch & Irish", 5),
         ("c", "Jager Bomb", "Liquor", "Booze", 5), ("d", "Smirnoff Vodka DBL", "Liquor", "Booze", 5))
    q = queue(client)
    assert {c["name"] for c in q["Main st. gls"]["candidates"]} == {"Main St Pilsner", "Main St Kingpin"}
    assert not q["Main st. gls"]["clear"]
    assert q["Glenfiddich 12 Double Premium"]["clear"]
    assert not q["Jager Bomb"]["clear"]
    assert q["Smirnoff Vodka DBL"]["lines"][0]["name"] == "Smirnoff Red"


def test_a_generic_word_or_a_shared_brand_is_not_a_match(client, db, stock):
    sold(db, ("a", "Brunch Lager", "Liquor", "Draft Beer", 5), ("b", "Cazadores Repo Single Premium", "Liquor", "Booze", 5),
         ("c", "Casamigos Repo Single Delux", "Liquor", "Booze", 5), ("d", "Phillips Sour", "Liquor", "Draft Beer", 5))
    q = queue(client)
    assert q["Brunch Lager"]["lines"] == []
    assert not q["Cazadores Repo Single Premium"]["clear"]
    assert q["Casamigos Repo Single Delux"]["lines"][0]["name"] == "Casamigos Reposado"
    assert q["Phillips Sour"]["lines"][0]["name"] == "Phillips Dino Sour"


def test_shortest_name_wins_a_tie(client, db, stock):
    item(client, "20", "Coke", "Pop", "each", 1)
    item(client, "21", "Diet Coke", "Pop", "each", 1)
    sold(db, ("a", "Coke", "Beverage", "Pop", 5), ("b", "Diet Coke", "Beverage", "Pop", 5))
    q = queue(client)
    assert q["Coke"]["clear"] and q["Coke"]["lines"][0]["name"] == "Coke"
    assert q["Diet Coke"]["clear"] and q["Diet Coke"]["lines"][0]["name"] == "Diet Coke"


def test_nothing_saved_until_approved_then_the_name_teaches(client, db, stock):
    sold(db, ("a", "Happy Lager", "Liquor", "Draft Beer", 5), ("b", "Spec Lager", "Liquor", "Draft Beer", 2))
    q = queue(client)
    assert q["Happy Lager"]["lines"] == [] and client.get("/api/recipes/a").status_code == 404
    r = client.post("/api/recipes/match/approve", json={"rows": [
        {"code": "a", "name": "Happy Lager", "lines": [{"item_id": stock["Red Truck Lager"]["id"], "qty": 20, "unit": "oz"}]}]})
    assert r.json() == {"saved": 1}
    q = queue(client)
    assert "Happy Lager" not in q and q["Spec Lager"]["how"] == "approved"
    assert q["Spec Lager"]["lines"][0]["name"] == "Red Truck Lager"


def test_approve_is_all_or_nothing_and_keeps_existing_recipes(client, db, stock):
    sold(db, ("a", "Corona", "Liquor", "Bottled Beer", 5), ("b", "Stella", "Liquor", "Bottled Beer", 5))
    gin = {"item_id": stock["Gordons Gin"]["id"], "qty": 1, "unit": "oz"}
    r = client.post("/api/recipes/match/approve", json={"rows": [
        {"code": "a", "name": "Corona", "lines": [gin]}, {"code": "b", "name": "Stella", "lines": []}]})
    assert r.status_code == 400 and client.get("/api/recipes/a").status_code == 404
    client.put("/api/recipes/a", json={"name": "Corona", "lines": [gin]})
    r = client.post("/api/recipes/match/approve", json={"rows": [{"code": "a", "name": "Corona", "ignored": True}]})
    assert r.json() == {"saved": 0} and not client.get("/api/recipes/a").json()["ignored"]


def test_cocktail_copies_its_namesake(client, db, stock):
    sold(db, ("a", "Happy Shaft", "Liquor", "Cocktails", 5), ("b", "Shaft", "Liquor", "Cocktails", 5))
    lines = [{"item_id": stock["Smirnoff Red"]["id"], "qty": 0.75, "unit": "oz"}, {"item_id": stock["Jager"]["id"], "qty": 1, "unit": "oz"}]
    client.put("/api/recipes/a", json={"name": "Happy Shaft", "lines": lines})
    r = queue(client)["Shaft"]
    assert r["how"] == "copy" and len(r["lines"]) == 2 and not r["clear"]


def test_food_and_coffee_take_no_stock(client, db, stock):
    sold(db, ("a", "Two Egg Breakfast", "Food", "Eggs", 5), ("b", "Coffee", "Beverage", "Coffee", 5),
         ("c", "Baileys Coffee", "Liquor", "Special Coffee", 5), ("d", "Coke", "Beverage", "Pop", 5))
    res = client.get("/api/recipes/match").json()
    assert res["no_stock"] == 2 and {r["code"] for r in res["rows"]} == {"c", "d"}
    assert client.post("/api/recipes/no-stock").json() == {"saved": 2}
    assert client.get("/api/recipes/a").json()["ignored"] and client.get("/api/recipes/b").json()["ignored"]
    assert client.get("/api/recipes/c").status_code == 404


def test_food_is_tracked_like_drinks_once_the_platform_switches_it_on(client, platform, db, stock):
    loc = default_location(db).id
    item(client, "50", "Chicken breast", "Meat", "kg", 1)
    sold(db, ("a", "Chicken Sandwich", "Food", "Sandwiches", 5), ("b", "Coffee", "Beverage", "Coffee", 5), ("c", "Happy Jager", "Liquor", "Booze", 3))
    assert client.post("/api/recipes/no-stock", json={}).json()["saved"] == 2
    assert set(queue(client)) == {"Happy Jager"}
    assert client.get("/api/auth/me").json()["user"]["business"]["track_food"] is False

    # only the platform console can switch it
    assert client.patch(f"/api/platform/businesses/{loc}", json={"track_food": True}).status_code == 403
    assert client.patch(f"/api/businesses/{loc}", json={"track_food": True}).json().get("track_food") is False
    assert platform.patch("/api/platform/businesses/nope", json={"track_food": True}).status_code == 404
    r = platform.patch(f"/api/platform/businesses/{loc}", json={"track_food": True}).json()
    assert r["track_food"] is True and r["food_released"] == 1

    # the dish is back in the queue, matched to food stock only, and its portion is for a person to give
    assert client.get("/api/auth/me").json()["user"]["business"]["track_food"] is True
    res = client.get("/api/recipes/match").json()
    q = {x["name"]: x for x in res["rows"]}
    assert set(q) == {"Chicken Sandwich", "Happy Jager"} and res["no_stock"] == 0  # coffee keeps its no-stock recipe
    dish = q["Chicken Sandwich"]
    assert [c["name"] for c in dish["candidates"]] == ["Chicken breast"] and not dish["clear"]
    assert dish["lines"][0]["qty"] is None and dish["lines"][0]["unit"] == "g" and dish["check"] == "portion size"
    assert client.post("/api/recipes/no-stock", json={}).json()["saved"] == 0  # and it is not marked again

    # linked like a drink, it stays linked when the flag goes off; a new dish takes no stock again
    line = {"item_id": dish["candidates"][0]["id"], "qty": "180", "unit": "g"}
    assert client.post("/api/recipes/match/approve", json={"rows": [{"code": "a", "name": "Chicken Sandwich", "lines": [line], "ignored": False}]}).json()["saved"] == 1
    assert platform.patch(f"/api/platform/businesses/{loc}", json={"track_food": False}).json()["track_food"] is False
    assert client.get("/api/recipes/a").json()["ignored"] is False
    detail = platform.get(f"/api/platform/tenants/{default_location(db).tenant_id}").json()
    assert detail["businesses"][0]["track_food"] is False
