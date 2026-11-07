import io

from openpyxl import Workbook

from app.models import ImportBatch, SwypeItemSales
from app.services import swype_item_sales as sis

BLANK = [""] * 15


def _row(b="", c="", qty=None, amt=None, a=""):
    r = list(BLANK)
    r[0], r[1], r[2] = a, b, c
    if qty is not None:
        r[4], r[5], r[8], r[11], r[12], r[13], r[14] = qty, 0, amt, amt, 0, 0, qty
    return r


def report(period="From 10/19/2026 to 10/25/2026\nAll Price Type", draft_qty=10, grand_qty=None) -> bytes:
    """Mirror of the Swype export layout, small enough to read."""
    wb = Workbook()
    ws = wb.active
    rows = [
        [None] * 7 + ["Caffe Barney"],
        ["Item Sales"], [], [],
        [period], [],
        ["", "", "", "", "Sales"],
        ["Item #", None, "Description", "", "Qty", "Combo Qty", None, None, "Amt.", None, "", "Net Amt.", "Promo Qty", "Free Qty", "Total Qty"],
        _row(a="Liquor"),
        _row("Booze"),
        _row("00000330_01", "Smirnoff Vodka SNG", 4, 32.0),
        _row("00000330_02", "Smirnoff Vodka DBL", 2, 24.0),
        _row("Booze     Subtotal", "", 6, 56.0),
        _row("Draft Beer"),
        _row("00000400", "Lager 20oz", draft_qty, 90.0),
        _row("Draft Beer     Subtotal", "", draft_qty, 90.0),
        _row("Liquor    Total", "", 6 + draft_qty, 146.0),
        _row("", "Grand Total", grand_qty if grand_qty is not None else 6 + draft_qty, 146.0),
        [], ["Total Qty = Sales + Promo + Free+ Combo"],
    ]
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_reads_period_and_items():
    p = sis.parse(report())
    assert (p.period_from.isoformat(), p.period_to.isoformat()) == ("2026-10-19", "2026-10-25")
    assert p.price_type == "All Price Type"
    assert [(ln["category"], ln["item_code"], ln["total_qty"]) for ln in p.lines] == [
        ("Booze", "00000330_01", 4), ("Booze", "00000330_02", 2), ("Draft Beer", "00000400", 10)]
    assert all(ln["department"] == "Liquor" for ln in p.lines)


def test_parse_rejects_totals_that_do_not_add_up():
    try:
        sis.parse(report(grand_qty=99))
    except sis.ReportError as e:
        assert "Grand Total" in str(e)
    else:
        raise AssertionError("expected ReportError")


def test_apply_replaces_same_period_and_rejects_overlap(db, location):
    r1 = sis.apply(db, report(), "wk1.xlsx", location.id)
    assert r1["status"] == "ok" and r1["inserted"] == 3
    assert sis.apply(db, report(), "again.xlsx", location.id)["error"] == "this exact file was already imported"

    # late void in Swype: same period re-exported with different numbers replaces the rows
    r2 = sis.apply(db, report(draft_qty=9), "wk1b.xlsx", location.id)
    assert (r2["inserted"], r2["updated"], r2["unchanged"]) == (0, 1, 2)
    assert db.query(SwypeItemSales).count() == 3
    assert db.query(SwypeItemSales).filter_by(item_code="00000400").one().total_qty == 9

    r3 = sis.apply(db, report(period="From 10/22/2026 to 10/28/2026\nAll Price Type"), "x.xlsx", location.id)
    assert r3["status"] == "rejected" and "overlaps" in r3["error"]
    assert db.query(SwypeItemSales).count() == 3
    assert db.query(ImportBatch).filter_by(dataset="item_sales", status="rejected").count() == 2


def test_api_preview_apply_periods(client):
    f = {"file": ("item_sales.xlsx", report(), "application/octet-stream")}
    pv = client.post("/api/imports/swype-item-sales/preview", files=f).json()
    assert pv["items"] == 3 and pv["departments"][0]["department"] == "Liquor"
    assert client.post("/api/imports/swype-item-sales", files=f).json()["inserted"] == 3
    r = client.post("/api/imports/swype-item-sales", files=f)
    assert r.status_code == 400 and "already imported" in r.json()["detail"]
    assert client.get("/api/imports/swype-item-sales/periods").json() == [
        {"period_from": "2026-10-19", "period_to": "2026-10-25", "items": 3, "total_qty": 16.0, "net_amount": 146.0}]
    assert client.delete("/api/imports/swype-item-sales/periods/2026-10-19/2026-10-25").json() == {"deleted": 3}


