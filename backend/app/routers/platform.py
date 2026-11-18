"""The platform console: everything above the tenants. Only platform admins (User.is_platform_admin) get in.

Tenants and their limits, suspending one, usage (Claude tokens and their estimated cost, activity, storage),
the monthly report for billing, and the few settings that cover every tenant. Sessions here carry no tenant, so
nothing is filtered by app.tenancy: every query below says which tenant it is about.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Response
from openpyxl import Workbook
from openpyxl.styles import Font
from pydantic import BaseModel, Field
from sqlalchemy import case, distinct, func
from sqlalchemy.orm import Session

from .. import audit, auth
from ..db import get_db
from ..models import (AiUsage, AuditChange, AuditEvent, BusinessLogo, BusinessMember, CountSession, Delivery, DeliveryReceipt, ImportBatch,
                      InventoryItem, Location, Recipe, RecipePhoto, Tenant, User, utcnow)
from ..schemas import EMAIL
from ..services import mail, recipe_match, settings_svc, usage
from .users import _send_invite, event_filters, events_out

router = APIRouter(prefix="/api/platform", tags=["platform"], dependencies=[auth.Platform])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
USERNAME = r"^[A-Za-z0-9._@-]+$"


class AdminIn(BaseModel):
    """The tenant's first admin. With an email and no password they get an invite."""
    username: str = Field(min_length=2, max_length=64, pattern=USERNAME)
    display_name: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=254, pattern=EMAIL)
    password: str | None = Field(default=None, min_length=8, max_length=200)


class TenantIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    plan: str | None = Field(default=None, max_length=64)
    contact_name: str | None = Field(default=None, max_length=120)
    contact_email: str | None = Field(default=None, max_length=254, pattern=EMAIL + "|^$")
    notes: str | None = None
    max_businesses: int | None = Field(default=None, ge=0)
    max_users: int | None = Field(default=None, ge=0)
    monthly_token_cap: int | None = Field(default=None, ge=0)
    admin: AdminIn | None = None
    business_name: str | None = Field(default=None, max_length=120)  # its first business, if given


class TenantPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    plan: str | None = Field(default=None, max_length=64)
    contact_name: str | None = Field(default=None, max_length=120)
    contact_email: str | None = Field(default=None, max_length=254, pattern=EMAIL + "|^$")
    notes: str | None = None
    max_businesses: int | None = Field(default=None, ge=0)
    max_users: int | None = Field(default=None, ge=0)
    monthly_token_cap: int | None = Field(default=None, ge=0)


class BusinessFeatures(BaseModel):
    """What a business is switched on for. Only the platform console sets these."""
    track_food: bool | None = None


class PlatformSettingsIn(BaseModel):
    audit_retention_days: int = Field(ge=0, le=3650)


# ---- figures ------------------------------------------------------------------------------------------------

def _month(m: str | None) -> tuple[datetime, datetime, str]:
    """(start, end, "YYYY-MM") of a calendar month in UTC; the current one by default."""
    try:
        first = datetime.strptime(m, "%Y-%m").replace(tzinfo=timezone.utc) if m else usage.month_start()
    except ValueError:
        raise HTTPException(422, "month is YYYY-MM") from None
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, nxt, first.strftime("%Y-%m")


def _f(v) -> float:
    return float(v or 0)


def _usage_by_tenant(db: Session, start: datetime, end: datetime) -> dict[str, dict]:
    rows = db.query(AiUsage.tenant_id, func.count(), func.sum(AiUsage.input_tokens), func.sum(AiUsage.output_tokens),
                    func.sum(AiUsage.cost_usd), func.sum(case((AiUsage.ok.is_(False), 1), else_=0))) \
        .filter(AiUsage.at >= start, AiUsage.at < end).group_by(AiUsage.tenant_id).all()
    return {t: {"calls": n, "input_tokens": int(i or 0), "output_tokens": int(o or 0), "cost_usd": _f(c), "failed": int(bad or 0)}
            for t, n, i, o, c, bad in rows}


