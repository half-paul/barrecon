"""Logins, sessions and per-area permissions.

The session cookie carries the user id, their session_version, how the session was opened (password or PIN)
and when. Every request reloads the user, so a permission change applies at once and a deactivation or
password reset logs them out everywhere. The cookie is re-signed as the person works; one older than the
idle timeout (setting idle_logout_minutes) is refused, so a walked-away-from bar PC logs itself out.

Failed logins are counted from the audit log: too many for one username or from one IP within LOCK_WINDOW
blocks further tries for a while, and a run of failed PINs switches PIN login off until a password login.
"""
import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import AuditEvent, DataIssue, User, utcnow
from .services import settings_svc

COOKIE = "barrecon_session"
_ser = URLSafeTimedSerializer(settings.session_secret, salt="session")
REFRESH_AFTER_S = 60  # re-sign the cookie at most this often

# Each area of the portal and the levels it can be given. "edit" includes "view".
AREAS: dict[str, tuple[str, tuple[str, ...]]] = {
    "counts": ("Counts", ("view", "edit")),
    "deliveries": ("Deliveries and stock movements", ("view", "edit")),
    "sales": ("Swype sales", ("view", "edit")),
    "variance": ("Variance", ("view",)),
    "items": ("Stock items and groups", ("view", "edit")),
    "swype_items": ("Swype items", ("view", "edit")),
    "recipes": ("Recipes", ("view", "edit")),
    "imports": ("Imports and data issues", ("view", "edit")),
    "settings": ("Settings", ("edit",)),  # anyone logged in can read them
    "audit": ("Audit log", ("view",)),
}
PIN_AREAS = ("counts", "deliveries")  # all a PIN session can reach, whatever the user's own permissions
READ_METHODS = ("GET", "HEAD", "OPTIONS")

LOCK_WINDOW = timedelta(minutes=15)
LOCK_USER = 5  # failed logins for one username in the window
LOCK_IP = 20  # failed logins from one address in the window
PIN_LIMIT = 10  # failed PINs since the last good login: PIN login is off until a password login or an unlock
FAIL_DELAY_S = 0.25  # per earlier failure, capped at 1 s; slows guessing

# ---- passwords and PINs -----------------------------------------------------------------------------------

_N, _R, _P = 2**14, 8, 1
COMMON = set("""password password1 password12 password123 passw0rd 12345678 123456789 1234567890 11111111 00000000
12341234 87654321 qwertyui qwertyuiop qwerty123 asdfghjk iloveyou sunshine princess football baseball welcome
welcome1 letmein1 trustno1 superman batman123 abc12345 abcd1234 changeme change-me admin123 administrator
monkey123 dragon123 master123 whatever starwars computer internet blink182 liverpool chelsea1 hockey12 canucks1
barstock bartender cocktail""".split())
_SEQ = "0123456789012345678909876543210"


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str | None) -> bool:
    try:
        algo, n, r, p, salt, h = (stored or "").split("$")
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
    return hmac.compare_digest(got, bytes.fromhex(h))


_DUMMY = hash_password(secrets.token_hex(8))


def password_problem(pw: str, username: str = "", display_name: str = "") -> str | None:
    low = pw.lower()
    if len(pw) < 8:
        return "Use at least 8 characters."
    if low in COMMON or len(set(low)) <= 2:
        return "That password is too common or too simple. Pick something harder to guess."
    for name in (username, *display_name.split()):
        if len(name) >= 3 and name.lower() in low:
            return "The password can't contain the username or name."
    return None


def pin_problem(pin: str) -> str | None:
    if not re.fullmatch(r"\d{4,8}", pin):
        return "A PIN is 4 to 8 digits."
    if len(set(pin)) == 1 or pin in _SEQ:
        return "That PIN is too easy to guess (repeated or in sequence)."
    return None


def check_password(pw: str, username: str = "", display_name: str = "") -> None:
    if msg := password_problem(pw, username, display_name):
        raise HTTPException(422, msg)


def check_pin(pin: str) -> None:
    if msg := pin_problem(pin):
        raise HTTPException(422, msg)


