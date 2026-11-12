"""Schema: reference data, inventory master, counts, movements, imports, settings, Swype sales totals, Swype menu, recipes,
users and the audit log.

IDs are UUID4 strings so the same models run on SQLite (tests) and PostgreSQL (runtime).
Quantities are Numeric(14, 4) in the item's stock unit.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


MOVEMENT_KINDS = (
    "delivery",
    "transfer_in",
    "transfer_out",
    "supplier_return",
    "breakage_expired",
    "other_explained",
)
INBOUND_KINDS = ("delivery", "transfer_in")
OUTBOUND_KINDS = ("transfer_out", "supplier_return")
EXPLAINED_KINDS = ("breakage_expired", "other_explained")


class Tenant(Base):
    """A customer of the platform: a company with one or more businesses and its own users. Managed from the
    platform console (/platform). Limits left empty mean no limit."""
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | suspended
    plan: Mapped[str | None] = mapped_column(String(64))
    contact_name: Mapped[str | None] = mapped_column(String(120))
    contact_email: Mapped[str | None] = mapped_column(String(254))
    notes: Mapped[str | None] = mapped_column(Text)
    max_businesses: Mapped[int | None] = mapped_column(Integer)
    max_users: Mapped[int | None] = mapped_column(Integer)  # active logins
    monthly_token_cap: Mapped[int | None] = mapped_column(BigInteger)  # Claude tokens (in + out) per calendar month
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


BUSINESS_KINDS = ("bar", "restaurant", "cafe", "other")


class Location(Base):
    """A business of a tenant (a bar, a restaurant). Everything stocked, counted, delivered and sold belongs to one
    business through location_id; app.tenancy keeps each request inside the business it is working in."""
    __tablename__ = "locations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str | None] = mapped_column(String(32))  # one of BUSINESS_KINDS
    address: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(254))
    website: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    logo_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # when the logo last changed; null: none
    swype_location_id: Mapped[str | None] = mapped_column(String(64))
    # SwypePOS back office login of this business (services/swype_api.py); the password is encrypted (services/secretbox.py)
    swype_email: Mapped[str | None] = mapped_column(String(254))
    swype_password: Mapped[str | None] = mapped_column(Text)
    swype_company: Mapped[str | None] = mapped_column(String(64))
    swype_store_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow)


Business = Location


class BusinessLogo(Base):
    """One logo per business, kept in the database so the nightly pg_dump backs it up."""
    __tablename__ = "business_logos"
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), primary_key=True)
    content_type: Mapped[str] = mapped_column(String(64))
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class BusinessMember(Base):
    """A user's access to one business: a level per area, as User.permissions used to hold for the single bar.
    Tenant admins reach every business of their tenant without a row here."""
    __tablename__ = "business_members"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), primary_key=True, index=True)
    permissions: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AiUsage(Base):
    """One Claude API call (reading a receipt), metered for the platform console: tokens and what they cost."""
    __tablename__ = "ai_usage"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    tenant_id: Mapped[str | None] = mapped_column(String(36))
    location_id: Mapped[str | None] = mapped_column(String(36))
    user_id: Mapped[str | None] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(32))  # receipt
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)  # uncached + cache writes + cache reads
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    ref: Mapped[str | None] = mapped_column(String(64))  # the delivery it was read for

    __table_args__ = (Index("ix_ai_usage_tenant_at", "tenant_id", "at"),)


class Unit(Base):
    __tablename__ = "units"
    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    dimension: Mapped[str] = mapped_column(String(16))  # volume | mass | count
    to_base: Mapped[Decimal] = mapped_column(Numeric(14, 6))


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.id"))
    sku: Mapped[str] = mapped_column(String(64))  # unique within a business
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(64))
    stock_unit: Mapped[str] = mapped_column(ForeignKey("units.code"))
    pack_name: Mapped[str | None] = mapped_column(String(32))
    pack_size: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal(1))
    case_size: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal(1))
    pack_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    yield_to_unit: Mapped[str | None] = mapped_column(ForeignKey("units.code"))
    yield_qty: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    par_level: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    costs: Mapped[list[InventoryItemCost]] = relationship(back_populates="item", cascade="all, delete-orphan")

    __table_args__ = (Index("uq_item_loc_sku", "location_id", "sku", unique=True),)

    @property
    def unit_cost(self) -> Decimal | None:
        if self.pack_cost is None or not self.pack_size:
            return None
        return Decimal(self.pack_cost) / Decimal(self.pack_size)


class ItemGroup(Base):
    """A stock item group ("Spirits", "Draft beer"). Items point at it by name (inventory_items.category).
    The unit and pack fields are defaults for new items in the group; "apply" pushes them onto existing items.
    sheet_in_cases: on the uploaded deliveries sheet, the packs number of an item in this group counts cases."""
    __tablename__ = "item_groups"
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    stock_unit: Mapped[str] = mapped_column(ForeignKey("units.code"))
    pack_name: Mapped[str | None] = mapped_column(String(32))
    pack_size: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal(1))
    case_size: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal(1))
    sheet_in_cases: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InventoryItemCost(Base):
    __tablename__ = "inventory_item_costs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(ForeignKey("inventory_items.id"))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    pack_cost: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    source: Mapped[str] = mapped_column(String(16))  # manual | delivery | sheet | legacy
    item: Mapped[InventoryItem] = relationship(back_populates="costs")


class CountSession(Base):
    __tablename__ = "count_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"))
    counted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | posted | superseded | deleted
    source: Mapped[str] = mapped_column(String(16), default="portal")  # portal | xlsx | legacy
    external_ref: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[str | None] = mapped_column(ForeignKey("count_sessions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    lines: Mapped[list[CountLine]] = relationship(back_populates="session", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_count_sessions_loc_at", "location_id", "counted_at"),)


class CountLine(Base):
    __tablename__ = "count_lines"
    session_id: Mapped[str] = mapped_column(ForeignKey("count_sessions.id"), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("inventory_items.id"), primary_key=True)
    packs_full: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal(0))
    packs_partial: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal(0))
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 4))  # derived, stock units
    note: Mapped[str | None] = mapped_column(Text)

    session: Mapped[CountSession] = relationship(back_populates="lines")
    item: Mapped[InventoryItem] = relationship()


class StockMovement(Base):
    __tablename__ = "stock_movements"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"))
    item_id: Mapped[str] = mapped_column(ForeignKey("inventory_items.id"))
    kind: Mapped[str] = mapped_column(String(32))
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 4))  # positive magnitude, stock units
    packs: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    pack_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    external_ref: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(16), default="portal")  # portal | xlsx | legacy | sheet
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | superseded | retracted
    note: Mapped[str | None] = mapped_column(Text)
    import_batch_id: Mapped[str | None] = mapped_column(ForeignKey("import_batches.id"))
    delivery_id: Mapped[str | None] = mapped_column(ForeignKey("deliveries.id"))  # set when posted from a delivery
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    item: Mapped[InventoryItem] = relationship()

    __table_args__ = (
        UniqueConstraint("location_id", "item_id", "kind", "external_ref", name="uq_movement_ref"),
        Index("ix_movements_item_at", "item_id", "occurred_at"),
    )


class Delivery(Base):
    """One purchase: a store receipt or supplier invoice. A draft is reviewed (lines matched to stock items,
    totals checked) and posting writes one delivery movement per item. Reopening a posted delivery retracts
    those movements so it can be corrected and posted again; voiding retracts them for good."""
    __tablename__ = "deliveries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"))
    store: Mapped[str | None] = mapped_column(String(200))
    store_address: Mapped[str | None] = mapped_column(String(300))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # date and time on the receipt
    receipt_no: Mapped[str | None] = mapped_column(String(100))
    subtotal: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    tax: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | posted | void
    source: Mapped[str] = mapped_column(String(16), default="receipt")  # receipt | manual | sheet
    note: Mapped[str | None] = mapped_column(Text)
    read_error: Mapped[str | None] = mapped_column(Text)  # why the receipt couldn't be read, if it couldn't
    extracted: Mapped[dict | None] = mapped_column(JSON)  # what was read off the receipt, as read
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    lines: Mapped[list[DeliveryLine]] = relationship(back_populates="delivery", cascade="all, delete-orphan",
                                                     order_by="DeliveryLine.position")
    receipt: Mapped[DeliveryReceipt | None] = relationship(back_populates="delivery", cascade="all, delete-orphan",
                                                           uselist=False, lazy="selectin")


class DeliveryReceipt(Base):
    """The uploaded receipt (photo or PDF), kept in the database so the nightly pg_dump backs it up."""
    __tablename__ = "delivery_receipts"
    delivery_id: Mapped[str] = mapped_column(ForeignKey("deliveries.id"), primary_key=True)
    content_type: Mapped[str] = mapped_column(String(64))
    file_name: Mapped[str | None] = mapped_column(String(255))
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    delivery: Mapped[Delivery] = relationship(back_populates="receipt")


class DeliveryLine(Base):
    """One line of a receipt as printed, and what it puts into stock: an item and a number of packs.
    Lines that aren't stock (bags, deposits, snacks) are kept with skip=True so the totals still add up."""
    __tablename__ = "delivery_lines"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    delivery_id: Mapped[str] = mapped_column(ForeignKey("deliveries.id"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(String(300))  # as printed: "SMIRNOFF VODKA 1.14L"
    receipt_sku: Mapped[str | None] = mapped_column(String(64))  # the store's product number, as printed
    receipt_qty: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal(1))
    line_total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))  # after line discounts, before tax
    item_id: Mapped[str | None] = mapped_column(ForeignKey("inventory_items.id"))
    packs: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))  # packs of the item this line adds
    skip: Mapped[bool] = mapped_column(Boolean, default=False)
    matched_by: Mapped[str | None] = mapped_column(String(16))  # sku | memory | rule | hand

    delivery: Mapped[Delivery] = relationship(back_populates="lines")
    item: Mapped[InventoryItem | None] = relationship()


