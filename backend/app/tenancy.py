"""Keeps every request inside its tenant and its business.

A request's database session carries who it works for in Session.info: "tenant_id" and "business_id", set by
app.auth.current_user (background jobs set them per business with `scope()`). While they are set:

- every ORM SELECT, UPDATE and DELETE of a business-owned table only sees rows of that business, and of a
  tenant-owned table (users, businesses) only rows of that tenant, whatever the query forgot to filter;
- every new business-owned row gets the business's id, and one aimed at another business is refused
  (info["also_business"] names one more business rows may be written to: copying a catalogue into a new business).

A session with neither key (startup, tests, the platform console) is not filtered. A session with a tenant but no
business (a tenant with no businesses yet) sees no business rows at all.
Pass execution_options(unscoped=True) for the few lookups that must cross tenants (is this username taken?).
"""
from __future__ import annotations

from contextlib import contextmanager

from fastapi import HTTPException
from sqlalchemy import event, or_
from sqlalchemy.orm import Session, with_loader_criteria

from .models import (
    BusinessMember,
    CountSession,
    DataIssue,
    Delivery,
    ImportBatch,
    InventoryItem,
    ItemGroup,
    Location,
    Recipe,
    ReceiptAlias,
    StockMovement,
    SwypeItemSales,
    SwypeMenuItem,
    User,
)

# tables whose rows belong to one business through location_id
BUSINESS_OWNED = (InventoryItem, ItemGroup, CountSession, StockMovement, Delivery, ReceiptAlias, ImportBatch,
                  SwypeItemSales, SwypeMenuItem, Recipe)
TENANT_OWNED = (User, Location)
_BUSINESS_TABLES = {m.__table__.name: m.__table__ for m in BUSINESS_OWNED}
_TENANT_TABLES = {m.__table__.name: m.__table__ for m in TENANT_OWNED}
NOWHERE = "-"  # matches no row: a tenant session with no business picked


def set_scope(db: Session, tenant_id: str | None, business_id: str | None) -> None:
    db.info["tenant_id"], db.info["business_id"] = tenant_id, business_id


def clear_scope(db: Session) -> None:
    db.info.pop("tenant_id", None)
    db.info.pop("business_id", None)


@contextmanager
def scope(db: Session, tenant_id: str | None, business_id: str | None):
    before = {k: db.info[k] for k in ("tenant_id", "business_id") if k in db.info}
    set_scope(db, tenant_id, business_id)
    try:
        yield db
    finally:
        clear_scope(db)
        db.info.update(before)


def business_id(db: Session) -> str:
    """The business this session works in; 409 when there is none (a tenant that has not added one yet)."""
    b = db.info.get("business_id")
    if not b:
        raise HTTPException(409, "Add or pick a business first.")
    return b


def _scoped(session: Session) -> bool:
    return "tenant_id" in session.info or "business_id" in session.info


def _on_execute(state) -> None:
    session = state.session
    if not _scoped(session) or state.execution_options.get("unscoped"):
        return
    tid, bid = session.info.get("tenant_id"), session.info.get("business_id") or NOWHERE
    if state.is_select:
        if state.is_column_load or state.is_relationship_load:
            return
        opts = [with_loader_criteria(m, m.location_id == bid, include_aliases=True) for m in BUSINESS_OWNED]
        opts.append(with_loader_criteria(
            DataIssue, or_(DataIssue.location_id == bid, (DataIssue.location_id.is_(None)) & (DataIssue.tenant_id == tid))
            if tid else DataIssue.location_id == bid, include_aliases=True))
        if tid:
            opts += [with_loader_criteria(m, m.tenant_id == tid, include_aliases=True) for m in TENANT_OWNED]
        state.statement = state.statement.options(*opts)
    elif state.is_update or state.is_delete:
        table = getattr(state.statement, "table", None)
        name = getattr(table, "name", None)
        if name in _BUSINESS_TABLES:
            state.statement = state.statement.where(_BUSINESS_TABLES[name].c.location_id == bid)
        elif name in _TENANT_TABLES and tid:
            state.statement = state.statement.where(_TENANT_TABLES[name].c.tenant_id == tid)
        elif name == DataIssue.__table__.name:
            state.statement = state.statement.where(DataIssue.__table__.c.location_id == bid)


def _before_flush(session: Session, _ctx, _instances) -> None:
    if not _scoped(session):
        return
    tid, bid = session.info.get("tenant_id"), session.info.get("business_id")
    for obj in session.new:
        if isinstance(obj, BUSINESS_OWNED):
            if obj.location_id is None:
                if not bid:
                    raise HTTPException(409, "Add or pick a business first.")
                obj.location_id = bid
            elif bid and obj.location_id != bid and session.info.get("also_business") != obj.location_id:
                raise HTTPException(403, "That belongs to another business.")
        elif isinstance(obj, DataIssue):
            if obj.location_id is None and obj.tenant_id is None:
                obj.location_id = bid
            if obj.tenant_id is None:
                obj.tenant_id = tid
        elif isinstance(obj, User) and tid and not obj.is_platform_admin:
            if obj.tenant_id is None:
                obj.tenant_id = tid
            elif obj.tenant_id != tid:
                raise HTTPException(403, "That belongs to another tenant.")
        elif isinstance(obj, Location) and tid:
            if obj.tenant_id is None:
                obj.tenant_id = tid
            elif obj.tenant_id != tid:
                raise HTTPException(403, "That belongs to another tenant.")


# first in line, so app.audit's bulk-statement recorder runs the statement already narrowed
event.listen(Session, "do_orm_execute", _on_execute, insert=True)
event.listen(Session, "before_flush", _before_flush)


def businesses_of(db: Session, user: User) -> list[Location]:
    """The active businesses this login may work in: all of the tenant's for an admin, else its memberships."""
    if not user.tenant_id:
        return []
    q = db.query(Location).filter(Location.tenant_id == user.tenant_id, Location.is_active.is_(True))
    if not user.is_admin:
        q = q.join(BusinessMember, BusinessMember.location_id == Location.id).filter(BusinessMember.user_id == user.id)
    return q.execution_options(unscoped=True).order_by(Location.name).all()
