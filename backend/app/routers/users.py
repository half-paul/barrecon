from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from .. import audit, auth, tenancy
from ..config import settings
from ..db import get_db
from ..models import AuditChange, AuditEvent, BusinessMember, Location, Tenant, User, UserPref, utcnow
from ..schemas import AuditOut, PasswordResetIn, PinSetIn, UserIn, UserOut, UserPatch
from ..services import logins, mail

router = APIRouter(prefix="/api/users", tags=["users"], dependencies=[auth.Admin])
audit_router = APIRouter(prefix="/api/audit", tags=["audit"], dependencies=[auth.perm("audit")])

DELETED = "!deleted"  # password_hash of a deleted login; matches no password


def _clean_permissions(p: dict[str, str]) -> dict[str, str]:
    out = {}
    for area, lv in p.items():
        if lv in (None, "", "none"):
            continue
        if area not in auth.AREAS:
            raise HTTPException(422, f"Unknown area {area}.")
        if lv not in auth.AREAS[area][1]:
            raise HTTPException(422, f"{auth.AREAS[area][0]} can't be given '{lv}'.")
        out[area] = lv
    return out


def _access(db: Session, u: User) -> dict[str, dict[str, str]]:
    return {m.location_id: dict(m.permissions or {}) for m in db.query(BusinessMember).filter(BusinessMember.user_id == u.id)}


def _set_access(db: Session, u: User, access: dict[str, dict[str, str]]) -> None:
    """Replace every membership: the businesses listed, with these levels, and no others."""
    have = {m.location_id: m for m in db.query(BusinessMember).filter(BusinessMember.user_id == u.id)}
    for bid, perms in access.items():
        if not db.get(Location, bid):  # only businesses of this tenant are visible here (app.tenancy)
            raise HTTPException(422, "Unknown business.")
        clean = _clean_permissions(perms)
        if bid in have:
            have[bid].permissions = clean
        else:
            db.add(BusinessMember(user_id=u.id, location_id=bid, permissions=clean))
    for bid, m in have.items():
        if bid not in access:
            db.delete(m)


def _set_here(db: Session, u: User, perms: dict[str, str]) -> None:
    """Levels in the business the admin is working in; the user becomes a member of it."""
    bid = tenancy.business_id(db)
    m = db.get(BusinessMember, (u.id, bid))
    if m:
        m.permissions = _clean_permissions(perms)
    else:
        db.add(BusinessMember(user_id=u.id, location_id=bid, permissions=_clean_permissions(perms)))


def _out(u: User, db: Session) -> UserOut:
    access = _access(db, u)
    return UserOut.model_validate(u).model_copy(update={"permissions": access.get(db.info.get("business_id") or "", {}), "access": access,
                                                        "has_pin": bool(u.pin_hash),
                                                        "locked": auth.is_locked(db, u), "mfa_enabled": bool(u.totp_enabled_at),
                                                        "invite_pending": u.password_hash == auth.INVITED})


def _get(db: Session, user_id: str) -> User:
    u = db.get(User, user_id)
    if not u or u.deleted_at:
        raise HTTPException(404, "user not found")
    return u


def _taken(db: Session, username: str, not_id: str | None = None) -> bool:
    q = db.query(User).filter(User.username == username)  # across every tenant: one login page for all
    if not_id:
        q = q.filter(User.id != not_id)
    return q.execution_options(unscoped=True).first() is not None


def _email_taken(db: Session, email: str, not_id: str | None = None) -> bool:
    q = db.query(User).filter(User.email == email)
    if not_id:
        q = q.filter(User.id != not_id)
    return q.execution_options(unscoped=True).first() is not None


def _send_invite(db: Session, u: User) -> bool:
    logins.revoke(db, u, "invite")
    token = logins.issue(db, u, "invite")
    db.commit()
    return mail.try_send(u.email, *mail.invite(u, token))


def _other_admins(db: Session, user_id: str) -> int:
    return db.query(User).filter(User.is_admin.is_(True), User.is_active.is_(True), User.id != user_id).count()


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db)):
    return [_out(u, db) for u in db.query(User).filter(User.deleted_at.is_(None))
            .order_by(User.is_active.desc(), func.lower(User.display_name))]


