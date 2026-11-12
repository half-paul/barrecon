"""Admin MFA, recovery, password reset by email and invites."""
import re
import time
from datetime import timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient

from app import auth
from app.config import settings
from app.main import app
from app.models import AuditEvent, AuthToken, DataIssue, User, utcnow
from app.services import logins, mail

PW = "test-pass"


@pytest.fixture()
def mfa_on(monkeypatch):
    monkeypatch.setattr(settings, "admin_mfa_required", True)
    monkeypatch.setattr(auth, "FAIL_DELAY_S", 0)


@pytest.fixture()
def mailbox(monkeypatch):
    sent = []
    monkeypatch.setattr(settings, "smtp_host", "smtp.test")
    monkeypatch.setattr(settings, "mail_from", "bar@test.example")
    monkeypatch.setattr(settings, "public_url", "https://bar.test.example/")
    monkeypatch.setattr(mail, "send", lambda to, subject, body: sent.append({"to": to, "subject": subject, "body": body}))
    return sent


def code(secret: str, ahead: int = 0) -> str:
    """A valid code for a step nothing has used yet."""
    return pyotp.TOTP(secret).at((int(time.time()) // 30 + ahead) * 30)


def login(c: TestClient, username="admin", password=PW):
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def enroll(username="admin", password=PW):
    """Log in through first-time setup. Returns the session client, the secret and the recovery codes."""
    c = TestClient(app)
    p = login(c, username, password)
    assert p["mfa"] == "setup"
    s = c.post("/api/auth/mfa/setup", json={"mfa_token": p["mfa_token"]}).json()
    r = c.post("/api/auth/mfa/confirm", json={"mfa_token": p["mfa_token"], "mfa_code": code(s["secret"])})
    assert r.status_code == 200, r.text
    return c, s["secret"], r.json()["recovery_codes"]


def verify(c, token, value):
    return c.post("/api/auth/mfa/verify", json={"mfa_token": token, "mfa_code": value})


def link_token(message: dict) -> str:
    return re.search(r"/set-password#(\S+)", message["body"]).group(1)


def make_user(admin, mailbox, username="sam", **kw):
    r = admin.post("/api/users", json={"username": username, "display_name": username.title(), "email": f"{username}@test.example", "permissions": {"counts": "edit"}, **kw})
    assert r.status_code == 201, r.text
    return r.json()


# ---- MFA --------------------------------------------------------------------------------------------------

def test_password_alone_gives_no_session(db, mfa_on):
    c = TestClient(app)
    p = login(c)
    assert p["ok"] is False and p["mfa"] == "setup" and "barrecon_session" not in c.cookies
    assert c.get("/api/users").status_code == 401
    assert db.query(AuditEvent).filter_by(action="login").count() == 0


def test_setup_turns_on_mfa_and_stores_nothing_readable(db, mfa_on):
    c, secret, codes = enroll()
    assert len(codes) == 10 and c.get("/api/users").status_code == 200
    me = c.get("/api/auth/me").json()["user"]
    assert me["mfa_enabled"] and me["recovery_codes_left"] == 10
    u = db.query(User).filter_by(username="admin").one()
    db.refresh(u)
    assert secret not in u.totp_secret
    assert not set(codes) & set(u.recovery_codes)


def test_setup_gives_a_scannable_qr(db, mfa_on):
    c = TestClient(app)
    p = login(c)
    s = c.post("/api/auth/mfa/setup", json={"mfa_token": p["mfa_token"]}).json()
    assert s["qr"].startswith("data:image/svg+xml") and len(s["secret"]) >= 16


def test_setup_is_refused_once_enrolled(db, mfa_on):
    enroll()
    c = TestClient(app)
    p = login(c)
    assert p["mfa"] == "verify"
    assert c.post("/api/auth/mfa/setup", json={"mfa_token": p["mfa_token"]}).status_code == 409


def test_code_works_once(db, mfa_on):
    _, secret, _ = enroll()
    c = TestClient(app)
    value = code(secret, 1)
    assert verify(c, login(c)["mfa_token"], value).status_code == 200
    assert c.get("/api/users").status_code == 200
    c2 = TestClient(app)
    assert verify(c2, login(c2)["mfa_token"], value).status_code == 401


def test_forged_pending_token_refused(db, mfa_on):
    c, secret, _ = enroll()
    assert verify(TestClient(app), "made-up", code(secret, 1)).status_code == 401
    session_cookie = c.cookies["barrecon_session"]  # a session token isn't a pending token
    assert verify(TestClient(app), session_cookie, code(secret, 1)).status_code == 401


def test_pending_token_dies_with_session_version(db, mfa_on):
    _, secret, _ = enroll()
    c = TestClient(app)
    token = login(c)["mfa_token"]
    u = db.query(User).filter_by(username="admin").one()
    db.refresh(u)
    u.session_version += 1
    db.commit()
    assert verify(c, token, code(secret, 1)).status_code == 401


def test_wrong_codes_lock_and_a_right_password_doesnt_clear_it(db, mfa_on):
    _, secret, _ = enroll()
    c = TestClient(app)
    for _ in range(auth.LOCK_USER):
        token = login(c)["mfa_token"]  # the password keeps being right
        assert verify(c, token, "000000").status_code == 401
    assert verify(c, token, code(secret, 1)).status_code == 429
    assert c.post("/api/auth/login", json={"username": "admin", "password": PW}).status_code == 429
    assert db.query(DataIssue).filter_by(code="login_locked").count() == 1


def test_recovery_code_works_once(db, mfa_on, mailbox):
    _, _, codes = enroll()
    c = TestClient(app)
    assert verify(c, login(c)["mfa_token"], codes[0].lower().replace("-", " ")).status_code == 200
    assert c.get("/api/auth/me").json()["user"]["recovery_codes_left"] == 9
    c2 = TestClient(app)
    assert verify(c2, login(c2)["mfa_token"], codes[0]).status_code == 401


def test_old_session_without_mfa_ends_when_required(db, monkeypatch):
    c = TestClient(app)
    assert login(c)["ok"] is True  # before MFA was switched on
    assert c.get("/api/users").status_code == 200
    monkeypatch.setattr(settings, "admin_mfa_required", True)
    assert c.get("/api/users").status_code == 401


def test_promoted_user_must_set_up_mfa(db, mfa_on):
    admin, _, _ = enroll()
    r = admin.post("/api/users", json={"username": "kit", "display_name": "Kit", "password": "pass-word-1"})
    kit = TestClient(app)
    assert login(kit, "kit", "pass-word-1")["ok"] is True  # not an admin: no second step
    assert admin.patch(f"/api/users/{r.json()['id']}", json={"is_admin": True}).status_code == 200
    assert kit.get("/api/auth/me").json()["user"] is None
    assert login(kit, "kit", "pass-word-1")["mfa"] == "setup"


def test_pin_login_skips_mfa_but_stays_limited(db, mfa_on):
    admin, _, _ = enroll()
    me = admin.get("/api/auth/me").json()["user"]
    assert admin.post(f"/api/users/{me['id']}/pin", json={"pin": "4827"}).status_code == 200
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": "admin", "pin": "4827"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert c.get("/api/counts").status_code == 200
    assert c.get("/api/users").status_code == 403
    assert c.get("/api/items").status_code == 403


def test_codes_stay_out_of_the_audit_log(db, mfa_on):
    admin, secret, _ = enroll()
    value = code(secret, 1)
    r = admin.post("/api/auth/mfa/recovery-codes", json={"current_password": PW, "mfa_code": value})
    assert r.status_code == 200 and len(r.json()["recovery_codes"]) == 10
    ev = db.query(AuditEvent).filter(AuditEvent.route == "/api/auth/mfa/recovery-codes").one()
    assert ev.detail["body"] == {"current_password": "***", "mfa_code": "***"}
    assert admin.get("/api/audit/verify").json()["ok"]


def test_new_recovery_codes_replace_old(db, mfa_on):
    admin, secret, old = enroll()
    new = admin.post("/api/auth/mfa/recovery-codes", json={"current_password": PW, "mfa_code": code(secret, 1)}).json()["recovery_codes"]
    c = TestClient(app)
    assert verify(c, login(c)["mfa_token"], old[0]).status_code == 401
    assert verify(c, login(c)["mfa_token"], new[0]).status_code == 200


def test_changing_own_second_step_needs_password_and_code(db, mfa_on):
    admin, secret, _ = enroll()
    assert admin.post("/api/auth/mfa/reset", json={"current_password": "wrong", "mfa_code": code(secret, 1)}).status_code == 400
    assert admin.post("/api/auth/mfa/reset", json={"current_password": PW, "mfa_code": "000000"}).status_code == 400
    assert admin.post("/api/auth/mfa/reset", json={"current_password": PW, "mfa_code": code(secret, 1)}).status_code == 200
    assert admin.get("/api/users").status_code == 401
    assert login(TestClient(app))["mfa"] == "setup"


def test_admin_resets_someone_elses_mfa_not_their_own(db, mfa_on, mailbox):
    admin, _, _ = enroll()
    other = make_user(admin, mailbox, "pat", is_admin=True)
    c = TestClient(app)
    c.post("/api/auth/set-password", json={"token": link_token(mailbox[-1]), "password": "pass-word-1"})
    pat, _, _ = enroll(username="pat", password="pass-word-1")
    assert admin.post(f"/api/users/{other['id']}/mfa/reset").status_code == 200
    assert pat.get("/api/users").status_code == 401
    assert login(TestClient(app), "pat", "pass-word-1")["mfa"] == "setup"
    me = admin.get("/api/auth/me").json()["user"]
    assert admin.post(f"/api/users/{me['id']}/mfa/reset").status_code == 409


# ---- MFA recovery by email --------------------------------------------------------------------------------

def _with_email(admin, email="boss@test.example"):
    me = admin.get("/api/auth/me").json()["user"]
    assert admin.patch(f"/api/users/{me['id']}", json={"email": email}).status_code == 200


def _emailed_code(message: dict) -> str:
    return re.search(r"recovery code is (\S+)", message["body"]).group(1)


def test_email_recovery_replaces_the_authenticator(db, mfa_on, mailbox):
    admin, secret, codes = enroll()
    _with_email(admin)
    c = TestClient(app)
    p = login(c)
    assert p["email_recovery"] is True
    assert c.post("/api/auth/mfa/email", json={"mfa_token": p["mfa_token"]}).status_code == 200
    r = verify(c, p["mfa_token"], _emailed_code(mailbox[-1]))
    assert r.status_code == 200 and r.json()["mfa"] == "setup"
    assert "barrecon_session" not in c.cookies  # still no session until the new authenticator is confirmed
    assert db.query(DataIssue).filter_by(code="mfa_email_recovery").count() == 1
    assert "removed" in mailbox[-1]["body"]
    c2 = TestClient(app)
    assert login(c2)["mfa"] == "setup"
    new, _, _ = enroll()
    assert new.get("/api/users").status_code == 200


def test_email_recovery_needs_the_password(db, mfa_on, mailbox):
    admin, _, _ = enroll()
    _with_email(admin)
    assert TestClient(app).post("/api/auth/mfa/email", json={"mfa_token": "made-up"}).status_code == 401
    assert not [m for m in mailbox if "recovery code" in m["body"]]


def test_emailed_code_expires_and_belongs_to_one_person(db, mfa_on, mailbox):
    admin, _, _ = enroll()
    _with_email(admin)
    c = TestClient(app)
    p = login(c)
    c.post("/api/auth/mfa/email", json={"mfa_token": p["mfa_token"]})
    row = db.query(AuthToken).filter_by(kind="mfa_email").one()
    row.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert verify(c, p["mfa_token"], _emailed_code(mailbox[-1])).status_code == 401


def test_emailed_code_cant_change_second_step_from_account(db, mfa_on, mailbox):
    admin, _, _ = enroll()
    _with_email(admin)
    c = TestClient(app)
    c.post("/api/auth/mfa/email", json={"mfa_token": login(c)["mfa_token"]})
    r = admin.post("/api/auth/mfa/reset", json={"current_password": PW, "mfa_code": _emailed_code(mailbox[-1])})
    assert r.status_code == 400


def test_inbox_alone_cant_take_an_admin_login(db, mfa_on, mailbox):
    """Reset the password by email, then try to replace the authenticator by email: held for 24 hours."""
    admin, _, _ = enroll()
    _with_email(admin)
    attacker = TestClient(app)
    attacker.post("/api/auth/forgot", json={"login": "boss@test.example"})
    r = attacker.post("/api/auth/set-password", json={"token": link_token(mailbox[-1]), "password": "taken-over-77"})
    assert r.status_code == 200
    p = login(attacker, password="taken-over-77")
    assert p["mfa"] == "verify" and "barrecon_session" not in attacker.cookies
    r = attacker.post("/api/auth/mfa/email", json={"mfa_token": p["mfa_token"]})
    assert r.status_code == 409 and "24 hours" in r.json()["detail"]


def test_email_codes_are_rate_limited(db, mfa_on, mailbox):
    admin, _, _ = enroll()
    _with_email(admin)
    c = TestClient(app)
    token = login(c)["mfa_token"]
    for _ in range(logins.EMAILS_PER_HOUR):
        assert c.post("/api/auth/mfa/email", json={"mfa_token": token}).status_code == 200
    assert c.post("/api/auth/mfa/email", json={"mfa_token": token}).status_code == 429


# ---- password reset by email ------------------------------------------------------------------------------

def _user_with_password(client, mailbox, name="sam", pw="pass-word-1"):
    u = make_user(client, mailbox, name)
    TestClient(app).post("/api/auth/set-password", json={"token": link_token(mailbox[-1]), "password": pw})
    return u


def test_forgot_answers_the_same_for_everyone(client, mailbox):
    _user_with_password(client, mailbox)
    mailbox.clear()
    c = TestClient(app)
    known = c.post("/api/auth/forgot", json={"login": "sam"})
    unknown = c.post("/api/auth/forgot", json={"login": "nobody"})
    assert known.status_code == unknown.status_code == 200 and known.json() == unknown.json()
    assert len(mailbox) == 1 and mailbox[0]["to"] == "sam@test.example"


def test_reset_by_username_or_email_changes_password_and_ends_sessions(client, mailbox):
    _user_with_password(client, mailbox)
    sam = TestClient(app)
    login(sam, "sam", "pass-word-1")
    c = TestClient(app)
    c.post("/api/auth/forgot", json={"login": "SAM@test.example"})
    token = link_token(mailbox[-1])
    assert c.post("/api/auth/link", json={"token": token}).json() == {"kind": "reset", "username": "sam", "display_name": "Sam"}
    assert c.post("/api/auth/set-password", json={"token": token, "password": "brand-new-44"}).status_code == 200
    assert "barrecon_session" not in c.cookies  # it never logs anyone in
    assert sam.get("/api/auth/me").json()["user"] is None
    assert login(TestClient(app), "sam", "brand-new-44")["ok"] is True
    assert "just changed" in mailbox[-1]["body"]


def test_link_works_once_and_weak_password_doesnt_spend_it(client, mailbox):
    _user_with_password(client, mailbox)
    c = TestClient(app)
    c.post("/api/auth/forgot", json={"login": "sam"})
    token = link_token(mailbox[-1])
    assert c.post("/api/auth/set-password", json={"token": token, "password": "password123"}).status_code == 422
    assert c.post("/api/auth/set-password", json={"token": token, "password": "brand-new-44"}).status_code == 200
    assert c.post("/api/auth/set-password", json={"token": token, "password": "another-one-55"}).status_code == 410


def test_expired_link_refused(client, mailbox, db):
    _user_with_password(client, mailbox)
    c = TestClient(app)
    c.post("/api/auth/forgot", json={"login": "sam"})
    token = link_token(mailbox[-1])
    row = db.query(AuthToken).filter_by(kind="reset").one()
    row.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert c.post("/api/auth/link", json={"token": token}).status_code == 410
    assert c.post("/api/auth/set-password", json={"token": token, "password": "brand-new-44"}).status_code == 410


def test_link_ignores_a_forged_host_header(client, mailbox):
    _user_with_password(client, mailbox)
    TestClient(app).post("/api/auth/forgot", json={"login": "sam"}, headers={"Host": "evil.example"})
    assert "evil.example" not in mailbox[-1]["body"]
    assert "https://bar.test.example/set-password#" in mailbox[-1]["body"]


def test_tokens_stored_hashed(client, mailbox, db):
    make_user(client, mailbox)
    token = link_token(mailbox[-1])
    assert token not in [t.token_hash for t in db.query(AuthToken).all()]


def test_reset_requests_are_rate_limited(client, mailbox):
    _user_with_password(client, mailbox)
    mailbox.clear()
    c = TestClient(app)
    for _ in range(logins.EMAILS_PER_HOUR + 2):
        assert c.post("/api/auth/forgot", json={"login": "sam"}).status_code == 200
    assert len(mailbox) == logins.EMAILS_PER_HOUR


def test_switched_off_user_gets_no_reset_and_old_link_dies(client, mailbox):
    u = _user_with_password(client, mailbox)
    c = TestClient(app)
    c.post("/api/auth/forgot", json={"login": "sam"})
    token = link_token(mailbox[-1])
    client.patch(f"/api/users/{u['id']}", json={"is_active": False})
    assert c.post("/api/auth/set-password", json={"token": token, "password": "brand-new-44"}).status_code == 410
    mailbox.clear()
    c.post("/api/auth/forgot", json={"login": "sam"})
    assert mailbox == []


def test_changing_email_kills_links_sent_to_the_old_one(client, mailbox):
    u = _user_with_password(client, mailbox)
    c = TestClient(app)
    c.post("/api/auth/forgot", json={"login": "sam"})
    token = link_token(mailbox[-1])
    assert client.patch(f"/api/users/{u['id']}", json={"email": "new@test.example"}).status_code == 200
    assert c.post("/api/auth/link", json={"token": token}).status_code == 410


def test_no_email_setup_sends_nothing(client):
    assert TestClient(app).get("/api/auth/options").json() == {"email_enabled": False}
    assert TestClient(app).post("/api/auth/forgot", json={"login": "admin"}).status_code == 200


# ---- invites ----------------------------------------------------------------------------------------------

def test_invite_email_then_choose_password(client, mailbox, db):
    u = make_user(client, mailbox)
    assert u["invite_pending"] and u["invite_sent"] is True and u["email"] == "sam@test.example"
    assert mailbox[-1]["to"] == "sam@test.example" and "sam" in mailbox[-1]["body"]
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "sam", "password": auth.INVITED}).status_code == 401
    token = link_token(mailbox[-1])
    assert c.post("/api/auth/link", json={"token": token}).json()["kind"] == "invite"
    r = c.post("/api/auth/set-password", json={"token": token, "password": "pass-word-1", "display_name": "Sam Lee"})
    assert r.status_code == 200 and r.json()["username"] == "sam"
    assert login(TestClient(app), "sam", "pass-word-1")["user"]["display_name"] == "Sam Lee"
    row = next(x for x in client.get("/api/users").json() if x["username"] == "sam")
    assert not row["invite_pending"]
    assert db.query(AuditEvent).filter_by(action="invite_accepted").count() == 1


