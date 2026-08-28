#!/usr/bin/env python3
"""tools/requisition.py - work a requisition through its approval chain.

    python3 tools/requisition.py list [--stage awaiting_approval]
    python3 tools/requisition.py show <id>
    python3 tools/requisition.py approve <id> --role "Department Head"

A requisition needs a *sequence* of named-role approvals, not the single
approve/edit/reject core.review is built for - see docs/how-it-works.md,
"Data model". Approving the last outstanding role creates the purchase
order (an internal record - not blocked by shadow mode, see design decision
4) and drafts the vendor-confirmation email, which DOES go through the
normal guarded review queue (`tools/review.py`) because sending it is an
outbound action.

Exit codes: 0 ok, 3 waiting on an `interactive` answer for the vendor
confirmation draft, 1 a real error, 2 bad arguments.
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
from core.llm import LLMError, LLMPendingInteractive, LLMSchemaError  # noqa: E402
from core.store import Store, StoreError, utcnow  # noqa: E402

import drafting  # noqa: E402
import requisition_engine as req_engine  # noqa: E402
import store_ext  # noqa: E402
from money import fmt  # noqa: E402


def cmd_list(store, args) -> int:
    rows = store_ext.list_requisitions(store, stage=args.stage)
    if not rows:
        print("No requisitions" + (f" in stage '{args.stage}'." if args.stage else "."))
        return 0
    print(f"{len(rows)} requisition(s):\n")
    for r in rows:
        pending = ", ".join(req_engine.next_pending_roles(r["approvals"])) or "-"
        print(f"  {r['id']}  {r['stage']:<18} {fmt(r['amount_eur'])}  {r['title'][:30]:<30} "
             f"waiting on: {pending}")
    return 0


def cmd_show(store, args) -> int:
    row = store_ext.get_requisition(store, args.id)
    if row is None:
        print(f"error: no requisition {args.id}", file=sys.stderr)
        return 1
    print(json.dumps(row, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_approve(store, settings, args) -> int:
    row = store_ext.get_requisition(store, args.id)
    if row is None:
        print(f"error: no requisition {args.id}", file=sys.stderr)
        return 1
    if row["stage"] == "budget_hold":
        print(f"error: {args.id} is on budget hold - {row['budget_check'].get('reason', '')}\n"
             f"  Raise config/agent.yaml: budgets for '{row['budget_line']}', or ask Finance "
             f"for an exception, before approving a role.", file=sys.stderr)
        return 1
    if row["stage"] not in ("awaiting_approval",):
        print(f"error: {args.id} is '{row['stage']}', not waiting on an approval.",
             file=sys.stderr)
        return 1

    # Resumable hand-off (marker-after-pend): the same convention as
    # tools/capex.py's `_capex_approval_cache` - see docs/how-it-works.md,
    # "Resumable stages". A retry after the vendor-confirmation LLM call
    # parks (LLMPendingInteractive, exit 3) must not re-run
    # req_engine.approve_role, and must not draw a second number off
    # `next_sequence('po')` - a second PO number would also change the
    # draft's `fixture_id`, so the `interactive` provider would build a
    # different prompt id and never find the answer already written for the
    # first prompt. Both the role decision and the reserved po_ref are
    # cached in core.store's `kv` table (small scalars, same job as a poll
    # cursor), keyed by requisition id - NOT on an item's payload like
    # capex.py, because unlike the award-letter item (keyed by the
    # project's own stable id) the vendor-confirmation item's key IS the
    # po_ref, and that does not exist yet the first time through here.
    # `p2p_requisitions.approvals` / `po_ref` / `stage` are deliberately
    # left untouched until the draft actually succeeds, below, so a
    # requisition stuck on a parked prompt still shows the real role as
    # pending, not a phantom "approved" with no PO to show for it.
    cache_key = f"p2p_requisition_pending_approval:{args.id}"
    cached = store.get(cache_key)
    if cached and cached.get("role") == args.role:
        new_approvals, complete, po_ref = cached["new_approvals"], True, cached["po_ref"]
    else:
        new_approvals, found = req_engine.approve_role(row["approvals"], args.role,
                                                        approved_at=utcnow())
        if not found:
            pending = ", ".join(req_engine.next_pending_roles(row["approvals"])) or "(none)"
            print(f"error: '{args.role}' is not a pending role on {args.id}. Pending: {pending}",
                 file=sys.stderr)
            return 1
        complete = req_engine.approvals_complete(new_approvals)
        po_ref = None

    if not complete:
        # Not the final role: nothing downstream can pend, so it is safe to
        # commit immediately - same as before.
        store_ext.set_requisition_approvals(store, args.id, new_approvals, stage=None)
        print(f"{args.role} approved on {args.id}.")
        pending = ", ".join(req_engine.next_pending_roles(new_approvals))
        print(f"Still waiting on: {pending}")
        return 0

    print(f"{args.role} approved on {args.id}.")
    if po_ref is None:
        po_ref = f"PO-{store.next_sequence('po', dry_run=settings.dry_run):05d}"
    print(f"Every role has signed - creating {po_ref}.")
    if settings.dry_run:
        print("(--dry-run) would create the PO and draft a vendor-confirmation email here.")
        return 0

    if not cached:
        # Cache the final role's approval AND the reserved po_ref BEFORE the
        # LLM call that can pend. `store_ext.set_requisition_approvals` /
        # `set_requisition_po` (the requisition's own authoritative record)
        # are deliberately NOT written here - only once the vendor
        # confirmation is actually drafted, below.
        store.set(cache_key, {"role": args.role, "new_approvals": new_approvals, "po_ref": po_ref})
    po = store_ext.create_po(store, po_ref, requisition_id=args.id, vendor=row["vendor"],
                             amount_eur=row["amount_eur"], description=row["title"])

    item = store.upsert_item("requisitions", po_ref, kind="vendor_confirmation", payload=po)
    if item.draft is not None:
        print(f"Vendor confirmation for {po_ref} was already drafted - see "
             f"`python3 tools/review.py show {item.id}`.")
        return 0
    try:
        confirmation = drafting.draft_vendor_confirmation(settings, store, item, po,
                                                          provider=args.provider)
    except LLMPendingInteractive as exc:
        print(str(exc))
        return 3
    except LLMSchemaError as exc:
        store.set_fields(item.id, error=str(exc))
        store.transition(item.id, "needs_human", actor="agent",
                         detail={"error": "vendor_confirmation_schema_error"})
        print(f"Vendor-confirmation draft failed ({exc}) - queued as needs_human, {item.id}.")
        return 0
    # The LLM call resolved: only now is it safe to record the final
    # approval and the PO on the requisition itself.
    store_ext.set_requisition_approvals(store, args.id, new_approvals, stage="approved")
    store_ext.set_requisition_po(store, args.id, po_ref)
    store.set(cache_key, None)  # clears the resumed marker
    store.set_fields(item.id, draft=confirmation)
    lang_reason = drafting.check_language(settings, confirmation)
    if lang_reason:
        store.transition(item.id, "needs_human", actor="agent",
                         detail={"po_ref": po_ref, "language": lang_reason})
        print(f"Vendor confirmation for {po_ref} drafted but held for review ({lang_reason}) - "
             f"see `python3 tools/review.py show {item.id}`.")
        return 0
    store.transition(item.id, "pending_review", actor="agent", detail={"po_ref": po_ref})
    print(f"Vendor confirmation drafted and queued - see `python3 tools/review.py show "
         f"{item.id}` then `python3 tools/review.py approve {item.id}`.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="requisitions, optionally by stage")
    p_list.add_argument("--stage", default=None,
                        choices=list(store_ext.STAGES))

    p_show = sub.add_parser("show", help="full detail for one requisition")
    p_show.add_argument("id")

    p_approve = sub.add_parser("approve", help="approve one role on the chain")
    p_approve.add_argument("id")
    p_approve.add_argument("--role", required=True)
    p_approve.add_argument("--provider", default=None,
                          help="override llm.provider for the vendor-confirmation draft")

    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        if args.command == "list":
            return cmd_list(store, args)
        if args.command == "show":
            return cmd_show(store, args)
        if args.command == "approve":
            return cmd_approve(store, settings, args)
        parser.error(f"unknown command {args.command}")
        return 2
    except (StoreError, LLMError) as exc:
        print(f"cannot do that: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
