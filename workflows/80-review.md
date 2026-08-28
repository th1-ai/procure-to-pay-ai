# Workflow: working the review queue

Objective: turn a queued item into a decision - approve, edit, or reject -
and, once approved, actually act on it.

Nothing leaves the building without going through this. `mode: shadow`
blocks every guarded write - a payment-batch line, a vendor-query email, a
vendor-confirmation email, an approval-chase email, a letter of award - even
for an item you have just approved; see `docs/safety.md`.

## Steps

1. **See what is waiting.**
   ```bash
   make review
   python3 tools/review.py list --kind invoice
   python3 tools/review.py list --status needs_human
   ```
   Each line shows the item id, its status, its kind, and a short label.
   `kind` matters here more than in most agents in this family - it decides
   what `send` will actually do:

   | kind | what a held item's draft contains | what `send` does |
   |---|---|---|
   | `invoice`, `draft.action == "hold"` | a drafted vendor-query email | sends the query to the vendor |
   | `invoice`, `draft.action == "schedule"` | the match verdict, no email | writes one payment-batch line |
   | `vendor_confirmation` | a drafted PO confirmation email | sends it to the vendor |
   | `approval_chase` | a drafted reminder email | sends it to the approver |
   | `award_letter` | a drafted letter of award | sends it to the winning vendor |

2. **Read one in full.**
   ```bash
   python3 tools/review.py show <id>
   ```
   Prints the item, its draft, and the full event history. Summarise it in
   plain language for whoever is reviewing it - do not paste the raw JSON at
   them. For a held invoice, say plainly what did not match and why (the
   `reason` string is written to be read aloud).

3. **Decide.**
   ```bash
   python3 tools/review.py approve <id>
   python3 tools/review.py edit <id> --body-file my-version.txt [--subject "New subject"]
   python3 tools/review.py reject <id> --reason "already resolved by phone"
   ```
   `edit` rewrites the email body for every kind except a cleared invoice
   (which has no email to edit - there is nothing to send but the payment
   line itself). Rejecting an invoice does not change its match verdict; the
   underlying variance or missing PO is still there next time you look.

4. **Act on what was approved.**
   ```bash
   python3 tools/review.py send
   ```
   Claims everything `approved`/`edited` and does whatever that kind's `send`
   step means (the table above). In `mode: shadow` every one of these is
   blocked - the approval is kept, not lost, and the message says exactly
   that: `blocked <id> (approval kept): ...`.

5. **A blocked or failed send.**
   ```bash
   python3 tools/review.py retry <id>
   ```
   re-queues it for another attempt. A block for a missing vendor/approver
   email address needs a config fix first
   (`config/agent.yaml: vendor_emails` / `approver_emails`) - `send` will
   keep blocking it otherwise, in any mode.

## Rules

- Only `tools/review.py` writes `approved` / `edited` / `rejected`. Only
  `send` writes `sending` / `sent`.
- A payment-batch line never sends automatically, in any mode -
  `review.require_approval_for` in `config/hotel.yaml` ships with `payment`
  in it; do not remove it. See `docs/safety.md`.
- Confirm with the hotel before sending anything, even an approved item, the
  first few times. `workflows/90-go-live.md` covers when to stop doing that.
