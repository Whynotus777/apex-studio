# GTM Engine Template

A five-agent go-to-market operations team for research, positioning, messaging, content production, quality review, and approval-gated distribution planning.

## Agents

| Agent      | Role                    | Heartbeat         |
|------------|-------------------------|-------------------|
| scout      | Market Researcher       | Every 6 hours     |
| strategist | Positioning & Messaging | On-demand         |
| writer     | Content Creator         | On-demand         |
| critic     | Quality Gate            | Continuous        |
| publisher  | Distribution Manager    | Daily at 9:00 AM  |

## Pipeline

`discover` → `strategize` → `create` → `review` → `publish`

## What It Does

- `scout` monitors competitors, tracks market shifts, and gathers evidence-backed research.
- `strategist` turns research into positioning, messaging pillars, and campaign angles.
- `writer` drafts GTM assets using the user's brand voice and channel context.
- `critic` reviews all output for claim accuracy, grounding, and brand consistency.
- `publisher` recommends timing, channels, and distribution sequencing without taking external action by default.

## Safety Defaults

- External publishing requires approval.
- Publishing systems are read-only by default.
- Writer instructions explicitly prohibit publishing without approval.
- Strategist and Writer must cite Scout evidence for market claims.
- Publisher is planning-oriented and does not execute unless explicitly approved.

## Usage

```python
from kernel.api import ApexKernel

k = ApexKernel()
result = k.launch_template("gtm-engine")
print(result["workspace_id"])
print(result["agents_created"])
```

## Default Budgets

- `api_calls`: 120 units/day (alert at 80%)
- `tool_cost`: $10/day (alert at 80%)

## Optional Integrations

- `web_search`
- `linkedin`
- `twitter`
- `email`
