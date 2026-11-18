from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import perm
from ..db import get_db
from ..tenancy import business_id
from ..services import recipe_import, recipe_match
from ..services import recipes as rec
from ..services import settings_svc

router = APIRouter(prefix="/api/recipes", tags=["recipes"], dependencies=[perm("recipes")])


class RecipeLineIn(BaseModel):
    item_id: str | None = None  # one item; or item_ids: the portion comes out of any one of them
    item_ids: list[str] = []
    qty: Decimal = Field(gt=0)
    unit: str = "oz"


class RecipeIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    lines: list[RecipeLineIn] = []
    effective_from: date | None = None  # None = correct the recipe in effect; a date = it changed that day
    ignored: bool = False
    steps: str | None = None
    notes: str | None = None


class SuggestIn(BaseModel):
    single_oz: Decimal = Field(default=Decimal(1), gt=0)
    double_oz: Decimal = Field(default=Decimal(2), gt=0)
    apply: bool = False


class MatchRowIn(BaseModel):
    code: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    lines: list[RecipeLineIn] = []
    ignored: bool = False


class MatchApproveIn(BaseModel):
    rows: list[MatchRowIn] = Field(min_length=1)


@router.get("/menu")
def menu(db: Session = Depends(get_db)):
    return rec.menu(db, business_id(db))


@router.post("/suggest-pours")
def suggest_pours(body: SuggestIn, db: Session = Depends(get_db)):
    return rec.suggest_pours(db, business_id(db), single_oz=body.single_oz, double_oz=body.double_oz,
                             apply=body.apply)


@router.get("/match")
def match_queue(db: Session = Depends(get_db)):
    return recipe_match.queue(db, business_id(db))


@router.post("/match/approve")
def match_approve(body: MatchApproveIn, db: Session = Depends(get_db)):
    try:
        n = recipe_match.approve(db, [{**r.model_dump(), "lines": [ln.model_dump() for ln in r.lines]} for r in body.rows])
    except rec.RecipeError as e:
        db.rollback()
        raise HTTPException(400, str(e)) from None
    db.commit()
    return {"saved": n}


@router.post("/no-stock")
def no_stock(db: Session = Depends(get_db)):
    n = recipe_match.apply_no_stock(db, business_id(db))
    db.commit()
    return {"saved": n}


@router.get("/template.xlsx")
def template(db: Session = Depends(get_db)):
    return Response(recipe_import.sample_workbook(db),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="Recipes-sample.xlsx"'})


@router.post("/import")
async def import_recipes(file: UploadFile, db: Session = Depends(get_db)):
    try:
        res = recipe_import.import_recipes(db, await file.read(), file.filename or "recipes.xlsx")
    except (ValueError, rec.RecipeError) as e:
        db.rollback()
        raise HTTPException(400, str(e)) from None
    return res.summary()


@router.get("/{code}")
def get_recipe(code: str, db: Session = Depends(get_db)):
    r = rec.load(db, code)
    if r is None:
        raise HTTPException(404)
    return rec.recipe_out(r, settings_svc.oz_ml(db))


@router.put("/{code}")
def put_recipe(code: str, body: RecipeIn, db: Session = Depends(get_db)):
    try:
        r = rec.save(db, code, name=body.name, lines=[ln.model_dump() for ln in body.lines],
                     effective_from=body.effective_from, ignored=body.ignored, steps=body.steps,
                     notes=body.notes)
    except rec.RecipeError as e:
        raise HTTPException(400, str(e)) from None
    db.commit()
    return rec.recipe_out(rec.load(db, r.swype_code), settings_svc.oz_ml(db))


@router.delete("/{code}", status_code=204)
def delete_recipe(code: str, db: Session = Depends(get_db)):
    if not rec.delete(db, code):
        raise HTTPException(404)
    db.commit()


@router.get("/{code}/photo")
def get_photo(code: str, db: Session = Depends(get_db)):
    r = rec.load(db, code)
    if r is None or r.photo is None:
        raise HTTPException(404)
    return Response(r.photo.data, media_type=r.photo.content_type, headers={"Cache-Control": "private, max-age=86400"})


@router.put("/{code}/photo")
async def put_photo(code: str, file: UploadFile, db: Session = Depends(get_db)):
    r = rec.load(db, code)
    if r is None:
        raise HTTPException(404, "save the recipe before adding a photo")
    try:
        rec.set_photo(db, r, await file.read(), file.content_type or "")
    except rec.RecipeError as e:
        raise HTTPException(400, str(e)) from None
    db.commit()
    return rec.recipe_out(rec.load(db, code), settings_svc.oz_ml(db))


@router.delete("/{code}/photo", status_code=204)
def delete_photo(code: str, db: Session = Depends(get_db)):
    r = rec.load(db, code)
    if r is None or r.photo is None:
        raise HTTPException(404)
    r.photo = None
    db.commit()