def _activity_by_tenant(db: Session, start: datetime, end: datetime) -> dict[str, dict]:
    """From the audit log: logins, people who did anything, and the last thing anyone did."""
    out: dict[str, dict] = {}
    rows = db.query(AuditEvent.tenant_id, func.sum(case((AuditEvent.action == "login", 1), else_=0)),
                    func.count(distinct(AuditEvent.user_id)), func.sum(case((AuditEvent.action == "change", 1), else_=0))) \
        .filter(AuditEvent.at >= start, AuditEvent.at < end, AuditEvent.tenant_id.isnot(None)).group_by(AuditEvent.tenant_id).all()
    for t, logins, people, changes in rows:
        out[t] = {"logins": int(logins or 0), "active_users": int(people or 0), "changes": int(changes or 0)}
    return out


def _work_by_tenant(db: Session, start: datetime, end: datetime) -> dict[str, dict]:
    """What was done in the period: counts and deliveries posted, Swype sales imports."""
    out: dict[str, dict] = {}

    def add(key: str, model, when, *where) -> None:
        q = db.query(Location.tenant_id, func.count()).select_from(model).join(Location, Location.id == model.location_id) \
            .filter(when >= start, when < end, *where).group_by(Location.tenant_id)
        for t, n in q:
            out.setdefault(t, {})[key] = n

    add("counts_posted", CountSession, CountSession.posted_at, CountSession.status.in_(("posted", "superseded")))
    add("deliveries_posted", Delivery, Delivery.posted_at, Delivery.status == "posted")
    add("sales_imports", ImportBatch, ImportBatch.imported_at, ImportBatch.dataset == "item_sales", ImportBatch.status == "ok")
    return out


def _last_activity(db: Session) -> dict[str, datetime]:
    return dict(db.query(AuditEvent.tenant_id, func.max(AuditEvent.at)).filter(AuditEvent.tenant_id.isnot(None))
                .group_by(AuditEvent.tenant_id).all())


def _storage_by_business(db: Session, tenant_id: str | None = None) -> dict[str, int]:
    """Bytes of receipts, recipe photos and logos kept in the database, per business."""
    out: dict[str, int] = {}

    def add(q) -> None:
        if tenant_id:
            q = q.filter(Location.tenant_id == tenant_id)
        for b, n in q.group_by(Location.id):
            out[b] = out.get(b, 0) + int(n or 0)

    add(db.query(Location.id, func.sum(func.length(DeliveryReceipt.data))).select_from(DeliveryReceipt)
        .join(Delivery, Delivery.id == DeliveryReceipt.delivery_id).join(Location, Location.id == Delivery.location_id))
    add(db.query(Location.id, func.sum(func.length(RecipePhoto.data))).select_from(RecipePhoto)
        .join(Recipe, Recipe.id == RecipePhoto.recipe_id).join(Location, Location.id == Recipe.location_id))
    add(db.query(Location.id, func.sum(func.length(BusinessLogo.data))).select_from(BusinessLogo)
        .join(Location, Location.id == BusinessLogo.location_id))
    return out


def _counts(db: Session) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    biz = dict(db.query(Location.tenant_id, func.count()).filter(Location.is_active.is_(True)).group_by(Location.tenant_id).all())
    users = dict(db.query(User.tenant_id, func.count()).filter(User.is_active.is_(True), User.tenant_id.isnot(None))
                 .group_by(User.tenant_id).all())
    by_biz = _storage_by_business(db)
    storage: dict[str, int] = {}
    for b, t in db.query(Location.id, Location.tenant_id):
        storage[t] = storage.get(t, 0) + by_biz.get(b, 0)
    return biz, users, storage


def _tenant_out(t: Tenant) -> dict:
    return {"id": t.id, "name": t.name, "status": t.status, "plan": t.plan, "contact_name": t.contact_name,
            "contact_email": t.contact_email, "notes": t.notes, "max_businesses": t.max_businesses, "max_users": t.max_users,
            "monthly_token_cap": t.monthly_token_cap, "suspended_at": t.suspended_at, "created_at": t.created_at}


