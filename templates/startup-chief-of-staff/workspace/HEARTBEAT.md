# APEX Heartbeat Schedule

## Morning Rollup — 8:00 AM EST (Apex)
- Overnight activity summary across all projects
- Pending approvals
- Scout signals from overnight scan
- Builder test results
- Any escalations or blockers

## Scout Scan — Every 4 hours
- X/Twitter, Reddit, HackerNews, industry news
- SEC EDGAR for relevant filings
- Log signals to scratchpad, alert on strong signals

## Builder Nightly — 11:00 PM EST
- Run test suite
- Report results
- Propose next day's tasks

## Critic Queue — Continuous
- Process review queue as items arrive
- Low-stakes: auto-score and log
- Medium/High-stakes: deep review via API