def test_api_fetch_from_swype(client, monkeypatch):
    from app.services import swype_api
    asked = []

    def fake(df, dt):
        asked.append((df, dt))
        return report()
    monkeypatch.setattr(swype_api, "fetch_item_sales", fake)
    r = client.post("/api/imports/swype-item-sales/fetch", json={"date_from": "2026-10-19", "date_to": "2026-10-25"})
    assert r.status_code == 200 and r.json()["inserted"] == 3
    assert str(asked[0][0]) == "2026-10-19"
    assert client.get("/api/imports/swype-item-sales/periods").json()[0]["items"] == 3
    batch = client.get("/api/imports").json()[0]
    assert batch["source"] == "swype_api"

    r = client.post("/api/imports/swype-item-sales/fetch", json={"date_from": "2026-10-25", "date_to": "2026-10-19"})
    assert r.status_code == 400

    def down(df, dt):
        raise swype_api.SwypeError("Swype has no sales from x to y")
    monkeypatch.setattr(swype_api, "fetch_item_sales", down)
    r = client.post("/api/imports/swype-item-sales/fetch", json={"date_from": "2026-10-19", "date_to": "2026-10-25"})
    assert r.status_code == 502 and "no sales" in r.json()["detail"]


def test_fetch_needs_login(monkeypatch):
    from app.services import swype_api
    monkeypatch.setattr(swype_api.settings, "swype_email", "")
    import datetime as _d
    try:
        swype_api.fetch_item_sales(_d.date(2026, 10, 19), _d.date(2026, 10, 25))
    except swype_api.SwypeError as e:
        assert "SWYPE_EMAIL" in str(e)
    else:
        raise AssertionError("expected SwypeError")


def test_api_fetch_one_day_at_a_time(client, monkeypatch):
    import datetime as _d
    from app.services import swype_api
    yesterday = _d.date.today() - _d.timedelta(days=1)
    closed = {yesterday - _d.timedelta(days=1)}

    def fake(df, dt):
        if df in closed:
            raise swype_api.NoSales("Swype has no sales")
        return report(f"From {df:%m/%d/%Y} to {dt:%m/%d/%Y}\nAll Price Type")
    monkeypatch.setattr(swype_api, "fetch_item_sales", fake)
    days = [yesterday - _d.timedelta(days=i) for i in (2, 1, 0)]
    for x in days:
        r = client.post("/api/imports/swype-item-sales/fetch", json={"date_from": str(x), "date_to": str(x)})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == ("no_sales" if x in closed else "ok")
    got = client.get("/api/imports/swype-item-sales/periods").json()
    assert [(p["period_from"], p["period_to"]) for p in got] == [(str(x), str(x)) for x in days if x not in closed]
    status = client.get("/api/imports/swype-item-sales/fetch").json()
    assert status["next_from"] == str(yesterday + _d.timedelta(days=1)) and status["through"] == str(yesterday)


def test_api_delete_range(client, monkeypatch):
    from app.services import swype_api

    def fake(df, dt):
        return report(f"From {df:%m/%d/%Y} to {dt:%m/%d/%Y}\nAll Price Type")
    monkeypatch.setattr(swype_api, "fetch_item_sales", fake)
    for a, b in [("2026-10-12", "2026-10-18"), ("2026-10-19", "2026-10-19"), ("2026-10-20", "2026-10-20")]:
        assert client.post("/api/imports/swype-item-sales/fetch", json={"date_from": a, "date_to": b}).status_code == 200
    r = client.delete("/api/imports/swype-item-sales/periods?date_from=2026-10-15&date_to=2026-10-19").json()
    assert r == {"deleted": 3, "periods": 1, "kept_partial": [{"period_from": "2026-10-12", "period_to": "2026-10-18"}]}
    left = [p["period_from"] for p in client.get("/api/imports/swype-item-sales/periods").json()]
    assert left == ["2026-10-12", "2026-10-20"]
    assert client.delete("/api/imports/swype-item-sales/periods?date_from=2026-10-20&date_to=2026-10-19").status_code == 400


def test_removed_file_can_be_imported_again(client):
    data = report()
    assert client.post("/api/imports/swype-item-sales", files={"file": ("s.xlsx", data)}).status_code == 200
    assert client.delete("/api/imports/swype-item-sales/periods?date_from=2026-10-19&date_to=2026-10-25").json()["deleted"] == 3
    assert client.get("/api/imports").json()[0]["status"] == "removed"
    assert client.post("/api/imports/swype-item-sales", files={"file": ("s.xlsx", data)}).status_code == 200


