# APEX — Agent Operating System

## What This Is
APEX is an Agent Operating System — a platform where anyone can create, configure, and operate AI agents through a simple interface. This repo contains the kernel (runtime engine), two launchable templates, and a Telegram adapter.

## Architecture Overview
The system has four layers:
1. **Kernel** (`/kernel/`) — agent lifecycle, task management, messaging, reviews, model routing, spawn protocol, evidence storage, memory, tool adapters
2. **Templates** (`/templates/`) — packaged agent team configs for specific use cases
3. **Adapters** (`/adapters/`) — interface connectors (Telegram currently, Slack/web planned)
4. **UI** (`/ui/`) — product frontend (stub — thin dashboard planned after workflow and trust-loop polish)

## Current Repo Structure
```
~/apex-studio/
├── kernel/
│   ├── api.py                  # ApexKernel class — stable API boundary for all runtime operations
│   ├── __init__.py             # Exports ApexKernel
│   ├── spawn-agent.sh          # Core spawn protocol (context injection → search → model call → parse → persist)
│   ├── call_model.py           # Ollama and Claude API caller (reads prompts from temp files)
│   ├── parse_response.py       # JSON parser with text fallback, message allowlist, status normalization
│   ├── run_critic.py           # Critic pipeline (reads review queue, scores, verdicts, evidence verification)
│   ├── evidence.py             # EvidenceStore — stores and retrieves search evidence per task
│   ├── critic_evidence.py      # Citation verification — checks agent URLs against stored evidence
│   ├── memory.py               # SessionMemory, WorkingMemory, DurableMemory abstractions
│   ├── memory_loader.py        # CLI helper for spawn-agent.sh to load/save memory
│   ├── tool_adapter.py         # Tool registry — maps tool names to adapter functions
│   ├── heartbeat.sh            # Cron wrapper for scheduled agent wakeups
│   ├── crontab                 # Heartbeat schedule
│   └── trigger_critic.sh       # CLI wrapper for running Critic
├── templates/
│   ├── startup-chief-of-staff/
│   │   ├── template.json       # 5-agent manifest with pipeline, permissions, heartbeats
│   │   ├── README.md
│   │   ├── agents/             # apex, scout, analyst, builder, critic
│   │   │   └── <agent>/
│   │   │       ├── agent.json
│   │   │       ├── AGENTS.md
│   │   │       ├── constraints/ (hard-rules.md, soft-preferences.md, anti-patterns.md)
│   │   │       └── workspace/scratchpad.md
│   │   └── workspace/
│   │       ├── AGENTS.md, SOUL.md, USER.md, MEMORY.md, HEARTBEAT.md
│   │       └── memory/
│   └── research-assistant/
│       ├── template.json       # 3-agent manifest (scout, analyst, critic)
│       ├── README.md
│       └── agents/             # scout, analyst, critic (research-focused rules)
├── adapters/
│   └── telegram/
│       ├── telegram_bot.py     # /start /status /goals /tasks /templates /launch /task /workspaces /agents /approvals /spawn
│       └── send_telegram.py    # Outbound message CLI
├── tests/
│   ├── test_parser.sh          # Parser unit tests (4 cases)
│   ├── test_json_output.sh     # JSON output integration test + live agent
│   ├── test_grounding.sh       # Scout + Analyst grounding verification
│   ├── test_critic.sh          # Full Critic pipeline test
│   ├── test_primitives.sh      # Tool, Permission, Budget primitives (28 tests)
│   ├── test_launch_template.sh # Template launch + workspace scoping (22 tests)
│   ├── test_memory.sh          # Memory abstraction tests
│   ├── test_web_search.sh      # Live DuckDuckGo search test
│   ├── test_evidence.sh        # Evidence store tests
│   ├── test_critic_evidence.sh # Citation verification unit tests
│   └── test_critic_evidence_integration.sh  # Critic + evidence override integration
├── db/
│   ├── apex_state.db           # SQLite: 15 tables
│   ├── schema.sql              # Full schema with indexes
│   ├── seed.sql                # Initial goals, projects, agent status
│   └── seed_tools.sql          # Web search tool registration
├── docs/                       # Architecture docs
├── ui/                         # Stub — dashboard MVP planned
├── .env                        # API keys and config (not committed)
├── .env.example                # Template for .env
├── CLAUDE.md                   # This file
├── ARCHITECTURE.md             # Product architecture document
└── .gitignore
```

