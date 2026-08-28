"""tools/run.py - the full three-phase loop against the bundled fixtures,
provider=mock. No network, no credentials. Uses load_settings(demo=True) so
a hotel's own config/agent.yaml edits can never turn this test red - see
factory/workflows/build-repo.md section 5, "Tests never read the live config".
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (REPO_ROOT, REPO_ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from core.config import HotelConfig, ReviewConfig, Settings, load_settings  # noqa: E402
from core.review import WriteBlocked  # noqa: E402
from core.store import Store  # noqa: E402

import payments  # noqa: E402
import run  # noqa: E402
import store_ext  # noqa: E402


def _store(tmp_path, *, dry_run: bool = False):
    settings = load_settings(demo=True, dry_run=dry_run)
    store = Store(settings, path=tmp_path / "p2p.db")
    store_ext.ensure_schema(store)
    return settings, store


def test_receipts_apply_and_noop_on_unknown_or_already_received(tmp_path):
    settings, store = _store(tmp_path)
    run._seed_pos_if_mock(settings, store)  # noqa: SLF001
    stats = run.process_receipts(settings, store, limit=50)
    assert stats["seen"] == 3
    assert stats["applied"] == 1  # only PO-00105 was unreceived and known
    po = store_ext.get_po(store, "PO-00105")
    assert bool(po["received"]) is True
    store.close()


def test_requisitions_get_budget_checked_and_chained(tmp_path):
    settings, store = _store(tmp_path)
    run.process_requisitions(settings, store, limit=50)
    req1 = store_ext.get_requisition(store, "req-001")
    req2 = store_ext.get_requisition(store, "req-002")
    req3 = store_ext.get_requisition(store, "req-003")
    assert req1["stage"] == "awaiting_approval"
    assert [a["role"] for a in req1["approvals"]] == ["Department Head"]
    assert req2["stage"] == "awaiting_approval"
    assert [a["role"] for a in req2["approvals"]] == ["Department Head", "General Manager"]
    assert req3["stage"] == "budget_hold"  # 9,000 against a 6,000 Software budget
    store.close()


def test_invoices_match_every_fixture_correctly(tmp_path):
    settings, store = _store(tmp_path)
    run._seed_pos_if_mock(settings, store)  # noqa: SLF001
    run.process_invoices(settings, store, limit=50, provider="mock")
    expected = {
        "inv-001": ("ok", "pending_review"),          # clean match
        "inv-002": ("variance", "needs_human"),        # the set piece
        "inv-003": ("ok", "pending_review"),            # de-minimis
        "inv-004": ("no_receipt", "needs_human"),
        "inv-005": ("no_po", "needs_human"),            # unknown PO reference
        "inv-006": ("no_po", "pending_review"),         # approved vendor, under threshold
        "inv-007": ("no_po", "needs_human"),            # unapproved vendor
    }
    for ext_id, (match, status) in expected.items():
        item = store.get_by_external("invoices", ext_id)
        assert item is not None, ext_id
        assert item.draft["match"] == match, ext_id
        assert item.review_status == status, ext_id
    store.close()


def test_held_invoices_get_a_drafted_vendor_query(tmp_path):
    settings, store = _store(tmp_path)
    run._seed_pos_if_mock(settings, store)  # noqa: SLF001
    run.process_invoices(settings, store, limit=50, provider="mock")
    item = store.get_by_external("invoices", "inv-002")
    assert "vendor_query" in item.draft
    assert item.draft["vendor_query"]["subject"]
    assert item.draft["vendor_query"]["body"]


def test_rerun_is_idempotent_across_all_three_phases(tmp_path):
    settings, store = _store(tmp_path)
    run._seed_pos_if_mock(settings, store)  # noqa: SLF001
    run.process_receipts(settings, store, limit=50)
    run.process_requisitions(settings, store, limit=50)
    run.process_invoices(settings, store, limit=50, provider="mock")
    first_items = len(store.list_items())
    first_reqs = len(store_ext.list_requisitions(store))

    stats2 = run.process_receipts(settings, store, limit=50)
    reqs2 = run.process_requisitions(settings, store, limit=50)
    inv2 = run.process_invoices(settings, store, limit=50, provider="mock")

    assert stats2["applied"] == 0  # nothing new to apply
    assert reqs2["awaiting_approval"] == 0 and reqs2["budget_hold"] == 0
    assert inv2["held"] == 0 and inv2["cleared"] == 0
    assert len(store.list_items()) == first_items
    assert len(store_ext.list_requisitions(store)) == first_reqs
    store.close()


def test_dry_run_writes_nothing_at_all(tmp_path):
    settings, store = _store(tmp_path, dry_run=True)
    run.one_pass(settings, store, limit=50, provider="mock")
    assert store.list_items() == []
    assert store_ext.list_requisitions(store) == []
    assert store_ext.list_pos(store) == []  # the PO fixture was read, never seeded
    store.close()


def test_dry_run_still_records_a_runs_row_flagged_dry_run(tmp_path):
    """Regression for SIMULATION.md Finding 4 (MINOR): the docs used to claim
    `--dry-run` writes "not a runs row" - false, core.log.Run opens and
    closes one on every pass, dry or not. The fix is not to stop writing it
    (that is core's observability contract) but to flag it, so the row does
    not read as a real run - see CLAUDE.md, "--dry-run writes no business
    data"."""
    import json as _json
    settings, store = _store(tmp_path, dry_run=True)
    run.one_pass(settings, store, limit=50, provider="mock")
    rows = store.db.execute("SELECT stats_json FROM runs").fetchall()
    assert len(rows) == 1  # one runs row - observability, not business data
    stats = _json.loads(rows[0]["stats_json"])
    assert stats["dry_run"] is True
    # the business tables this row's existence must never be confused with:
    assert store.list_items() == []
    assert store_ext.list_requisitions(store) == []
    assert store_ext.list_pos(store) == []
    store.close()


def test_dry_run_twice_never_raises_and_stays_empty(tmp_path):
    settings, store = _store(tmp_path, dry_run=True)
    run.one_pass(settings, store, limit=50, provider="mock")
    run.one_pass(settings, store, limit=50, provider="mock")
    assert store.list_items() == []
    store.close()


def test_shadow_mode_blocks_the_payment_batch_write_and_keeps_the_approval(tmp_path):
    settings, store = _store(tmp_path)
    run._seed_pos_if_mock(settings, store)  # noqa: SLF001
    run.process_invoices(settings, store, limit=50, provider="mock")
    item = store.get_by_external("invoices", "inv-001")  # cleared, pending_review
    assert item.review_status == "pending_review"
    from core.review import approve
    approved = approve(store, item.id)
    assert approved.review_status == "approved"
    claimed = store.claim_for_send(limit=5)
    claimed_item = next(i for i in claimed if i.id == item.id)
    try:
        payments.schedule_payment(settings, claimed_item)
        raised = False
    except WriteBlocked:
        raised = True
    assert raised is True
    # tools/review.py's send path keeps the approval on a WriteBlocked - simulate that here.
    store.transition(item.id, "approved", "agent", {"blocked": "shadow"})
    counts = store.counts()
    assert counts.get("sent", 0) == 0
    assert counts.get("approved", 0) == 1
    store.close()


def test_report_only_counts_schedule_action_invoices_as_cleared(tmp_path):
    """Regression: approving a HELD invoice's vendor-query email must not
    make report.py count it as cleared for payment - only draft.action ==
    'schedule' items are a payment decision. See tools/report.py."""
    settings, store = _store(tmp_path)
    run._seed_pos_if_mock(settings, store)  # noqa: SLF001
    run.process_invoices(settings, store, limit=50, provider="mock")

    from core.review import approve
    held = store.get_by_external("invoices", "inv-002")  # variance hold
    assert held.draft["action"] == "hold"
    approve(store, held.id)  # a human approves the vendor QUERY, not a payment

    import report
    stats = report.gather(store, settings.hotel.currency)
    assert stats["held"] >= 1
    # the approved-but-still-a-hold invoice must not be counted as cleared
    cleared_vendors = [i.draft.get("vendor") for i in store.list_items(
        kind="invoice", status=["approved", "edited", "pending_review"], limit=50)
        if (i.draft or {}).get("action") == "schedule"]
    assert "Atlantic Seafood Co." not in cleared_vendors or held.draft["action"] != "schedule"
    assert stats["cleared"] == 3  # inv-001, inv-003, inv-006 - every action=="schedule" invoice
    store.close()


def test_payment_batch_write_blocked_even_if_hotel_config_drops_payment_from_the_gate_list(
    tmp_path,
):
    """Regression for the stranger-onboarding BLOCKER: a hotel editing
    config/hotel.yaml and removing "payment" from review.require_approval_for
    must not be able to make an unapproved payment-batch write go through in
    live mode. tools/payments.py:schedule_payment() guards with the
    hardcoded action name "payment", which core.review.ALWAYS_HUMAN_ACTIONS
    protects in every mode, no matter what review.require_approval_for says -
    see core/review.py."""
    settings = Settings(
        hotel=HotelConfig(currency="EUR"), mode="live", dry_run=False,
        review=ReviewConfig(require_approval_for=["send_email"]),  # "payment" removed
    )
    store = Store(settings, path=tmp_path / "gate.db")
    item = store.upsert_item("invoices", "inv-gate", kind="invoice")
    store.set_fields(item.id, draft={"vendor": "Test Supplies", "amount_eur": 100.0,
                                     "currency": "EUR", "match": "clean"})
    store.transition(item.id, "pending_review")
    item = store.get_item(item.id)
    assert item.review_status == "pending_review"  # never approved

    try:
        payments.schedule_payment(settings, item)
        raised = False
    except WriteBlocked as exc:
        raised = True
        assert "never moves unattended" in str(exc)
    assert raised is True, "payment must stay blocked even with 'payment' removed from config"
    store.close()
