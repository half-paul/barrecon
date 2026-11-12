import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from sqlalchemy import delete, text

from app import audit, auth
from app.main import app
from app.models import AuditChange, AuditEvent, DataIssue, InventoryItem, User, utcnow
from app.services import maintenance


@pytest.fixture(autouse=True)
def no_delay(monkeypatch):
    monkeypatch.setattr(auth, "FAIL_DELAY_S", 0)


def _login(username, password=None, pin=None, expect=200) -> TestClient:
    c = TestClient(app)
    body = {"username": username, **({"pin": pin} if pin is not None else {"password": password})}
    r = c.post("/api/auth/login", json=body)
    assert r.status_code == expect, r.text
    return c


def _make(client, username="sam", permissions=None, **kw):
    r = client.post("/api/users", json={"username": username, "display_name": username.title(), "password": "Bar-stock-77",
                                         "permissions": permissions or {}, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def _events(client, **q):
    return client.get("/api/audit", params={"limit": 200, **q}).json()["events"]


# ---- field-level changes ----------------------------------------------------------------------------------

def test_changes_recorded_with_before_and_after(client, db):
    r = client.post("/api/items", json={"sku": "V1", "name": "Vodka", "stock_unit": "ml", "pack_size": "750"})
    item_id = r.json()["id"]
    client.patch(f"/api/items/{item_id}", json={"name": "Vodka 750", "par_level": "6"})
    ev = next(e for e in _events(client) if e["route"] == "/api/items/{item_id}")
    assert ev["label"] == "Changed stock item" and ev["changes"] == 1
    ch = client.get(f"/api/audit/{ev['id']}/changes").json()
    assert ch == [{"table": "inventory_items", "row_id": item_id, "op": "update",
                   "changes": {"name": ["Vodka", "Vodka 750"], "par_level": [None, "6"]}}]
    created = next(e for e in _events(client) if e["route"] == "/api/items" and e["method"] == "POST")
    ins = client.get(f"/api/audit/{created['id']}/changes").json()
    assert ins[0]["op"] == "insert" and ins[0]["changes"]["sku"] == [None, "V1"]


def test_secrets_never_stored_in_changes(client, db):
    u = _make(client, "pat")
    client.post(f"/api/users/{u['id']}/password", json={"password": "Another-pw-42"})
    ev = next(e for e in _events(client) if e["route"] == "/api/users/{user_id}/password")
    ch = client.get(f"/api/audit/{ev['id']}/changes").json()
    assert ch[0]["changes"]["password_hash"] == ["***", "***"]


def test_bulk_delete_and_rollback(db, location):
    holder = {"event_id": "ev-1"}
    tok = audit.audit_ctx.set(holder)
    try:
        db.add(InventoryItem(sku="X", name="X", stock_unit="ml"))
        db.flush()
        db.rollback()  # nothing kept: the change rows go with it
        assert db.query(AuditChange).count() == 0
        db.execute(delete(InventoryItem).where(InventoryItem.sku == "nothing"))
        db.commit()
    finally:
        audit.audit_ctx.reset(tok)
    row = db.query(AuditChange).one()
    assert row.op == "bulk_delete" and row.changes["rows"] == 0 and "inventory_items.sku" in row.changes["where"]


def test_system_job_recorded(db):
    with audit.system_job("test_job", area="sales") as job:
        db.add(InventoryItem(sku="S1", name="Sys", stock_unit="ml"))
        db.commit()
        job["detail"] = {"done": 1}
    ev = db.query(AuditEvent).filter_by(username="system:test_job").one()
    assert ev.action == "system" and db.query(AuditChange).filter_by(event_id=ev.id).count() == 1


# ---- hash chain, retention --------------------------------------------------------------------------------

def test_hash_chain_detects_tampering(client, db):
    client.post("/api/items", json={"sku": "C1", "name": "Gin", "stock_unit": "ml", "pack_size": "750"})
    client.put("/api/settings", json={"variance_threshold_pct": "25"})
    r = client.get("/api/audit/verify").json()
    assert r["ok"] and r["checked"] >= 3
    ev = db.query(AuditEvent).filter_by(route="/api/settings").one()
    db.execute(text("UPDATE audit_events SET status = 200, path = '/api/x' WHERE id = :i"), {"i": ev.id})
    db.commit()
    bad = client.get("/api/audit/verify").json()
    assert not bad["ok"] and bad["broken_at"]["id"] == ev.id


def test_tampered_change_breaks_chain(client, db):
    client.post("/api/items", json={"sku": "C2", "name": "Rum", "stock_unit": "ml", "pack_size": "750"})
    db.execute(text("UPDATE audit_changes SET changes = '{}'"))
    db.commit()
    assert not client.get("/api/audit/verify").json()["ok"]


def test_retention_default_and_purge(db):
    old = utcnow() - timedelta(days=400)
    for i in range(3):
        audit.record(username="old", action="change", path=f"/old/{i}", at=old + timedelta(minutes=i))
    with audit.system_job("old_job") as job:  # its change row goes with it
        db.add(InventoryItem(sku="O1", name="Old", stock_unit="ml"))
        db.commit()
        job["detail"] = {"x": 1}
    client = TestClient(app)
    client.post("/api/auth/login", json={"username": "admin", "password": "test-pass"})
    assert client.get("/api/settings").json()["audit_retention_days"] == 365
    client.post("/api/items", json={"sku": "R1", "name": "Rye", "stock_unit": "ml", "pack_size": "750"})
    r = maintenance.purge_audit(db)
    assert r["deleted"] == 3
    assert db.query(AuditChange).filter_by(table_name="inventory_items").count() == 2
    assert db.query(AuditEvent).filter_by(username="old").count() == 0
    assert db.query(AuditEvent).filter_by(username="system:audit_purge").count() == 1
    assert client.get("/api/audit/verify").json()["ok"]  # chain still checks from the oldest remaining row


def test_purge_never_leaves_a_hole_in_the_chain(client, db):
    audit.record(username="late", action="change", at=utcnow() - timedelta(days=400))  # newer seq, old date: kept
    assert maintenance.purge_audit(db)["deleted"] == 0
    assert client.get("/api/audit/verify").json()["ok"]


def test_retention_forever_and_validation(client, platform, db):
    """Retention covers every tenant, so it is set in the platform console; a tenant admin can't change it."""
    audit.record(username="old", action="change", at=utcnow() - timedelta(days=4000))
    assert client.put("/api/settings", json={"audit_retention_days": 0}).status_code == 403
    assert platform.put("/api/platform/settings", json={"audit_retention_days": 10}).status_code == 422
    assert platform.put("/api/platform/settings", json={"audit_retention_days": 0}).json()["audit_retention_days"] == 0
    assert maintenance.purge_audit(db)["deleted"] == 0


def test_security_settings_admin_only(client):
    _make(client, "mgr", {"settings": "edit", "variance": "view"})
    m = _login("mgr", "Bar-stock-77")
    cur = m.get("/api/settings").json()
    # saving the whole form with the security values unchanged is fine
    assert m.put("/api/settings", json={**cur, "variance_threshold_pct": "20"}).status_code == 200
    assert m.put("/api/settings", json={"audit_retention_days": 30}).status_code == 403
    assert m.put("/api/settings", json={"idle_logout_minutes": 0}).status_code == 403
    _make(client, "viewer", {"variance": "view"})
    v = _login("viewer", "Bar-stock-77")
    assert v.put("/api/settings", json={"variance_chart_items": ["a"]}).status_code == 200
    assert v.put("/api/settings", json={"variance_view_added": ["a"], "variance_view_removed": ["b"]}).status_code == 200
    assert v.put("/api/settings", json={"count_weekday": 2}).status_code == 403


# ---- lockout, passwords -----------------------------------------------------------------------------------

def test_lockout_after_five_failures_and_unlock(client, db):
    u = _make(client, "lee", {"counts": "view"})
    for _ in range(5):
        _login("lee", "wrong-one", expect=401)
    r = TestClient(app).post("/api/auth/login", json={"username": "lee", "password": "Bar-stock-77"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    assert db.query(DataIssue).filter_by(code="login_locked", entity_id="lee").count() == 1
    assert next(x for x in client.get("/api/users").json() if x["id"] == u["id"])["locked"]
    assert client.post(f"/api/users/{u['id']}/unlock").status_code == 200
    _login("lee", "Bar-stock-77")
    assert client.post(f"/api/users/{u['id']}/unlock").status_code == 409  # not locked any more


def test_success_resets_failure_count(client):
    _make(client, "kim")
    for _ in range(4):
        _login("kim", "nope-nope", expect=401)
    _login("kim", "Bar-stock-77")
    for _ in range(4):
        _login("kim", "nope-nope", expect=401)
    _login("kim", "Bar-stock-77")  # 8 failures in the window, but never 5 since the last good login


def test_unknown_usernames_lock_too(client):
    for _ in range(5):
        _login("ghost", "x-x-x-x-x", expect=401)
    _login("ghost", "x-x-x-x-x", expect=429)


def test_password_rules(client):
    def make(pw, name="ruth"):
        return client.post("/api/users", json={"username": name, "display_name": "Ruth Baker", "password": pw})
    assert make("password1").status_code == 422
    assert make("aaaaaaaaaa").status_code == 422
    assert make("ruth-is-great").status_code == 422
    assert make("baker-2026!").status_code == 422
    assert make("Tall-glass-9").status_code == 201


# ---- PINs, idle logout, cookies ---------------------------------------------------------------------------

def test_pin_login_is_limited(client, db):
    boss = _make(client, "boss", is_admin=True)
    assert client.post(f"/api/users/{boss['id']}/pin", json={"pin": "1234"}).status_code == 422
    assert client.post(f"/api/users/{boss['id']}/pin", json={"pin": "7777"}).status_code == 422
    assert client.post(f"/api/users/{boss['id']}/pin", json={"pin": "8352"}).json()["has_pin"]
    b = _login("boss", pin="8352")
    me = b.get("/api/auth/me").json()["user"]
    assert me["session_mode"] == "pin" and not me["is_admin"] and set(me["permissions"]) == {"counts", "deliveries"}
    assert b.post("/api/counts", json={"counted_at": "2026-11-01T10:00:00Z"}).status_code == 201
    r = b.get("/api/items")
    assert r.status_code == 403 and "PIN" in r.json()["detail"]
    assert b.get("/api/users").status_code == 403
    assert b.post("/api/auth/password", json={"current_password": "Bar-stock-77", "new_password": "Other-pass-55"}).status_code == 403
    _login("boss", pin="0000", expect=401)
    ev = db.query(AuditEvent).filter_by(action="login", username="boss").one()
    assert ev.detail == {"method": "pin"}


def test_pin_locked_after_ten_wrong(client, db):
    u = _make(client, "ivy", {"counts": "edit"})
    client.post(f"/api/users/{u['id']}/pin", json={"pin": "5829"})
    for i in range(10):  # spread over hours, so the 15-minute lock never kicks in
        audit.record(username="ivy", user_id=u["id"], action="login_failed", detail={"method": "pin"},
                     at=utcnow() - timedelta(hours=10 - i))
    r = TestClient(app).post("/api/auth/login", json={"username": "ivy", "pin": "5829"})
    assert r.status_code == 429 and "password" in r.json()["detail"]
    _login("ivy", "Bar-stock-77")  # a password login switches PIN login back on
    _login("ivy", pin="5829")


def test_idle_logout_and_sliding_refresh(client, db, monkeypatch):
    client.put("/api/settings", json={"idle_logout_minutes": 5})
    u = db.query(User).filter_by(username="admin").one()

    def token_from(seconds_ago):
        monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: int(time.time()) - seconds_ago)
        t = auth._token(u, "password", int(time.time()) - seconds_ago)
        monkeypatch.undo()
        monkeypatch.setattr(auth, "FAIL_DELAY_S", 0)
        return t

    c = TestClient(app)
    c.cookies.set(auth.COOKIE, token_from(10 * 60))
    r = c.get("/api/counts")
    assert r.status_code == 401 and "idle" in r.json()["detail"]
    c = TestClient(app)
    c.cookies.set(auth.COOKIE, token_from(2 * 60))
    r = c.get("/api/counts")
    assert r.status_code == 200 and auth.COOKIE in r.headers.get("set-cookie", "")


def test_secure_cookie_behind_https():
    r = TestClient(app).post("/api/auth/login", json={"username": "admin", "password": "test-pass"}, headers={"x-forwarded-proto": "https"})
    assert "secure" in r.headers["set-cookie"].lower()
    r = TestClient(app).post("/api/auth/login", json={"username": "admin", "password": "test-pass"})
    assert "secure" not in r.headers["set-cookie"].lower()


def test_forwarded_for_ignored_unless_trusted(client, db, monkeypatch):
    h = {"x-forwarded-for": "6.6.6.6"}
    TestClient(app).post("/api/auth/login", json={"username": "nobody", "password": "x-x-x-x-x"}, headers=h)
    assert db.query(AuditEvent).filter_by(username="nobody").one().ip != "6.6.6.6"
    monkeypatch.setattr(audit.settings, "trust_proxy", True)
    TestClient(app).post("/api/auth/login", json={"username": "nobody2", "password": "x-x-x-x-x"}, headers=h)
    assert db.query(AuditEvent).filter_by(username="nobody2").one().ip == "6.6.6.6"


# ---- reads and labels -------------------------------------------------------------------------------------

def test_sensitive_reads_and_labels(client):
    client.get("/api/items/export.xlsx")
    client.get("/api/items")  # ordinary read: not recorded
    s = client.post("/api/counts", json={"counted_at": "2026-11-01T10:00:00Z"}).json()
    ev = _events(client)
    reads = [e for e in ev if e["action"] == "read"]
    assert [e["route"] for e in reads] == ["/api/items/export.xlsx"] and reads[0]["label"] == "Downloaded stock items sheet"
    assert not any(e["route"] == "/api/items" and e["method"] == "GET" for e in ev)
    started = next(e for e in ev if e["route"] == "/api/counts" and e["method"] == "POST")
    assert started["label"] == "Started count" and started["link"] is None
    post = client.post(f"/api/counts/{s['id']}/post")
    e = next(e for e in _events(client) if e["route"] == "/api/counts/{session_id}/post")
    assert e["label"] == "Posted count" and e["link"] == f"/counts/{s['id']}", post.text
    login = next(e for e in ev if e["action"] == "login")
    assert login["label"] == "Logged in"
