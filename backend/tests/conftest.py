import os
import sys
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["ADMIN_PASSWORD"] = "test-pass"
os.environ["SESSION_SECRET"] = "test-secret"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["ADMIN_MFA_REQUIRED"] = "false"  # test_login_security.py turns it on
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app import db as dbmod
from app.db import Base
from app.main import app
from app.seed import seed

FIXTURE = Path(__file__).parent / "fixture_legacy_workbook.xlsx"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    loc = seed(s)
    # tests write rows straight into the session: they belong to the first business, like the admin's requests
    s.info["tenant_id"], s.info["business_id"] = loc.tenant_id, loc.id
    # route the app at the same engine
    dbmod.engine = engine
    dbmod.SessionLocal = Session
    def override():
        s2 = Session()
        try:
            yield s2
        finally:
            s2.close()
    app.dependency_overrides[dbmod.get_db] = override
    yield s
    s.close()
    app.dependency_overrides.clear()


@pytest.fixture()
def client(db):
    with TestClient(app) as c:
        c.post("/api/auth/login", json={"password": "test-pass"})
        yield c


@pytest.fixture()
def platform(db):
    """Logged in to the platform console (the login seed() makes from ADMIN_PASSWORD)."""
    with TestClient(app) as c:
        assert c.post("/api/auth/login", json={"username": "platform", "password": "test-pass"}).status_code == 200
        yield c


@pytest.fixture()
def location(db):
    from app.seed import default_location
    return default_location(db)
