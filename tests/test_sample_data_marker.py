"""Fixture data must never be approved as if it were the hotel's own money.

On a fresh clone every adapter still ships as `mock`, so a REAL (not
`make demo`) pass reads the bundled fixtures. The family-wide fix in
`core.store.Store.upsert_item` (via `core.adapters.is_sample_source`) tags
those items with payload `_sample: True`, which `item.is_sample` reads back.
This repo does not re-implement the tagging - it only consumes it, by
printing a `[SAMPLE DATA]` marker in `make review` (`tools/review.py`
`list` and `show`), which is what these tests pin.

`config/agent.example.yaml: systems_used: [email]` means email is the one
core adapter whose `mock` default counts as sample data here - this agent
never reads a PMS and never sends a guest chat message.

`tests/conftest.py`'s autouse fixture isolates AGENT_CONFIG_DIR /
AGENT_REPO_ROOT for every test in this module.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (REPO_ROOT, REPO_ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from core.config import load_settings  # noqa: E402
from core.store import Store  # noqa: E402

import review  # noqa: E402

INVOICE = {"vendor": "Docapesca Seafood Lda", "po_ref": "PO-00105",
           "total_eur": 412.50, "currency": "EUR"}


def _queued_sample_invoice(tmp_path, *, demo: bool = False):
    """One invoice item, ingested the way a real pass on a fresh clone would."""
    settings = load_settings(demo=demo)
    assert settings.systems.email.adapter == "mock"  # the shipped default
    store = Store(settings, path=tmp_path / "test.db")
    item = store.upsert_item("email", "INV-9001", kind="invoice", payload=dict(INVOICE))
    return settings, store, item


def test_a_real_pass_on_the_mock_default_tags_its_item_sample(tmp_path):
    settings, store, item = _queued_sample_invoice(tmp_path)
    store.close()
    assert settings.demo is False  # the real path, not `make demo`
    assert item.is_sample is True
    assert item.payload.get("_sample") is True


def test_make_review_list_and_show_print_the_sample_marker(tmp_path, capsys):
    settings, store, item = _queued_sample_invoice(tmp_path)
    store.transition(item.id, "pending_review", "agent")
    capsys.readouterr()  # discard anything printed during setup

    review.cmd_list(store, SimpleNamespace(status=None, kind=None, limit=50))
    listed = capsys.readouterr().out
    assert "[SAMPLE DATA]" in listed
    assert "not your property" in listed

    review.cmd_show(store, SimpleNamespace(id=item.id))
    store.close()
    shown = capsys.readouterr().out
    assert shown.startswith("[SAMPLE DATA]")


def test_demo_mode_is_not_marked_sample_it_already_announces_itself(tmp_path):
    # `make demo` announces itself loudly and never shares data/agent.db with
    # a real run, so it is deliberately excluded from the `_sample` tag - see
    # `core.adapters.is_sample_source`.
    settings, store, item = _queued_sample_invoice(tmp_path, demo=True)
    store.close()
    assert settings.demo is True
    assert item.is_sample is False
