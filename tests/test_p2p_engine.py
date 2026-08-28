"""tools/requisition_engine.py + tools/match_engine.py - pure decisioning.
No store, no adapter, no model - see docs/how-it-works.md "Deterministic
decisioning, LLM for language".
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (REPO_ROOT, REPO_ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import match_engine as me  # noqa: E402
import requisition_engine as req  # noqa: E402

MATRIX = [
    {"max_eur": 1000, "roles": ["Department Head"]},
    {"max_eur": 10000, "roles": ["Department Head", "General Manager"]},
    {"max_eur": None, "roles": ["Department Head", "General Manager", "Owner"]},
]
BUDGETS = {"F&B": {"limit_eur": 20000}, "Software": {"limit_eur": 6000}}


# --------------------------------------------------------------------------
# budget check + delegation matrix
# --------------------------------------------------------------------------
def test_budget_check_within_remaining_passes():
    result = req.budget_check(450, "F&B", BUDGETS, committed_eur=0)
    assert result["ok"] is True


def test_budget_check_over_remaining_holds_with_a_named_reason():
    result = req.budget_check(9000, "Software", BUDGETS, committed_eur=0)
    assert result["ok"] is False
    assert "Software" in result["reason"] and "6,000.00" in result["reason"]


def test_budget_check_unknown_line_holds_for_finance():
    result = req.budget_check(100, "Marketing", BUDGETS, committed_eur=0)
    assert result["ok"] is False
    assert "No budget line" in result["reason"]


def test_budget_check_counts_already_committed_amount():
    result = req.budget_check(1000, "F&B", BUDGETS, committed_eur=19500)
    assert result["ok"] is False  # only 500 remaining


def test_approval_chain_picks_the_first_band_that_fits():
    assert req.approval_chain_for(450, MATRIX) == ["Department Head"]
    assert req.approval_chain_for(4200, MATRIX) == ["Department Head", "General Manager"]
    assert req.approval_chain_for(50000, MATRIX) == ["Department Head", "General Manager", "Owner"]


def test_approve_role_any_order_and_completion():
    approvals = req.approvals_json(["Department Head", "General Manager"])
    assert req.approvals_complete(approvals) is False
    approvals, found = req.approve_role(approvals, "General Manager", approved_at="t1")
    assert found is True
    assert req.next_pending_roles(approvals) == ["Department Head"]
    approvals, found = req.approve_role(approvals, "Department Head", approved_at="t2")
    assert req.approvals_complete(approvals) is True


def test_approve_role_unknown_role_not_found():
    approvals = req.approvals_json(["Department Head"])
    _, found = req.approve_role(approvals, "Owner", approved_at="t1")
    assert found is False


# --------------------------------------------------------------------------
# the 3-way match ladder (spec section 3 / section 8 samples)
# --------------------------------------------------------------------------
PO_CLEAN = {"vendor": "Harbourview Linens", "amount_eur": 1240.00,
           "description": "Linen replacement - Q3", "received": True}
PO_VARIANCE = {"vendor": "Atlantic Seafood Co.", "amount_eur": 1572.50,
              "description": "Weekly seafood delivery", "received": True}
PO_DEMINIMIS = {"vendor": "CleanNest Supplies", "amount_eur": 58.00,
                "description": "Office cleaning supplies", "received": True}
PO_UNRECEIVED = {"vendor": "GreenScape Grounds", "amount_eur": 340.00,
                 "description": "Grounds maintenance", "received": False}


def test_clean_match_schedules():
    out = me.three_way_match(1240.00, PO_CLEAN, "PO-00101", "Harbourview Linens",
                             tolerance_pct=2, tolerance_eur=100, three_way_on=True)
    assert out["match"] == "ok" and out["action"] == "schedule"
    assert "Clean 3-way match" in out["reason"]


def test_variance_breaching_both_tolerances_holds():
    out = me.three_way_match(1719.00, PO_VARIANCE, "PO-00102", "Atlantic Seafood Co.",
                             tolerance_pct=2, tolerance_eur=100, three_way_on=True)
    assert out["match"] == "variance" and out["action"] == "hold"
    assert out["variance_pct"] == 9.3
    assert round(out["variance_eur"], 2) == 146.50


def test_de_minimis_breaches_only_percent_and_clears():
    out = me.three_way_match(61.20, PO_DEMINIMIS, "PO-00103", "CleanNest Supplies",
                             tolerance_pct=2, tolerance_eur=100, three_way_on=True)
    assert out["match"] == "ok" and out["action"] == "schedule"
    assert "de-minimis" in out["reason"]


def test_no_po_found_holds():
    out = me.three_way_match(500.00, None, "PO-99999", "Harbourview Linens",
                             tolerance_pct=2, tolerance_eur=100, three_way_on=True)
    assert out["match"] == "no_po" and out["action"] == "hold"


def test_not_received_holds_unconditionally():
    out = me.three_way_match(340.00, PO_UNRECEIVED, "PO-00104", "GreenScape Grounds",
                             tolerance_pct=2, tolerance_eur=100, three_way_on=True)
    assert out["match"] == "no_receipt" and out["action"] == "hold"


def test_vendor_mismatch_holds_regardless_of_amount():
    out = me.three_way_match(1240.00, PO_CLEAN, "PO-00101", "Someone Else Ltd",
                             tolerance_pct=2, tolerance_eur=100, three_way_on=True)
    assert out["match"] == "vendor_mismatch" and out["action"] == "hold"


def test_rule_off_schedules_on_the_vendors_word_alone():
    out = me.three_way_match(1719.00, PO_VARIANCE, "PO-00102", "Atlantic Seafood Co.",
                             tolerance_pct=2, tolerance_eur=100, three_way_on=False)
    assert out["match"] == "ok" and out["action"] == "schedule"
    assert "switched off" in out["reason"]


def test_no_po_branch_approved_vendor_under_threshold_schedules():
    out = me.no_po_branch("CleanNest Supplies", 240.00, 1000, ["cleannest supplies"])
    assert out["match"] == "no_po" and out["action"] == "schedule"


def test_no_po_branch_unapproved_vendor_holds():
    out = me.no_po_branch("Riverside Print & Copy", 180.00, 1000, ["cleannest supplies"])
    assert out["action"] == "hold"


def test_no_po_branch_at_or_above_threshold_always_holds():
    out = me.no_po_branch("CleanNest Supplies", 1500.00, 1000, ["cleannest supplies"])
    assert out["action"] == "hold"


def test_batch_summary_partitions_and_counts_variances():
    cleared = [{"amount_eur": 100}, {"amount_eur": 50}]
    held = [{"amount_eur": 200, "match": "variance"}, {"amount_eur": 10, "match": "no_receipt"}]
    summary = me.batch_summary(cleared, held)
    assert summary["cleared_count"] == 2 and summary["held_count"] == 2
    assert summary["variance_count"] == 1
    assert summary["cleared_total"] == 150 and summary["held_total"] == 210
