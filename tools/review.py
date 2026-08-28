#!/usr/bin/env python3
"""tools/review.py - work the review queue: list / show / approve / edit / reject / send.

    python3 tools/review.py list [--status pending_review] [--kind invoice]
    python3 tools/review.py show <id>
    python3 tools/review.py approve <id> [--note "..."]
    python3 tools/review.py edit <id> --body-file draft.txt [--subject "..."] [--note "..."]
    python3 tools/review.py reject <id> --reason "wrong tone"
    python3 tools/review.py retry <id>          # re-queue a failed send
    python3 tools/review.py send                # act on everything approved/edited
    python3 tools/review.py stale               # go-live: clear the shadow-era queue

Only this tool writes `approved` / `edited` / `rejected` (core/review.py). Only
`send` writes `sending` / `sent`. What `send` actually DOES depends on the
item's `kind`:

    invoice, draft.action == "schedule"   -> a payment-batch line (tools/payments.py)
    invoice, draft.action == "hold"       -> the vendor-query email
    vendor_confirmation                   -> the PO confirmation email
    approval_chase                        -> the chase email
    award_letter                          -> the letter of award, by email

Nothing here bypasses `mode: shadow` - see docs/safety.md. A payment batch
line is never sent even in live mode without this item being approved first;
`review.require_approval_for` in config/hotel.yaml ships with `payment` in
it, and workflows/90-go-live.md says not to remove it.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import argparse  # noqa: E402
import json  # noqa: E402

from core.adapters import get_email  # noqa: E402
from core.config import ConfigError, load_settings  # noqa: E402
from core.review import (WriteBlocked, approve, edit, list_queue, reject, retry,  # noqa: E402
                         show, stale_backlog)
from core.store import Store, StoreError  # noqa: E402

import payments  # noqa: E402
import store_ext  # noqa: E402


def _normalize(text: str) -> str:
    return "".join(ch for ch in str(text or "").strip().lower() if ch.isalnum())


def _recipient(item, settings) -> str:
    """Who a `send_email` action goes to, for the kinds review.py can send."""
    draft = item.draft or {}
    payload = item.payload or {}
    if item.kind in ("invoice", "vendor_confirmation", "award_letter"):
        vendor = draft.get("vendor") or payload.get("vendor") or ""
        emails = settings.agent_get("vendor_emails", {}) or {}
        for name, address in emails.items():
            if _normalize(name) == _normalize(vendor) and address:
                return str(address)
        return ""
    if item.kind == "approval_chase":
        role = payload.get("role_waiting", "")
        emails = settings.agent_get("approver_emails", {}) or {}
        return str(emails.get(role, "") or "")
    return ""


def _print_item_line(item) -> None:
    payload = item.payload or {}
    draft = item.draft or {}
    label = payload.get("title") or draft.get("vendor") or payload.get("po_ref") or ""
    marker = "[SAMPLE DATA] " if item.is_sample else ""
    print(f"  {item.id}  {item.review_status:<14} {item.kind:<18} "
          f"{str(label)[:40]}  {marker}".rstrip())


def cmd_list(store, args) -> int:
    items = list_queue(store, status=args.status, kind=args.kind, limit=args.limit)
    if not items:
        print("Nothing is waiting for you.")
        return 0
    print(f"{len(items)} item(s) waiting:\n")
    for item in items:
        _print_item_line(item)
    if any(item.is_sample for item in items):
        print("\n[SAMPLE DATA] One or more items above came from the shipped sample "
              "fixtures, not your property - systems.email.adapter is 'mock'. Connect "
              "your real systems (docs/integrations.md) before approving them.")
    print("\nRun `python3 tools/review.py show <id>` for the full draft.")
    return 0


def cmd_show(store, args) -> int:
    try:
        detail = show(store, args.id)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    payload = (detail.get("item") or {}).get("payload") or {}
    if payload.get("_sample"):
        print("[SAMPLE DATA] This item came from the shipped sample fixtures, not your "
              "property - systems.email.adapter is 'mock'. Connect your real systems "
              "(docs/integrations.md) before approving it.\n")
    print(json.dumps(detail, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_approve(store, args) -> int:
    item = approve(store, args.id, note=args.note or "")
    print(f"approved {item.id} - now in the send queue")
    return 0


def cmd_edit(store, args) -> int:
    item = store.get_item(args.id)
    if item is None:
        print(f"error: no item {args.id}", file=sys.stderr)
        return 1
    body = Path(args.body_file).read_text(encoding="utf-8")
    new_draft = dict(item.draft or {})
    if item.kind == "invoice" and "vendor_query" in new_draft:
        new_draft["vendor_query"] = {**new_draft["vendor_query"], "body": body}
        if args.subject:
            new_draft["vendor_query"]["subject"] = args.subject
    else:
        new_draft["body"] = body
        if args.subject:
            new_draft["subject"] = args.subject
    edit(store, args.id, new_draft, note=args.note or "")
    print(f"edited {item.id} - now in the send queue")
    return 0


def cmd_reject(store, args) -> int:
    item = reject(store, args.id, reason=args.reason or "")
    print(f"rejected {item.id}")
    return 0


def cmd_retry(store, args) -> int:
    item = retry(store, args.id)
    print(f"queued {item.id} for another attempt")
    return 0


def _send_one(store, settings, email, item) -> tuple[bool, str]:
    """Act on one claimed item. Returns (ok, message)."""
    draft = item.draft or {}
    if item.kind == "invoice" and draft.get("action") == "schedule":
        try:
            result = payments.schedule_payment(settings, item)
        except WriteBlocked as exc:
            store.transition(item.id, "approved", "agent", {"blocked": str(exc)[:200]})
            return False, f"blocked {item.id} (approval kept): {exc}"
        except Exception as exc:  # noqa: BLE001 - record and move on, never crash the batch
            store.mark_send_failed(item.id, str(exc))
            return False, f"failed {item.id}: {exc}"
        store.mark_sent(item.id, result["batch_file"])
        return True, f"scheduled {item.id}: {result['amount']} -> {result['batch_file']}"

    # Every other sendable kind is an email: invoice (vendor query on a hold),
    # vendor_confirmation, approval_chase, award_letter.
    body = (draft.get("vendor_query") or draft) if item.kind == "invoice" else draft
    to = _recipient(item, settings)
    if not to:
        store.transition(item.id, "approved", "agent", {"blocked": "no recipient address"})
        return False, (f"blocked {item.id} (approval kept): no email address on file for this "
                       f"vendor/role - add one to config/agent.yaml: vendor_emails / "
                       f"approver_emails.")
    try:
        result = email.send(to, body.get("subject", ""), body.get("body", ""), item=item)
    except WriteBlocked as exc:
        store.transition(item.id, "approved", "agent", {"blocked": str(exc)[:200]})
        return False, f"blocked {item.id} (approval kept): {exc}"
    except Exception as exc:  # noqa: BLE001
        store.mark_send_failed(item.id, str(exc))
        return False, f"failed {item.id}: {exc}"
    store.mark_sent(item.id, result.get("message_id"))
    return True, f"sent {item.id} to {to}"


def cmd_send(store, settings, args) -> int:
    claimed = store.claim_for_send(limit=args.limit)
    if not claimed:
        print("Nothing approved or edited is waiting to send.")
        return 0
    email = get_email(settings)
    ok_count, failed = 0, 0
    for item in claimed:
        ok, message = _send_one(store, settings, email, item)
        print(message)
        if ok:
            ok_count += 1
        else:
            failed += 1
    print(f"\n{ok_count} done, {failed} blocked or failed.")
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="what is waiting for a human")
    p_list.add_argument("--status", default=None)
    p_list.add_argument("--kind", default=None)
    p_list.add_argument("--limit", type=int, default=50)

    p_show = sub.add_parser("show", help="full detail for one item")
    p_show.add_argument("id")

    p_approve = sub.add_parser("approve", help="approve the draft unchanged")
    p_approve.add_argument("id")
    p_approve.add_argument("--note", default="")

    p_edit = sub.add_parser("edit", help="rewrite the draft, then queue it")
    p_edit.add_argument("id")
    p_edit.add_argument("--body-file", required=True)
    p_edit.add_argument("--subject", default=None)
    p_edit.add_argument("--note", default="")

    p_reject = sub.add_parser("reject", help="discard the draft")
    p_reject.add_argument("id")
    p_reject.add_argument("--reason", "--note", dest="reason", default="")

    p_retry = sub.add_parser("retry", help="re-queue a failed attempt")
    p_retry.add_argument("id")

    p_send = sub.add_parser("send", help="act on everything approved or edited")
    p_send.add_argument("--limit", type=int, default=20)

    sub.add_parser("stale", help="go-live step: mark everything still un-sent as stale "
                                 "(the shadow-era queue was never sent and is out of date)")

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
            return cmd_approve(store, args)
        if args.command == "edit":
            return cmd_edit(store, args)
        if args.command == "reject":
            return cmd_reject(store, args)
        if args.command == "retry":
            return cmd_retry(store, args)
        if args.command == "send":
            return cmd_send(store, settings, args)
        if args.command == "stale":
            moved = stale_backlog(store)
            print(f"marked {len(moved)} item(s) stale. Nothing from before go-live will send.")
            return 0
        parser.error(f"unknown command {args.command}")
        return 2
    except StoreError as exc:
        print(f"cannot do that: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
