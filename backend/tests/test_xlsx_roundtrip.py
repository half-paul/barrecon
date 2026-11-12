import io
from decimal import Decimal

from openpyxl import Workbook, load_workbook

from app.services import inventory as inv
from app.services import xlsx_io


def _wb(sheet, header, rows):
    wb = Workbook(); ws = wb.active; ws.title = sheet
    ws.append(header)
    for r in rows:
        ws.append(r)
    bio = io.BytesIO(); wb.save(bio); return bio.getvalue()


def test_items_template_then_import(db):
    data = xlsx_io.items_template(db)
    wb = load_workbook(io.BytesIO(data))
    assert wb.sheetnames == ["Items", "Legend"]
    rows = [["67", "Smirnoff Red", "Spirits", "ml", "bottle", 750, 12, 30, None, None, 4, True],
            ["GEN-GUINESS", "Guinness", "Draft beer", "ml", "keg", 50000, 1, None, None, None, None, True]]
    res = xlsx_io.import_items(db, _wb("Items", xlsx_io.ITEM_COLS, rows), "items.xlsx")
    assert res.status == "ok" and res.inserted == 2
    # same file again -> rejected by hash, nothing changes
    res2 = xlsx_io.import_items(db, _wb("Items", xlsx_io.ITEM_COLS, rows), "items.xlsx")
    assert res2.status == "rejected"
    # bad unit -> whole file rejected, nothing written
    bad = rows + [["X", "Bad", "Spirits", "pint", "bottle", 750, 12, None, None, None, None, True]]
    res3 = xlsx_io.import_items(db, _wb("Items", xlsx_io.ITEM_COLS, bad), "items2.xlsx")
    assert res3.status == "rejected" and inv.item_by_sku(db, "X") is None


def test_counts_import_creates_posted_sessions_and_corrections(db, location):
    inv.upsert_item(db, dict(sku="67", name="Smirnoff Red", stock_unit="ml", pack_size=Decimal(750))); db.commit()
    rows = [["2026-10-25", "67", "Smirnoff Red", "bottle", 3, 0.4, None]]
    res = xlsx_io.import_counts(db, _wb("Counts", xlsx_io.COUNT_COLS, rows), "c1.xlsx", location.id)
    assert res.status == "ok" and len(res.sessions_created) == 1
    rows2 = [["2026-10-25", "67", "Smirnoff Red", "bottle", 3, 0.6, "recount"]]
    res2 = xlsx_io.import_counts(db, _wb("Counts", xlsx_io.COUNT_COLS, rows2), "c2.xlsx", location.id)
    assert res2.status == "ok" and any(r.status == "superseded" for r in res2.rows)
    from app.models import CountSession
    assert db.query(CountSession).filter_by(status="superseded").count() == 1
    # unknown sku -> rejected
    res3 = xlsx_io.import_counts(db, _wb("Counts", xlsx_io.COUNT_COLS, [["2026-10-25", "nope", "", "", 1, 0, None]]), "c3.xlsx", location.id)
    assert res3.status == "rejected"
    # future date -> rejected
    res4 = xlsx_io.import_counts(db, _wb("Counts", xlsx_io.COUNT_COLS, [["2099-01-01", "67", "", "", 1, 0, None]]), "c4.xlsx", location.id)
    assert res4.status == "rejected"


def test_movements_import_is_idempotent(db, location):
    inv.upsert_item(db, dict(sku="67", name="Smirnoff Red", stock_unit="ml", pack_size=Decimal(750), case_size=Decimal(12)))
    inv.upsert_item(db, dict(sku="68", name="Gin", stock_unit="ml", pack_size=Decimal(750))); db.commit()
    rows = [["D-1", "2026-10-20", "67", "delivery", 12, None, 31, "INV-9", None],
            [None, "2026-10-20", "68", None, None, 1500, None, "INV-9", None],
            ["D-2", "2026-10-21", "67", "breakage_expired", None, 750, None, None, "dropped"]]
    res = xlsx_io.import_movements(db, _wb("Movements", xlsx_io.MOVE_COLS, rows), "m1.xlsx", location.id)
    assert res.status == "ok" and res.inserted == 3, res.rows
    from app.models import Delivery, StockMovement
    [d] = db.query(Delivery).all()
    assert (d.receipt_no, d.status, d.source, len(d.lines)) == ("INV-9", "posted", "sheet", 2)
    # re-upload with one changed cell: the delivery is updated, D-2 duplicate
    rows[0][4] = 11
    res2 = xlsx_io.import_movements(db, _wb("Movements", xlsx_io.MOVE_COLS, rows), "m2.xlsx", location.id)
    assert (res2.updated, res2.duplicate) == (2, 1)
    assert db.query(Delivery).count() == 1
    active = db.query(StockMovement).filter_by(status="active").all()
    assert sorted((m.item.sku, m.kind, m.qty) for m in active) == [
        ("67", "breakage_expired", 750), ("67", "delivery", 11 * 750), ("68", "delivery", 1500)]


def test_movements_import_rejects_bottles_in_qty(db, location):
    inv.upsert_item(db, dict(sku="67", name="Smirnoff Red", stock_unit="ml", pack_size=Decimal(750))); db.commit()
    rows = [[None, "2026-10-20", "67", "delivery", None, 2, None, "INV-9", None]]
    res = xlsx_io.import_movements(db, _wb("Movements", xlsx_io.MOVE_COLS, rows), "m1.xlsx", location.id)
    assert res.status == "rejected" and "packs" in res.rows[0].message


def test_movements_sheet_packs_are_cases_for_flagged_group(db, location):
    from app.models import Delivery, ItemGroup
    db.add(ItemGroup(name="Wine", stock_unit="ml", pack_name="bottle", pack_size=Decimal(750), case_size=Decimal(12),
                     sheet_in_cases=True))
    inv.upsert_item(db, dict(sku="945626", name="Fiol", category="Wine", stock_unit="ml", pack_size=Decimal(750),
                             case_size=Decimal(12)))
    inv.upsert_item(db, dict(sku="67", name="Smirnoff Red", category="Spirits", stock_unit="ml", pack_size=Decimal(750),
                             case_size=Decimal(12))); db.commit()
    rows = [[None, "2026-11-03", "945626", None, 2, None, 13.28, "BCLS 1", None],
            [None, "2026-11-03", "67", None, 6, None, 19.73, "BCLS 1", None]]
    res = xlsx_io.import_movements(db, _wb("Movements", xlsx_io.MOVE_COLS, rows), "m.xlsx", location.id)
    assert res.status == "ok", res.rows
    d = db.query(Delivery).filter_by(receipt_no="BCLS 1").one()
    packs = {ln.text: ln.packs for ln in d.lines}
    assert packs == {"Fiol": Decimal(24), "Smirnoff Red": Decimal(6)}
    fiol = next(ln for ln in d.lines if ln.text == "Fiol")
    assert fiol.line_total == Decimal("318.72")  # pack_cost stays per bottle
