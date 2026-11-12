from datetime import datetime, timezone
from decimal import Decimal

from app import merge_movements
from app.models import Delivery, StockMovement
from app.services import inventory as inv


def test_merge_turns_sheet_movements_into_deliveries(db, location):
    a = inv.upsert_item(db, dict(sku="1", name="Gin", stock_unit="ml", pack_size=Decimal(750)))[0]
    b = inv.upsert_item(db, dict(sku="2", name="Wine", stock_unit="ml", pack_size=Decimal(750)))[0]
    when = datetime(2026, 10, 28, 6, 59, tzinfo=timezone.utc)
    # a bottle count typed into qty (1 ml), and a proper one
    inv.add_movement(db, location_id=location.id, item=a, kind="delivery", occurred_at=when, external_ref="D-1",
                     qty=Decimal(2), source="xlsx", note="BCLS 2754977 Gin - order line 1")
    inv.add_movement(db, location_id=location.id, item=b, kind="delivery", occurred_at=when, external_ref="D-2",
                     packs=Decimal(3), pack_cost=Decimal(12), source="xlsx", note="BCLS 2754977 Wine - order line 2")
    db.commit()

    merge_movements.main(apply=False)
    db.expire_all()
    assert db.query(Delivery).count() == 0

    merge_movements.main(apply=True)
    db.expire_all()
    [d] = db.query(Delivery).all()
    assert (d.receipt_no, d.store, d.status, d.source) == ("BCLS 2754977", "BC Liquor Store", "posted", "sheet")
    active = db.query(StockMovement).filter_by(status="active").all()
    assert sorted((m.item.name, m.qty, m.delivery_id == d.id) for m in active) == [
        ("Gin", 1500, True), ("Wine", 2250, True)]
    # nothing left to merge
    merge_movements.main(apply=True)
    db.expire_all()
    assert db.query(Delivery).count() == 1