## Key Technical Decisions

### Models
- **Local**: `qwen3.5-apex` — custom Ollama model with thinking disabled. Based on `qwen3.5:4b`.
- **API**: Claude Opus for Apex orchestrator and Critic deep reviews. Claude Sonnet as fallback. Currently no API key set — everything falls back to local.
- **All Ollama API calls MUST include `"think": false`** — without this, Qwen 3.5 outputs verbose chain-of-thought.

### Spawn Protocol
Every agent wakeup goes through `kernel/spawn-agent.sh`:
1. Resolve agent config path (supports both global and workspace-scoped agents via meta.config_path)
2. Update agent status to active
3. Build system prompt (agent identity + hard rules + JSON response schema + search grounding rule)
4. Build user prompt (inbox + task context)
5. If agent has web_search tool grant: generate 3 focused queries via Qwen, run all through DuckDuckGo, deduplicate by URL, store via EvidenceStore, inject as `## Search Evidence`
6. Write prompts to temp files
7. Call `call_model.py` with model name + temp file paths
8. Parse response through `parse_response.py` (tries JSON first, falls back to text)
9. Process: save session via memory_loader.py, send messages, update task status
10. Update agent status to idle

### Response Format
Agents respond in JSON:
```json
{
  "actions_taken": "what was actually done",
  "observations": "what was noticed",
  "proposed_output": "deliverable (labeled as proposed if not executed)",
  "messages": [{"to": "agent_name", "type": "request", "content": "..."}],
  "scratchpad_update": "key facts to remember",
  "status": "done|blocked:reason|needs_review:low|needs_review:medium|needs_review:high"
}
```

### Workspace Scoping
- Templates launch into isolated workspaces: `kernel.launch_template("research-assistant")` creates workspace `ws-abc123`
- Agents are namespaced: `ws-abc123-scout`, `ws-abc123-analyst`, `ws-abc123-critic`
- All DB tables include `workspace_id` column for filtering
- Zero collisions when multiple templates share role names
- Backward compatible: `workspace_id="global"` preserves legacy behavior
- `_resolve_agent_config_path()` handles workspace-scoped agents by reading meta.config_path

### Grounding Rules
- Only claim actions actually performed in the session
- Never invent URLs, statistics, or data
- If Search Evidence is present, cite only from provided evidence
- If Search Evidence is absent or empty, do not invent sources — state what evidence you lack

### Critic Pipeline + Evidence Verification
- 6-dimension rubric scoring (accuracy, completeness, actionability, conciseness, hard rule compliance, grounding)
- PASS / REVISE / BLOCK verdicts
- **Automated evidence verification**: after Critic scores, `verify_agent_output()` checks all cited URLs against stored evidence
- **Trust override**: if grounding_score < 0.5 and Critic said PASS, automatically overrides to REVISE
- Evidence verification results stored in review feedback JSON
- Per-dimension eval logging including `evidence_grounding` dimension

### Search Integration
- DuckDuckGo HTML search via `adapters/tools/web_search.py` (no API key needed)
- Multi-query: spawn script generates 3 focused queries via Qwen, runs all, deduplicates by URL (~12 results vs 5 with single query)
- Evidence stored in `evidence` table via `kernel/evidence.py`
- Template-level auto-grants: `launch_template()` automatically grants web_search to scout and analyst when listed in template integrations

### Template System
- `launch_template(template_id, overrides)` → creates workspace, registers agents, applies permissions, budgets, and tool grants
- Templates may define default permissions, budgets, and tool grants that are applied automatically at launch
- Self-contained packages in `/templates/<id>/`
- `list_templates()`, `get_template(id)`, `launch_template(id, overrides)`

## Database Schema
15 tables in `db/apex_state.db`:
- `goals`, `projects` — goal/project hierarchy
- `tasks` — atomic work units with checkout locking + workspace_id
- `agent_messages` — inter-agent communication bus + workspace_id
- `agent_status` — current state per agent + workspace_id
- `agent_sessions` — persisted execution context
- `reviews` — Critic review queue + workspace_id
- `evals` — per-dimension quality scores + workspace_id
- `tools` — tool registry
- `tool_grants` — per-agent tool access + workspace_id
- `permissions` — per-agent access controls + workspace_id
- `budgets` — per-agent budget envelopes + workspace_id
- `spend_log` — immutable spend audit trail
- `evidence` — stored search results for citation verification
- `workspaces` — workspace registry