class ReceiptAlias(Base):
    """What a store's receipt line means, learned when a delivery is posted: the line text and SKU at that store
    is this item, and one of it on the receipt is `packs_per_unit` packs (a 24-pack line into cans, say).
    It only pre-fills the next receipt; every line is still reviewed before posting."""
    __tablename__ = "receipt_aliases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.id"))
    store_key: Mapped[str] = mapped_column(String(200))
    text_key: Mapped[str] = mapped_column(String(300))
    sku: Mapped[str | None] = mapped_column(String(64))  # the line's product number (sku_key), when it has one
    item_id: Mapped[str | None] = mapped_column(ForeignKey("inventory_items.id"))  # None: a line to skip
    packs_per_unit: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal(1))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    # one store prints the same words for different products (three SIPS wines), told apart by SKU
    __table_args__ = (Index("uq_receipt_alias_loc_line", "location_id", "store_key", "text_key", text("coalesce(sku, '')"), unique=True),)


class ImportBatch(Base):
    __tablename__ = "import_batches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.id"))
    source: Mapped[str] = mapped_column(String(16))  # xlsx | legacy | swype_file | swype_api | sheet | portal
    dataset: Mapped[str] = mapped_column(String(16))  # items | counts | movements | legacy | sales | modifiers
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    period_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rows_received: Mapped[int] = mapped_column(Integer, default=0)
    rows_inserted: Mapped[int] = mapped_column(Integer, default=0)
    rows_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    rows_error: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok | rejected | partial | removed (its sales were deleted later)
    error_log: Mapped[list | dict | None] = mapped_column(JSON)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SwypeItemSales(Base):
    """Per-item totals from Swype's "Item Sales" report for one date range (both ends inclusive, local dates).

    The report is a summary, not ticket lines, so one row per (period, Swype item #). Re-importing the
    same period replaces its rows; overlapping periods are rejected so totals are never counted twice.
    """
    __tablename__ = "swype_item_sales"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"))
    period_from: Mapped[date] = mapped_column(Date)
    period_to: Mapped[date] = mapped_column(Date)
    department: Mapped[str] = mapped_column(String(64))  # Food | Beverage | Liquor
    category: Mapped[str] = mapped_column(String(120))  # Draft Beer, Booze, White Wine, ...
    item_code: Mapped[str] = mapped_column(String(32))  # Swype item #, e.g. 00000330_02 (size variant suffix)
    description: Mapped[str] = mapped_column(String(200))
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    combo_qty: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal(0))
    promo_qty: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal(0))
    free_qty: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal(0))
    total_qty: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    net_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    import_batch_id: Mapped[str] = mapped_column(ForeignKey("import_batches.id"))

    __table_args__ = (
        UniqueConstraint("location_id", "period_from", "period_to", "item_code", name="uq_swype_item_sales"),
        Index("ix_swype_item_sales_period", "location_id", "period_from"),
    )


