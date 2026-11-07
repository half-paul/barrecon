from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from ..models import AppSetting
from ..units import OZ_IMPERIAL_ML

DEFAULTS: dict[str, str] = {
    "oz_ml": str(OZ_IMPERIAL_ML),  # Canadian / imperial ounce
    "variance_threshold_pct": "30",
    "price_alert_pct": "10",  # flag a delivery line whose pack cost moved this much from the last one
    "count_weekday": "6",  # 0=Mon .. 6=Sun; day the weekly count is taken
    "count_reminder_enabled": "true",
    "variance_chart_items": "[]",  # JSON list of item ids picked for the variance chart
    "variance_view_added": "[]",  # JSON list of not-counted item ids added to the variance list
    "variance_view_removed": "[]",  # JSON list of counted item ids removed from the variance list
    "swype_auto_import": "true",  # pull yesterday's Swype sales every night
    "swype_auto_import_hour": "5",  # local hour the nightly import runs (bar closed, day complete)
    "swype_auto_last": "",  # JSON result of the last nightly run, written by swype_auto
    "idle_logout_minutes": "30",  # a session unused this long is logged out (shared bar PC); 0: off
    "audit_retention_days": "365",  # audit entries older than this are removed nightly; 0: keep forever
    "audit_purge_last": "",  # local date of the last retention run, written by services/maintenance.py
}


def get(db: Session, key: str) -> str:
    row = db.get(AppSetting, key)
    return row.value if row else DEFAULTS[key]


def get_all(db: Session) -> dict[str, str]:
    out = dict(DEFAULTS)
    for row in db.query(AppSetting).all():
        out[row.key] = row.value
    return out


def set_many(db: Session, values: dict[str, str]) -> None:
    for k, v in values.items():
        if k not in DEFAULTS:
            raise ValueError(f"unknown setting {k}")
        row = db.get(AppSetting, k)
        if row:
            row.value = str(v)
        else:
            db.add(AppSetting(key=k, value=str(v)))
    db.commit()


def oz_ml(db: Session) -> Decimal:
    return Decimal(get(db, "oz_ml"))
