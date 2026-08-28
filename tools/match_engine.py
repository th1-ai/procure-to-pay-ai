"""tools/match_engine.py - the 3-way match ladder and the payment batch.

Pure functions over plain dicts, no I/O, unit-tested directly in
tests/test_p2p_engine.py. Ported from the source demo's ``finance-engine.ts``
match branch (specs/procure-to-pay-ai.md sections 3 and 8) and standalone: no
import from finance-filing-ai, no shared database - see
docs/how-it-works.md, "Where this agent starts and stops".

Order of tests matters and mirrors the spec exactly:

    1. rule off (three-way disabled)   -> ok, on the vendor's word alone
    2. no PO found                     -> hold, no_po
    3. vendor on the PO does not match -> hold, vendor_mismatch
    4. PO found but not received       -> hold, no_receipt
    5. amount compared, BOTH tolerances must breach to hold -> variance
    6. no po_ref at all                -> the no-PO branch
"""

from __future__ import annotations

from money import fmt, fmt_pct, fmt_signed


def _normalize(text: str | None) -> str:
    return "".join(ch for ch in str(text or "").strip().lower() if ch.isalnum())


def three_way_match(amount_eur: float, po: dict | None, po_ref: str, vendor: str, *,
                    tolerance_pct: float, tolerance_eur: float, three_way_on: bool,
                    currency: str = "EUR") -> dict:
    if not three_way_on:
        return {"match": "ok", "action": "schedule",
                "reason": f"3-way match is switched off - {po_ref} was never opened. "
                          f"{fmt(amount_eur, currency)} queued on the vendor's word alone.",
                "notes": ["With the 3-way match rule off, a price variance against the PO "
                          "would be paid without anyone seeing it."]}

    if po is None:
        return {"match": "no_po", "action": "hold", "notes": [],
                "reason": f"Invoice quotes {po_ref} but no such purchase order exists in the "
                          f"ledger. Held for procurement."}

    po_vendor = str(po.get("vendor") or "")
    if po_vendor and vendor and _normalize(po_vendor) != _normalize(vendor):
        return {"match": "vendor_mismatch", "action": "hold", "notes": [],
                "reason": f"Wrong PO: {po_ref} belongs to {po_vendor}, but the invoice is from "
                          f"{vendor}. Held as a vendor mismatch, whatever the amount says - not "
                          f"a price problem."}

    if not po.get("received"):
        return {"match": "no_receipt", "action": "hold", "notes": [],
                "reason": f"{po_ref} has no goods-received record. Nothing is paid before "
                          f"receipt is confirmed."}

    po_amount = float(po.get("amount_eur") or 0)
    description = po.get("description", "")
    variance_eur = round(amount_eur - po_amount, 2)
    variance_pct = round((variance_eur / po_amount) * 100, 1) if po_amount else 0.0
    breach_pct = abs(variance_pct) > tolerance_pct
    breach_eur = abs(variance_eur) > tolerance_eur

    if breach_pct and breach_eur:
        return {"match": "variance", "action": "hold", "variance_eur": variance_eur,
                "variance_pct": variance_pct, "expected_eur": po_amount, "notes": [],
                "reason": f"Price variance: invoiced {fmt(amount_eur, currency)} against "
                          f"{po_ref} at {fmt(po_amount, currency)} - {fmt_pct(variance_pct)} "
                          f"({fmt_signed(variance_eur, currency)}) on '{description}'. Above "
                          f"the {tolerance_pct}% / {fmt(tolerance_eur, currency)} tolerance - "
                          f"2% and {fmt(tolerance_eur, currency)}, whichever bites second - so "
                          f"the payment stops here."}

    if breach_pct or breach_eur:
        only = "percentage" if breach_pct else "absolute"
        return {"match": "ok", "action": "schedule", "variance_eur": variance_eur,
                "variance_pct": variance_pct, "expected_eur": po_amount,
                "notes": [f"Only the {only} tolerance was breached, not both - logged for "
                          f"the vendor review, not held."],
                "reason": f"Matched to {po_ref}: {fmt(amount_eur, currency)} vs "
                          f"{fmt(po_amount, currency)}, {fmt_pct(variance_pct)} but only "
                          f"{fmt(abs(variance_eur), currency)} in absolute terms - inside the "
                          f"{fmt(tolerance_eur, currency)} de-minimis floor. Logged for the "
                          f"vendor review, not held."}

    return {"match": "ok", "action": "schedule", "variance_eur": variance_eur,
            "variance_pct": variance_pct, "expected_eur": po_amount, "notes": [],
            "reason": f"Clean 3-way match: invoice, {po_ref} ({fmt(po_amount, currency)}) and "
                      f"goods-received all agree on '{description}'."}