def _rows(db: Session, month: str | None) -> tuple[str, list[dict]]:
    """One row per tenant for a month: what the list, the report and its downloads all show."""
    start, end, label = _month(month)
    use, act, work = _usage_by_tenant(db, start, end), _activity_by_tenant(db, start, end), _work_by_tenant(db, start, end)
    biz, users, storage = _counts(db)
    last = _last_activity(db)
    out = []
    for t in db.query(Tenant).order_by(func.lower(Tenant.name)):
        u, a, w = use.get(t.id, {}), act.get(t.id, {}), work.get(t.id, {})
        tokens = u.get("input_tokens", 0) + u.get("output_tokens", 0)
        out.append({**_tenant_out(t), "businesses": biz.get(t.id, 0), "users": users.get(t.id, 0),
                    "active_users": a.get("active_users", 0), "logins": a.get("logins", 0), "changes": a.get("changes", 0),
                    "counts_posted": w.get("counts_posted", 0), "deliveries_posted": w.get("deliveries_posted", 0),
                    "sales_imports": w.get("sales_imports", 0), "receipts_read": u.get("calls", 0), "receipts_failed": u.get("failed", 0),
                    "input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0), "tokens": tokens,
                    "cost_usd": round(u.get("cost_usd", 0.0), 4), "storage_bytes": storage.get(t.id, 0),
                    "cap_used_pct": round(100 * tokens / t.monthly_token_cap, 1) if t.monthly_token_cap else None,
                    "last_activity": last.get(t.id)})
    return label, out


def _daily(db: Session, days: int, tenant_id: str | None = None) -> list[dict]:
    """Tokens, cost and receipts per UTC day for the last `days` days, days with nothing included."""
    today = datetime.now(timezone.utc).date()
    first = today - timedelta(days=days - 1)
    q = db.query(func.date(AiUsage.at), func.sum(AiUsage.input_tokens), func.sum(AiUsage.output_tokens), func.sum(AiUsage.cost_usd), func.count()) \
        .filter(AiUsage.at >= datetime.combine(first, datetime.min.time(), timezone.utc))
    if tenant_id:
        q = q.filter(AiUsage.tenant_id == tenant_id)
    got = {str(d): (int(i or 0), int(o or 0), _f(c), n) for d, i, o, c, n in q.group_by(func.date(AiUsage.at))}
    out = []
    for k in range(days):
        d = (first + timedelta(days=k)).isoformat()
        i, o, c, n = got.get(d, (0, 0, 0.0, 0))
        out.append({"day": d, "input_tokens": i, "output_tokens": o, "cost_usd": round(c, 4), "calls": n})
    return out


def _get(db: Session, tenant_id: str) -> Tenant:
    t = db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(404, "tenant not found")
    return t


# ---- overview and tenants -----------------------------------------------------------------------------------

@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    label, rows = _rows(db, None)
    return {"month": label,
            "tenants": len(rows), "active_tenants": sum(r["status"] == "active" for r in rows),
            "suspended_tenants": sum(r["status"] != "active" for r in rows),
            "businesses": sum(r["businesses"] for r in rows), "users": sum(r["users"] for r in rows),
            "active_users": sum(r["active_users"] for r in rows),
            "tokens": sum(r["tokens"] for r in rows), "cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
            "receipts_read": sum(r["receipts_read"] for r in rows), "storage_bytes": sum(r["storage_bytes"] for r in rows),
            "near_cap": [{"id": r["id"], "name": r["name"], "cap_used_pct": r["cap_used_pct"]} for r in rows
                         if r["cap_used_pct"] is not None and r["cap_used_pct"] >= 80],
            "daily": _daily(db, 30), "prices": {m: list(p) for m, p in {**usage.PRICES, **{k: tuple(v) for k, v in usage.settings.ai_prices.items()}}.items()}}


@router.get("/tenants")
def list_tenants(month: str | None = None, db: Session = Depends(get_db)):
    label, rows = _rows(db, month)
    return {"month": label, "tenants": rows}


