from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session

from pydantic import BaseModel

from .. import auth, tenancy
from ..audit import client_ip, event_id, record
from ..db import get_db
from ..models import AuditEvent, Location, Tenant, User, utcnow
from ..schemas import ForgotIn, LoginIn, PasswordChangeIn, PinChangeIn, SetPasswordIn, TokenIn
from ..services import logins, mail

router = APIRouter(prefix="/api/auth", tags=["auth"])
FORGOT_IP_LIMIT = 20  # reset requests from one address in auth.LOCK_WINDOW


def business_out(b: Location) -> dict:
    """What every page may show of a business: its name, contact details and whether it has a logo."""
    return {"id": b.id, "name": b.name, "kind": b.kind, "address": b.address, "city": b.city, "phone": b.phone,
            "email": b.email, "website": b.website, "is_active": b.is_active,
            "logo_at": b.logo_at.isoformat() if b.logo_at else None}


def me_out(u: User, db: Session) -> dict:
    """The login, the tenant it belongs to, the business this session works in and the ones it can switch to.
    The session must have been entered (auth.enter) so the permissions are the ones in that business."""
    mine = tenancy.businesses_of(db, u)
    t = db.get(Tenant, u.tenant_id) if u.tenant_id else None
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "is_admin": auth.is_admin(u),
            "is_platform_admin": u.is_platform_admin,
            "permissions": auth.effective_permissions(u), "session_mode": u.session_mode, "has_pin": bool(u.pin_hash),
            "email": u.email, "mfa_enabled": bool(u.totp_enabled_at), "mfa_required": logins.must_enroll(u), "recovery_codes_left": len(u.recovery_codes or []),
            "idle_logout_minutes": int(auth.settings_svc.get(db, "idle_logout_minutes") or 0),
            "tenant": {"id": t.id, "name": t.name} if t else None,
            "business": next((business_out(b) for b in mine if b.id == u.session_business), None),
            "businesses": [business_out(b) for b in mine]}


@router.post("/login")
def login(body: LoginIn, request: Request, resp: Response, db: Session = Depends(get_db)):
    name = body.username.strip().lower()[:64]
    ip = client_ip(request.scope)
    method = "pin" if body.pin is not None else "password"
    known = db.query(User).filter(User.username == name).one_or_none()
    base = {"username": name, "path": request.url.path, "method": "POST", "ip": ip, "tenant_id": known.tenant_id if known else None}

    wait, failures = auth.lock_status(db, name, ip)
    if wait:
        record(**base, user_id=known.id if known else None, action="login_blocked", status=429, detail={"method": method, "retry_after": wait})
        raise HTTPException(429, f"Too many wrong tries. Try again in {max(1, wait // 60)} minute{'s' if wait > 60 else ''}.",
                            headers={"Retry-After": str(wait)})
    if method == "pin" and auth.pin_locked(db, name):
        record(**base, user_id=known.id if known else None, action="login_blocked", status=429, detail={"method": "pin", "reason": "pin locked"})
        raise HTTPException(429, "PIN login is off after too many wrong PINs. Log in with your password.")

    u = auth.authenticate(db, name, pw=body.password, pin=body.pin)
    if not u:
        reason = "inactive" if known and not known.is_active else "no PIN set" if method == "pin" and known and not known.pin_hash \
            else "wrong username or PIN" if method == "pin" else "wrong username or password"
        record(**base, user_id=known.id if known else None, action="login_failed", status=401, detail={"method": method, "reason": reason})
        if failures + 1 >= auth.LOCK_USER:
            auth.raise_lock_issue(db, name, ip)
        auth.fail_delay(failures + 1)
        raise HTTPException(401, "wrong username or PIN" if method == "pin" else "wrong username or password")
    if msg := auth.tenant_problem(db, u):  # the right password, but the tenant is suspended
        record(**base, user_id=u.id, action="login_failed", status=403, detail={"method": method, "reason": "tenant suspended"})
        raise HTTPException(403, msg)
    if logins.mfa_required(u, method):  # no session and no "login" event until the second step is done
        return {"ok": False, "mfa": "verify" if u.totp_enabled_at else "setup", "mfa_token": logins.pending_token(u),
                "email_recovery": bool(u.email and mail.enabled())}
    u.last_login_at = utcnow()
    db.commit()
    u.session_mode = method
    auth.enter(db, u, u.last_location_id)
    auth.set_session(resp, u, request, method)
    record(**base, id=event_id(request.scope), user_id=u.id, action="login", status=200, detail={"method": method},
           location_id=u.session_business)
    return {"ok": True, "user": me_out(u, db)}


@router.get("/options")
def options():
    """What the login page may offer, before anyone is logged in."""
    return {"email_enabled": mail.enabled()}