def no_po_branch(vendor: str, amount_eur: float, threshold_eur: float,
                 approved_vendors: list[str], *, currency: str = "EUR") -> dict:
    """Spec step 3 / finance-filing-ai's identical no-PO rule: a small invoice
    from an approved vendor clears without a PO; anything else, or anything
    at/above the threshold, waits for a retrospective PO."""
    approved = _normalize(vendor) in {_normalize(v) for v in approved_vendors}
    if amount_eur < threshold_eur and approved:
        return {"match": "no_po", "action": "schedule", "notes": [],
                "reason": f"No purchase order, and none required: {fmt(amount_eur, currency)} "
                          f"is under the {fmt(threshold_eur, currency)} no-PO threshold and "
                          f"{vendor} is on the approved-vendor list."}
    if amount_eur < threshold_eur:
        return {"match": "no_po", "action": "hold", "notes": [],
                "reason": f"{fmt(amount_eur, currency)} is under the "
                          f"{fmt(threshold_eur, currency)} no-PO threshold, but {vendor} is "
                          f"not on the approved-vendor list. Held for a retrospective PO."}
    return {"match": "no_po", "action": "hold", "notes": [],
            "reason": f"{fmt(amount_eur, currency)} is at or above the "
                      f"{fmt(threshold_eur, currency)} no-PO threshold. Held for a "
                      f"retrospective PO."}


def decide(invoice: dict, po: dict | None, *, tolerance_pct: float, tolerance_eur: float,
          no_po_threshold_eur: float, three_way_on: bool, approved_vendors: list[str],
          currency: str = "EUR") -> dict:
    """The one entry point tools/run.py calls: routes to the PO branch or the
    no-PO branch depending on whether the invoice quotes a ``po_ref``."""
    amount_eur = float(invoice.get("amount_eur") or 0)
    po_ref = invoice.get("po_ref")
    if po_ref:
        return three_way_match(amount_eur, po, po_ref, invoice.get("vendor", ""),
                               tolerance_pct=tolerance_pct, tolerance_eur=tolerance_eur,
                               three_way_on=three_way_on, currency=currency)
    return no_po_branch(invoice.get("vendor", ""), amount_eur, no_po_threshold_eur,
                        approved_vendors, currency=currency)


# --------------------------------------------------------------------------
# payment batch (spec step 5: partition, total, count variances)
# --------------------------------------------------------------------------
def batch_summary(cleared: list[dict], held: list[dict], *, currency: str = "EUR") -> dict:
    """``cleared`` / ``held`` are invoice dicts carrying ``amount_eur`` and,
    once matched, ``match``. Mirrors the spec's batch-summary headline."""
    cleared_total = round(sum(float(i.get("amount_eur") or 0) for i in cleared), 2)
    held_total = round(sum(float(i.get("amount_eur") or 0) for i in held), 2)
    variances = sum(1 for i in held if i.get("match") in ("variance", "no_po_utility_flag"))
    total = len(cleared) + len(held)
    auto_filed_pct = round(100 * len(cleared) / total, 1) if total else 0.0
    headline = (f"{len(cleared)} invoice(s) cleared for payment "
                f"({fmt(cleared_total, currency)}); {len(held)} on hold "
                f"({fmt(held_total, currency)}), {variances} of them a price variance.")
    return {"cleared_count": len(cleared), "cleared_total": cleared_total,
            "held_count": len(held), "held_total": held_total, "variance_count": variances,
            "auto_cleared_pct": auto_filed_pct, "headline": headline}
