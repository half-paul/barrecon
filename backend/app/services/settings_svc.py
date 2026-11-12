from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from ..models import AppSetting, UserPref
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


# settings that cover every tenant; changed in the platform console, not by a business
PLATFORM_KEYS = {"audit_retention_days", "audit_purge_last"}
PLATFORM = "platform"


def _scope(db: Session, key: str) -> str:
    """Settings belong to the business the session works in (app.tenancy); a few are platform-wide."""
    return PLATFORM if key in PLATFORM_KEYS else db.info.get("business_id") or PLATFORM


def get(db: Session, key: str) -> str:
    row = db.get(AppSetting, (_scope(db, key), key))
    return row.value if row else DEFAULTS[key]


def get_all(db: Session) -> dict[str, str]:
    out = dict(DEFAULTS)
    scopes = {PLATFORM, db.info.get("business_id") or PLATFORM}
    for row in db.query(AppSetting).filter(AppSetting.scope.in_(scopes)).all():
        if row.scope == _scope(db, row.key):
            out[row.key] = row.value
    return out


def set_many(db: Session, values: dict[str, str]) -> None:
    for k, v in values.items():
        if k not in DEFAULTS:
            raise ValueError(f"unknown setting {k}")
        row = db.get(AppSetting, (_scope(db, k), k))
        if row:
            row.value = str(v)
        else:
            db.add(AppSetting(scope=_scope(db, k), key=k, value=str(v)))
    db.commit()


# choices kept per login and per business, not shared with the rest of the bar
USER_DEFAULTS: dict[str, str] = {
    "variance_chart_style": "bars",  # which of the three Stock vs sales charts this login looks at
}


def get_user_prefs(db: Session, user_id: str, business_id: str) -> dict[str, str]:
    out = dict(USER_DEFAULTS)
    for row in db.query(UserPref).filter(UserPref.user_id == user_id, UserPref.location_id == business_id).all():
        if row.key in out:
            out[row.key] = row.value
    return out


def set_user_prefs(db: Session, user_id: str, business_id: str, values: dict[str, str]) -> None:
    for k, v in values.items():
        if k not in USER_DEFAULTS:
            raise ValueError(f"unknown preference {k}")
        row = db.get(UserPref, (user_id, business_id, k))
        if row:
            row.value = str(v)
        else:
            db.add(UserPref(user_id=user_id, location_id=business_id, key=k, value=str(v)))
    db.commit()


def oz_ml(db: Session) -> Decimal:
    return Decimal(get(db, "oz_ml"))
