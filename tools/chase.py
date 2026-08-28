#!/usr/bin/env python3
"""tools/chase.py - chase an approver whose sign-off is overdue.

    python3 tools/chase.py
    python3 tools/chase.py --dry-run

Reads `core.store`'s ticklers (the ``tasks`` table): every requisition still
`awaiting_approval` gets one when `tools/run.py` first assigns its chain
(`config/agent.yaml: chase.gap_days` apart). This addresses the spec's own
open question - "no approval sits unactioned is unmeasured" - for
requisitions; the Tender & CAPEX sub-agent does not chase yet, see
docs/sub-agents.md.

After `chase.max_follow_ups` reminders with no sign-off, the task is marked
``escalated`` (core.store.advance_task) rather than chased again - see it
with `python3 tools/review.py list --kind approval_chase`.

Exit codes: 0 ok, 3 waiting on an `interactive` answer, 1 a real error.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, load_settings  # noqa: E402
from core.llm import LLMError, LLMPendingInteractive, LLMSchemaError  # noqa: E402
from core.log import Run, get_logger, summary_line  # noqa: E402
from core.store import Store  # noqa: E402

import drafting  # noqa: E402
import requisition_engine as req_engine  # noqa: E402
import store_ext  # noqa: E402

log = get_logger("chase")


def _days_waiting(created_at: str) -> int:
    try:
        created = datetime.fromisoformat(created_at)
    except (TypeError, ValueError):
        return 0
    return max(0, (datetime.now(timezone.utc) - created).days)


def one_pass(settings, store, *, limit: int, provider: str | None) -> tuple[int, dict]:
    stats = {"processed": 0, "drafted": 0, "escalated": 0, "skipped": 0}
    gap_days = int(settings.agent_get("chase.gap_days", 3))
    with Run("approval-chase", settings, store) as run:
        tasks = store.due_tasks("requisition_approval", limit=limit)
        for task in tasks:
            stats["processed"] += 1
            req = store_ext.get_requisition(store, task.ref_id)
            if req is None or req["stage"] != "awaiting_approval":
                if not settings.dry_run:
                    store.close_task(task.id, status="done")
                stats["skipped"] += 1
                continue
            pending_roles = req_engine.next_pending_roles(req["approvals"])
            if not pending_roles:
                if not settings.dry_run:
                    store.close_task(task.id, status="done")
                stats["skipped"] += 1
                continue
            role = pending_roles[0]
            days = _days_waiting(req["created_at"])
            if settings.dry_run:
                log.info("(--dry-run) would chase", id=task.ref_id, role=role, days=days)
                stats["drafted"] += 1
                continue
            chase_id = f"{task.ref_id}-{task.follow_up_count + 1}"
            item = store.upsert_item("requisition_approval", chase_id, kind="approval_chase",
                                     payload={**req, "role_waiting": role, "days_waiting": days})
            if item.draft is not None:
                stats["skipped"] += 1
                continue
            try:
                chase = drafting.draft_approval_chase(settings, store, item, req, role, days,
                                                      provider=provider)
            except LLMPendingInteractive as exc:
                run.stats = dict(stats)
                print(str(exc))
                return 3, stats
            except LLMSchemaError as exc:
                store.set_fields(item.id, error=str(exc))
                store.transition(item.id, "needs_human", actor="agent",
                                 detail={"error": "approval_chase_schema_error"})
                stats["drafted"] += 1
                continue
            store.set_fields(item.id, draft=chase)
            lang_reason = drafting.check_language(settings, chase)
            detail = {"role": role, "days_waiting": days}
            if lang_reason:
                detail["language"] = lang_reason
            store.transition(item.id, "needs_human" if lang_reason else "pending_review",
                             actor="agent", detail=detail)
            advanced = store.advance_task(task.id, gap_days=gap_days, note=f"chased {role}")
            stats["drafted"] += 1
            if advanced.status == "escalated":
                stats["escalated"] += 1
                log.warn("requisition escalated - chased the maximum number of times",
                        id=task.ref_id, role=role)
        run.stats = dict(stats)
    return 0, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--provider", default=None)
    args = parser.parse_args(argv)

    try:
        settings = load_settings(provider=args.provider, dry_run=args.dry_run)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        code, stats = one_pass(settings, store, limit=args.limit, provider=args.provider)
        print(f"{stats['drafted']} chase(s) drafted, {stats['escalated']} escalated, "
             f"{stats['skipped']} skipped ({settings.mode}).")
        return code
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
