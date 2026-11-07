from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import Auth, perm
from ..db import get_db
from ..seed import default_location
from ..services import variance

router = APIRouter(prefix="/api/variance", tags=["variance"], dependencies=[Auth])
VARIANCE = perm("variance")


@router.get("/periods", dependencies=[VARIANCE])
def periods(db: Session = Depends(get_db)):
    return variance.periods(db, default_location(db).id)


@router.get("/current")  # the Overview and Sales "this week" checklist, shown to everyone
def current(db: Session = Depends(get_db)):
    return variance.current(db, default_location(db).id)


@router.get("", dependencies=[VARIANCE])
def report(opening: str | None = None, closing: str | None = None, db: Session = Depends(get_db)):
    try:
        return variance.run(db, default_location(db).id, opening, closing)
    except variance.VarianceError as e:
        raise HTTPException(400, str(e)) from None
