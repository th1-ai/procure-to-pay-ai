# Sub-agents in this repo

## Tender & CAPEX Approval AI ("The Chancellor")

**Off by default** (`config/agent.yaml: subagents.tender_capex.enabled`). The
parent - requisitions, purchase orders, goods receipts, 3-way matching,
payment-batch preparation - is fully useful without it. Turn it on when the
property also wants help running big-ticket tenders and capital projects
through a scored comparison and a multi-role sign-off.

**What it adds.**

- A quote comparison, scored deterministically from each quote's amount,
  weeks on site and a free-text note (warranty years, a scope gap, whether
  the work phases around trading) - see `tools/capex_engine.py` and
  `docs/how-it-works.md` for the exact formula.
- A "three comparable quotes above the threshold, or a documented exception"
  gate (`rules.capex-three-quotes`) - fewer than three quotes and the AI will
  not put a recommendation to the approval chain at all, only narrate why.
- A recommendation, addressed "for committee consideration" rather than
  labelled the winner - it names the runner-up, says why it lost in money
  terms, and surfaces the trade-off (cheaper, faster, keeps trading) the
  committee would otherwise have to work out itself.
- A three-role approval chain (configurable roles, `subagents.tender_capex.roles`)
  that stays locked until a recommendation exists.
- A drafted letter of award, once every role has signed, queued into the
  normal review flow.

**Guardrail.** "Never picks the winner" is the roster's own promise, and the
engine does rank and does name a top quote. The defence, made explicit
everywhere this repo shows the recommendation: it is put to the approval
chain, not released - every role still has to sign, and a scope-gap quote is
disqualified (score `-999`) rather than merely marked down, so cheapest never
wins by default on a quote that omits real work.

**What it does not do** (see `specs/tender-capex-approval-ai.md` section 11 in
the factory this repo was built from, if you have access to it): budget
requests, RFP assembly, vendor qualification and bid collection all happen
before this sub-agent starts - it begins once quotes are already in hand.
There is no SLA/reminder/escalation on a CAPEX approval yet, unlike a
requisition's own chain (`tools/chase.py` only chases requisitions, not
CAPEX approvals).

**The award -> PO bridge is deliberately not built.** An awarded tender
should, in a fuller version, become a purchase order in the parent's own `p2p_pos`
ledger, so the vendor's invoices are 3-way matched against the award amount
and a variation order shows up as a price variance. This repo keeps
`p2p_capex` and `p2p_pos` separate on purpose, for the same reason the source
spec flags it as unbuilt: building that bridge well needs a real
line-items model (quantities, provisional sums, phased payment terms) that
neither table has yet. See `docs/how-it-works.md`, "Core requests", for what
a fuller version would need.

Turn it on: `workflows/20-tender-capex.md`.