def test_resend_invite_replaces_the_link(client, mailbox):
    u = make_user(client, mailbox)
    first = link_token(mailbox[-1])
    assert client.post(f"/api/users/{u['id']}/invite").json()["invite_sent"] is True
    c = TestClient(app)
    assert c.post("/api/auth/link", json={"token": first}).status_code == 410
    assert c.post("/api/auth/link", json={"token": link_token(mailbox[-1])}).status_code == 200


def test_resend_refused_after_accepting(client, mailbox):
    u = _user_with_password(client, mailbox)
    assert client.post(f"/api/users/{u['id']}/invite").status_code == 409


def test_invite_needs_email_setup_but_password_still_works(client):
    r = client.post("/api/users", json={"username": "sam", "display_name": "Sam", "email": "sam@test.example"})
    assert r.status_code == 409 and "password" in r.json()["detail"]
    r = client.post("/api/users", json={"username": "sam", "display_name": "Sam", "email": "sam@test.example", "password": "pass-word-1"})
    assert r.status_code == 201 and not r.json()["invite_pending"]
    assert client.post("/api/users", json={"username": "kim", "display_name": "Kim"}).status_code == 422


def test_failed_send_keeps_the_user_and_can_be_resent(client, mailbox, monkeypatch):
    def down(*a):
        raise OSError("smtp down")
    monkeypatch.setattr(mail, "send", down)
    r = client.post("/api/users", json={"username": "sam", "display_name": "Sam", "email": "sam@test.example"})
    assert r.status_code == 201 and r.json()["invite_sent"] is False and r.json()["invite_pending"]


