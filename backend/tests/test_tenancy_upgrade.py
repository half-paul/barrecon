"""A database from before tenants (one bar: fixture_pre_tenancy.sql) started with this version: its rows end up in
one business of a first tenant, with the keys that let other businesses use the same names and numbers."""
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker

from app import tenancy
from app.auth import hash_password
from app.db import Base, add_missing_columns, upgrade_tenancy
from app.models import (AppSetting, AuditEvent, BusinessMember, DataIssue, InventoryItem, ItemGroup, Location, Recipe, ReceiptAlias,
                        SwypeMenuItem, Tenant, User)
from app.seed import seed
from app.services import settings_svc

OLD = Path(__file__).parent / "fixture_pre_tenancy.sql"
NOW = "2026-10-01 12:00:00"


@pytest.fixture()
def old_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")

    @event.listens_for(engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    with engine.begin() as c:
        for stmt in OLD.read_text().split(";\n"):
            if stmt.strip() and not stmt.strip().startswith("--") or "CREATE" in stmt:
                c.exec_driver_sql(stmt)
        pw = hash_password("old-pass-123")
        c.execute(text("INSERT INTO locations (id, name) VALUES ('loc1', 'Main bar')"))
        c.execute(text("INSERT INTO units (code, dimension, to_base) VALUES ('ml', 'volume', 1)"))
        c.execute(text(f"INSERT INTO users (id, username, display_name, password_hash, is_admin, permissions, is_active, session_version, created_at, updated_at) "
                       f"VALUES ('u1', 'admin', 'Admin', :pw, 1, '{{}}', 1, 1, '{NOW}', '{NOW}'), "
                       f"('u2', 'sam', 'Sam', :pw, 0, :perms, 1, 1, '{NOW}', '{NOW}')"), {"pw": pw, "perms": json.dumps({"counts": "edit"})})
        c.execute(text(f"INSERT INTO inventory_items (id, location_id, sku, name, category, stock_unit, pack_size, case_size, is_active, created_at, updated_at) "
                       f"VALUES ('i1', NULL, 'V1', 'Vodka', 'Spirits', 'ml', 750, 1, 1, '{NOW}', '{NOW}')"))
        c.execute(text(f"INSERT INTO item_groups (name, stock_unit, pack_size, case_size, created_at) VALUES ('Spirits', 'ml', 750, 12, '{NOW}')"))
        c.execute(text(f"INSERT INTO swype_menu_items (code, name, source, created_at, updated_at) VALUES ('00000330', 'Vodka', 'sales', '{NOW}', '{NOW}')"))
        c.execute(text(f"INSERT INTO recipes (id, swype_code, name, ignored, created_at, updated_at) VALUES ('r1', '00000330', 'Vodka', 0, '{NOW}', '{NOW}')"))
        c.execute(text(f"INSERT INTO recipe_versions (id, recipe_id, effective_from, created_at) VALUES ('rv1', 'r1', '2026-01-01', '{NOW}')"))
        c.execute(text("INSERT INTO recipe_lines (id, version_id, item_id, qty, unit, position) VALUES ('rl1', 'rv1', 'i1', 1, 'ml', 0)"))
        c.execute(text("INSERT INTO app_settings (key, value) VALUES ('variance_threshold_pct', '42'), ('audit_retention_days', '90')"))
        c.execute(text(f"INSERT INTO receipt_aliases (id, store_key, text_key, item_id, packs_per_unit, updated_at) VALUES ('a1', 'bcl', 'vodka', 'i1', 1, '{NOW}')"))
        c.execute(text(f"INSERT INTO audit_events (id, at, user_id, username, action) VALUES ('e1', '{NOW}', 'u2', 'sam', 'login')"))
        c.execute(text(f"INSERT INTO data_issues (id, code, resolved, created_at) VALUES ('d1', 'login_locked', 0, '{NOW}')"))
    return engine


def _start(engine):
    """What app.main does at startup."""
    Base.metadata.create_all(engine)
    add_missing_columns(engine)
    changed = upgrade_tenancy(engine)
    with sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)() as s:  # as app.db.SessionLocal
        seed(s)
    return changed


