# Workflow: Tender & CAPEX Approval AI ("The Chancellor")

Objective: turn on the sub-agent that scores tender/CAPEX quotes, routes a
recommendation to a three-role approval chain, and drafts the letter of
award.

Off by default. The parent (`workflows/10-procure-to-pay.md`) is fully useful
without this - requisitions, purchase orders, goods receipts and 3-way
matching all work with it off. See `docs/sub-agents.md` for the full picture,
and `docs/how-it-works.md` design decision 8 for the "never picks the winner"
distinction this sub-agent is built around.

## Steps

1. **Turn it on.**
   ```yaml
   # config/agent.yaml
   subagents:
     tender_capex:
       enabled: true
       three_quote_threshold_eur: 25000
       roles: ["Chief Engineer", "General Manager", "Owner rep"]
   ```
   `roles` is the approval chain every CAPEX project gets - change the names
   to match who actually signs capital spend at your property.

2. **Load a project.** Two sample projects seed automatically from
   `fixtures/tender/capex-projects.json` the first time you run a
   `tools/capex.py` command. For a real project, add a row there (or ask the
   hotel's Claude session to add one) with `title`, `category`, `budget_eur`
   and a `quotes` list of `{vendor, amount, weeks, note}`.

3. **List and inspect.**
   ```bash
   python3 tools/capex.py list
   python3 tools/capex.py show <id>
   ```

4. **Draft the recommendation.**
   ```bash
   python3 tools/capex.py draft-recommendation <id>
   ```
   With fewer than three comparable quotes and
   `rules.capex-three-quotes: true` (the default), this refuses to
   recommend and names the exception route instead - the comparison table
   still prints. With three or more, it prints the scored comparison and a
   recommendation for committee consideration - never "the winner" in the
   copy, see design decision 9. Nothing here approves anything.

5. **Approve each role.**
   ```bash
   python3 tools/capex.py approve <id> --role "Chief Engineer"
   python3 tools/capex.py approve <id> --role "General Manager"
   python3 tools/capex.py approve <id> --role "Owner rep"
   ```
   Any role may approve in any order. The chain is locked until step 4 has
   produced a recommendation - approving before that is refused with the
   same explanation the source spec uses: "nobody signs off on a quote
   comparison that has not been made."

6. **The letter of award.** The last role's approval drafts it automatically
   and queues it into the normal review flow:
   ```bash
   python3 tools/review.py show <id>
   python3 tools/review.py approve <id>
   python3 tools/review.py send
   ```
   Sending needs a real address for the winning vendor in
   `config/agent.yaml: vendor_emails` - blank by default, and a send is
   refused with the approval kept rather than guessing one.

   With `llm.provider: interactive`, the award-letter draft can park and
   exit 3 right after the last role's approval (`waiting for an answer to
   prompt award-letter-<id>` under `data/pending/`). This is resumable, not
   stuck: the last role's approval is cached on the draft item until the
   letter is actually written, so `python3 tools/capex.py approve <id>
   --role "<same role>"` - the exact same command - picks up where it left
   off once you answer the prompt. Do not re-approve a different role and do
   not re-run `draft-recommendation` to "unstick" it.

## What does not happen (spec's open question, still open)

Budget requests, RFP assembly, vendor qualification and bid collection are
not built - this sub-agent starts once quotes are already in hand. See
`specs/tender-capex-approval-ai.md` section 11 and `docs/sub-agents.md`.