class SwypeMenuItem(Base):
    """A Swype item # (what is rung in: "Caesar", "Smirnoff Vodka DBL", "Kokanee Btl"). Not an inventory item:
    Swype items and inventory items have their own numbers and names, and the only bridge between them is a
    recipe (one Swype item -> one or more inventory items, each with its own portion)."""
    __tablename__ = "swype_menu_items"
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), primary_key=True)
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))  # as Swype has it; every import refreshes it
    custom_name: Mapped[str | None] = mapped_column(String(200))  # our rename; imports leave it alone
    category: Mapped[str | None] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(16))  # sales | csv (where it was first seen)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())  # a sale switches it back on
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Recipe(Base):
    """What one Swype item # (a cocktail, a pour, a pint) takes out of inventory. Versioned so a recipe
    change only applies from its date; past weeks keep the portions they were sold with."""
    __tablename__ = "recipes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.id"))
    swype_code: Mapped[str] = mapped_column(String(32))  # unique within a business
    name: Mapped[str] = mapped_column(String(200))
    ignored: Mapped[bool] = mapped_column(Boolean, default=False)  # sold item that takes no tracked stock
    steps: Mapped[str | None] = mapped_column(Text)  # preparation: untracked ingredients, glass, garnish, method
    notes: Mapped[str | None] = mapped_column(Text)  # why the portions are what they are (pre-batch scaling, house wine box)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    versions: Mapped[list[RecipeVersion]] = relationship(back_populates="recipe", cascade="all, delete-orphan",
                                                         order_by="RecipeVersion.effective_from")
    photo: Mapped[RecipePhoto | None] = relationship(back_populates="recipe", cascade="all, delete-orphan",
                                                     uselist=False, lazy="selectin")

    __table_args__ = (Index("uq_recipe_loc_code", "location_id", "swype_code", unique=True),)


