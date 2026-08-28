#!/usr/bin/env python3
"""tools/doctor.py - is Procure-to-Pay AI configured and ready right now?

    make doctor
    python3 tools/doctor.py

Runs the generic core.doctor checks (python, deps, config, .env, hotel
identity, mode, llm provider, every adapter, the store, knowledge) plus this
agent's own: the delegation matrix, the budgets, the prompts, and the CAPEX
sub-agent's config when it is enabled. Exits 0 when everything passed, 1 when
a FAIL line needs fixing. Never a traceback.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, Settings, load_settings  # noqa: E402
from core.doctor import Check, FAIL, PASS, WARN, print_table, run_checks  # noqa: E402


def check_delegation_matrix(settings: Settings) -> Check:
    matrix = settings.agent_get("delegation_matrix", [])
    if not matrix:
        return Check("delegation matrix", FAIL, "no bands in config/agent.yaml",
                     "Copy config/agent.example.yaml to config/agent.yaml - it ships with a "
                     "three-band example.")
    if matrix[-1].get("max_eur") is not None:
        return Check("delegation matrix", WARN,
                     "the last band has a max_eur - a requisition above it gets every role",
                     "Set the last band's max_eur to null so it always matches.")
    return Check("delegation matrix", PASS, f"{len(matrix)} band(s)")


def check_budgets(settings: Settings) -> Check:
    budgets = settings.agent_get("budgets", {})
    if not budgets:
        return Check("budgets", FAIL, "no budget lines in config/agent.yaml",
                     "Add at least one line under budgets: - a requisition against an unknown "
                     "line is always held.")
    return Check("budgets", PASS, f"{len(budgets)} line(s): {', '.join(sorted(budgets))}")


def check_prompts() -> Check:
    missing = [p for p in (
        "prompts/vendor-query.md", "prompts/vendor-confirmation.md",
        "prompts/approval-chase.md", "prompts/award-letter.md",
        "prompts/schemas/vendor-query.json", "prompts/schemas/vendor-confirmation.json",
        "prompts/schemas/approval-chase.json", "prompts/schemas/award-letter.json",
    ) if not (REPO_ROOT / p).is_file()]
    if missing:
        return Check("prompts", FAIL, f"missing {', '.join(missing)}",
                     "These ship with the repo - restore them from git.")
    return Check("prompts", PASS, "all four drafting prompts + schemas present")


def check_prompt_languages() -> Check:
    """The language rule (docs/how-it-works.md): every drafting prompt must
    tell the model which language to write in. A prompt that never mentions
    {{default_language}} or {{hotel_languages}} is a prompt the model will
    default to English on, whatever hotel.languages says - see
    SIMULATION.md, Finding 3 (2026-08-27)."""
    prompts = ("prompts/vendor-query.md", "prompts/vendor-confirmation.md",
              "prompts/approval-chase.md", "prompts/award-letter.md")
    missing = []
    for p in prompts:
        path = REPO_ROOT / p
        if not path.is_file():
            continue  # check_prompts() already FAILs a missing file
        text = path.read_text(encoding="utf-8")
        if "{{default_language}}" not in text and "{{hotel_languages}}" not in text:
            missing.append(p)
    if missing:
        return Check("prompt languages", WARN,
                     f"{', '.join(missing)} never reference hotel.languages",
                     "Add a line telling the model to write in {{default_language}} - "
                     "see prompts/vendor-query.md for the wording. Otherwise a draft "
                     "silently defaults to English regardless of the hotel's languages.")
    return Check("prompt languages", PASS, "every drafting prompt names the target language")


def check_capex(settings: Settings) -> Check:
    if not bool(settings.agent_get("subagents.tender_capex.enabled", False)):
        return Check("tender & capex sub-agent", WARN, "disabled - the parent works fully "
                     "without it", "See docs/sub-agents.md if you want tender/CAPEX support.")
    roles = settings.agent_get("subagents.tender_capex.roles", [])
    if not roles:
        return Check("tender & capex sub-agent", FAIL, "enabled but no approval roles configured",
                     "Set subagents.tender_capex.roles in config/agent.yaml.")
    return Check("tender & capex sub-agent", PASS, f"enabled, roles: {', '.join(roles)}")


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        checks = run_checks(None) + [Check("config", FAIL, str(exc),
                                           "Fix config/hotel.yaml or config/agent.yaml.")]
        return print_table(checks, title="Procure-to-Pay AI - doctor")

    checks = run_checks(settings, extra=[check_delegation_matrix, check_budgets, check_capex])
    checks.append(check_prompts())
    checks.append(check_prompt_languages())
    return print_table(checks, title="Procure-to-Pay AI - doctor")


if __name__ == "__main__":
    raise SystemExit(main())
