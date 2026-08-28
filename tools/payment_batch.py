#!/usr/bin/env python3
"""tools/payment_batch.py - preview the payment batch before you approve it.

    python3 tools/payment_batch.py preview

Read-only - spec step 5, "prepare the payment batch": partitions every
invoice item into cleared-for-payment and on-hold, totals each, and counts
how many holds are a price variance. Run this before
`python3 tools/review.py approve <id>` on the cleared invoices and
`python3 tools/review.py send`, which is the step that actually writes the
batch (guarded, see tools/payments.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import argparse  # noqa: E402

from core.config import ConfigError, load_settings  # noqa: E402
from core.store import Store  # noqa: E402

import match_engine  # noqa: E402
import store_ext  # noqa: E402
from money import fmt  # noqa: E402


def cmd_preview(store, settings) -> int:
    cleared_items = store.list_items(status="pending_review", kind="invoice", limit=500)
    held_items = store.list_items(status=["needs_human", "stale"], kind="invoice", limit=500)
    cleared = [i.draft for i in cleared_items if i.draft]
    held = [i.draft for i in held_items if i.draft]
    summary = match_engine.batch_summary(cleared, held, currency=settings.hotel.currency)

    print(summary["headline"])
    if cleared_items:
        print("\nCleared, awaiting your approval:")
        for item, draft in zip(cleared_items, cleared):
            print(f"  {item.id}  {draft.get('vendor', ''):<20} "
                 f"{fmt(draft.get('amount_eur'), settings.hotel.currency):>14}  "
                 f"{draft.get('po_ref') or '(no PO)'}")
    if held_items:
        print("\nOn hold - see the vendor query drafted for each:")
        for item, draft in zip(held_items, held):
            print(f"  {item.id}  {draft.get('vendor', ''):<20} "
                 f"{fmt(draft.get('amount_eur'), settings.hotel.currency):>14}  "
                 f"{draft.get('match', '')}")
    print(f"\nNext: `python3 tools/review.py approve <id>` on the cleared lines, then "
         f"`python3 tools/review.py send` to write the batch. Mode: {settings.mode} - "
         f"in shadow, nothing is written even once approved.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preview", help="what would be in the next payment batch")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        if args.command == "preview":
            return cmd_preview(store, settings)
        parser.error(f"unknown command {args.command}")
        return 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
