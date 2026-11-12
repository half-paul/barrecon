"""The businesses of a tenant (a bar, a restaurant): contact details, logo, Swype login and who may work there.

Everyone logged in can list the businesses they may enter and fetch their logos; only a tenant admin changes them.
A business is never deleted, only switched off: its counts, deliveries and audit trail stay.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import auth, tenancy
from ..db import get_db
from ..models import (BUSINESS_KINDS, BusinessLogo, BusinessMember, InventoryItem, ItemGroup, Location, Recipe, RecipeLine,
                      RecipeLineOption,
                      RecipeVersion, SwypeMenuItem, Tenant, User, utcnow)
from ..schemas import EMAIL
from ..services import secretbox
from .auth import business_out
from .users import _clean_permissions

router = APIRouter(prefix="/api/businesses", tags=["businesses"], dependencies=[auth.Auth])

LOGO_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}  # no SVG: it can carry script
LOGO_MAX_BYTES = 1024 * 1024
COPYABLE = ("items", "recipes")


class BusinessIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str | None = None
    address: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=254, pattern=EMAIL + "|^$")
    website: str | None = Field(default=None, max_length=200)
    swype_email: str | None = Field(default=None, max_length=254)
    swype_password: str | None = Field(default=None, max_length=200)  # write-only; "" removes it
    swype_company: str | None = Field(default=None, max_length=64)
    swype_store_id: int | None = Field(default=None, ge=0)
    # start the new business with another one's stock items and groups, and optionally its Swype items and recipes
    copy_from: str | None = None
    copy_what: list[str] = []  # any of COPYABLE; items and groups always come along


class BusinessPatch(BusinessIn):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    is_active: bool | None = None


class MemberIn(BaseModel):
    permissions: dict[str, str] = {}


def _get(db: Session, business_id: str) -> Location:
    b = db.get(Location, business_id)  # only this tenant's are visible (app.tenancy)
    if not b:
        raise HTTPException(404, "business not found")
    return b


def _admin_out(db: Session, b: Location) -> dict:
    members = db.query(func.count()).select_from(BusinessMember).filter(BusinessMember.location_id == b.id).scalar()
    return {**business_out(b), "swype_email": b.swype_email, "swype_company": b.swype_company, "swype_store_id": b.swype_store_id,
            "swype_password_set": bool(b.swype_password), "members": members, "created_at": b.created_at}


def _apply(b: Location, ch: dict) -> None:
    for k in ("name", "address", "city", "phone", "email", "website", "swype_email", "swype_company"):
        if k in ch:
            v = (ch[k] or "").strip() or None
            if k == "name" and not v:
                raise HTTPException(422, "A business needs a name.")
            setattr(b, k, v)
    if "kind" in ch:
        if ch["kind"] and ch["kind"] not in BUSINESS_KINDS:
            raise HTTPException(422, f"The kind is one of: {', '.join(BUSINESS_KINDS)}.")
        b.kind = ch["kind"] or None
    if "swype_store_id" in ch:
        b.swype_store_id = ch["swype_store_id"]
    if ch.get("swype_password") is not None:
        b.swype_password = secretbox.seal(ch["swype_password"]) if ch["swype_password"] else None


def _name_taken(db: Session, name: str, not_id: str | None = None) -> bool:
    q = db.query(Location).filter(func.lower(Location.name) == name.strip().lower())
    if not_id:
        q = q.filter(Location.id != not_id)
    return q.first() is not None


def _copy(db: Session, src: str, dst: str, what: list[str]) -> dict:
    """Stock items and their groups; with "recipes" also the Swype items and each recipe's portions as they are now.
    Counts, deliveries, sales and cost history stay behind: the new business starts its own."""
    out = {"items": 0, "groups": 0, "recipes": 0}
    db.info["also_business"] = dst
    try:
        _copy_rows(db, src, dst, what, out)
        db.flush()
    finally:
        db.info.pop("also_business", None)
    return out


def _copy_rows(db: Session, src: str, dst: str, what: list[str], out: dict) -> None:
    q = lambda model: db.query(model).filter(model.location_id == src).execution_options(unscoped=True)  # noqa: E731
    for g in q(ItemGroup):
        db.add(ItemGroup(location_id=dst, name=g.name, stock_unit=g.stock_unit, pack_name=g.pack_name, pack_size=g.pack_size,
                         case_size=g.case_size, sheet_in_cases=g.sheet_in_cases))
        out["groups"] += 1
    item_map: dict[str, str] = {}
    for it in q(InventoryItem):
        new = InventoryItem(location_id=dst, sku=it.sku, name=it.name, category=it.category, stock_unit=it.stock_unit,
                            pack_name=it.pack_name, pack_size=it.pack_size, case_size=it.case_size, pack_cost=it.pack_cost,
                            yield_to_unit=it.yield_to_unit, yield_qty=it.yield_qty, par_level=it.par_level, is_active=it.is_active)
        db.add(new)
        db.flush()
        item_map[it.id] = new.id
        out["items"] += 1
    if "recipes" in what:
        for m in q(SwypeMenuItem):
            db.add(SwypeMenuItem(location_id=dst, code=m.code, name=m.name, custom_name=m.custom_name, category=m.category,
                                 source=m.source, is_active=m.is_active))
        for r in q(Recipe):
            new = Recipe(location_id=dst, swype_code=r.swype_code, name=r.name, ignored=r.ignored, steps=r.steps, notes=r.notes)
            if r.versions:
                last = r.versions[-1]
                v = RecipeVersion(effective_from=r.versions[0].effective_from)
                for ln in last.lines:
                    ids = [item_map[i] for i in ln.item_ids if i in item_map]
                    if ids:
                        v.lines.append(RecipeLine(item_id=ids[0], qty=ln.qty, unit=ln.unit, position=ln.position,
                                                  options=[RecipeLineOption(item_id=i, position=k)
                                                           for k, i in enumerate(ids[1:], 1)]))
                new.versions = [v]
            db.add(new)
            out["recipes"] += 1


@router.get("")
def list_businesses(me: User = auth.Auth, db: Session = Depends(get_db)):
    """An admin gets every business of the tenant, switched-off ones included, with its settings; everyone else the
    ones they may enter."""
    if auth.is_admin(me):
        return [_admin_out(db, b) for b in db.query(Location).order_by(Location.is_active.desc(), func.lower(Location.name))]
    return [business_out(b) for b in tenancy.businesses_of(db, me)]


@router.get("/kinds")
def kinds():
    return list(BUSINESS_KINDS)


@router.post("", status_code=201, dependencies=[auth.Admin])
def create_business(body: BusinessIn, me: User = auth.Admin, db: Session = Depends(get_db)):
    t = db.get(Tenant, me.tenant_id)
    if t.max_businesses is not None and db.query(Location).filter(Location.is_active.is_(True)).count() >= t.max_businesses:
        raise HTTPException(409, f"This account is limited to {t.max_businesses} businesses. Ask for a higher limit.")
    if _name_taken(db, body.name):
        raise HTTPException(409, f"There is already a business called {body.name.strip()}.")
    for w in body.copy_what:
        if w not in COPYABLE:
            raise HTTPException(422, f"Can't copy '{w}'.")
    b = Location(tenant_id=me.tenant_id, name=body.name.strip())
    _apply(b, body.model_dump(exclude_unset=True, exclude={"copy_from", "copy_what", "name"}))
    db.add(b)
    db.flush()
    copied = None
    if body.copy_from:
        src = _get(db, body.copy_from)
        copied = _copy(db, src.id, b.id, body.copy_what)
    db.commit()
    return {**_admin_out(db, b), "copied": copied}


@router.patch("/{business_id}", dependencies=[auth.Admin])
def update_business(business_id: str, body: BusinessPatch, db: Session = Depends(get_db)):
    b = _get(db, business_id)
    ch = body.model_dump(exclude_unset=True, exclude={"copy_from", "copy_what"})
    if ch.get("name") and _name_taken(db, ch["name"], b.id):
        raise HTTPException(409, f"There is already a business called {ch['name'].strip()}.")
    _apply(b, ch)
    if ch.get("is_active") is not None and ch["is_active"] != b.is_active:
        if not ch["is_active"] and db.query(Location).filter(Location.is_active.is_(True), Location.id != b.id).count() == 0:
            raise HTTPException(409, "This is the only active business. Add another before switching it off.")
        if ch["is_active"]:
            t = db.get(Tenant, b.tenant_id)
            if t.max_businesses is not None and db.query(Location).filter(Location.is_active.is_(True)).count() >= t.max_businesses:
                raise HTTPException(409, f"This account is limited to {t.max_businesses} businesses.")
        b.is_active = ch["is_active"]
    db.commit()
    return _admin_out(db, b)


# ---- logo ---------------------------------------------------------------------------------------------------

@router.get("/{business_id}/logo")
def get_logo(business_id: str, me: User = auth.Auth, db: Session = Depends(get_db)):
    """For anyone who may enter the business. The page asks with ?v=<logo_at>, so a changed logo is a new address."""
    if not auth.is_admin(me) and not any(b.id == business_id for b in tenancy.businesses_of(db, me)):
        raise HTTPException(404)
    logo = db.get(BusinessLogo, _get(db, business_id).id)
    if not logo:
        raise HTTPException(404)
    return Response(logo.data, media_type=logo.content_type,
                    headers={"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"})


@router.put("/{business_id}/logo", dependencies=[auth.Admin])
async def put_logo(business_id: str, file: UploadFile, db: Session = Depends(get_db)):
    b = _get(db, business_id)
    data, ctype = await file.read(), file.content_type or ""
    if ctype not in LOGO_TYPES:
        raise HTTPException(400, "The logo must be a PNG, JPEG, WebP or GIF image.")
    if not data:
        raise HTTPException(400, "The file is empty.")
    if len(data) > LOGO_MAX_BYTES:
        raise HTTPException(400, "The logo is over 1 MB. Use a smaller image.")
    logo = db.get(BusinessLogo, b.id)
    if logo:
        logo.content_type, logo.data = ctype, data
    else:
        db.add(BusinessLogo(location_id=b.id, content_type=ctype, data=data))
    b.logo_at = utcnow()
    db.commit()
    return _admin_out(db, b)


@router.delete("/{business_id}/logo", dependencies=[auth.Admin])
def delete_logo(business_id: str, db: Session = Depends(get_db)):
    b = _get(db, business_id)
    logo = db.get(BusinessLogo, b.id)
    if logo:
        db.delete(logo)
    b.logo_at = None
    db.commit()
    return _admin_out(db, b)


# ---- who may work there -------------------------------------------------------------------------------------

@router.get("/{business_id}/members", dependencies=[auth.Admin])
def members(business_id: str, db: Session = Depends(get_db)):
    """Every login of the tenant: admins reach the business anyway; the rest are members or not."""
    b = _get(db, business_id)
    have = {m.user_id: m for m in db.query(BusinessMember).filter(BusinessMember.location_id == b.id)}
    return [{"user_id": u.id, "username": u.username, "display_name": u.display_name, "is_admin": u.is_admin,
             "is_active": u.is_active, "member": u.is_admin or u.id in have,
             "permissions": dict(have[u.id].permissions or {}) if u.id in have else {}}
            for u in db.query(User).order_by(User.is_active.desc(), func.lower(User.display_name))]


@router.put("/{business_id}/members/{user_id}", dependencies=[auth.Admin])
def set_member(business_id: str, user_id: str, body: MemberIn, db: Session = Depends(get_db)):
    b = _get(db, business_id)
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "user not found")
    perms = _clean_permissions(body.permissions)
    m = db.get(BusinessMember, (u.id, b.id))
    if m:
        m.permissions = perms
    else:
        db.add(BusinessMember(user_id=u.id, location_id=b.id, permissions=perms))
    db.commit()
    return {"ok": True}


@router.delete("/{business_id}/members/{user_id}", dependencies=[auth.Admin])
def remove_member(business_id: str, user_id: str, db: Session = Depends(get_db)):
    b = _get(db, business_id)
    m = db.get(BusinessMember, (user_id, b.id)) if db.get(User, user_id) else None
    if m:
        db.delete(m)
        db.commit()
    return {"ok": True}
