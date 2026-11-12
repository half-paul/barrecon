from decimal import Decimal as D


def _item(client, sku, name, category, **kw):
    body = {"sku": sku, "name": name, "category": category, "stock_unit": "ml", "pack_name": "bottle",
            "pack_size": 750, "case_size": 12, **kw}
    r = client.post("/api/items", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_groups_sync_create_rename_delete(client):
    _item(client, "1", "Vodka", "Spirits")
    _item(client, "2", "Gin", "Spirits")
    _item(client, "3", "Odd gin", "Spirits", pack_size=1140)
    groups = {g["name"]: g for g in client.get("/api/groups").json()}
    sp = groups["Spirits"]
    assert sp["item_count"] == 3 and D(sp["pack_size"]) == 750 and sp["differing"] == 1

    r = client.post("/api/groups", json={"name": "Cans", "stock_unit": "each", "pack_name": "can", "pack_size": 1, "case_size": 24})
    assert r.status_code == 201 and r.json()["item_count"] == 0
    assert client.post("/api/groups", json={"name": "Cans", "stock_unit": "each", "pack_size": 1}).status_code == 400
    assert client.post("/api/groups", json={"name": "X", "stock_unit": "litres-ish", "pack_size": 1}).status_code == 400

    r = client.patch("/api/groups/Spirits", json={"name": "Liquor"})
    assert r.status_code == 200 and r.json()["item_count"] == 3
    assert {i["category"] for i in client.get("/api/items").json()} == {"Liquor"}
    assert [g["name"] for g in client.get("/api/groups").json()] == ["Cans", "Liquor"]

    r = client.delete("/api/groups/Liquor", params={"move_to": "Cans"})
    assert r.json()["moved"] == 3
    assert {i["category"] for i in client.get("/api/items").json()} == {"Cans"}
    assert [g["name"] for g in client.get("/api/groups").json()] == ["Cans"]
    client.delete("/api/groups/Cans")
    assert {i["category"] for i in client.get("/api/items").json()} == {None}


def test_group_apply_keeps_unit_of_items_with_history(client):
    a = _item(client, "1", "Lager", "Beer", pack_size=355)
    _item(client, "2", "Ale", "Beer", pack_size=355)
    client.get("/api/groups")
    client.post("/api/movements", json={"item_id": a["id"], "kind": "delivery", "occurred_at": "2026-10-20T12:00:00Z",
                                        "external_ref": "INV-1", "packs": 24})
    client.patch("/api/groups/Beer", json={"stock_unit": "each", "pack_name": "can", "pack_size": 1, "case_size": 24})
    r = client.post("/api/groups/Beer/apply", json={"fields": ["stock_unit", "pack_name", "pack_size", "case_size"]})
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 2, "skipped_unit": ["Lager"]}
    items = {i["name"]: i for i in client.get("/api/items").json()}
    assert items["Ale"]["stock_unit"] == "each" and D(items["Ale"]["pack_size"]) == 1
    assert items["Lager"]["stock_unit"] == "ml" and items["Lager"]["pack_name"] == "can"
    assert client.post("/api/groups/Beer/apply", json={"fields": ["name"]}).status_code == 400


def test_delivery_batch(client):
    a = _item(client, "1", "Vodka", "Spirits")
    b = _item(client, "2", "Gin", "Spirits")
    body = {"occurred_at": "2026-10-20T12:00:00Z", "external_ref": "INV-9",
            "lines": [{"item_id": a["id"], "packs": 12, "pack_cost": 31}, {"item_id": b["id"], "packs": 6}]}
    r = client.post("/api/movements/batch", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == "inserted"
    moves = client.get("/api/movements").json()
    assert {(m["name"], D(m["qty"])) for m in moves} == {("Vodka", D(9000)), ("Gin", D(4500))}
    assert D(client.get(f"/api/items/{a['id']}").json()["pack_cost"]) == 31
    # it is a delivery like a receipt: listed under Deliveries, posted
    dl = client.get("/api/deliveries").json()
    assert [(d["receipt_no"], d["status"], d["source"], d["item_count"]) for d in dl] == [("INV-9", "posted", "sheet", 2)]

    assert client.post("/api/movements/batch", json=body).json()["outcome"] == "duplicate"
    body["lines"][1]["packs"] = 7
    assert client.post("/api/movements/batch", json=body).json()["outcome"] == "superseded"
    moves = client.get("/api/movements").json()
    assert {(m["name"], D(m["qty"])) for m in moves} == {("Vodka", D(9000)), ("Gin", D(5250))}
    assert len(client.get("/api/deliveries").json()) == 1

    # all or nothing: a bad line saves none of them
    bad = {**body, "external_ref": "INV-10", "lines": [{"item_id": a["id"], "packs": 1}, {"item_id": "nope", "packs": 1}]}
    assert client.post("/api/movements/batch", json=bad).status_code == 400
    assert not [m for m in client.get("/api/movements").json() if m["external_ref"] == "INV-10"]
    dup = {**body, "lines": [{"item_id": a["id"], "packs": 1}, {"item_id": a["id"], "packs": 2}]}
    assert client.post("/api/movements/batch", json=dup).status_code == 400