def test_email_is_unique_ignoring_case(client, mailbox):
    make_user(client, mailbox)
    r = client.post("/api/users", json={"username": "other", "display_name": "O", "email": "SAM@test.example"})
    assert r.status_code == 409


def test_invited_admin_sets_up_mfa_at_first_login(db, mfa_on, mailbox):
    admin, _, _ = enroll()
    make_user(admin, mailbox, "pat", is_admin=True)
    c = TestClient(app)
    c.post("/api/auth/set-password", json={"token": link_token(mailbox[-1]), "password": "pass-word-1"})
    assert login(TestClient(app), "pat", "pass-word-1")["mfa"] == "setup"


def test_mailgun_used_when_configured(monkeypatch):
    import httpx
    from app.config import settings
    from app.services import mail
    monkeypatch.setattr(settings, "mailgun_api_key", "key-test")
    monkeypatch.setattr(settings, "mailgun_domain", "mg.test")
    monkeypatch.setattr(settings, "mail_from", "bar@mg.test")
    monkeypatch.setattr(settings, "public_url", "https://bar.test")
    calls = []

    def fake_post(url, **kw):
        calls.append((url, kw))
        return httpx.Response(calls and (403 if len(calls) > 1 else 200), text="nope")
    monkeypatch.setattr(httpx, "post", fake_post)

    assert mail.enabled()
    assert mail.try_send("a@x.test", "Hi", "body")
    url, kw = calls[0]
    assert url == "https://api.mailgun.net/v3/mg.test/messages"
    assert kw["auth"] == ("api", "key-test")
    assert kw["data"] == {"from": "bar@mg.test", "to": "a@x.test", "subject": "Hi", "text": "body"}
    assert not mail.try_send("a@x.test", "Hi", "body")  # 403 is logged, not raised


