"""Nightly Swype sales import: once a day, at the configured local hour, pull every full day that is not on
file yet, one day at a time, exactly as the Sales page does by hand.

The days are the ones after the latest imported day through yesterday, plus any day missing since the last
posted count (a gap left by a failed night). A day Swype has no sales for is skipped. The first failure
stops the run, keeps the days already imported, and raises a "To review" item so it is seen on the Overview.
The result of the last run is kept in the setting swype_auto_last for the Sales page.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from .. import audit
from ..config import settings
from ..db import SessionLocal
from ..models import DataIssue
from ..seed import default_location
from . import settings_svc, swype_api, swype_item_sales, variance

log = logging.getLogger(__name__)
CHECK_EVERY_S = 600


def days_to_import(db: Session, location_id: str) -> list[date]:
    start, end = swype_item_sales.pending_range(db, location_id)
    days = set()
    if start is not None:
        while start <= end:
            days.add(start)
            start += timedelta(days=1)
    days |= set(variance.current(db, location_id)["missing_sales_days"])
    return sorted(days)


def run(db: Session) -> dict:
    """Import the missing days now. Returns {"imported", "no_sales", "error"}; also stored as swype_auto_last."""
    loc = default_location(db).id
    out: dict = {"at": datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec="minutes"),
                 "imported": [], "no_sales": [], "error": None}
    for d in days_to_import(db, loc):
        try:
            data = swype_api.fetch_item_sales(d, d)
        except swype_api.NoSales:
            out["no_sales"].append(d.isoformat())
            continue
        except swype_api.SwypeError as e:
            out["error"] = f"{d}: {e}"
            break
        r = swype_item_sales.apply(db, data, f"swype_item_sales_{d}_{d}.xlsx", loc, source="swype_auto")
        if r["status"] != "ok":
            out["error"] = f"{d}: {r['error']}"
            break
        out["imported"].append(d.isoformat())
    if out["error"]:
        db.add(DataIssue(code="swype_auto_import_failed", entity="swype",
                         detail={"detail": f"Nightly Swype sales import stopped at {out['error']}. "
                                           "Import the missing days on the Sales page."}))
    settings_svc.set_many(db, {"swype_auto_last": json.dumps(out)})
    return out


def due(db: Session, now: datetime) -> bool:
    """On, Swype login set, past today's hour, and not run yet today (so a restart after the hour still runs)."""
    if settings_svc.get(db, "swype_auto_import") != "true" or not swype_api.configured():
        return False
    if now.hour < int(settings_svc.get(db, "swype_auto_import_hour")):
        return False
    last = settings_svc.get(db, "swype_auto_last")
    return not last or json.loads(last).get("at", "")[:10] != now.date().isoformat()


def tick() -> None:
    with SessionLocal() as db:
        if due(db, datetime.now(ZoneInfo(settings.timezone))):
            with audit.system_job("swype_auto", area="sales") as job:
                r = run(db)
                job["detail"] = r
            log.info("nightly Swype import: %s", r)


async def loop() -> None:
    while True:
        try:
            await asyncio.to_thread(tick)
        except Exception:  # noqa: BLE001 - keep the scheduler alive; the next tick tries again
            log.exception("nightly Swype import failed")
        await asyncio.sleep(CHECK_EVERY_S)
