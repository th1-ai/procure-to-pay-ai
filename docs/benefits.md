# The business case

**Why.** P2P is where hotel groups leak money and time - manual hand-offs,
chased approvals, unmatched invoices. This enforces the controls you already
wrote down.

**Output.** Invoice-to-payment cycle from days to hours; every 3-way
mismatch caught and reason-coded automatically.

**ROI.** -75% Invoice-to-payment cycle (labor).

(Quoted verbatim from the roster - see `README.md` for the full promise.)

## The problem this solves

A purchase order sits in an inbox until someone chases it. An invoice
arrives and a person has to remember what was ordered, whether it turned up,
and whether the price on the invoice is the price that was agreed - usually
by opening three different systems and doing the arithmetic by hand. Most of
the time it matches and the check was wasted effort; occasionally it does
not, and that is exactly the invoice that gets paid anyway because nobody
had time to look closely. This agent does the arithmetic on every invoice,
every time, in seconds, and only interrupts a person for the ones that
actually need a decision: a price that moved, a receipt that never arrived,
a purchase order nobody can find.

## What to measure

`python3 tools/report.py` reads straight from `core.store` and shows:

- **Cleared rate**: the share of every invoice seen whose 3-way match came
  back clean or inside the de-minimis floor - the number that lets you check
  "every 3-way mismatch caught" against what actually happened on your
  invoices, not the demo's.
- **Held value and variance count**: euros waiting on a query, and how many
  of those are a genuine price variance versus a missing PO or receipt - a
  rising variance count with a stable held count is a vendor pricing
  problem, not a process one.
- **Average requisition to PO**: from a requisition first landing to its
  purchase order being created - the practical measure of how much of the
  chase is now automatic.
- **Spend**: the four drafting prompts (vendor query, vendor confirmation,
  approval chase, letter of award) are the only model calls in this repo -
  see `docs/safety.md`, "Subscription or API".

## Honest caveats

- **"-75% invoice-to-payment cycle" is the source material's own estimate,
  not a guarantee for your property.** It assumes most of your invoices
  quote a purchase order and your goods-receipt discipline is reasonably
  consistent - a property that receives a lot of no-PO invoices from
  one-off vendors will see more holds and a smaller share of the cycle
  compressed. `python3 tools/report.py` tells you your own number.
- **This agent never releases money, and that never changes.** The strongest
  line in the roster: payment authorisation stays with your signatories,
  dual approval at the bank remains human. A payment-batch line is a record
  for your own banking process, not a payment - see `docs/how-it-works.md`,
  design decisions 5-7, and `docs/safety.md`.
- **Purchase requisitions, budget checks and approval routing are real, but
  simpler than a mature ERP's.** One delegation-of-authority matrix, amount
  banded; one budget line per category with no fiscal-year rollover; roles
  may sign in any order. `docs/how-it-works.md` design decisions 1-3 record
  exactly where this is simplified and why.
- **Matching is amount-only, like the source spec's demo.** There is no
  quantity dimension - a short delivery or a substituted item reads as a
  price variance or does not show up at all, not as its own reason code.
  See `specs/procure-to-pay-ai.md` section 11 if you have access to the
  factory this repo was built from.
- **No duplicate-invoice check.** Two invoices for the same PO, same amount,
  arriving days apart, are not flagged against each other - only the PO
  match runs.