class RecipePhoto(Base):
    """One photo per recipe (the drink as served), kept in the database so the nightly pg_dump backs it up."""
    __tablename__ = "recipe_photos"
    recipe_id: Mapped[str] = mapped_column(ForeignKey("recipes.id"), primary_key=True)
    content_type: Mapped[str] = mapped_column(String(64))
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    recipe: Mapped[Recipe] = relationship(back_populates="photo")


class RecipeVersion(Base):
    __tablename__ = "recipe_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    recipe_id: Mapped[str] = mapped_column(ForeignKey("recipes.id"))
    effective_from: Mapped[date] = mapped_column(Date)  # local date; the first version starts at RECIPE_EPOCH
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    recipe: Mapped[Recipe] = relationship(back_populates="versions")
    lines: Mapped[list[RecipeLine]] = relationship(back_populates="version", cascade="all, delete-orphan",
                                                   order_by="RecipeLine.position")

    __table_args__ = (UniqueConstraint("recipe_id", "effective_from", name="uq_recipe_version_from"),)


class RecipeLine(Base):
    """One portion of a recipe: qty/unit of one stock item, or of any one of several ("6 oz of Box A or Box B or
    Box C"). item_id is the first one listed; the others are in `options`. The order means nothing: no item is
    the main one, since Swype never says which was poured. A line with options makes its items one pool on the
    variance report (services/variance.py)."""
    __tablename__ = "recipe_lines"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(ForeignKey("recipe_versions.id"))
    item_id: Mapped[str] = mapped_column(ForeignKey("inventory_items.id"))
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    unit: Mapped[str] = mapped_column(ForeignKey("units.code"))  # oz | ml | each | ... ; converted at run time
    position: Mapped[int] = mapped_column(Integer, default=0)

    version: Mapped[RecipeVersion] = relationship(back_populates="lines")
    item: Mapped[InventoryItem] = relationship()
    options: Mapped[list[RecipeLineOption]] = relationship(back_populates="line", cascade="all, delete-orphan",
                                                           order_by="RecipeLineOption.position", lazy="selectin")

    @property
    def items(self) -> list[InventoryItem]:
        """Every item the portion can come out of, first one first."""
        return [self.item, *(o.item for o in self.options)]

    @property
    def item_ids(self) -> list[str]:
        return [self.item_id, *(o.item_id for o in self.options)]