def test_single_bar_database_becomes_the_first_tenant(old_engine):
    assert inspect(old_engine).get_pk_constraint("item_groups")["constrained_columns"] == ["name"]
    assert _start(old_engine) is True

    insp = inspect(old_engine)
    assert insp.get_pk_constraint("item_groups")["constrained_columns"] == ["location_id", "name"]
    assert insp.get_pk_constraint("swype_menu_items")["constrained_columns"] == ["location_id", "code"]
    assert insp.get_pk_constraint("app_settings")["constrained_columns"] == ["scope", "key"]
    assert [u["column_names"] for u in insp.get_unique_constraints("inventory_items")] == []
    assert ["location_id", "sku"] in [i["column_names"] for i in insp.get_indexes("inventory_items") if i["unique"]]
    with old_engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []  # the rebuilt tables still line up

    s = sessionmaker(bind=old_engine, expire_on_commit=False)()
    t = s.query(Tenant).one()
    loc = s.query(Location).one()
    assert (loc.id, loc.name, loc.tenant_id, loc.is_active) == ("loc1", "Main bar", t.id, True)
    # every row belongs to the one business there was
    for model in (InventoryItem, ItemGroup, SwypeMenuItem, Recipe, ReceiptAlias):
        assert [r.location_id for r in s.query(model)] == ["loc1"], model
    assert s.get(Recipe, "r1").versions[0].lines[0].item_id == "i1"
    # logins: the admin runs the tenant, everyone else keeps their levels as a membership of the business
    admin, sam = s.get(User, "u1"), s.get(User, "u2")
    assert admin.tenant_id == sam.tenant_id == t.id and admin.is_admin and not admin.is_platform_admin
    assert s.get(BusinessMember, ("u2", "loc1")).permissions == {"counts": "edit"}
    assert s.get(BusinessMember, ("u1", "loc1")) is None
    assert s.query(User).filter(User.is_platform_admin.is_(True)).one().username == "platform"
    # settings: the business's own, except the platform-wide ones
    assert s.get(AppSetting, ("loc1", "variance_threshold_pct")).value == "42"
    assert s.get(AppSetting, ("platform", "audit_retention_days")).value == "90"
    with tenancy.scope(s, t.id, "loc1"):
        assert settings_svc.get(s, "variance_threshold_pct") == "42" and settings_svc.get(s, "audit_retention_days") == "90"
        assert [i.code for i in s.query(DataIssue)] == ["login_locked"]
    ev = s.get(AuditEvent, "e1")
    assert (ev.tenant_id, ev.location_id) == (t.id, "loc1")
    s.close()

    # the same SKU, group and Swype item can now exist in a second business
    s = sessionmaker(bind=old_engine, expire_on_commit=False)()
    s.add(Location(id="loc2", tenant_id=t.id, name="Second"))
    s.flush()
    s.add_all([InventoryItem(location_id="loc2", sku="V1", name="Vodka", stock_unit="ml"),
               ItemGroup(location_id="loc2", name="Spirits", stock_unit="ml"),
               SwypeMenuItem(location_id="loc2", code="00000330", name="Vodka", source="sales"),
               Recipe(location_id="loc2", swype_code="00000330", name="Vodka")])
    s.commit()
    s.close()


def test_starting_again_changes_nothing(old_engine):
    _start(old_engine)
    assert _start(old_engine) is False
    with sessionmaker(bind=old_engine)() as s:
        assert s.query(Tenant).count() == 1 and s.query(Location).count() == 1
        assert s.query(User).count() == 3 and s.query(BusinessMember).count() == 1


def test_new_database_needs_no_upgrade():
    engine = create_engine("sqlite://")
    assert _start(engine) is False
