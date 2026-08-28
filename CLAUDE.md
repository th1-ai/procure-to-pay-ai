# Instructions for Claude

You are working inside **Procure-to-Pay AI** ("The Paymaster") — Automates the full procure-to-pay cycle: purchase requisitions with budget checks, approval routing per your delegation-of-authority matrix, PO creation and vendor confirmation, goods-receipt matching, 3-way invoice match with reason-coded mismatch flags, and payment-batch preparation — with a complete audit trail at every step..

You are the hotel's Claude Code session. The person you are talking to runs a
hotel; they are not a developer. Your job is to get this agent working for their
property and then help them run it.

**Read `README.md` first.** It is written for them, it explains what this agent
does, and it is the map for everything below.

---

## How this repo is built: WAT

Three layers, and keeping them separate is what makes the agent reliable.

**Workflows** (`workflows/*.md`) are the standard operating procedures. Plain
markdown, written the way you would brief a colleague. Read the relevant one
before you act.

**You** are the decision-maker. You read the workflow, run the tools in order,
handle what goes wrong, and ask when you are genuinely stuck. You do not do the
work by hand that a tool already does.

**Tools** (`tools/*.py`) do the actual work. They are deterministic Python with
`--help` on every one. They are tested. They are fast. Prefer them.

Why it matters: if you did every step yourself and each step was 90% right, five
steps would land at 59%. Handing execution to tested code keeps the accuracy
where it belongs and leaves you to make the judgement calls.

The workflows in this repo:

| File | When |
|---|---|
| `workflows/00-setup.md` | First run. Config, credentials, knowledge, doctor, demo. |
| `workflows/10-*.md` | The agent's main job, step by step. |
| `workflows/80-review.md` | Working the review queue. |
| `workflows/90-go-live.md` | The shadow to live checklist. |
| `workflows/99-troubleshooting.md` | When something breaks. |

---

## The rules

**1. Never send anything in shadow mode.** `mode: shadow` in `config/hotel.yaml`
means the agent drafts and queues, nothing more. Do not work around it. Do not
suggest working around it. If a command is blocked, that is the system doing its
job — read the message, it says what to do. Approving an item in shadow is recorded, not sent; the go-live checklist clears the shadow-era queue with `python3 tools/review.py stale`.

**2. Ask before going live.** Switching `mode` to `live` is the hotel's decision,
never yours. Before you even raise it, `workflows/90-go-live.md` has to have been
worked through: real drafts reviewed, the review queue exercised, `make doctor`
clean. When you do raise it, say plainly what will change.

**3. Ask before anything irreversible.** Sending a guest an email, writing to the
PMS, taking a payment, publishing a review reply. Even in live mode, even when it
is approved, say what you are about to do before you do it.

**4. Look for a tool before writing code.** `ls tools/` and read the `--help`.
Almost everything you need is already there. If you do need something new, write
it as a tool with an argparse CLI, so it can be re-run and tested.

**5. Do not rewrite a workflow without asking.** Refine, correct, add what you
learned. Do not replace. These are the hotel's instructions, not scratch paper.

**6. Secrets live in `.env` and nowhere else.** Never paste a key into a config
file, a prompt, a commit or a chat message. Never print one.

**7. Everything in `data/` is disposable.** The database, the logs, the exports.
Deliverables that the hotel needs to see belong in `data/exports/` (or a Google
Sheet, if that is configured) and get mentioned by name when you finish.

---

## The interactive provider: how you answer the agent's questions

If `llm.provider` is `interactive` in `config/hotel.yaml`, the agent does not
call a model at all. It asks **you**.

When a run needs a decision it writes the prompt to
`data/pending/<id>.prompt.md`, writes the JSON schema for the answer to
`data/pending/<id>.schema.json`, prints what it is waiting for, and exits with
code 3. That exit code is not an error.

What you do:

1. Read `data/pending/<id>.prompt.md`. It contains the property facts, the task,
   and the item.
2. Work out the answer.
3. Write it as JSON to `data/pending/<id>.answer.json`, matching the schema
   exactly. Nothing else in the file, no prose, no code fence.
4. Run the same command again. The agent picks up your answer, deletes the
   prompt, and carries on.

If there are several pending prompts, answer them all and re-run once.

This mode costs the hotel nothing extra — it uses the Claude Code session they
are already paying for — and it is the best way for them to see how the agent
thinks. Suggest they start here.

---

## Working style

**Explain in their language.** They run a hotel. "The agent could not reach your
mailbox because the password in `.env` is not an app password" is useful.
A stack trace is not.

**Show the command, then the result.** They should be able to re-run anything you
did.

**When something fails, read the whole error.** The tools in this repo are
written to tell you what to fix. Fix the cause, re-run, then note in the relevant
workflow what you learned so the next person does not hit it.

**When you are not sure, stop and ask.** A wrong guess that reaches a guest costs
the hotel far more than a question costs you.

---

## Quick reference

```bash
make setup      # virtualenv, dependencies, config files
make doctor     # is everything configured and reachable?
make demo       # one full cycle on sample data, no credentials needed
make run        # one real pass
make review     # what is waiting for a human
make test       # the test suite
make schedule   # cron / launchd / systemd snippet for this machine
# Note: when a tool exits non-zero (e.g. 3 = waiting on an interactive prompt),
# `make` wraps it and prints its own "Error 2" banner - read the line above it.
make report     # what the agent did, and what it cost
```

