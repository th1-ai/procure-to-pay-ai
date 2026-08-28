# knowledge/

This folder is the agent's memory of your property. It reads these files before
it answers anything, so the quality of what is in here is the quality of what
goes out.

## What to put here

| File | What it holds |
|---|---|
| `property.md` | The facts. Name, currency, contact details - used in every drafted email's context. |
| `faq.md` | Kept for consistency with the rest of this family; not read by this agent's own prompts. |
| `signature.md` | The sign-off on outgoing email, and the AI-disclosure line. Plain text. |
| `procurement-policy.md` | A plain-language mirror of `config/agent.yaml`'s budgets, delegation matrix and matching tolerances - for a person to read; the agent runs on the config file, not this. |

Copy the `.example.md` files, rename them without `.example`, and fill them in:

```bash
cp knowledge/property.example.md         knowledge/property.md
cp knowledge/faq.example.md              knowledge/faq.md
cp knowledge/procurement-policy.example.md knowledge/procurement-policy.md
```

`knowledge/*.md` is gitignored (the `.example.md` files are not), because your
property notes are yours.

## How to write it

**Write it the way you would brief a new person in accounts payable.** Short
sentences, concrete facts, no marketing language. The agent will quote this
material to vendors and approvers, so anything vague here becomes something
vague in an email.

**Be specific about numbers and times.** "Check-in from 15:00" is usable.
"Check-in in the afternoon" is not.

**Say what you do NOT do.** "We have no parking; the nearest car park is X, about
EUR 15 a day" prevents a wrong answer far better than silence does.

**Keep prices dated.** "Breakfast EUR 18 per person (2026 rates)" tells the agent
and you when it is stale.

**One fact per line where you can.** It makes the agent's job easier and it makes
your job easier when something changes.

## Keeping it current

The agent is only as right as this folder. When a policy changes, change it here
first. A good habit: whenever you correct one of the agent's drafts in the review
queue, ask whether the correction belongs in `property.md`. If it does, the agent
stops making that mistake.

You can also ask your Claude Code session to do it:

> Read knowledge/property.md and the last ten items in the review queue. If any
> of my edits contradict what is in the file, tell me which line to change.
