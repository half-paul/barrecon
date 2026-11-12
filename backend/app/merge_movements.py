"""Turn delivery movements recorded outside a delivery (old movements-sheet uploads, the old quick sheet)
into deliveries, so every delivery is listed under Deliveries.

    python -m app.merge_movements           # show what would change
    python -m app.merge_movements --apply   # do it

Movements are grouped by instant and source into one delivery each. The reference is what the lines' notes
share at the start (the invoice number the sheet carried), else "sheet <date>". A line saved with no packs and
a qty under one pack was a bottle count typed into qty (stock units): it becomes that many packs. The old
movements are kept as retracted history; the delivery posts new ones.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .audit import record
from .config import settings
from . import db as dbmod
from .models import StockMovement
from .services import deliveries as dlv
from .services import inventory as inv


def _ref(notes: list[str], day) -> str:
    p = os.path.commonprefix(notes) if len(notes) > 1 else ""
    p = p[: p.rfind(" ")].strip(" -/·") if " " in p else ""
    return p[:100] or f"sheet {day}"


def _packs(m: StockMovement) -> Decimal:
    if m.packs is not None:
        return Decimal(m.packs)
    size = Decimal(m.item.pack_size)
    q = Decimal(m.qty)
    return q if size > 1 and q < size else q / size


def main(apply: bool) -> int:
    tz = ZoneInfo(settings.timezone)
    with dbmod.SessionLocal() as db:
        loose = list(db.scalars(select(StockMovement).where(StockMovement.kind == "delivery",
                                                            StockMovement.status == "active",
                                                            StockMovement.delivery_id.is_(None))
                                .order_by(StockMovement.occurred_at)))
        groups: dict[tuple, list[StockMovement]] = defaultdict(list)
        for m in loose:
            groups[(m.location_id, inv.to_utc(m.occurred_at), m.source)].append(m)
        made = 0
        for (loc, when, _src), ms in groups.items():
            day = when.astimezone(tz).date()
            ref = _ref([m.note or "" for m in ms], day)
            store = "BC Liquor Store" if ref.upper().startswith("BCLS") else None
            by_item: dict[str, list] = {}
            for m in ms:
                it, p, c = by_item.get(m.item_id, (m.item, Decimal(0), m.pack_cost))
                by_item[m.item_id] = (it, p + _packs(m), c)
            fixed = [m.item.name for m in ms if m.packs is None and Decimal(m.item.pack_size) > 1
                     and Decimal(m.qty) < Decimal(m.item.pack_size)]
            print(f"{day}  {ref!r}  {len(by_item)} items" + (f"  (bottles typed as {ms[0].item.stock_unit}: "
                                                           f"{', '.join(fixed)})" if fixed else ""))
            if not apply:
                continue
            for m in ms:
                m.status = "retracted"
                m.external_ref = f"{m.external_ref}#merged-{m.id[:8]}"
            db.flush()
            dlv.record_sheet(db, location_id=loc, source="sheet", occurred_at=when, ref=ref, store=store,
                             note="moved from stock movements", lines=list(by_item.values()))
            made += 1
        if apply:
            db.commit()
            record(username="system", user_id=None, action="system",
                   detail={"job": "merge_movements", "deliveries": made, "movements": len(loose)})
            print(f"Made {made} deliveries from {len(loose)} movements.")
        else:
            print(f"Dry run: {len(groups)} deliveries from {len(loose)} movements. Run with --apply to do it.")
    return 0


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv[1:]))
