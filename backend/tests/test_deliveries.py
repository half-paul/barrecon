from decimal import Decimal

import pytest

from app.services import receipts


def D(v):
    return Decimal(str(v))  # dict responses carry amounts as JSON numbers


RECEIPT = {
    "store": "BC Liquor Store #212", "store_address": "123 Main St", "date": "2026-10-28", "time": "15:42",
    "receipt_no": "0042-1187",
    "lines": [
        {"text": "SMIRNOFF VODKA 1.14L", "qty": 2, "line_total": 69.98},
        {"text": "KOKANEE 24PK CAN", "qty": 1, "line_total": 45.00},
        {"text": "CONTAINER DEPOSIT", "qty": 1, "line_total": 2.40},
    ],
    "subtotal": 117.38, "tax": 14.09, "total": 131.47,
}
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture()
def read_as(monkeypatch):
    """Stand in for Claude: the next receipt reads as `data`."""
    box = {"data": RECEIPT}
    monkeypatch.setattr(receipts, "enabled", lambda: True)
    monkeypatch.setattr(receipts, "read", lambda data, ctype, meter=None: box["data"])
    return box


def items(client):
    vodka = client.post("/api/items", json={"sku": "VOD", "name": "Smirnoff Vodka 1.14L", "stock_unit": "ml",
                                            "pack_name": "bottle", "pack_size": 1140, "pack_cost": 33}).json()
    beer = client.post("/api/items", json={"sku": "KOK", "name": "Kokanee can", "stock_unit": "each",
                                           "pack_name": "can", "pack_size": 1, "pack_cost": 1.50}).json()
    return vodka, beer


def upload(client):
    r = client.post("/api/deliveries/receipt", files={"file": ("r.png", PNG, "image/png")})
    assert r.status_code == 201, r.text
    return r.json()


def body(d, **lines):
    """The draft as the review screen would send it back, with lines changed by position."""
    out = {k: d[k] for k in ("store", "store_address", "occurred_at", "receipt_no", "subtotal", "tax", "total", "note")}
    out["lines"] = [{**{k: ln[k] for k in ("text", "receipt_qty", "line_total", "item_id", "packs", "skip")},
                     **lines.get(f"l{ln['position']}", {})} for ln in d["lines"]]
    return out


def test_receipt_to_posted_delivery_and_learned_matches(client, read_as):
    vodka, beer = items(client)
    d = upload(client)
    assert d["store"] == "BC Liquor Store #212" and d["status"] == "draft" and d["has_receipt"]
    assert d["occurred_at"].startswith("2026-10-28T22:42")  # 15:42 Vancouver (PDT) in UTC
    assert len(d["lines"]) == 3 and d["checks"]["totals_ok"]
    assert d["checks"]["unmatched"] == [0, 1]  # the container deposit is already marked not stock
    assert client.post(f"/api/deliveries/{d['id']}/post").status_code == 400  # lines not matched yet

    r = client.put(f"/api/deliveries/{d['id']}", json=body(
        d, l0={"item_id": vodka["id"], "packs": 2}, l1={"item_id": beer["id"], "packs": 24}, l2={"skip": True}))
    d = r.json()
    assert r.status_code == 200 and d["checks"]["unmatched"] == [] and d["checks"]["price_alerts"] == 1
    vod_price = next(p for p in d["checks"]["prices"] if p["item_id"] == vodka["id"])
    assert D(vod_price["pack_cost"]) == D("34.99") and not vod_price["alert"]  # +6 %, under the 10 % default
    beer_price = next(p for p in d["checks"]["prices"] if p["item_id"] == beer["id"])
    assert D(beer_price["pack_cost"]) == D("1.875") and beer_price["alert"]  # +25 %

    d = client.post(f"/api/deliveries/{d['id']}/post").json()
    assert d["status"] == "posted"
    mv = [m for m in client.get("/api/movements").json() if m["kind"] == "delivery"]
    assert {(m["sku"], D(m["qty"])) for m in mv} == {("VOD", 2280), ("KOK", 24)}
    assert all("BC Liquor Store #212" in m["note"] and m["occurred_at"].startswith("2026-10-28") for m in mv)

    # the next receipt from another branch of the same chain is pre-filled, 2 x 24-pack -> 48 cans
    read_as["data"] = {**RECEIPT, "store": "BC Liquor Store #105", "date": "2026-11-02",
                       "lines": [{"text": "KOKANEE 24PK CAN", "qty": 2, "line_total": 90},
                                 {"text": "Container deposit", "qty": 1, "line_total": 4.8},
                                 {"text": "BAILEYS 750ML", "qty": 1, "line_total": 32}],
                       "subtotal": None, "tax": None, "total": None}
    d2 = upload(client)
    l0, l1, l2 = d2["lines"]
    assert (l0["item_id"], D(l0["packs"]), l0["matched_by"]) == (beer["id"], 48, "memory")
    assert l1["skip"] and l1["matched_by"] == "rule"
    assert l2["item_id"] is None and d2["checks"]["unmatched"] == [2]