class RecipeLineOption(Base):
    """The second and later items of a recipe line that can be poured from any one of several items."""
    __tablename__ = "recipe_line_options"
    line_id: Mapped[str] = mapped_column(ForeignKey("recipe_lines.id"), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("inventory_items.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, default=1)

    line: Mapped[RecipeLine] = relationship(back_populates="options")
    item: Mapped[InventoryItem] = relationship(lazy="joined")


class AppSetting(Base):
    """scope is a business id, or "platform" for the few settings that cover every tenant (services/settings_svc.py)."""
    __tablename__ = "app_settings"
    scope: Mapped[str] = mapped_column(String(36), primary_key=True, default="platform")
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class UserPref(Base):
    """A choice one login made in one business (which variance chart to show). Not shared with other logins,
    and each business keeps its own; see services/settings_svc.py."""
    __tablename__ = "user_prefs"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    location_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class DataIssue(Base):
    __tablename__ = "data_issues"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str | None] = mapped_column(String(36))
    location_id: Mapped[str | None] = mapped_column(String(36))  # null: about the tenant as a whole (a locked login)
    code: Mapped[str] = mapped_column(String(48))
    entity: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict | None] = mapped_column(JSON)
    import_batch_id: Mapped[str | None] = mapped_column(ForeignKey("import_batches.id"))
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class User(Base):
    """A portal login. It belongs to one tenant; usernames and emails are unique across the platform.
    is_admin is a tenant admin: every business of the tenant, plus its businesses and users. Everyone else gets a
    level per area in each business they are a member of (BusinessMember.permissions = {"counts": "edit", ...};
    a missing area means no access). `permissions` here is what the single bar used before businesses and is no
    longer read. is_platform_admin logins have no tenant and only reach the platform console.
    Bumping session_version logs the user out everywhere (password reset, deactivation)."""
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id"), index=True)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    last_location_id: Mapped[str | None] = mapped_column(String(36))  # the business this login worked in last
    username: Mapped[str] = mapped_column(String(64), unique=True)  # stored lower case
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    pin_hash: Mapped[str | None] = mapped_column(String(255))  # quick login on the shared bar PC; counts and deliveries only
    email: Mapped[str | None] = mapped_column(String(254), unique=True)  # stored lower case
    totp_secret: Mapped[str | None] = mapped_column(String(255))  # encrypted, see services/logins.py
    totp_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # null until a first code is confirmed
    totp_last_step: Mapped[int | None] = mapped_column(Integer)  # last 30-second step used, so a code can't be replayed
    recovery_codes: Mapped[list | None] = mapped_column(JSON)  # keyed hashes of the unused ones
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    permissions: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    session_mode = "password"  # not stored: how the current request's session was opened (password | pin), set by app.auth
    session_mfa = False  # not stored: this session passed the second login step
    session_business = None  # not stored: the business this request works in (Location.id)
    session_permissions = None  # not stored: this login's levels in that business
    session_started = None  # not stored: when the session was opened (epoch seconds)


class AuthToken(Base):
    """A one-time secret sent by email: invite, reset (links) or mfa_email (a code). Only a keyed hash is kept."""
    __tablename__ = "auth_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    """Who did what: every change made through the API (method, route, status, request body with secrets
    removed), sensitive reads, logins, failed logins, logouts, refused requests and system jobs. Written by app.audit;
    never edited. Rows form a hash chain (seq, prev_hash, hash) so an edited or removed row shows up in verify()."""
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    tenant_id: Mapped[str | None] = mapped_column(String(36))  # not part of the hash chain (added later)
    location_id: Mapped[str | None] = mapped_column(String(36))  # the business the request worked in, if any
    username: Mapped[str | None] = mapped_column(String(64))  # as typed for a failed login; kept if the user is renamed
    seq: Mapped[int | None] = mapped_column(Integer, unique=True)
    action: Mapped[str] = mapped_column(String(32))  # login | login_failed | login_blocked | logout | change | denied | read | system
    area: Mapped[str | None] = mapped_column(String(32))
    method: Mapped[str | None] = mapped_column(String(8))
    route: Mapped[str | None] = mapped_column(String(200))  # /api/counts/{session_id}/post
    path: Mapped[str | None] = mapped_column(String(500))  # /api/counts/3f2c.../post
    status: Mapped[int | None] = mapped_column(Integer)
    ip: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict | None] = mapped_column(JSON)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (Index("ix_audit_user_at", "user_id", "at"), Index("ix_audit_at", "at"), Index("ix_audit_tenant_at", "tenant_id", "at"))


class AuditChange(Base):
    """One row the request changed: table, primary key, and {field: [before, after]}. Written in the same
    transaction as the change itself (app.audit), so a rolled-back request leaves none. Bulk statements and
    flushes of more than COLLAPSE rows of one table are kept as one summary row."""
    __tablename__ = "audit_changes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(String(36), index=True)  # AuditEvent.id; that row is written after the response
    position: Mapped[int] = mapped_column(Integer)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    table_name: Mapped[str] = mapped_column(String(64))
    row_id: Mapped[str | None] = mapped_column(String(200))
    op: Mapped[str] = mapped_column(String(16))  # insert | update | delete | bulk_update | bulk_delete
    changes: Mapped[dict | None] = mapped_column(JSON)