# ---- login lockout ----------------------------------------------------------------------------------------

def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


UNLOCK_ROUTE = "/api/users/{user_id}/unlock"


def _last_good(db: Session, username: str) -> datetime | None:
    """The last successful login, or an admin's unlock: failures before it no longer count."""
    u = db.query(User).filter(User.username == username).one_or_none()
    cond = (AuditEvent.action == "login") & (AuditEvent.username == username)
    if u:
        cond = cond | ((AuditEvent.route == UNLOCK_ROUTE) & (AuditEvent.path == f"/api/users/{u.id}/unlock") & (AuditEvent.status == 200))
    row = db.query(AuditEvent.at).filter(cond).order_by(AuditEvent.at.desc()).first()
    return _utc(row.at) if row else None


def _failures(db: Session, since: datetime, username: str | None = None, ip: str | None = None, limit: int = 50) -> list[AuditEvent]:
    q = db.query(AuditEvent).filter(AuditEvent.action == "login_failed", AuditEvent.at >= since)
    q = q.filter(AuditEvent.username == username) if username is not None else q.filter(AuditEvent.ip == ip)
    return q.order_by(AuditEvent.at.desc()).limit(limit).all()


def lock_status(db: Session, username: str, ip: str | None) -> tuple[int, int]:
    """(seconds until this username/address may try again, failures for the username in the window)."""
    now = utcnow()
    since = max(now - LOCK_WINDOW, _last_good(db, username) or now - LOCK_WINDOW)
    by_user = _failures(db, since, username=username, limit=LOCK_USER)
    wait = 0
    if len(by_user) >= LOCK_USER:
        wait = (_utc(by_user[-1].at) + LOCK_WINDOW - now).total_seconds()
    if ip:
        by_ip = _failures(db, now - LOCK_WINDOW, ip=ip, limit=LOCK_IP)
        if len(by_ip) >= LOCK_IP:
            wait = max(wait, (_utc(by_ip[-1].at) + LOCK_WINDOW - now).total_seconds())
    return max(0, int(wait + 0.999)), len(by_user)


def pin_locked(db: Session, username: str) -> bool:
    since = _last_good(db, username) or datetime(2000, 1, 1, tzinfo=timezone.utc)
    pins = [e for e in _failures(db, since, username=username, limit=500) if (e.detail or {}).get("method") == "pin"]
    return len(pins) >= PIN_LIMIT


def is_locked(db: Session, u: User) -> bool:
    return lock_status(db, u.username, None)[0] > 0


def raise_lock_issue(db: Session, username: str, ip: str | None) -> None:
    """One "To review" item per locked username per window, so it shows on the Overview."""
    recent = db.query(DataIssue).filter(DataIssue.code == "login_locked", DataIssue.entity_id == username,
                                        DataIssue.created_at >= utcnow() - LOCK_WINDOW).first()
    if not recent:
        db.add(DataIssue(code="login_locked", entity="user", entity_id=username[:64],
                         detail={"detail": f"Login for {username} blocked after {LOCK_USER} wrong tries (last from {ip or 'unknown'}). "
                                           "If that wasn't them, change their password under Setup → Users."}))
        db.commit()


def fail_delay(failures: int) -> None:
    if FAIL_DELAY_S:
        time.sleep(min(1.0, FAIL_DELAY_S * failures))


def authenticate(db: Session, username: str, pw: str | None = None, pin: str | None = None) -> User | None:
    u = db.query(User).filter(User.username == username.strip().lower()).one_or_none()
    secret, stored = (pin, u.pin_hash if u else None) if pin is not None else (pw or "", u.password_hash if u else None)
    if not u or not stored:
        verify_password(secret or "", _DUMMY)  # same time as a real check, so usernames can't be probed
        return None
    return u if verify_password(secret or "", stored) and u.is_active else None


# ---- sessions ---------------------------------------------------------------------------------------------

def idle_seconds(db: Session) -> int:
    m = int(settings_svc.get(db, "idle_logout_minutes") or 0)
    return m * 60 if m > 0 else settings.session_max_age_seconds


