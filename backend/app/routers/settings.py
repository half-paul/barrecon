import json
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import auth
from ..auth import Auth
from ..models import User
from ..db import get_db
from ..schemas import SettingsIn, SettingsOut
from ..services import settings_svc

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Auth])


def _out(db: Session) -> SettingsOut:
    v = settings_svc.get_all(db)
    return SettingsOut(oz_ml=Decimal(v["oz_ml"]), variance_threshold_pct=Decimal(v["variance_threshold_pct"]),
                       count_weekday=int(v["count_weekday"]), count_reminder_enabled=v["count_reminder_enabled"] == "true",
                       variance_chart_items=json.loads(v["variance_chart_items"]),
                       variance_view_added=json.loads(v["variance_view_added"]),
                       variance_view_removed=json.loads(v["variance_view_removed"]),
                       swype_auto_import=v["swype_auto_import"] == "true",
                       swype_auto_import_hour=int(v["swype_auto_import_hour"]),
                       idle_logout_minutes=int(v["idle_logout_minutes"]), audit_retention_days=int(v["audit_retention_days"]))


SECURITY = {"idle_logout_minutes", "audit_retention_days"}  # admins only: shortening retention could hide tracks
VARIANCE_PICKS = {"variance_chart_items", "variance_view_added", "variance_view_removed"}


@router.get("", response_model=SettingsOut)  # every page reads these (oz size, count day)
def get_settings(db: Session = Depends(get_db)):
    return _out(db)


@router.put("", response_model=SettingsOut)
def put_settings(body: SettingsIn, request: Request, user: User = Auth, db: Session = Depends(get_db)):
    vals = {k: ("true" if v is True else "false" if v is False else json.dumps(v) if isinstance(v, list) else str(v))
            for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    current = settings_svc.get_all(db)
    vals = {k: v for k, v in vals.items() if current.get(k) != v}  # unchanged values need no permission
    if set(vals) <= VARIANCE_PICKS:
        auth.require(request, user, "variance", "view")  # these picks are saved from the Variance page
    else:
        auth.require(request, user, "settings", "edit")
    if set(vals) & SECURITY and not auth.is_admin(user):
        raise HTTPException(403, "Only an admin can change the idle logout or audit log settings.")
    settings_svc.set_many(db, vals)
    return _out(db)