## Running the Telegram Bot
```bash
cd ~/apex-studio
export $(grep -v '^#' .env | xargs)
PYTHONPATH=. python3 adapters/telegram/telegram_bot.py
```

## Environment Variables
```
ANTHROPIC_API_KEY=    # Optional — falls back to local model
TELEGRAM_BOT_TOKEN=   # From @BotFather
TELEGRAM_CHAT_ID=     # Your numeric Telegram ID
OLLAMA_URL=http://localhost:11434
APEX_HOME=/Users/abdulmanan/apex-studio
```

## Hardware
- **Dev**: MacBook Pro 2019, Intel i9, 16GB RAM, CPU-only inference
- **Prod** (upcoming): RTX 5090 desktop for Qwen 3.5 27B + Perplexica

## Do Not Change (Guardrails for Coding Agents)
- **All existing tests must keep passing** — run all test scripts after every change
- **Preserve Telegram bot behavior** — all commands must work identically
- **Preserve JSON response parsing + text fallback** — parse_response.py is stable
- **Preserve grounding rules** — all anti-hallucination constraints must survive
- **SQLite remains the source of truth** — no new databases in this phase
- **Do not rewrite working code from scratch** — wrap it, don't rewrite it
- **All Ollama API calls must include `"think": false`**
- **Evidence verification override in Critic must not be disabled** — grounding_score < 0.5 overrides PASS to REVISE
- **Template tool grants must remain automatic** — launch_template() handles web_search grants

## Known Rough Edges
- Telegram is currently the only operator interface
- DuckDuckGo HTML search is a temporary backend — brittle parsing, sometimes blocked
- Some kernel methods still carry legacy/global compatibility behavior
- Templates define default permissions, budgets, and tool grants applied at launch — but some edge cases may remain
- Dev hardware (Intel Mac, CPU-only) limits inference speed and model size

## What Needs Building Next (Priority Order)

### Immediate (optimized for Intel Mac — no heavy infra)
1. **Telegram workflow polish** — cleaner task status messages, better task routing (multi-agent chain), workspace summaries, evidence visibility in responses, approval UX polish, better error messages. Make the phone-based loop feel excellent.
2. **Template launch polish** — make launch fully declarative: default tool grants, permissions, budgets, heartbeats, search roles all from config. Goal: `launch_template()` requires zero follow-up setup.
3. **Trust loop + evidence UX** — evidence-backed Critic enforcement is built but not visible to operator. Add `/evidence <task_id>` Telegram command, evidence preview in approval cards, clearer blocked/insufficient-evidence statuses.
4. **More templates** — Content Engine (scout + writer + critic + scheduler), Sales Ops (scout + writer + operator + critic). Proves generality beyond research. Low hardware dependence.
5. **Thin dashboard MVP** — template launcher, workspaces list, agent status, approvals, recent tasks. Call kernel/api.py — no direct DB access. Keep it minimal.

### Phase 3: Product Readiness (after 5090 arrives)
6. **Perplexica integration** — Replace DuckDuckGo with Perplexica for much better search quality. Docker setup on 5090. Point APEX at remote Ollama + Perplexica backend.
7. **Anthropic API key** — Opus for Apex routing and Critic high-stakes reviews. Qwen for everything else.
8. **Stronger local models** — Qwen 3.5 27B on 5090 for dramatically better agent output quality.
9. Multi-tenancy — user accounts, isolated workspaces per user, auth
10. Web onboarding wizard — 6-step flow from ARCHITECTURE.md

### Phase 4: Platform & Ecosystem
11. **Skills ecosystem compatibility** — Make templates exportable as skill packages for skills.sh CLI. Agent constraint files should be SKILL.md compatible for interoperability with Claude Code, Cursor, Codex.
12. **Skill graphs** — Interconnected knowledge graphs using wikilinks and progressive disclosure for deep vertical templates. Each domain (PE research, legal, trading) gets a traversable graph of specialized knowledge that agents load on demand.
13. Template marketplace — community templates with ratings
14. Billing — Stripe, usage-based pricing, budget enforcement per user

## Code Style
- Shell scripts: `set -e`, functions for reuse, `log()` for consistent output
- Python: stdlib preferred, type hints encouraged
- SQL: parameterized queries, `INSERT OR IGNORE` for idempotency
- All agent prompts request JSON output
- All Ollama calls include `"think": false`
- New files for parallel work — modify existing files only in sequential tasks
- Run all tests after every change
