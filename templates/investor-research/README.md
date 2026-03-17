# Investor Research Engine

A four-agent pipeline that replaces 10–20 hours of manual fundraise prep. Finds active investors in your space, enriches each with fund details and portfolio data, ranks them by fit, and drafts personalized outreach for your top targets.

## What it replaces

- Fractional fundraise advisor ($3–8K/month)
- 15–25 hours of founder research before a raise
- PitchBook / Crunchbase subscriptions used only during fundraising

## Agents

| Agent | Role | What it does |
|---|---|---|
| `scout` | Investor Finder | Discovers active investors in the target space with at least one deal in the last 12 months |
| `analyst` | Investment Analyst | Enriches each investor: fund, partner, AUM, check size, thesis, portfolio, most recent deal |
| `strategist` | Outreach Strategist | For Tier 1 investors: thesis connection, closest portfolio comp, intro channel, draft cold email |
| `critic` | Research Reviewer | Verifies accuracy and recency of the full package before it reaches the operator |

## Pipeline

```
Scout → Analyst → Strategist → Critic → Your Approval → Pipeline
```

## Output

The Analyst produces a prioritized Tier 1/2/3 ranked list. The Strategist generates one personalized outreach angle per Tier 1 investor. The full package lands in your approval queue — you review, edit if needed, and save to pipeline.

## Review dimensions

The Critic scores on four dimensions that match the web app's review page:

- **Accuracy** — fund details, partner names, and deal history are verified
- **Relevance** — the investor actually funds this space and stage
- **Thesis Fit** — specific connection to the company, not generic "we fund AI"
- **Recency** — most recent deal is cited with a date

## Launching

```python
from kernel.api import ApexKernel

k = ApexKernel()
result = k.launch_template("investor-research")
workspace_id = result["workspace_id"]

# Give the team a mission
k.create_task(
    title="Build investor list for our Series A raise",
    description=(
        "Target stage: Series A ($3–10M check size). "
        "Space: AI infrastructure and developer tools. "
        "Geography: US-based or US-investing. "
        "Find 20+ investors with a recent deal in this space."
    ),
    workspace_id=workspace_id,
)
```

## Configuration fields

Set these at launch or in team settings:

- **target_stage** — Pre-seed / Seed / Series A / Series B
- **check_size_range** — e.g., "$500K–$3M" or "$3M–$10M"
- **geography** — e.g., "US-based", "US or Europe", "Global"
