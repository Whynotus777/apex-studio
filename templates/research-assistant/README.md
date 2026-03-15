# Research Assistant Template

A three-agent research pipeline for deep literature review, market intelligence, and quality-gated synthesis.

## Agents

| Agent   | Role          | Heartbeat         |
|---------|---------------|-------------------|
| scout   | Discovery     | Every 6 hours     |
| analyst | Intelligence  | On-demand         |
| critic  | Quality gate  | Continuous        |

## Pipeline

`discover` → `analyze` → `validate`

## Usage

```python
from kernel.api import ApexKernel
k = ApexKernel()
result = k.launch_template("research-assistant")
print(result["agents_created"])
```

## Default budgets

- `api_calls`: 50 units/day (alert at 70%)
- `tool_cost`: $10/day (alert at 80%)