def test_reopen_and_void_retract_movements(client, read_as):
    vodka, beer = items(client)
    d = upload(client)
    d = client.put(f"/api/deliveries/{d['id']}", json=body(
        d, l0={"item_id": vodka["id"], "packs": 2}, l1={"item_id": vodka["id"], "packs": 1}, l2={"skip": True})).json()
    d = client.post(f"/api/deliveries/{d['id']}/post").json()
    active = lambda: [m for m in client.get("/api/movements").json() if m["kind"] == "delivery"]
    assert len(active()) == 1 and D(active()[0]["packs"]) == 3  # two lines of one item add up
    assert D(active()[0]["pack_cost"]) == D("38.3267")  # (69.98 + 45) / 3

    d = client.post(f"/api/deliveries/{d['id']}/reopen").json()
    assert d["status"] == "draft" and active() == []
    d = client.put(f"/api/deliveries/{d['id']}", json=body(d, l1={"item_id": beer["id"], "packs": 24})).json()
    d = client.post(f"/api/deliveries/{d['id']}/post").json()
    assert {m["sku"] for m in active()} == {"VOD", "KOK"}
    assert client.delete(f"/api/deliveries/{d['id']}").status_code == 400
    d = client.post(f"/api/deliveries/{d['id']}/void").json()
    assert d["status"] == "void" and active() == []
    assert client.get("/api/deliveries").json() == []


def test_totals_check_and_duplicate(client, read_as):
    read_as["data"] = {**RECEIPT, "total": 140.00}
    d = upload(client)
    assert not d["checks"]["totals_ok"] and D(d["checks"]["total_diff"]) == D("-8.53")
    d2 = upload(client)
    assert d2["checks"]["possible_duplicate"]["id"] == d["id"]


def test_unreadable_receipt_still_makes_a_draft(client, monkeypatch):
    def fail(data, ctype, meter=None):
        raise receipts.ReceiptReadError("couldn't reach Claude")
    monkeypatch.setattr(receipts, "read", fail)
    d = upload(client)
    assert d["read_error"] == "couldn't reach Claude" and d["lines"] == [] and d["status"] == "draft"
    assert client.get(f"/api/deliveries/{d['id']}/receipt").content == PNG
    r = client.post("/api/deliveries/receipt", files={"file": ("r.txt", b"hi", "text/plain")})
    assert r.status_code == 400
    assert client.delete(f"/api/deliveries/{d['id']}").status_code == 204


SKU_RECEIPT = {**RECEIPT, "lines": [
    {"text": "SMIRNOFF VODKA 1.14L", "sku": "000067", "qty": 2, "line_total": 69.98},
    {"text": "KOKANEE 24PK CAN", "sku": "555", "qty": 1, "line_total": 45.00},
]}


def sku_items(client):
    vodka = client.post("/api/items", json={"sku": "67", "name": "Smirnoff Red", "stock_unit": "ml", "pack_name": "bottle",
                                            "pack_size": 1140}).json()
    beer = client.post("/api/items", json={"sku": "GEN-KOK", "name": "Kokanee can", "stock_unit": "each", "pack_size": 1}).json()
    return vodka, beer


def test_printed_sku_matches_the_stock_item(client, read_as):
    vodka, _ = sku_items(client)
    read_as["data"] = SKU_RECEIPT
    d = upload(client)
    line = d["lines"][0]
    assert line["receipt_sku"] == "000067" and line["item_id"] == vodka["id"]
    assert line["matched_by"] == "sku" and D(line["packs"]) == 2
    assert d["lines"][1]["item_id"] is None


@pytest.mark.parametrize("store", ["BCL", "Costco Wholesale", None])
def test_printed_sku_matches_at_any_store(client, read_as, store):
    vodka, _ = sku_items(client)
    read_as["data"] = {**SKU_RECEIPT, "store": store}
    line = upload(client)["lines"][0]
    assert line["item_id"] == vodka["id"] and line["matched_by"] == "sku" and D(line["packs"]) == 2


def test_hand_fix_of_a_sku_match_wins_next_time(client, read_as):
    vodka, beer = sku_items(client)
    read_as["data"] = {**SKU_RECEIPT, "store": "Costco Wholesale"}
    d = upload(client)
    assert d["lines"][0]["item_id"] == vodka["id"]
    d = client.put(f"/api/deliveries/{d['id']}", json=body(d, l0={"item_id": beer["id"], "packs": 2}, l1={"skip": True})).json()
    assert client.post(f"/api/deliveries/{d['id']}/post").status_code == 200

    read_as["data"] = {**SKU_RECEIPT, "store": "Costco Wholesale", "date": "2026-11-02"}
    line = upload(client)["lines"][0]
    assert line["item_id"] == beer["id"] and line["matched_by"] == "memory"


