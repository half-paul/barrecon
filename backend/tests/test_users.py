from fastapi.testclient import TestClient

from app.main import app
from app.models import AuditEvent, User


def _login(username: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


def _make(client, username="sam", permissions=None, **kw):
    r = client.post("/api/users", json={"username": username, "display_name": username.title(), "password": "pass-word-1",
                                         "permissions": permissions or {}, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_seeded_admin_and_me(client):
    me = client.get("/api/auth/me").json()["user"]
    assert me["username"] == "admin" and me["is_admin"]
    assert me["permissions"]["counts"] == "edit" and me["permissions"]["variance"] == "view"


def test_view_only_user_can_read_not_write(client, db):
    _make(client, "sam", {"counts": "view"})
    sam = _login("sam", "pass-word-1")
    assert sam.get("/api/counts").status_code == 200
    r = sam.post("/api/counts", json={"counted_at": "2026-11-01T10:00:00Z"})
    assert r.status_code == 403 and "view" in r.json()["detail"]
    assert sam.get("/api/items").status_code == 403
    assert sam.get("/api/users").status_code == 403
    me = sam.get("/api/auth/me").json()["user"]
    assert me["permissions"] == {"counts": "view"}


def test_edit_user_can_write_and_is_audited(client, db):
    u = _make(client, "kit", {"counts": "edit"})
    kit = _login("kit", "pass-word-1")
    r = kit.post("/api/counts", json={"counted_at": "2026-11-01T10:00:00Z"})
    assert r.status_code == 201
    kit.put("/api/settings", json={"oz_ml": "30"})  # denied
    ev = client.get(f"/api/audit?user_id={u['id']}").json()
    actions = [(e["action"], e["method"], e["route"], e["status"]) for e in ev["events"]]
    assert ("change", "POST", "/api/counts", 201) in actions
    assert ("denied", "PUT", "/api/settings", 403) in actions
    assert any(a[0] == "login" for a in actions)
    created = next(e for e in ev["events"] if e["status"] == 201)
    assert created["area"] == "counts" and created["path"] == "/api/counts"
    assert created["detail"]["body"]["counted_at"].startswith("2026-11-01")


def test_failed_login_recorded_and_passwords_redacted(client, db):
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
    assert c.post("/api/auth/login", json={"username": "ghost", "password": "nope"}).status_code == 401
    fails = db.query(AuditEvent).filter_by(action="login_failed").all()
    assert {f.username for f in fails} == {"admin", "ghost"}
    u = _make(client, "pat")
    client.post(f"/api/users/{u['id']}/password", json={"password": "new-secret-9"})
    ev = db.query(AuditEvent).filter(AuditEvent.route == "/api/users/{user_id}/password").one()
    assert ev.detail["body"]["password"] == "***"
    create = db.query(AuditEvent).filter(AuditEvent.route == "/api/users", AuditEvent.method == "POST").one()
    assert create.detail["body"]["password"] == "***" and create.area == "users"


def test_deactivate_and_reset_log_user_out(client, db):
    u = _make(client, "lee", {"variance": "view"})
    lee = _login("lee", "pass-word-1")
    assert lee.get("/api/variance/periods").status_code == 200
    client.post(f"/api/users/{u['id']}/password", json={"password": "another-pw-2"})
    assert lee.get("/api/variance/periods").status_code == 401
    lee = _login("lee", "another-pw-2")
    client.patch(f"/api/users/{u['id']}", json={"is_active": False})
    assert lee.get("/api/variance/periods").status_code == 401
    assert TestClient(app).post("/api/auth/login", json={"username": "lee", "password": "another-pw-2"}).status_code == 401


def test_permission_change_applies_immediately(client):
    u = _make(client, "max", {"recipes": "view"})
    m = _login("max", "pass-word-1")
    assert m.get("/api/recipes/menu").status_code == 200
    client.patch(f"/api/users/{u['id']}", json={"permissions": {}})
    assert m.get("/api/recipes/menu").status_code == 403


def test_last_admin_guard_and_validation(client, db):
    admin = db.query(User).filter_by(username="admin").one()
    assert client.patch(f"/api/users/{admin.id}", json={"is_admin": False}).status_code == 409
    assert client.post("/api/users", json={"username": "x1", "display_name": "X", "password": "pass-word-1",
                                           "permissions": {"variance": "edit"}}).status_code == 422
    assert client.post("/api/users", json={"username": "x2", "display_name": "X", "password": "short"}).status_code == 422
    _make(client, "dup")
    assert client.post("/api/users", json={"username": "DUP", "display_name": "X", "password": "pass-word-1"}).status_code == 409
    # a second admin can demote the first
    _make(client, "boss", is_admin=True)
    boss = _login("boss", "pass-word-1")
    assert boss.patch(f"/api/users/{admin.id}", json={"is_admin": False}).status_code == 200


def test_change_own_password(client):
    _make(client, "ann", {"counts": "view"})
    a = _login("ann", "pass-word-1")
    assert a.post("/api/auth/password", json={"current_password": "wrong", "new_password": "pass-word-2"}).status_code == 400
    assert a.post("/api/auth/password", json={"current_password": "pass-word-1", "new_password": "pass-word-2"}).status_code == 200
    assert a.get("/api/counts").status_code == 200  # this session keeps working
    _login("ann", "pass-word-2")


def test_audit_viewer_and_filters(client):
    _make(client, "aud", {"audit": "view"})
    a = _login("aud", "pass-word-1")
    assert a.get("/api/audit/users").status_code == 200
    r = a.get("/api/audit?action=login").json()
    assert r["total"] >= 2 and all(e["action"] == "login" for e in r["events"])
    assert a.get("/api/audit?date_from=2000-01-01&date_to=2000-01-02").json()["total"] == 0
    assert a.get("/api/users").status_code == 403


def test_delete_user_keeps_audit_log(client, db):
    u = _make(client, "gone", {"counts": "edit"}, email="gone@bar.example")
    gone = _login("gone", "pass-word-1")
    assert gone.post("/api/counts", json={"counted_at": "2026-11-01T10:00:00Z"}).status_code == 201
    before = client.get(f"/api/audit?user_id={u['id']}").json()["total"]

    assert client.delete(f"/api/users/{u['id']}").status_code == 204
    # gone from the user list, can't log in, the open session is dead
    assert u["id"] not in [x["id"] for x in client.get("/api/users").json()]
    assert gone.get("/api/auth/me").json()["user"] is None
    assert TestClient(app).post("/api/auth/login", json={"username": "gone", "password": "pass-word-1"}).status_code == 401
    row = db.get(User, u["id"])
    db.refresh(row)
    assert row.deleted_at and not row.is_active and row.email is None and row.username != "gone"
    # every entry stays, still under their name, and the chain still verifies
    ev = client.get(f"/api/audit?user_id={u['id']}").json()
    assert ev["total"] >= before and all(e["username"] == "gone" for e in ev["events"] if e["action"] != "login_failed")
    assert client.get("/api/audit/verify").json()["ok"]
    people = {p["id"]: p for p in client.get("/api/audit/users").json()}
    assert people[u["id"]]["deleted"] and people[u["id"]]["display_name"] == "Gone"
    # the username and email can be used again; the deleted login can't be touched
    _make(client, "gone", email="gone@bar.example")
    assert client.patch(f"/api/users/{u['id']}", json={"is_active": True}).status_code == 404
    assert client.delete(f"/api/users/{u['id']}").status_code == 404


def test_cannot_delete_self_or_last_admin(client, db):
    me = client.get("/api/auth/me").json()["user"]
    assert client.delete(f"/api/users/{me['id']}").status_code == 409
    other = _make(client, "boss", is_admin=True)
    boss = _login("boss", "pass-word-1")
    assert boss.delete(f"/api/users/{me['id']}").status_code == 204
    assert boss.delete(f"/api/users/{other['id']}").status_code == 409
