"""Daily housekeeping: remove audit entries older than the retention setting (audit_retention_days, default
365; 0 keeps everything). Runs once a local day; the run itself is recorded in the audit log."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from .. import audit
from ..config import settings
from ..db import SessionLocal
from . import settings_svc

log = logging.getLogger(__name__)
CHECK_EVERY_S = 3600


def purge_audit(db: Session) -> dict:
    days = int(settings_svc.get(db, "audit_retention_days") or 0)
    r = audit.purge(db, days)
    settings_svc.set_many(db, {"audit_purge_last": datetime.now(ZoneInfo(settings.timezone)).date().isoformat()})
    if r["deleted"] or r["changes"]:
        audit.record(username="system:audit_purge", action="system", path="audit_purge", status=200,
                     detail={**r, "retention_days": days})
    return r


def tick() -> None:
    with SessionLocal() as db:
        today = datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
        if settings_svc.get(db, "audit_purge_last") != today:
            log.info("audit retention: %s", purge_audit(db))


async def loop() -> None:
    while True:
        try:
            await asyncio.to_thread(tick)
        except Exception:  # noqa: BLE001 - keep the scheduler alive; the next tick tries again
            log.exception("audit retention failed")
        await asyncio.sleep(CHECK_EVERY_S)