# ---- optional MFA: opt in and out from My account ----------------------------------------------------------

def opt_in(c: TestClient, password=PW):
    s = c.post("/api/auth/mfa/enroll", json={"current_password": password})
    assert s.status_code == 200, s.text
    r = c.post("/api/auth/mfa/enroll/confirm", json={"mfa_code": code(s.json()["secret"])})
    assert r.status_code == 200, r.text
    return s.json()["secret"], r.json()["recovery_codes"]


def test_mfa_is_optional_by_default(db, monkeypatch):
    monkeypatch.setattr(auth, "FAIL_DELAY_S", 0)
    c = TestClient(app)
    assert login(c)["ok"] is True  # admin, no second step
    me = c.get("/api/auth/me").json()["user"]
    assert me["mfa_enabled"] is False and me["mfa_required"] is False


def test_opt_in_then_password_login_asks_for_code(db, monkeypatch):
    monkeypatch.setattr(auth, "FAIL_DELAY_S", 0)
    c = TestClient(app)
    login(c)
    other = TestClient(app)
    login(other)
    secret, codes = opt_in(c)
    assert len(codes) == 10
    assert c.get("/api/users").status_code == 200  # this session passed setup and stays
    assert other.get("/api/users").status_code == 401  # sessions without the code end
    fresh = TestClient(app)
    p = login(fresh)
    assert p["mfa"] == "verify"
    assert verify(fresh, p["mfa_token"], code(secret, 1)).status_code == 200


