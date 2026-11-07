from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.models import CountSession
from app.services import inventory as inv

T0 = datetime(2026, 10, 25, 23, 59, tzinfo=timezone.utc)


def _vodka(db):
    item, _ = inv.upsert_item(db, dict(sku="67", name="Smirnoff Red", category="Spirits", stock_unit="ml",
                                       pack_name="bottle", pack_size=Decimal(750), case_size=Decimal(12), pack_cost=Decimal("30")))
    db.commit()
    return item


def test_upsert_records_cost_history(db):
    item = _vodka(db)
    assert item.unit_cost == Decimal("30") / 750
    inv.upsert_item(db, dict(sku="67", pack_cost=Decimal("32")))
    inv.upsert_item(db, dict(sku="67", pack_cost=Decimal("32")))  # unchanged -> no new row
    db.commit()
    assert [c.pack_cost for c in item.costs] == [Decimal("30"), Decimal("32")]


def test_count_posting_supersedes_not_overwrites(db, location):
    item = _vodka(db)
    s1 = CountSession(location_id=location.id, counted_at=T0)
    db.add(s1); db.flush()
    inv.set_count_line(db, s1, item, Decimal(3), Decimal("0.4"))
    assert db.get(type(s1.lines[0]), (s1.id, item.id)).qty == Decimal("3.4") * 750
    inv.post_count(db, s1)
    with pytest.raises(inv.InventoryError):
        inv.set_count_line(db, s1, item, Decimal(1), Decimal(0))  # posted is immutable
    s2 = CountSession(location_id=location.id, counted_at=T0)
    db.add(s2); db.flush()
    inv.set_count_line(db, s2, item, Decimal(3), Decimal("0.6"))
    inv.post_count(db, s2)
    db.commit()
    assert s1.status == "superseded" and s1.superseded_by == s2.id
    assert inv.active_session_at(db, location.id, T0).id == s2.id


def test_movement_idempotent_and_superseding(db, location):
    item = _vodka(db)
    kw = dict(location_id=location.id, item=item, kind="delivery", occurred_at=T0, external_ref="INV-1")
    m1, o1 = inv.add_movement(db, packs=Decimal(12), pack_cost=Decimal("31"), **kw)
    m2, o2 = inv.add_movement(db, packs=Decimal(12), pack_cost=Decimal("31"), **kw)
    assert (o1, o2) == ("inserted", "duplicate") and m1.id == m2.id
    assert m1.qty == 12 * 750
    m3, o3 = inv.add_movement(db, packs=Decimal(11), pack_cost=Decimal("31"), **kw)
    db.commit()
    assert o3 == "superseded" and m1.status == "superseded" and m3.status == "active"
    assert item.pack_cost == Decimal("31")  # delivery cost updated the item


def test_movement_validation(db, location):
    item = _vodka(db)
    with pytest.raises(inv.InventoryError):
        inv.add_movement(db, location_id=location.id, item=item, kind="spill", occurred_at=T0, external_ref="x", packs=Decimal(1))
    with pytest.raises(inv.InventoryError):
        inv.add_movement(db, location_id=location.id, item=item, kind="delivery", occurred_at=T0, external_ref="", packs=Decimal(1))
    with pytest.raises(inv.InventoryError):
        inv.add_movement(db, location_id=location.id, item=item, kind="delivery", occurred_at=T0, external_ref="y", packs=Decimal(0))
