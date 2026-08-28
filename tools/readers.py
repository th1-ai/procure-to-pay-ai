"""tools/readers.py - the small, credential-free readers this agent needs.

None of these four record kinds (purchase orders, requisitions, invoices,
goods receipts) has a core adapter yet - ``core.adapters.get_stub("procurement",
settings)`` is a pure interface, exactly like ``finance-filing-ai``'s
``tools/po_ledger.py`` (see that repo's own build report and
docs/how-it-works.md, "Core requests"). Each reader here is ``mock`` (reads a
fixture) or ``csv`` (reads a plain export in ``data/imports/``) - never a
network call, so ``make demo`` and ``make test`` never need credentials.

Every record kind carries an ``id`` used for dedup: `core.store.upsert_item` /
`store_ext.create_requisition` are both keyed on it, so re-reading the same
fixture or CSV row twice is always a no-op.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from core.config import Settings, repo_root, sub_data_dir


def _bool(value: object) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "y")


def _float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _read_dir(dirname: str) -> list[dict]:
    """Every ``*.json`` file under ``fixtures/inbound/<dirname>/``, one record
    each, id defaulting to the filename stem - the same convention
    ``core.adapters.email_mock`` uses for a single email per file."""
    base = repo_root() / "fixtures" / "inbound" / dirname
    out: list[dict] = []
    if not base.is_dir():
        return out
    for path in sorted(base.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict):
            raw.setdefault("id", path.stem)
            out.append(raw)
    return out


def _read_csv(name: str) -> list[dict]:
    path = sub_data_dir("imports") / name
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


# --------------------------------------------------------------------------
# purchase orders (the ledger invoices get matched against)
# --------------------------------------------------------------------------
def read_purchase_orders(settings: Settings) -> list[dict]:
    """``mock``: fixtures/hotel/purchase-orders.json (the seeded ledger - see
    tools/store_ext.py:seed_pos). ``csv``: data/imports/purchase_orders.csv.
    Only used by ``csv`` mode here; ``mock`` mode is seeded once at startup by
    tools/run.py, not re-read every pass."""
    adapter = str(settings.agent_get("po_reader.adapter", "mock") or "mock").lower()
    if adapter != "csv":
        return []
    rows = _read_csv("purchase_orders.csv")
    return [{
        "po_ref": str(r.get("po_ref") or "").strip().upper(), "vendor": r.get("vendor", ""),
        "amount_eur": _float(r.get("amount_eur")), "description": r.get("description", ""),
        "received": _bool(r.get("received", False)),
    } for r in rows if r.get("po_ref")]


# --------------------------------------------------------------------------
# requisitions
# --------------------------------------------------------------------------
def read_requisitions(settings: Settings) -> list[dict]:
    adapter = str(settings.agent_get("po_reader.adapter", "mock") or "mock").lower()
    if adapter == "csv":
        rows = _read_csv("requisitions.csv")
    else:
        rows = _read_dir("requisitions")
    return [{
        "id": str(r.get("id", "")), "title": str(r.get("title", "")),
        "department": str(r.get("department", "")), "requested_by": str(r.get("requested_by", "")),
        "vendor": str(r.get("vendor", "")),
        "amount_eur": _float(r.get("amount_eur")), "budget_line": str(r.get("budget_line", "")),
    } for r in rows if r.get("id")]


# --------------------------------------------------------------------------
# invoices (already structured - see docs/how-it-works.md "Where this agent
# starts and stops": extraction from a raw email/PDF is Finance Filing AI's
# job, not this one)
# --------------------------------------------------------------------------
def read_invoices(settings: Settings) -> list[dict]:
    adapter = str(settings.agent_get("invoice_reader.adapter", "mock") or "mock").lower()
    if adapter == "csv":
        rows = _read_csv("invoices.csv")
    else:
        rows = _read_dir("invoices")
    out = []
    for r in rows:
        if not r.get("id"):
            continue
        out.append({
            "id": str(r["id"]), "vendor": str(r.get("vendor", "")),
            "invoice_no": str(r.get("invoice_no", "")), "amount_eur": _float(r.get("amount_eur")),
            "currency": str(r.get("currency") or "EUR"),
            "po_ref": (str(r["po_ref"]).strip().upper() if r.get("po_ref") else None),
            "description": str(r.get("description", "")),
        })
    return out


# --------------------------------------------------------------------------
# goods receipts
# --------------------------------------------------------------------------
def read_goods_receipts(settings: Settings) -> list[dict]:
    adapter = str(settings.agent_get("receipt_reader.adapter", "mock") or "mock").lower()
    if adapter == "csv":
        rows = _read_csv("goods_receipts.csv")
    else:
        rows = _read_dir("goods-receipts")
    return [{
        "id": str(r.get("id", "")),
        "po_ref": str(r.get("po_ref") or "").strip().upper(),
        "note": str(r.get("note", "")),
    } for r in rows if r.get("id") and r.get("po_ref")]