@router.post("/tenants", status_code=201)
def create_tenant(body: TenantIn, db: Session = Depends(get_db)):
    name = body.name.strip()
    if db.query(Tenant).filter(func.lower(Tenant.name) == name.lower()).first():
        raise HTTPException(409, f"There is already a tenant called {name}.")
    t = Tenant(name=name, plan=body.plan, contact_name=body.contact_name, contact_email=body.contact_email or None, notes=body.notes,
               max_businesses=body.max_businesses, max_users=body.max_users, monthly_token_cap=body.monthly_token_cap)
    db.add(t)
    db.flush()
    if body.business_name and body.business_name.strip():
        db.add(Location(tenant_id=t.id, name=body.business_name.strip()))
    invite_sent = None
    if body.admin:
        u = _new_admin(db, t, body.admin)
        db.commit()
        invite_sent = None if body.admin.password else _send_invite(db, u)
    db.commit()
    return {**_tenant_out(t), "invite_sent": invite_sent}


def _new_admin(db: Session, t: Tenant, a: AdminIn) -> User:
    uname = a.username.strip().lower()
    if db.query(User).filter(User.username == uname).first():
        raise HTTPException(409, f"The username {uname} is taken (usernames are unique across all tenants).")
    email = a.email.strip().lower() if a.email else None
    if email and db.query(User).filter(User.email == email).first():
        raise HTTPException(409, f"{email} is already on another login.")
    if a.password:
        auth.check_password(a.password, uname, a.display_name)
    elif not email:
        raise HTTPException(422, "Give the admin an email for the invite, or a password.")
    elif not mail.enabled():
        raise HTTPException(409, "Email isn't set up, so an invite can't be sent. Give the admin a password instead.")
    u = User(tenant_id=t.id, username=uname, display_name=a.display_name.strip(), email=email, is_admin=True,
             password_hash=auth.hash_password(a.password) if a.password else auth.INVITED)
    db.add(u)
    return u


@router.get("/tenants/{tenant_id}")
def tenant_detail(tenant_id: str, db: Session = Depends(get_db)):
    t = _get(db, tenant_id)
    start, end, label = _month(None)
    storage = _storage_by_business(db, t.id)
    members = dict(db.query(BusinessMember.location_id, func.count()).join(Location, Location.id == BusinessMember.location_id)
                   .filter(Location.tenant_id == t.id).group_by(BusinessMember.location_id).all())
    items = dict(db.query(InventoryItem.location_id, func.count()).join(Location, Location.id == InventoryItem.location_id)
                 .filter(Location.tenant_id == t.id).group_by(InventoryItem.location_id).all())
    use = {b: (n, int(i or 0) + int(o or 0), _f(c)) for b, n, i, o, c in
           db.query(AiUsage.location_id, func.count(), func.sum(AiUsage.input_tokens), func.sum(AiUsage.output_tokens), func.sum(AiUsage.cost_usd))
           .filter(AiUsage.tenant_id == t.id, AiUsage.at >= start, AiUsage.at < end).group_by(AiUsage.location_id)}
    last = dict(db.query(AuditEvent.location_id, func.max(AuditEvent.at)).filter(AuditEvent.tenant_id == t.id).group_by(AuditEvent.location_id).all())
    counts = dict(db.query(CountSession.location_id, func.count()).join(Location, Location.id == CountSession.location_id)
                  .filter(Location.tenant_id == t.id, CountSession.status == "posted").group_by(CountSession.location_id).all())
    businesses = [{"id": b.id, "name": b.name, "kind": b.kind, "city": b.city, "is_active": b.is_active, "has_logo": bool(b.logo_at),
                   "track_food": bool(b.track_food), "swype": bool(b.swype_email), "members": members.get(b.id, 0), "items": items.get(b.id, 0),
                   "counts_posted": counts.get(b.id, 0), "receipts_read": use.get(b.id, (0, 0, 0.0))[0],
                   "tokens": use.get(b.id, (0, 0, 0.0))[1], "cost_usd": round(use.get(b.id, (0, 0, 0.0))[2], 4),
                   "storage_bytes": storage.get(b.id, 0), "last_activity": last.get(b.id), "created_at": b.created_at}
                  for b in db.query(Location).filter(Location.tenant_id == t.id).order_by(Location.is_active.desc(), func.lower(Location.name))]
    users = [{"id": u.id, "username": u.username, "display_name": u.display_name, "email": u.email, "is_admin": u.is_admin,
              "is_active": u.is_active, "mfa_enabled": bool(u.totp_enabled_at), "invite_pending": u.password_hash == auth.INVITED,
              "last_login_at": u.last_login_at}
             for u in db.query(User).filter(User.tenant_id == t.id, User.deleted_at.is_(None)).order_by(User.is_active.desc(), User.is_admin.desc(), func.lower(User.display_name))]
    months = []
    first = usage.month_start()
    for _ in range(12):
        nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
        n, i, o, c = db.query(func.count(), func.sum(AiUsage.input_tokens), func.sum(AiUsage.output_tokens), func.sum(AiUsage.cost_usd)) \
            .filter(AiUsage.tenant_id == t.id, AiUsage.at >= first, AiUsage.at < nxt).one()
        months.append({"month": first.strftime("%Y-%m"), "calls": n, "input_tokens": int(i or 0), "output_tokens": int(o or 0), "cost_usd": round(_f(c), 4)})
        first = (first - timedelta(days=1)).replace(day=1)
    _, rows = _rows(db, None)
    return {"tenant": next(r for r in rows if r["id"] == t.id), "month": label, "businesses": businesses, "users": users,
            "months": months, "daily": _daily(db, 30, t.id)}


