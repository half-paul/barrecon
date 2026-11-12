"""Audit trail.

- AuditMiddleware gives every /api request a context (audit_ctx: event id, who, area). app.auth fills in who.
  Once the response is sent it writes one AuditEvent for: every non-GET request by a logged-in user, a GET that
  wrote something, and the sensitive reads in SENSITIVE_READS. Logins and logouts are written by routers/auth.py.
- While a context is set, every flush records the rows it touched as AuditChange rows (field before/after), in
  the same transaction, so a rolled-back request leaves none. Bulk UPDATE/DELETE statements are recorded too.
- Events form a hash chain: hash = HMAC-SHA256(prev_hash + the event + a digest of its changes). verify() walks it.
  On PostgreSQL a trigger refuses UPDATE and DELETE on both tables, except the retention purge.
- System jobs (nightly Swype import, retention purge) run inside system_job() and are recorded the same way.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, LargeBinary, delete, event, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import db as dbmod
from .config import settings
from .models import AuditChange, AuditEvent, new_id, utcnow

log = logging.getLogger(__name__)

READ_METHODS = ("GET", "HEAD", "OPTIONS")
SKIP = {"/api/auth/login", "/api/auth/logout"}  # recorded by the auth router itself
MAX_BODY = 16_000
SECRET = re.compile(r"pass|secret|token|key|(^|_)pin$|mfa_code", re.I)
FILENAME = re.compile(rb'filename="([^"]{1,255})"')
# reads worth knowing about: data leaving the portal, and who looked at the audit log
SENSITIVE_READS = {
    "/api/items/export.xlsx", "/api/counts/template.xlsx", "/api/movements/template.xlsx",
    "/api/deliveries/{delivery_id}/receipt", "/api/audit", "/api/audit/{event_id}/changes", "/api/users",
    "/api/platform/reports/monthly", "/api/platform/tenants/{tenant_id}", "/api/platform/audit/{event_id}/changes",
}

audit_ctx: ContextVar[dict | None] = ContextVar("audit_ctx", default=None)


# ---- request bodies ---------------------------------------------------------------------------------------

def redact(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: "***" if SECRET.search(str(k)) else redact(x) for k, x in v.items()}
    if isinstance(v, list):
        return [redact(x) for x in v]
    return v


def _body_detail(content_type: str, body: bytes) -> dict | None:
    if not body:
        return None
    if content_type.startswith("application/json"):
        if len(body) > MAX_BODY:
            return {"body_truncated": True, "body_bytes": len(body)}
        try:
            return {"body": redact(json.loads(body))}
        except ValueError:
            return None
    if content_type.startswith("multipart/form-data"):
        return {"files": [f.decode(errors="replace") for f in FILENAME.findall(body[:200_000])], "body_bytes": len(body)}
    return None


def event_id(scope: Scope) -> str | None:
    """The id this request's event will get, so changes it makes link to it."""
    return (scope.get("state", {}).get("audit") or {}).get("event_id")


def client_ip(scope: Scope) -> str | None:
    if settings.trust_proxy:
        for k, v in scope.get("headers", []):
            if k == b"x-forwarded-for":
                return v.decode().split(",")[0].strip()[:64]
    c = scope.get("client")
    return c[0] if c else None


# ---- field-level changes ----------------------------------------------------------------------------------

SKIP_TABLES = {"audit_events", "audit_changes", "auth_tokens"}
NOISE = {"created_at", "updated_at"}
HIDDEN = {"password_hash", "pin_hash", "totp_secret", "recovery_codes", "swype_password"}  # recorded as changed, never the value
COLLAPSE = 200  # more rows than this of one table in one flush: one summary row
BIG_JSON = 2000


def _val(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, Decimal):
        return str(v.normalize()) if v == v.to_integral() else str(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v)


def _shown(col, key: str, v: Any) -> Any:
    if key in HIDDEN:
        return "***" if v else None
    if isinstance(col.type, JSON) and v is not None:
        s = json.dumps(v, default=str)
        return v if len(s) <= BIG_JSON else f"<{len(s)} characters>"
    return _val(v)


def _row_id(st) -> str | None:
    vals = [st.dict.get(st.mapper.get_property_by_column(c).key) for c in st.mapper.primary_key]
    return "/".join(str(v) for v in vals) if all(v is not None for v in vals) else None


