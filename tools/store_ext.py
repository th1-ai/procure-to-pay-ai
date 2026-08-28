"""tools/store_ext.py - Procure-to-Pay AI's own tables, on top of core.store.Store.

The generic ``items`` table (core/store.py) is the single-decision review
queue: invoices, vendor queries, vendor confirmations, approval chases and
award letters all live there, because each needs exactly one human decision.

Requisitions and CAPEX projects need a *sequence* of named-role decisions
instead, which does not fit that FSM, so they get their own tables here - the
same shape the source demo's Chancellor tab used for ``fin_capex``. Purchase
orders are their own ledger too: ``p2p_pos`` is what invoices get matched
against, and it is written from two different places (a requisition's
approval completing, or a demo/CSV seed of pre-existing POs).

Call :func:`ensure_schema` once per ``Store``, right after constructing it -
every tool in this repo does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.store import Store, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS p2p_requisitions (
  id                 TEXT PRIMARY KEY,
  title              TEXT NOT NULL,
  department         TEXT NOT NULL,
  requested_by       TEXT NOT NULL,
  vendor             TEXT NOT NULL,
  amount_eur         REAL NOT NULL,
  budget_line        TEXT NOT NULL,
  budget_check_json  TEXT,
  approvals_json     TEXT,
  stage              TEXT NOT NULL DEFAULT 'new',
  po_ref             TEXT,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS p2p_pos (
  po_ref            TEXT PRIMARY KEY,
  requisition_id    TEXT,
  vendor            TEXT NOT NULL,
  amount_eur        REAL NOT NULL,
  description       TEXT,
  vendor_confirmed  INTEGER NOT NULL DEFAULT 0,
  received          INTEGER NOT NULL DEFAULT 0,
  received_at       TEXT,
  source            TEXT NOT NULL DEFAULT 'requisition',
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS p2p_capex (
  id              TEXT PRIMARY KEY,
  title           TEXT NOT NULL,
  category        TEXT NOT NULL,
  budget_eur      REAL NOT NULL,
  quotes_json     TEXT,
  approvals_json  TEXT,
  stage           TEXT NOT NULL DEFAULT 'draft',
  recommendation  TEXT,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);
"""

STAGES = ("new", "budget_hold", "awaiting_approval", "approved", "po_created", "rejected")


def ensure_schema(store: Store) -> None:
    store.migrate(SCHEMA)


# --------------------------------------------------------------------------
# requisitions
# --------------------------------------------------------------------------
def get_requisition(store: Store, req_id: str) -> dict | None:
    row = store.db.execute("SELECT * FROM p2p_requisitions WHERE id=?", (req_id,)).fetchone()
    return _req_from_row(row) if row else None


def _req_from_row(row) -> dict:
    d = dict(row)
    d["budget_check"] = json.loads(d.pop("budget_check_json") or "null")
    d["approvals"] = json.loads(d.pop("approvals_json") or "[]")
    return d


def list_requisitions(store: Store, *, stage: str | None = None) -> list[dict]:
    if stage:
        rows = store.db.execute(
            "SELECT * FROM p2p_requisitions WHERE stage=? ORDER BY created_at ASC",
            (stage,)).fetchall()
    else:
        rows = store.db.execute(
            "SELECT * FROM p2p_requisitions ORDER BY created_at ASC").fetchall()
    return [_req_from_row(r) for r in rows]


def create_requisition(store: Store, req_id: str, *, title: str, department: str,
                       requested_by: str, vendor: str, amount_eur: float, budget_line: str,
                       budget_check: dict, approvals: list[dict], stage: str) -> dict:
    """Idempotent on ``req_id`` - a re-fetched fixture/import row is a no-op."""
    existing = get_requisition(store, req_id)
    if existing is not None:
        return existing
    now = utcnow()
    store.db.execute(
        "INSERT INTO p2p_requisitions (id, title, department, requested_by, vendor, amount_eur, "
        "budget_line, budget_check_json, approvals_json, stage, po_ref, created_at, "
        "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (req_id, title, department, requested_by, vendor, float(amount_eur), budget_line,
         json.dumps(budget_check, ensure_ascii=False), json.dumps(approvals, ensure_ascii=False),
         stage, None, now, now))
    return get_requisition(store, req_id)  # type: ignore[return-value]


def set_requisition_approvals(store: Store, req_id: str, approvals: list[dict],
                              *, stage: str | None = None) -> None:
    now = utcnow()
    if stage:
        store.db.execute(
            "UPDATE p2p_requisitions SET approvals_json=?, stage=?, updated_at=? WHERE id=?",
            (json.dumps(approvals, ensure_ascii=False), stage, now, req_id))
    else:
        store.db.execute(
            "UPDATE p2p_requisitions SET approvals_json=?, updated_at=? WHERE id=?",
            (json.dumps(approvals, ensure_ascii=False), now, req_id))


def set_requisition_po(store: Store, req_id: str, po_ref: str) -> None:
    now = utcnow()
    store.db.execute(
        "UPDATE p2p_requisitions SET po_ref=?, stage='po_created', updated_at=? WHERE id=?",
        (po_ref, now, req_id))


def budget_committed(store: Store, budget_line: str) -> float:
    """Sum of every open requisition's amount against ``budget_line``.

    Computed live, never a stored counter - see docs/how-it-works.md, design
    decision 2. "Open" means it still holds a claim on the budget: awaiting
    approval, approved, or already turned into a PO. A rejected requisition
    releases its claim automatically because it is excluded here.
    """
    row = store.db.execute(
        "SELECT COALESCE(SUM(amount_eur), 0) AS total FROM p2p_requisitions "
        "WHERE budget_line=? AND stage IN ('awaiting_approval','approved','po_created')",
        (budget_line,)).fetchone()
    return float(row["total"])