@router.post("", response_model=UserOut, status_code=201)
def create_user(body: UserIn, db: Session = Depends(get_db)):
    name = body.username.strip().lower()
    if _taken(db, name):
        raise HTTPException(409, f"The username {name} is taken.")
    email = body.email.strip().lower() if body.email else None
    if email and _email_taken(db, email):
        raise HTTPException(409, f"{email} is already on another login.")
    if body.password:
        auth.check_password(body.password, name, body.display_name)
    elif not mail.enabled():
        raise HTTPException(409, "Email isn't set up, so an invite can't be sent. Give them a password instead.")
    t = db.get(Tenant, db.info.get("tenant_id"))
    if t and t.max_users is not None and db.query(User).filter(User.is_active.is_(True)).count() >= t.max_users:
        raise HTTPException(409, f"This account is limited to {t.max_users} active logins. Switch one off first, or ask for a higher limit.")
    u = User(username=name, display_name=body.display_name.strip(), email=email,
             password_hash=auth.hash_password(body.password) if body.password else auth.INVITED,
             is_admin=body.is_admin, permissions={})
    db.add(u)
    db.flush()
    if body.access is not None:
        _set_access(db, u, body.access)
    elif db.info.get("business_id"):
        _set_here(db, u, body.permissions)
    db.commit()
    sent = None if body.password else _send_invite(db, u)
    return _out(u, db).model_copy(update={"invite_sent": sent})


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: str, body: UserPatch, me: User = auth.Admin, db: Session = Depends(get_db)):
    u = _get(db, user_id)
    ch = body.model_dump(exclude_unset=True)
    losing_admin = (ch.get("is_admin") is False and u.is_admin) or (ch.get("is_active") is False and u.is_admin)
    if losing_admin and not _other_admins(db, u.id):
        raise HTTPException(409, "This is the last active admin. Make someone else an admin first.")
    if u.id == me.id and (ch.get("is_active") is False or ch.get("is_admin") is False):
        raise HTTPException(409, "You can't switch off or demote your own login. Ask another admin.")
    if "username" in ch and ch["username"] is not None:
        name = ch["username"].strip().lower()
        if _taken(db, name, u.id):
            raise HTTPException(409, f"The username {name} is taken.")
        u.username = name
    if ch.get("display_name"):
        u.display_name = ch["display_name"].strip()
    if "email" in ch:
        email = (ch["email"] or "").strip().lower() or None
        if email and _email_taken(db, email, u.id):
            raise HTTPException(409, f"{email} is already on another login.")
        if email != u.email:  # links already sent to the old address stop working
            u.email = email
            logins.revoke(db, u, "invite", "reset", "mfa_email")
    if ch.get("is_admin") is not None:
        u.is_admin = ch["is_admin"]
    if ch.get("access") is not None:
        _set_access(db, u, ch["access"])
    elif ch.get("permissions") is not None:
        _set_here(db, u, ch["permissions"])
    if ch.get("is_active") is not None and ch["is_active"] != u.is_active:
        if ch["is_active"]:
            t = db.get(Tenant, db.info.get("tenant_id"))
            if t and t.max_users is not None and db.query(User).filter(User.is_active.is_(True)).count() >= t.max_users:
                raise HTTPException(409, f"This account is limited to {t.max_users} active logins.")
        u.is_active = ch["is_active"]
        if not u.is_active:
            u.session_version += 1  # logged out everywhere
    db.commit()
    return _out(u, db)


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: str, me: User = auth.Admin, db: Session = Depends(get_db)):
    """Remove a login for good. The row stays, because audit events point at it and are hash-chained: the log keeps
    showing who did what. Everything that could sign in or identify the person is scrubbed; the display name stays
    for the audit person filter, and the username and email become free for a new login."""
    u = _get(db, user_id)
    if u.id == me.id:
        raise HTTPException(409, "You can't delete your own login. Ask another admin.")
    if u.is_admin and u.is_active and not _other_admins(db, u.id):
        raise HTTPException(409, "This is the last active admin. Make someone else an admin first.")
    logins.revoke(db, u, "invite", "reset", "mfa_email")
    logins.clear_mfa(u)
    db.query(BusinessMember).filter(BusinessMember.user_id == u.id).delete(synchronize_session=False)
    db.query(UserPref).filter(UserPref.user_id == u.id).delete(synchronize_session=False)
    u.username = f"deleted-{u.id}"
    u.email = None
    u.password_hash = DELETED
    u.pin_hash = None
    u.is_admin = False
    u.is_active = False
    u.permissions = {}
    u.last_location_id = None
    u.session_version += 1  # logged out everywhere
    u.deleted_at = utcnow()
    db.commit()


