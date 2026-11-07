from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .. import audit, auth
from ..config import settings
from ..db import get_db
from ..models import AuditChange, AuditEvent, User, utcnow
from ..schemas import AuditOut, PasswordResetIn, PinSetIn, UserIn, UserOut, UserPatch

router = APIRouter(prefix="/api/users", tags=["users"], dependencies=[auth.Admin])
audit_router = APIRouter(prefix="/api/audit", tags=["audit"], dependencies=[auth.perm("audit")])


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


def _out(u: User, db: Session) -> UserOut:
    return UserOut.model_validate(u).model_copy(update={"permissions": u.permissions or {}, "has_pin": bool(u.pin_hash),
                                                        "locked": auth.is_locked(db, u)})


def _get(db: Session, user_id: str) -> User:
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "user not found")
    return u


def _taken(db: Session, username: str, not_id: str | None = None) -> bool:
    q = db.query(User).filter(User.username == username)
    if not_id:
        q = q.filter(User.id != not_id)
    return db.query(q.exists()).scalar()


def _other_admins(db: Session, user_id: str) -> int:
    return db.query(User).filter(User.is_admin.is_(True), User.is_active.is_(True), User.id != user_id).count()


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db)):
    return [_out(u, db) for u in db.query(User).order_by(User.is_active.desc(), func.lower(User.display_name))]


@router.post("", response_model=UserOut, status_code=201)
def create_user(body: UserIn, db: Session = Depends(get_db)):
    name = body.username.strip().lower()
    if _taken(db, name):
        raise HTTPException(409, f"The username {name} is taken.")
    auth.check_password(body.password, name, body.display_name)
    u = User(username=name, display_name=body.display_name.strip(), password_hash=auth.hash_password(body.password),
             is_admin=body.is_admin, permissions=_clean_permissions(body.permissions))
    db.add(u)
    db.commit()
    return _out(u, db)


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
    if ch.get("is_admin") is not None:
        u.is_admin = ch["is_admin"]
    if ch.get("permissions") is not None:
        u.permissions = _clean_permissions(ch["permissions"])
    if ch.get("is_active") is not None and ch["is_active"] != u.is_active:
        u.is_active = ch["is_active"]
        if not u.is_active:
            u.session_version += 1  # logged out everywhere
    db.commit()
    return _out(u, db)


@router.post("/{user_id}/password", response_model=UserOut)
def reset_password(user_id: str, body: PasswordResetIn, db: Session = Depends(get_db)):
    """Set someone's password (they forgot it). Logs them out everywhere."""
    u = _get(db, user_id)
    auth.check_password(body.password, u.username, u.display_name)
    u.password_hash = auth.hash_password(body.password)
    u.password_changed_at = utcnow()
    u.session_version += 1
    db.commit()
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
def audit_users(db: Session = Depends(get_db)):
    """Everyone, for the audit log's user filter."""
    return [{"id": u.id, "username": u.username, "display_name": u.display_name, "is_active": u.is_active}
            for u in db.query(User).order_by(func.lower(User.display_name))]


@audit_router.get("")
def list_events(db: Session = Depends(get_db), user_id: str | None = None, area: str | None = None,
                action: str | None = None, date_from: date | None = None, date_to: date | None = None,
                q: str | None = None, limit: int = 100, offset: int = 0):
    query = db.query(AuditEvent)
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
    total = query.count()
    rows = query.order_by(AuditEvent.at.desc()).offset(max(offset, 0)).limit(min(max(limit, 1), 500)).all()
    n = audit.count_changes(db, [r.id for r in rows])
    out = []
    for r in rows:
        label, link = audit.describe(r)
        out.append(AuditOut.model_validate(r).model_copy(update={"label": label, "link": link, "changes": n.get(r.id, 0)}))
    return {"total": total, "events": out}


@audit_router.get("/verify")
def verify(db: Session = Depends(get_db)):
    """Recompute the hash chain: any edited or removed entry (other than the oldest ones purged by retention) shows."""
    return audit.verify(db)


@audit_router.get("/{event_id}/changes")
def changes(event_id: str, db: Session = Depends(get_db)):
    rows = db.query(AuditChange).filter(AuditChange.event_id == event_id).order_by(AuditChange.position).all()
    return [{"table": c.table_name, "row_id": c.row_id, "op": c.op, "changes": c.changes} for c in rows]
