# Content Engine Template

A four-agent content pipeline for finding timely topics, drafting content, quality-gating output, and recommending when and where to publish.

## Agents

| Agent     | Role              | Heartbeat          |
|-----------|-------------------|--------------------|
| scout     | Trend Finder      | Every 6 hours      |
| writer    | Content Creator   | On-demand          |
| critic    | Quality Gate      | Continuous         |
| scheduler | Publishing Manager| Daily at 9:00 AM   |

## Pipeline

`discover` → `create` → `review` → `publish`

## What It Does

- `scout` monitors industry shifts, audience questions, and topical hooks.
- `writer` turns approved ideas into drafts for posts, articles, and captions.
- `critic` reviews every draft for tone consistency, audience fit, originality, and rule compliance.
- `scheduler` recommends timing, cadence, and channel-specific publishing plans.

## Safety Defaults

- Drafting is allowed, but external publishing requires approval.
- Social platform access is read-only by default.
- Writer instructions explicitly prohibit publishing without approval.
- Scheduler instructions limit the role to planning and recommendation, not execution.

## Usage

```python
from kernel.api import ApexKernel

k = ApexKernel()
result = k.launch_template("content-engine")
print(result["workspace_id"])
print(result["agents_created"])
```

## Default Budgets

- `api_calls`: 100 units/day (alert at 80%)
- `tool_cost`: $5/day (alert at 80%)

## Optional Integrations

- `web_search`
- `twitter`
- `linkedin`
