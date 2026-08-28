"""tools/drafting.py - the four LLM calls in this repo, all "language, not
decisioning" (see docs/how-it-works.md's opening paragraph). Every decision
that leads here (hold this invoice, create this PO, chase this approver,
award this tender) was already made by deterministic code in
tools/*_engine.py; these functions only turn that decision into prose a
human reads before it goes anywhere - see core/llm.py and
core/templates.py:build_prompt for how the four providers share one prompt.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.config import Settings
from core.i18n import detect_language
from core.llm import LLMResult, complete
from core.store import Item, Store
from core.templates import build_prompt

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "prompts" / "schemas"


def _schema(name: str) -> dict:
    return json.loads((SCHEMAS_DIR / f"{name}.json").read_text(encoding="utf-8"))


VENDOR_QUERY_SCHEMA = _schema("vendor-query")
VENDOR_CONFIRMATION_SCHEMA = _schema("vendor-confirmation")
APPROVAL_CHASE_SCHEMA = _schema("approval-chase")
AWARD_LETTER_SCHEMA = _schema("award-letter")


def _run(task: str, schema: dict, settings: Settings, store: Store | None, item: Item,
        payload: dict, *, fixture_id: str, effort: str, provider: str | None) -> dict:
    prompt = build_prompt(task, settings=settings, item=payload, fixture_id=fixture_id)
    result: LLMResult = complete(task, prompt, schema, settings=settings, provider=provider,
                                 store=store, item_id=item.id, fixture_id=fixture_id, effort=effort)
    return result.data or {}


def draft_vendor_query(settings: Settings, store: Store | None, item: Item, invoice: dict,
                       match: dict, *, provider: str | None = None) -> dict:
    payload = {**invoice, "match": match.get("match"), "reason": match.get("reason", "")}
    effort = str(settings.agent_get("llm.vendor_query_effort", "medium"))
    return _run("vendor-query", VENDOR_QUERY_SCHEMA, settings, store, item, payload,
               fixture_id=invoice["id"], effort=effort, provider=provider)


def draft_vendor_confirmation(settings: Settings, store: Store | None, item: Item,
                              po: dict, *, provider: str | None = None) -> dict:
    effort = str(settings.agent_get("llm.vendor_confirmation_effort", "low"))
    return _run("vendor-confirmation", VENDOR_CONFIRMATION_SCHEMA, settings, store, item, po,
               fixture_id=po["po_ref"], effort=effort, provider=provider)


def draft_approval_chase(settings: Settings, store: Store | None, item: Item,
                         requisition: dict, role: str, days_waiting: int, *,
                         provider: str | None = None) -> dict:
    payload = {**requisition, "role_waiting": role, "days_waiting": days_waiting}
    effort = str(settings.agent_get("llm.approval_chase_effort", "low"))
    return _run("approval-chase", APPROVAL_CHASE_SCHEMA, settings, store, item, payload,
               fixture_id=f"{requisition['id']}-{role}", effort=effort, provider=provider)


def draft_award_letter(settings: Settings, store: Store | None, item: Item, capex: dict, *,
                       provider: str | None = None) -> dict:
    effort = str(settings.agent_get("llm.award_letter_effort", "medium"))
    return _run("award-letter", AWARD_LETTER_SCHEMA, settings, store, item, capex,
               fixture_id=capex["id"], effort=effort, provider=provider)


# --------------------------------------------------------------------------
# language rule - see docs/how-it-works.md, "Language rule", and
# factory/workflows/build-repo.md section 5, "Reply only in the hotel's
# languages."
# --------------------------------------------------------------------------
def check_language(settings: Settings, draft: dict) -> str | None:
    """Verify a draft actually came back in one of ``hotel.languages``.

    Every ``prompts/*.md`` file tells the model to write in
    ``{{default_language}}`` (the property's own working language - there is
    no per-vendor language on file in this repo's data model, unlike a
    guest-facing agent with a PMS language field). This is the check that
    catches a model that did not comply: same detector, same idea as the
    guest-facing rule, applied to an outbound business email instead of a
    reply to a guest. Returns a ``needs_human`` reason string when the
    drafted body is confidently in a language NOT in ``hotel.languages``,
    else ``None`` - a low-confidence guess (``detect_language`` already
    falls back to the hotel default for those) is never flagged, so a short
    or ambiguous email is not held for nothing.
    """
    body = str(draft.get("body") or "").strip()
    if not body:
        return None
    guess = detect_language(body, settings=settings)
    if guess.source == "text" and guess.lang not in settings.hotel.languages:
        return (f"drafted in '{guess.lang}', not in hotel.languages "
               f"({', '.join(settings.hotel.languages)}) - held for a human to "
               f"check or rewrite in {settings.hotel.default_language}")
    return None
