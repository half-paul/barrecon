from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginIn(BaseModel):
    username: str = "admin"
    password: str | None = None
    pin: str | None = Field(default=None, max_length=8)

    @model_validator(mode="after")
    def _one_secret(self):
        if (self.password is None) == (self.pin is None):
            raise ValueError("give a password or a PIN")
        return self


class PinChangeIn(BaseModel):
    current_password: str
    pin: str | None = None  # None removes it


class PinSetIn(BaseModel):
    pin: str


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)


class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=200)
    is_admin: bool = False
    permissions: dict[str, str] = {}


class UserPatch(BaseModel):
    username: str | None = Field(default=None, min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    is_admin: bool | None = None
    is_active: bool | None = None
    permissions: dict[str, str] | None = None


class PasswordResetIn(BaseModel):
    password: str = Field(min_length=8, max_length=200)


class UserOut(ORM):
    id: str
    username: str
    display_name: str
    is_admin: bool
    is_active: bool
    permissions: dict[str, str]
    has_pin: bool = False
    locked: bool = False
    last_login_at: datetime | None
    password_changed_at: datetime | None
    created_at: datetime


class AuditOut(ORM):
    id: str
    at: datetime
    user_id: str | None
    username: str | None
    action: str
    area: str | None
    method: str | None
    route: str | None
    path: str | None
    status: int | None
    ip: str | None
    detail: dict | None
    label: str = ""
    link: str | None = None
    changes: int = 0


class ItemIn(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    category: str | None = None
    stock_unit: str
    pack_name: str | None = None
    pack_size: Decimal = Field(gt=0)
    case_size: Decimal = Field(default=Decimal(1), gt=0)
    pack_cost: Decimal | None = Field(default=None, ge=0)
    yield_to_unit: str | None = None
    yield_qty: Decimal | None = Field(default=None, gt=0)
    par_level: Decimal | None = Field(default=None, ge=0)
    is_active: bool = True


class ItemPatch(BaseModel):
    sku: str | None = Field(default=None, max_length=64)
    name: str | None = None
    category: str | None = None
    stock_unit: str | None = None
    pack_name: str | None = None
    pack_size: Decimal | None = Field(default=None, gt=0)
    case_size: Decimal | None = Field(default=None, gt=0)
    pack_cost: Decimal | None = Field(default=None, ge=0)
    yield_to_unit: str | None = None
    yield_qty: Decimal | None = None
    par_level: Decimal | None = None
    is_active: bool | None = None


class CostOut(ORM):
    effective_from: datetime
    pack_cost: Decimal
    source: str


class ItemOut(ORM):
    id: str
    sku: str
    name: str
    category: str | None
    stock_unit: str
    pack_name: str | None
    pack_size: Decimal
    case_size: Decimal
    pack_cost: Decimal | None
    unit_cost: Decimal | None
    yield_to_unit: str | None
    yield_qty: Decimal | None
    par_level: Decimal | None
    is_active: bool
    updated_at: datetime
    last_counted_packs: Decimal | None = None
    last_counted_at: datetime | None = None


class CountLineIn(BaseModel):
    """A blank field is None. Both blank means the item is not counted and has no line."""
    item_id: str
    packs_full: Decimal | None = Field(default=None, ge=0)
    packs_partial: Decimal | None = Field(default=None, ge=0)
    note: str | None = None


class CountSessionIn(BaseModel):
    counted_at: datetime
    note: str | None = None
    prefill_active_items: bool = True


class CountLineOut(ORM):
    item_id: str
    sku: str
    name: str
    category: str | None
    pack_name: str | None
    stock_unit: str
    pack_size: Decimal
    packs_full: Decimal | None  # None on a draft for an active item not counted yet
    packs_partial: Decimal | None
    qty: Decimal | None
    note: str | None


class CountSessionOut(ORM):
    id: str
    counted_at: datetime
    status: str
    source: str
    note: str | None
    posted_at: datetime | None
    superseded_by: str | None
    line_count: int = 0


class CountSessionDetail(CountSessionOut):
    lines: list[CountLineOut]
    versions: list[CountSessionOut] = []


class MovementIn(BaseModel):
    item_id: str
    kind: str
    occurred_at: datetime
    external_ref: str = Field(min_length=1, max_length=200)
    packs: Decimal | None = Field(default=None, gt=0)
    qty: Decimal | None = Field(default=None, gt=0)
    pack_cost: Decimal | None = Field(default=None, ge=0)
    note: str | None = None


class DeliveryLineIn(BaseModel):
    item_id: str
    packs: Decimal = Field(gt=0)
    pack_cost: Decimal | None = Field(default=None, ge=0)


class DeliveryIn(BaseModel):
    """One invoice (or transfer, return, breakage) covering many items, entered on one sheet."""
    kind: str = "delivery"
    occurred_at: datetime
    external_ref: str = Field(min_length=1, max_length=200)
    note: str | None = None
    lines: list[DeliveryLineIn] = Field(min_length=1)


class MovementOut(ORM):
    id: str
    item_id: str
    sku: str
    name: str
    kind: str
    qty: Decimal
    packs: Decimal | None
    pack_cost: Decimal | None
    occurred_at: datetime
    external_ref: str
    source: str
    status: str
    note: str | None
    delivery_id: str | None = None


class BatchOut(ORM):
    id: str
    source: str
    dataset: str
    file_name: str | None
    rows_received: int
    rows_inserted: int
    rows_duplicate: int
    rows_error: int
    status: str
    error_log: list | dict | None
    imported_at: datetime


class IssueOut(ORM):
    id: str
    code: str
    entity: str | None
    detail: dict | None
    resolved: bool
    created_at: datetime


class SettingsOut(BaseModel):
    oz_ml: Decimal
    variance_threshold_pct: Decimal
    count_weekday: int
    count_reminder_enabled: bool
    variance_chart_items: list[str]
    variance_view_added: list[str]
    variance_view_removed: list[str]
    swype_auto_import: bool = True
    swype_auto_import_hour: int = 5
    idle_logout_minutes: int = 30
    audit_retention_days: int = 365


class SettingsIn(BaseModel):
    oz_ml: Decimal | None = Field(default=None, gt=0)
    variance_threshold_pct: Decimal | None = Field(default=None, ge=0)
    count_weekday: int | None = Field(default=None, ge=0, le=6)
    count_reminder_enabled: bool | None = None
    variance_chart_items: list[str] | None = None
    variance_view_added: list[str] | None = None
    variance_view_removed: list[str] | None = None
    swype_auto_import: bool | None = None
    swype_auto_import_hour: int | None = Field(default=None, ge=0, le=23)
    idle_logout_minutes: int | None = Field(default=None, ge=0, le=720)  # 0: only the 12-hour session limit
    audit_retention_days: int | None = Field(default=None, ge=0, le=3650)  # 0: keep forever

    @model_validator(mode="after")
    def _retention(self):
        if self.audit_retention_days is not None and 0 < self.audit_retention_days < 30:
            raise ValueError("keep the audit log at least 30 days (or 0 to keep it forever)")
        return self


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    stock_unit: str
    pack_name: str | None = Field(default=None, max_length=32)
    pack_size: Decimal = Field(gt=0)
    case_size: Decimal = Field(default=Decimal(1), gt=0)


class GroupPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    stock_unit: str | None = None
    pack_name: str | None = Field(default=None, max_length=32)
    pack_size: Decimal | None = Field(default=None, gt=0)
    case_size: Decimal | None = Field(default=None, gt=0)


class GroupOut(ORM):
    name: str
    stock_unit: str
    pack_name: str | None
    pack_size: Decimal
    case_size: Decimal
    item_count: int = 0
    active_count: int = 0
    differing: int = 0  # items whose unit or pack differs from the group's defaults


class GroupApplyIn(BaseModel):
    fields: list[str] = Field(min_length=1)  # any of stock_unit, pack_name, pack_size, case_size


class ReceiptLineIn(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    receipt_sku: str | None = Field(default=None, max_length=64)
    receipt_qty: Decimal = Field(default=Decimal(1), ge=0)
    line_total: Decimal | None = None  # a discount line can be negative
    item_id: str | None = None
    packs: Decimal | None = Field(default=None, ge=0)
    skip: bool = False


class ReceiptDeliveryIn(BaseModel):
    """The reviewed receipt: header and every line as it should be posted."""
    store: str | None = Field(default=None, max_length=200)
    store_address: str | None = Field(default=None, max_length=300)
    occurred_at: datetime | None = None
    receipt_no: str | None = Field(default=None, max_length=100)
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    note: str | None = None
    lines: list[ReceiptLineIn] = []