def test_remove_one_import_removes_its_values(client, monkeypatch):
    from app.services import swype_api

    def fake(df, dt):
        return report(f"From {df:%m/%d/%Y} to {dt:%m/%d/%Y}\nAll Price Type")
    monkeypatch.setattr(swype_api, "fetch_item_sales", fake)
    ids = [client.post("/api/imports/swype-item-sales/fetch", json={"date_from": x, "date_to": x}).json()["batch_id"]
           for x in ("2026-10-19", "2026-10-20")]
    assert client.delete(f"/api/imports/{ids[0]}").json() == {"deleted": 3}
    assert [p["period_from"] for p in client.get("/api/imports/swype-item-sales/periods").json()] == ["2026-10-20"]
    assert {b["id"]: b["status"] for b in client.get("/api/imports").json()}[ids[0]] == "removed"
    assert client.delete(f"/api/imports/{ids[0]}").status_code == 400  # already removed
    assert client.delete("/api/imports/nope").status_code == 400


def test_nightly_import(db, location, monkeypatch):
    import datetime as _d
    import json
    from app.models import DataIssue
    from app.services import settings_svc, swype_api, swype_auto
    yesterday = _d.date.today() - _d.timedelta(days=1)
    first = yesterday - _d.timedelta(days=3)
    asked = []

    def fake(df, dt):
        asked.append(df)
        if df == yesterday - _d.timedelta(days=1):
            raise swype_api.NoSales("closed")
        if df == yesterday and fail:
            raise swype_api.SwypeError("Swype is down")
        return report(f"From {df:%m/%d/%Y} to {dt:%m/%d/%Y}\nAll Price Type")
    monkeypatch.setattr(swype_api, "fetch_item_sales", fake)
    monkeypatch.setattr(swype_api, "configured", lambda: True)
    fail = True
    assert sis.apply(db, fake(first, first), "first.xlsx", location.id, source="swype_api")["status"] == "ok"
    asked.clear()

    now = _d.datetime.now().replace(hour=6)
    assert swype_auto.due(db, now) and not swype_auto.due(db, now.replace(hour=4))
    r = swype_auto.run(db)
    assert r["imported"] == [str(first + _d.timedelta(days=1))] and r["no_sales"] == [str(yesterday - _d.timedelta(days=1))]
    assert "Swype is down" in r["error"]
    assert db.query(DataIssue).filter_by(code="swype_auto_import_failed").count() == 1
    assert json.loads(settings_svc.get(db, "swype_auto_last"))["error"] == r["error"]
    assert not swype_auto.due(db, _d.datetime.fromisoformat(r["at"]).replace(hour=23))  # ran today already

    fail = False
    r = swype_auto.run(db)
    assert r["imported"] == [str(yesterday)] and r["error"] is None
    settings_svc.set_many(db, {"swype_auto_import": "false", "swype_auto_last": ""})
    assert not swype_auto.due(db, now)


def test_api_period_items(client, db):
    assert client.post("/api/imports/swype-item-sales", files={"file": ("r.xlsx", report())}).status_code == 200
    r = client.get("/api/imports/swype-item-sales/periods/2026-10-19/2026-10-25/items")
    assert r.status_code == 200
    assert [(x["code"], x["name"], x["category"], x["total_qty"], x["net_amount"]) for x in r.json()] == [
        ("00000330_01", "Smirnoff Vodka SNG", "Booze", 4.0, 32.0),
        ("00000330_02", "Smirnoff Vodka DBL", "Booze", 2.0, 24.0),
        ("00000400", "Lager 20oz", "Draft Beer", 10.0, 90.0)]
    assert client.get("/api/imports/swype-item-sales/periods/2026-10-19/2026-10-26/items").status_code == 404


def test_api_batch_items_follow_the_import_in_effect(client, db):
    first = client.post("/api/imports/swype-item-sales", files={"file": ("a.xlsx", report())}).json()
    got = client.get(f"/api/imports/{first['batch_id']}/items").json()
    assert [(x["code"], x["total_qty"]) for x in got] == [("00000330_01", 4.0), ("00000330_02", 2.0), ("00000400", 10.0)]
    second = client.post("/api/imports/swype-item-sales", files={"file": ("b.xlsx", report(draft_qty=12, grand_qty=18))}).json()
    assert second["status"] == "ok"
    assert client.get(f"/api/imports/{first['batch_id']}/items").json() == []
    assert client.get(f"/api/imports/{second['batch_id']}/items").json()[-1]["total_qty"] == 12.0
