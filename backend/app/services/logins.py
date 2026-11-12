"""The optional second login step (required of admins with ADMIN_MFA_REQUIRED), and the one-time tokens sent by email.

A correct password on a login with MFA on gets a short-lived pending token, not a session. The session cookie is only
set once an authenticator code or recovery code checks out, or once a new authenticator is confirmed.

Authenticator secrets are encrypted and codes are keyed hashes, both from SESSION_SECRET, so a database backup alone
gives neither. Changing SESSION_SECRET therefore means every admin sets up MFA again (see ADMIN_MFA_REQUIRED).
"""
import base64
import hashlib
import hmac
import re
import secrets
import time
from datetime import timedelta

import pyotp
import segno
from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from ..auth import _utc
from ..config import settings
from ..models import AuthToken, User, utcnow

PENDING_MAX_AGE_S = 600  # from the password to the code
RECOVERY_COUNT = 10
EMAILS_PER_HOUR = 3  # per person and kind
RESET_HOLD = timedelta(hours=24)  # no MFA recovery by email this long after a password reset by email
TTL = {"invite": timedelta(days=7), "reset": timedelta(hours=1), "mfa_email": timedelta(minutes=10)}
ISSUER = "Back of house"
ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"  # no 0/O, 1/I/L, U: read off paper

_pending = URLSafeTimedSerializer(settings.session_secret, salt="mfa-pending")


def must_enroll(u: User) -> bool:
    """Admins can be made to use two-step login (ADMIN_MFA_REQUIRED); for everyone else it's opt-in."""
    return settings.admin_mfa_required and (u.is_admin or u.is_platform_admin)


def mfa_required(u: User, method: str) -> bool:
    """A password login needs the second step once someone has turned it on, or when it's required of them."""
    return method == "password" and (bool(u.totp_enabled_at) or must_enroll(u))


def keyed(value: str) -> str:
    return hmac.new(settings.session_secret.encode(), f"login-code:{value}".encode(), hashlib.sha256).hexdigest()


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(f"totp:{settings.session_secret}".encode()).digest()))


# ---- the step between password and session ----------------------------------------------------------------

def pending_token(u: User) -> str:
    return _pending.dumps({"u": u.id, "v": u.session_version})


def pending_user(db: Session, token: str) -> User | None:
    try:
        data = _pending.loads(token, max_age=PENDING_MAX_AGE_S)
    except BadSignature:
        return None
    u = db.get(User, data.get("u"))
    return u if u and u.is_active and u.session_version == data.get("v") else None


def lock_row(db: Session, u: User) -> None:
    """Serialise code checks so one code can't be spent twice. key_share: audit rows pointing at this user must not wait on it."""
    db.refresh(u, with_for_update={"key_share": True})


# ---- authenticator and recovery codes ---------------------------------------------------------------------

def start_setup(u: User) -> dict:
    secret = pyotp.random_base32()
    u.totp_secret = _fernet().encrypt(secret.encode()).decode()
    u.totp_enabled_at = None
    uri = pyotp.TOTP(secret).provisioning_uri(name=u.username, issuer_name=ISSUER)
    return {"secret": secret, "qr": segno.make(uri, error="m").svg_data_uri(scale=5, light="white")}


def totp_step(u: User, code: str) -> int | None:
    """The 30-second step this code belongs to, one step of clock drift allowed. Steps already used don't match."""
    try:
        totp = pyotp.TOTP(_fernet().decrypt((u.totp_secret or "").encode()).decode())
    except InvalidToken:
        return None
    now = int(time.time()) // 30
    for step in (now - 1, now, now + 1):
        if step > (u.totp_last_step or 0) and hmac.compare_digest(totp.at(step * 30), code):
            return step
    return None


def _normal(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", code.upper())


def new_code() -> str:
    c = "".join(secrets.choice(ALPHABET) for _ in range(10))
    return f"{c[:5]}-{c[5:]}"


def new_recovery_codes(u: User) -> list[str]:
    codes = [new_code() for _ in range(RECOVERY_COUNT)]
    u.recovery_codes = [keyed(_normal(c)) for c in codes]
    return codes


def verify(db: Session, u: User, code: str, allow_email: bool = False) -> str | None:
    """How the code checked out ("totp", "recovery", "email"), spending it; None if it didn't. The caller commits."""
    code = code.strip().replace(" ", "")
    if re.fullmatch(r"\d{6}", code):
        step = totp_step(u, code) if u.totp_enabled_at else None
        if step is None:
            return None
        u.totp_last_step = step
        return "totp"
    h = keyed(_normal(code))
    if h in (u.recovery_codes or []):
        u.recovery_codes = [c for c in u.recovery_codes if c != h]
        return "recovery"
    if allow_email:
        row = find(db, h, ("mfa_email",), hashed=True)
        if row and row.user_id == u.id and consume(db, row):
            return "email"
    return None


def clear_mfa(u: User) -> None:
    u.totp_secret = u.totp_enabled_at = u.totp_last_step = u.recovery_codes = None


# ---- one-time tokens --------------------------------------------------------------------------------------

def issue(db: Session, u: User, kind: str, value: str | None = None) -> str:
    """Stores a keyed hash only. mfa_email passes the code it will send; links get a fresh random token."""
    value = value or secrets.token_urlsafe(32)
    h = keyed(_normal(value)) if kind == "mfa_email" else keyed(value)
    db.add(AuthToken(user_id=u.id, kind=kind, token_hash=h, expires_at=utcnow() + TTL[kind]))
    return value


def find(db: Session, value: str, kinds: tuple[str, ...], hashed: bool = False) -> AuthToken | None:
    h = value if hashed else keyed(value)
    row = db.query(AuthToken).filter(AuthToken.token_hash == h, AuthToken.kind.in_(kinds), AuthToken.used_at.is_(None)).one_or_none()
    return row if row and _utc(row.expires_at) > utcnow() else None


def consume(db: Session, row: AuthToken) -> bool:
    """Atomic: of two racing uses, only one gets True."""
    n = db.query(AuthToken).filter(AuthToken.id == row.id, AuthToken.used_at.is_(None)).update({"used_at": utcnow()}, synchronize_session=False)
    return n == 1


def revoke(db: Session, u: User, *kinds: str) -> None:
    db.query(AuthToken).filter(AuthToken.user_id == u.id, AuthToken.kind.in_(kinds), AuthToken.used_at.is_(None)).delete(synchronize_session=False)


def sent_too_often(db: Session, u: User, kind: str) -> bool:
    n = db.query(AuthToken).filter(AuthToken.user_id == u.id, AuthToken.kind == kind, AuthToken.created_at >= utcnow() - timedelta(hours=1)).count()
    return n >= EMAILS_PER_HOUR


def reset_by_email_recently(db: Session, u: User) -> bool:
    """Whoever holds the inbox can reset the password. This hold stops the same inbox also replacing the authenticator at once."""
    return db.query(AuthToken).filter(AuthToken.user_id == u.id, AuthToken.kind == "reset", AuthToken.used_at >= utcnow() - RESET_HOLD).count() > 0
