# Sales Outreach Engine

A four-agent pipeline that finds ICP-matched prospects, enriches each company with recent signals, drafts personalized outreach, and verifies every claim before operator approval.

## What It Does

Replaces 15–20 hours/month of manual prospecting, company research, and outreach drafting. Every email that reaches your approval queue has been written against verified, real company details — no hallucinated facts, no generic templates.

## Pipeline

```
Scout → Analyst → Writer → Critic → [Operator Approval] → Send
```

| Stage | Agent | Input | Output |
|---|---|---|---|
| Discover | Scout | ICP definition from task | Qualified prospect list with signals |
| Enrich | Analyst | Prospect list | Company profiles with personalization hooks |
| Draft | Writer | Analyst enrichment | Personalized email + follow-up sequence |
| Review | Critic | Writer draft + enrichment | PASS / REVISE / BLOCK with specific feedback |

## Agents

### Scout — Lead Finder
Searches for companies matching the target ICP. Prioritizes companies with a recent buying signal (funding, leadership hire, product launch, job postings for key roles). Produces a prospect list with one recommended personalization angle per company.

**Tool grant:** `web_search` — Scout cannot operate without search access.

### Analyst — Company Enricher
Researches each prospect for the details that make an email feel genuinely personal: the specific funding round, the new VP hire, the product the company just launched. Rates enrichment confidence (HIGH / MEDIUM / LOW) so the Writer knows how specifically to reference each detail.

**Tool grant:** `web_search` — Analyst cannot operate without search access.

### Writer — Outreach Drafter
Writes a cold email (75–120 words) that leads with a specific company signal, connects it to a relevant value proposition, and closes with one frictionless CTA. Every specific claim is sourced back to the Analyst's enrichment. Also drafts a two-step follow-up sequence (day 3 value bump + day 7 breakup).

**No tool grant required.** Writer operates entirely from Analyst enrichment — it does not run independent searches.

### Critic — Outreach Reviewer
Verifies every specific claim in the email against the Analyst's sourced enrichment. Scores six dimensions: grounding, personalization quality, tone, CTA clarity, length, and accuracy risk. Issues PASS, REVISE, or BLOCK with specific actionable feedback. Fabricated claims trigger automatic BLOCK.

## Launching

```python
from kernel.api import ApexKernel

k = ApexKernel()
result = k.launch_template("sales-outreach")
print(result)
# → {'template_name': 'Sales Outreach Engine', 'workspace_id': 'ws-...', 'agents_created': [...]}
```

### Running the pipeline

```bash
# 1. Scout discovers prospects (pass ICP definition as task)
./kernel/spawn-agent.sh ws-<id>-scout <task_id>

# 2. Analyst enriches each prospect
./kernel/spawn-agent.sh ws-<id>-analyst <task_id>

# 3. Writer drafts outreach
./kernel/spawn-agent.sh ws-<id>-writer <task_id>

# 4. Critic reviews the draft
./kernel/trigger_critic.sh <review_id>
```

Or let Scout run on its heartbeat (weekday mornings at 8:00 AM) and trigger the downstream chain via inter-agent messages.

## Configuration

The ICP definition and sender context are passed as task descriptions. At minimum, include:

- Target industry and company size
- The problem you solve
- The role you are reaching out to (title/function)
- Any exclusions (competitors, existing customers, specific geographies)

Example task description:
```
Find 10–15 B2B SaaS companies (50–300 employees) in the HR tech or workforce management
space that have raised a Series A or B in the last 6 months. We help ops teams reduce
headcount overhead through automation. Target: VP Operations or COO. Exclude: companies
in the ATS space (we compete there).
```

## Quality Gates

- **Grounding is non-negotiable.** Critic will BLOCK any email containing a claim not sourced in the Analyst's enrichment.
- **All drafts are proposed.** Nothing is sent without explicit operator approval via the Telegram bot or approval queue.
- **Evidence verification** is applied by the Critic pipeline — the standard APEX grounding override (grounding_score < 0.5 → REVISE) applies.

## Tool Grants

`web_search` is granted automatically to Scout and Analyst when the template is launched via `launch_template()`. No manual setup required.

## Heartbeat

Scout runs on weekday mornings at 8:00 AM (`0 8 * * 1-5`). All other agents are on-demand, triggered by inter-agent messages or direct task assignment.
