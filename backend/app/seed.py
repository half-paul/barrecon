"""Idempotent seed: units, the single location, and the first admin login."""
from __future__ import annotations

from sqlalchemy.orm import Session

from .auth import hash_password
from .config import settings
from .models import Location, Unit, User
from .units import BASE_UNITS

DEFAULT_LOCATION_NAME = "Main bar"


def seed(db: Session) -> Location:
    for code, (dim, fac) in BASE_UNITS.items():
        if not db.get(Unit, code):
            db.add(Unit(code=code, dimension=dim, to_base=fac))
    loc = db.query(Location).order_by(Location.name).first()
    if not loc:
        loc = Location(name=DEFAULT_LOCATION_NAME)
        db.add(loc)
    if not db.query(User).first():
        # first start: one admin, password from ADMIN_PASSWORD; after that logins live in the database
        db.add(User(username="admin", display_name="Admin", password_hash=hash_password(settings.admin_password), is_admin=True))
    db.commit()
    return loc


def default_location(db: Session) -> Location:
    loc = db.query(Location).order_by(Location.name).first()
    return loc or seed(db)
