"""tools/capex_engine.py - Tender & CAPEX Approval AI's scoring, gate and
recommendation prose. Pure functions, no I/O - see docs/sub-agents.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (REPO_ROOT, REPO_ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import capex_engine as ce  # noqa: E402

POOL_QUOTES = [
    {"vendor": "Azure Pool Works", "amount": 61400, "weeks": 9,
     "note": "Phased so the pool stays half-open, 5-year warranty"},
    {"vendor": "Coastal Build Co.", "amount": 54900, "weeks": 7,
     "note": "Excludes scope for drainage renewal"},
    {"vendor": "Granite Construct", "amount": 71200, "weeks": 6,
     "note": "3-year warranty"},
]
FRIDGE_QUOTES = [
    {"vendor": "ColdChain Supply", "amount": 29800, "weeks": 2, "note": "2-year warranty"},
    {"vendor": "Arctic Commercial", "amount": 33500, "weeks": 3, "note": "3-year warranty"},
]


def test_scope_gap_disqualifies_not_merely_penalises():
    scored = ce.score_quote(POOL_QUOTES[1], 68000)
    assert scored["scope_gap"] is True
    assert scored["score"] == -999.0


def test_warranty_and_revenue_protection_parsed_from_note():
    scored = ce.score_quote(POOL_QUOTES[0], 68000)
    assert scored["warranty_years"] == 5
    assert scored["revenue_protection"] is True
    assert scored["scope_gap"] is False


def test_score_matches_the_worked_example_within_rounding():
    ranked = ce.rank_quotes(POOL_QUOTES, 68000)
    by_vendor = {q["vendor"]: q["score"] for q in ranked}
    assert abs(by_vendor["Azure Pool Works"] - 14.6) < 0.2
    assert by_vendor["Coastal Build Co."] == -999.0
    assert abs(by_vendor["Granite Construct"] - (-16.1)) < 0.2


def test_ranking_puts_the_scope_gap_quote_last():
    ranked = ce.rank_quotes(POOL_QUOTES, 68000)
    assert ranked[0]["vendor"] == "Azure Pool Works"
    assert ranked[-1]["vendor"] == "Coastal Build Co."


def test_three_quotes_gate_blocks_below_threshold_count():
    blocked = ce.three_quotes_gate(FRIDGE_QUOTES, 31500, 25000, three_quotes_on=True)
    assert blocked is not None
    assert blocked["status"] == "blocked"
    assert blocked["chosen_vendor"] is None


def test_three_quotes_gate_off_lets_it_through():
    blocked = ce.three_quotes_gate(FRIDGE_QUOTES, 31500, 25000, three_quotes_on=False)
    assert blocked is None


def test_three_quotes_gate_passes_with_enough_quotes():
    blocked = ce.three_quotes_gate(POOL_QUOTES, 68000, 25000, three_quotes_on=True)
    assert blocked is None


def test_recommendation_names_the_winner_and_the_scope_gap_runner():
    ranked = ce.rank_quotes(POOL_QUOTES, 68000)
    text = ce.draft_recommendation(ranked, 68000)
    assert "Azure Pool Works" in text
    assert "for committee consideration" in text  # never "Recommended" - see how-it-works.md
    assert "scope gap" in text
    assert "Recommendation to the approval chain" in text


def test_capex_status_no_quotes_and_already_approved():
    assert ce.capex_status({"stage": "draft", "quotes": []}) == "no_quotes"
    assert ce.capex_status({"stage": "approved", "quotes": POOL_QUOTES}) == "already_approved"
    assert ce.capex_status({"stage": "draft", "quotes": POOL_QUOTES}) == "recommended"
