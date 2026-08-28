#!/usr/bin/env python3
"""tools/demo.py - the whole loop on the bundled fixtures, zero credentials.

    make demo
    python3 tools/demo.py

Forces `llm.provider=mock` and `mode=shadow` regardless of config/hotel.yaml,
so this always works on a fresh clone with a blank .env. Runs against its own
database (data/demo/demo.db) so running it twice always shows the same
fixtures, and never touches data/agent.db (that is `make run`'s file).

Prints one line every check reads for the pass/fail signal:

    DEMO OK - 6 items processed, 2 drafted, 0 sent (shadow)
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, load_settings, sub_data_dir  # noqa: E402
from core.log import summary_line  # noqa: E402
from core.store import Store  # noqa: E402

import match_engine  # noqa: E402
import requisition_engine as req_engine  # noqa: E402
import run  # noqa: E402
import store_ext  # noqa: E402
from money import fmt  # noqa: E402


def main() -> int:
    try:
        settings = load_settings(demo=True)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    demo_db = sub_data_dir("demo") / "demo.db"
    if demo_db.exists():
        demo_db.unlink()
    store = Store(settings, path=demo_db)
    store_ext.ensure_schema(store)

    print("Procure-to-Pay AI demo - Hotel Aurora's sample requisitions, purchase orders, "
         "goods receipts and invoices\n")

    run._seed_pos_if_mock(settings, store)  # noqa: SLF001 - shared with the real loop on purpose

    receipts = run.process_receipts(settings, store, limit=50)
    print(f"Goods receipts: {receipts['applied']} PO(s) marked received "
         f"({receipts['seen']} event(s) seen).")

    reqs = run.process_requisitions(settings, store, limit=50)
    print(f"\nRequisitions: {reqs['seen']} seen, {reqs['awaiting_approval']} routed for "
         f"approval, {reqs['budget_hold']} held on budget.")
    for row in store_ext.list_requisitions(store):
        chain = " -> ".join(req_engine.next_pending_roles(row["approvals"])) or "-"
        print(f"  {row['id']}: {row['title']} ({fmt(row['amount_eur'], settings.hotel.currency)}) "
             f"-> {row['stage']}"
             + (f", waiting on {chain}" if row["stage"] == "awaiting_approval" else ""))

    invoices = run.process_invoices(settings, store, limit=50, provider="mock")
    print(f"\nInvoices: {invoices['seen']} seen, {invoices['cleared']} cleared for payment, "
         f"{invoices['held']} held.")
    for item in store.list_items(kind="invoice", status=["pending_review", "needs_human"],
                                 limit=50):
        draft = item.draft or {}
        print(f"  {item.id}: {draft.get('vendor', '')} {fmt(draft.get('amount_eur'), settings.hotel.currency)}"
             f" -> {draft.get('match')} ({item.review_status})")
        print(f"    {draft.get('reason', '')}")

    cleared = [i.draft for i in store.list_items(status="pending_review", kind="invoice", limit=50)
              if i.draft]
    held = [i.draft for i in store.list_items(status="needs_human", kind="invoice", limit=50)
           if i.draft]
    batch = match_engine.batch_summary(cleared, held, currency=settings.hotel.currency)
    print(f"\nPayment batch preview: {batch['headline']}")
    print("Nothing was sent or scheduled: mode is shadow, and demo never approves or sends.")
    print("Next: `make review` to see the drafts, or read workflows/10-procure-to-pay.md.\n")

    stats = {"processed": reqs["awaiting_approval"] + reqs["budget_hold"] + invoices["seen"],
             "drafted": invoices["held"], "sent": 0}
    print(f"DEMO OK - {summary_line(stats, settings.mode)}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
