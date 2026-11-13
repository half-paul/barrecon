from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, future=True)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_fk(dbapi_conn, _):  # pragma: no cover - trivial
        dbapi_conn.execute("PRAGMA foreign_keys=ON")


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


# columns added after their table was first created; create_all only makes missing tables
ADDED_COLUMNS = {
    "swype_menu_items": {"custom_name": "VARCHAR(200)", "is_active": "BOOLEAN NOT NULL DEFAULT TRUE",
                         "location_id": "VARCHAR(36) REFERENCES locations(id)",
                         "price": "NUMERIC(10, 2)", "price_period_to": "DATE", "catalogue_price": "NUMERIC(10, 2)"},
    "recipes": {"steps": "TEXT", "location_id": "VARCHAR(36) REFERENCES locations(id)"},
    "stock_movements": {"delivery_id": "VARCHAR(36) REFERENCES deliveries(id)"},
    "delivery_lines": {"receipt_sku": "VARCHAR(64)"},
    "receipt_aliases": {"sku": "VARCHAR(64)", "location_id": "VARCHAR(36) REFERENCES locations(id)"},
    "item_groups": {"sheet_in_cases": "BOOLEAN NOT NULL DEFAULT FALSE", "location_id": "VARCHAR(36) REFERENCES locations(id)"},
    "users": {"pin_hash": "VARCHAR(255)", "email": "VARCHAR(254)", "totp_secret": "VARCHAR(255)",
              "totp_enabled_at": "TIMESTAMP WITH TIME ZONE", "totp_last_step": "INTEGER", "recovery_codes": "JSON",
              "tenant_id": "VARCHAR(36) REFERENCES tenants(id)", "is_platform_admin": "BOOLEAN NOT NULL DEFAULT FALSE",
              "last_location_id": "VARCHAR(36)"},
    "audit_events": {"seq": "INTEGER", "prev_hash": "VARCHAR(64)", "hash": "VARCHAR(64)",
                     "tenant_id": "VARCHAR(36)", "location_id": "VARCHAR(36)"},
    "locations": {"tenant_id": "VARCHAR(36) REFERENCES tenants(id)", "kind": "VARCHAR(32)", "address": "VARCHAR(300)",
                  "city": "VARCHAR(120)", "phone": "VARCHAR(40)", "email": "VARCHAR(254)", "website": "VARCHAR(200)",
                  "is_active": "BOOLEAN NOT NULL DEFAULT TRUE", "logo_at": "TIMESTAMP WITH TIME ZONE",
                  "track_food": "BOOLEAN NOT NULL DEFAULT FALSE",
                  "swype_email": "VARCHAR(254)", "swype_password": "TEXT", "swype_company": "VARCHAR(64)",
                  "swype_store_id": "INTEGER", "created_at": "TIMESTAMP WITH TIME ZONE"},
    "data_issues": {"tenant_id": "VARCHAR(36)", "location_id": "VARCHAR(36)"},
    "app_settings": {"scope": "VARCHAR(36)"},
}


# SQLite can't add a UNIQUE column, so the constraint comes as an index
ADDED_INDEXES = ["CREATE UNIQUE INDEX IF NOT EXISTS uq_audit_events_seq ON audit_events (seq)",
                 "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email ON users (email)",
                 "CREATE INDEX IF NOT EXISTS ix_audit_tenant_at ON audit_events (tenant_id, at)",
                 "CREATE INDEX IF NOT EXISTS ix_users_tenant_id ON users (tenant_id)",
                 "CREATE INDEX IF NOT EXISTS ix_locations_tenant_id ON locations (tenant_id)"]
# constraints replaced since their table was first created; SQLite databases are rebuilt instead (upgrade_tenancy)
DROPPED_CONSTRAINTS = {"receipt_aliases": ["uq_receipt_alias"]}


def add_missing_columns(bind) -> None:
    insp = inspect(bind)
    with bind.begin() as conn:
        for table, cols in ADDED_COLUMNS.items():
            have = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols.items():
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
        if conn.dialect.name == "postgresql":
            for table, names in DROPPED_CONSTRAINTS.items():
                for name in names:
                    conn.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}"))
        for ddl in ADDED_INDEXES:
            conn.execute(text(ddl))


# ---- one bar -> tenants and businesses ----------------------------------------------------------------------
# Before tenants, the database held one bar: item groups, Swype items and settings were keyed by name alone, and
# SKUs and recipe codes were unique across the whole database. upgrade_tenancy moves such a database to keys that
# include the business (location_id), giving every existing row to the one business there was. It runs at every
# start and does nothing once the keys are in place. seed() then puts that business and its users under a tenant.

PLATFORM_SETTINGS = ("audit_retention_days", "audit_purge_last")
# table -> (new primary key, old single-column unique constraints that must go)
REKEYED = {
    "item_groups": (["location_id", "name"], []),
    "swype_menu_items": (["location_id", "code"], []),
    "app_settings": (["scope", "key"], []),
    "inventory_items": (None, ["sku"]),
    "recipes": (None, ["swype_code"]),
}
BACKFILL = ("inventory_items", "import_batches", "recipes", "receipt_aliases", "item_groups", "swype_menu_items")


