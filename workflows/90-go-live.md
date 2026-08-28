# Workflow: shadow to live

Objective: decide, together with the hotel, whether Procure-to-Pay AI is
ready to send approved vendor queries, vendor confirmations, chases and
letters of award on its own instead of only drafting them - and make the
change safely if so.

**Payment scheduling is a separate promise, and it never changes.** A
payment-batch line needs a human approval in every mode, shadow or live -
that is the roster's own "cant": payment authorisation stays with your
signatories. Nothing in this checklist, or in `mode: live`, removes that.

This is the hotel's decision, never the agent's. Do not suggest it until the
checklist below is genuinely true, and when you do raise it, say plainly what
changes.

## Checklist

- [ ] `make doctor` is clean (no `FAIL` lines). `warn` on `mode` is expected
      until you flip it.
- [ ] `config/hotel.yaml` has the real property name and details;
      `config/agent.yaml` has real `budgets`, a real `delegation_matrix`, and
      real `vendor_emails` / `approver_emails` (not blank placeholders).
- [ ] At least a few real requisitions and invoices have gone through the
      review queue, not just the demo fixtures - `tools/report.py` shows a
      cleared/held split you recognise as sane.
- [ ] `python3 tools/review.py stale` has been run once, to clear whatever
      built up during shadow testing (see below).
- [ ] `tolerance_pct` / `tolerance_eur` / `no_po_threshold_eur` in
      `config/agent.yaml: matching` reflect this property's own controls, not
      the shipped defaults, if those defaults do not match.
- [ ] A real mailbox is connected (`systems.email.adapter: imap` or `gmail`)
      and `make doctor` shows it healthy.

## Making the change

1. Edit `config/hotel.yaml`:
   ```yaml
   mode: live
   ```
2. `review.require_approval_for` still lists `payment` and `send_email` by
   default - it should. **Never remove `payment` from this list** - though
   even if you did, a payment-batch write would still be blocked without an
   approval: `payment` is **enforced in code, not config**
   (`core.review.ALWAYS_HUMAN_ACTIONS` in `core/review.py`), so no
   `config/hotel.yaml` edit can lift the requirement for a human approval on
   a payment-batch line, in any mode. Going live means an approved vendor
   query, confirmation, chase or award letter can now really send, and an
   approved payment-batch line can now really be written - not that either
   happens without that approval first.
3. Clear the shadow-era queue so nothing old goes out by surprise:
   ```bash
   python3 tools/review.py stale
   ```
4. Run `make doctor` again to confirm.
5. Run one real pass and manually watch a send go through:
   ```bash
   make run ARGS="--limit 1"
   python3 tools/review.py list
   python3 tools/review.py approve <id>
   python3 tools/review.py send
   ```
6. Tell the hotel exactly what just changed: an approved vendor email now
   really sends, and an approved payment-batch line now really gets written
   to `data/exports/payment-batches/` - a person still has to execute that
   batch in the bank. Everything not yet approved still waits for a person,
   exactly as before.

## Going back to shadow

```yaml
mode: shadow
```
in `config/hotel.yaml`, or `AGENT_MODE=shadow` in `.env` for one run. Either
stops every outbound action and every payment-batch write on the next pass,
mid-schedule, with no other change required.
