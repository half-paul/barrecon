from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_FLOOR, Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import current_user, perm
from ..config import settings
from ..db import get_db
from ..models import CountLine, CountSession, InventoryItem, User
from ..schemas import CountLineIn, CountLineOut, CountSessionDetail, CountSessionIn, CountSessionOut
from ..tenancy import business_id
from ..services import inventory as inv
from ..services import variance, xlsx_io

router = APIRouter(prefix="/api/counts", tags=["counts"], dependencies=[perm("counts")])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _packs(item: InventoryItem, qty: Decimal) -> tuple[Decimal, Decimal]:
    """Stock-unit qty as whole packs plus the open one, in tenths as the count screen enters it
    (hundredths for items counted in 'each')."""
    packs = qty / Decimal(item.pack_size)
    full = packs.to_integral_value(rounding=ROUND_FLOOR)
    partial = (packs - full).quantize(Decimal("0.01") if item.stock_unit == "each" else Decimal("0.1"))
    if partial >= 1:
        full, partial = full + 1, Decimal(0)
    return full, partial


def _out(db: Session, s: CountSession) -> CountSessionOut:
    o = CountSessionOut.model_validate(s)
    o.line_count = db.scalar(select(func.count()).select_from(CountLine).where(CountLine.session_id == s.id)) or 0
    return o


def _line_out(it: InventoryItem, ln: CountLine | None) -> CountLineOut:
    return CountLineOut(item_id=it.id, sku=it.sku, name=it.name, category=it.category, pack_name=it.pack_name,
                        stock_unit=it.stock_unit, pack_size=it.pack_size, packs_full=ln.packs_full if ln else None,
                        packs_partial=ln.packs_partial if ln else None, qty=ln.qty if ln else None, note=ln.note if ln else None)


def _detail(s: CountSession, db: Session) -> CountSessionDetail:
    rows = [(ln.item, ln) for ln in s.lines]
    if s.status == "draft":
        # a draft also lists the active items nobody has counted yet, with blank fields
        have = {ln.item_id for ln in s.lines}
        rows += [(it, None) for it in db.scalars(select(InventoryItem).where(InventoryItem.is_active)) if it.id not in have]
    lines = [_line_out(it, ln) for it, ln in sorted(rows, key=lambda r: ((r[0].category or ""), r[0].name))]
    base = CountSessionOut.model_validate(s).model_dump(exclude={"line_count"})
    versions = inv.count_versions(db, s)
    return CountSessionDetail(**base, lines=lines, line_count=len(s.lines),
                              versions=[_out(db, v) for v in versions] if len(versions) > 1 else [])


@router.get("", response_model=list[CountSessionOut])
def list_sessions(db: Session = Depends(get_db), include_superseded: bool = False):
    stmt = select(CountSession)
    if not include_superseded:
        stmt = stmt.where(CountSession.status.not_in(("superseded", "deleted")))
    return [_out(db, s) for s in db.scalars(stmt.order_by(CountSession.counted_at.desc()))]


@router.get("/template.xlsx")
def template(db: Session = Depends(get_db), count_date: date | None = None):
    return Response(xlsx_io.counts_template(db, count_date), media_type=XLSX,
                    headers={"Content-Disposition": 'attachment; filename="counts.xlsx"'})


@router.post("/import")
async def import_counts(file: UploadFile, db: Session = Depends(get_db)):
    loc = business_id(db)
    try:
        res = xlsx_io.import_counts(db, await file.read(), file.filename or "counts.xlsx", loc)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return res.summary()


@router.post("", response_model=CountSessionDetail, status_code=201)
def create_session(body: CountSessionIn, db: Session = Depends(get_db)):
    loc = business_id(db)
    s = CountSession(location_id=loc, counted_at=inv.to_utc(body.counted_at), note=body.note, status="draft", source="portal")
    db.add(s)
    db.flush()
    if body.prefill_active_items:
        # start an item at what should be left (last posted count minus recipe usage of Swype sales since)
        # only when the formula gives something; the rest stay blank until counted
        left = variance.expected_left(db, loc, s.counted_at)
        for it in db.scalars(select(InventoryItem).where(InventoryItem.is_active)):
            full, partial = _packs(it, left.get(it.id, Decimal(0)))
            if full + partial > 0:
                inv.set_count_line(db, s, it, full, partial)
    db.commit()
    db.refresh(s)
    return _detail(s, db)


@router.get("/{session_id}", response_model=CountSessionDetail)
def get_session(session_id: str, db: Session = Depends(get_db)):
    s = db.get(CountSession, session_id)
    if not s:
        raise HTTPException(404)
    return _detail(s, db)


class CountDateIn(BaseModel):
    counted_at: datetime
    reason: str | None = None


@router.patch("/{session_id}", response_model=CountSessionDetail)
def set_date(session_id: str, body: CountDateIn, db: Session = Depends(get_db)):
    """Move a count to another date and time, e.g. back-date it to when the stock was really counted. A draft
    just changes; a posted count gets a new posted version at the new time and the old one is kept as
    superseded history. Returns the count now in effect (a new id for a posted count)."""
    s = db.get(CountSession, session_id)
    if not s:
        raise HTTPException(404)
    if s.status == "draft":
        s.counted_at = inv.to_utc(body.counted_at)
        db.commit()
        db.refresh(s)
        return _detail(s, db)
    if s.status == "deleted":
        raise HTTPException(400, "this count was deleted")
    if s.status != "posted":
        raise HTTPException(400, "this count was replaced by a newer version; change that one instead")
    tz = ZoneInfo(settings.timezone)
    note = f"Date changed from {inv.to_utc(s.counted_at).astimezone(tz):%Y-%m-%d %H:%M}"
    note += f": {body.reason.strip()}" if body.reason and body.reason.strip() else ""
    if s.note:
        note += f" · {s.note}"
    try:
        new = inv.redate_count(db, s, body.counted_at, note)
    except inv.InventoryError as e:
        raise HTTPException(400, str(e)) from None
    db.commit()
    db.refresh(new)
    return _detail(new, db)


