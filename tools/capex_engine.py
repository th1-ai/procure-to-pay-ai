"""tools/capex_engine.py - Tender & CAPEX Approval AI ("The Chancellor").

Pure functions over plain dicts, no I/O, unit-tested directly in
tests/test_p2p_capex.py. Ported from specs/tender-capex-approval-ai.md
sections 3, 5 and 6 - the source demo's ``capexRecommend()``.

"Never picks the winner" is the roster's own promise, and the scorer below
does rank and does name a top quote - see docs/how-it-works.md design
decision 9: this repo labels it "for committee consideration", never
"Recommended", and nothing releases until every approval role has signed.
"""

from __future__ import annotations

import re

from money import fmt, fmt_pct

_WARRANTY_RE = re.compile(r"(\d+)\s*-?\s*year", re.I)
_SCOPE_WORD_RE = re.compile(r"\bscope\b", re.I)
_SCOPE_GAP_RE = re.compile(r"\b(no|without|excl|missing|omits?)", re.I)
_REVENUE_PROTECTION_RE = re.compile(
    r"phased|stays?\s+(half-)?open|half-open|out of season|overnight|off-peak", re.I)


def score_quote(quote: dict, budget_eur: float) -> dict:
    """One quote -> its derived features, verdict and score."""
    amount = float(quote.get("amount", 0))
    weeks = float(quote.get("weeks", 0))
    note = str(quote.get("note", ""))
    vs_budget_pct = round(((budget_eur - amount) / budget_eur) * 100, 1) if budget_eur else 0.0
    m = _WARRANTY_RE.search(note)
    warranty_years = int(m.group(1)) if m else 0
    scope_gap = bool(_SCOPE_WORD_RE.search(note) and _SCOPE_GAP_RE.search(note))
    revenue_protection = bool(_REVENUE_PROTECTION_RE.search(note))

    if scope_gap:
        verdict = "Not comparable - scope gap"
        score = -999.0
    else:
        verdict = (f"{fmt(abs(amount - budget_eur))} inside budget" if amount <= budget_eur
                  else f"{fmt(amount - budget_eur)} over budget")
        score = (vs_budget_pct * 1.5 + warranty_years * 3 - weeks * 3
                + (12 if revenue_protection else 0))

    return {**quote, "amount": amount, "weeks": weeks, "note": note,
            "vs_budget_pct": vs_budget_pct, "warranty_years": warranty_years,
            "scope_gap": scope_gap, "revenue_protection": revenue_protection,
            "verdict": verdict, "score": score}


def rank_quotes(quotes: list[dict], budget_eur: float) -> list[dict]:
    scored = [score_quote(q, budget_eur) for q in quotes]
    return sorted(scored, key=lambda q: q["score"], reverse=True)


def three_quotes_gate(quotes: list[dict], budget_eur: float, threshold_eur: float,
                      three_quotes_on: bool) -> dict | None:
    """Returns a ``blocked`` result dict, or ``None`` when the gate does not fire."""
    if not three_quotes_on or len(quotes) >= 3:
        return None
    side = "above" if budget_eur >= threshold_eur else "below"
    return {
        "status": "blocked", "rule_named": "Three quotes above the CAPEX threshold",
        "chosen_vendor": None,
        "reason": (
            f"Only {len(quotes)} comparable quote(s) on file. Under the \"Three quotes above "
            f"{fmt(threshold_eur)}\" rule the AI does not put a short-listed recommendation to "
            f"the approval chain - at {fmt(budget_eur)} this project sits {side} the "
            f"{fmt(threshold_eur)} mandatory line, so it can only proceed as a documented "
            f"exception that Finance has to request. A third quote has been requested from the "
            f"approved-vendor list; turn the rule off to see the recommendation the AI would "
            f"otherwise make."),
    }


def draft_recommendation(ranked: list[dict], budget_eur: float) -> str:
    """Assembled prose, no LLM - spec section 3 step 6. ``ranked`` must be
    :func:`rank_quotes`'s output (already sorted, worst-first excluded)."""
    winner = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else None
    cheapest = min(ranked, key=lambda q: q["amount"])
    fastest = min(ranked, key=lambda q: q["weeks"])

    sentences = []
    direction = "inside" if winner["amount"] <= budget_eur else "over"
    pct = fmt_pct(winner["vs_budget_pct"])
    sentences.append(
        f"Recommend {winner['vendor']} for committee consideration at {fmt(winner['amount'])} - "
        f"{fmt(abs(budget_eur - winner['amount']))} ({pct}) {direction} the {fmt(budget_eur)} "
        f"budget, {winner['weeks']:.0f} weeks on site.")

    if cheapest["vendor"] != winner["vendor"]:
        diff = fmt(winner["amount"] - cheapest["amount"])
        if cheapest.get("scope_gap"):
            sentences.append(
                f"{cheapest['vendor']} is {diff} cheaper on paper but its quote carries a scope "
                f"gap ('{cheapest['note']}'), so it is not comparable - that work would come "
                f"back as a variation order and land above {winner['vendor']}.")
        else:
            sentences.append(
                f"{cheapest['vendor']} is {diff} cheaper but scores lower on warranty "
                f"({cheapest['warranty_years']} years against {winner['warranty_years']}) / the "
                f"scored criteria.")

    if fastest["vendor"] != winner["vendor"]:
        over = ""
        if fastest["amount"] > budget_eur:
            over = f", but is {fmt(fastest['amount'] - budget_eur)} over budget"
        sentences.append(
            f"{fastest['vendor']} is the fastest at {fastest['weeks']:.0f} weeks{over}.")

    if winner.get("revenue_protection"):
        sentences.append(
            f"{winner['vendor']} is the only quote that phases the work so the facility keeps "
            f"trading through it - {winner['weeks']:.0f} weeks of partial closure instead of a "
            f"full one.")

    if runner_up is not None and winner["warranty_years"] > runner_up["warranty_years"]:
        extra_years = winner["warranty_years"] - runner_up["warranty_years"]
        extra_cost = winner["amount"] - runner_up["amount"]
        if extra_cost > 0:
            sentences.append(
                f"{winner['vendor']} carries a {winner['warranty_years']}-year warranty against "
                f"{runner_up['warranty_years']} on {runner_up['vendor']} - {fmt(extra_cost)} "
                f"more for {extra_years} extra covered year(s), "
                f"{fmt(extra_cost / extra_years)}/year of extra cover.")
        else:
            sentences.append(
                f"{winner['vendor']} carries a {winner['warranty_years']}-year warranty against "
                f"{runner_up['warranty_years']} on {runner_up['vendor']} - {extra_years} extra "
                f"covered year(s) at no premium.")

    sentences.append(
        f"Recommendation to the approval chain: {winner['vendor']}, {fmt(winner['amount'])}, "
        f"{winner['weeks']:.0f} weeks.")
    return " ".join(sentences)


def capex_status(capex: dict) -> str:
    """``recommended | blocked | already_approved | no_quotes`` - engine-level,
    never stored, matching the source spec's ``CapexRecommendation.status``."""
    if capex.get("stage") == "approved":
        return "already_approved"
    if not capex.get("quotes"):
        return "no_quotes"
    return "recommended"
