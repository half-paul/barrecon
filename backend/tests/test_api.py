from decimal import Decimal as D

from tests.conftest import FIXTURE


def test_auth_required(db):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        assert c.get("/api/items").status_code == 401
        assert c.post("/api/auth/login", json={"password": "wrong"}).status_code == 401


def test_items_counts_movements_flow(client):
    r = client.post("/api/items", json={"sku": "67", "name": "Smirnoff Red", "category": "Spirits", "stock_unit": "ml",
                                        "pack_name": "bottle", "pack_size": 750, "case_size": 12, "pack_cost": 30})
    assert r.status_code == 201, r.text
    item = r.json()
    assert client.post("/api/items", json={**item, "name": "dup"}).status_code == 409
    r = client.patch(f"/api/items/{item['id']}", json={"pack_cost": 32, "par_level": 4})
    assert D(r.json()["pack_cost"]) == 32 and D(r.json()["par_level"]) == 4
    assert len(client.get(f"/api/items/{item['id']}/costs").json()) == 2

    r = client.post("/api/counts", json={"counted_at": "2026-10-25T23:59:00-07:00"})
    sess = r.json()
    assert r.status_code == 201 and sess["line_count"] == 0 and sess["lines"][0]["packs_full"] is None
    r = client.put(f"/api/counts/{sess['id']}/lines", json=[{"item_id": item["id"], "packs_full": 3, "packs_partial": 0.4}])
    assert D(r.json()["lines"][0]["qty"]) == 2550
    r = client.post(f"/api/counts/{sess['id']}/post")
    assert r.json()["status"] == "posted"
    assert client.put(f"/api/counts/{sess['id']}/lines", json=[]).status_code == 400
    it = client.get(f"/api/items/{item['id']}").json()
    assert D(it["last_counted_packs"]) == D("3.4")

    r = client.post("/api/movements", json={"item_id": item["id"], "kind": "delivery", "occurred_at": "2026-10-20T12:00:00-07:00",
                                            "external_ref": "INV-1", "packs": 12, "pack_cost": 31})
    assert r.status_code == 201 and r.json()["outcome"] == "inserted"
    r = client.post("/api/movements", json={"item_id": item["id"], "kind": "delivery", "occurred_at": "2026-10-20T12:00:00-07:00",
                                            "external_ref": "INV-1", "packs": 12, "pack_cost": 31})
    assert r.json()["outcome"] == "duplicate"
    assert len(client.get("/api/movements").json()) == 1
    assert client.get("/api/movements/kinds").json()[0]["kind"] == "delivery"

    for url in ("/api/items/export.xlsx", "/api/counts/template.xlsx", "/api/movements/template.xlsx"):
        r = client.get(url)
        assert r.status_code == 200 and r.content[:2] == b"PK"


def test_legacy_endpoints(client):
    files = {"file": ("legacy.xlsx", FIXTURE.read_bytes())}
    r = client.post("/api/imports/legacy/preview", files=files)
    assert r.status_code == 200 and r.json()["deliveries"] > 100
    r = client.post("/api/imports/legacy", files=files)
    assert r.status_code == 200 and r.json()["count_sessions_created"] == 21
    assert len(client.get("/api/items").json()) >= 90
    assert len(client.get("/api/counts").json()) == 21
    assert client.get("/api/imports").json()[0]["dataset"] == "legacy"
    issues = client.get("/api/imports/issues").json()
    assert any(i["code"] == "order_unparsed" for i in issues)


def test_settings(client):
    s = client.get("/api/settings").json()
    assert D(s["oz_ml"]) == D("28.4131") and s["count_weekday"] == 6
    s = client.put("/api/settings", json={"oz_ml": 29.5735, "count_weekday": 1}).json()
    assert D(s["oz_ml"]) == D("29.5735") and s["count_weekday"] == 1
    assert s["variance_chart_items"] == []
    s = client.put("/api/settings", json={"variance_chart_items": ["a", "b"]}).json()
    assert s["variance_chart_items"] == ["a", "b"] and s["count_weekday"] == 1
    assert client.get("/api/settings").json()["variance_chart_items"] == ["a", "b"]
    assert s["variance_view_added"] == [] and s["variance_view_removed"] == []
    s = client.put("/api/settings", json={"variance_view_added": ["a"], "variance_view_removed": ["b"]}).json()
    assert s["variance_view_added"] == ["a"] and s["variance_view_removed"] == ["b"]
    s = client.put("/api/settings", json={"variance_view_added": []}).json()
    assert s["variance_view_added"] == [] and s["variance_view_removed"] == ["b"]


