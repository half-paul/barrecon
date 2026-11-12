"""The way back in when no admin can log in: python -m app.reset_mfa <username>

Needs shell access to the server. That person sets up a new authenticator at their next login.
"""
import sys

from .audit import record
from .db import SessionLocal
from .models import User
from .services import logins


def main(username: str) -> int:
    with SessionLocal() as db:
        u = db.query(User).filter(User.username == username.strip().lower()).one_or_none()
        if not u:
            print(f"No login called {username}.")
            return 1
        logins.clear_mfa(u)
        u.session_version += 1
        db.commit()
        record(username=u.username, user_id=u.id, action="system", detail={"job": "reset_mfa"})
    print(f"Two-step login reset for {u.username}. They set it up again at their next login.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
