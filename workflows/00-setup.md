# Workflow: first-run setup

Objective: get Procure-to-Pay AI from a fresh clone to a working demo, then to
real config, in one sitting.

## Steps

1. **Install and check.**
   ```bash
   make setup
   make doctor
   ```
   `make setup` creates the virtualenv, installs `requirements.txt`, and
   copies `.env.example` -> `.env` and every `config/*.example.yaml` ->
   `config/*.yaml` (only if those files do not exist yet - it never overwrites
   your own copies). `make doctor` will show a `FAIL` on "hotel identity"
   right after setup - that is expected, it means the property name is still
   the shipped placeholder ("Hotel Aurora"). It will also show `pms adapter`
   and `messaging adapter` lines: this agent does not use either, ignore
   them - only `email` and `sheets` matter here, see `docs/integrations.md`.

2. **Run the demo.** No credentials needed.
   ```bash
   make demo
   ```
   Expect to see requisitions routed for approval, purchase orders matched
   against invoices, and a line like
   `DEMO OK - 10 items processed, 4 drafted, 0 sent (shadow)`. If you do not
   see that, stop and read `workflows/99-troubleshooting.md` before going
   further.

3. **Fill in the property.** Edit `config/hotel.yaml` (name, address, contact,
   currency, languages). Then:
   ```bash
   cp knowledge/property.example.md            knowledge/property.md
   cp knowledge/procurement-policy.example.md  knowledge/procurement-policy.md
   ```
   Replace the Hotel Aurora content with the real property's facts -
   `property.md` is read into every drafted email's context, so keep it to
   what accounts payable actually needs (billing address, vendor terms, the
   payment calendar), not guest-facing amenities. Also create
   `knowledge/signature.md` (plain text, no example ships for this one) with
   the sign-off every drafted email carries and the AI-disclosure line -
   `docs/safety.md` has suggested wording. `knowledge/faq.example.md` is
   optional - copy it to `knowledge/faq.md` only if you want an internal
   accounts-payable reference doc; no prompt reads it (see
   `knowledge/README.md`), so skipping it changes nothing about how the
   agent behaves.

4. **Fill in this agent's own knobs.** Edit `config/agent.yaml`:
   - `budgets` - one line per budget your P&L actually tracks, with its
     annual/period limit. A requisition against a line not listed here is
     always held for Finance.
   - `delegation_matrix` - who signs at what value, in what order. The
     shipped example is a plausible starting point (Department Head under
     EUR 1,000; + General Manager to EUR 10,000; + Owner above that) - change
     the bands and the role names to match your own sign-off chain.
   - `approved_vendors` and `vendor_emails` - which vendors can clear a small
     invoice without a PO, and where a query or confirmation email goes once
     approved. Leave an address blank and a send blocks with a clear message
     rather than guessing one.
   - `approver_emails` - one address per role in your delegation matrix, for
     the approval-chase reminders.

5. **Pick how the agent thinks.** `config/hotel.yaml`'s `llm.provider` starts
   as `interactive` - it asks you, in this Claude Code session, instead of
   calling a model. That costs nothing extra and is the best way to see how
   the agent reasons. `docs/how-it-works.md` and `docs/safety.md` explain the
   other three providers (`mock`, `claude-code`, `anthropic`) and when to move
   to one of them.

6. **Point it at your real invoices, POs and requisitions (optional for
   now).** `po_reader.adapter`, `invoice_reader.adapter` and
   `receipt_reader.adapter` in `config/agent.yaml` start as `mock`, which only
   ever sees the bundled fixtures. `docs/integrations.md` covers the `csv`
   option, which works with any ERP, accounting system or spreadsheet export.

7. **Re-check.**
   ```bash
   make doctor
   ```
   Once the property name is real and `knowledge/property.md` exists, the
   "hotel identity" and "knowledge" lines turn green. Move on to
   `workflows/10-procure-to-pay.md` to run the loop for real, and
   `workflows/20-tender-capex.md` if you also want the Tender & CAPEX
   sub-agent.