# --------------------------------------------------------------------------
# purchase orders
# --------------------------------------------------------------------------
def get_po(store: Store, po_ref: str) -> dict | None:
    row = store.db.execute("SELECT * FROM p2p_pos WHERE po_ref=?", (po_ref,)).fetchone()
    return dict(row) if row else None


def list_pos(store: Store) -> list[dict]:
    rows = store.db.execute("SELECT * FROM p2p_pos ORDER BY created_at ASC").fetchall()
    return [dict(r) for r in rows]


def create_po(store: Store, po_ref: str, *, requisition_id: str | None, vendor: str,
             amount_eur: float, description: str, received: bool = False,
             source: str = "requisition") -> dict:
    existing = get_po(store, po_ref)
    if existing is not None:
        return existing
    now = utcnow()
    store.db.execute(
        "INSERT INTO p2p_pos (po_ref, requisition_id, vendor, amount_eur, description, "
        "vendor_confirmed, received, received_at, source, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (po_ref, requisition_id, vendor, float(amount_eur), description, 0,
         1 if received else 0, now if received else None, source, now, now))
    return get_po(store, po_ref)  # type: ignore[return-value]


def mark_vendor_confirmed(store: Store, po_ref: str) -> None:
    store.db.execute(
        "UPDATE p2p_pos SET vendor_confirmed=1, updated_at=? WHERE po_ref=?",
        (utcnow(), po_ref))


def mark_received(store: Store, po_ref: str, *, note: str = "") -> bool:
    """Record a goods-receipt event. Unguarded on purpose - see
    docs/how-it-works.md design decision 4: this reports a fact from another
    internal system, nothing leaves the building. Returns False (a no-op,
    not an error) for an unknown PO or one already marked received.
    """
    po = get_po(store, po_ref)
    if po is None or po["received"]:
        return False
    now = utcnow()
    store.db.execute(
        "UPDATE p2p_pos SET received=1, received_at=?, updated_at=? WHERE po_ref=?",
        (now, now, po_ref))
    return True


def seed_pos(store: Store, path: Path) -> int:
    """Load ``purchase-orders.json`` once. Returns rows inserted (0 if already seeded)."""
    if store.db.execute("SELECT COUNT(*) AS n FROM p2p_pos").fetchone()["n"]:
        return 0
    if not Path(path).exists():
        return 0
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    for row in rows:
        create_po(store, str(row["po_ref"]).strip().upper(), requisition_id=None,
                  vendor=row.get("vendor", ""), amount_eur=float(row.get("amount_eur") or 0),
                  description=row.get("description", ""), received=bool(row.get("received", False)),
                  source="seed")
    return len(rows)


# --------------------------------------------------------------------------
# CAPEX (Tender & CAPEX Approval AI)
# --------------------------------------------------------------------------
def get_capex(store: Store, project_id: str) -> dict | None:
    row = store.db.execute("SELECT * FROM p2p_capex WHERE id=?", (project_id,)).fetchone()
    return _capex_from_row(row) if row else None


def _capex_from_row(row) -> dict:
    d = dict(row)
    d["quotes"] = json.loads(d.pop("quotes_json") or "[]")
    d["approvals"] = json.loads(d.pop("approvals_json") or "[]")
    return d


def list_capex(store: Store) -> list[dict]:
    rows = store.db.execute("SELECT * FROM p2p_capex ORDER BY created_at ASC").fetchall()
    return [_capex_from_row(r) for r in rows]


def seed_capex(store: Store, path: Path, *, roles: list[str]) -> int:
    if store.db.execute("SELECT COUNT(*) AS n FROM p2p_capex").fetchone()["n"]:
        return 0
    if not Path(path).exists():
        return 0
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    now = utcnow()
    for row in rows:
        approvals = [{"role": r, "status": "pending", "approved_at": None} for r in roles]
        store.db.execute(
            "INSERT INTO p2p_capex (id, title, category, budget_eur, quotes_json, "
            "approvals_json, stage, recommendation, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (row["id"], row["title"], row.get("category", ""), float(row["budget_eur"]),
             json.dumps(row.get("quotes", []), ensure_ascii=False),
             json.dumps(approvals, ensure_ascii=False), row.get("stage", "draft"), None, now, now))
    return len(rows)


def set_capex_recommendation(store: Store, project_id: str, recommendation: str) -> None:
    store.db.execute(
        "UPDATE p2p_capex SET recommendation=?, stage='recommendation', updated_at=? WHERE id=?",
        (recommendation, utcnow(), project_id))


def set_capex_approvals(store: Store, project_id: str, approvals: list[dict],
                        *, stage: str | None = None) -> None:
    now = utcnow()
    if stage:
        store.db.execute(
            "UPDATE p2p_capex SET approvals_json=?, stage=?, updated_at=? WHERE id=?",
            (json.dumps(approvals, ensure_ascii=False), stage, now, project_id))
    else:
        store.db.execute(
            "UPDATE p2p_capex SET approvals_json=?, updated_at=? WHERE id=?",
            (json.dumps(approvals, ensure_ascii=False), now, project_id))


# --------------------------------------------------------------------------
# shared
# --------------------------------------------------------------------------
def load_json_fixture(path: Path) -> list[dict]:
    """Read a fixture straight off disk - no store, no write. Used by
    ``--dry-run`` so a rehearsal can compute a real preview without seeding a
    single row."""
    path = Path(path)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))