def _secure(request: Request | None) -> bool:
    if settings.cookie_secure or request is None:
        return settings.cookie_secure
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto.split(",")[0].strip() == "https"


def _token(user: User, mode: str, started: int) -> str:
    return _ser.dumps({"u": user.id, "v": user.session_version, "m": mode, "s": started})


def set_session(resp: Response, user: User, request: Request | None = None, mode: str = "password", started: int | None = None) -> None:
    resp.set_cookie(COOKIE, _token(user, mode, started or int(time.time())), max_age=settings.session_max_age_seconds,
                    httponly=True, samesite="lax", secure=_secure(request))


def clear_session(resp: Response) -> None:
    resp.delete_cookie(COOKIE)


def level(user: User, area: str) -> str | None:
    """'edit', 'view' or None."""
    pin = user.session_mode == "pin"
    if pin and area not in PIN_AREAS:
        return None
    if user.is_admin:
        return AREAS[area][1][-1]
    lv = (user.permissions or {}).get(area)
    return lv if lv in AREAS[area][1] else None


def is_admin(user: User) -> bool:
    """Admin powers need a password session; a PIN never gives them."""
    return user.is_admin and user.session_mode != "pin"


def can(user: User, area: str, need: str = "view") -> bool:
    lv = level(user, area)
    return lv == "edit" or (lv == "view" and need == "view")


def effective_permissions(user: User) -> dict[str, str]:
    return {a: lv for a in AREAS if (lv := level(user, a))}


def _holder(request: Request) -> dict | None:
    return request.scope.get("state", {}).get("audit")


def note(request: Request, user: User | None = None, area: str | None = None) -> None:
    """Tell app.audit who is making this request and in which area."""
    holder = _holder(request)
    if holder is None:
        return
    if user:
        holder["user_id"], holder["username"] = user.id, user.username
    if area:
        holder["area"] = area


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    tok = request.cookies.get(COOKIE)
    if not tok:
        raise HTTPException(401, "not logged in")
    try:
        data, signed = _ser.loads(tok, max_age=idle_seconds(db), return_timestamp=True)
    except SignatureExpired:
        raise HTTPException(401, "logged out after being idle") from None
    except BadSignature:
        raise HTTPException(401, "session expired") from None
    if not isinstance(data, dict):
        raise HTTPException(401, "session expired")
    started = int(data.get("s") or signed.timestamp())
    if time.time() - started > settings.session_max_age_seconds:
        raise HTTPException(401, "session expired")
    u = db.get(User, data.get("u"))
    if not u or not u.is_active or u.session_version != data.get("v"):
        raise HTTPException(401, "session expired")
    u.session_mode = data.get("m") or "password"
    note(request, u)
    holder = _holder(request)
    if holder is not None and time.time() - signed.timestamp() > REFRESH_AFTER_S:
        r = Response()
        set_session(r, u, request, u.session_mode, started)
        holder["set_cookie"] = (f"{COOKIE}=".encode(), next(v for k, v in r.raw_headers if k == b"set-cookie"))
    return u


def require(request: Request, user: User, area: str, need: str) -> None:
    note(request, area=area)
    if not can(user, area, need):
        label = AREAS[area][0]
        if user.session_mode == "pin" and area not in PIN_AREAS:
            raise HTTPException(403, f"A PIN login can't open {label}. Log in with your password.")
        raise HTTPException(403, f"You don't have access to {label}." if need == "view" or not level(user, area)
                            else f"You can view {label} but not change it.")


def perm(area: str):
    """Router dependency: reads need view, anything else needs edit on `area`."""
    def check(request: Request, user: User = Depends(current_user)) -> User:
        require(request, user, area, "view" if request.method in READ_METHODS else "edit")
        return user
    return Depends(check)


def require_admin(request: Request, user: User = Depends(current_user)) -> User:
    note(request, area="users")
    if not is_admin(user):
        raise HTTPException(403, "Only an admin can manage users." if user.session_mode != "pin"
                            else "Log in with your password to manage users.")
    return user


Auth = Depends(current_user)  # logged in, any permissions
Admin = Depends(require_admin)