def _diff(obj, op: str) -> dict | None:
    st = inspect(obj)
    out = {}
    for attr in st.mapper.column_attrs:
        col, key = attr.columns[0], attr.key
        if key in NOISE or isinstance(col.type, LargeBinary):
            continue
        if op == "update":
            h = st.attrs[key].history
            if not h.has_changes():
                continue
            old = h.deleted[0] if h.deleted else None
            new = h.added[0] if h.added else None
            if old == new:
                continue
            out[key] = [_shown(col, key, old), _shown(col, key, new)]
        else:
            v = st.dict.get(key)
            if v is not None:
                out[key] = [None, _shown(col, key, v)] if op == "insert" else [_shown(col, key, v), None]
    return out or None


def _write_changes(conn, ctx: dict, rows: list[dict]) -> None:
    now = utcnow()
    for r in rows:
        ctx["pos"] = ctx.get("pos", 0) + 1
        r.update(id=new_id(), event_id=ctx["event_id"], position=ctx["pos"], at=now)
    conn.execute(AuditChange.__table__.insert(), rows)
    ctx["changes"] = ctx.get("changes", 0) + len(rows)


@event.listens_for(Session, "after_flush")
def _after_flush(session: Session, _ctx) -> None:
    ctx = audit_ctx.get()
    if not ctx:
        return
    rows: list[dict] = []
    for op, objs in (("insert", session.new), ("update", session.dirty), ("delete", session.deleted)):
        by_table: dict[str, list] = defaultdict(list)
        for obj in objs:
            t = getattr(obj, "__tablename__", None)
            if not t or t in SKIP_TABLES:
                continue
            ch = _diff(obj, op)
            if ch is not None or op != "update":
                by_table[t].append((_row_id(inspect(obj)), ch))
        for t, items in by_table.items():
            if len(items) > COLLAPSE:
                rows.append({"table_name": t, "op": op, "row_id": None, "changes": {"rows": len(items)}})
            else:
                rows += [{"table_name": t, "op": op, "row_id": rid, "changes": ch} for rid, ch in items]
    if rows:
        _write_changes(session.connection(), ctx, rows)


@event.listens_for(Session, "do_orm_execute")
def _bulk(state) -> Any:
    ctx = audit_ctx.get()
    if not ctx or not (state.is_update or state.is_delete):
        return None
    table = state.statement.table.name
    if table in SKIP_TABLES:
        return None
    result = state.invoke_statement()
    where = state.statement.whereclause
    try:
        cond = str(where.compile(compile_kwargs={"literal_binds": True})) if where is not None else ""
    except Exception:  # noqa: BLE001 - a value the dialect can't inline: keep the template and the values
        c = where.compile()
        cond = f"{c} {json.dumps({k: _val(v) for k, v in c.params.items()})}"
    _write_changes(state.session.connection(), ctx, [{"table_name": table, "op": "bulk_delete" if state.is_delete else "bulk_update",
                                                       "row_id": None, "changes": {"rows": result.rowcount, "where": cond}}])
    return result


# ---- events and the hash chain ----------------------------------------------------------------------------

HASHED = ("seq", "at", "user_id", "username", "action", "area", "method", "route", "path", "status", "ip", "detail")


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _canon_at(dt: datetime) -> str:
    return _utc(dt).replace(tzinfo=None).isoformat(timespec="microseconds")


