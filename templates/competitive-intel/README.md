# Competitive Intelligence Engine Template

A three-agent competitive monitoring pipeline for daily signal collection, change detection, and source-verified briefings.

## Agents

| Agent   | Role               | Heartbeat         |
|---------|--------------------|-------------------|
| scout   | Competitor Monitor | Daily at 8:00 AM  |
| analyst | Change Detector    | On-demand         |
| critic  | Source Verifier    | Continuous        |

## Pipeline

`discover` → `analyze` → `review`

## What It Does

- `scout` searches for competitor news, launches, pricing changes, and hiring signals.
- `analyst` compares new findings against previous state and turns them into meaningful change summaries.
- `critic` verifies all competitor claims against evidence before anything is delivered.

## Usage

```python
from kernel.api import ApexKernel

k = ApexKernel()
result = k.launch_template("competitive-intel")
print(result["workspace_id"])
print(result["agents_created"])
```

## Default Budgets

- `api_calls`: 75 units/day (alert at 75%)
- `tool_cost`: $10/day (alert at 80%)

## Optional Integrations

- `web_search`
