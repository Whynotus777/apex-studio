# Daily Briefing Template

The simplest APEX template. One agent. One job.

**Daily Briefer (Scout)** wakes up every morning at 7 AM, searches for the top news on your configured topics, and delivers a 5-story digest under 500 words.

## What it does

- Runs automatically every morning at 7:00 AM
- Searches multiple queries across your topics
- Surfaces the top 5 stories with source links
- Prioritizes sources you've configured in preferences
- Delivers a clean, concise digest — no filler

## Setup

1. Launch the template:
   ```
   /launch daily-briefing
   ```

2. Give it a task with your topics:
   ```
   /task <workspace_id> Morning briefing on AI agents, startup funding, and crypto
   ```

3. (Optional) Add preferred sources:
   ```
   /preferences <workspace_id> add-source techcrunch.com
   /preferences <workspace_id> add-source bloomberg.com
   ```

The agent runs on its own heartbeat. You can also trigger it manually with `/spawn`.

## Why this exists

This template proves APEX handles simple 1-agent use cases, not just complex multi-agent teams. It's the "Hello World" of APEX — simple enough to understand immediately, useful enough to keep.

## Agents

| Agent | Role | Schedule |
|-------|------|----------|
| scout | Daily Briefer | 7:00 AM daily |

## Tool grants

- `web_search` — granted automatically at launch
