#!/usr/bin/env python3
"""tools/capex.py - Tender & CAPEX Approval AI ("The Chancellor").

Off by default (`config/agent.yaml: subagents.tender_capex.enabled`) - see
docs/sub-agents.md. Everything else in this repo works without it.

    python3 tools/capex.py list
    python3 tools/capex.py show <id>
    python3 tools/capex.py draft-recommendation <id>
    python3 tools/capex.py approve <id> --role "Chief Engineer"

`draft-recommendation` never picks a winner beyond ranking a quote "for
committee consideration" - every approval role still has to sign, and the
chain stays locked until a recommendation exists (see tools/capex_engine.py).
The last role's approval drafts a letter of award, queued into the normal
review flow (`tools/review.py`) because sending it is an outbound action.

Exit codes: 0 ok, 3 waiting on an `interactive` answer, 1 a real error.
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

import capex_engine as ce  # noqa: E402
import drafting  # noqa: E402
import requisition_engine as req_engine  # noqa: E402
import store_ext  # noqa: E402
from money import fmt  # noqa: E402


def _require_enabled(settings) -> bool:
    if not bool(settings.agent_get("subagents.tender_capex.enabled", False)):
        print("Tender & CAPEX Approval AI is off. Enable it first: set "
             "subagents.tender_capex.enabled: true in config/agent.yaml. See "
             "docs/sub-agents.md.", file=sys.stderr)
        return False
    return True


def cmd_list(store, args) -> int:
    rows = store_ext.list_capex(store)
    if not rows:
        print("No CAPEX projects seeded. See fixtures/tender/capex-projects.json.")
        return 0
    for r in rows:
        print(f"  {r['id']}  {r['stage']:<15} {fmt(r['budget_eur']):>14}  "
             f"{r['title'][:40]:<40} {len(r['quotes'])} quote(s)")
    return 0


def cmd_show(store, args) -> int:
    row = store_ext.get_capex(store, args.id)
    if row is None:
        print(f"error: no CAPEX project {args.id}", file=sys.stderr)
        return 1
    print(json.dumps(row, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_draft(store, settings, args) -> int:
    row = store_ext.get_capex(store, args.id)
    if row is None:
        print(f"error: no CAPEX project {args.id}", file=sys.stderr)
        return 1
    status = ce.capex_status(row)
    if status == "already_approved":
        print(f"Every role on the chain has signed. {row['title']} is released to the chosen "
             f"vendor; nothing further for the AI to do.")
        return 0
    if status == "no_quotes":
        print("Nothing to compare until quotes are returned.")
        return 0

    threshold = float(settings.agent_get("subagents.tender_capex.three_quote_threshold_eur", 25000))
    three_quotes_on = bool(settings.agent_get("rules.capex-three-quotes", True))
    blocked = ce.three_quotes_gate(row["quotes"], row["budget_eur"], threshold, three_quotes_on)
    if blocked:
        print(blocked["reason"])
        return 0

    ranked = ce.rank_quotes(row["quotes"], row["budget_eur"])
    for q in ranked:
        print(f"  {q['vendor']:<20} {fmt(q['amount']):>14}  {q['weeks']:.0f}wk  {q['verdict']}")
    recommendation = ce.draft_recommendation(ranked, row["budget_eur"])
    if not settings.dry_run:
        store_ext.set_capex_recommendation(store, args.id, recommendation)
    print(f"\n{recommendation}")
    return 0


def cmd_approve(store, settings, args) -> int:
    row = store_ext.get_capex(store, args.id)
    if row is None:
        print(f"error: no CAPEX project {args.id}", file=sys.stderr)
        return 1
    if not row.get("recommendation"):
        print("The approval chain stays locked until a recommendation exists - nobody signs "
             f"off on a quote comparison that has not been made. Run "
             f"`python3 tools/capex.py draft-recommendation {args.id}` first.", file=sys.stderr)
        return 1

    # Resumable hand-off (marker-after-pend): the same convention as
    # `_match_cache` in tools/run.py - see docs/how-it-works.md, "Resumable
    # stages". A retry after the award-letter LLM call parks
    # (LLMPendingInteractive, exit 3) must not re-run req_engine.approve_role
    # - the role's decision was already made, and store_ext's own approvals
    # record for the project deliberately has NOT been updated yet (see
    # below), so the cache on the draft item is the only place that
    # decision survives between the two runs.
    cache_item = store.get_by_external("capex", row["id"])
    cached = (cache_item.payload or {}).get("_capex_approval_cache") if cache_item else None
    if cached and cached.get("role") == args.role:
        new_approvals, complete = cached["new_approvals"], True
    else:
        new_approvals, found = req_engine.approve_role(row["approvals"], args.role,
                                                        approved_at=utcnow())
        if not found:
            pending = ", ".join(req_engine.next_pending_roles(row["approvals"])) or "(none)"
            print(f"error: '{args.role}' is not a pending role on {args.id}. Pending: {pending}",
                 file=sys.stderr)
            return 1
        complete = req_engine.approvals_complete(new_approvals)

    if not complete:
        # Not the final role: nothing downstream can pend, so it is safe to
        # commit immediately - same as before.
        store_ext.set_capex_approvals(store, args.id, new_approvals, stage=None)
        pending = ", ".join(req_engine.next_pending_roles(new_approvals))
        print(f"{args.role} approved. Passed to the next role on the chain: {pending}")
        return 0
    print("Every role has signed - the project is released to the vendor.")
    if settings.dry_run:
        print("(--dry-run) would draft the letter of award here.")
        return 0

    winner = ce.rank_quotes(row["quotes"], row["budget_eur"])[0]
    payload = {"id": row["id"], "title": row["title"], "vendor": winner["vendor"],
              "amount": winner["amount"], "weeks": winner["weeks"],
              "recommendation": row["recommendation"]}
    item = store.upsert_item("capex", row["id"], kind="award_letter", payload=payload)
    if not cached:
        # Cache the final role's approval on the item BEFORE the LLM call
        # that can pend. `store_ext.set_capex_approvals` (the project's own
        # authoritative approvals record) is deliberately NOT written here -
        # only once the letter is actually drafted, below - so a project
        # stuck on a parked prompt still shows the real role as pending, not
        # a phantom "approved" with no letter to show for it.
        item = store.set_fields(
            item.id, payload={**payload, "_capex_approval_cache":
                              {"role": args.role, "new_approvals": new_approvals}}) or item
    if item.draft is not None:
        print(f"Letter of award already drafted - see `python3 tools/review.py show {item.id}`.")
        return 0
    try:
        letter = drafting.draft_award_letter(settings, store, item, payload,
                                             provider=args.provider)
    except LLMPendingInteractive as exc:
        print(str(exc))
        return 3
    except LLMSchemaError as exc:
        store.set_fields(item.id, error=str(exc))
        store.transition(item.id, "needs_human", actor="agent",
                         detail={"error": "award_letter_schema_error"})
        print(f"Letter-of-award draft failed ({exc}) - queued as needs_human, {item.id}.")
        return 0
    # The LLM call resolved: only now is it safe to record the final
    # approval on the project itself and release it to the vendor.
    store_ext.set_capex_approvals(store, args.id, new_approvals, stage="approved")
    store.set_fields(item.id, draft=letter, payload=payload)  # clears the resumed marker
    lang_reason = drafting.check_language(settings, letter)
    if lang_reason:
        store.transition(item.id, "needs_human", actor="agent",
                         detail={"vendor": winner["vendor"], "language": lang_reason})
        print(f"Letter of award drafted but held for review ({lang_reason}) - see "
             f"`python3 tools/review.py show {item.id}`.")
        return 0
    store.transition(item.id, "pending_review", actor="agent", detail={"vendor": winner["vendor"]})
    print(f"Letter of award drafted and queued - see `python3 tools/review.py show {item.id}` "
         f"then `python3 tools/review.py approve {item.id}`.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="every CAPEX project")

    p_show = sub.add_parser("show", help="full detail for one project")
    p_show.add_argument("id")

    p_draft = sub.add_parser("draft-recommendation", help="score the quotes, draft a recommendation")
    p_draft.add_argument("id")

    p_approve = sub.add_parser("approve", help="approve one role on the chain")
    p_approve.add_argument("id")
    p_approve.add_argument("--role", required=True)
    p_approve.add_argument("--provider", default=None)

    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1
    if not _require_enabled(settings):
        return 1

    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        roles = settings.agent_get("subagents.tender_capex.roles",
                                   ["Chief Engineer", "General Manager", "Owner rep"])
        store_ext.seed_capex(store, REPO_ROOT / "fixtures" / "tender" / "capex-projects.json",
                            roles=roles)
        if args.command == "list":
            return cmd_list(store, args)
        if args.command == "show":
            return cmd_show(store, args)
        if args.command == "draft-recommendation":
            return cmd_draft(store, settings, args)
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
