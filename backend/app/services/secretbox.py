"""Encrypts small secrets kept in the database (a business's Swype password), keyed from SESSION_SECRET, so a
database backup alone doesn't give them. Changing SESSION_SECRET means entering them again."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from ..config import settings


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(f"box:{settings.session_secret}".encode()).digest()))


def seal(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def open_(value: str | None) -> str | None:
    """None when there is nothing stored or it can't be read (the key changed)."""
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return None
