# APEX — Agent Operating System

## What This Is
APEX is an Agent Operating System — a platform where anyone can create, configure, and operate AI agents through a simple interface. This repo contains the kernel (runtime engine) and the first template (Startup Chief of Staff).

## Architecture Overview
The system has four layers:
1. **Kernel** (`/kernel/`) — agent lifecycle, task management, messaging, reviews, model routing, spawn protocol
2. **Templates** (`/templates/`) — packaged agent team configs for specific use cases
3. **Adapters** (`/adapters/`) — interface connectors (Telegram, Slack, web, etc.)
4. **UI** (`/ui/`) — product frontend (onboarding wizard, dashboard, approval queue)

Currently everything lives flat in the repo from the initial build. The next refactor will organize into this structure.

## Current Repo Structure
```
~/apex-studio/
├── agents/           # Agent configs (5 agents: apex, scout, analyst, builder, critic)
│   └── <agent>/
│       ├── agent.json          # Model routing, heartbeat, capabilities
│       ├── AGENTS.md           # Identity and job description
│       ├── constraints/        # hard-rules.md, soft-preferences.md, anti-patterns.md
│       └── workspace/          # scratchpad.md
├── db/
│   ├── apex_state.db           # SQLite: 8 tables (goals, projects, tasks, agent_messages, agent_status, agent_sessions, reviews, evals)
│   ├── schema.sql              # Table definitions with indexes
│   └── seed.sql                # Initial goals, projects, agent status
├── services/
│   ├── spawn-agent.sh          # Core spawn protocol (context injection → model call → parse → persist)
│   ├── call_model.py           # Ollama and Claude API caller (reads prompts from temp files)
│   ├── parse_response.py       # JSON parser with text fallback, message allowlist, status normalization
│   ├── run_critic.py           # Critic pipeline (reads review queue, scores, verdicts)
│   ├── telegram_bot.py         # Bidirectional Telegram interface
│   ├── send_telegram.py        # Outbound message CLI
│   ├── heartbeat.sh            # Cron wrapper for scheduled agent wakeups
│   ├── trigger_critic.sh       # CLI wrapper for running Critic
│   ├── crontab                 # Heartbeat schedule (installed via `crontab services/crontab`)
│   ├── test_spawn.sh           # Spawn protocol test
│   ├── test_parser.sh          # Parser unit tests
│   ├── test_json_output.sh     # JSON output integration test
│   ├── test_grounding.sh       # Scout + Analyst grounding tests
│   └── test_critic.sh          # Full Critic pipeline test
├── workspace/
│   ├── AGENTS.md               # System-wide operating rules
│   ├── SOUL.md                 # Personality and voice
│   ├── USER.md                 # Abdul's operator profile
│   ├── MEMORY.md               # Shared long-term memory (Apex writes)
│   └── HEARTBEAT.md            # Schedule reference
├── skills/                     # Shared skill library (empty)
├── .env                        # API keys and config (not committed)
├── .env.example                # Template for .env
└── .gitignore
```

## Key Technical Decisions

### Models
- **Local**: `qwen3.5-apex` — custom Ollama model with thinking disabled (`--think=false`). Based on `qwen3.5:4b`.
- **API**: Claude Opus for Apex orchestrator and Critic deep reviews. Claude Sonnet as fallback. Currently no API key set — everything falls back to local.
- **All Ollama API calls MUST include `"think": false`** — without this, Qwen 3.5 outputs verbose chain-of-thought that wastes tokens and time.

### Spawn Protocol
Every agent wakeup goes through `spawn-agent.sh`:
1. Update agent status to active
2. Build system prompt (agent identity + hard rules + JSON response schema)
3. Build user prompt (inbox + task context)
4. Write prompts to temp files
5. Call `call_model.py` with model name + temp file paths
6. Parse response through `parse_response.py` (tries JSON first, falls back to text)
7. Process: save session, append scratchpad, send messages, update task status
8. Update agent status to idle

### Response Format
Agents respond in JSON:
```json
{
  "actions_taken": "what was actually done",
  "observations": "what was noticed",
  "proposed_output": "deliverable (labeled as proposed if not executed)",
  "messages": [{"to": "agent_name", "type": "request", "content": "..."}],
  "scratchpad_update": "key facts to remember",
  "status": "done|blocked:reason|needs_review:low|medium|high"
}
```