@router.post("/{user_id}/password", response_model=UserOut)
def reset_password(user_id: str, body: PasswordResetIn, db: Session = Depends(get_db)):
    """Set someone's password (they forgot it). Logs them out everywhere."""
    u = _get(db, user_id)
    auth.check_password(body.password, u.username, u.display_name)
    u.password_hash = auth.hash_password(body.password)
    u.password_changed_at = utcnow()
    u.session_version += 1
    logins.revoke(db, u, "invite", "reset")
    db.commit()
    return _out(u, db)


@router.post("/{user_id}/invite", response_model=UserOut)
def resend_invite(user_id: str, db: Session = Depends(get_db)):
    """A fresh link; the earlier one stops working."""
    u = _get(db, user_id)
    if u.password_hash != auth.INVITED:
        raise HTTPException(409, f"{u.display_name} has already chosen a password.")
    if not u.email or not mail.enabled():
        raise HTTPException(409, "An invite needs an email address, and email set up on the server.")
    return _out(u, db).model_copy(update={"invite_sent": _send_invite(db, u)})


@router.post("/{user_id}/mfa/reset", response_model=UserOut)
def reset_mfa(user_id: str, me: User = auth.Admin, db: Session = Depends(get_db)):
    """Someone lost their phone and their recovery codes. They set up a new authenticator at their next login."""
    u = _get(db, user_id)
    if u.id == me.id:
        raise HTTPException(409, "Use My account for your own authenticator.")
    logins.clear_mfa(u)
    u.session_version += 1
    db.commit()
    if u.email and mail.enabled():
        mail.try_send(u.email, *mail.notice(u, f"{me.display_name} reset your two-step login. You'll set it up again at your next login."))
    return _out(u, db)


@router.post("/{user_id}/pin", response_model=UserOut)
def set_pin(user_id: str, body: PinSetIn, db: Session = Depends(get_db)):
    """A PIN for quick logins on the shared bar PC. A PIN session only reaches counts and deliveries."""
    u = _get(db, user_id)
    auth.check_pin(body.pin)
    u.pin_hash = auth.hash_password(body.pin)
    db.commit()
    return _out(u, db)


@router.delete("/{user_id}/pin", response_model=UserOut)
def clear_pin(user_id: str, db: Session = Depends(get_db)):
    u = _get(db, user_id)
    u.pin_hash = None
    db.commit()
    return _out(u, db)


@router.post("/{user_id}/unlock", response_model=UserOut)
def unlock(user_id: str, db: Session = Depends(get_db)):
    """Clear a lockout after too many wrong tries (it also clears by itself after 15 minutes). The audit event this
    request leaves is what auth.lock_status counts from."""
    u = _get(db, user_id)
    if not auth.is_locked(db, u) and not auth.pin_locked(db, u.username):
        raise HTTPException(409, f"{u.display_name}'s login isn't locked.")
    return _out(u, db).model_copy(update={"locked": False})


@router.post("/{user_id}/logout", response_model=UserOut)
def logout_everywhere(user_id: str, db: Session = Depends(get_db)):
    u = _get(db, user_id)
    u.session_version += 1
    db.commit()
    return _out(u, db)


def _local_start(d: date) -> datetime:
    return datetime.combine(d, time.min, ZoneInfo(settings.timezone)).astimezone(ZoneInfo("UTC"))


