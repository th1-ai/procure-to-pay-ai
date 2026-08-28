# Property facts - Hotel Aurora

<!--
Copy this to knowledge/property.md and replace everything with your own
details. This is a back-office agent - it never emails a guest - so keep this
file to what accounts payable actually needs, not amenities. Delete any
section that does not apply. Keep it factual and current.
-->

## The basics

- Legal name: Hotel Aurora, Lda.
- Trading name (what vendors and approvers see on correspondence): Hotel Aurora
- Billing address (put this on every PO and vendor email): 1 Example Street,
  1000-001 Lisbon, Portugal
- Accounts payable email: accounts.payable@example.com
- Accounts payable phone: +351 200 000 001
- VAT / tax ID: PT 000 000 000
- Currency: EUR

## Vendor terms

- Standard payment terms: net 30 from invoice date, bank transfer only - we
  do not pay by card or cheque.
- New vendor onboarding: bank details and a VAT/tax ID on file before the
  first invoice is scheduled for payment. Ask Finance if either is missing.
- Credit notes: a vendor query always asks for either a corrected invoice or
  a credit note for the difference - never a partial payment against a
  disputed amount.

## Payment calendar

- Payment batches are prepared every Monday for that week's cleared
  invoices; a signatory executes the transfer from the bank separately -
  this agent never releases money itself.
- Invoices must be cleared (matched, or a vendor query resolved) by Friday
  close to make the following Monday's batch.
- Urgent/overdue invoices: flag to the Bookkeeper directly rather than
  waiting for the weekly batch.

## Approval matrix

- The exact sign-off thresholds and delegation chain live in
  `config/agent.yaml: delegation_matrix` and are mirrored, for a person to
  read, in `knowledge/procurement-policy.md`. Keep the three in sync -
  the config file is what the agent runs on.

## What we do not do

- We never release a payment from this system - a human signatory pays from
  the bank, after reviewing the batch this agent prepares.
- We do not accept a goods-received confirmation from the vendor themselves,
  only from our own receiving process.
- We do not pay against a supplier statement - only a matched invoice, PO
  and (where required) goods receipt.
