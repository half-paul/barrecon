from app.models import CountSession, StockMovement
from app.services import legacy_import
from app.services import inventory as inv
from tests.conftest import FIXTURE


def test_preview_reads_real_workbook():
    pv = legacy_import.preview(FIXTURE.read_bytes())
    assert pv.tabs == ["June 2026", "July 2026", "August 2026", "Sept 2026", "Oct 2026"]
    assert 95 <= len(pv.items) <= 105
    cats = {i["category"] for i in pv.items}
    assert cats == {"Spirits", "Scotch & whisky", "Wine", "Boxed wine", "Bottled beer", "Draft beer"}
    fc = next(i for i in pv.items if i["name"] == "Forty Creek")
    assert fc["sku"] == "550715" and fc["par_level"] == 4
    assert not any(i["name"] in ("Lemon Meringue", "Red Sangria", "El Humo") for i in pv.items)
    assert len(pv.count_sessions) == 21
    assert {r["code"] for r in pv.review} <= {"no_sku", "order_unparsed", "unknown_section"}


def test_order_parser():
    p = legacy_import.parse_order
    assert p("2 case") == (2, "case", None)
    assert p("4 flats") == (4, "case", None)
    assert p("5 bottkes")[0] == 5
    assert p(3.0) == (3, "pack", None)
    assert p("2 case* never recieved")[2] == "marked as not received"
    assert p("3x30l - solaris summer ale")[2] == "free text"
    assert p(None) == (None, None, None)


def test_apply_is_idempotent(db, location):
    r1 = legacy_import.apply(db, FIXTURE.read_bytes(), "legacy.xlsx", location.id)
    assert r1["items_created"] >= 95 and r1["count_sessions_created"] == 21
    n_mov = db.query(StockMovement).count()
    r2 = legacy_import.apply(db, FIXTURE.read_bytes(), "legacy.xlsx", location.id)
    assert r2["items_created"] == 0 and r2["count_sessions_created"] == 0
    assert db.query(StockMovement).count() == n_mov
    assert db.query(CountSession).filter_by(status="posted").count() == 21
    guin = inv.item_by_sku(db, "GEN-GUINESS")
    assert guin.category == "Draft beer" and guin.pack_size == 50000
