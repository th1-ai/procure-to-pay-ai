"""tools/payments.py - the one write path that prepares a payment batch line.

Guarded exactly like an adapter write (``assert_write_allowed`` is what the
``@guarded_write`` decorator calls under the hood - see
core/adapters/base.py) even though this is a local file write, not a call to
the ``Payments`` stub adapter: this is THE action ``mode: shadow`` and
``--dry-run`` must block, and it is the one action this repo never lets even
``mode: live`` skip past a human - see docs/how-it-works.md, design
decisions 5 and 6.

Nothing here moves money. It writes the record a human's own banking process
reads to actually pay - a JSON line under data/exports/payment-batches/ and a
row in the payments sheet - the same shape finance-filing-ai's
``finalize_invoice`` uses for a filed invoice.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from core.adapters import get_sheets
from core.config import Settings, sub_data_dir
from core.review import assert_write_allowed
from core.store import Item
from money import fmt

PAYMENTS_SHEET = "payment_batches"
PAYMENTS_HEADER = ["scheduled_at", "item_id", "vendor", "invoice_no", "po_ref",
                   "amount", "currency", "match", "batch_file"]


def schedule_payment(settings: Settings, item: Item) -> dict:
    """Write one cleared invoice into today's payment batch. Raises
    :class:`core.review.WriteBlocked` in shadow mode or when the item is not
    approved - callers handle that exactly like any other guarded write."""
    assert_write_allowed(settings, "payment", item)
    draft = item.draft or {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    batch_dir = sub_data_dir("exports") / "payment-batches"
    batch_dir.mkdir(parents=True, exist_ok=True)
    batch_file = batch_dir / f"{today}.json"
    lines = []
    if batch_file.exists():
        try:
            lines = json.loads(batch_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            lines = []
    record = {
        "item_id": item.id, "vendor": draft.get("vendor", ""),
        "invoice_no": draft.get("invoice_no", ""), "po_ref": draft.get("po_ref") or "",
        "amount_eur": draft.get("amount_eur"), "currency": draft.get("currency", "EUR"),
        "match": draft.get("match", ""),
        "scheduled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "SIMULATED: nothing was actually paid. A person executes this "
                "batch in your own banking system.",
    }
    lines.append(record)
    batch_file.write_text(json.dumps(lines, indent=2, ensure_ascii=False), encoding="utf-8")

    sheets = get_sheets(settings)
    existing = []
    try:
        existing = sheets.read(PAYMENTS_SHEET)
    except Exception:  # noqa: BLE001 - a broken read must not block scheduling
        pass
    rows = [] if existing else [PAYMENTS_HEADER]
    rows.append([
        record["scheduled_at"], item.id, record["vendor"], record["invoice_no"],
        record["po_ref"], record["amount_eur"], record["currency"], record["match"],
        str(batch_file.relative_to(sub_data_dir("exports"))),
    ])
    sheet_result = sheets.append(PAYMENTS_SHEET, rows, item=item)

    return {"batch_file": str(batch_file.relative_to(sub_data_dir("exports"))),
            "amount": fmt(record["amount_eur"], record["currency"]), "sheet": sheet_result}