def _needs_rekey(insp, table: str) -> bool:
    pk, uniques = REKEYED[table]
    if pk is not None and insp.get_pk_constraint(table)["constrained_columns"] != pk:
        return True
    have = [u["column_names"] for u in insp.get_unique_constraints(table)]
    have += [i["column_names"] for i in insp.get_indexes(table) if i.get("unique")]
    return any([c] in have for c in uniques)


def _rebuild_sqlite(conn, table: str) -> None:
    """SQLite can't change a key in place: make the table again from the model and copy the rows over."""
    from sqlalchemy.schema import CreateTable

    model = Base.metadata.tables[table]
    ddl = str(CreateTable(model).compile(conn)).replace(f"CREATE TABLE {table} ", f"CREATE TABLE {table}__new ", 1)
    cols = ", ".join(c.name for c in model.columns)
    conn.exec_driver_sql(ddl)
    conn.exec_driver_sql(f"INSERT INTO {table}__new ({cols}) SELECT {cols} FROM {table}")
    conn.exec_driver_sql(f"DROP TABLE {table}")
    conn.exec_driver_sql(f"ALTER TABLE {table}__new RENAME TO {table}")
    for ix in model.indexes:
        ix.create(conn, checkfirst=True)


def upgrade_tenancy(bind) -> bool:
    """Returns True if the database was changed."""
    insp = inspect(bind)
    todo = [t for t in REKEYED if _needs_rekey(insp, t)]
    with bind.begin() as conn:  # replaced by uq_receipt_alias_loc_line, which create_all or the loop below makes
        conn.execute(text("DROP INDEX IF EXISTS uq_receipt_alias_line"))
    with bind.connect() as probe:
        unowned = any(probe.execute(text(f"SELECT 1 FROM {t} WHERE location_id IS NULL LIMIT 1")).first() for t in BACKFILL)
        unowned = unowned or probe.execute(text("SELECT 1 FROM app_settings WHERE scope IS NULL LIMIT 1")).first() is not None
    if not todo and not unowned:
        return False

    sqlite = bind.dialect.name == "sqlite"
    with bind.connect() as conn:
        if sqlite:  # foreign keys must be off, and can only be switched outside a transaction
            conn = conn.execution_options(isolation_level="AUTOCOMMIT")
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            conn.exec_driver_sql("BEGIN")
        tx = None if sqlite else conn.begin()
        try:
            loc = conn.execute(text("SELECT id FROM locations ORDER BY name LIMIT 1")).scalar()
            if loc is None and unowned:  # rows but no bar to give them to: make the one seed() would
                import uuid
                loc = str(uuid.uuid4())
                conn.execute(text("INSERT INTO locations (id, name, is_active) VALUES (:id, 'Main bar', TRUE)"), {"id": loc})
            if loc is not None:
                for t in BACKFILL:
                    conn.execute(text(f"UPDATE {t} SET location_id = :loc WHERE location_id IS NULL"), {"loc": loc})
                conn.execute(text("UPDATE data_issues SET location_id = :loc WHERE location_id IS NULL AND tenant_id IS NULL"), {"loc": loc})
                keys = ", ".join(f"'{k}'" for k in PLATFORM_SETTINGS)
                conn.execute(text(f"UPDATE app_settings SET scope = 'platform' WHERE scope IS NULL AND key IN ({keys})"))
                conn.execute(text("UPDATE app_settings SET scope = :loc WHERE scope IS NULL"), {"loc": loc})
            for t in todo:
                if sqlite:
                    _rebuild_sqlite(conn, t)
                    continue
                pk, uniques = REKEYED[t]
                if pk is not None:
                    name = insp.get_pk_constraint(t)["name"]
                    for c in pk:
                        conn.execute(text(f"ALTER TABLE {t} ALTER COLUMN {c} SET NOT NULL"))
                    conn.execute(text(f'ALTER TABLE {t} DROP CONSTRAINT "{name}"'))
                    conn.execute(text(f"ALTER TABLE {t} ADD PRIMARY KEY ({', '.join(pk)})"))
                for u in insp.get_unique_constraints(t):
                    if u["column_names"] in ([c] for c in uniques):
                        conn.execute(text(f'ALTER TABLE {t} DROP CONSTRAINT "{u["name"]}"'))
                for i in insp.get_indexes(t):
                    if i.get("unique") and i["column_names"] in ([c] for c in uniques):
                        conn.execute(text(f'DROP INDEX IF EXISTS "{i["name"]}"'))
            for t in ("inventory_items", "recipes", "receipt_aliases"):
                for ix in Base.metadata.tables[t].indexes:
                    ix.create(conn, checkfirst=True)
            if sqlite:
                conn.exec_driver_sql("COMMIT")
            else:
                tx.commit()
        except Exception:
            if sqlite:
                conn.exec_driver_sql("ROLLBACK")
            else:
                tx.rollback()
            raise
        finally:
            if sqlite:
                conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    return True


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
