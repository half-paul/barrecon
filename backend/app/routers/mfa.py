"""The optional second login step. See services/logins.py.

Nothing here records "login" until the whole login is done: that event is what clears the failure count, so
a password alone must not reset the limit on guessing codes.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import auth
from ..audit import client_ip, event_id, record
from ..db import get_db
from ..models import DataIssue, User, utcnow
from ..services import logins, mail
from .auth import me_out

router = APIRouter(prefix="/api/auth/mfa", tags=["auth"])


class PendingIn(BaseModel):
    mfa_token: str = Field(max_length=500)


class CodeIn(PendingIn):
    mfa_code: str = Field(max_length=32)


class PasswordIn(BaseModel):
    current_password: str = Field(max_length=200)


class EnrollCodeIn(BaseModel):
    mfa_code: str = Field(max_length=32)


class SelfCheckIn(BaseModel):
    current_password: str = Field(max_length=200)
    mfa_code: str = Field(max_length=32)


def _base(request: Request, u: User) -> dict:
    return {"username": u.username, "user_id": u.id, "tenant_id": u.tenant_id, "path": request.url.path, "method": "POST", "ip": client_ip(request.scope)}


def _pending(db: Session, token: str) -> User:
    u = logins.pending_user(db, token)
    if not u:
        raise HTTPException(401, "That took too long. Log in again.")
    if msg := auth.tenant_problem(db, u):
        raise HTTPException(403, msg)
    return u


def _guard(db: Session, u: User, request: Request) -> None:
    wait, _ = auth.lock_status(db, u.username, client_ip(request.scope))
    if wait:
        record(**_base(request, u), action="login_blocked", status=429, detail={"method": "mfa", "retry_after": wait})
        raise HTTPException(429, f"Too many wrong tries. Try again in {max(1, wait // 60)} minute{'s' if wait > 60 else ''}.",
                            headers={"Retry-After": str(wait)})


def _wrong_code(db: Session, u: User, request: Request) -> None:
    db.rollback()
    ip = client_ip(request.scope)
    record(**_base(request, u), action="login_failed", status=401, detail={"method": "mfa", "reason": "wrong code"})
    _, failures = auth.lock_status(db, u.username, ip)
    if failures >= auth.LOCK_USER:
        auth.raise_lock_issue(db, u.username, ip)
    auth.fail_delay(failures)


def _finish(u: User, how: str, request: Request, resp: Response, db: Session) -> dict:
    u.last_login_at = utcnow()
    db.commit()
    u.session_mode, u.session_mfa = "password", True
    auth.enter(db, u, u.last_location_id)
    auth.set_session(resp, u, request)
    record(**_base(request, u), id=event_id(request.scope), action="login", status=200, detail={"method": how},
           location_id=u.session_business)
    return {"ok": True, "user": me_out(u, db)}


@router.post("/setup")
def setup(body: PendingIn, request: Request, db: Session = Depends(get_db)):
    """A new secret for the QR code. Nothing changes for login until a code from it is confirmed."""
    u = _pending(db, body.mfa_token)
    if u.totp_enabled_at:
        raise HTTPException(409, "Two-step login is already set up.")
    out = logins.start_setup(u)
    db.commit()
    record(**_base(request, u), id=event_id(request.scope), action="mfa_setup_started", status=200)
    return out


@router.post("/confirm")
def confirm(body: CodeIn, request: Request, resp: Response, db: Session = Depends(get_db)):
    """The first code from the app proves the secret landed. Turns MFA on and finishes the login."""
    u = _pending(db, body.mfa_token)
    _guard(db, u, request)
    logins.lock_row(db, u)
    if u.totp_enabled_at or not u.totp_secret:
        raise HTTPException(409, "Log in again to set up two-step login.")
    step = logins.totp_step(u, body.mfa_code.strip().replace(" ", ""))
    if step is None:
        _wrong_code(db, u, request)
        raise HTTPException(401, "That code is not right. Check the time on your phone and try the next one.")
    u.totp_enabled_at, u.totp_last_step = utcnow(), step
    codes = logins.new_recovery_codes(u)
    return {**_finish(u, "mfa_setup", request, resp, db), "recovery_codes": codes}


@router.post("/verify")
def verify(body: CodeIn, request: Request, resp: Response, db: Session = Depends(get_db)):
    u = _pending(db, body.mfa_token)
    _guard(db, u, request)
    logins.lock_row(db, u)
    if not u.totp_enabled_at:
        raise HTTPException(409, "Log in again to set up two-step login.")
    how = logins.verify(db, u, body.mfa_code, allow_email=True)
    if not how:
        _wrong_code(db, u, request)
        raise HTTPException(401, "That code is not right.")
    if how == "email":
        # the phone is gone: the old authenticator stops working and a new one must be set up before any session
        logins.clear_mfa(u)
        db.add(DataIssue(code="mfa_email_recovery", entity="user", entity_id=u.username[:64], tenant_id=u.tenant_id,
                         detail={"detail": f"{u.username} replaced their authenticator using an emailed code. If that wasn't them, "
                                           "switch the login off under Setup → Users."}))
        db.commit()
        record(**_base(request, u), id=event_id(request.scope), action="mfa_email_recovery", status=200)
        mail.try_send(u.email, *mail.notice(u, "Your authenticator was removed using a code sent to this address."))
        return {"ok": False, "mfa": "setup", "mfa_token": logins.pending_token(u)}
    out = _finish(u, how, request, resp, db)
    if how == "recovery" and u.email:
        left = len(u.recovery_codes or [])
        mail.try_send(u.email, *mail.notice(u, f"A recovery code was used to log in. You have {left} left."))
    return out


@router.post("/email")
def email_code(body: PendingIn, request: Request, db: Session = Depends(get_db)):
    """Lost phone: a one-time code by email. Only reachable with the right password."""
    u = _pending(db, body.mfa_token)
    _guard(db, u, request)
    if not u.totp_enabled_at or not u.email or not mail.enabled():
        raise HTTPException(409, "Email recovery isn't available for this login. Use a recovery code or ask another admin.")
    if logins.reset_by_email_recently(db, u):
        raise HTTPException(409, "Email recovery is off for 24 hours after a password reset. Use a recovery code or ask another admin.")
    if logins.sent_too_often(db, u, "mfa_email"):
        raise HTTPException(429, "Too many codes sent. Try again in an hour.")
    code = logins.issue(db, u, "mfa_email", logins.new_code())
    db.commit()
    record(**_base(request, u), id=event_id(request.scope), action="mfa_email_sent", status=200)
    if not mail.try_send(u.email, *mail.login_code(u, code)):
        raise HTTPException(502, "The email couldn't be sent. Use a recovery code or ask another admin.")
    return {"ok": True}


def _check_self(u: User, body: SelfCheckIn, request: Request, db: Session) -> None:
    """Password and a fresh code, for changes to your own second step."""
    if u.session_mode == "pin":
        raise HTTPException(403, "Log in with your password to do this.")
    if not u.totp_enabled_at:
        raise HTTPException(409, "Two-step login isn't set up.")
    _guard(db, u, request)
    logins.lock_row(db, u)
    if not auth.verify_password(body.current_password, u.password_hash):
        db.rollback()
        raise HTTPException(400, "Your current password is not right.")
    if logins.verify(db, u, body.mfa_code) is None:
        _wrong_code(db, u, request)
        raise HTTPException(400, "That code is not right.")


@router.post("/recovery-codes")
def recovery_codes(body: SelfCheckIn, request: Request, u: User = auth.Auth, db: Session = Depends(get_db)):
    """New recovery codes; the old ones stop working."""
    _check_self(u, body, request, db)
    codes = logins.new_recovery_codes(u)
    db.commit()
    return {"recovery_codes": codes}


@router.post("/reset")
def reset(body: SelfCheckIn, request: Request, resp: Response, u: User = auth.Auth, db: Session = Depends(get_db)):
    """New phone: drop this authenticator and log out everywhere. The next login sets up the new one."""
    _check_self(u, body, request, db)
    logins.clear_mfa(u)
    u.session_version += 1
    db.commit()
    auth.clear_session(resp)
    return {"ok": True}


# ---- opting in and out from My account ------------------------------------------------------------------

def _password_session(u: User) -> None:
    if u.session_mode == "pin":
        raise HTTPException(403, "Log in with your password to do this.")


@router.post("/enroll")
def enroll(body: PasswordIn, request: Request, u: User = auth.Auth, db: Session = Depends(get_db)):
    """Turn on two-step login while logged in: a new secret for the QR code. Nothing changes until a code is confirmed."""
    _password_session(u)
    if u.totp_enabled_at:
        raise HTTPException(409, "Two-step login is already on.")
    _guard(db, u, request)
    if not auth.verify_password(body.current_password, u.password_hash):
        raise HTTPException(400, "Your current password is not right.")
    out = logins.start_setup(u)
    db.commit()
    record(**_base(request, u), id=event_id(request.scope), action="mfa_setup_started", status=200)
    return out


@router.post("/enroll/confirm")
def enroll_confirm(body: EnrollCodeIn, request: Request, resp: Response, u: User = auth.Auth, db: Session = Depends(get_db)):
    """The first code proves the secret landed. Turns MFA on; this session counts as having passed it."""
    _password_session(u)
    _guard(db, u, request)
    logins.lock_row(db, u)
    if u.totp_enabled_at or not u.totp_secret:
        raise HTTPException(409, "Start the setup again.")
    step = logins.totp_step(u, body.mfa_code.strip().replace(" ", ""))
    if step is None:
        _wrong_code(db, u, request)
        raise HTTPException(400, "That code is not right. Check the time on your phone and try the next one.")
    u.totp_enabled_at, u.totp_last_step = utcnow(), step
    codes = logins.new_recovery_codes(u)
    db.commit()
    u.session_mfa = True
    auth.set_session(resp, u, request)
    if u.email:
        mail.try_send(u.email, *mail.notice(u, "Two-step login was turned on for your account."))
    return {"recovery_codes": codes, "user": me_out(u, db)}


@router.post("/disable")
def disable(body: SelfCheckIn, request: Request, resp: Response, u: User = auth.Auth, db: Session = Depends(get_db)):
    """Turn two-step login off. Not allowed when it's required of this login (see ADMIN_MFA_REQUIRED)."""
    if logins.must_enroll(u):
        raise HTTPException(409, "Admins must keep two-step login on. Use \"Move to a new phone\" to replace your authenticator.")
    _check_self(u, body, request, db)
    logins.clear_mfa(u)
    db.commit()
    u.session_mfa = False
    auth.set_session(resp, u, request)
    if u.email:
        mail.try_send(u.email, *mail.notice(u, "Two-step login was turned off for your account."))
    return {"ok": True, "user": me_out(u, db)}
