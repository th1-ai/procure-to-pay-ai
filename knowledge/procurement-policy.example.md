# Procurement policy - Hotel Aurora (fixture data)

This is a plain-language mirror of `config/agent.yaml`'s `budgets`,
`delegation_matrix`, `matching` and `approved_vendors` blocks - written for a
person to read, not for the agent to parse. Keep the two in sync; the config
file is what the agent actually runs on.

## Budget lines

| Line | Annual limit |
|---|---|
| F&B | EUR 20,000 |
| Housekeeping | EUR 8,000 |
| Property | EUR 50,000 |
| Software | EUR 6,000 |
| Sundry | EUR 4,000 |

A requisition against a line not on this list is always held for Finance to
add one.

## Who signs, at what value

| Up to | Sign-off |
|---|---|
| EUR 1,000 | Department Head |
| EUR 10,000 | Department Head, then General Manager |
| Above that | Department Head, then General Manager, then Owner |

Any role may sign in whatever order it reaches them; the purchase order is
only created once every role in the chain has signed.

## 3-way match tolerances

A price variance stops a payment only when it breaches **both**: more than
2% of the purchase order's value, **and** more than EUR 100 in absolute
terms. A variance breaching only one of the two is logged for review, not
held.

## No-PO threshold

An invoice under EUR 1,000 with no purchase order clears automatically if
the vendor is on the approved list below; otherwise, or at/above the
threshold, it is held for a retrospective PO.

## Approved vendors (no-PO invoices under the threshold)

- CleanNest Supplies
- GreenScape Grounds

## What we do not do

- We never release a payment from this system. A signatory pays from the
  bank, separately, after reviewing the batch this agent prepares.
- We do not accept a goods-received confirmation from the vendor
  themselves - only from our own receiving process.
