"""Idempotent seed: units, the first tenant with its first business and admin login, and the platform admin.

On a database from before tenants (one bar, its users) this is also the last step of the upgrade (db.upgrade_tenancy
re-keys the tables first): the bar becomes the first business of a first tenant, admins become that tenant's admins
and everyone else keeps their permissions as a membership of that business.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from .auth import hash_password
from .config import settings
from .models import BusinessMember, Location, Tenant, Unit, User
from .services import secretbox
from .units import BASE_UNITS

DEFAULT_LOCATION_NAME = "Main bar"


def _first_tenant(db: Session) -> Tenant:
    """Everything that has no tenant yet goes to the first one."""
    tenant = db.query(Tenant).order_by(Tenant.created_at).first()
    created = tenant is None
    if created:
        tenant = Tenant(name=settings.first_tenant_name)
        db.add(tenant)
        db.flush()
    loc = db.query(Location).order_by(Location.name).first()
    if created and not loc:
        loc = Location(name=DEFAULT_LOCATION_NAME, kind="bar")
        db.add(loc)
        db.flush()
    for b in db.query(Location).filter(Location.tenant_id.is_(None)):
        b.tenant_id = tenant.id
        if not b.swype_email and settings.swype_email:  # the Swype login used to come from .env
            b.swype_email, b.swype_password = settings.swype_email, secretbox.seal(settings.swype_password)
            b.swype_company, b.swype_store_id = settings.swype_company, settings.swype_store_id
    for u in db.query(User).filter(User.tenant_id.is_(None), User.is_platform_admin.is_(False)):
        u.tenant_id = tenant.id
        if loc and not u.is_admin and not db.get(BusinessMember, (u.id, loc.id)):
            db.add(BusinessMember(user_id=u.id, location_id=loc.id, permissions=dict(u.permissions or {})))
    db.flush()  # sessions don't autoflush: the check below must see the users just adopted
    if created and not db.query(User).filter(User.tenant_id == tenant.id).first():
        # first start: one admin, password from ADMIN_PASSWORD; after that logins live in the database
        db.add(User(username="admin", display_name="Admin", password_hash=hash_password(settings.admin_password),
                    is_admin=True, tenant_id=tenant.id))
    db.flush()
    if created and loc:
        _adopt_history(db, tenant.id, loc.id)
    return tenant


def _adopt_history(db: Session, tenant_id: str, location_id: str) -> None:
    """Audit entries and review items written before tenants belong to the first one. Audit rows are read-only on
    PostgreSQL (app.audit), so the guard is lifted for this one statement; tenant_id is not part of the hash chain."""
    conn = db.connection()
    pg = conn.dialect.name == "postgresql"
    if pg:
        conn.execute(text("ALTER TABLE audit_events DISABLE TRIGGER USER"))
    # system jobs and anything by a tenant user; platform admins' own events stay without a tenant
    conn.execute(text(
        "UPDATE audit_events SET tenant_id = :t, location_id = :l WHERE tenant_id IS NULL AND "
        "(user_id IS NULL OR user_id IN (SELECT id FROM users WHERE tenant_id = :t))"), {"t": tenant_id, "l": location_id})
    if pg:
        conn.execute(text("ALTER TABLE audit_events ENABLE TRIGGER USER"))
    conn.execute(text("UPDATE data_issues SET tenant_id = :t WHERE tenant_id IS NULL"), {"t": tenant_id})


def _platform_admin(db: Session) -> None:
    """The login for the platform console. Made once, from PLATFORM_ADMIN_USERNAME / PLATFORM_ADMIN_PASSWORD
    (the password falls back to ADMIN_PASSWORD)."""
    if db.query(User).filter(User.is_platform_admin.is_(True)).first():
        return
    name = settings.platform_admin_username.strip().lower()
    while db.query(User).filter(User.username == name).first():
        name = f"{name}-admin"
    db.add(User(username=name, display_name="Platform admin", is_platform_admin=True,
                password_hash=hash_password(settings.platform_admin_password or settings.admin_password)))


def seed(db: Session) -> Location:
    for code, (dim, fac) in BASE_UNITS.items():
        if not db.get(Unit, code):
            db.add(Unit(code=code, dimension=dim, to_base=fac))
    _first_tenant(db)
    _platform_admin(db)
    db.commit()
    return db.query(Location).order_by(Location.name).first()


def default_location(db: Session) -> Location:
    """The first business. Only for tests and one-off scripts: requests work in tenancy.business_id(db)."""
    loc = db.query(Location).order_by(Location.name).first()
    return loc or seed(db)
