"""Tenants, businesses and the platform console: isolation between tenants and between a tenant's businesses,
per-business permissions, switching, logos, limits, suspension and usage metering."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import app
from app.models import AiUsage, AuditEvent, InventoryItem, Location, Tenant, User
from app.services import receipts, usage

PW = "Bar-stock-77"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
ITEM = {"sku": "V1", "name": "Vodka", "stock_unit": "ml", "pack_size": "750"}


@pytest.fixture(autouse=True)
def no_delay(monkeypatch):
    monkeypatch.setattr(auth, "FAIL_DELAY_S", 0)


def _login(username, password=PW):
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


def _tenant(platform, name="Other Co", admin="olga", business="Other bar", **limits):
    r = platform.post("/api/platform/tenants", json={
        "name": name, "business_name": business, **limits,
        "admin": {"username": admin, "display_name": admin.title(), "password": PW}})
    assert r.status_code == 201, r.text
    return r.json()


def _business(client, name="Second bar", **kw):
    r = client.post("/api/businesses", json={"name": name, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def _switch(client, business_id):
    r = client.post("/api/auth/business", json={"business_id": business_id})
    assert r.status_code == 200, r.text
    return r.json()["user"]


# ---- seed and me --------------------------------------------------------------------------------------------

def test_first_start_has_a_tenant_a_business_and_a_platform_admin(client, db):
    me = client.get("/api/auth/me").json()["user"]
    assert me["is_admin"] and not me["is_platform_admin"]
    assert me["tenant"]["name"] and me["business"]["name"] == "Main bar"
    assert [b["id"] for b in me["businesses"]] == [me["business"]["id"]]
    p = db.query(User).filter(User.is_platform_admin.is_(True)).execution_options(unscoped=True).one()
    assert p.username == "platform" and p.tenant_id is None and not p.is_admin


def test_platform_admin_has_no_business_and_tenant_users_no_console(client, platform):
    me = platform.get("/api/auth/me").json()["user"]
    assert me["is_platform_admin"] and me["tenant"] is None and me["business"] is None and me["permissions"] == {}
    assert platform.get("/api/items").status_code == 403
    assert platform.get("/api/users").status_code == 403
    assert platform.post("/api/items", json=ITEM).status_code == 403
    assert client.get("/api/platform/tenants").status_code == 403
    assert client.get("/api/platform/overview").status_code == 403


# ---- tenants are walled off ---------------------------------------------------------------------------------

def test_tenants_dont_see_each_other(client, platform, db):
    _tenant(platform)
    other = _login("olga")
    assert other.get("/api/auth/me").json()["user"]["business"]["name"] == "Other bar"

    mine = client.post("/api/items", json=ITEM).json()
    theirs = other.post("/api/items", json={**ITEM, "name": "Their vodka"})  # the same SKU is fine in another tenant
    assert theirs.status_code == 201
    assert [i["name"] for i in client.get("/api/items").json()] == ["Vodka"]
    assert [i["name"] for i in other.get("/api/items").json()] == ["Their vodka"]
    # by id, another tenant's rows don't exist
    assert other.patch(f"/api/items/{mine['id']}", json={"name": "Stolen"}).status_code == 404
    assert client.patch(f"/api/items/{theirs.json()['id']}", json={"name": "Stolen"}).status_code == 404
    assert db.get(InventoryItem, mine["id"]).name == "Vodka"

    # users, businesses and the audit log stay inside the tenant
    assert [u["username"] for u in other.get("/api/users").json()] == ["olga"]
    assert "olga" not in [u["username"] for u in client.get("/api/users").json()]
    admin_id = next(u["id"] for u in client.get("/api/users").json() if u["username"] == "admin")
    assert other.patch(f"/api/users/{admin_id}", json={"display_name": "Hacked"}).status_code == 404
    assert other.post(f"/api/users/{admin_id}/password", json={"password": PW}).status_code == 404
    assert [b["name"] for b in other.get("/api/businesses").json()] == ["Other bar"]
    my_business = client.get("/api/auth/me").json()["user"]["business"]["id"]
    assert other.post("/api/auth/business", json={"business_id": my_business}).status_code == 403
    assert other.patch(f"/api/businesses/{my_business}", json={"name": "Taken"}).status_code == 404
    seen = {e["username"] for e in other.get("/api/audit").json()["events"]}
    assert seen == {"olga"}
    assert "olga" not in {e["username"] for e in client.get("/api/audit").json()["events"]}
    ev = client.get("/api/audit").json()["events"][0]
    assert other.get(f"/api/audit/{ev['id']}/changes").status_code == 404

    # settings are the business's own
    assert other.put("/api/settings", json={"variance_threshold_pct": "55"}).status_code == 200
    assert client.get("/api/settings").json()["variance_threshold_pct"] != "55"


def test_usernames_are_unique_across_tenants(client, platform):
    _tenant(platform)
    other = _login("olga")
    r = other.post("/api/users", json={"username": "admin", "display_name": "Admin Two", "password": PW})
    assert r.status_code == 409
    r = platform.post("/api/platform/tenants", json={"name": "Third", "admin": {"username": "olga", "display_name": "O", "password": PW}})
    assert r.status_code == 409


# ---- businesses inside a tenant -----------------------------------------------------------------------------

def test_second_business_has_its_own_data_and_switching(client, db):
    first = client.get("/api/auth/me").json()["user"]["business"]
    client.post("/api/items", json=ITEM)
    assert client.post("/api/groups", json={"name": "Spirits", "stock_unit": "ml", "pack_size": "750"}).status_code == 201
    second = _business(client, "Patio bar", kind="bar", address="12 Main St", phone="604 555 0100")
    assert second["address"] == "12 Main St" and second["phone"] == "604 555 0100"
    assert client.post("/api/businesses", json={"name": "patio bar"}).status_code == 409

    me = _switch(client, second["id"])
    assert me["business"]["id"] == second["id"] and len(me["businesses"]) == 2
    assert client.get("/api/items").json() == [] and client.get("/api/groups").json() == []
    assert client.post("/api/items", json={**ITEM, "name": "Patio vodka"}).status_code == 201  # same SKU, other business
    assert client.post("/api/groups", json={"name": "Spirits", "stock_unit": "ml", "pack_size": "1140"}).status_code == 201
    assert client.post("/api/counts", json={"counted_at": "2026-11-01T10:00:00Z"}).status_code == 201
    assert len(client.get("/api/counts").json()) == 1

    _switch(client, first["id"])
    assert [i["name"] for i in client.get("/api/items").json()] == ["Vodka"]
    assert client.get("/api/counts").json() == []
    assert client.get("/api/groups").json()[0]["pack_size"] in ("750", "750.0000")
    # where the login starts next time is remembered
    _switch(client, second["id"])
    again = _login("admin", "test-pass")
    assert again.get("/api/auth/me").json()["user"]["business"]["id"] == second["id"]
    assert db.query(InventoryItem).execution_options(unscoped=True).count() == 2


def test_my_prefs_are_per_business(client):
    """The Stock vs sales chart a login picked in one business does not follow it into the next."""
    first = client.get("/api/auth/me").json()["user"]["business"]
    assert client.put("/api/settings/me", json={"variance_chart_style": "difference"}).status_code == 200
    second = _business(client, "Patio bar")
    _switch(client, second["id"])
    assert client.get("/api/settings/me").json() == {"variance_chart_style": "bars"}
    assert client.put("/api/settings/me", json={"variance_chart_style": "gap"}).status_code == 200
    _switch(client, first["id"])
    assert client.get("/api/settings/me").json() == {"variance_chart_style": "difference"}


def test_a_stale_tab_cannot_write_into_the_other_business(client):
    first = client.get("/api/auth/me").json()["user"]["business"]["id"]
    second = _business(client)["id"]
    _switch(client, second)
    r = client.post("/api/items", json=ITEM, headers={"X-Business": first})  # a tab still showing the first business
    assert r.status_code == 409 and r.headers["X-Business-Changed"] == "1"
    assert client.get("/api/items", headers={"X-Business": second}).json() == []


def test_new_business_can_start_from_another_ones_catalogue(client, db):
    first = client.get("/api/auth/me").json()["user"]["business"]["id"]
    assert client.post("/api/groups", json={"name": "Spirits", "stock_unit": "ml", "pack_size": "750"}).status_code == 201
    item = client.post("/api/items", json={**ITEM, "category": "Spirits"}).json()
    assert client.put("/api/recipes/00000330", json={"name": "Vodka single", "effective_from": "2026-01-01",
                                                      "lines": [{"item_id": item["id"], "qty": "1", "unit": "oz"}]}).status_code == 200
    b = _business(client, "Copy bar", copy_from=first, copy_what=["recipes"])
    assert b["copied"] == {"items": 1, "groups": 1, "recipes": 1}
    _switch(client, b["id"])
    items = client.get("/api/items").json()
    assert [i["sku"] for i in items] == ["V1"] and items[0]["id"] != item["id"]
    recipe = client.get("/api/recipes/00000330").json()
    assert recipe["lines"][0]["item_id"] == items[0]["id"]  # points at the copy, not the original
    assert client.get("/api/counts").json() == []


def test_permissions_are_per_business(client):
    first = client.get("/api/auth/me").json()["user"]["business"]["id"]
    second = _business(client)["id"]
    r = client.post("/api/users", json={"username": "sam", "display_name": "Sam", "password": PW,
                                        "access": {first: {"counts": "edit"}, second: {"counts": "view", "items": "view"}}})
    assert r.status_code == 201 and set(r.json()["access"]) == {first, second}
    sam = _login("sam")
    me = sam.get("/api/auth/me").json()["user"]
    assert me["permissions"] == {"counts": "edit"} and len(me["businesses"]) == 2 and not me["is_admin"]
    assert sam.post("/api/counts", json={"counted_at": "2026-11-01T10:00:00Z"}).status_code == 201
    assert sam.get("/api/items").status_code == 403

    assert _switch(sam, second)["permissions"] == {"counts": "view", "items": "view"}
    assert sam.post("/api/counts", json={"counted_at": "2026-11-01T10:00:00Z"}).status_code == 403
    assert sam.get("/api/items").status_code == 200
    assert sam.post("/api/businesses", json={"name": "Mine"}).status_code == 403
    assert [b["id"] for b in sam.get("/api/businesses").json()] and "swype_email" not in sam.get("/api/businesses").json()[0]

    # taken out of the second business: the session falls back to the first at the next request
    uid = r.json()["id"]
    assert client.delete(f"/api/businesses/{second}/members/{uid}").status_code == 200
    me = sam.get("/api/auth/me").json()["user"]
    assert me["business"]["id"] == first and [b["id"] for b in me["businesses"]] == [first]
    assert sam.post("/api/auth/business", json={"business_id": second}).status_code == 403
    # the old single-business way still works: permissions apply to the business the admin is in
    assert client.patch(f"/api/users/{uid}", json={"permissions": {"variance": "view"}}).json()["access"] == {first: {"variance": "view"}}
    assert client.patch(f"/api/users/{uid}", json={"access": {"nope": {}}}).status_code == 422


def test_member_list_and_access_from_the_business_side(client):
    second = _business(client)["id"]
    uid = client.post("/api/users", json={"username": "kim", "display_name": "Kim", "password": PW}).json()["id"]
    rows = {m["username"]: m for m in client.get(f"/api/businesses/{second}/members").json()}
    assert rows["admin"]["member"] and not rows["kim"]["member"]
    assert client.put(f"/api/businesses/{second}/members/{uid}", json={"permissions": {"deliveries": "edit"}}).status_code == 200
    assert client.put(f"/api/businesses/{second}/members/{uid}", json={"permissions": {"deliveries": "boss"}}).status_code == 422
    rows = {m["username"]: m for m in client.get(f"/api/businesses/{second}/members").json()}
    assert rows["kim"]["member"] and rows["kim"]["permissions"] == {"deliveries": "edit"}


def test_logo_and_business_details(client, platform):
    b = client.get("/api/auth/me").json()["user"]["business"]
    assert b["logo_at"] is None and client.get(f"/api/businesses/{b['id']}/logo").status_code == 404
    assert client.put(f"/api/businesses/{b['id']}/logo", files={"file": ("l.svg", b"<svg/>", "image/svg+xml")}).status_code == 400
    assert client.put(f"/api/businesses/{b['id']}/logo", files={"file": ("l.png", b"x" * (1024 * 1024 + 1), "image/png")}).status_code == 400
    r = client.put(f"/api/businesses/{b['id']}/logo", files={"file": ("l.png", PNG, "image/png")})
    assert r.status_code == 200 and r.json()["logo_at"]
    got = client.get(f"/api/businesses/{b['id']}/logo")
    assert got.content == PNG and got.headers["content-type"] == "image/png"
    assert client.get("/api/auth/me").json()["user"]["business"]["logo_at"]
    # another tenant can't fetch it
    _tenant(platform)
    assert _login("olga").get(f"/api/businesses/{b['id']}/logo").status_code == 404
    assert client.delete(f"/api/businesses/{b['id']}/logo").json()["logo_at"] is None

    r = client.patch(f"/api/businesses/{b['id']}", json={"name": "Barney's", "city": "Vancouver", "email": "hi@bar.example",
                                                         "swype_email": "pos@bar.example", "swype_password": "s3cret", "swype_company": "barneys"})
    out = r.json()
    assert out["name"] == "Barney's" and out["swype_password_set"] and "swype_password" not in out
    assert client.get("/api/imports/swype-item-sales/fetch").json()["configured"] is True
    assert client.patch(f"/api/businesses/{b['id']}", json={"is_active": False}).status_code == 409  # the only one
    assert client.patch(f"/api/businesses/{b['id']}", json={"kind": "spaceship"}).status_code == 422


def test_swype_password_is_encrypted_and_never_audited(client, db):
    b = client.get("/api/auth/me").json()["user"]["business"]["id"]
    client.patch(f"/api/businesses/{b}", json={"swype_email": "pos@bar.example", "swype_password": "s3cret", "swype_company": "barneys"})
    db.expire_all()
    stored = db.get(Location, b).swype_password
    assert stored and "s3cret" not in stored
    ev = client.get("/api/audit").json()["events"][0]
    assert ev["detail"]["body"]["swype_password"] == "***"
    changes = client.get(f"/api/audit/{ev['id']}/changes").json()
    assert "s3cret" not in str(changes) and stored not in str(changes)


# ---- the platform console -----------------------------------------------------------------------------------

def test_limits(client, platform):
    t = _tenant(platform, max_businesses=1, max_users=2)
    other = _login("olga")
    assert other.post("/api/businesses", json={"name": "Two"}).status_code == 409
    assert other.post("/api/users", json={"username": "u2", "display_name": "U2", "password": PW}).status_code == 201
    r = other.post("/api/users", json={"username": "u3", "display_name": "U3", "password": PW})
    assert r.status_code == 409 and "limited to 2" in r.json()["detail"]
    assert platform.patch(f"/api/platform/tenants/{t['id']}", json={"max_businesses": None, "max_users": 5}).status_code == 200
    assert other.post("/api/businesses", json={"name": "Two"}).status_code == 201
    assert other.post("/api/users", json={"username": "u3", "display_name": "U3", "password": PW}).status_code == 201
    assert client.post("/api/businesses", json={"name": "Unlimited"}).status_code == 201  # no limits on the first tenant


def test_suspend_logs_the_tenant_out_and_reactivate_lets_it_back(client, platform, db):
    t = _tenant(platform)
    other = _login("olga")
    assert other.get("/api/items").status_code == 200
    assert platform.post(f"/api/platform/tenants/{t['id']}/suspend").json()["status"] == "suspended"
    assert other.get("/api/items").status_code == 401
    r = TestClient(app).post("/api/auth/login", json={"username": "olga", "password": PW})
    assert r.status_code == 403 and "suspended" in r.json()["detail"]
    assert client.get("/api/items").status_code == 200  # other tenants carry on
    assert platform.post(f"/api/platform/tenants/{t['id']}/reactivate").json()["status"] == "active"
    assert _login("olga").get("/api/items").status_code == 200


def test_receipt_reads_are_metered_and_capped(client, platform, db, monkeypatch):
    def read(data, ctype, meter=None):
        meter("claude-opus-5-5", SimpleNamespace(input_tokens=1000, output_tokens=500, cache_creation_input_tokens=0, cache_read_input_tokens=0), True)
        return {"store": "BCL", "store_address": None, "date": None, "time": None, "receipt_no": None, "lines": [],
                "subtotal": None, "tax": None, "total": None}
    monkeypatch.setattr(receipts, "enabled", lambda: True)
    monkeypatch.setattr(receipts, "read", read)
    up = lambda: client.post("/api/deliveries/receipt", files={"file": ("r.jpg", b"jpg", "image/jpeg")})  # noqa: E731
    assert up().json()["read_error"] is None
    row = db.query(AiUsage).one()
    me = client.get("/api/auth/me").json()["user"]
    assert (row.tenant_id, row.location_id, row.user_id) == (me["tenant"]["id"], me["business"]["id"], me["id"])
    assert row.input_tokens == 1000 and row.output_tokens == 500
    assert float(row.cost_usd) == pytest.approx(1000 * 4 / 1e6 + 500 * 20 / 1e6)

    tenants = platform.get("/api/platform/tenants").json()["tenants"]
    mine = next(t for t in tenants if t["id"] == me["tenant"]["id"])
    assert mine["tokens"] == 1500 and mine["receipts_read"] == 1 and mine["cost_usd"] == pytest.approx(0.014)
    assert platform.get("/api/platform/overview").json()["tokens"] == 1500
    assert platform.get("/api/platform/usage").json()["rows"][0]["business"] == "Main bar"

    # a cap below what's used: the next receipt is stored but not read
    platform.patch(f"/api/platform/tenants/{mine['id']}", json={"monthly_token_cap": 1000})
    d = up().json()
    assert "allowance is used up" in d["read_error"] and d["status"] == "draft"
    assert db.query(AiUsage).count() == 1
    assert next(t for t in platform.get("/api/platform/tenants").json()["tenants"] if t["id"] == mine["id"])["cap_used_pct"] == 150.0


def test_cost_uses_cache_rates_and_unknown_models_are_free():
    u = SimpleNamespace(input_tokens=1_000_000, output_tokens=0, cache_creation_input_tokens=1_000_000, cache_read_input_tokens=1_000_000)
    assert float(usage.cost("claude-opus-5-5", u)) == pytest.approx(4 + 5 + 0.4)
    assert usage.cost("some-future-model", u) == 0


def test_tenant_detail_and_monthly_report(client, platform):
    t = _tenant(platform, plan="Pro")
    other = _login("olga")
    other.post("/api/items", json=ITEM)
    d = platform.get(f"/api/platform/tenants/{t['id']}").json()
    assert d["tenant"]["plan"] == "Pro" and d["tenant"]["users"] == 1 and d["tenant"]["logins"] >= 1
    assert [b["name"] for b in d["businesses"]] == ["Other bar"] and d["businesses"][0]["items"] == 1
    assert d["users"][0]["username"] == "olga" and d["users"][0]["is_admin"]
    assert len(d["months"]) == 12 and len(d["daily"]) == 30

    rep = platform.get("/api/platform/reports/monthly").json()
    row = next(r for r in rep["rows"] if r["id"] == t["id"])
    assert row["active_users"] == 1 and row["changes"] >= 1 and rep["totals"]["businesses"] == 2
    csv = platform.get("/api/platform/reports/monthly?format=csv")
    assert csv.headers["content-type"].startswith("text/csv") and "Other Co" in csv.text and csv.text.startswith("Tenant,")
    x = platform.get("/api/platform/reports/monthly?format=xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    assert platform.get("/api/platform/reports/monthly?month=nope").status_code == 422
    assert platform.get("/api/platform/reports/monthly?month=2020-01").json()["rows"][0]["logins"] == 0


def test_platform_audit_and_admins(client, platform, db):
    t = _tenant(platform)
    ev = platform.get("/api/platform/audit?platform_only=true").json()["events"]
    assert ev[0]["label"] == "Added tenant" and ev[0]["username"] == "platform"
    assert db.query(AuditEvent).filter(AuditEvent.area == "platform").first().tenant_id is None
    # what the platform does is not in any tenant's audit log
    assert "platform" not in {e["username"] for e in client.get("/api/audit").json()["events"]}
    assert platform.get(f"/api/platform/audit?tenant_id={t['id']}").json()["total"] == 0
    _login("olga")
    assert platform.get(f"/api/platform/audit?tenant_id={t['id']}").json()["events"][0]["tenant"] == "Other Co"
    assert platform.get("/api/platform/audit/verify").json()["ok"]

    assert platform.post("/api/platform/admins", json={"username": "ops", "display_name": "Ops", "password": PW}).status_code == 201
    ops = _login("ops")
    assert ops.get("/api/platform/overview").status_code == 200
    me = platform.get("/api/auth/me").json()["user"]["id"]
    assert platform.post(f"/api/platform/admins/{me}/active?active=false").status_code == 409
    r = platform.post(f"/api/platform/tenants/{t['id']}/admins", json={"username": "second", "display_name": "Second", "password": PW})
    assert r.status_code == 201 and _login("second").get("/api/users").status_code == 200


def test_audit_log_is_per_business_and_whole_in_the_console(client, platform, db):
    first = client.get("/api/auth/me").json()["user"]["business"]
    client.post("/api/users", json={"username": "sam", "display_name": "Sam", "password": PW, "permissions": {"audit": "view", "items": "edit"}})
    client.post("/api/items", json=ITEM)
    second = _business(client)
    _switch(client, second["id"])
    client.post("/api/items", json={**ITEM, "name": "Second vodka"})

    def labels(c, **q):
        return [e["label"] for e in c.get("/api/audit", params={"limit": 200, **q}).json()["events"]]

    # the admin sees the business they are in, not the tenant
    assert labels(client).count("Added stock item") == 1 and "Added business" not in labels(client)
    in_second = client.get("/api/audit").json()["events"]
    added = next(e for e in in_second if e["label"] == "Added stock item")
    assert client.get(f"/api/audit/{added['id']}/changes").json()[0]["changes"]["name"][1] == "Second vodka"
    assert "Added business" not in labels(client, business_id=first["id"])  # no way to ask for another one
    _switch(client, first["id"])
    assert "Added business" in labels(client) and labels(client).count("Added stock item") == 1
    assert client.get(f"/api/audit/{added['id']}/changes").status_code == 404  # the other business's entry
    assert [p["username"] for p in client.get("/api/audit/users").json()] == ["admin", "sam"]

    # entries of no business (a failed login) show to the tenant's admins only
    assert TestClient(app).post("/api/auth/login", json={"username": "sam", "password": "wrong-pass-1"}).status_code == 401
    assert "Failed login" in labels(client)
    sam = _login("sam")
    assert "Failed login" not in labels(sam) and "Added stock item" in labels(sam)
    _switch(client, second["id"])
    assert [p["username"] for p in client.get("/api/audit/users").json()] == ["admin"]  # sam is not in the second one

    # the console: everything, with where it happened
    t = _tenant(platform)
    _login("olga").post("/api/items", json=ITEM)
    everything = platform.get("/api/platform/audit?limit=500").json()["events"]
    where = {(e["tenant"], e["business"]) for e in everything}
    assert {("First tenant", first["name"]), ("First tenant", "Second bar"), ("Other Co", "Other bar"), (None, None)} <= where
    assert all(e["link"] is None for e in everything)
    assert {e["business"] for e in platform.get(f"/api/platform/audit?business_id={second['id']}").json()["events"]} == {"Second bar"}
    assert {e["tenant"] for e in platform.get(f"/api/platform/audit?tenant_id={t['id']}").json()["events"]} == {"Other Co"}
    assert {e["tenant"] for e in platform.get("/api/platform/audit?tenant_id=none").json()["events"]} == {None}
    assert {e["label"] for e in platform.get("/api/platform/audit?action=login_failed").json()["events"]} == {"Failed login"}
    assert platform.get(f"/api/platform/audit/{added['id']}/changes").json()[0]["changes"]["name"][1] == "Second vodka"
    assert platform.get("/api/platform/audit?platform_only=true").json()["events"][0]["label"] == "Viewed audit details"
    names = {x["name"]: [b["name"] for b in x["businesses"]] for x in platform.get("/api/platform/audit/filters").json()}
    assert names["Other Co"] == ["Other bar"] and "Second bar" in names["First tenant"]
    assert client.get("/api/platform/audit").status_code == 403


def test_a_tenant_with_no_business_yet(platform, db):
    r = platform.post("/api/platform/tenants", json={"name": "Empty", "admin": {"username": "eve", "display_name": "Eve", "password": PW}})
    assert r.status_code == 201
    eve = _login("eve")
    me = eve.get("/api/auth/me").json()["user"]
    assert me["business"] is None and me["businesses"] == [] and me["is_admin"]
    assert eve.get("/api/items").json() == []  # nothing to see, and nothing of anyone else's
    assert eve.post("/api/items", json=ITEM).status_code == 409
    b = _business(eve, "First")
    assert eve.get("/api/auth/me").json()["user"]["business"]["id"] == b["id"]
    assert eve.post("/api/items", json=ITEM).status_code == 201
    assert db.query(Tenant).count() == 2
