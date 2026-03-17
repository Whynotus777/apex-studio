# APEX Local Dev Setup

## Prerequisites

- Python 3.10+
- Node.js 18+
- `curl`

## Quick Start

```bash
make dev
```

What `make dev` does:

- validates Python and Node versions
- creates `.venv/` if needed
- installs backend dependencies from `api/requirements.txt`
- installs frontend dependencies in `ui/web/`
- creates `.env` from `.env.example` if missing
- ensures `db/apex_state.db` exists from `db/schema.sql`
- applies SQLite WAL mode
- seeds demo workspaces and sample data
- starts FastAPI on `http://localhost:8000`
- starts the Next.js app on `http://localhost:3000`

When both services are healthy, it prints:

```text
APEX is running at http://localhost:3000 — API at http://localhost:8000
```

## Make Targets

- `make dev`: full local stack, backend + frontend
- `make seed`: bootstrap the SQLite DB and insert demo data only
- `make api`: run only the FastAPI backend on port `8000`
- `make web`: run only the Next.js frontend on port `3000`
- `make test`: run `scripts/smoke_test.sh`
- `make clean`: stop tracked dev processes and remove `.venv/` plus `ui/web/node_modules/`

## Demo Mode

You can run the web app without Ollama or any external API keys.

`make dev` works in demo mode because:

- the seed step creates demo teams, tasks, reviews, and evidence
- the web UI can render seeded data without live agent execution
- the API will still start even if `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID` are blank

Recommended minimal `.env` values:

```dotenv
ANTHROPIC_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
OLLAMA_URL=http://localhost:11434
APEX_HOME=/absolute/path/to/apex-studio
NEXT_PUBLIC_API_URL=http://localhost:8000
```

If `.env.example` does not yet include every key the web stack needs, `make dev` appends sane defaults for `APEX_HOME` and `NEXT_PUBLIC_API_URL` into `.env` automatically.

## Health Check

Use:

```bash
bash scripts/check_health.sh
```

It verifies:

- `http://localhost:8000/docs`
- `http://localhost:3000`

## Troubleshooting

### Port 8000 already in use

`make dev` and `make api` stop early with a clear error instead of letting Uvicorn fail noisily. Free the port, then rerun.

### Port 3000 already in use

The web launcher performs the same preflight check for the frontend port.

### SQLite locked

APEX uses WAL mode during local development. If you still hit a lock:

- stop duplicate backend processes
- rerun `make clean`
- rerun `make seed` to confirm the DB opens cleanly

### Missing dependencies

If `python3`, `node`, `npm`, or `curl` are missing, the scripts print a direct error and stop before partial setup.

### `.env` missing

The scripts copy `.env.example` to `.env` automatically and warn you. Review the resulting file before enabling external integrations.

## Ctrl+C Behavior

`make dev` traps `Ctrl+C` and stops both the backend and frontend processes cleanly.
