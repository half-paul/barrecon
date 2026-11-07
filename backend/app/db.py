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
    "swype_menu_items": {"custom_name": "VARCHAR(200)", "is_active": "BOOLEAN NOT NULL DEFAULT TRUE"},
    "recipes": {"steps": "TEXT"},
    "stock_movements": {"delivery_id": "VARCHAR(36) REFERENCES deliveries(id)"},
    "delivery_lines": {"receipt_sku": "VARCHAR(64)"},
    "receipt_aliases": {"sku": "VARCHAR(64)"},
    "users": {"pin_hash": "VARCHAR(255)"},
    "audit_events": {"seq": "INTEGER", "prev_hash": "VARCHAR(64)", "hash": "VARCHAR(64)"},
}


# SQLite can't add a UNIQUE column, so the constraint comes as an index
ADDED_INDEXES = ["CREATE UNIQUE INDEX IF NOT EXISTS uq_audit_events_seq ON audit_events (seq)",
                 "CREATE UNIQUE INDEX IF NOT EXISTS uq_receipt_alias_line ON receipt_aliases "
                 "(store_key, text_key, coalesce(sku, ''))"]
# constraints replaced since their table was first created; SQLite databases are only ever new ones (tests)
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


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