@audit_router.get("/users")
def audit_users(db: Session = Depends(get_db), me: User = auth.Auth):
    """For the audit log's person filter: the people who can work in this business (its members and the tenant's
    admins), plus deleted logins that left entries in this log."""
    members = db.query(BusinessMember.user_id).filter(BusinessMember.location_id == me.session_business)
    acted = db.query(AuditEvent.user_id).filter(_here(me), AuditEvent.user_id.isnot(None)).distinct()
    people = db.query(User).filter(User.tenant_id == me.tenant_id, or_(
        and_(User.deleted_at.is_(None), or_(User.is_admin.is_(True), User.id.in_(members))),
        and_(User.deleted_at.isnot(None), User.id.in_(acted))))
    return [{"id": u.id, "username": u.username, "display_name": u.display_name, "is_active": u.is_active,
             "deleted": u.deleted_at is not None}
            for u in people.order_by(func.lower(User.display_name))]


def event_filters(query, user_id: str | None = None, area: str | None = None, action: str | None = None,
                  date_from: date | None = None, date_to: date | None = None, q: str | None = None):
    """The audit log's filters, shared by a business's log and the platform console's."""
    if user_id:
        query = query.filter(AuditEvent.user_id == user_id)
    if area:
        query = query.filter(AuditEvent.area == area)
    if action:
        query = query.filter(AuditEvent.action == action)
    if date_from:
        query = query.filter(AuditEvent.at >= _local_start(date_from))
    if date_to:
        query = query.filter(AuditEvent.at < _local_start(date_to + timedelta(days=1)))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(AuditEvent.path.ilike(like), AuditEvent.route.ilike(like), AuditEvent.username.ilike(like)))
    return query


def events_out(db: Session, rows: list[AuditEvent], links: bool = True) -> list[AuditOut]:
    n = audit.count_changes(db, [r.id for r in rows])
    out = []
    for r in rows:
        label, link = audit.describe(r)
        out.append(AuditOut.model_validate(r).model_copy(update={"label": label, "link": link if links else None, "changes": n.get(r.id, 0)}))
    return out


def _here(me: User):
    """What one business's audit log holds: what was done while working in that business. A tenant admin also sees
    the tenant's entries that belong to no business (a failed login, a password reset by email), whichever business
    they are in, since those would otherwise show nowhere but the platform console."""
    mine = AuditEvent.location_id == me.session_business
    if auth.is_admin(me):
        mine = or_(mine, AuditEvent.location_id.is_(None))
    return and_(AuditEvent.tenant_id == me.tenant_id, mine)


@audit_router.get("")
def list_events(db: Session = Depends(get_db), user_id: str | None = None, area: str | None = None,
                action: str | None = None, date_from: date | None = None, date_to: date | None = None,
                q: str | None = None, limit: int = 100, offset: int = 0, me: User = auth.Auth):
    """The audit log of the business being worked in, for everyone including tenant admins: another business's log
    is read by switching to it. Every tenant and business together is the platform console's log."""
    query = event_filters(db.query(AuditEvent).filter(_here(me)), user_id, area, action, date_from, date_to, q)
    total = query.count()
    rows = query.order_by(AuditEvent.at.desc()).offset(max(offset, 0)).limit(min(max(limit, 1), 500)).all()
    return {"total": total, "events": events_out(db, rows)}


@audit_router.get("/verify")
def verify(db: Session = Depends(get_db)):
    """Recompute the hash chain: any edited or removed entry (other than the oldest ones purged by retention) shows."""
    return audit.verify(db)


@audit_router.get("/{event_id}/changes")
def changes(event_id: str, db: Session = Depends(get_db), me: User = auth.Auth):
    ev = db.query(AuditEvent).filter(AuditEvent.id == event_id, _here(me)).first()
    if not ev:
        raise HTTPException(404, "entry not found")
    rows = db.query(AuditChange).filter(AuditChange.event_id == event_id).order_by(AuditChange.position).all()
    return [{"table": c.table_name, "row_id": c.row_id, "op": c.op, "changes": c.changes} for c in rows]