@router.post("/forgot")
def forgot(body: ForgotIn, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Same reply and same speed whether or not the login exists; the sending happens after the reply."""
    key = body.login.strip().lower()
    ip = client_ip(request.scope)
    u = db.query(User).filter(or_(User.username == key, User.email == key)).first()
    since = utcnow() - auth.LOCK_WINDOW
    busy = ip is not None and db.query(AuditEvent).filter(AuditEvent.action == "reset_requested", AuditEvent.ip == ip, AuditEvent.at >= since).count() >= FORGOT_IP_LIMIT
    if u and u.is_active and u.email and mail.enabled() and not busy and not logins.sent_too_often(db, u, "reset"):
        token = logins.issue(db, u, "reset")
        db.commit()
        background.add_task(mail.try_send, u.email, *mail.reset(u, token))
    record(id=event_id(request.scope), username=key[:64], user_id=u.id if u else None, tenant_id=u.tenant_id if u else None, action="reset_requested",
           path=request.url.path, method="POST", status=200, ip=ip)
    return {"ok": True}


def _link(db: Session, token: str):
    row = logins.find(db, token, ("invite", "reset"))
    u = db.get(User, row.user_id) if row else None
    if not u or not u.is_active:
        raise HTTPException(410, "This link has expired or was already used.")
    return row, u


@router.post("/link")
def link_info(body: TokenIn, db: Session = Depends(get_db)):
    """What the set-password page shows. A POST so the token stays out of URLs and logs."""
    row, u = _link(db, body.token)
    return {"kind": row.kind, "username": u.username, "display_name": u.display_name}


@router.post("/set-password")
def set_password(body: SetPasswordIn, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Finish an invite or a reset. It never logs anyone in: an admin still needs their second step, so email alone can't open the portal."""
    row, u = _link(db, body.token)
    invite = row.kind == "invite"
    name = (body.display_name or "").strip() or u.display_name if invite else u.display_name
    auth.check_password(body.password, u.username, name)  # before spending the link, so a weak password can be retried
    if not logins.consume(db, row):
        raise HTTPException(410, "This link has expired or was already used.")
    u.display_name = name
    u.password_hash = auth.hash_password(body.password)
    u.password_changed_at = utcnow()
    u.session_version += 1
    logins.revoke(db, u, "invite", "reset")
    db.commit()
    record(id=event_id(request.scope), username=u.username, user_id=u.id, tenant_id=u.tenant_id, action="invite_accepted" if invite else "password_reset",
           path=request.url.path, method="POST", status=200, ip=client_ip(request.scope))
    if not invite:
        background.add_task(mail.try_send, u.email, *mail.notice(u, "Your password was just changed using an emailed link."))
    return {"ok": True, "username": u.username}


@router.post("/logout")
def logout(request: Request, resp: Response, db: Session = Depends(get_db)):
    try:
        u = auth.current_user(request, db)
        record(user_id=u.id, username=u.username, tenant_id=u.tenant_id, location_id=u.session_business, action="logout",
               path=request.url.path, method="POST", status=200, ip=client_ip(request.scope))
    except HTTPException:
        pass
    auth.clear_session(resp)
    return {"ok": True}


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    """Also keeps the session alive: the page calls it while someone is working without saving."""
    try:
        return {"user": me_out(auth.current_user(request, db), db)}
    except HTTPException as e:
        return {"user": None, "reason": e.detail}


class BusinessPick(BaseModel):
    business_id: str


@router.post("/business")
def switch_business(body: BusinessPick, request: Request, resp: Response, u: User = auth.Auth, db: Session = Depends(get_db)):
    """Work in another business of the same tenant. The session cookie carries the choice, so it holds for every
    tab; it is also remembered as where this login starts next time."""
    if not any(b.id == body.business_id for b in tenancy.businesses_of(db, u)):
        raise HTTPException(403, "You don't have access to that business.")
    u.last_location_id = body.business_id
    db.commit()
    auth.enter(db, u, body.business_id)
    auth.note(request, u)
    auth.set_session(resp, u, request, u.session_mode, u.session_started)
    return {"ok": True, "user": me_out(u, db)}


@router.get("/areas", dependencies=[auth.Auth])
def areas():
    return [{"key": k, "label": label, "levels": list(levels)} for k, (label, levels) in auth.AREAS.items()]


@router.post("/password")
def change_password(body: PasswordChangeIn, request: Request, resp: Response, u: User = auth.Auth, db: Session = Depends(get_db)):
    """Change your own password. Other sessions are logged out; this one gets a fresh cookie."""
    if u.session_mode == "pin":
        raise HTTPException(403, "Log in with your password to change it.")
    if not auth.verify_password(body.current_password, u.password_hash):
        raise HTTPException(400, "Your current password is not right.")
    auth.check_password(body.new_password, u.username, u.display_name)
    u.password_hash = auth.hash_password(body.new_password)
    u.password_changed_at = utcnow()
    u.session_version += 1
    logins.revoke(db, u, "reset")
    db.commit()
    auth.set_session(resp, u, request, started=u.session_started)
    return {"ok": True}


@router.post("/pin")
def change_pin(body: PinChangeIn, u: User = auth.Auth, db: Session = Depends(get_db)):
    """Set or remove your own PIN (pin null). Needs your password."""
    if u.session_mode == "pin":
        raise HTTPException(403, "Log in with your password to change your PIN.")
    if not auth.verify_password(body.current_password, u.password_hash):
        raise HTTPException(400, "Your current password is not right.")
    if body.pin is not None:
        auth.check_pin(body.pin)
    u.pin_hash = auth.hash_password(body.pin) if body.pin is not None else None
    db.commit()
    return {"ok": True, "has_pin": bool(u.pin_hash)}
