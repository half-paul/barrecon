import io
from decimal import Decimal

from openpyxl import Workbook

from app.services import inventory as inv
from app.services import recipes as rec
from app.services.recipe_import import RECIPE_COLS, import_recipes
from app.services.xlsx_io import ITEM_COLS


def workbook(recipes, items=()):
    wb = Workbook()
    ws = wb.active
    ws.title = "Recipes"
    ws.append(RECIPE_COLS)
    for r in recipes:
        ws.append(list(r))
    if items:
        wi = wb.create_sheet("Items")
        wi.append(ITEM_COLS)
        for r in items:
            wi.append(list(r))
    wb.create_sheet("Photos")
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def vodka(db):
    it, _ = inv.upsert_item(db, {"sku": "VOD", "name": "Smirnoff Vodka", "stock_unit": "ml", "pack_size": Decimal(750)})
    return it


def test_tracked_untracked_and_new_items(db):
    vodka(db)
    data = workbook([
        (561, "Moscow Mule", "Smirnoff Vodka", "VOD", 1.5, "oz", None),
        (561, None, "Lime juice", None, 0.5, "oz", None),
        (561, None, "Ginger Beer", None, None, None, "top"),
        (561, None, None, None, None, None, "Glass: copper mug"),
        ("00000532", "Aperol Spritz", "Aperol", "ING-APEROL", 2, "oz", None),
        ("00001328", "Mango Faux Mule", "Mango juice", None, 2, "oz", None),
    ], items=[("ING-APEROL", "Aperol", "Liqueur", "ml", "bottle", 750, 12, None, None, None, None, True)])
    res = import_recipes(db, data, "menu.xlsx")
    assert res.status == "ok", res.rows
    assert res.inserted == 3
    mule = rec.load(db, "00000561")  # numeric code got its leading zeros back
    assert mule.name == "Moscow Mule" and not mule.ignored
    assert [(ln.item.sku, ln.qty, ln.unit) for ln in mule.versions[0].lines] == [("VOD", Decimal("1.5"), "oz")]
    assert mule.steps == "Lime juice 0.5 oz\nGinger Beer (top)\nGlass: copper mug" and mule.notes is None
    assert rec.load(db, "00000532").versions[0].lines[0].item.name == "Aperol"
    mocktail = rec.load(db, "00001328")
    assert mocktail.ignored and mocktail.steps == "Mango juice 2 oz"


def test_unknown_sku_writes_nothing(db):
    data = workbook([("00000561", "Moscow Mule", "Vodka", "NOPE", 1.5, "oz", None)],
                    items=[("ING-X", "X", None, "ml", "bottle", 750, 12, None, None, None, None, True)])
    res = import_recipes(db, data, "bad.xlsx")
    assert res.status == "rejected" and "NOPE" in res.rows[0].message
    assert rec.load(db, "00000561") is None and inv.item_by_sku(db, "ING-X") is None


def test_each_item_portion_is_saved_with_a_warning(db):
    inv.upsert_item(db, {"sku": "SWYPE-00000380", "name": "Forty Creek", "stock_unit": "each", "pack_size": Decimal(1)})
    res = import_recipes(db, workbook([("00001256", "Canadian Buck", "Forty Creek", "SWYPE-00000380", 2, "oz", None)]), "a.xlsx")
    assert res.status == "ok" and "can't be converted" in res.rows[0].message


def test_api_upload(client, db):
    vodka(db)
    db.commit()
    data = workbook([("00000561", "Moscow Mule", "Smirnoff Vodka", "VOD", 1.5, "oz", None)])
    r = client.post("/api/recipes/import", files={"file": ("r.xlsx", data)})
    assert r.status_code == 200 and r.json()["inserted"] == 1, r.text
    again = client.post("/api/recipes/import", files={"file": ("r.xlsx", data)}).json()
    assert again["status"] == "rejected"  # same file twice


def test_steps_and_notes_are_split_and_show_in_menu(client, db):
    vodka(db)
    db.commit()
    data = workbook([
        ("00000648", "White Sangria", "Smirnoff Vodka", "VOD", 3.43, "oz", "house wine: check which box is poured"),
        ("00000648", None, "Ginger ale", None, None, None, "top"),
        ("00000648", None, None, None, None, None, "Glass: Wine glass"),
        ("00000648", None, None, None, None, None, "Note: pre-batch scaled to the 6 oz pour"),
    ])
    assert client.post("/api/recipes/import", files={"file": ("s.xlsx", data)}).json()["status"] == "ok"
    recipe = next(r for r in client.get("/api/recipes/menu").json() if r["code"] == "00000648")["recipe"]
    assert recipe["steps"] == "Ginger ale (top)\nGlass: Wine glass"
    assert recipe["notes"] == "pre-batch scaled to the 6 oz pour\nSmirnoff Vodka: house wine: check which box is poured"


PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def test_recipe_photo(client, db):
    it = vodka(db)
    db.commit()
    assert client.put("/api/recipes/00000561/photo", files={"file": ("p.png", PNG, "image/png")}).status_code == 404
    client.put("/api/recipes/00000561", json={"name": "Moscow Mule", "lines": [{"item_id": it.id, "qty": 1.5}]})
    bad = client.put("/api/recipes/00000561/photo", files={"file": ("p.txt", b"hi", "text/plain")})
    assert bad.status_code == 400
    r = client.put("/api/recipes/00000561/photo", files={"file": ("p.png", PNG, "image/png")})
    assert r.status_code == 200 and r.json()["photo_at"], r.text
    got = client.get("/api/recipes/00000561/photo")
    assert got.content == PNG and got.headers["content-type"] == "image/png"
    # saving the recipe again keeps its photo
    client.put("/api/recipes/00000561", json={"name": "Moscow Mule", "lines": [{"item_id": it.id, "qty": 2}], "steps": "Glass: mug"})
    assert client.get("/api/recipes/00000561").json()["photo_at"]
    assert client.delete("/api/recipes/00000561/photo").status_code == 204
    assert client.get("/api/recipes/00000561/photo").status_code == 404
    client.put("/api/recipes/00000561/photo", files={"file": ("p.png", PNG, "image/png")})
    assert client.delete("/api/recipes/00000561").status_code == 204  # photo goes with the recipe
