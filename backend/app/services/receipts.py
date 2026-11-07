"""Reading a store receipt (photo or PDF) with Claude: store, date and time, receipt number, lines and totals.

What comes back is only a starting point for the review screen; nothing is posted from it directly.
"""
from __future__ import annotations

import base64
import json

import anthropic

from ..config import settings

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
RECEIPT_TYPES = IMAGE_TYPES | {"application/pdf"}
RECEIPT_MAX_BYTES = 10 * 1024 * 1024
IMAGE_MAX_BYTES = 5 * 1024 * 1024  # the API's limit for one image

PROMPT = """This is a receipt or invoice for stock bought by a bar. Read it and return what is printed on it.

- store: the store or supplier name as printed (e.g. "BC Liquor Store #212", "Costco Wholesale"); store_address if printed.
- date and time of the purchase: date as YYYY-MM-DD, time as 24-hour HH:MM. Null if not printed.
- receipt_no: the receipt, invoice or transaction number, if any.
- lines: every purchased line in printed order. text is the item description exactly as printed (keep sizes like
  "1.14L" or "24PK"). sku is the product, SKU or item number printed for that line, digits as printed; null if the
  line has none (never a price, a quantity or a barcode you can't read). qty is how many of that line were bought (1 if not shown). line_total is the line amount after
  any discount printed for that line, before tax. Put a discount that applies to one line into that line's total
  instead of listing it separately. Deposits, recycling or container fees and bag charges are their own lines.
- subtotal, tax (all taxes added together) and total as printed. Null for anything not printed.

Copy amounts exactly; don't calculate missing ones. If part of the receipt can't be read, leave it out rather than guess."""

_num = {"anyOf": [{"type": "number"}, {"type": "null"}]}
_str = {"anyOf": [{"type": "string"}, {"type": "null"}]}
SCHEMA = {
    "type": "object",
    "properties": {
        "store": _str, "store_address": _str, "date": _str, "time": _str, "receipt_no": _str,
        "lines": {"type": "array", "items": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "sku": _str, "qty": {"type": "number"}, "line_total": _num},
            "required": ["text", "sku", "qty", "line_total"],
            "additionalProperties": False,
        }},
        "subtotal": _num, "tax": _num, "total": _num,
    },
    "required": ["store", "store_address", "date", "time", "receipt_no", "lines", "subtotal", "tax", "total"],
    "additionalProperties": False,
}


class ReceiptReadError(Exception):
    pass


def enabled() -> bool:
    return bool(settings.anthropic_api_key)


def check_file(data: bytes, content_type: str) -> None:
    if content_type not in RECEIPT_TYPES:
        raise ReceiptReadError("the receipt must be a photo (JPEG, PNG, WebP) or a PDF")
    if not data:
        raise ReceiptReadError("the file is empty")
    if len(data) > RECEIPT_MAX_BYTES:
        raise ReceiptReadError(f"the file is over {RECEIPT_MAX_BYTES // (1024 * 1024)} MB")


def read(data: bytes, content_type: str) -> dict:
    """The receipt's contents as SCHEMA describes. Raises ReceiptReadError with a message for the user."""
    if not enabled():
        raise ReceiptReadError("receipt reading is off (no ANTHROPIC_API_KEY); enter the lines by hand")
    b64 = base64.standard_b64encode(data).decode("ascii")
    if content_type == "application/pdf":
        doc = {"type": "document", "source": {"type": "base64", "media_type": content_type, "data": b64}}
    else:
        if len(data) > IMAGE_MAX_BYTES:
            raise ReceiptReadError("the photo is over 5 MB; take it again at a lower resolution")
        doc = {"type": "image", "source": {"type": "base64", "media_type": content_type, "data": b64}}
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=180)
    try:
        resp = client.beta.messages.create(
            model=settings.receipt_model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": [doc, {"type": "text", "text": PROMPT}]}],
        )
    except anthropic.AuthenticationError:
        raise ReceiptReadError("the Claude API key was refused; check ANTHROPIC_API_KEY") from None
    except anthropic.BadRequestError as e:
        raise ReceiptReadError(f"Claude couldn't take this file: {e.message}") from None
    except anthropic.RateLimitError:
        raise ReceiptReadError("Claude is rate limited right now; try reading it again in a minute") from None
    except anthropic.APIStatusError as e:
        raise ReceiptReadError(f"Claude returned an error ({e.status_code}); try reading it again") from None
    except anthropic.APIConnectionError:
        raise ReceiptReadError("couldn't reach Claude; check the server's internet connection") from None
    if resp.stop_reason == "refusal":
        raise ReceiptReadError("Claude declined to read this file")
    if resp.stop_reason == "max_tokens":
        raise ReceiptReadError("the receipt is too long to read in one go; enter the rest by hand")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise ReceiptReadError("Claude's answer couldn't be understood; try reading it again") from None
