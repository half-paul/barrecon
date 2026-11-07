from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from .. import auth
from ..audit import client_ip, event_id, record
from ..db import get_db
from ..models import User, utcnow
from ..schemas import LoginIn, PasswordChangeIn, PinChangeIn

router = APIRouter(prefix="/api/auth", tags=["auth"])


def me_out(u: User, db: Session) -> dict:
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "is_admin": auth.is_admin(u),
            "permissions": auth.effective_permissions(u), "session_mode": u.session_mode, "has_pin": bool(u.pin_hash),
            "idle_logout_minutes": int(auth.settings_svc.get(db, "idle_logout_minutes") or 0)}


@router.post("/login")
def login(body: LoginIn, request: Request, resp: Response, db: Session = Depends(get_db)):
    name = body.username.strip().lower()[:64]
    ip = client_ip(request.scope)
    method = "pin" if body.pin is not None else "password"
    base = {"username": name, "path": request.url.path, "method": "POST", "ip": ip}
    known = db.query(User).filter(User.username == name).one_or_none()

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
    u.last_login_at = utcnow()
    db.commit()
    u.session_mode = method
    auth.set_session(resp, u, request, method)
    record(**base, id=event_id(request.scope), user_id=u.id, action="login", status=200, detail={"method": method})
    return {"ok": True, "user": me_out(u, db)}


@router.post("/logout")
def logout(request: Request, resp: Response, db: Session = Depends(get_db)):
    try:
        u = auth.current_user(request, db)
        record(user_id=u.id, username=u.username, action="logout", path=request.url.path, method="POST", status=200,
               ip=client_ip(request.scope))
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
    db.commit()
    auth.set_session(resp, u, request)
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
