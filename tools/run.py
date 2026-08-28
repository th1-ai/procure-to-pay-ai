#!/usr/bin/env python3
"""tools/run.py - Procure-to-Pay AI's main loop, three phases every pass:

    1. goods receipts  -> mark the matching PO received (internal, unguarded)
    2. requisitions    -> budget check, assign the approval chain
    3. invoices        -> 3-way match, draft a vendor query on a hold

    python3 tools/run.py --once
    python3 tools/run.py --watch
    python3 tools/run.py --once --dry-run
    python3 tools/run.py --once --limit 5
    python3 tools/run.py --once --provider mock

This tool never sends anything and never schedules a payment - it only reads,
decides and queues. `tools/review.py` (vendor queries, vendor confirmations,
approval chases, award letters) and `tools/requisition.py` /
`tools/receiving.py` / `tools/capex.py` (the two-role-chain items) do every
outbound action, always through the review guard - see docs/safety.md.

Exit codes: 0 ok, 3 waiting on an `interactive` answer (see the message),
1 a real error.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters.base import AdapterError  # noqa: E402
from core.config import ConfigError, load_settings, repo_root  # noqa: E402
from core.llm import LLMError, LLMPendingInteractive, LLMSchemaError  # noqa: E402
from core.log import Run, get_logger, summary_line  # noqa: E402
from core.store import Store  # noqa: E402

import drafting  # noqa: E402
import match_engine  # noqa: E402
import readers  # noqa: E402
import requisition_engine as req_engine  # noqa: E402
import store_ext  # noqa: E402

log = get_logger("run")


def _seed_pos_if_mock(settings, store) -> None:
    if str(settings.agent_get("po_reader.adapter", "mock") or "mock").lower() != "mock":
        return
    path = repo_root() / "fixtures" / "hotel" / "purchase-orders.json"
    n = store_ext.seed_pos(store, path)
    if n:
        log.info("seeded purchase-order ledger", count=n, path=str(path))


def _import_csv_pos(settings, store) -> int:
    rows = readers.read_purchase_orders(settings)
    n = 0
    for row in rows:
        before = store_ext.get_po(store, row["po_ref"])
        store_ext.create_po(store, row["po_ref"], requisition_id=None, vendor=row["vendor"],
                            amount_eur=row["amount_eur"], description=row["description"],
                            received=row["received"], source="seed")
        n += 0 if before else 1
    return n


# --------------------------------------------------------------------------
# phase 1: goods receipts
# --------------------------------------------------------------------------
def process_receipts(settings, store, *, limit: int) -> dict:
    stats = {"seen": 0, "applied": 0, "skipped": 0}
    receipts = readers.read_goods_receipts(settings)[:limit]
    if settings.dry_run:
        for r in receipts:
            po = store_ext.get_po(store, r["po_ref"])
            would = bool(po and not po["received"])
            log.info("(--dry-run) would apply receipt" if would else "(--dry-run) no-op",
                     receipt_id=r["id"], po_ref=r["po_ref"])
        stats["seen"] = len(receipts)
        return stats
    seen = store.already_processed("goods_receipt", [r["id"] for r in receipts])
    for r in receipts:
        stats["seen"] += 1
        if r["id"] in seen:
            stats["skipped"] += 1
            continue
        item = store.upsert_item("goods_receipt", r["id"], kind="goods_receipt", payload=r)
        if item.review_status != "new":
            stats["skipped"] += 1
            continue
        applied = store_ext.mark_received(store, r["po_ref"], note=r.get("note", ""))
        store.transition(item.id, "skipped", actor="agent",
                         detail={"po_ref": r["po_ref"], "applied": applied})
        if applied:
            stats["applied"] += 1
            log.info("goods receipt applied", po_ref=r["po_ref"])
        else:
            log.info("goods receipt was a no-op (unknown PO or already received)",
                     po_ref=r["po_ref"])
    return stats


# --------------------------------------------------------------------------
# phase 2: requisitions
# --------------------------------------------------------------------------
def process_requisitions(settings, store, *, limit: int) -> dict:
    stats = {"seen": 0, "budget_hold": 0, "awaiting_approval": 0, "skipped": 0}
    matrix = settings.agent_get("delegation_matrix", [])
    budgets = settings.agent_get("budgets", {})
    gap_days = int(settings.agent_get("chase.gap_days", 3))
    max_follow_ups = int(settings.agent_get("chase.max_follow_ups", 3))
    rows = readers.read_requisitions(settings)[:limit]
    for r in rows:
        stats["seen"] += 1
        committed = store_ext.budget_committed(store, r["budget_line"])
        bc = req_engine.budget_check(r["amount_eur"], r["budget_line"], budgets, committed)
        if settings.dry_run:
            stage = "budget_hold" if not bc["ok"] else "awaiting_approval"
            log.info("(--dry-run) would create requisition", id=r["id"], stage=stage,
                     reason=bc["reason"])
            stats["budget_hold" if not bc["ok"] else "awaiting_approval"] += 1
            continue
        existing = store_ext.get_requisition(store, r["id"])
        if existing is not None:
            stats["skipped"] += 1
            continue
        if not bc["ok"]:
            store_ext.create_requisition(store, r["id"], title=r["title"],
                                         department=r["department"], requested_by=r["requested_by"],
                                         vendor=r["vendor"], amount_eur=r["amount_eur"],
                                         budget_line=r["budget_line"], budget_check=bc,
                                         approvals=[], stage="budget_hold")
            stats["budget_hold"] += 1
            log.info("requisition held: over budget", id=r["id"], reason=bc["reason"])
            continue
        chain = req_engine.approval_chain_for(r["amount_eur"], matrix)
        approvals = req_engine.approvals_json(chain)
        store_ext.create_requisition(store, r["id"], title=r["title"], department=r["department"],
                                     requested_by=r["requested_by"], vendor=r["vendor"],
                                     amount_eur=r["amount_eur"], budget_line=r["budget_line"],
                                     budget_check=bc, approvals=approvals,
                                     stage="awaiting_approval")
        next_due = (datetime.now(timezone.utc) + timedelta(days=gap_days)).isoformat(
            timespec="seconds")
        store.upsert_task("requisition_approval", r["id"], next_action_due=next_due,
                          max_follow_ups=max_follow_ups, payload={"chain": chain})
        stats["awaiting_approval"] += 1
        log.info("requisition queued for approval", id=r["id"], chain=" -> ".join(chain))
    return stats


# --------------------------------------------------------------------------
# phase 3: invoices
# --------------------------------------------------------------------------
def process_invoice(settings, store, invoice: dict, *, get_po, provider: str | None):
    """Match one invoice and, on a hold, draft the vendor query. Idempotent
    and resumable exactly like finance-filing-ai's process_invoice - see
    docs/how-it-works.md, "Resumable stages". ``get_po(po_ref) -> dict | None``
    is supplied by the caller so a dry run can look a PO up straight from the
    fixture/CSV in memory, never through the store - see docs/how-it-works.md,
    "--dry-run writes nothing". Returns (item_or_None, did_work).
    """
    currency = settings.hotel.currency
    tol_pct = float(settings.agent_get("matching.tolerance_pct", 2))
    tol_eur = float(settings.agent_get("matching.tolerance_eur", 100))
    no_po_threshold = float(settings.agent_get("matching.no_po_threshold_eur", 1000))
    three_way_on = bool(settings.agent_get("rules.three-way", True))
    approved_vendors = settings.agent_get("approved_vendors", [])

    dry = settings.dry_run
    existing = store.get_by_external("invoices", invoice["id"])
    payload = dict(invoice)
    if existing is not None and existing.payload and "_match_cache" in existing.payload:
        payload["_match_cache"] = existing.payload["_match_cache"]

    if dry:
        from core.store import Item
        item = existing if existing is not None else Item(
            id=f"dry-run-{invoice['id']}", kind="invoice", source="invoices",
            external_id=invoice["id"], payload=payload, review_status="new")
        item.payload = payload
        call_store = None
    else:
        item = store.upsert_item("invoices", invoice["id"], kind="invoice", payload=payload)
        call_store = store

    if item.draft is not None:
        return item, False

    match = (item.payload or {}).get("_match_cache")
    if not match:
        po = get_po(invoice["po_ref"]) if invoice.get("po_ref") else None
        match = match_engine.decide(invoice, po, tolerance_pct=tol_pct, tolerance_eur=tol_eur,
                                    no_po_threshold_eur=no_po_threshold,
                                    three_way_on=three_way_on, approved_vendors=approved_vendors,
                                    currency=currency)
        merged = {**(item.payload or {}), "_match_cache": match}
        if dry:
            item.payload = merged
        else:
            item = store.set_fields(item.id, payload=merged) or item

    draft = {**invoice, **match}

    if match["action"] == "hold":
        try:
            vq = drafting.draft_vendor_query(settings, call_store, item, invoice, match,
                                             provider=provider)
        except LLMSchemaError as exc:
            if dry:
                item.error, item.review_status = str(exc), "needs_human"
                return item, True
            store.set_fields(item.id, error=str(exc))
            updated = store.transition(item.id, "needs_human", actor="agent",
                                       detail={"error": "vendor_query_schema_error"})
            return updated, True
        draft["vendor_query"] = vq
        status = "needs_human"
        lang_reason = drafting.check_language(settings, vq)
    else:
        status = "pending_review"
        lang_reason = None

    if dry:
        item.intent, item.draft, item.review_status = match["match"], draft, status
        log.info("computed (--dry-run, nothing written)", item_id=item.id,
                 vendor=invoice.get("vendor"), would_be=status)
        return item, True

    store.set_fields(item.id, intent=match["match"], draft=draft)
    detail = {"reason": match["reason"]}
    if lang_reason:
        detail["language"] = lang_reason
    updated = store.transition(item.id, status, actor="agent", detail=detail)
    log.info("held" if status == "needs_human" else "cleared for payment", item_id=updated.id,
            vendor=invoice.get("vendor"), po_ref=invoice.get("po_ref"), reason=match["reason"])
    return updated, True


def _build_po_lookup(settings, store):
    """A ``get_po(ref) -> dict | None`` function. On a dry run it reads the
    fixture/CSV straight into memory and never touches the store - see
    docs/how-it-works.md, "--dry-run writes nothing"."""
    adapter = str(settings.agent_get("po_reader.adapter", "mock") or "mock").lower()
    if settings.dry_run:
        if adapter == "csv":
            rows = readers.read_purchase_orders(settings)
        else:
            path = repo_root() / "fixtures" / "hotel" / "purchase-orders.json"
            rows = store_ext.load_json_fixture(path)
        lookup = {str(r["po_ref"]).strip().upper(): r for r in rows if r.get("po_ref")}
        return lambda ref: lookup.get(str(ref).strip().upper()) if ref else None
    if adapter == "csv":
        _import_csv_pos(settings, store)
    return lambda ref: store_ext.get_po(store, ref) if ref else None


def process_invoices(settings, store, *, limit: int, provider: str | None) -> dict:
    stats = {"seen": 0, "held": 0, "cleared": 0, "skipped": 0}
    get_po = _build_po_lookup(settings, store)
    invoices = readers.read_invoices(settings)[:limit]
    seen = store.already_processed("invoices", [i["id"] for i in invoices]) \
        if not settings.dry_run else set()
    for invoice in invoices:
        stats["seen"] += 1
        if invoice["id"] in seen:
            stats["skipped"] += 1
            continue
        item, did_work = process_invoice(settings, store, invoice, get_po=get_po,
                                         provider=provider)
        if not did_work:
            stats["skipped"] += 1
            continue
        if item.review_status == "needs_human":
            stats["held"] += 1
        elif item.review_status == "pending_review":
            stats["cleared"] += 1
    return stats


# --------------------------------------------------------------------------
# one pass, three phases
# --------------------------------------------------------------------------
def one_pass(settings, store, *, limit: int, provider: str | None) -> tuple[int, dict]:
    stats = {"processed": 0, "drafted": 0, "needs_human": 0, "sent": 0, "skipped": 0}
    with Run("procure-to-pay", settings, store) as run:
        if not settings.dry_run:
            _seed_pos_if_mock(settings, store)  # dry runs read the fixture in memory instead
        try:
            receipts = process_receipts(settings, store, limit=limit)
            reqs = process_requisitions(settings, store, limit=limit)
            invoices = process_invoices(settings, store, limit=limit, provider=provider)
        except LLMPendingInteractive as exc:
            run.stats = {**stats, "dry_run": settings.dry_run}
            print(str(exc))
            return 3, stats
        stats["processed"] = (receipts["applied"] + reqs["awaiting_approval"] + reqs["budget_hold"]
                              + invoices["held"] + invoices["cleared"])
        stats["needs_human"] = reqs["budget_hold"] + invoices["held"]
        stats["drafted"] = invoices["held"]  # every hold gets an LLM-drafted vendor query
        stats["skipped"] = receipts["skipped"] + reqs["skipped"] + invoices["skipped"]
        reaped = store.reap_stuck_sending()
        if reaped:
            log.warn("reaped stuck sends", count=len(reaped))
        run.stats = {**stats, "dry_run": settings.dry_run,
                    "receipts": receipts, "requisitions": reqs, "invoices": invoices}
        log.info("pass complete", receipts=receipts["applied"], requisitions=reqs["seen"],
                 invoices=invoices["seen"], held=invoices["held"], cleared=invoices["cleared"])
    return 0, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--once", action="store_true", help="run a single pass (default)")
    mode_group.add_argument("--watch", action="store_true",
                            help="keep running on the configured interval")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute everything, write nothing, even in live mode")
    parser.add_argument("--limit", type=int, default=50, help="max records per phase per pass")
    parser.add_argument("--provider", default=None, help="override llm.provider for this run")
    parser.add_argument("--poll-seconds", type=int, default=None,
                        help="override the --watch interval (default: agent.yaml or 900)")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(provider=args.provider, dry_run=args.dry_run)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        if args.watch:
            poll_seconds = args.poll_seconds or int(settings.agent_get("poll_seconds", 900))
            while True:
                code, stats = one_pass(settings, store, limit=args.limit, provider=args.provider)
                print(summary_line(stats, settings.mode))
                if code != 0:
                    return code
                time.sleep(poll_seconds)
        code, stats = one_pass(settings, store, limit=args.limit, provider=args.provider)
        print(summary_line(stats, settings.mode))
        return code
    except AdapterError as exc:
        print(f"integration error: {exc}", file=sys.stderr)
        print("Run `make doctor` to see what is missing and how to fix it.", file=sys.stderr)
        return 1
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
