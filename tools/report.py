#!/usr/bin/env python3
"""tools/report.py - what the agent did, and what it cost.

    make report
    python3 tools/report.py

The roster's promise is "invoice-to-payment cycle from days to hours; every
3-way mismatch caught and reason-coded automatically" and "-75%
invoice-to-payment cycle (labor)". This prints the numbers that let you check
that promise: how many invoices cleared versus were held, the variance rate,
how long a requisition takes to become a PO, and the spend on the four
drafting prompts.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, load_settings  # noqa: E402
from core.store import Store  # noqa: E402

import store_ext  # noqa: E402
from money import fmt  # noqa: E402


def _amount(item) -> float:
    return float((item.draft or {}).get("amount_eur") or 0)


def gather(store: Store, currency: str) -> dict:
    invoices = store.list_items(kind="invoice", status=list(
        {"pending_review", "needs_human", "sent", "approved", "edited", "sending", "stale"}),
        limit=5000)
    # Classify by draft.action, not review_status alone: an "approved" invoice
    # whose action is "hold" only means the vendor-QUERY email was approved to
    # send - the invoice itself is still not cleared for payment. Only
    # action == "schedule" ever counts toward cleared/scheduled - see the
    # report bug this fixes in the build report.
    is_schedule = lambda i: (i.draft or {}).get("action") == "schedule"  # noqa: E731
    scheduled = [i for i in invoices if i.review_status == "sent" and is_schedule(i)]
    cleared = [i for i in invoices
              if is_schedule(i) and i.review_status in ("pending_review", "approved", "edited")]
    held = [i for i in invoices if not is_schedule(i) or i.review_status in ("needs_human", "stale")]
    variances = sum(1 for i in held if (i.draft or {}).get("match") == "variance")
    total = len(invoices)

    reqs = store_ext.list_requisitions(store)
    po_cycle_hours = []
    for r in reqs:
        if r["stage"] != "po_created":
            continue
        try:
            created = datetime.fromisoformat(r["created_at"])
            updated = datetime.fromisoformat(r["updated_at"])
            po_cycle_hours.append((updated - created).total_seconds() / 3600)
        except (TypeError, ValueError):
            continue

    cost_usd = 0.0
    for row in store.db.execute(
        "SELECT detail_json FROM events WHERE action='llm_call'").fetchall():
        try:
            cost_usd += float((json.loads(row["detail_json"]) or {}).get("cost_usd") or 0.0)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue

    return {
        "total_invoices": total, "cleared": len(cleared), "scheduled": len(scheduled),
        "held": len(held), "held_value": round(sum(_amount(i) for i in held), 2),
        "cleared_value": round(sum(_amount(i) for i in cleared) + sum(_amount(i) for i in scheduled), 2),
        "variance_count": variances,
        "cleared_pct": round(100 * (len(cleared) + len(scheduled)) / total, 1) if total else 0.0,
        "requisitions": len(reqs),
        "requisitions_awaiting": sum(1 for r in reqs if r["stage"] == "awaiting_approval"),
        "avg_requisition_to_po_hours": (round(sum(po_cycle_hours) / len(po_cycle_hours), 1)
                                        if po_cycle_hours else None),
        "llm_cost_usd": round(cost_usd, 4), "by_status": store.counts(), "currency": currency,
    }


def print_report(stats: dict) -> None:
    c = stats["currency"]
    print("Procure-to-Pay AI - report\n")
    print(f"  Invoices seen so far:     {stats['total_invoices']}")
    print(f"  Cleared for payment:      {stats['cleared']} awaiting approval, "
         f"{stats['scheduled']} scheduled ({fmt(stats['cleared_value'], c)})")
    print(f"  On hold:                  {stats['held']} ({fmt(stats['held_value'], c)}), "
         f"{stats['variance_count']} a price variance")
    print(f"  Cleared rate:             {stats['cleared_pct']}% of everything seen")
    print(f"  Requisitions:             {stats['requisitions']} total, "
         f"{stats['requisitions_awaiting']} awaiting approval")
    avg = stats["avg_requisition_to_po_hours"]
    print(f"  Avg requisition -> PO:    {avg} hour(s)" if avg is not None
         else "  Avg requisition -> PO:   no PO created yet")
    print(f"  LLM spend so far:         ${stats['llm_cost_usd']} (vendor queries, vendor "
         f"confirmations, approval chases, award letters)")
    print("\n  By status: " + ", ".join(f"{k}={v}" for k, v in sorted(stats["by_status"].items())))


def main(argv: list[str] | None = None) -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        stats = gather(store, settings.hotel.currency)
        print_report(stats)
        print(f"\n  Mode: {settings.mode}. 'Scheduled' above only ever counts a payment-batch "
             f"line that was actually written by `python3 tools/review.py send` - in shadow "
             f"mode that write is always blocked, so an approved invoice shows as 'approved', "
             f"never 'scheduled', until you go live. See docs/how-it-works.md.")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
