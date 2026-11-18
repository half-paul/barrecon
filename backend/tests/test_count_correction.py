from app.models import CountLine


def _item(client, sku, name, unit="ml", pack_size=750):
    return client.post("/api/items", json={"sku": sku, "name": name, "category": "Bar", "stock_unit": unit,
                                           "pack_name": "bottle", "pack_size": pack_size}).json()


def _posted(client, lines, at="2026-11-09T23:59:00-08:00"):
    s = client.post("/api/counts", json={"counted_at": at}).json()
    client.put(f"/api/counts/{s['id']}/lines", json=[{"item_id": i["id"], "packs_full": p, "packs_partial": 0} for i, p in lines])
    assert client.post(f"/api/counts/{s['id']}/post").status_code == 200
    return s


def _line(detail, item):
    return next((ln for ln in detail["lines"] if ln["item_id"] == item["id"]), None)


def test_correcting_one_item_posts_a_new_version_and_keeps_the_rest(client):
    vodka, gin = _item(client, "V1", "Vodka"), _item(client, "G1", "Gin")
    s = _posted(client, [(vodka, 4), (gin, 2)])
    r = client.post(f"/api/counts/{s['id']}/corrections",
                    json={"item_id": vodka["id"], "packs_full": 3, "packs_partial": "0.5", "reason": "miscounted"})
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["id"] != s["id"] and new["status"] == "posted" and new["counted_at"] == client.get(f"/api/counts/{s['id']}").json()["counted_at"]
    assert float(_line(new, vodka)["qty"]) == 3.5 * 750
    assert float(_line(new, gin)["packs_full"]) == 2
    assert "Vodka corrected from 4" in new["note"] and "miscounted" in new["note"]
    old = client.get(f"/api/counts/{s['id']}").json()
    assert old["status"] == "superseded" and old["superseded_by"] == new["id"]
    assert float(_line(old, vodka)["packs_full"]) == 4  # history untouched
    assert [c["id"] for c in client.get("/api/counts").json()] == [new["id"]]
    row = next(i for i in client.get("/api/items").json() if i["id"] == vodka["id"])
    assert float(row["last_counted_packs"]) == 3.5


def test_same_packs_recomputes_a_qty_saved_with_a_wrong_pack_size(client, db):
    cans = _item(client, "C1", "Zero cans", unit="each", pack_size=440)  # wrong: cans are counted one by one
    s = _posted(client, [(cans, 45)])
    client.patch(f"/api/items/{cans['id']}", json={"pack_size": 1})
    assert float(_line(client.get(f"/api/counts/{s['id']}").json(), cans)["qty"]) == 19800
    r = client.post(f"/api/counts/{s['id']}/corrections", json={"item_id": cans["id"], "packs_full": 45, "packs_partial": 0})
    assert r.status_code == 200, r.text
    assert float(_line(r.json(), cans)["qty"]) == 45
    # nothing left to fix now
    again = client.post(f"/api/counts/{r.json()['id']}/corrections", json={"item_id": cans["id"], "packs_full": 45, "packs_partial": 0})
    assert again.status_code == 400 and "already" in again.json()["detail"]


def test_blank_takes_the_item_out_and_an_uncounted_item_can_be_added(client):
    vodka, gin, rum = _item(client, "V1", "Vodka"), _item(client, "G1", "Gin"), _item(client, "R1", "Rum")
    s = _posted(client, [(vodka, 4), (gin, 2)])
    out = client.post(f"/api/counts/{s['id']}/corrections", json={"item_id": gin["id"]}).json()
    assert _line(out, gin) is None and _line(out, vodka) is not None
    added = client.post(f"/api/counts/{out['id']}/corrections", json={"item_id": rum["id"], "packs_full": 1}).json()
    assert float(_line(added, rum)["qty"]) == 750 and len(added["lines"]) == 2
    assert len(added["versions"]) == 3
    r = client.post(f"/api/counts/{added['id']}/corrections", json={"item_id": gin["id"]})
    assert r.status_code == 400 and "not on this count" in r.json()["detail"]


def test_only_the_current_posted_version_can_be_corrected(client, db):
    vodka = _item(client, "V1", "Vodka")
    s = _posted(client, [(vodka, 4)])
    new = client.post(f"/api/counts/{s['id']}/corrections", json={"item_id": vodka["id"], "packs_full": 3}).json()
    r = client.post(f"/api/counts/{s['id']}/corrections", json={"item_id": vodka["id"], "packs_full": 2})
    assert r.status_code == 400 and "newer version" in r.json()["detail"]
    draft = client.post("/api/counts", json={"counted_at": "2026-11-10T23:59:00-08:00"}).json()
    assert client.post(f"/api/counts/{draft['id']}/corrections", json={"item_id": vodka["id"], "packs_full": 1}).status_code == 400
    client.delete(f"/api/counts/{new['id']}")
    assert client.post(f"/api/counts/{new['id']}/corrections", json={"item_id": vodka["id"], "packs_full": 1}).status_code == 400
    assert client.post(f"/api/counts/{s['id']}/corrections", json={"item_id": vodka["id"], "packs_full": -1}).status_code == 422
    assert client.post("/api/counts/nope/corrections", json={"item_id": vodka["id"], "packs_full": 1}).status_code == 404


def test_removing_the_last_item_is_refused_and_changes_nothing(client, db):
    vodka = _item(client, "V1", "Vodka")
    s = _posted(client, [(vodka, 4)])
    r = client.post(f"/api/counts/{s['id']}/corrections", json={"item_id": vodka["id"]})
    assert r.status_code == 400 and "no lines" in r.json()["detail"]
    assert [c["id"] for c in client.get("/api/counts?include_superseded=true").json()] == [s["id"]]
    db.expire_all()
    assert db.query(CountLine).count() == 1