### Grounding Rules
All agents have anti-hallucination rules:
- Only claim actions actually performed in the session
- Never invent URLs, statistics, or data
- Scout and Analyst explicitly know they have NO search tools in Phase 1.5
- If data is unavailable, agents say so instead of fabricating

### Message Allowlist
Valid message targets: `apex`, `scout`, `analyst`, `builder`, `critic`. Messages to invalid targets are rerouted to `apex` as escalations.

### Critic Pipeline
`run_critic.py` processes the review queue:
- Reads pending reviews from DB
- Calls Critic model with a 6-dimension rubric (accuracy, completeness, actionability, conciseness, hard rule compliance, grounding)
- Returns PASS (≥3.5, no violations) / REVISE (feedback sent to agent) / BLOCK (escalated to apex)
- Logs eval scores per dimension for trend tracking

## Database Schema
8 tables in `db/apex_state.db`:
- `goals` — top-level objectives
- `projects` — under goals, with pipeline stage
- `tasks` — atomic work units with checkout locking
- `agent_messages` — inter-agent communication bus
- `agent_status` — current state per agent
- `agent_sessions` — persisted execution context
- `reviews` — Critic review queue
- `evals` — per-dimension quality scores

## Running Tests
```bash
# Source environment first
export $(grep -v '^#' .env | xargs)

# Parser unit tests (instant)
./services/test_parser.sh

# Spawn test with JSON output (~1-2 min on CPU)
./services/test_json_output.sh

# Grounding tests for Scout + Analyst (~3-4 min)
./services/test_grounding.sh

# Full Critic pipeline test (~3-4 min)
./services/test_critic.sh
```

## Running the Telegram Bot
```bash
export $(grep -v '^#' .env | xargs)
python3 services/telegram_bot.py
```
Commands: `/start`, `/status`, `/goals`, `/tasks`, `/rollup`, `/spawn <agent>`

## Environment Variables
```
ANTHROPIC_API_KEY=    # Optional — system falls back to local model
TELEGRAM_BOT_TOKEN=   # From @BotFather
TELEGRAM_CHAT_ID=     # Your numeric Telegram ID
OLLAMA_URL=http://localhost:11434
APEX_HOME=/Users/abdulmanan/apex-studio
```

## Hardware
- **Dev**: MacBook Pro 2019, Intel i9, 16GB RAM, CPU-only inference
- **Prod** (upcoming): RTX 5090 desktop for Qwen 3.5 27B + Perplexica

## Do Not Change (Guardrails for Coding Agents)
These must remain stable during all refactoring and new development:
- **All existing tests must keep passing** — run test_parser.sh, test_json_output.sh, test_grounding.sh, test_critic.sh after every change
- **Preserve Telegram bot behavior** — commands, free-text routing, and agent spawning must work identically
- **Preserve JSON response parsing + text fallback** — parse_response.py behavior is stable
- **Preserve grounding rules** — all anti-hallucination constraints in hard-rules.md files must survive any file moves
- **SQLite remains the source of truth** — do not introduce a new database or ORM in this phase
- **Do not build the dashboard before kernel boundary work is complete** — Phase A and B before Phase D
- **Do not re-implement existing working functionality from scratch** — wrap it, don't rewrite it
- **All Ollama API calls must include `"think": false`** — this is non-negotiable

## What Needs Building Next (Priority Order)

### Phase A: Kernel Boundary (DO THIS FIRST)
1. **Repo restructure** — move files into this target layout:
   ```
   /kernel/          — all runtime primitives (spawn, parse, model call, critic, message bus)
   /kernel/api.py    — Python class with kernel API methods
   /kernel/db.py     — database operations
   /kernel/models.py — model routing and calling
   /templates/       — packaged agent team configs
   /templates/startup-chief-of-staff/  — first template (extracted from current agents/)
   /adapters/        — interface connectors
   /adapters/telegram/  — current telegram_bot.py + send_telegram.py
   /ui/              — product frontend (stub for now)
   /tests/           — all test scripts
   /docs/            — architecture docs
   ```
   All existing functionality must keep working after the move. Run all tests to verify.

   **Acceptance criteria for Phase A:**
   - All 4 test scripts pass after the restructure (parser, json output, grounding, critic)
   - Telegram bot starts and responds to /status, /goals, /tasks, /spawn
   - `spawn-agent.sh` still works from its new location with correct path resolution
   - No behavior regressions in spawn, parse, critic, or messaging flows
   - `kernel/api.py` wraps existing shell scripts and Python modules — it does NOT rewrite them from scratch
   - Agent configs in `/templates/startup-chief-of-staff/` are identical to current `agents/` content
   - `.env` and `db/apex_state.db` paths still resolve correctly

