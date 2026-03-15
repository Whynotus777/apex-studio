# APEX — Product Architecture v1.0

**Abdul Manan · March 2026 · Status: Architecture Ready**

---

## 1. Vision

APEX is an Agent Operating System — a platform where anyone can create, configure, and operate AI agents through a simple interface. Users describe what they want automated in plain English, and the platform builds the right agent team, wires the tools, manages the runtime, and keeps everything safe.

**Core promise:** "Tell us what you want automated. We'll create the right agent team, wire the tools, manage the runtime, and keep it safe."

- **Target Users:** SMBs, solo founders, and agencies (launch wedge). Enterprise and developers as expansion.
- **Deployment:** Hybrid — hosted by default, self-hostable for power users.
- **Revenue:** Freemium — free tier with limits, paid for more agents and features.

---

## 2. Four-Layer Architecture

The system separates into four distinct layers. Each layer can evolve independently.

| Layer | Responsibility | Examples |
|-------|---------------|----------|
| 1. Agent OS Kernel | Lifecycle, safety, routing, evals, permissions, budgets, memory, approvals | spawn protocol, message bus, critic pipeline, task checkout, model routing |
| 2. Template Library | Reusable agent teams for specific use cases | "LinkedIn Manager", "Startup Chief of Staff", "Research Assistant" |
| 3. Interface Adapters | How users interact with agents | Telegram, Slack, Discord, Email, Web Dashboard, SMS |
| 4. Product UI | The consumer-facing experience | Onboarding wizard, dashboard, approval queue, agent editor, billing |

**Key principle:** If it's about lifecycle, safety, routing, evals, permissions, budgets, memory, or approvals → it belongs in the Kernel. If it's about a specific use case → it's a Template.

---

## 3. Kernel Primitives

These are the core objects the kernel manages. Every feature in the platform is built on top of these primitives.

| Primitive | Description | Current Status |
|-----------|-------------|----------------|
| Agent | A configured AI worker with identity, rules, model routing, and capabilities | ✅ Built (agent.json + AGENTS.md + constraints) |
| Task | An atomic work unit with goal ancestry, checkout locking, and status lifecycle | ✅ Built (SQLite with indexes) |
| Message | Inter-agent communication with allowlisted targets and priority | ✅ Built (SQLite bus) |
| Review | Quality gate entry with stakes classification and scoring rubric | ✅ Built (Critic pipeline, 6-dimension scoring) |
| Eval | Per-dimension quality score for trend tracking across sessions | ✅ Built (evals table) |
| Session | Persisted agent execution context for resumability | ✅ Built (agent_sessions table) |
| Tool | A permission-scoped external capability an agent can invoke | ❌ Not built |
| Permission | Explicit per-agent access control for tools and actions | ❌ Not built |
| Budget | Token and dollar tracking per agent with limits and alerts | ❌ Not built |
| Memory | Hierarchical agent memory: session, working, durable | ⚠️ Partial (scratchpads + MEMORY.md) |
| Template | A packaged agent team config for a specific use case | ❌ Not built |
| Workspace | A tenant container holding agents, tasks, memory, and config | ⚠️ Partial — single-tenant only |

---

## 4. Kernel API Contract

These are the function signatures the Product UI and Interface Adapters will call.

### Agent Lifecycle
- `create_agent(config)` — Create a new agent from a config object
- `pause_agent(agent_id)` — Pause an agent's heartbeat and task processing
- `resume_agent(agent_id)` — Resume a paused agent
- `delete_agent(agent_id)` — Remove an agent and archive its history
- `get_agent_status(agent_id)` — Get current status, model, last active, task count
- `update_agent_config(agent_id, config)` — Update agent rules, model, or permissions

### Task Management
- `create_task(task)` — Create a task with goal ancestry and pipeline stage
- `assign_task(task_id, agent_id)` — Assign a task to an agent with atomic checkout
- `complete_task(task_id, output)` — Mark task done and release checkout
- `block_task(task_id, reason)` — Mark task blocked with reason
- `get_task_queue(filters)` — Get tasks by status, agent, project, or stage

### Review & Approval
- `submit_for_review(task_id, stakes)` — Queue a task output for Critic review
- `run_critic_pipeline()` — Process all pending reviews
- `get_approval_queue()` — Get items waiting for human approval
- `approve_action(review_id)` — Human approves a pending item
- `reject_action(review_id, feedback)` — Human rejects with feedback
- `get_eval_history(agent_id)` — Get quality scores over time for an agent