def _changes_digest(s: Session, event_id: str) -> str:
    rows = s.query(AuditChange).filter(AuditChange.event_id == event_id).order_by(AuditChange.position).all()
    body = [[c.position, c.table_name, c.row_id, c.op, c.changes] for c in rows]
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def _hash(ev: AuditEvent, prev: str | None, digest: str) -> str:
    fields = {k: getattr(ev, k) for k in HASHED}
    fields["at"] = _canon_at(ev.at)
    payload = json.dumps([prev or "", fields, digest], sort_keys=True, default=str)
    # keyed, so database access alone can't rebuild the chain after an edit; changing SESSION_SECRET breaks verify()
    return hmac.new(settings.session_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def record(**fields) -> str:
    """Write one event at the end of the chain. Returns its id."""
    fields.setdefault("id", new_id())
    fields.setdefault("at", utcnow())
    for attempt in range(5):
        with dbmod.SessionLocal() as s:
            if s.bind.dialect.name == "postgresql":
                s.execute(text("SELECT pg_advisory_xact_lock(724001)"))
            last = s.query(AuditEvent.seq, AuditEvent.hash).filter(AuditEvent.seq.isnot(None)).order_by(AuditEvent.seq.desc()).first()
            ev = AuditEvent(**fields, seq=(last.seq + 1) if last else 1, prev_hash=last.hash if last else None)
            ev.hash = _hash(ev, ev.prev_hash, _changes_digest(s, ev.id))
            s.add(ev)
            try:
                s.commit()
                return ev.id
            except IntegrityError:  # another writer took this seq (SQLite has no advisory lock)
                s.rollback()
    raise RuntimeError("could not append to the audit log")


def verify(s: Session) -> dict:
    """Recompute the chain. The oldest remaining row is trusted as the start (older ones may have been purged)."""
    checked, prev, first = 0, None, True
    for ev in s.query(AuditEvent).filter(AuditEvent.seq.isnot(None)).order_by(AuditEvent.seq).yield_per(500):
        expect_prev = ev.prev_hash if first else prev
        if ev.prev_hash != expect_prev or _hash(ev, expect_prev, _changes_digest(s, ev.id)) != ev.hash:
            return {"ok": False, "checked": checked, "broken_at": {"seq": ev.seq, "id": ev.id, "at": ev.at}}
        prev, first, checked = ev.hash, False, checked + 1
    return {"ok": True, "checked": checked, "broken_at": None}


GUARD_SQL = [
    """CREATE OR REPLACE FUNCTION barrecon_audit_readonly() RETURNS trigger AS $$
    BEGIN
      IF TG_OP = 'DELETE' AND current_setting('barrecon.audit_purge', true) = 'on' THEN RETURN OLD; END IF;
      RAISE EXCEPTION 'audit records are read-only';
    END $$ LANGUAGE plpgsql""",
    "DROP TRIGGER IF EXISTS audit_events_readonly ON audit_events",
    "CREATE TRIGGER audit_events_readonly BEFORE UPDATE OR DELETE ON audit_events FOR EACH ROW EXECUTE FUNCTION barrecon_audit_readonly()",
    "DROP TRIGGER IF EXISTS audit_changes_readonly ON audit_changes",
    "CREATE TRIGGER audit_changes_readonly BEFORE UPDATE OR DELETE ON audit_changes FOR EACH ROW EXECUTE FUNCTION barrecon_audit_readonly()",
]


def install_guards(engine) -> None:
    """PostgreSQL only: refuse edits and deletes of audit rows (the retention purge sets barrecon.audit_purge)."""
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        for ddl in GUARD_SQL:
            conn.execute(text(ddl))


def purge(s: Session, retention_days: int) -> dict:
    """Delete events and changes older than the retention period. 0 days: keep everything."""
    if retention_days <= 0:
        return {"deleted": 0, "changes": 0, "cutoff": None}
    cutoff = utcnow() - timedelta(days=retention_days)
    if s.bind.dialect.name == "postgresql":
        s.execute(text("SET LOCAL barrecon.audit_purge = 'on'"))
    # only ever a prefix of the chain, so what remains still verifies; two events can land out of time order
    keep_from = s.query(func.min(AuditEvent.seq)).filter(AuditEvent.at >= cutoff).scalar()
    old = select(AuditEvent.id).where(AuditEvent.seq < keep_from) if keep_from is not None else select(AuditEvent.id)
    n_ch = s.execute(delete(AuditChange).where(AuditChange.event_id.in_(old))).rowcount
    n_ch += s.execute(delete(AuditChange).where(AuditChange.at < cutoff, AuditChange.event_id.not_in(select(AuditEvent.id)))).rowcount
    n_ev = s.execute(delete(AuditEvent).where(AuditEvent.seq < keep_from) if keep_from is not None else delete(AuditEvent)).rowcount
    s.commit()
    return {"deleted": n_ev, "changes": n_ch, "cutoff": cutoff.isoformat(timespec="seconds")}


@contextmanager
def system_job(name: str, area: str | None = None, tenant_id: str | None = None, location_id: str | None = None):
    """Record what a background job changed as one event by `system:<name>`, for the business it ran for.
    Put a summary in holder["detail"]."""
    holder: dict[str, Any] = {"event_id": new_id(), "username": f"system:{name}"}
    tok = audit_ctx.set(holder)
    status = 200
    try:
        yield holder
    except Exception:
        status = 500
        raise
    finally:
        audit_ctx.reset(tok)
        if holder.get("changes") or holder.get("detail") or status != 200:
            record(id=holder["event_id"], username=holder["username"], action="system", area=area,
                   path=name, status=status, detail=holder.get("detail"), tenant_id=tenant_id, location_id=location_id)


# ---- plain-language labels --------------------------------------------------------------------------------

LABELS: dict[tuple[str, str], str] = {
    ("POST", "/api/auth/password"): "Changed own password",
    ("POST", "/api/auth/pin"): "Changed own PIN",
    ("POST", "/api/auth/mfa/recovery-codes"): "Made new recovery codes",
    ("POST", "/api/auth/mfa/reset"): "Removed own authenticator",
    ("POST", "/api/auth/mfa/enroll"): "Started two-step login setup",
    ("POST", "/api/auth/mfa/enroll/confirm"): "Turned on two-step login",
    ("POST", "/api/auth/mfa/disable"): "Turned off two-step login",
    ("POST", "/api/items/import"): "Imported stock items sheet",
    ("POST", "/api/items"): "Added stock item",
    ("PATCH", "/api/items/{item_id}"): "Changed stock item",
    ("GET", "/api/items/export.xlsx"): "Downloaded stock items sheet",
    ("POST", "/api/counts/import"): "Imported count sheet",
    ("POST", "/api/counts"): "Started count",
    ("PATCH", "/api/counts/{session_id}"): "Changed count date",
    ("PUT", "/api/counts/{session_id}/lines"): "Entered count lines",
    ("DELETE", "/api/counts/{session_id}/lines/{item_id}"): "Removed count line",
    ("POST", "/api/counts/{session_id}/post"): "Posted count",
    ("DELETE", "/api/counts/{session_id}"): "Deleted count",
    ("GET", "/api/counts/template.xlsx"): "Downloaded count sheet",
    ("POST", "/api/movements/import"): "Imported deliveries sheet",
    ("POST", "/api/movements"): "Recorded stock movement",
    ("POST", "/api/movements/batch"): "Recorded delivery sheet",
    ("POST", "/api/movements/{movement_id}/retract"): "Retracted stock movement",
    ("GET", "/api/movements/template.xlsx"): "Downloaded deliveries sheet",
    ("POST", "/api/imports/legacy/preview"): "Previewed inventory workbook",
    ("POST", "/api/imports/legacy"): "Imported inventory workbook",
    ("POST", "/api/imports/swype-item-sales/preview"): "Previewed Swype sales file",
    ("POST", "/api/imports/swype-item-sales"): "Imported Swype sales file",
    ("POST", "/api/imports/swype-item-sales/fetch"): "Imported Swype sales from Swype",
    ("DELETE", "/api/imports/swype-item-sales/periods"): "Removed Swype sales for dates",
    ("DELETE", "/api/imports/swype-item-sales/periods/{period_from}/{period_to}"): "Removed Swype sales period",
    ("POST", "/api/imports/swype-items/preview"): "Previewed Swype item list",
    ("POST", "/api/imports/swype-items"): "Imported Swype item list",
    ("POST", "/api/imports/swype-items/sync-from-sales"): "Synced Swype items from sales",
    ("DELETE", "/api/imports/{batch_id}"): "Removed an import",
    ("POST", "/api/imports/issues/{issue_id}/resolve"): "Resolved a review item",
    ("PUT", "/api/settings"): "Changed settings",
    ("POST", "/api/recipes/suggest-pours"): "Drafted pour recipes",
    ("POST", "/api/recipes/match/approve"): "Approved matched recipes",
    ("POST", "/api/recipes/no-stock"): "Marked food and coffee as no stock",
    ("POST", "/api/recipes/import"): "Imported recipes sheet",
    ("PUT", "/api/recipes/{code}"): "Saved recipe",
    ("DELETE", "/api/recipes/{code}"): "Deleted recipe",
    ("PUT", "/api/recipes/{code}/photo"): "Changed recipe photo",
    ("DELETE", "/api/recipes/{code}/photo"): "Removed recipe photo",
    ("POST", "/api/groups"): "Added item group",
    ("PATCH", "/api/groups/{name}"): "Changed item group",
    ("POST", "/api/groups/{name}/apply"): "Applied group pack to items",
    ("DELETE", "/api/groups/{name}"): "Removed item group",
    ("PATCH", "/api/swype-items/{code}"): "Changed Swype item",
    ("POST", "/api/deliveries/receipt"): "Uploaded receipt",
    ("POST", "/api/deliveries"): "Started delivery",
    ("PUT", "/api/deliveries/{delivery_id}/receipt"): "Replaced receipt",
    ("POST", "/api/deliveries/{delivery_id}/read"): "Re-read receipt",
    ("PUT", "/api/deliveries/{delivery_id}"): "Saved delivery",
    ("POST", "/api/deliveries/{delivery_id}/post"): "Posted delivery",
    ("POST", "/api/deliveries/{delivery_id}/reopen"): "Reopened delivery",
    ("POST", "/api/deliveries/{delivery_id}/void"): "Voided delivery",
    ("DELETE", "/api/deliveries/{delivery_id}"): "Deleted draft delivery",
    ("GET", "/api/deliveries/{delivery_id}/receipt"): "Viewed receipt",
    ("POST", "/api/users"): "Added user",
    ("PATCH", "/api/users/{user_id}"): "Changed user",
    ("POST", "/api/users/{user_id}/password"): "Set user's password",
    ("POST", "/api/users/{user_id}/pin"): "Set user's PIN",
    ("DELETE", "/api/users/{user_id}/pin"): "Removed user's PIN",
    ("POST", "/api/users/{user_id}/logout"): "Logged user out everywhere",
    ("POST", "/api/users/{user_id}/unlock"): "Unlocked user's login",
    ("POST", "/api/users/{user_id}/invite"): "Resent user's invite",
    ("POST", "/api/users/{user_id}/mfa/reset"): "Reset user's two-step login",
    ("GET", "/api/users"): "Viewed users",
    ("GET", "/api/audit"): "Searched audit log",
    ("GET", "/api/audit/{event_id}/changes"): "Viewed audit details",
    ("GET", "/api/platform/audit/{event_id}/changes"): "Viewed audit details",
    ("POST", "/api/auth/business"): "Switched business",
    ("POST", "/api/businesses"): "Added business",
    ("PATCH", "/api/businesses/{business_id}"): "Changed business",
    ("PUT", "/api/businesses/{business_id}/logo"): "Changed business logo",
    ("DELETE", "/api/businesses/{business_id}/logo"): "Removed business logo",
    ("PUT", "/api/businesses/{business_id}/members/{user_id}"): "Set user's access to a business",
    ("DELETE", "/api/businesses/{business_id}/members/{user_id}"): "Removed user from a business",
    ("POST", "/api/platform/tenants"): "Added tenant",
    ("GET", "/api/platform/tenants/{tenant_id}"): "Viewed tenant",
    ("PATCH", "/api/platform/tenants/{tenant_id}"): "Changed tenant",
    ("POST", "/api/platform/tenants/{tenant_id}/suspend"): "Suspended tenant",
    ("POST", "/api/platform/tenants/{tenant_id}/reactivate"): "Reactivated tenant",
    ("POST", "/api/platform/tenants/{tenant_id}/admins"): "Added tenant admin",
    ("PATCH", "/api/platform/businesses/{business_id}"): "Changed business features",
    ("PUT", "/api/platform/settings"): "Changed platform settings",
    ("POST", "/api/platform/admins"): "Added platform admin",
    ("POST", "/api/platform/admins/{user_id}/active"): "Switched platform admin on or off",
    ("GET", "/api/platform/reports/monthly"): "Viewed usage report",
}
ACTION_LABELS = {"login": "Logged in", "login_failed": "Failed login", "login_blocked": "Login blocked (too many tries)",
                 "logout": "Logged out", "mfa_setup_started": "Started two-step login setup", "mfa_email_sent": "Emailed a recovery code",
                 "mfa_email_recovery": "Replaced authenticator by email", "reset_requested": "Asked for a password reset",
                 "password_reset": "Reset password by email", "invite_accepted": "Accepted invite"}
# where in the portal the thing a route changed is shown
LINKS = [
    (re.compile(r"^/api/counts/(?P<id>[^/]+)"), "/counts/{id}"),
    (re.compile(r"^/api/deliveries/(?P<id>[0-9a-f-]{36})"), "/deliveries/{id}"),
    (re.compile(r"^/api/recipes/(?P<id>[^/]+)"), "/recipes?q={id}"),
    (re.compile(r"^/api/users/(?P<id>[0-9a-f-]{36})"), "/audit?user={id}"),
    (re.compile(r"^/api/groups"), "/items/groups"),
    (re.compile(r"^/api/imports/swype-item-sales"), "/sales"),
    (re.compile(r"^/api/settings"), "/settings"),
]
NOT_LINKED = {"template.xlsx", "import", "suggest-pours"}


def describe(ev: AuditEvent) -> tuple[str, str | None]:
    if ev.action in ACTION_LABELS:
        return ACTION_LABELS[ev.action], None
    if ev.action == "system":
        return {"swype_auto": "Nightly Swype import", "audit_purge": "Removed old audit entries"}.get(ev.path or "", ev.path or "System job"), None
    label = LABELS.get((ev.method or "", ev.route or "")) or f"{ev.method} {ev.route or ev.path}"
    if ev.action == "denied":
        label = f"Refused: {label[0].lower()}{label[1:]}"
    link = None
    if ev.path and ev.method != "DELETE" and not any(ev.path.endswith(x) for x in NOT_LINKED):
        for rx, tpl in LINKS:
            m = rx.match(ev.path)
            if m:
                link = tpl.format(**m.groupdict())
                break
    return label, link


# ---- middleware -------------------------------------------------------------------------------------------

class AuditMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return

        holder: dict[str, Any] = {"event_id": new_id()}
        scope.setdefault("state", {})["audit"] = holder
        tok = audit_ctx.set(holder)
        write = scope["method"] not in READ_METHODS
        chunks: list[bytes] = []
        size = 0
        status = 500

        async def recv() -> Message:
            nonlocal size
            msg = await receive()
            if write and msg["type"] == "http.request" and size < 200_000:
                b = msg.get("body", b"")
                chunks.append(b)
                size += len(b)
            return msg

        async def snd(msg: Message) -> None:
            nonlocal status
            if msg["type"] == "http.response.start":
                status = msg["status"]
                cookie = holder.get("set_cookie")
                # a fresh session cookie (sliding idle timeout), unless the route set or cleared it itself
                if cookie and not any(k == b"set-cookie" and v.startswith(cookie[0]) for k, v in msg.get("headers", [])):
                    msg = {**msg, "headers": [*msg.get("headers", []), (b"set-cookie", cookie[1])]}
            await send(msg)

        try:
            await self.app(scope, recv, snd)
        finally:
            audit_ctx.reset(tok)
            route = getattr(scope.get("route"), "path", None)
            wanted = path not in SKIP and (write or holder.get("changes") or (route in SENSITIVE_READS and status < 400))
            if holder.get("user_id") and wanted:
                ctype = next((v.decode() for k, v in scope.get("headers", []) if k == b"content-type"), "")
                detail = _body_detail(ctype, b"".join(chunks)) or {}
                if scope.get("query_string"):
                    detail["query"] = scope["query_string"].decode(errors="replace")
                action = "denied" if status == 403 else "change" if write or holder.get("changes") else "read"
                try:
                    await run_in_threadpool(
                        record, id=holder["event_id"], user_id=holder["user_id"], username=holder.get("username"),
                        tenant_id=holder.get("tenant_id"), location_id=holder.get("location_id"),
                        action=action, area=holder.get("area"), method=scope["method"], route=route, path=path[:500],
                        status=status, ip=client_ip(scope), detail=detail or None)
                except Exception:  # noqa: BLE001 - never fail a request that already happened; leave a trace in the log
                    log.exception("could not write audit event for %s %s", scope["method"], path)


def count_changes(s: Session, event_ids: list[str]) -> dict[str, int]:
    if not event_ids:
        return {}
    return dict(s.execute(select(AuditChange.event_id, func.count()).where(AuditChange.event_id.in_(event_ids))
                          .group_by(AuditChange.event_id)).all())