def test_enroll_needs_password_and_right_code(db, monkeypatch):
    monkeypatch.setattr(auth, "FAIL_DELAY_S", 0)
    c = TestClient(app)
    login(c)
    assert c.post("/api/auth/mfa/enroll", json={"current_password": "nope"}).status_code == 400
    assert c.post("/api/auth/mfa/enroll", json={"current_password": PW}).status_code == 200
    assert c.post("/api/auth/mfa/enroll/confirm", json={"mfa_code": "000000"}).status_code == 400
    assert c.get("/api/auth/me").json()["user"]["mfa_enabled"] is False


def test_opt_out_keeps_session_and_drops_second_step(db, monkeypatch):
    monkeypatch.setattr(auth, "FAIL_DELAY_S", 0)
    c = TestClient(app)
    login(c)
    secret, _ = opt_in(c)
    r = c.post("/api/auth/mfa/disable", json={"current_password": PW, "mfa_code": code(secret, 1)})
    assert r.status_code == 200, r.text
    assert c.get("/api/users").status_code == 200
    assert login(TestClient(app))["ok"] is True


def test_cannot_opt_out_when_required(db, mfa_on):
    admin, secret, _ = enroll()
    assert admin.get("/api/auth/me").json()["user"]["mfa_required"] is True
    r = admin.post("/api/auth/mfa/disable", json={"current_password": PW, "mfa_code": code(secret, 1)})
    assert r.status_code == 409


def test_user_without_email_when_mail_is_on(db, mfa_on, mailbox):
    admin, _, _ = enroll()
    r = admin.post("/api/users", json={"username": "noemail", "display_name": "No Email", "password": "pass-word-1"})
    assert r.status_code == 201, r.text
    assert r.json()["email"] is None and r.json()["invite_sent"] is None
    assert not mailbox
    assert login(TestClient(app), "noemail", "pass-word-1")["ok"] is True
    assert admin.post("/api/users", json={"username": "neither", "display_name": "Neither"}).status_code == 422
