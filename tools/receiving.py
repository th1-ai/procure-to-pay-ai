#!/usr/bin/env python3
"""tools/receiving.py - record that goods against a PO have arrived.

    python3 tools/receiving.py record PO-00001 [--note "2 boxes, signed by kitchen"]
    python3 tools/receiving.py show PO-00001

`tools/run.py` applies goods-receipt events from `fixtures/inbound/goods-receipts/`
or `data/imports/goods_receipts.csv` automatically every pass; this tool is
for the moment someone tells the hotel's Claude session directly ("the order
arrived") instead of it coming through a feed. Unguarded by `mode: shadow` -
see docs/how-it-works.md design decision 4: this records a fact reported by
another internal system (the warehouse, a person), nothing leaves the
building. Everything that reads this flag afterwards (the 3-way match, the
payment batch) still goes through the guard.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, load_settings  # noqa: E402
from core.store import Store, StoreError  # noqa: E402

import store_ext  # noqa: E402


def cmd_record(store, args) -> int:
    po = store_ext.get_po(store, args.po_ref)
    if po is None:
        print(f"error: no purchase order {args.po_ref}. `python3 tools/requisition.py list` "
             f"shows what has a PO; a PO not raised through this agent needs a row in "
             f"fixtures/hotel/purchase-orders.json or data/imports/purchase_orders.csv.",
             file=sys.stderr)
        return 1
    if po["received"]:
        print(f"{args.po_ref} was already marked received (at {po['received_at']}). No change.")
        return 0
    store_ext.mark_received(store, args.po_ref, note=args.note or "")
    print(f"{args.po_ref} marked received. Any held invoice against it will clear on the "
         f"next `make run`.")
    return 0


def cmd_show(store, args) -> int:
    po = store_ext.get_po(store, args.po_ref)
    if po is None:
        print(f"error: no purchase order {args.po_ref}", file=sys.stderr)
        return 1
    print(json.dumps(po, indent=2, ensure_ascii=False, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_record = sub.add_parser("record", help="mark a PO as received")
    p_record.add_argument("po_ref")
    p_record.add_argument("--note", default="")

    p_show = sub.add_parser("show", help="show one PO's current state")
    p_show.add_argument("po_ref")

    args = parser.parse_args(argv)
    args.po_ref = args.po_ref.strip().upper()

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        if args.command == "record":
            return cmd_record(store, args)
        if args.command == "show":
            return cmd_show(store, args)
        parser.error(f"unknown command {args.command}")
        return 2
    except StoreError as exc:
        print(f"cannot do that: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
