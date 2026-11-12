"""Metering of Claude API calls (reading receipts) per tenant and business, for the platform console: tokens,
an estimated cost, and the tenant's monthly token cap.

Costs are estimates from list prices (USD per million tokens); override or add models with AI_PRICES in .env,
e.g. AI_PRICES={"claude-opus-5-5": [4, 20]}. The invoice from Anthropic is the real figure.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..models import AiUsage, Tenant

# model -> (input, output) USD per million tokens
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-2": (8, 40), "claude-opus-5-5": (4, 20), "claude-opus-5": (5, 25), "claude-opus-4-8": (5, 25),
    "claude-sonnet-5-5": (2, 10), "claude-sonnet-5": (2, 10), "claude-haiku-5-5": (0.10, 0.50),
}
CACHE_WRITE, CACHE_READ = 1.25, 0.1  # of the input price
MILLION = Decimal(1_000_000)


def price(model: str) -> tuple[float, float] | None:
    p = settings.ai_prices.get(model) or PRICES.get(model)
    return (float(p[0]), float(p[1])) if p else None


def cost(model: str, usage) -> Decimal:
    """What one response cost. 0 for a model with no known price (it still counts toward tokens)."""
    p = price(model)
    if not p:
        return Decimal(0)
    fresh, written, read = (int(getattr(usage, k, 0) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    out = int(getattr(usage, "output_tokens", 0) or 0)
    usd = Decimal(str(p[0])) * (fresh + Decimal(str(CACHE_WRITE)) * written + Decimal(str(CACHE_READ)) * read) + Decimal(str(p[1])) * out
    return (usd / MILLION).quantize(Decimal("0.000001"))


def record(db: Session, kind: str, model: str, usage, ok: bool = True, ref: str | None = None) -> AiUsage:
    """Add one call to the session (the caller commits), for the tenant and business the session works in."""
    tokens_in = sum(int(getattr(usage, k, 0) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    row = AiUsage(tenant_id=db.info.get("tenant_id"), location_id=db.info.get("business_id"), user_id=db.info.get("user_id"),
                  kind=kind, model=model, input_tokens=tokens_in, output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
                  cost_usd=cost(model, usage), ok=ok, ref=ref)
    db.add(row)
    return row


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def month_tokens(db: Session, tenant_id: str) -> int:
    n = db.query(func.coalesce(func.sum(AiUsage.input_tokens + AiUsage.output_tokens), 0)) \
        .filter(AiUsage.tenant_id == tenant_id, AiUsage.at >= month_start()).scalar()
    return int(n or 0)


def over_cap(db: Session) -> str | None:
    """Why the session's tenant may not make another call this month, if its cap is used up."""
    tid = db.info.get("tenant_id")
    t = db.get(Tenant, tid) if tid else None
    if t and t.monthly_token_cap is not None and month_tokens(db, t.id) >= t.monthly_token_cap:
        return "this month's receipt-reading allowance is used up; enter the lines by hand or ask for a higher limit"
    return None
