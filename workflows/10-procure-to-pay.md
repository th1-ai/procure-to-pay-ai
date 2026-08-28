# Workflow: the procure-to-pay loop

Objective: run the full chain - requisition, budget check, approval, PO,
vendor confirmation, goods receipt, 3-way match, payment-batch preparation -
and know exactly what needs a human at each step.

See `docs/how-it-works.md` for the mermaid flow and every design decision.
This workflow is the step-by-step version.

## Steps

1. **Run one pass.**
   ```bash
   make run
   make run ARGS="--limit 5"       # just the first five records per phase
   make run ARGS="--dry-run"       # compute everything, write nothing
   ```
   Every pass does three things, in order: applies any new goods-receipt
   events, budget-checks and routes any new requisitions, and 3-way matches
   any new invoices. Read the printed summary line -
   `N items processed, M drafted, 0 sent (shadow)` - `drafted` counts the
   held invoices that got an LLM-drafted vendor query.

   If `llm.provider` is `interactive`, a held invoice's vendor-query draft can
   park a prompt and exit with code 3. Answer it in `data/pending/*.prompt.md`
   / `*.answer.json` and re-run the same command - see `CLAUDE.md`.

2. **Work the requisition queue.**
   ```bash
   python3 tools/requisition.py list
   python3 tools/requisition.py show <id>
   python3 tools/requisition.py approve <id> --role "Department Head"
   ```
   A requisition on `budget_hold` needs a config change
   (`config/agent.yaml: budgets`) or a documented exception from Finance -
   there is no "approve anyway" for a budget failure. One held on
   `awaiting_approval` needs every role in its chain approved, in any order;
   approving the last one creates the purchase order and drafts the vendor
   confirmation, which lands in the normal review queue (step 4).

   If `llm.provider` is `interactive`, that last role's vendor-confirmation
   draft can itself park a prompt and exit with code 3, exactly like step 1's
   invoice hold. Answer it in `data/pending/*.prompt.md` / `*.answer.json`
   and re-run the **exact same** `approve <id> --role "..."` command - it
   resumes from the cached approval and PO number rather than re-approving
   the role or drafting a second PO, and the requisition stays honestly
   `awaiting_approval` until the confirmation is actually drafted. See
   `docs/how-it-works.md`, "Resumable stages".

3. **Record a goods receipt directly**, if it did not arrive through
   `fixtures/inbound/goods-receipts/` or a CSV import:
   ```bash
   python3 tools/receiving.py record PO-00104 --note "signed by kitchen"
   ```
   This is not blocked by `mode: shadow` - it records a fact from your own
   receiving process, nothing leaves the building. See
   `docs/how-it-works.md` design decision 4.

4. **Preview and work the payment batch.**
   ```bash
   python3 tools/payment_batch.py preview
   make review
   python3 tools/review.py show <id>
   python3 tools/review.py approve <id>
   python3 tools/review.py send
   ```
   A cleared invoice (`match: "ok"`) needs your approval before it is a
   payment-batch line - this is never automatic, even in `mode: live`; see
   `docs/safety.md`. A held invoice (`variance`, `no_po`, `no_receipt`,
   `vendor_mismatch`) has a drafted vendor-query email waiting in the same
   queue - approve or edit it, then `send`.

5. **After `send`.** A payment-batch line writes
   `data/exports/payment-batches/<date>.json` and a row in the payments
   sheet, both labelled "SIMULATED: nothing was actually paid" - a person
   still executes the batch in your own banking system. See
   `docs/how-it-works.md`, "Payments honesty".

6. **Chase overdue approvals** (its own scheduled job):
   ```bash
   python3 tools/chase.py
   ```
   Drafts a reminder for the first pending role on any requisition that has
   waited `chase.gap_days` since it last moved, and queues it into the same
   review flow. After `chase.max_follow_ups` reminders the task is
   `escalated`, not chased again - `python3 tools/review.py list --kind approval_chase`
   shows what has gone out.

## Rules

- Only `tools/review.py` writes `approved` / `edited` / `rejected`. Only
  `send` writes `sending` / `sent`.
- A requisition's approval chain and a purchase order's existence are never
  blocked by `mode: shadow` - only what leaves the building is (an email, a
  payment-batch line). Confirm with the hotel before approving a real
  requisition role on their behalf; that is their sign-off, not yours.
- `--dry-run` computes the same decision and writes no business data - not
  an item, not a requisition, not a seeded PO row. It does record one `runs`
  row flagged `"dry_run": true` in `stats_json`, so the rehearsal shows up
  in the audit trail rather than disappearing without a trace.