def test_patch_item_sku(client):
    base = {"category": "Spirits", "stock_unit": "ml", "pack_name": "bottle", "pack_size": 750}
    a = client.post("/api/items", json={"sku": "SWYPE-00000380", "name": "Forty Creek", **base}).json()
    client.post("/api/items", json={"sku": "67", "name": "Smirnoff Red", **base})
    r = client.patch(f"/api/items/{a['id']}", json={"sku": " 550715 ", "par_level": 2})
    assert r.status_code == 200, r.text
    assert r.json()["sku"] == "550715" and r.json()["id"] == a["id"] and D(r.json()["par_level"]) == 2
    assert client.patch(f"/api/items/{a['id']}", json={"sku": "67"}).status_code == 400
    assert client.patch(f"/api/items/{a['id']}", json={"sku": ""}).status_code == 400
    assert client.get(f"/api/items/{a['id']}").json()["sku"] == "550715"


def test_backdate_draft_count(client):
    item = client.post("/api/items", json={"sku": "67", "name": "Smirnoff Red", "category": "Spirits", "stock_unit": "ml",
                                           "pack_name": "bottle", "pack_size": 750, "case_size": 12}).json()
    s = client.post("/api/counts", json={"counted_at": "2026-11-03T17:30:00-07:00"}).json()
    r = client.patch(f"/api/counts/{s['id']}", json={"counted_at": "2026-10-17T23:00:00-07:00"})
    assert r.status_code == 200 and r.json()["counted_at"].startswith("2026-10-18T06:00")
    client.put(f"/api/counts/{s['id']}/lines", json=[{"item_id": item["id"], "packs_full": 1, "packs_partial": 0}])
    assert client.post(f"/api/counts/{s['id']}/post").status_code == 200


def test_redate_posted_count_keeps_history(client):
    item = client.post("/api/items", json={"sku": "67", "name": "Smirnoff Red", "category": "Spirits", "stock_unit": "ml",
                                           "pack_name": "bottle", "pack_size": 750, "case_size": 12}).json()
    s = client.post("/api/counts", json={"counted_at": "2026-11-03T17:30:00-07:00"}).json()
    client.put(f"/api/counts/{s['id']}/lines", json=[{"item_id": item["id"], "packs_full": 2, "packs_partial": 0.5}])
    client.post(f"/api/counts/{s['id']}/post")

    r = client.patch(f"/api/counts/{s['id']}", json={"counted_at": "2026-10-17T23:00:00-07:00", "reason": "counted on the 17th"})
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["id"] != s["id"] and new["status"] == "posted" and new["counted_at"].startswith("2026-10-18T06:00")
    assert new["note"] == "Date changed from 2026-11-03 17:30: counted on the 17th"
    assert [(ln["item_id"], float(ln["packs_full"]), float(ln["packs_partial"])) for ln in new["lines"]] == [(item["id"], 2.0, 0.5)]
    assert [v["id"] for v in new["versions"]] == [s["id"], new["id"]]

    old = client.get(f"/api/counts/{s['id']}").json()
    assert old["status"] == "superseded" and old["superseded_by"] == new["id"] and len(old["lines"]) == 1
    assert [c["id"] for c in client.get("/api/counts").json()] == [new["id"]]
    assert len(client.get("/api/counts?include_superseded=true").json()) == 2
    # the old version cannot be moved again; the same time is refused
    assert client.patch(f"/api/counts/{s['id']}", json={"counted_at": "2026-10-16T23:00:00-07:00"}).status_code == 400
    assert client.patch(f"/api/counts/{new['id']}", json={"counted_at": "2026-10-17T23:00:00-07:00"}).status_code == 400


def test_new_count_is_blank_except_formula_and_posts_only_entered(client):
    mk = lambda sku, name: client.post("/api/items", json={"sku": sku, "name": name, "category": "Spirits", "stock_unit": "ml",
                                                         "pack_name": "bottle", "pack_size": 750}).json()
    vodka, gin, rum = mk("V", "Vodka"), mk("G", "Gin"), mk("R", "Rum")
    s = client.post("/api/counts", json={"counted_at": "2026-10-19T23:00:00-07:00"}).json()
    client.put(f"/api/counts/{s['id']}/lines", json=[{"item_id": vodka["id"], "packs_full": 2}])
    client.post(f"/api/counts/{s['id']}/post")

    s = client.post("/api/counts", json={"counted_at": "2026-10-26T23:00:00-07:00"}).json()
    by = {ln["item_id"]: ln for ln in s["lines"]}
    assert s["line_count"] == 1 and len(by) == 3
    assert D(by[vodka["id"]]["packs_full"]) == 2  # what the formula says is left
    assert by[gin["id"]]["packs_full"] is None and by[rum["id"]]["packs_partial"] is None

    r = client.put(f"/api/counts/{s['id']}/lines", json=[{"item_id": gin["id"], "packs_full": 0, "packs_partial": None},
                                                         {"item_id": vodka["id"], "packs_full": None, "packs_partial": None}])
    assert r.json()["line_count"] == 1
    posted = client.post(f"/api/counts/{s['id']}/post").json()
    assert [(ln["item_id"], D(ln["qty"])) for ln in posted["lines"]] == [(gin["id"], 0)]  # blank vodka and rum ignored