def test_hand_match_on_a_sku_is_remembered_even_if_the_words_change(client, read_as):
    vodka, beer = sku_items(client)
    read_as["data"] = SKU_RECEIPT
    d = upload(client)
    d = client.put(f"/api/deliveries/{d['id']}", json=body(d, l1={"item_id": beer["id"], "packs": 24})).json()
    assert d["lines"][1]["receipt_sku"] == "555"  # survives a save that doesn't send it back
    assert client.post(f"/api/deliveries/{d['id']}/post").status_code == 200

    read_as["data"] = {**SKU_RECEIPT, "date": "2026-11-02", "lines": [
        {"text": "KOKANEE CAN 24 PACK", "sku": "555", "qty": 2, "line_total": 90.00}]}
    line = upload(client)["lines"][0]
    assert line["item_id"] == beer["id"] and line["matched_by"] == "memory" and D(line["packs"]) == 48


def test_hand_fix_beats_the_stock_sku(client, read_as):
    vodka, beer = sku_items(client)
    read_as["data"] = SKU_RECEIPT
    d = upload(client)
    d = client.put(f"/api/deliveries/{d['id']}", json=body(
        d, l0={"item_id": beer["id"], "packs": 2}, l1={"item_id": beer["id"], "packs": 24})).json()
    assert client.post(f"/api/deliveries/{d['id']}/post").status_code == 200
    read_as["data"] = {**SKU_RECEIPT, "date": "2026-11-02"}
    assert upload(client)["lines"][0]["item_id"] == beer["id"]


def test_same_words_with_another_sku_are_not_the_learned_item(client, read_as):
    _, beer = sku_items(client)
    read_as["data"] = SKU_RECEIPT
    d = upload(client)
    d = client.put(f"/api/deliveries/{d['id']}", json=body(d, l1={"item_id": beer["id"], "packs": 24})).json()
    assert client.post(f"/api/deliveries/{d['id']}/post").status_code == 200
    read_as["data"] = {**SKU_RECEIPT, "date": "2026-11-02", "lines": [
        {"text": "KOKANEE 24PK CAN", "sku": "777", "qty": 1, "line_total": 45.00}]}
    assert upload(client)["lines"][0]["item_id"] is None


@pytest.mark.parametrize("text", ["CONTAINER DEPOSIT", "Container Deposit 0.10"])
def test_container_deposit_is_never_stock_but_counts_in_the_totals(client, read_as, text):
    sku_items(client)
    read_as["data"] = {**RECEIPT, "lines": [*RECEIPT["lines"][:2], {"text": text, "sku": "67", "qty": 1, "line_total": 2.40}]}
    d = upload(client)
    line = d["lines"][2]
    assert line["skip"] and line["item_id"] is None and line["matched_by"] == "rule"
    assert 2 not in d["checks"]["unmatched"] and d["checks"]["totals_ok"]


def test_line_marked_not_stock_by_hand_is_remembered(client, read_as):
    vodka, _ = items(client)
    lines = [RECEIPT["lines"][0], {"text": "PAPER BAG", "qty": 1, "line_total": 0.25}]
    read_as["data"] = {**RECEIPT, "lines": lines}
    d = upload(client)
    d = client.put(f"/api/deliveries/{d['id']}", json=body(d, l0={"item_id": vodka["id"], "packs": 2}, l1={"skip": True})).json()
    assert client.post(f"/api/deliveries/{d['id']}/post").status_code == 200

    read_as["data"] = {**RECEIPT, "date": "2026-11-02", "lines": lines}
    line = upload(client)["lines"][1]
    assert line["skip"] and line["matched_by"] == "memory"


def test_same_words_with_different_skus_post_and_are_remembered_apart(client, read_as):
    vodka, beer = sku_items(client)
    other = client.post("/api/items", json={"sku": "GEN-OTHER", "name": "Other", "stock_unit": "each", "pack_size": 1}).json()
    words = "SIPS LANGUEDOC 1X750ML"
    lines = [{"text": words, "sku": "32652", "qty": 1, "line_total": 10.00},
             {"text": words, "sku": "32637", "qty": 1, "line_total": 10.00},
             {"text": words, "sku": "32637", "qty": 2, "line_total": 20.00},  # the same product on two lines
             {"text": "JAMESON", "qty": 1, "line_total": 30.00},
             {"text": "JAMESON", "qty": 1, "line_total": 30.00}]  # and with no SKU at all
    read_as["data"] = {**RECEIPT, "lines": lines}
    d = upload(client)
    d = client.put(f"/api/deliveries/{d['id']}", json=body(
        d, l0={"item_id": vodka["id"], "packs": 1}, l1={"item_id": beer["id"], "packs": 1}, l2={"item_id": beer["id"], "packs": 2},
        l3={"item_id": other["id"], "packs": 1}, l4={"item_id": other["id"], "packs": 1})).json()
    assert client.post(f"/api/deliveries/{d['id']}/post").status_code == 200

    read_as["data"] = {**RECEIPT, "date": "2026-11-02", "lines": lines}
    got = [ln["item_id"] for ln in upload(client)["lines"]]
    assert got == [vodka["id"], beer["id"], beer["id"], other["id"], other["id"]]

    # those words alone can't say which product it was
    read_as["data"] = {**RECEIPT, "date": "2026-11-03", "lines": [{"text": words, "qty": 1, "line_total": 10.00}]}
    assert upload(client)["lines"][0]["item_id"] is None
