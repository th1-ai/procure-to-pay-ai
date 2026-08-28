# Accounts payable FAQ - Hotel Aurora

<!--
Copy this to knowledge/faq.md if you want it. Unlike property.md, this file
is NOT read by any of this agent's prompts (see knowledge/README.md) - it is
kept purely as a reference for whoever in accounts payable is new to the
process, or for onboarding a Claude Code session that needs the "why" behind
a decision this agent makes. Skip it entirely if you do not want it; nothing
in the agent's behaviour depends on it.
-->

## An invoice arrived with no purchase order. What happens?

Under `matching.no_po_threshold_eur`, a small invoice from a vendor on
`approved_vendors` clears automatically. Anything above the threshold, or
from a vendor not on that list, is held and the agent drafts a query asking
who placed the order, so a retrospective PO can be raised.

## The invoiced amount does not match the PO. What happens?

Held, unless the difference is inside both tolerances in
`matching.tolerance_pct` / `matching.tolerance_eur` (currently logged for
review, not held). Outside either tolerance, the agent drafts a query to the
vendor asking for a corrected invoice or a credit note - never partial
payment against a dispute.

## How do I add a new approved vendor?

Add the name to `config/agent.yaml: approved_vendors` and an email address
to `vendor_emails`. Confirm bank details and a VAT/tax ID are on file before
their first invoice is due - see `knowledge/property.md`, "Vendor terms".

## How do I change who signs off a requisition?

Edit `config/agent.yaml: delegation_matrix`. The bands are amount-based and
ascending - the chain for a higher band should list every role in the bands
below it, since a higher band does not automatically include a lower one.

## An approver has not signed for days. What happens?

`tools/chase.py` sends a reminder every `chase.gap_days` days. After
`chase.max_follow_ups` reminders with no sign-off, the requisition is
escalated rather than chased again - see it with
`python3 tools/review.py list --kind approval_chase`.

## Can this agent actually pay an invoice?

No. It prepares a payment batch - a record for a human's own banking
process - and a person always executes the transfer separately. See
`docs/safety.md`, "Never releases money."

## A CAPEX project's approval chain is complete. What happens?

The last role's approval drafts a letter of award automatically and queues
it into the normal review flow - see `workflows/20-tender-capex.md`, step 6.
