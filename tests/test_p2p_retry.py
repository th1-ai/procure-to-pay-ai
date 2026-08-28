"""Resumable-stage regression test - factory/workflows/build-repo.md section
1b, "the trap found in front-desk-ai": with the `interactive` provider, the
vendor-query draft can pend AFTER the (deterministic, cheap) match decision
is already made. A retry must resume at the draft stage, not silently skip
the item and not recompute the match from scratch.
"""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (REPO_ROOT, REPO_ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import core.llm as core_llm  # noqa: E402
from core.config import HotelConfig, LLMConfig, Settings, load_settings  # noqa: E402
from core.llm import LLMPendingInteractive  # noqa: E402
from core.store import Store  # noqa: E402

import capex  # noqa: E402
import requisition  # noqa: E402
import run  # noqa: E402
import store_ext  # noqa: E402

VARIANCE_INVOICE = {"id": "inv-002", "vendor": "Atlantic Seafood Co.", "invoice_no": "ASC-9102",
                    "amount_eur": 1719.00, "currency": "EUR", "po_ref": "PO-00102",
                    "description": "Weekly seafood delivery"}


def test_retry_after_vendor_query_pends_resumes_without_recomputing_match(tmp_path, monkeypatch):
    settings = load_settings(demo=True)
    store = Store(settings, path=tmp_path / "retry.db")
    store_ext.ensure_schema(store)
    run._seed_pos_if_mock(settings, store)  # noqa: SLF001

    import drafting
    calls = {"match": 0, "draft": 0}
    real_decide = run.match_engine.decide

    def counting_decide(*a, **kw):
        calls["match"] += 1
        return real_decide(*a, **kw)

    real_draft = drafting.draft_vendor_query

    def pending_then_real(*a, **kw):
        calls["draft"] += 1
        if calls["draft"] == 1:
            raise LLMPendingInteractive("pending-1", tmp_path / "p.prompt.md", None,
                                        tmp_path / "p.answer.json")
        return real_draft(*a, **kw)

    monkeypatch.setattr(run.match_engine, "decide", counting_decide)
    monkeypatch.setattr(drafting, "draft_vendor_query", pending_then_real)

    get_po = run._build_po_lookup(settings, store)  # noqa: SLF001

    try:
        run.process_invoice(settings, store, VARIANCE_INVOICE, get_po=get_po, provider="mock")
        pended = False
    except LLMPendingInteractive:
        pended = True
    assert pended is True

    parked = store.get_by_external("invoices", "inv-002")
    assert parked.draft is None  # not finished yet
    assert (parked.payload or {}).get("_match_cache") is not None  # but the match was cached
    assert parked.review_status == "new"  # not moved out of new until fully settled

    item, did_work = run.process_invoice(settings, store, VARIANCE_INVOICE, get_po=get_po,
                                         provider="mock")
    assert did_work is True
    assert item.draft is not None
    assert item.review_status == "needs_human"
    assert calls == {"match": 1, "draft": 2}  # match computed once, draft attempted twice
    store.close()


def test_capex_award_letter_resumes_after_interactive_pend_without_losing_the_approval(
    tmp_path, monkeypatch
):
    """Regression for SIMULATION.md Finding 2 (BLOCKER): tools/capex.py's
    cmd_approve() used to write the final role's approval to
    store_ext.set_capex_approvals() BEFORE calling the award-letter LLM. If
    that call pended under the `interactive` provider (LLMPendingInteractive,
    exit 3), the role was no longer "pending" on retry, so
    `python3 tools/capex.py approve pool-deck-refurb --role "Owner rep"` -
    the documented recovery step - failed forever with "is not a pending
    role". The fix caches the final approval on the award-letter item's
    `_capex_approval_cache` payload key (marker-after-pend, same convention
    as `_match_cache` - docs/how-it-works.md "Resumable stages") and only
    commits it to the project's own approvals record once the letter is
    actually drafted, so a retry resumes and completes.
    """
    # Redirect only core.llm's data/pending/ writes to tmp_path - NOT the
    # whole repo root, or the interactive provider could not find the real
    # prompts/award-letter.md template.
    def _fake_sub_data_dir(name):
        d = tmp_path / "data" / name
        d.mkdir(parents=True, exist_ok=True)
        return d
    monkeypatch.setattr(core_llm, "sub_data_dir", _fake_sub_data_dir)

    settings = Settings(hotel=HotelConfig(currency="EUR"), llm=LLMConfig(provider="interactive"))
    store = Store(settings, path=tmp_path / "capex.db")
    store_ext.ensure_schema(store)
    roles = ["Chief Engineer", "General Manager", "Owner rep"]
    store_ext.seed_capex(store, REPO_ROOT / "fixtures" / "tender" / "capex-projects.json",
                         roles=roles)

    assert capex.cmd_draft(store, settings, SimpleNamespace(id="pool-deck-refurb")) == 0
    for role in ("Chief Engineer", "General Manager"):
        args = SimpleNamespace(id="pool-deck-refurb", role=role, provider=None)
        assert capex.cmd_approve(store, settings, args) == 0

    # The last role's approval triggers the award-letter LLM call, which
    # parks under the interactive provider - reproduces live, exit code 3.
    final_args = SimpleNamespace(id="pool-deck-refurb", role="Owner rep", provider=None)
    assert capex.cmd_approve(store, settings, final_args) == 3

    row = store_ext.get_capex(store, "pool-deck-refurb")
    assert row["stage"] != "approved"  # not committed while the letter is unresolved
    owner_rep = next(a for a in row["approvals"] if a["role"] == "Owner rep")
    assert owner_rep["status"] != "approved"  # still honestly "pending"

    item = store.get_by_external("capex", "pool-deck-refurb")
    assert item is not None and item.draft is None
    assert (item.payload or {}).get("_capex_approval_cache", {}).get("role") == "Owner rep"

    # Answer the parked prompt exactly as the documented recovery step says.
    pending = _fake_sub_data_dir("pending")
    prompt_path = pending / "award-letter-pool-deck-refurb.prompt.md"
    assert prompt_path.exists()
    answer_path = pending / "award-letter-pool-deck-refurb.answer.json"
    answer_path.write_text(json.dumps({
        "subject": "Letter of award - Pool deck refurbishment",
        "body": "Dear Azure Pool Works, we are pleased to confirm the award.",
    }), encoding="utf-8")

    # Re-running the SAME documented command must resume and complete, not
    # fail "is not a pending role on pool-deck-refurb".
    rc2 = capex.cmd_approve(store, settings, final_args)
    assert rc2 == 0

    row2 = store_ext.get_capex(store, "pool-deck-refurb")
    assert row2["stage"] == "approved"
    assert all(a["status"] == "approved" for a in row2["approvals"])

    item2 = store.get_by_external("capex", "pool-deck-refurb")
    assert item2.draft is not None
    assert item2.review_status == "pending_review"
    assert "_capex_approval_cache" not in (item2.payload or {})  # marker cleared
    store.close()


def test_requisition_vendor_confirmation_resumes_after_interactive_pend_without_losing_the_approval(
    tmp_path, monkeypatch
):
    """Regression for the round-2 (scoped) incidental finding in
    SIMULATION.md: tools/requisition.py's cmd_approve() used to write the
    final role's approval (store_ext.set_requisition_approvals(...,
    stage="approved")) AND the PO (store_ext.set_requisition_po(), which
    also flips stage to 'po_created') BEFORE calling the vendor-confirmation
    LLM. If that call pended under the `interactive` provider
    (LLMPendingInteractive, exit 3), the requisition was no longer
    'awaiting_approval' on retry, so `python3 tools/requisition.py approve
    req-001 --role "Department Head"` - the only documented recovery step -
    failed forever with "not waiting on an approval" / "not a pending
    role". The fix ports fix-pass-1's CAPEX marker-after-pend pattern: cache
    `{role, new_approvals, po_ref}` in core.store's `kv` table, keyed by
    requisition id, and only commit them to the requisition's own record
    once the vendor confirmation is actually drafted, so a retry resumes and
    completes - see docs/how-it-works.md, "Resumable stages".
    """
    # Redirect only core.llm's data/pending/ writes to tmp_path - NOT the
    # whole repo root, or the interactive provider could not find the real
    # prompts/vendor-confirmation.md template.
    def _fake_sub_data_dir(name):
        d = tmp_path / "data" / name
        d.mkdir(parents=True, exist_ok=True)
        return d
    monkeypatch.setattr(core_llm, "sub_data_dir", _fake_sub_data_dir)

    # Deterministic, strictly-increasing timestamps so a re-approve on retry
    # (the bug this guards against) is distinguishable from a true resume:
    # a resume reuses the FIRST timestamp, a re-approve would stamp a new one.
    ticks = itertools.count()
    monkeypatch.setattr(requisition, "utcnow", lambda: f"2026-01-01T00:00:{next(ticks):02d}+00:00")

    settings = Settings(hotel=HotelConfig(currency="EUR"), llm=LLMConfig(provider="interactive"))
    store = Store(settings, path=tmp_path / "requisition-retry.db")
    store_ext.ensure_schema(store)
    # req-001 (fixtures/inbound/requisitions/req-001.json): EUR 450 against
    # 'F&B' - the delegation matrix's first band (max_eur: 1000) is a single
    # role, "Department Head", so approving it is both the first AND the
    # final role in one call.
    store_ext.create_requisition(
        store, "req-001", title="Coffee machine quarterly service", department="F&B",
        requested_by="Restaurant Manager", vendor="Bean & Brew Maintenance",
        amount_eur=450.00, budget_line="F&B", budget_check={"ok": True},
        approvals=[{"role": "Department Head", "status": "pending", "approved_at": None}],
        stage="awaiting_approval")

    args = SimpleNamespace(id="req-001", role="Department Head", provider=None)
    # The last (only) role's approval triggers the vendor-confirmation LLM
    # call, which parks under the interactive provider - reproduces live,
    # exit code 3.
    assert requisition.cmd_approve(store, settings, args) == 3

    row = store_ext.get_requisition(store, "req-001")
    assert row["stage"] == "awaiting_approval"  # not committed while the draft is unresolved
    dept_head = next(a for a in row["approvals"] if a["role"] == "Department Head")
    assert dept_head["status"] != "approved"  # still honestly "pending"
    assert row["po_ref"] is None  # no PO number committed onto the requisition yet

    cached = store.get("p2p_requisition_pending_approval:req-001")
    assert cached is not None and cached["role"] == "Department Head"
    po_ref = cached["po_ref"]
    cached_approval = next(a for a in cached["new_approvals"] if a["role"] == "Department Head")
    assert cached_approval["status"] == "approved"
    approved_at_first = cached_approval["approved_at"]

    item = store.get_by_external("requisitions", po_ref)
    assert item is not None and item.draft is None

    # Answer the parked prompt exactly as the documented recovery step says.
    pending = _fake_sub_data_dir("pending")
    prompt_path = pending / f"vendor-confirmation-{po_ref}.prompt.md"
    assert prompt_path.exists()
    answer_path = pending / f"vendor-confirmation-{po_ref}.answer.json"
    answer_path.write_text(json.dumps({
        "subject": f"Purchase order confirmation - {po_ref}",
        "body": "Hello, please confirm the agreed price and an expected delivery date by "
               "reply. Thank you, regards.",
    }), encoding="utf-8")

    # Re-running the SAME documented command must resume and complete, not
    # fail "not waiting on an approval" / "is not a pending role on req-001".
    rc2 = requisition.cmd_approve(store, settings, args)
    assert rc2 == 0

    row2 = store_ext.get_requisition(store, "req-001")
    assert row2["stage"] == "po_created"
    assert row2["po_ref"] == po_ref
    final_approval = next(a for a in row2["approvals"] if a["role"] == "Department Head")
    assert final_approval["status"] == "approved"
    # Approval recorded exactly once: the resumed run reused the FIRST
    # timestamp rather than re-approving with a fresh one.
    assert final_approval["approved_at"] == approved_at_first

    item2 = store.get_by_external("requisitions", po_ref)
    assert item2.draft is not None
    assert item2.review_status == "pending_review"

    assert store.get("p2p_requisition_pending_approval:req-001") is None  # marker cleared

    # A PO was created exactly once for this requisition, not twice.
    assert len(store_ext.list_pos(store)) == 1

    # Re-approving now (the requisition is no longer 'awaiting_approval')
    # fails cleanly - no duplicate PO, no second vendor-confirmation draft.
    assert requisition.cmd_approve(store, settings, args) == 1
    assert len(store_ext.list_pos(store)) == 1
    store.close()


def test_already_processed_never_reports_a_still_new_item(tmp_path):
    """core.store.already_processed ignores rows stuck in 'new' - a bulk
    pre-filter in run.py must not skip an item an earlier pass parked and
    never finished. See core/store.py docstring."""
    settings = load_settings(demo=True)
    store = Store(settings, path=tmp_path / "prefilter.db")
    store_ext.ensure_schema(store)
    store.upsert_item("invoices", "inv-002", kind="invoice", payload=VARIANCE_INVOICE)
    seen = store.already_processed("invoices", ["inv-002"])
    assert "inv-002" not in seen
    store.close()
