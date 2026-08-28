"""tools/drafting.py: check_language() - the language rule (MAJOR finding in
SIMULATION.md, 2026-08-27): every prompts/*.md now tells the model to write
in {{default_language}}, and this is the check that catches a draft that did
not comply - core.i18n.detect_language, same detector a guest-facing repo
uses to pick a reply language, applied here to an outbound business email.
See docs/how-it-works.md, "Language rule", and
factory/workflows/build-repo.md section 5, "Reply only in the hotel's
languages."
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (REPO_ROOT, REPO_ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from core.config import HotelConfig, Settings, load_settings  # noqa: E402
from core.store import Store  # noqa: E402

import drafting  # noqa: E402
import requisition  # noqa: E402
import run  # noqa: E402
import store_ext  # noqa: E402

GERMAN_DRAFT = {
    "subject": "Bestellbestätigung - PO-00099",
    "body": ("Hallo, bitte betrachten Sie dies als Bestätigung der Bestellung PO-00099 "
            "über EUR 450,00 für die vierteljährliche Wartung. Könnten Sie den "
            "vereinbarten Preis und ein voraussichtliches Lieferdatum per Antwort "
            "bestätigen? Vielen Dank und freundliche Grüße."),
}


def test_check_language_accepts_a_supported_language_german_hotel():
    """Gasthof Alpenrose-style hotel (SIMULATION.md persona): German first,
    English second - a German supplier draft is exactly what should happen,
    and must not be held."""
    settings = Settings(hotel=HotelConfig(languages=["de", "en"]))
    assert drafting.check_language(settings, GERMAN_DRAFT) is None


def test_check_language_flags_a_language_outside_hotel_languages():
    """An English-only property (hotel.languages: [en]) getting a German
    draft for a German supplier - the exact gap SIMULATION.md Finding 3
    describes: nothing previously told the model which language to use, and
    nothing checked what it actually wrote."""
    settings = Settings(hotel=HotelConfig(languages=["en"]))
    reason = drafting.check_language(settings, GERMAN_DRAFT)
    assert reason is not None
    assert "'de'" in reason
    assert "en" in reason  # names hotel.languages so a human can see the gap


def test_check_language_does_not_flag_short_or_ambiguous_text():
    settings = Settings(hotel=HotelConfig(languages=["en"]))
    assert drafting.check_language(settings, {"body": "PO-00099"}) is None
    assert drafting.check_language(settings, {"body": ""}) is None
    assert drafting.check_language(settings, {}) is None


def test_check_language_accepts_the_hotels_own_english_default():
    settings = Settings(hotel=HotelConfig(languages=["en", "pt", "es"]))
    assert drafting.check_language(
        settings, {"body": "Hello, please confirm the agreed price and delivery date. "
                           "Thank you, our records show the purchase order for your "
                           "reference. Regards."}) is None


def test_vendor_confirmation_needs_human_when_drafted_outside_hotel_languages(
    tmp_path, monkeypatch
):
    """End-to-end through the real call site: tools/requisition.py's
    cmd_approve() must hold a vendor-confirmation draft for a human instead
    of auto-clearing it to pending_review when the draft comes back in a
    language the hotel does not list - see workflows/00-setup.md's language
    guidance and docs/how-it-works.md "Language rule"."""
    settings = load_settings(demo=True)  # hotel.languages: [en, pt, es] - no "de"
    store = Store(settings, path=tmp_path / "lang.db")
    store_ext.ensure_schema(store)
    run.process_requisitions(settings, store, limit=50)  # seeds req-001 etc.

    monkeypatch.setattr(drafting, "draft_vendor_confirmation",
                        lambda *a, **kw: dict(GERMAN_DRAFT))

    args = SimpleNamespace(id="req-001", role="Department Head", provider=None)
    rc = requisition.cmd_approve(store, settings, args)
    assert rc == 0

    po = store_ext.get_requisition(store, "req-001")
    item = store.get_by_external("requisitions", po["po_ref"])
    assert item is not None and item.draft is not None
    assert item.review_status == "needs_human"  # NOT auto-cleared to pending_review

    events = store.list_events(item.id)
    reasons = [e["detail"].get("language") for e in events if e["detail"].get("language")]
    assert reasons and "'de'" in reasons[0]
    store.close()


def test_vendor_confirmation_clears_normally_when_language_matches(tmp_path, monkeypatch):
    """Same call site, but the draft is in the hotel's own default language -
    must clear straight to pending_review exactly as before this fix."""
    settings = load_settings(demo=True)
    store = Store(settings, path=tmp_path / "lang2.db")
    store_ext.ensure_schema(store)
    run.process_requisitions(settings, store, limit=50)

    english_draft = {"subject": "Purchase order confirmation - PO-00099",
                     "body": ("Hello, please treat this as confirmation of the order. "
                             "Could you confirm the agreed price and an expected "
                             "delivery date by reply? Thank you, regards.")}
    monkeypatch.setattr(drafting, "draft_vendor_confirmation",
                        lambda *a, **kw: dict(english_draft))

    args = SimpleNamespace(id="req-001", role="Department Head", provider=None)
    assert requisition.cmd_approve(store, settings, args) == 0

    po = store_ext.get_requisition(store, "req-001")
    item = store.get_by_external("requisitions", po["po_ref"])
    assert item.review_status == "pending_review"
    store.close()