@router.patch("/tenants/{tenant_id}")
def update_tenant(tenant_id: str, body: TenantPatch, db: Session = Depends(get_db)):
    t = _get(db, tenant_id)
    ch = body.model_dump(exclude_unset=True)
    if ch.get("name"):
        name = ch["name"].strip()
        if db.query(Tenant).filter(func.lower(Tenant.name) == name.lower(), Tenant.id != t.id).first():
            raise HTTPException(409, f"There is already a tenant called {name}.")
        t.name = name
    for k in ("plan", "contact_name", "contact_email", "notes"):
        if k in ch:
            setattr(t, k, (ch[k] or "").strip() or None)
    for k in ("max_businesses", "max_users", "monthly_token_cap"):  # null: no limit
        if k in ch:
            setattr(t, k, ch[k])
    db.commit()
    return _tenant_out(t)


@router.post("/tenants/{tenant_id}/suspend")
def suspend(tenant_id: str, db: Session = Depends(get_db)):
    """Its people can't log in and are logged out at their next click. Nothing is deleted; the nightly jobs skip it."""
    t = _get(db, tenant_id)
    t.status, t.suspended_at = "suspended", utcnow()
    db.commit()
    return _tenant_out(t)


@router.post("/tenants/{tenant_id}/reactivate")
def reactivate(tenant_id: str, db: Session = Depends(get_db)):
    t = _get(db, tenant_id)
    t.status, t.suspended_at = "active", None
    db.commit()
    return _tenant_out(t)


@router.post("/tenants/{tenant_id}/admins", status_code=201)
def add_admin(tenant_id: str, body: AdminIn, db: Session = Depends(get_db)):
    """Another admin login for the tenant (their only admin left, or lost access)."""
    t = _get(db, tenant_id)
    u = _new_admin(db, t, body)
    db.commit()
    return {"id": u.id, "username": u.username, "invite_sent": None if body.password else _send_invite(db, u)}


@router.patch("/businesses/{business_id}")
def update_business(business_id: str, body: BusinessFeatures, db: Session = Depends(get_db)):
    """Switch a feature of one business on or off. Food tracking on: the food it sold stops being marked as taking
    no stock, and the food already marked that way without anyone asking goes back to the Recipes queue to be
    linked to stock. Off: recipes made for food stay; food with none takes no stock again from the next sales import."""
    b = db.get(Location, business_id)
    if not b:
        raise HTTPException(404, "business not found")
    released = 0
    if body.track_food is not None and body.track_food != bool(b.track_food):
        b.track_food = body.track_food
        if body.track_food:
            released = recipe_match.release_food(db, b.id)
    db.commit()
    return {"id": b.id, "name": b.name, "track_food": bool(b.track_food), "food_released": released}


# ---- usage and reports --------------------------------------------------------------------------------------