2. **Kernel API class** (`/kernel/api.py`) — Python class wrapping current shell scripts and DB operations:
   - `create_agent(config: dict) -> str` — create agent from config, return agent_id
   - `pause_agent(agent_id: str)` / `resume_agent(agent_id: str)`
   - `get_agent_status(agent_id: str) -> dict`
   - `create_task(task: dict) -> str` — create task with goal ancestry
   - `assign_task(task_id: str, agent_id: str)` — with atomic checkout
   - `spawn_agent(agent_id: str, task_id: str = None) -> dict` — full spawn cycle, return parsed response
   - `submit_for_review(task_id: str, stakes: str)` — queue for Critic
   - `run_critic_pipeline() -> list` — process all pending reviews
   - `get_approval_queue() -> list` — items waiting for human decision
   - `approve_action(review_id: int)` / `reject_action(review_id: int, feedback: str)`
   - `send_message(from_agent: str, to_agent: str, content: str, msg_type: str)`
   - `route_user_message(text: str) -> dict` — route through Apex
   - `get_eval_history(agent_id: str) -> list` — quality scores over time
   This class is the stable boundary between kernel and everything else (UI, adapters, templates).

### Phase B: Missing Primitives
3. **Tool primitive** — a registry table + Python interface:
   - Schema: `tools(id, name, adapter, auth_method, scopes, read_write, cost_per_call, approval_required)`
   - `register_tool(config)`, `grant_tool_access(agent_id, tool_id, permission_level)`, `invoke_tool(agent_id, tool_id, params)`
   - Permission levels: read_only, draft, write_with_approval, full_write

4. **Permission primitive** — explicit per-agent access controls:
   - Schema: `permissions(id, agent_id, tool_id, level, max_spend_per_day, requires_approval, created_at)`
   - Replaces implicit hard-rules.md for tool access
   - Checked at runtime before any tool invocation

5. **Budget tracking** — token and dollar tracking:
   - Schema: `budgets(id, agent_id, budget_type, limit_amount, spent_amount, period, alert_threshold)`
   - Track per-agent: API tokens used, API dollars spent, tool invocation costs
   - Alert when approaching limit, hard-stop when exceeded

6. **Memory abstraction** — define interfaces, keep SQLite backing for now:
   - `session_memory` — current conversation context (already exists as agent_sessions)
   - `working_memory` — scratchpad facts for current task (already exists as scratchpad.md)
   - `durable_memory` — long-term facts that persist across sessions (already exists as MEMORY.md)
   - Define read/write methods that agents call. Later swap backing store without changing agent code.

### Phase C: Template System
7. **Extract "Startup Chief of Staff" template** — package current agents/ into a launchable config:
   - `template.json` with metadata, agent list, default permissions, heartbeat schedule
   - Must be launchable via `kernel.launch_template("startup-chief-of-staff")` — no manual file creation
   - All 5 current agents become part of this template

8. **Create "Research Assistant" template** — prove generality:
   - 3 agents: Scout, Analyst, Critic
   - Different rules, different heartbeat schedule
   - Must launch from config alone with zero code changes to the kernel

### Phase D: Product UI (AFTER kernel is stable)
9. **Dashboard MVP** — approval queue, activity feed, agent status, stats
10. **Onboarding wizard** — 6-step flow from the product architecture doc

## Code Style
- Shell scripts: `set -e`, functions for reuse, `log()` for consistent output
- Python: stdlib preferred (no heavy frameworks), type hints encouraged
- SQL: parameterized queries when possible, `INSERT OR IGNORE` for idempotency
- All agent-facing prompts should request JSON output
- All model calls must include `"think": false` for Ollama
