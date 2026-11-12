"""Sending email: invites, password resets and login notices.

Goes out through Mailgun's HTTP API when MAILGUN_API_KEY and MAILGUN_DOMAIN are set, otherwise over SMTP.

Links are built from PUBLIC_URL, never from the request's Host header, which a caller controls: a forged Host
would otherwise put an attacker's address into a real reset email. Tokens go after the # so they stay out of
server logs and Referer headers.
"""
import logging
import smtplib
from email.message import EmailMessage
from urllib.parse import urlsplit

import httpx

from ..config import settings
from ..models import User

log = logging.getLogger(__name__)


def _mailgun() -> bool:
    return bool(settings.mailgun_api_key and settings.mailgun_domain)


def enabled() -> bool:
    return bool((_mailgun() or settings.smtp_host) and settings.mail_from and settings.public_url)


def send(to: str, subject: str, body: str) -> None:
    if _mailgun():
        _send_mailgun(to, subject, body)
    else:
        _send_smtp(to, subject, body)


def _send_mailgun(to: str, subject: str, body: str) -> None:
    url = f"{settings.mailgun_api_base.rstrip('/')}/v3/{settings.mailgun_domain}/messages"
    r = httpx.post(url, auth=("api", settings.mailgun_api_key), timeout=15,
                   data={"from": settings.mail_from, "to": to, "subject": subject, "text": body})
    if r.status_code >= 400:
        # Mailgun's error text says why (bad key, unauthorized sandbox recipient...); it never echoes the body
        raise ValueError(f"Mailgun {r.status_code}: {r.text[:200]}")


def _send_smtp(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.mail_from, to, subject
    msg.set_content(body)
    implicit_tls = settings.smtp_port == 465
    # named here because smtplib would otherwise look it up with a reverse DNS call that ignores the timeout and can hang
    helo = urlsplit(settings.public_url).hostname or "localhost"
    with (smtplib.SMTP_SSL if implicit_tls else smtplib.SMTP)(settings.smtp_host, settings.smtp_port, local_hostname=helo, timeout=15) as s:
        if settings.smtp_tls and not implicit_tls:
            s.starttls()
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(msg)


def try_send(to: str, subject: str, body: str) -> bool:
    """False when it couldn't go out; the address and reason go to the log, never the message body (it can hold a token)."""
    try:
        send(to, subject, body)
        return True
    except (smtplib.SMTPException, httpx.HTTPError, OSError, ValueError) as e:
        log.warning("could not send %r to %s: %s", subject, to, e)
        return False


def set_password_link(token: str) -> str:
    return f"{settings.public_url.rstrip('/')}/set-password#{token}"


def invite(u: User, token: str) -> tuple[str, str]:
    return ("You're invited to Back of house",
            f"Hi {u.display_name},\n\nYou have a new Back of house login. Your username is {u.username}.\n\n"
            f"Choose your password here (the link works for 7 days, once):\n{set_password_link(token)}\n")


def reset(u: User, token: str) -> tuple[str, str]:
    return ("Reset your Back of house password",
            f"Hi {u.display_name},\n\nSomeone asked to reset the password for {u.username}.\n\n"
            f"Choose a new one here (the link works for 1 hour, once):\n{set_password_link(token)}\n\n"
            "If this wasn't you, ignore this email. Your password stays the same.\n")


def login_code(u: User, code: str) -> tuple[str, str]:
    return ("Your Back of house recovery code",
            f"Hi {u.display_name},\n\nYour recovery code is {code}\n\nIt works for 10 minutes, once, and you'll set up your "
            "authenticator app again straight after.\n\nIf this wasn't you, someone has your password: change it now and tell another admin.\n")


def notice(u: User, what: str) -> tuple[str, str]:
    return ("Back of house security notice",
            f"Hi {u.display_name},\n\n{what}\n\nIf this wasn't you, change your password now and tell an admin.\n")