@router.get("/usage")
def usage_rows(date_from: date | None = None, date_to: date | None = None, tenant_id: str | None = None,
               limit: int = 200, db: Session = Depends(get_db)):
    """Every metered Claude call, newest first, with the tenant and business it was for."""
    q = db.query(AiUsage, Tenant.name, Location.name).outerjoin(Tenant, Tenant.id == AiUsage.tenant_id) \
        .outerjoin(Location, Location.id == AiUsage.location_id)
    if date_from:
        q = q.filter(AiUsage.at >= datetime.combine(date_from, datetime.min.time(), timezone.utc))
    if date_to:
        q = q.filter(AiUsage.at < datetime.combine(date_to + timedelta(days=1), datetime.min.time(), timezone.utc))
    if tenant_id:
        q = q.filter(AiUsage.tenant_id == tenant_id)
    total = q.count()
    return {"total": total, "rows": [
        {"id": a.id, "at": a.at, "tenant_id": a.tenant_id, "tenant": tn, "business": bn, "kind": a.kind, "model": a.model,
         "input_tokens": a.input_tokens, "output_tokens": a.output_tokens, "cost_usd": float(a.cost_usd), "ok": a.ok}
        for a, tn, bn in q.order_by(AiUsage.at.desc()).limit(min(max(limit, 1), 1000))]}


REPORT_COLUMNS = [
    ("name", "Tenant"), ("status", "Status"), ("plan", "Plan"), ("businesses", "Businesses"), ("users", "Active logins"),
    ("active_users", "People active"), ("logins", "Logins"), ("changes", "Changes made"), ("counts_posted", "Counts posted"),
    ("deliveries_posted", "Deliveries posted"), ("sales_imports", "Sales imports"), ("receipts_read", "Receipts read"),
    ("input_tokens", "Input tokens"), ("output_tokens", "Output tokens"), ("tokens", "Tokens"), ("cost_usd", "Est. cost (USD)"),
    ("monthly_token_cap", "Token cap"), ("cap_used_pct", "Cap used %"), ("storage_bytes", "Storage (bytes)"),
]


@router.get("/reports/monthly")
def monthly_report(month: str | None = None, format: str = "json", db: Session = Depends(get_db)):
    """Usage per tenant for one calendar month (UTC), as JSON, CSV or XLSX."""
    label, rows = _rows(db, month)
    if format == "json":
        totals = {k: round(sum(r[k] or 0 for r in rows), 4) for k, _ in REPORT_COLUMNS
                  if k not in ("name", "status", "plan", "monthly_token_cap", "cap_used_pct")}
        return {"month": label, "columns": [{"key": k, "label": lb} for k, lb in REPORT_COLUMNS], "rows": rows, "totals": totals}
    table = [[lb for _, lb in REPORT_COLUMNS]] + [[r[k] for k, _ in REPORT_COLUMNS] for r in rows]
    name = f"usage-{label}"
    if format == "csv":
        buf = io.StringIO()
        csv.writer(buf).writerows(table)
        return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
    if format == "xlsx":
        wb = Workbook()
        ws = wb.active
        ws.title = label
        for row in table:
            ws.append([float(v) if isinstance(v, Decimal) else v for v in row])
        for c in ws[1]:
            c.font = Font(bold=True)
        ws.freeze_panes = "B2"
        ws.column_dimensions["A"].width = 32
        buf2 = io.BytesIO()
        wb.save(buf2)
        return Response(buf2.getvalue(), media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})
    raise HTTPException(422, "format is json, csv or xlsx")


# ---- platform settings, audit, admins -----------------------------------------------------------------------

@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    return {"audit_retention_days": int(settings_svc.get(db, "audit_retention_days")),
            "audit_purge_last": settings_svc.get(db, "audit_purge_last") or None}


@router.put("/settings")
def put_settings(body: PlatformSettingsIn, db: Session = Depends(get_db)):
    if 0 < body.audit_retention_days < 30:
        raise HTTPException(422, "Keep the audit log at least 30 days (or 0 to keep it forever).")
    settings_svc.set_many(db, {"audit_retention_days": str(body.audit_retention_days)})
    return get_settings(db)


@router.get("/audit/filters")
def audit_filters(db: Session = Depends(get_db)):
    """Every tenant with its businesses, for the log's filters."""
    by_tenant: dict[str, list] = {}
    for b in db.query(Location).order_by(func.lower(Location.name)):
        by_tenant.setdefault(b.tenant_id, []).append({"id": b.id, "name": b.name})
    return [{"id": t.id, "name": t.name, "businesses": by_tenant.get(t.id, [])} for t in db.query(Tenant).order_by(func.lower(Tenant.name))]