### Communication
- `send_message(from_agent, to_agent, content, msg_type)` — Send inter-agent message with allowlist validation
- `get_inbox(agent_id)` — Get pending messages for an agent
- `route_user_message(text)` — Route a human message through Apex to the right agent(s)
- `send_to_user(text, channel, buttons)` — Send outbound message via chosen channel

### Model & Spawn
- `spawn_agent(agent_id, task_id)` — Full spawn: inject context, call model, parse, persist
- `route_model(agent_id, stakes)` — Select the right model based on agent config and stakes
- `call_model(model, system_prompt, user_prompt, temperature)` — Make an LLM call

---

## 5. Template System

A Template is a packaged agent team for a specific use case.

### Template Structure
- `template.json` — metadata: name, description, category, required integrations
- `agents[]` — array of agent configs with name, role, rules, model preferences, capabilities
- `pipeline` — default task flow stages
- `permissions` — default guardrail settings
- `heartbeats` — default schedule for each agent
- `integrations[]` — required and optional service connections

### Launch Templates (Priority Order)
1. **Startup Chief of Staff** — Scout, Analyst, Builder, Operator, Critic (Abdul's use case — proves full pipeline)
2. **Research Assistant** — Scout, Analyst, Critic (minimal agents, high value, no browser automation)
3. **Content Engine** — Scout, Writer, Critic, Scheduler (high demand, visible output)
4. **Sales Ops** — Scout, Writer, Operator, Critic (SMB wedge — immediate revenue impact)

---

## 6. Onboarding Flow

The user never writes prompts. They configure roles, permissions, and goals.

1. **What do you want to automate?** — 10 categories or plain English custom description
2. **Meet your agent team** — suggested agents with toggles, Critic always recommended
3. **Connect your tools** — integration selection, read-only by default
4. **Set guardrails and limits** — autonomy, communication, data access sliders + spending budget
5. **Choose your channel** — Telegram, Slack, Email, Dashboard, Discord, SMS
6. **Review and launch** — summary with edit links, then deploy

---

## 7. Dashboard

Answers three questions: What are my agents doing? What needs my attention? Is everything working?

- **Approval Queue** (most prominent) — cards with Critic scores, stakes badges, Approve/Edit/Reject
- **Activity Feed** — chronological timeline of agent actions, color-coded by type
- **Agent Status Panel** — roster with status dots, model info, quality avg, Wake/Pause controls
- **Stats Bar** — tasks today, reviews, pass rate, active agents, cost

---

## 8. Key Product Decisions

- **Single-agent UX, multi-agent backend** — user sees "LinkedIn Manager," system runs Scout + Writer + Critic + Scheduler
- **Roles + Permissions + Goals, not prompts** — if a user has to write a system prompt, we've lost the mainstream market
- **Critic/Evaluator as kernel primitive** — not "just another agent" but infrastructure: pluggable evals, rubric reviews, policy enforcement, human review hooks
- **Approval Queue as core product surface** — where trust lives. What makes agents scary is unauthorized action. The queue solves this.

---

## 9. Competitive Landscape

| Player | What They Do | Why APEX Is Different |
|--------|-------------|----------------------|
| CrewAI / AutoGen / LangGraph | Developer frameworks | APEX is the hosted runtime — agents stay alive, grounded, accountable |
| Sam Woods AgentOS 2026 | Consumer workspace ($49/mo) | APEX has real quality gate (Critic) + grounding rules |
| PwC Agent OS | Enterprise orchestration | APEX targets SMBs with 10x simpler UX |
| OpenClaw / OpenFang | Open-source frameworks | APEX is the product layer — config-driven, not code-driven |

**APEX's moat:** Critic pipeline + grounding rules + approval queue. These make agents trustworthy, not just impressive.

---

## 10. Roadmap

### Phase 1: Prove the Kernel (Current)
- Kernel/template audit and repo restructure
- Define kernel API as Python class
- Add Tool, Permission, Budget primitives
- Abstract Memory interface

### Phase 2: Prove One Template End-to-End
- Extract "Startup Chief of Staff" as first template
- Prove "Research Assistant" launches from config alone
- Set up Perplexica for real search
- Move to RTX 5090 for Qwen 3.5 27B

### Phase 3: Build the Product UI
- Onboarding wizard
- Dashboard MVP
- Web-based approval queue
- Multi-tenancy

### Phase 4: Launch
- Template marketplace
- Integration layer (OAuth, webhooks)
- Billing (Stripe)
- Public beta with SMB wedge