class CountCorrectionIn(BaseModel):
    """One item's new count on a posted count, in packs. Both blank takes the item out of the count."""
    item_id: str
    packs_full: Decimal | None = Field(default=None, ge=0)
    packs_partial: Decimal | None = Field(default=None, ge=0)
    reason: str | None = None


def _num(v: Decimal) -> str:
    return f"{v.normalize():f}"


@router.post("/{session_id}/corrections", response_model=CountSessionDetail)
def correct_line(session_id: str, body: CountCorrectionIn, db: Session = Depends(get_db)):
    """Correct one item on a posted count. The count gets a new posted version with that one line changed and
    the old version is kept as superseded history. Returns the count now in effect (a new id)."""
    s = db.get(CountSession, session_id)
    if not s:
        raise HTTPException(404)
    if s.status == "draft":
        raise HTTPException(400, "this count is a draft; change the number on the count itself")
    if s.status == "deleted":
        raise HTTPException(400, "this count was deleted")
    if s.status != "posted":
        raise HTTPException(400, "this count was replaced by a newer version; change that one instead")
    it = db.get(InventoryItem, body.item_id)
    if not it:
        raise HTTPException(400, f"unknown item {body.item_id}")
    old = next((ln for ln in s.lines if ln.item_id == it.id), None)
    was = "not counted" if old is None else f"{_num(Decimal(old.packs_full) + Decimal(old.packs_partial))} ({_num(Decimal(old.qty))} {it.stock_unit})"
    if body.packs_full is None and body.packs_partial is None:
        now = "not counted"
    else:
        packs = Decimal(body.packs_full or 0) + Decimal(body.packs_partial or 0)
        now = f"{_num(packs)} ({_num(packs * Decimal(it.pack_size))} {it.stock_unit})"
    note = f"{it.name} corrected from {was} to {now}"
    note += f": {body.reason.strip()}" if body.reason and body.reason.strip() else ""
    if s.note:
        note += f" · {s.note}"
    try:
        new = inv.correct_count_line(db, s, it, body.packs_full, body.packs_partial, note)
    except inv.InventoryError as e:
        db.rollback()
        raise HTTPException(400, str(e)) from None
    db.commit()
    db.refresh(new)
    return _detail(new, db)


@router.put("/{session_id}/lines", response_model=CountSessionDetail)
def set_lines(session_id: str, lines: list[CountLineIn], db: Session = Depends(get_db)):
    s = db.get(CountSession, session_id)
    if not s:
        raise HTTPException(404)
    if s.status != "draft":
        raise HTTPException(400, "count is posted; start a new count to correct it")
    try:
        for ln in lines:
            it = db.get(InventoryItem, ln.item_id)
            if not it:
                raise HTTPException(400, f"unknown item {ln.item_id}")
            if ln.packs_full is None and ln.packs_partial is None:
                # both fields cleared: not counted, so no line (posting ignores it)
                if (old := db.get(CountLine, (s.id, it.id))) is not None:
                    s.lines.remove(old)
                    db.delete(old)
                continue
            inv.set_count_line(db, s, it, ln.packs_full, ln.packs_partial, ln.note)
    except inv.InventoryError as e:
        raise HTTPException(400, str(e)) from None
    db.commit()
    db.refresh(s)
    return _detail(s, db)


@router.delete("/{session_id}/lines/{item_id}", response_model=CountSessionDetail)
def remove_line(session_id: str, item_id: str, db: Session = Depends(get_db)):
    s = db.get(CountSession, session_id)
    if not s or s.status != "draft":
        raise HTTPException(400, "not a draft")
    ln = db.get(CountLine, (session_id, item_id))
    if ln:
        db.delete(ln)
        db.commit()
    db.refresh(s)
    return _detail(s, db)


@router.post("/{session_id}/post", response_model=CountSessionDetail)
def post(session_id: str, db: Session = Depends(get_db)):
    s = db.get(CountSession, session_id)
    if not s:
        raise HTTPException(404)
    # only items with a number entered have a line; blank items are not part of the count
    try:
        inv.post_count(db, s)
    except inv.InventoryError as e:
        raise HTTPException(400, str(e)) from None
    db.commit()
    db.refresh(s)
    return _detail(s, db)


@router.delete("/{session_id}", status_code=204)
def delete_count(session_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Remove a draft, or flag a posted count "deleted": its rows stay, but variance and on-hand stop using it at once."""
    s = db.get(CountSession, session_id)
    if not s:
        raise HTTPException(404)
    if s.status != "draft" and user.session_mode == "pin":
        raise HTTPException(403, "Log in with your password to delete a posted count.")
    try:
        inv.delete_count(db, s)
    except inv.InventoryError as e:
        raise HTTPException(400, str(e)) from None
    db.commit()
