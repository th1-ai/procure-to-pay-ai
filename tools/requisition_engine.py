"""tools/requisition_engine.py - budget check + delegation-of-authority routing.

Pure functions over plain dicts, no I/O, unit-tested directly in
tests/test_p2p_engine.py. Two decisions, both deterministic:

    budget_check()       does this amount fit the remaining budget line?
    approval_chain_for()  which roles, in which order, must sign?

Shared by tools/run.py (real intake) and tools/demo.py (the zero-credential
walkthrough), so both exercise exactly the same code path.
"""

from __future__ import annotations

from typing import Any

from money import fmt


def budget_check(amount_eur: float, budget_line: str, budgets: dict,
                 committed_eur: float) -> dict:
    """Spec: "purchase requisitions with budget checks." A budget line not
    found in config is treated as a hold, not a silent pass - Finance has to
    add it, not the agent."""
    entry = (budgets or {}).get(budget_line)
    if entry is None:
        return {"ok": False,
                "reason": f"No budget line '{budget_line}' is configured. Held for Finance "
                          f"to assign one in config/agent.yaml: budgets."}
    limit_eur = float(entry.get("limit_eur") or 0)
    remaining = limit_eur - committed_eur
    if amount_eur > remaining:
        return {"ok": False, "limit_eur": limit_eur, "committed_eur": committed_eur,
                "remaining_eur": remaining,
                "reason": f"{fmt(amount_eur)} exceeds the {fmt(remaining)} remaining on the "
                          f"'{budget_line}' budget ({fmt(limit_eur)} total, "
                          f"{fmt(committed_eur)} already committed). Held for a budget "
                          f"exception."}
    return {"ok": True, "limit_eur": limit_eur, "committed_eur": committed_eur,
            "remaining_eur": remaining,
            "reason": f"{fmt(amount_eur)} is within the {fmt(remaining)} remaining on the "
                      f"'{budget_line}' budget."}


def approval_chain_for(amount_eur: float, matrix: list[dict]) -> list[str]:
    """Spec: "approval routing per your delegation-of-authority matrix."

    ``matrix`` is ``config/agent.yaml: delegation_matrix`` - amount bands in
    ascending order, first match wins. The last band should carry
    ``max_eur: null`` so every amount lands somewhere; if the matrix is
    misconfigured and nothing matches, every role from every band is
    returned rather than silently approving with an empty chain.
    """
    for band in matrix or []:
        ceiling = band.get("max_eur")
        if ceiling is None or amount_eur <= float(ceiling):
            return list(band.get("roles") or [])
    all_roles: list[str] = []
    for band in matrix or []:
        for role in band.get("roles") or []:
            if role not in all_roles:
                all_roles.append(role)
    return all_roles


def approvals_json(roles: list[str]) -> list[dict]:
    return [{"role": r, "status": "pending", "approved_at": None} for r in roles]


def approvals_complete(approvals: list[dict]) -> bool:
    return bool(approvals) and all(a.get("status") == "approved" for a in approvals)


def next_pending_roles(approvals: list[dict]) -> list[str]:
    return [a["role"] for a in approvals if a.get("status") != "approved"]


def approve_role(approvals: list[dict], role: str, *, approved_at: str) -> tuple[list[dict], bool]:
    """Mark ``role`` approved. Returns ``(new_approvals, found)``. Any role may
    approve in any order - see docs/how-it-works.md design decision 3."""
    found = False
    out = []
    for a in approvals:
        if a["role"] == role and a.get("status") != "approved":
            out.append({**a, "status": "approved", "approved_at": approved_at})
            found = True
        else:
            out.append(a)
    return out, found


def summarise_requisition(title: str, amount_eur: float, budget_line: str,
                          chain: list[str]) -> str:
    """One line for a human working the queue - not sent anywhere, just
    printed by tools/requisition.py."""
    roles = " -> ".join(chain) if chain else "(no approvers configured)"
    return f"{title}: {fmt(amount_eur)} against '{budget_line}'. Chain: {roles}."
