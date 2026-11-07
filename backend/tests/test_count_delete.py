from app.models import CountLine, CountSession
from conftest import FIXTURE


def _item(client, sku="V1"):
    return client.post("/api/items", json={"sku": sku, "name": "Vodka", "category": "Vodka", "stock_unit": "ml",
                                           "pack_name": "bottle", "pack_size": 750}).json()


def _posted(client, item, at="2026-10-25T23:59:00-07:00", packs=2):
    s = client.post("/api/counts", json={"counted_at": at}).json()
    client.put(f"/api/counts/{s['id']}/lines", json=[{"item_id": item["id"], "packs_full": packs, "packs_partial": 0}])
    assert client.post(f"/api/counts/{s['id']}/post").status_code == 200
    return s


def test_delete_flags_a_posted_count_and_keeps_its_rows(client, db):
    item = _item(client)
    keep = _posted(client, item, "2026-10-18T23:59:00-07:00", packs=4)
    gone = _posted(client, item)
    assert client.delete(f"/api/counts/{gone['id']}").status_code == 204

    assert [c["id"] for c in client.get("/api/counts").json()] == [keep["id"]]
    every = {c["id"]: c["status"] for c in client.get("/api/counts?include_superseded=true").json()}
    assert every == {keep["id"]: "posted", gone["id"]: "deleted"}
    kept = client.get(f"/api/counts/{gone['id']}").json()
    assert kept["status"] == "deleted" and kept["lines"][0]["packs_full"] is not None
    db.expire_all()
    assert db.query(CountLine).filter_by(session_id=gone["id"]).count() == 1

    # nothing counts it: on-hand falls back to the earlier count, variance refuses it
    row = next(i for i in client.get("/api/items").json() if i["id"] == item["id"])
    assert float(row["last_counted_packs"]) == 4
    r = client.get(f"/api/variance?opening={keep['id']}&closing={gone['id']}")
    assert r.status_code == 400 and "not posted" in r.json()["detail"]


def test_a_deleted_count_cannot_be_changed_or_deleted_again(client):
    gone = _posted(client, _item(client))
    client.delete(f"/api/counts/{gone['id']}")
    assert client.delete(f"/api/counts/{gone['id']}").status_code == 400
    assert client.put(f"/api/counts/{gone['id']}/lines", json=[]).status_code == 400
    assert client.post(f"/api/counts/{gone['id']}/post").status_code == 400
    assert client.patch(f"/api/counts/{gone['id']}", json={"counted_at": "2026-10-17T23:00:00-07:00"}).status_code == 400


def test_delete_leaves_earlier_versions_superseded_and_refuses_them(client):
    item = _item(client)
    first = _posted(client, item)
    second = _posted(client, item, packs=3)  # same time: supersedes the first
    assert client.delete(f"/api/counts/{first['id']}").status_code == 400
    assert client.delete(f"/api/counts/{second['id']}").status_code == 204
    every = {c["id"]: c["status"] for c in client.get("/api/counts?include_superseded=true").json()}
    assert every == {first["id"]: "superseded", second["id"]: "deleted"}


def test_a_new_count_at_the_same_time_leaves_the_deleted_one_alone(client):
    item = _item(client)
    gone = _posted(client, item)
    client.delete(f"/api/counts/{gone['id']}")
    new = _posted(client, item, packs=3)
    every = {c["id"]: c["status"] for c in client.get("/api/counts?include_superseded=true").json()}
    assert every == {gone["id"]: "deleted", new["id"]: "posted"}


def test_legacy_reimport_does_not_bring_a_deleted_count_back(client, db):
    upload = lambda: client.post("/api/imports/legacy", files={"file": ("w.xlsx", FIXTURE.read_bytes())})
    assert upload().status_code == 200
    counts = client.get("/api/counts").json()
    client.delete(f"/api/counts/{counts[0]['id']}")
    assert upload().status_code == 200
    assert len(client.get("/api/counts").json()) == len(counts) - 1


def test_delete_draft_really_removes_it_and_unknown_is_404(client, db):
    s = client.post("/api/counts", json={"counted_at": "2026-10-25T23:59:00-07:00"}).json()
    assert client.delete(f"/api/counts/{s['id']}").status_code == 204
    assert client.delete(f"/api/counts/{s['id']}").status_code == 404
    db.expire_all()
    assert db.query(CountSession).count() == 0


def test_pin_session_deletes_drafts_only(client):
    item = _item(client)
    posted = _posted(client, item)
    draft = client.post("/api/counts", json={"counted_at": "2026-10-26T23:59:00-07:00"}).json()
    assert client.post("/api/auth/pin", json={"pin": "4827", "current_password": "test-pass"}).status_code == 200
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": "admin", "pin": "4827"}).status_code == 200
    r = client.delete(f"/api/counts/{posted['id']}")
    assert r.status_code == 403 and "password" in r.json()["detail"]
    assert client.delete(f"/api/counts/{draft['id']}").status_code == 204
