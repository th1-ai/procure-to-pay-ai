# Workflow: troubleshooting

Read the whole error before doing anything - every tool here is written to
say what broke and what to do about it. If you fix something not covered
below, add it here.

## `make doctor` shows a FAIL

Each `FAIL` line has a `->` fix hint right under it. Common ones:

- **`hotel identity`: name is still 'Hotel Aurora'.** Expected on a fresh
  clone. Edit `config/hotel.yaml`.
- **`delegation matrix`: no bands in config/agent.yaml.** Copy
  `config/agent.example.yaml` to `config/agent.yaml` if `make setup` has not
  run yet.
- **`budgets`: no budget lines in config/agent.yaml.** Same fix - and add
  every budget line the property actually tracks; a requisition against an
  unlisted line is always held.
- **`llm provider`: claude-code selected but `claude` is not on PATH.**
  Install Claude Code, or switch `llm.provider` to `interactive` or
  `anthropic` in `config/hotel.yaml`.
- **An adapter shows FAIL, not warn.** `universal`/`built` adapters fail loud
  when misconfigured (a `warn` is reserved for stubs). Read the `detail`
  column - it names the missing file or variable. `pms adapter` and
  `messaging adapter` are irrelevant to this agent (it uses neither) - ignore
  their lines.

## `make demo` does not print `DEMO OK`

- Make sure `make setup` ran first (`.venv` must exist).
- `tools/demo.py` forces `llm.provider=mock` and reads
  `fixtures/hotel/purchase-orders.json` and `fixtures/inbound/` - if you
  deleted or renamed those files, restore them from git.
- Read the traceback if there is one; `tools/demo.py` does not swallow errors
  on purpose, so a fixture problem shows up immediately.

## `make run` / `tools/chase.py` / `tools/requisition.py approve` exits with code 3

Not an error. `llm.provider: interactive` parked a prompt. Read
`data/pending/*.prompt.md`, write your answer to the matching
`*.answer.json` (JSON only, matching the schema shown, no prose, no code
fence), and run the same command again.

## `python3 tools/review.py send` reports "no email address on file"

`config/agent.yaml: vendor_emails` (invoices, vendor confirmations, award
letters) or `approver_emails` (approval chases) is missing that vendor or
role. It ships blank on purpose - fill in the real addresses, then
`python3 tools/review.py retry <id>` (the approval was kept, not lost).

## A requisition is stuck on `budget_hold`

That is correct if the amount genuinely exceeds what remains on the budget
line. Either raise `config/agent.yaml: budgets` for that line, or record the
exception Finance has agreed to and route around it manually - there is no
"approve anyway" for a budget failure, by design.

## An invoice holds on `no_receipt` and you know the goods arrived

```bash
python3 tools/receiving.py record <PO-ref> --note "..."
make run
```
The hold clears on the next pass once the receipt is on file - the match
never pays before a receipt exists, whatever the amounts say.

## An item is stuck at `sending`

A process died between claiming an item and finishing the send.
`tools/run.py` calls `core.store.Store.reap_stuck_sending()` on every pass,
which moves anything stuck for more than 30 minutes to `failed` so you see it
in the queue instead of it vanishing. Use
`python3 tools/review.py retry <id>` once the cause is fixed.

## `python3 tools/*.py` says `ModuleNotFoundError: No module named 'core'`

You ran it with a Python that is not the repo's virtualenv, or from outside
the repo root. Use `make run` / `make doctor` / etc. (they call
`.venv/bin/python` for you), or run `.venv/bin/python tools/run.py` directly
from the repo root.

## Still stuck

`data/logs/*.jsonl` has every decision the agent made, in order, with a run
id. `python3 tools/review.py show <id>` has the full event trail for one item.
If neither explains it, that is a real bug - describe exactly what you ran
and what you expected, and ask.