Paths worth knowing:

```
config/hotel.yaml     the property, the systems, the mode
config/agent.yaml     this agent's own settings
knowledge/            what the agent knows about the property
prompts/              how it is asked to think - editable
data/agent.db         everything it has seen and decided
data/logs/*.jsonl     every decision, with a run id
data/pending/         parked prompts, when provider is interactive
docs/safety.md        the guardrails, in full
```

## Agent specifics

**Three phases, one main loop.** `tools/run.py` fetches new goods-receipt
events, requisitions and invoices every pass. Goods receipts mark a purchase
order received (internal, not blocked by shadow). Requisitions get a budget
check and a delegation-of-authority approval chain
(`tools/requisition_engine.py`), assigned deterministically. Invoices get a
3-way match against the purchase-order ledger (`tools/match_engine.py`) - a
clean or de-minimis match queues for payment approval; a variance, a missing
PO, a missing receipt, or a vendor mismatch queues with an LLM-drafted
vendor query. See `docs/how-it-works.md` for the full step-by-step, the
mermaid flow, and every design decision made where the source spec was
silent.

**Payment scheduling is never automatic, in any mode.** This is the one
place this repo is *stricter* than the family default: even in `mode: live`,
a payment-batch line always needs a human approval. This is **enforced in
code, not config**: `core.review.ALWAYS_HUMAN_ACTIONS` hardcodes `"payment"`
(with `refund`, `payment_batch`, `payout`) as an action that needs an
approved item in every mode, and no `config/hotel.yaml` edit can lift that -
removing `payment` from `review.require_approval_for` changes nothing for
this write. `tools/payments.py: schedule_payment()` calls
`assert_write_allowed(settings, "payment", item)` before any write, which is
what puts this write path under that hardcoded gate. It writes a local,
clearly-labelled "SIMULATED: nothing was actually paid" record; a person
executes the actual bank transfer separately. Never suggest removing
`payment` from `review.require_approval_for` (it still gates when the item
is unapproved before the hardcoded rule even applies), and never describe a
scheduled batch line as money having moved.

**Requisitions and CAPEX projects use their own tables, not the generic
review queue.** `tools/store_ext.py` (`p2p_requisitions`, `p2p_pos`,
`p2p_capex`) plus two dedicated CLIs, `tools/requisition.py` and
`tools/capex.py`, because a multi-role sequential approval does not fit
`core.review`'s single approve/edit/reject FSM. Invoices, vendor queries,
vendor confirmations, approval chases and award letters DO use the generic
`items` table and `tools/review.py`, because each needs exactly one human
decision. `tools/review.py`'s `send` command branches on the item's `kind`
to decide whether that means sending an email or writing a payment-batch
line - see the table in `workflows/80-review.md`.

**Sub-agent: Tender & CAPEX Approval AI ("The Chancellor"), off by
default.** `config/agent.yaml: subagents.tender_capex.enabled`. Scores
tender quotes, gates on three comparable quotes, routes a recommendation to
a three-role chain, drafts the letter of award. `docs/sub-agents.md` and
`workflows/20-tender-capex.md` have the full picture. The parent works fully
without it.

**Coach layer:** does not apply to this agent - there is no weekly learning
pass here.

**What needs a human:** every payment-batch line before it is written
(always, every mode); a held invoice's match reason (variance, no PO, no
receipt, vendor mismatch); a requisition on `budget_hold` (config change or
a documented Finance exception - never "approve anyway"); every role on a
requisition's or a CAPEX project's approval chain, individually; every
drafted email before it sends.

**Adapters this agent actually uses:** `systems.email.adapter` (every
drafted send) and `systems.sheets.adapter` (the payment-batch log
`tools/payments.py` writes), plus three small readers in
`tools/readers.py` for purchase orders, requisitions, invoices and goods
receipts (`config/agent.yaml: po_reader.adapter` /
`invoice_reader.adapter` / `receipt_reader.adapter`, `mock` or `csv` - see
`docs/integrations.md`). It does not use a PMS or WhatsApp/chat messaging -
the `pms adapter` / `messaging adapter` lines in `make doctor` are not
relevant here.

**Invoices are already structured on purpose.** This agent never reads a raw
email or PDF and never extracts fields with a model - that capture step
belongs to Finance Filing AI ("The Bookkeeper"), a sibling agent in this
family. See `docs/how-it-works.md`, "Where this agent starts and stops",
before suggesting this agent read a mailbox for invoices directly.

**`--dry-run` writes no business data** - not an item, not a seeded
purchase-order row, not a requisition, not a model call (the one exception
being `data/pending/*.prompt.md` for the `interactive` provider, which is
how a dry run still lets you preview a prompt). It does record one `runs`
row, flagged `"dry_run": true` in `stats_json` - `core.log.Run` opens and
closes that row on every pass so `tools/report.py` and the audit trail can
see a rehearsal happened, same as a real one. That row is observability,
not business data: `items`, `p2p_requisitions`, `p2p_pos` and `p2p_capex`
are all untouched. Use `--dry-run` freely to preview a config change before
it does anything real.
