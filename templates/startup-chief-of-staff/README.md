# Startup Chief of Staff

`Startup Chief of Staff` packages the current five-agent APEX team into a reusable startup operations template.

## What It Does

This template includes:

- `apex`: orchestration, routing, memory ownership, morning rollups
- `scout`: discovery and signal detection
- `analyst`: market analysis and structured briefs
- `builder`: code, tests, and implementation support
- `critic`: quality review and approval gating

Default pipeline:

`discover -> analyze -> validate -> build -> launch -> grow`

The bundled workspace and agent configs are the live startup-focused configuration currently used by APEX.

## Included Files

- `template.json`: manifest for the template
- `agents/`: the five packaged agent definitions
- `workspace/`: shared operating context, user profile, and durable memory

## Heartbeats

Schedules are sourced from [`kernel/crontab`](/Users/abdulmanan/apex-studio/kernel/crontab:1):

- `apex`: `0 8 * * *`
- `scout`: `0 */4 * * *`
- `builder`: `0 23 * * *`
- `analyst`: on-demand only
- `critic`: queue-driven / continuous

## Launching It Now

The template manifest exists, but a first-class `launch_template("startup-chief-of-staff")` kernel entrypoint is still planned work.

With the current runtime, use the packaged template alongside the existing kernel and adapters:

```bash
export $(grep -v '^#' .env | xargs)
crontab kernel/crontab
python3 adapters/telegram/telegram_bot.py
```

Manual agent wakeups still use the kernel directly:

```bash
./kernel/spawn-agent.sh apex
./kernel/spawn-agent.sh scout
./kernel/spawn-agent.sh builder
```

## Integrations

- Required: none
- Optional: `web_search`, `github`, `telegram`

## Notes

- Agent payloads in `template.json` are copied from the current `agent.json` files in `agents/`.
- Heartbeat schedules are copied from the current `kernel/crontab`.
- External search is still disabled in Phase 1.5 unless that capability is added later.