@router.get("/audit")
def audit_log(tenant_id: str | None = None, business_id: str | None = None, platform_only: bool = False, area: str | None = None,
              action: str | None = None, date_from: date | None = None, date_to: date | None = None, q: str | None = None,
              limit: int = 100, offset: int = 0, db: Session = Depends(get_db)):
    """The whole audit log: every tenant, every business, and what belongs to neither (this console, a login tried
    with a name nobody has). tenant_id "none" is the entries of no tenant; business_id "none" those of no business."""
    query = db.query(AuditEvent)
    if tenant_id:
        query = query.filter(AuditEvent.tenant_id.is_(None) if tenant_id == "none" else AuditEvent.tenant_id == tenant_id)
    if business_id:
        query = query.filter(AuditEvent.location_id.is_(None) if business_id == "none" else AuditEvent.location_id == business_id)
    if platform_only:
        query = query.filter(AuditEvent.area == "platform")
    query = event_filters(query, None, area, action, date_from, date_to, q)
    total = query.count()
    rows = query.order_by(AuditEvent.at.desc()).offset(max(offset, 0)).limit(min(max(limit, 1), 500)).all()
    tenants = dict(db.query(Tenant.id, Tenant.name).filter(Tenant.id.in_({r.tenant_id for r in rows if r.tenant_id})).all())
    places = dict(db.query(Location.id, Location.name).filter(Location.id.in_({r.location_id for r in rows if r.location_id})).all())
    return {"total": total, "events": [{**e.model_dump(), "tenant_id": r.tenant_id, "tenant": tenants.get(r.tenant_id),
                                        "business_id": r.location_id, "business": places.get(r.location_id)}
                                       for e, r in zip(events_out(db, rows, links=False), rows)]}


@router.get("/audit/{event_id}/changes")
def audit_changes(event_id: str, db: Session = Depends(get_db)):
    if not db.get(AuditEvent, event_id):
        raise HTTPException(404, "entry not found")
    rows = db.query(AuditChange).filter(AuditChange.event_id == event_id).order_by(AuditChange.position).all()
    return [{"table": c.table_name, "row_id": c.row_id, "op": c.op, "changes": c.changes} for c in rows]


@router.get("/audit/verify")
def audit_verify(db: Session = Depends(get_db)):
    return audit.verify(db)


@router.get("/admins")
def admins(db: Session = Depends(get_db)):
    return [{"id": u.id, "username": u.username, "display_name": u.display_name, "email": u.email, "is_active": u.is_active,
             "mfa_enabled": bool(u.totp_enabled_at), "last_login_at": u.last_login_at}
            for u in db.query(User).filter(User.is_platform_admin.is_(True)).order_by(func.lower(User.display_name))]


@router.post("/admins", status_code=201)
def add_platform_admin(body: AdminIn, db: Session = Depends(get_db)):
    uname = body.username.strip().lower()
    if db.query(User).filter(User.username == uname).first():
        raise HTTPException(409, f"The username {uname} is taken.")
    if not body.password:
        raise HTTPException(422, "Give the platform admin a password.")
    auth.check_password(body.password, uname, body.display_name)
    email = body.email.strip().lower() if body.email else None
    if email and db.query(User).filter(User.email == email).first():
        raise HTTPException(409, f"{email} is already on another login.")
    u = User(username=uname, display_name=body.display_name.strip(), email=email, is_platform_admin=True,
             password_hash=auth.hash_password(body.password))
    db.add(u)
    db.commit()
    return {"id": u.id, "username": u.username}


@router.post("/admins/{user_id}/active")
def set_admin_active(user_id: str, active: bool, me: User = auth.Platform, db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u or not u.is_platform_admin:
        raise HTTPException(404, "platform admin not found")
    if u.id == me.id and not active:
        raise HTTPException(409, "You can't switch off your own login.")
    u.is_active = active
    if not active:
        u.session_version += 1
    db.commit()
    return {"ok": True}
