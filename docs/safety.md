# Guardrails and safety

This agent decides what gets paid and what does not, and drafts email that
goes to vendors and to people who sign off spend. Everything below is built
in, not optional, and this page explains what it does and what is left for
you to decide.

## The two modes

| Mode | What happens |
|---|---|
| `shadow` (default) | The agent reads, decides, drafts and queues. It **never** sends an email and **never** writes a payment-batch line. Approving, editing or rejecting a draft records your decision (and, for an edit, teaches the agent) but sends nothing. At go-live, `python3 tools/review.py stale` clears that shadow-era queue so nothing old goes out by surprise. |
| `live` | Items you approved are really acted on: an email really sends, a payment-batch line really gets written. Everything else still waits. |

`mode` lives in `config/hotel.yaml`. It is a global kill switch: flipping it
back to `shadow` stops every outbound action immediately, mid-schedule, with
no other change. `config/agent.yaml` can be stricter than `hotel.yaml`,
never looser.

Two more brakes:

- `make run ARGS="--dry-run"` (and every tool's `--dry-run`) computes
  everything and writes no business data, even in live mode - `items`,
  `p2p_requisitions`, `p2p_pos` and `p2p_capex` are all untouched. It does
  record one `runs` row flagged `"dry_run": true` in `stats_json` -
  observability, not business data - so a rehearsal shows up in the audit
  trail alongside real runs.
- `review.require_approval_for` in `config/hotel.yaml` lists the actions
  that need a human even in live mode. The defaults are `send_email`,
  `send_message`, `pms_write`, `payment`, `publish` - **never remove
  `payment`.** For `payment` this is belt and braces, not the only
  protection: `core.review.ALWAYS_HUMAN_ACTIONS` hardcodes `payment` (with
  `refund`, `payment_batch`, `payout`) as needing an approved item in every
  mode, **enforced in code, not config** - no `hotel.yaml` edit, however it
  happens, can make an unapproved payment-batch write go through.

Every outbound action in the codebase goes through one function,
`core/review.py:assert_write_allowed`. There is no second path. A requisition
role's approval and a purchase order being created are not gated by mode -
they are internal records, not something leaving the building - but the
vendor confirmation email they trigger is, and always goes through the same
guard as everything else. See `docs/how-it-works.md`, design decisions 4-6.

## Never releases money

The strongest line in the roster's promise, and the one this agent is built
around: **payment authorisation stays with your signatories; dual approval
at the bank remains human.** There is no code path in this repo that moves
money. `tools/payments.py:schedule_payment` writes a local record - a JSON
file under `data/exports/payment-batches/` and a row in a sheet - labelled
"SIMULATED: nothing was actually paid" every time. A person reads that
record and executes the batch in your own banking system. The `Payments`
adapter family in `core/adapters/base.py` is deliberately read-mostly
(`list_charges`, `refund`) - there is no "release a payment" method to call,
on purpose.

## The review queue

Nothing leaves the building without passing through the queue.

```bash
make review                        # what is waiting
python3 tools/review.py show <id>  # the full draft and how it got there
python3 tools/review.py approve <id>
python3 tools/review.py edit <id> --body-file my-version.txt
python3 tools/review.py reject <id> --reason "already resolved"
```

An invoice moves `new -> pending_review` (cleared, awaiting your approval to
schedule it) or `new -> needs_human` (a variance, a missing PO, a missing
receipt - with a vendor query already drafted). Only `tools/review.py` can
write `approved`, `edited` or `rejected`; only `send` can write
`sending`/`sent`. A crash between "about to send" and "sent" is picked up on
the next pass and shown to you as failed rather than silently retried.

## What the agent will not do

- Send anything, or write a payment-batch line, while `mode: shadow`.
- Send an approval-required item a human has not approved.
- Release, refund or move money, in any mode.
- Clear a payment without a matching purchase order and a confirmed goods
  receipt - both are unconditional, not tolerance-gated.
- Approve its own requisition or CAPEX chain, or invent an approval role.
  Every sign-off is a person's own action, recorded with who and when.
- Invent a fact that is not in the invoice, the PO ledger or the
  requisition it was given. When a match cannot be made confidently, it
  queues the item and drafts a question instead of guessing.

## Data handling

**What leaves your machine.** With `llm.provider: anthropic` or
`claude-code`, the prompt for a vendor query, confirmation, chase or award
letter goes to Anthropic. That prompt contains the invoice or PO details and
the relevant property facts - never a full vendor ledger, never a bank
detail. With `llm.provider: mock` or `interactive`, nothing leaves the
machine at all.

**What is stored, and where.** Everything lives in `data/` inside this
folder: `agent.db` (SQLite), `logs/*.jsonl`, `exports/`. `data/` is
gitignored. There is no cloud service behind this repo and no telemetry.

**Retention.** `privacy.retention_days` (default 365) is how long processed
items stay in the database. Deleting `data/agent.db` deletes everything the
agent knows - including the payment-batch history, so export
`tools/report.py` first if you need it.

## GDPR, in practice

The data this agent handles is mostly commercial, not personal - vendor
names, invoice amounts, purchase orders. It still touches names and email
addresses of approvers and vendor contacts:

- **You are the controller.** This software runs on your machine, under your
  control, on your data. TH1 does not receive it.
- **Your model provider is a processor**, if you use `anthropic` or
  `claude-code`. Check their data processing terms and record them in your
  processing register.
- **Purpose and minimisation.** Keep `vendor_emails` and `approver_emails`
  to the addresses actually needed for this workflow, not a full staff
  directory.
- **Right to erasure**, for a former employee's approval history or a
  vendor contact who asks: *"Delete every item in data/agent.db and every
  row in p2p_requisitions/p2p_pos whose payload mentions this name or
  address, and tell me how many rows you removed."*

This is a practical summary, not legal advice.

## Telling people they are talking to AI

The EU AI Act (Article 50) requires that a person is told when they are
interacting with an AI system, unless it is obvious. Whether it applies to
you depends on where you and your vendors are, but it is good practice
everywhere. Add a line like this to `knowledge/signature.md`, appended to
every email this agent sends:

> This message was drafted with AI assistance and reviewed by our team
> before sending. Reply to this message any time to reach a person
> directly.

## Subscription or API: an honest note

Two ways to pay for the reasoning:

**Your Claude Code subscription** (`llm.provider: claude-code` or
`interactive`). Flat monthly cost, no per-message billing - genuinely the
cheapest way to run this agent for a small hotel or a single property. The
caveat, plainly: a personal Pro or Max subscription is intended for
interactive use, and Anthropic's usage policy and rate limits apply to
automated use of it. A handful of scheduled runs a day is normal; pointing a
busy AP inbox at it around the clock is not.

**The Anthropic API** (`llm.provider: anthropic`). Pay per token, proper
rate limits, usage you can attribute. The right answer for a group running
this across several properties. `python3 tools/report.py` shows what you are
spending.

Start on the subscription while you are learning what the agent does. Move
to the API when it becomes part of how the group runs.

## If something goes wrong

1. `mode: shadow` in `config/hotel.yaml`, or `AGENT_MODE=shadow` in `.env`.
   Every outbound action and every payment-batch write stops on the next
   pass.
2. Remove the schedule (`crontab -e`, `launchctl unload`, or
   `systemctl disable --now <slug>.timer`).
3. `make doctor` to see what the agent thinks its state is.
4. `data/logs/*.jsonl` has every decision, with the run id, in order.
