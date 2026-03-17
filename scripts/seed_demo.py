#!/usr/bin/env python3
"""
scripts/seed_demo.py — Deterministic demo data for APEX web app development.

Seeds multiple teams with realistic content, evidence, critic reviews, and chain
events so every web app screen renders real-looking data without running the
full agent pipeline.

Idempotent: running twice does not create duplicates.

Usage:
    PYTHONPATH=. python3 scripts/seed_demo.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

APEX_HOME = Path(__file__).resolve().parents[1]
DB_PATH = APEX_HOME / "db" / "apex_state.db"


# ── DB helpers ────────────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def _row_exists(conn: sqlite3.Connection, table: str, where: str, params: tuple) -> bool:
    return conn.execute(f"SELECT 1 FROM {table} WHERE {where}", params).fetchone() is not None


# ── Realistic content ─────────────────────────────────────────────────────────

# Marketing task 1 — approved & published
_DRAFT_MKT_1 = """\
The 2024 fragmentation in AI agent frameworks is resolving into a clear \
pattern: orchestration wins over raw model capability.

We've seen this before. The database wars (MySQL vs Postgres vs Oracle) settled \
not because one was objectively better, but because tooling, documentation, and \
ecosystem maturity compounded over time.

The same thing is happening in agentic infrastructure right now. LangGraph, \
AutoGen, and CrewAI aren't fundamentally different under the hood — they're \
competing on developer experience and community momentum.

The category winner will be the one that makes multi-agent coordination boring \
and reliable. Not impressive demos. Boring, reliable coordination.

Three signals I'm watching: which framework lands the first Fortune 500 internal \
standard, which one ships a native testing harness, and which one integrates \
deepest with enterprise identity systems.

If you're building on top of these frameworks, the abstraction layer you build \
today will determine your optionality in 18 months.

#AIAgents #OpenSource #Infrastructure #BuildingInPublic"""

# Marketing task 2 — pending approval (~200 words LinkedIn post)
_DRAFT_MKT_2 = """\
Traditional SaaS is dying. Not because the software is bad — but because it was \
never meant to take actions on your behalf.

For a decade we bought tools that made us faster at doing things ourselves. Notion \
didn't think. Salesforce didn't act. Slack didn't decide.

That's the actual shift happening right now: software that operates autonomously \
within defined constraints. Not AI chat. Not AI autocomplete. Agents that run \
pipelines, verify their own work, and route to humans when uncertain.

I've been building one of these systems for six months. The hardest problems aren't \
model quality — they're trust primitives. How does an agent prove it didn't \
hallucinate? How do you audit a chain of three agents when something goes wrong? \
How do you give an agent the right level of access without giving it too much?

The answer isn't better models. It's evidence grounding, critic pipelines, and \
structured escalation. Infrastructure patterns, not AI magic.

The companies building this layer — the trust infrastructure for agentic systems — \
are the ones I'm watching most closely in 2026.

What patterns are you seeing in production agentic systems?

#AIAgents #AgenticInfrastructure #AI2026"""

# Sales task 1 — approved outreach
_DRAFT_SALES_1 = """\
Subject: AI-assisted deal analysis at Meridian's scale

Hi James,

I came across Meridian Capital's recent moves in the healthcare SaaS space — \
the DiagnosticAI and MedFlow deals in Q4 caught my attention.

We've been working with a few mid-market PE firms on a specific problem: the gap \
between deal flow volume and analyst bandwidth. The pattern is consistent — teams \
are reviewing 3–5x more CIMs than two years ago, but headcount hasn't scaled \
proportionally.

One firm at your scale (8 partners, ~$400M AUM) piloted an AI-assisted layer \
that produced first-pass investment memos from CIMs in under 15 minutes. Their \
analysts shifted from spending 60% of time on initial screening to 80% on \
conviction-building.

The quality gate matters here — they kept a human review step on every memo \
before it reached partners, which preserved the analytical standard while \
reclaiming the bandwidth.

Given Meridian's healthcare focus, I'd be curious whether CIM volume is actually \
the constraint, or whether it's something further downstream.

Worth a short call to compare notes?

Best,
[Your name]"""

# Sales task 2 — pending approval outreach email
_DRAFT_SALES_2 = """\
Subject: Scaling data infrastructure past Series A — quick question

Hi Sarah,

I noticed DataFlow Analytics raised a $12M Series A in January — congrats on \
the milestone. That stage usually brings one infrastructure challenge that \
compounds fast: the pipelines that got you to product-market fit start to become \
the thing that slows your next phase.

We've been working with a handful of Series A data companies through exactly this \
inflection. The pattern is consistent — 2–3 engineers spending 15–20 hours a week \
on pipeline maintenance that should be automated, while the product team waits on \
data they needed yesterday.

One company we worked with (comparable size, B2B SaaS, similar data volume) cut \
that maintenance overhead by 70% over about eight weeks. The interesting part \
wasn't the tooling change — it was identifying which parts of their pipeline \
actually needed human oversight versus which had just accumulated it by default.

One question: what's the current bottleneck between your data team and your \
product decisions? Not pitching anything — genuinely curious whether the pattern \
holds at DataFlow's scale.

Worth a 15-minute call this week?

Best,
[Your name]"""


# ── Evidence data (8 rows total, realistic URLs & snippets) ──────────────────

def _ev(uid: str, task_id: str, agent_id: str, query: str, results: list[dict]) -> dict:
    return {
        "id": uid, "task_id": task_id, "agent_id": agent_id,
        "tool_name": "web_search", "query": query,
        "results": json.dumps(results),
    }


_EVIDENCE = [
    # ── Marketing task 1 (2 rows, 5 URLs across both) ───────────────────────
    _ev("ev-demo-mkt1-a", "task-demo-mkt-001", "ws-demo-marketing-scout",
        "AI agent framework convergence LangGraph AutoGen 2026",
        [
            {"title": "LangGraph 0.3 ships production-ready multi-agent support",
             "url": "https://blog.langchain.dev/langgraph-0-3-release",
             "snippet": "LangGraph now supports stateful multi-agent workflows with built-in "
                        "checkpointing and human-in-the-loop approval gates.",
             "source": "langchain.dev"},
            {"title": "AutoGen vs LangGraph: Which framework for enterprise agents?",
             "url": "https://towardsdatascience.com/autogen-vs-langgraph-2026",
             "snippet": "Comparative analysis of the two dominant agentic frameworks for "
                        "production deployments across Fortune 500 pilots.",
             "source": "towardsdatascience.com"},
            {"title": "The Agentic Infrastructure Stack",
             "url": "https://a16z.com/agentic-infrastructure-stack-2026",
             "snippet": "Framework consolidation is the defining theme of early 2026 in "
                        "the AI agent ecosystem. a16z analysis of 40 production deployments.",
             "source": "a16z.com"},
        ]),
    _ev("ev-demo-mkt1-b", "task-demo-mkt-001", "ws-demo-marketing-scout",
        "multi-agent orchestration open source adoption March 2026",
        [
            {"title": "CrewAI raises $18M, open-sources enterprise orchestration layer",
             "url": "https://techcrunch.com/2026/02/crewai-series-a",
             "snippet": "CrewAI's new enterprise orchestration layer includes native audit "
                        "trails and permission scoping for regulated industries.",
             "source": "techcrunch.com"},
            {"title": "Fortune 500 AI agent adoption: 2026 survey",
             "url": "https://mckinsey.com/capabilities/quantumblack/our-insights/ai-agents-enterprise-2026",
             "snippet": "47% of Fortune 500 companies have a production agent deployment "
                        "as of Q1 2026, up from 12% in Q1 2025.",
             "source": "mckinsey.com"},
        ]),

    # ── Marketing task 2 (2 rows, 3 distinct URLs used in UI) ───────────────
    _ev("ev-demo-mkt2-a", "task-demo-mkt-002", "ws-demo-marketing-scout",
        "agentic AI trust grounding critic pipelines production 2026",
        [
            {"title": "Evidence-grounded agents: solving hallucination in production",
             "url": "https://arxiv.org/abs/2601.08742",
             "snippet": "Systematic review of citation-verification approaches in production "
                        "LLM deployments. Grounded agents reduce hallucination by 63%.",
             "source": "arxiv.org"},
            {"title": "Critic pipelines in multi-agent systems — open source patterns",
             "url": "https://github.com/anthropics/agent-evaluation-patterns",
             "snippet": "Open-source patterns for agent self-evaluation and peer review "
                        "in production systems. Includes evidence grounding templates.",
             "source": "github.com"},
        ]),
    _ev("ev-demo-mkt2-b", "task-demo-mkt-002", "ws-demo-marketing-scout",
        "autonomous software agents replacing SaaS enterprise 2026",
        [
            {"title": "The shift from SaaS to autonomous software agents",
             "url": "https://bain.com/insights/autonomous-software-agents-2026",
             "snippet": "Bain analysis of enterprise AI deployment patterns shows "
                        "agent-first architectures delivering 3.2x ROI vs traditional SaaS.",
             "source": "bain.com"},
        ]),

    # ── Marketing task 3 — scout done, writer drafting (1 row) ──────────────
    _ev("ev-demo-mkt3-a", "task-demo-mkt-003", "ws-demo-marketing-scout",
        "agentic infrastructure reliability vs model capability March 2026",
        [
            {"title": "The case for infrastructure-first AI",
             "url": "https://stratechery.com/2026/infrastructure-first-ai",
             "snippet": "Why the current phase of AI deployment rewards reliability over "
                        "raw model performance. Stratechery analysis.",
             "source": "stratechery.com"},
            {"title": "HELM-Agents 2026: reliability benchmarks across orchestration frameworks",
             "url": "https://arxiv.org/abs/2602.11337",
             "snippet": "Reliability variance across orchestration frameworks exceeds "
                        "capability variance, making architecture choice more important than model choice.",
             "source": "arxiv.org"},
        ]),

    # ── Sales task 1 (2 rows, 8 URLs total) ─────────────────────────────────
    _ev("ev-demo-sales1-a", "task-demo-sales-001", "ws-demo-sales-scout",
        "Meridian Capital portfolio healthcare SaaS investments 2025 2026",
        [
            {"title": "Meridian Capital closes $420M Fund IV, healthcare focus confirmed",
             "url": "https://pitchbook.com/news/articles/meridian-capital-fund-iv-close",
             "snippet": "Meridian Capital's fourth fund concentrates on healthcare SaaS "
                        "and diagnostic technology.",
             "source": "pitchbook.com"},
            {"title": "DiagnosticAI Series B — Meridian leads $28M round",
             "url": "https://techcrunch.com/2025/11/diagnosticai-series-b",
             "snippet": "Meridian Capital leads DiagnosticAI's Series B citing proprietary "
                        "diagnostic model performance in radiology.",
             "source": "techcrunch.com"},
            {"title": "MedFlow acquired by Meridian portfolio co for $45M",
             "url": "https://businesswire.com/news/medflow-acquisition-meridian",
             "snippet": "MedFlow, a clinical workflow automation platform, joins the "
                        "Meridian portfolio through a strategic acquisition.",
             "source": "businesswire.com"},
        ]),
    _ev("ev-demo-sales1-b", "task-demo-sales-001", "ws-demo-sales-scout",
        "AI-assisted PE due diligence CIM analysis tools ROI 2026",
        [
            {"title": "PE firms adopt AI for CIM analysis — ROI study",
             "url": "https://bain.com/insights/private-equity-ai-due-diligence-2026",
             "snippet": "Survey of 120 PE firms: top performers reduce CIM screening time "
                        "by 65% using AI-assisted analysis layers.",
             "source": "bain.com"},
            {"title": "Inven AI raises $15M for PE intelligence platform",
             "url": "https://techcrunch.com/2026/01/inven-ai-series-a",
             "snippet": "Inven AI targets private equity firms with automated deal sourcing "
                        "and preliminary investment analysis.",
             "source": "techcrunch.com"},
            {"title": "How Thoma Bravo uses AI to analyse 10,000 companies per month",
             "url": "https://wsj.com/articles/thoma-bravo-ai-deal-sourcing-2026",
             "snippet": "Thoma Bravo's internal AI system surfaces deal candidates from "
                        "10K+ companies monthly, reducing sourcing cost by 80%.",
             "source": "wsj.com"},
            {"title": "Investment memo automation: accuracy benchmarks",
             "url": "https://arxiv.org/abs/2603.04421",
             "snippet": "Automated investment memo generation achieves 91% accuracy on "
                        "factual claims when grounded in source documents.",
             "source": "arxiv.org"},
            {"title": "Deal sourcing AI comparison: Grata vs Inven vs in-house",
             "url": "https://medium.com/pe-technology/deal-sourcing-ai-comparison-2026",
             "snippet": "Comparison of PE technology tools for deal sourcing automation "
                        "across 15 mid-market firms.",
             "source": "medium.com"},
        ]),

    # ── Sales task 2 (1 row) ─────────────────────────────────────────────────
    _ev("ev-demo-sales2-a", "task-demo-sales-002", "ws-demo-sales-scout",
        "DataFlow Analytics Series A funding 2026 data infrastructure scaling",
        [
            {"title": "DataFlow Analytics raises $12M Series A for real-time data pipelines",
             "url": "https://techcrunch.com/2026/01/dataflow-analytics-series-a",
             "snippet": "DataFlow Analytics closes $12M Series A led by Sequoia to expand "
                        "real-time data pipeline infrastructure for mid-market B2B SaaS.",
             "source": "techcrunch.com"},
            {"title": "DataFlow CEO Sarah Chen on scaling infrastructure post-funding",
             "url": "https://sifted.eu/articles/dataflow-analytics-sarah-chen-interview",
             "snippet": "Sarah Chen discusses DataFlow's engineering challenges and team "
                        "structure at Series A scale.",
             "source": "sifted.eu"},
        ]),
]  # 8 rows total


# ── Agent sessions (drafts + scout summaries) ─────────────────────────────────

def _session(sid: str, agent: str, task_id: str, output: str,
             actions: str, status: str = "active",
             created_offset: str = "now") -> dict:
    context = json.dumps({
        "actions_taken": actions,
        "observations": "Task completed based on search evidence.",
        "proposed_output": output,
        "messages": [],
        "scratchpad_update": "",
        "status": {"state": status, "stakes": "medium"},
    })
    return {
        "id": sid, "agent_name": agent, "task_id": task_id,
        "context": context,
        "created_at": f"datetime('now', '{created_offset}')",
        "last_active": f"datetime('now', '{created_offset}')",
        "status": "active",
    }


# Timestamps expressed as SQLite datetime offsets from now
_SESSIONS = [
    # Marketing task 1 — approved / published
    _session("sess-demo-mkt1-scout", "ws-demo-marketing-scout", "task-demo-mkt-001",
             "Research complete. Found 5 sources on AI framework convergence. "
             "LangGraph, AutoGen, CrewAI are the main players. a16z and McKinsey both published "
             "relevant analysis. Handing off to Writer.",
             "Ran 2 search queries, stored 5 evidence URLs, summarised key themes.",
             "done", "-2 days -55 minutes"),
    _session("sess-demo-mkt1-writer", "ws-demo-marketing-writer", "task-demo-mkt-001",
             _DRAFT_MKT_1,
             "Drafted LinkedIn post on AI framework convergence. "
             "Cited a16z, McKinsey, and TechCrunch sources. 1,180 characters.",
             "needs_review", "-2 days -50 minutes"),
    _session("sess-demo-mkt1-critic", "ws-demo-marketing-critic", "task-demo-mkt-001",
             "PASS. Strong post. Contrarian hook works well. All sources verified. "
             "Minor suggestion: add a specific data point in paragraph 2 on revision.",
             "Reviewed Writer draft. Verified 5/5 source citations. Scored dimensions. Verdict: PASS.",
             "done", "-2 days -45 minutes"),

    # Marketing task 2 — pending approval (the key demo draft)
    _session("sess-demo-mkt2-scout", "ws-demo-marketing-scout", "task-demo-mkt-002",
             "Research complete. Found strong evidence on trust primitives in agentic AI: "
             "arxiv paper on grounding, Bain analysis on SaaS displacement, GitHub patterns repo. "
             "3 high-quality sources. Handing off to Writer.",
             "Ran 2 search queries, stored 3 evidence URLs, summarised findings on trust infrastructure.",
             "done", "-7 hours"),
    _session("sess-demo-mkt2-writer", "ws-demo-marketing-writer", "task-demo-mkt-002",
             _DRAFT_MKT_2,
             "Drafted LinkedIn post (~200 words) on the trust layer in agentic AI. "
             "Bold opinionated tone. Sources cited from Search Evidence. Submitting for Critic review.",
             "needs_review", "-6 hours -45 minutes"),
    _session("sess-demo-mkt2-critic", "ws-demo-marketing-critic", "task-demo-mkt-002",
             "PASS. Strong opening hook. Argumentative arc is clear. Sources well-used. "
             "One improvement: paragraph 4 could cite a specific metric rather than a general claim. "
             "Overall quality is above threshold. Approving for human review.",
             "Reviewed Writer draft. Verified 3 source citations. Scored all 4 dimensions. Verdict: PASS.",
             "done", "-6 hours -30 minutes"),

    # Marketing task 3 — in progress (scout done, writer active)
    _session("sess-demo-mkt3-scout", "ws-demo-marketing-scout", "task-demo-mkt-003",
             "Research complete. Found 2 strong sources: Stratechery on infra-first AI, "
             "HELM-Agents benchmark on reliability vs capability. "
             "Clear angle available: reliability gap is bigger than capability gap. Handing off to Writer.",
             "Ran 1 search query, stored 2 evidence URLs, identified key angle.",
             "done", "-1 hour -45 minutes"),
    _session("sess-demo-mkt3-writer", "ws-demo-marketing-writer", "task-demo-mkt-003",
             "[Draft in progress — Writer is actively composing based on Scout research. "
             "Expected completion in ~3 minutes.]",
             "Reviewing Scout evidence. Drafting LinkedIn post on infrastructure vs capability angle.",
             "active", "-1 hour -30 minutes"),

    # Sales task 1 — approved
    _session("sess-demo-sales1-scout", "ws-demo-sales-scout", "task-demo-sales-001",
             "Prospect research complete. Meridian Capital: $420M Fund IV, healthcare SaaS focus. "
             "Recent deals: DiagnosticAI ($28M Series B), MedFlow acquisition. "
             "8 analysts. James Whitmore is the managing partner for healthcare deals. "
             "Pain point identified: CIM volume up 4x vs 2023, team size flat. Handing to Analyst.",
             "Ran 2 queries, found 8 sources on Meridian Capital and AI PE tools.",
             "done", "-5 days -2 hours"),
    _session("sess-demo-sales1-analyst", "ws-demo-sales-analyst", "task-demo-sales-001",
             "Enrichment complete. James Whitmore (Managing Partner, Healthcare). "
             "Last 3 posts mention AI and operational efficiency. "
             "Meridian's portfolio companies use AI tools for clinical workflow — suggests receptivity. "
             "Recommended angle: CIM analysis time reduction, ROI framing, not product pitch.",
             "Enriched contact profile for James Whitmore. Identified relevant pain points and talking points.",
             "done", "-5 days -1 hour -55 minutes"),
    _session("sess-demo-sales1-writer", "ws-demo-sales-writer", "task-demo-sales-001",
             _DRAFT_SALES_1,
             "Drafted personalised outreach email for James Whitmore at Meridian Capital. "
             "Referenced specific portfolio deals. Framed as peer conversation, not sales pitch.",
             "needs_review", "-5 days -1 hour -50 minutes"),
    _session("sess-demo-sales1-critic", "ws-demo-sales-critic", "task-demo-sales-001",
             "PASS. Email is well-personalised with specific deal references. "
             "Tone is peer-to-peer, not salesy. CIM volume stat is sourced. Recommend sending.",
             "Reviewed outreach draft. Verified personalisation details against Scout research. Verdict: PASS.",
             "done", "-5 days -1 hour -45 minutes"),

    # Sales task 2 — pending approval
    _session("sess-demo-sales2-scout", "ws-demo-sales-scout", "task-demo-sales-002",
             "Prospect research complete. DataFlow Analytics: $12M Series A (Jan 2026), Sequoia-backed. "
             "CEO: Sarah Chen. Product: real-time data pipeline infrastructure for B2B SaaS. "
             "Pain point: engineering scaling post-funding. 2 relevant sources found.",
             "Ran 1 query, found 2 sources on DataFlow Analytics Series A.",
             "done", "-3 days -1 hour"),
    _session("sess-demo-sales2-writer", "ws-demo-sales-writer", "task-demo-sales-002",
             _DRAFT_SALES_2,
             "Drafted personalised outreach email for Sarah Chen at DataFlow Analytics. "
             "Referenced Series A funding. Pipeline maintenance overhead angle.",
             "needs_review", "-3 days -55 minutes"),
    _session("sess-demo-sales2-critic", "ws-demo-sales-critic", "task-demo-sales-002",
             "PASS. Good personalisation with Series A reference. "
             "Opening line is specific and timely. Pipeline maintenance angle is credible. "
             "Minor: could sharpen the one-question CTA. Overall above threshold.",
             "Reviewed outreach draft. Verified personalisation against Scout research. Verdict: PASS.",
             "done", "-3 days -50 minutes"),
]


# ── Chain messages (activity feed) ───────────────────────────────────────────

_MESSAGES = [
    # Marketing task 1 — full approved chain
    ("ws-demo-marketing-scout",  "ws-demo-marketing-writer",  "request",
     "task-demo-mkt-001",  "ws-demo-marketing",
     "Research complete. 5 sources on AI framework convergence. "
     "Key themes: LangGraph/AutoGen convergence, Fortune 500 adoption at 47%, ecosystem maturity. "
     "Evidence stored. Ready for drafting.",
     "-2 days -54 minutes"),
    ("ws-demo-marketing-writer",  "ws-demo-marketing-critic",  "request",
     "task-demo-mkt-001",  "ws-demo-marketing",
     "Draft complete. LinkedIn post on AI framework convergence (~1,180 chars). "
     "Bold opinionated tone. 5 sources cited. Ready for review.",
     "-2 days -49 minutes"),
    ("ws-demo-marketing-critic",  "ws-demo-marketing-writer",  "alert",
     "task-demo-mkt-001",  "ws-demo-marketing",
     "PASS (4.2/5). Strong post — contrarian hook works. All sources verified. "
     "Minor: consider adding one specific data point in paragraph 2 before final approval.",
     "-2 days -44 minutes"),

    # Marketing task 2 — approved by critic, awaiting human approval
    ("ws-demo-marketing-scout",  "ws-demo-marketing-writer",  "request",
     "task-demo-mkt-002",  "ws-demo-marketing",
     "Research complete. 3 high-quality sources on agentic trust and SaaS displacement. "
     "Strong angle: trust primitives are the real bottleneck, not model capability. "
     "Evidence stored. Ready for drafting.",
     "-6 hours -59 minutes"),
    ("ws-demo-marketing-writer",  "ws-demo-marketing-critic",  "request",
     "task-demo-mkt-002",  "ws-demo-marketing",
     "Draft complete. LinkedIn post on trust infrastructure (~200 words). "
     "Bold opinionated tone. 3 sources cited. Submitting for review.",
     "-6 hours -44 minutes"),
    ("ws-demo-marketing-critic",  "ws-demo-marketing-writer",  "alert",
     "task-demo-mkt-002",  "ws-demo-marketing",
     "PASS (3.8/5). Strong hook and clear argument. Sources verified. "
     "One suggestion: add a specific metric in paragraph 4. Approved for human review.",
     "-6 hours -29 minutes"),

    # Marketing task 3 — partial chain (scout→writer handoff only)
    ("ws-demo-marketing-scout",  "ws-demo-marketing-writer",  "request",
     "task-demo-mkt-003",  "ws-demo-marketing",
     "Research complete. 2 sources: Stratechery on infra-first AI + HELM-Agents reliability benchmarks. "
     "Angle: reliability gap > capability gap. Evidence stored. Ready for drafting.",
     "-1 hour -44 minutes"),

    # Sales task 1 — full approved chain
    ("ws-demo-sales-scout",  "ws-demo-sales-analyst",  "request",
     "task-demo-sales-001",  "ws-demo-sales",
     "Prospect research complete: Meridian Capital. Fund IV $420M, healthcare focus. "
     "8 evidence sources. Key finding: CIM volume 4x but team flat. Passing to Analyst for enrichment.",
     "-5 days -1 hour -59 minutes"),
    ("ws-demo-sales-analyst",  "ws-demo-sales-writer",  "request",
     "task-demo-sales-001",  "ws-demo-sales",
     "Enrichment complete. Contact: James Whitmore (Managing Partner, Healthcare). "
     "LinkedIn posts signal receptivity to AI ops. Recommended angle: CIM analysis ROI. Passing to Writer.",
     "-5 days -1 hour -54 minutes"),
    ("ws-demo-sales-writer",  "ws-demo-sales-critic",  "request",
     "task-demo-sales-001",  "ws-demo-sales",
     "Outreach email drafted. References DiagnosticAI and MedFlow deals. "
     "Peer conversation frame, not pitch. Ready for critic review.",
     "-5 days -1 hour -49 minutes"),
    ("ws-demo-sales-critic",  "ws-demo-sales-writer",  "alert",
     "task-demo-sales-001",  "ws-demo-sales",
     "PASS (4.5/5). Well-personalised with verified deal references. "
     "Tone is peer-to-peer. Recommend sending. Approved for human review.",
     "-5 days -1 hour -44 minutes"),

    # Sales task 2 — approved by critic, awaiting human approval
    ("ws-demo-sales-scout",  "ws-demo-sales-writer",  "request",
     "task-demo-sales-002",  "ws-demo-sales",
     "Prospect research complete: DataFlow Analytics. $12M Series A Jan 2026, Sequoia. "
     "CEO: Sarah Chen. Pain point: engineering scaling. Passing directly to Writer (no analyst needed).",
     "-3 days -59 minutes"),
    ("ws-demo-sales-writer",  "ws-demo-sales-critic",  "request",
     "task-demo-sales-002",  "ws-demo-sales",
     "Outreach email drafted for Sarah Chen. References Series A. "
     "Pipeline maintenance overhead angle. Ready for review.",
     "-3 days -54 minutes"),
    ("ws-demo-sales-critic",  "ws-demo-sales-writer",  "alert",
     "task-demo-sales-002",  "ws-demo-sales",
     "PASS (4.1/5). Good personalisation and timely hook. "
     "CTA is clean. Approved for human review.",
     "-3 days -49 minutes"),
]


# ── Main seed logic ───────────────────────────────────────────────────────────

def seed(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {"teams": 0, "tasks": 0, "evidence": 0, "sessions": 0,
              "reviews_pending": 0, "reviews_total": 0, "messages": 0,
              "evals": 0, "prefs": 0}

    # ── 1. Goals ──────────────────────────────────────────────────────────────
    goals = [
        ("goal-demo-marketing", "Content Marketing Automation",
         "Automate research, drafting, and publishing of thought leadership content."),
        ("goal-demo-sales",     "Sales Outreach Automation",
         "Automate prospect research, enrichment, and personalised outreach drafts."),
        ("goal-demo-investor",  "Investor Intelligence Briefing",
         "Build an investor target list, enrich each fund, and draft outreach for the highest-fit firms."),
    ]
    for gid, name, desc in goals:
        conn.execute(
            "INSERT OR REPLACE INTO goals (id, name, description, status) VALUES (?,?,?,'active')",
            (gid, name, desc),
        )

    # ── 2. Workspaces ─────────────────────────────────────────────────────────
    workspaces = [
        ("ws-demo-marketing", "content-engine",   "My Marketing Team"),
        ("ws-demo-sales",     "sales-outreach",   "My Sales Team"),
        ("ws-demo-investor",  "investor-research", "My Investor Team"),
    ]
    for wid, tid, name in workspaces:
        conn.execute(
            "INSERT OR REPLACE INTO workspaces (id, template_id, name, status) VALUES (?,?,?,'active')",
            (wid, tid, name),
        )
        counts["teams"] += 1

    # ── 3. Agent statuses ─────────────────────────────────────────────────────
    def _meta(workspace_id: str, template_id: str, template_agent: str, config_tpl: str) -> str:
        return json.dumps({
            "paused": False,
            "config_path": str(APEX_HOME / "templates" / config_tpl),
            "template_id": template_id,
            "template_agent_name": template_agent,
            "workspace_id": workspace_id,
        })

    agents = [
        # Marketing team — writer is active (drafting task 3)
        ("ws-demo-marketing-scout",     "ws-demo-marketing", "idle",   None,
         _meta("ws-demo-marketing", "content-engine", "scout",
               "content-engine/agents/scout/agent.json")),
        ("ws-demo-marketing-writer",    "ws-demo-marketing", "active", "task-demo-mkt-003",
         _meta("ws-demo-marketing", "content-engine", "writer",
               "content-engine/agents/writer/agent.json")),
        ("ws-demo-marketing-critic",    "ws-demo-marketing", "idle",   None,
         _meta("ws-demo-marketing", "content-engine", "critic",
               "content-engine/agents/critic/agent.json")),
        ("ws-demo-marketing-scheduler", "ws-demo-marketing", "idle",   None,
         _meta("ws-demo-marketing", "content-engine", "scheduler",
               "content-engine/agents/scheduler/agent.json")),
        # Sales team — all idle
        ("ws-demo-sales-scout",         "ws-demo-sales",     "idle",   None,
         _meta("ws-demo-sales", "sales-outreach", "scout",
               "startup-chief-of-staff/agents/scout/agent.json")),
        ("ws-demo-sales-analyst",       "ws-demo-sales",     "idle",   None,
         _meta("ws-demo-sales", "sales-outreach", "analyst",
               "startup-chief-of-staff/agents/analyst/agent.json")),
        ("ws-demo-sales-writer",        "ws-demo-sales",     "idle",   None,
         _meta("ws-demo-sales", "sales-outreach", "writer",
               "content-engine/agents/writer/agent.json")),
        ("ws-demo-sales-critic",        "ws-demo-sales",     "idle",   None,
         _meta("ws-demo-sales", "sales-outreach", "critic",
               "content-engine/agents/critic/agent.json")),
        # Investor team — strategist is actively turning research into outreach
        ("ws-demo-investor-scout",      "ws-demo-investor",  "idle",   None,
         _meta("ws-demo-investor", "investor-research", "scout",
               "investor-research/agents/scout/agent.json")),
        ("ws-demo-investor-analyst",    "ws-demo-investor",  "idle",   None,
         _meta("ws-demo-investor", "investor-research", "analyst",
               "investor-research/agents/analyst/agent.json")),
        ("ws-demo-investor-strategist", "ws-demo-investor",  "active", "task-demo-investor-001",
         _meta("ws-demo-investor", "investor-research", "strategist",
               "investor-research/agents/strategist/agent.json")),
        ("ws-demo-investor-critic",     "ws-demo-investor",  "idle",   None,
         _meta("ws-demo-investor", "investor-research", "critic",
               "investor-research/agents/critic/agent.json")),
    ]
    for agent_name, ws_id, status, current_task, meta in agents:
        conn.execute(
            """INSERT OR REPLACE INTO agent_status
               (agent_name, status, current_task, last_heartbeat, workspace_id, meta)
               VALUES (?, ?, ?, datetime('now'), ?, ?)""",
            (agent_name, status, current_task, ws_id, meta),
        )

    # ── 4. Tasks ──────────────────────────────────────────────────────────────
    tasks = [
        # Marketing — task 1: done + approved + published
        ("task-demo-mkt-001", "ws-demo-marketing", "goal-demo-marketing",
         "How AI agent frameworks are converging in 2026",
         "Research the convergence of open-source AI agent frameworks and draft "
         "a LinkedIn post from the perspective of an infrastructure builder.",
         "done", "approved",
         "ws-demo-marketing-writer", None,
         "datetime('now', '-2 days -40 minutes')",
         "datetime('now', '-2 days')"),

        # Marketing — task 2: critic passed, awaiting human approval
        ("task-demo-mkt-002", "ws-demo-marketing", "goal-demo-marketing",
         "The trust infrastructure powering autonomous AI systems",
         "Research the trust and grounding primitives enabling reliable agentic AI "
         "and draft a bold LinkedIn post about the shift from SaaS to autonomous software.",
         "review", "critic_passed",
         "ws-demo-marketing-writer", None,
         "datetime('now', '-6 hours -20 minutes')",
         None),

        # Marketing — task 3: in progress, writer drafting
        ("task-demo-mkt-003", "ws-demo-marketing", "goal-demo-marketing",
         "Why infrastructure beats smarter models in agentic AI",
         "Research the reliability vs capability debate in agentic AI and draft "
         "a LinkedIn post arguing that infrastructure patterns matter more than model quality.",
         "in_progress", None,
         "ws-demo-marketing-writer", "ws-demo-marketing-writer",
         "datetime('now', '-1 hour -25 minutes')",
         None),

        # Sales — task 1: done + approved
        ("task-demo-sales-001", "ws-demo-sales", "goal-demo-sales",
         "Prospect research: Meridian Capital (James Whitmore)",
         "Research Meridian Capital's portfolio and recent deals. Enrich contact profile "
         "for James Whitmore. Draft personalised outreach email on AI-assisted deal analysis.",
         "done", "approved",
         "ws-demo-sales-writer", None,
         "datetime('now', '-5 days -1 hour -40 minutes')",
         "datetime('now', '-5 days')"),

        # Sales — task 2: critic passed, awaiting human approval
        ("task-demo-sales-002", "ws-demo-sales", "goal-demo-sales",
         "Prospect research: DataFlow Analytics (Sarah Chen)",
         "Research DataFlow Analytics' recent Series A and scaling challenges. "
         "Draft personalised outreach email on data pipeline infrastructure.",
         "review", "critic_passed",
         "ws-demo-sales-writer", None,
         "datetime('now', '-3 days -45 minutes')",
         None),

        # Investor — analyst briefing in progress, scaffold for finance/research UI
        ("task-demo-investor-001", "ws-demo-investor", "goal-demo-investor",
         "Tier 1 AI infrastructure investor target package",
         "Find active investors in AI infrastructure, enrich each fund with thesis and portfolio fit, "
         "then draft a personalized outreach angle for the top matches.",
         "in_progress", None,
         "ws-demo-investor-strategist", "ws-demo-investor-strategist",
         "datetime('now', '-4 hours -10 minutes')",
         None),
    ]
    for (tid, ws, goal, title, desc, status, rev_status,
         assigned_to, checked_out_by, created_at, completed_at) in tasks:
        conn.execute(
            f"""INSERT OR REPLACE INTO tasks
               (id, workspace_id, goal_id, title, description, status, review_status,
                assigned_to, checked_out_by, created_at, completed_at)
               VALUES (?,?,?,?,?,?,?,?,?,{created_at},{completed_at or 'NULL'})""",
            (tid, ws, goal, title, desc, status, rev_status, assigned_to, checked_out_by),
        )
        counts["tasks"] += 1

    # ── 5. Evidence ───────────────────────────────────────────────────────────
    for ev in _EVIDENCE:
        conn.execute(
            """INSERT OR REPLACE INTO evidence
               (id, task_id, agent_id, tool_name, query, results)
               VALUES (?,?,?,?,?,?)""",
            (ev["id"], ev["task_id"], ev["agent_id"],
             ev["tool_name"], ev["query"], ev["results"]),
        )
        counts["evidence"] += 1

    investor_evidence = [
        (
            "ev-demo-investor-a",
            "task-demo-investor-001",
            "ws-demo-investor-scout",
            "AI infrastructure investors March 2026 enterprise agent infrastructure seed series A",
            json.dumps([
                {
                    "title": "Felicis backs agent infrastructure startup TraceLayer",
                    "url": "https://techcrunch.com/2026/03/tracelayer-series-a",
                    "snippet": "TraceLayer's Series A was led by Felicis, highlighting continued investor appetite for infrastructure that manages production AI systems.",
                    "source": "techcrunch.com",
                },
                {
                    "title": "Index Ventures bets on enterprise orchestration for AI agents",
                    "url": "https://sifted.eu/articles/index-ventures-ai-agent-orchestration-2026",
                    "snippet": "Index is increasing conviction around control-plane and observability layers for enterprise agent deployments.",
                    "source": "sifted.eu",
                },
                {
                    "title": "Theory Ventures on why AI infra still has room for new winners",
                    "url": "https://theory.ventures/blog/ai-infrastructure-2026",
                    "snippet": "Theory Ventures outlines what it looks for in vertical infrastructure products serving the next wave of AI-native software.",
                    "source": "theory.ventures",
                },
            ]),
        ),
    ]
    for evidence_id, task_id, agent_id, query, results_json in investor_evidence:
        conn.execute(
            """INSERT OR REPLACE INTO evidence
               (id, task_id, agent_id, tool_name, query, results)
               VALUES (?, ?, ?, 'web_search', ?, ?)""",
            (evidence_id, task_id, agent_id, query, results_json),
        )
        counts["evidence"] += 1

    # ── 6. Agent sessions (drafts) ────────────────────────────────────────────
    for s in _SESSIONS:
        conn.execute(
            f"""INSERT OR REPLACE INTO agent_sessions
               (id, agent_name, task_id, context, created_at, last_active, status)
               VALUES (?,?,?,?,{s['created_at']},{s['last_active']},?)""",
            (s["id"], s["agent_name"], s["task_id"], s["context"], s["status"]),
        )
        counts["sessions"] += 1

    investor_sessions = [
        {
            "id": "sess-demo-investor-scout",
            "agent_name": "ws-demo-investor-scout",
            "task_id": "task-demo-investor-001",
            "context": json.dumps({
                "actions_taken": "Built an initial list of active investors backing AI infrastructure and enterprise agent tooling.",
                "observations": "The strongest recent activity is concentrated among firms already backing observability, infra control planes, and workflow software.",
                "proposed_output": "Scout longlist complete. Passing candidate investors with recent deal evidence to Analyst.",
                "messages": [{"to": "ws-demo-investor-analyst", "type": "request", "content": "Enrich these investors with fund details, thesis, and portfolio fit."}],
                "scratchpad_update": "Prioritize investors already active in developer tooling, observability, and enterprise AI infrastructure.",
                "status": {"state": "done", "stakes": "medium"},
            }),
            "status": "complete",
            "created_at": "datetime('now', '-4 hours -12 minutes')",
            "last_active": "datetime('now', '-4 hours -11 minutes')",
        },
        {
            "id": "sess-demo-investor-analyst",
            "agent_name": "ws-demo-investor-analyst",
            "task_id": "task-demo-investor-001",
            "context": json.dumps({
                "actions_taken": "Researched fund size, stage fit, thesis alignment, and closest portfolio comps for the highest-signal investors from Scout.",
                "observations": "Felicis, Index, and Theory Ventures all show credible fit for an AI infrastructure raise, but with different positioning angles.",
                "proposed_output": (
                    "Tier 1 shortlist: Felicis for workflow software appetite, Index for enterprise orchestration angle, Theory for infra conviction and founder resonance."
                ),
                "messages": [{"to": "ws-demo-investor-strategist", "type": "handoff", "content": "Top investor shortlist ready. Draft personalized outreach angles and cold emails."}],
                "scratchpad_update": "Portfolio comp angle is strongest for Index; infra wedge is strongest for Theory.",
                "status": {"state": "done", "stakes": "medium"},
            }),
            "status": "complete",
            "created_at": "datetime('now', '-4 hours -5 minutes')",
            "last_active": "datetime('now', '-3 hours -58 minutes')",
        },
        {
            "id": "sess-demo-investor-strategist",
            "agent_name": "ws-demo-investor-strategist",
            "task_id": "task-demo-investor-001",
            "context": json.dumps({
                "actions_taken": "Drafting tailored outreach angles for the Tier 1 investor shortlist.",
                "observations": "Best openings connect current investor thesis to production trust infrastructure rather than generic AI tooling.",
                "proposed_output": (
                    "Draft outreach package: one tailored cold email per Tier 1 investor, each tied to a recent deal, portfolio comp, and the company's trust-infrastructure wedge."
                ),
                "messages": [],
                "scratchpad_update": "Keep cold emails under 150 words and thesis-linked.",
                "status": {"state": "active", "stakes": "medium"},
            }),
            "status": "active",
            "created_at": "datetime('now', '-3 hours -50 minutes')",
            "last_active": "datetime('now', '-3 hours -42 minutes')",
        },
    ]
    for s in investor_sessions:
        conn.execute(
            f"""INSERT OR REPLACE INTO agent_sessions
               (id, agent_name, task_id, context, created_at, last_active, status)
               VALUES (?,?,?,?,{s['created_at']},{s['last_active']},?)""",
            (s["id"], s["agent_name"], s["task_id"], s["context"], s["status"]),
        )
        counts["sessions"] += 1

    # ── 7. Reviews ────────────────────────────────────────────────────────────
    # Reviews use INTEGER autoincrement PK — match on (task_id, agent_name) to stay idempotent
    reviews = [
        # Marketing task 1 — approved
        ("task-demo-mkt-001", "ws-demo-marketing-critic", "sess-demo-mkt1-critic",
         "high", "PASS", "approved",
         json.dumps({
             "scores": {"accuracy": 5, "grounding": 4, "authenticity": 4, "completeness": 4},
             "overall_score": 4.2, "verdict": "PASS",
             "feedback": (
                 "Strong contrarian hook. All 5 sources verified against evidence store. "
                 "Argument arc is clear. Minor: paragraph 2 would benefit from a specific "
                 "data point rather than a general claim about ecosystem maturity."
             ),
             "hard_rule_violations": [], "grounding_issues": [],
         }),
         "ws-demo-marketing",
         "datetime('now', '-2 days -44 minutes')",
         "datetime('now', '-2 days -40 minutes')"),

        # Marketing task 2 — pending human approval
        ("task-demo-mkt-002", "ws-demo-marketing-critic", "sess-demo-mkt2-critic",
         "medium", "PASS", None,
         json.dumps({
             "scores": {"accuracy": 4, "grounding": 4, "authenticity": 4, "completeness": 3},
             "overall_score": 3.8, "verdict": "PASS",
             "feedback": (
                 "Strong opening hook that subverts the expected narrative. "
                 "Sources are well-used and grounded. The trust primitives section "
                 "is the strongest part. Paragraph 4 could cite a specific metric — "
                 "currently relies on assertion. Above threshold for human review."
             ),
             "hard_rule_violations": [], "grounding_issues": [],
         }),
         "ws-demo-marketing",
         "datetime('now', '-6 hours -29 minutes')",
         None),

        # Sales task 1 — approved
        ("task-demo-sales-001", "ws-demo-sales-critic", "sess-demo-sales1-critic",
         "high", "PASS", "approved",
         json.dumps({
             "scores": {"accuracy": 5, "grounding": 4, "authenticity": 5, "completeness": 4},
             "overall_score": 4.5, "verdict": "PASS",
             "feedback": (
                 "Excellent personalisation — DiagnosticAI and MedFlow deal references are "
                 "specific and verified against Scout research. Tone is peer conversation, "
                 "not sales pitch. CIM volume framing is strong. Recommend sending as-is."
             ),
             "hard_rule_violations": [], "grounding_issues": [],
         }),
         "ws-demo-sales",
         "datetime('now', '-5 days -1 hour -44 minutes')",
         "datetime('now', '-5 days -1 hour -40 minutes')"),

        # Sales task 2 — pending human approval
        ("task-demo-sales-002", "ws-demo-sales-critic", "sess-demo-sales2-critic",
         "medium", "PASS", None,
         json.dumps({
             "scores": {"accuracy": 4, "grounding": 4, "authenticity": 4, "completeness": 4},
             "overall_score": 4.1, "verdict": "PASS",
             "feedback": (
                 "Good personalisation with the Series A reference — timely and specific. "
                 "Pipeline maintenance overhead is a credible angle for this prospect profile. "
                 "The single-question CTA is clean. Minor: the 70% reduction stat should be "
                 "attributed to a specific case study rather than left generic."
             ),
             "hard_rule_violations": [], "grounding_issues": [],
         }),
         "ws-demo-sales",
         "datetime('now', '-3 days -49 minutes')",
         None),
    ]
    for (task_id, agent_name, output_ref, stakes, verdict, reviewed_verdict,
         feedback_json, ws_id, created_at, reviewed_at) in reviews:
        # Idempotent: skip if already exists for this task + agent
        if not _row_exists(conn, "reviews", "task_id=? AND agent_name=?",
                           (task_id, agent_name)):
            conn.execute(
                f"""INSERT INTO reviews
                   (task_id, agent_name, output_ref, stakes, verdict, feedback,
                    workspace_id, created_at, reviewed_at)
                   VALUES (?,?,?,?,?,?,?,{created_at},{reviewed_at or 'NULL'})""",
                (task_id, agent_name, output_ref, stakes, verdict, feedback_json, ws_id),
            )
        counts["reviews_total"] += 1
        if reviewed_verdict is None and verdict == "PASS":
            counts["reviews_pending"] += 1

    # ── 8. Evals (per-dimension scores) ──────────────────────────────────────
    evals = [
        # Marketing task 1
        ("task-demo-mkt-001", "ws-demo-marketing-critic", "ws-demo-marketing",
         [("accuracy", 5.0), ("grounding", 4.0), ("authenticity", 4.0),
          ("completeness", 4.0), ("overall", 4.2)]),
        # Marketing task 2
        ("task-demo-mkt-002", "ws-demo-marketing-critic", "ws-demo-marketing",
         [("accuracy", 4.0), ("grounding", 4.0), ("authenticity", 4.0),
          ("completeness", 3.0), ("overall", 3.8)]),
        # Sales task 1
        ("task-demo-sales-001", "ws-demo-sales-critic", "ws-demo-sales",
         [("accuracy", 5.0), ("grounding", 4.0), ("authenticity", 5.0),
          ("completeness", 4.0), ("overall", 4.5)]),
        # Sales task 2
        ("task-demo-sales-002", "ws-demo-sales-critic", "ws-demo-sales",
         [("accuracy", 4.0), ("grounding", 4.0), ("authenticity", 4.0),
          ("completeness", 4.0), ("overall", 4.1)]),
    ]
    for task_id, agent_name, ws_id, dimensions in evals:
        for dim, score in dimensions:
            if not _row_exists(conn, "evals",
                               "task_id=? AND agent_name=? AND dimension=?",
                               (task_id, agent_name, dim)):
                conn.execute(
                    """INSERT INTO evals
                       (task_id, agent_name, eval_layer, eval_type, dimension,
                        score, max_score, workspace_id)
                       VALUES (?,?,'critic','dimension_score',?,?,5.0,?)""",
                    (task_id, agent_name, dim, score, ws_id),
                )
                counts["evals"] += 1

    investor_messages = [
        (
            "ws-demo-investor-scout",
            "ws-demo-investor-analyst",
            "research_handoff",
            "task-demo-investor-001",
            "ws-demo-investor",
            "Initial investor longlist is ready. Focus enrichment on firms with recent AI infra or observability deals.",
            "-4 hours -8 minutes",
        ),
        (
            "ws-demo-investor-analyst",
            "ws-demo-investor-strategist",
            "research_handoff",
            "task-demo-investor-001",
            "ws-demo-investor",
            "Tier 1 shortlist is ready. Draft investor-specific outreach angles and cold emails next.",
            "-3 hours -54 minutes",
        ),
    ]
    for (from_a, to_a, msg_type, task_id, ws_id, content, offset) in investor_messages:
        if not _row_exists(conn, "agent_messages",
                           "task_id=? AND from_agent=? AND to_agent=? AND msg_type=?",
                           (task_id, from_a, to_a, msg_type)):
            conn.execute(
                f"""INSERT INTO agent_messages
                   (from_agent, to_agent, msg_type, content, task_id, workspace_id,
                    status, created_at)
                   VALUES (?,?,?,?,?,?,'read',datetime('now', ?))""",
                (from_a, to_a, msg_type, content, task_id, ws_id, offset),
            )
            counts["messages"] += 1

    # ── 9. Agent messages (chain / activity feed) ─────────────────────────────
    for (from_a, to_a, msg_type, task_id, ws_id, content, offset) in _MESSAGES:
        if not _row_exists(conn, "agent_messages",
                           "task_id=? AND from_agent=? AND to_agent=?",
                           (task_id, from_a, to_a)):
            conn.execute(
                f"""INSERT INTO agent_messages
                   (from_agent, to_agent, msg_type, content, task_id, workspace_id,
                    status, created_at)
                   VALUES (?,?,?,?,?,?,'read',datetime('now', ?))""",
                (from_a, to_a, msg_type, content, task_id, ws_id, offset),
            )
            counts["messages"] += 1

    # ── 10. User preferences ──────────────────────────────────────────────────
    prefs = [
        # Marketing team
        ("ws-demo-marketing", "topic_preference",    "topics",
         "AI agents, agentic infrastructure, robotics"),
        ("ws-demo-marketing", "platform",            "target",   "linkedin"),
        ("ws-demo-marketing", "tone_preference",     "tone",     "bold_opinionated"),
        ("ws-demo-marketing", "preferred_source",    "arxiv.org",     "arxiv.org"),
        ("ws-demo-marketing", "preferred_source",    "github.com",    "github.com"),
        ("ws-demo-marketing", "preferred_source",    "bain.com",      "bain.com"),
        ("ws-demo-marketing", "preferred_source",    "mckinsey.com",  "mckinsey.com"),
        ("ws-demo-marketing", "preferred_source",    "a16z.com",      "a16z.com"),
        # Marketing voice samples (2 linkedin samples)
        ("ws-demo-marketing", "voice_sample:linkedin", "sample_1",
         "Most people think the bottleneck in building AI products is the model quality. "
         "It's not. It's the feedback loop. The teams shipping the best AI products right now "
         "have obsessive evaluation pipelines — not the best base models. Speed of iteration "
         "beats quality of starting point, every time. The moat is the loop, not the model."),
        ("ws-demo-marketing", "voice_sample:linkedin", "sample_2",
         "The most underrated skill in tech right now: knowing what NOT to automate. "
         "Everyone is racing to add AI to everything. The builders who will win are the ones "
         "who can identify the 20% of decisions that still need a human — and build systems "
         "that route to them correctly. Automation without escalation is just technical debt "
         "with a better marketing pitch."),
        # Sales team
        ("ws-demo-sales", "topic_preference",   "topics",
         "enterprise SaaS, Series A startups, AI infrastructure"),
        ("ws-demo-sales", "platform",           "target",   "email"),
        ("ws-demo-sales", "preferred_source",   "pitchbook.com",  "pitchbook.com"),
        ("ws-demo-sales", "preferred_source",   "techcrunch.com", "techcrunch.com"),
        ("ws-demo-sales", "preferred_source",   "bain.com",       "bain.com"),
        ("ws-demo-sales", "preferred_source",   "linkedin.com",   "linkedin.com"),
        # Investor team
        ("ws-demo-investor", "topic_preference", "topics",
         "AI infrastructure, enterprise software, data infrastructure, vertical SaaS"),
        ("ws-demo-investor", "platform",         "target", "briefing"),
        ("ws-demo-investor", "preferred_source", "pitchbook.com", "pitchbook.com"),
        ("ws-demo-investor", "preferred_source", "techcrunch.com", "techcrunch.com"),
        ("ws-demo-investor", "preferred_source", "nvidia.com", "nvidia.com"),
        ("ws-demo-investor", "preferred_source", "arxiv.org", "arxiv.org"),
    ]
    for ws_id, pref_type, key, value in prefs:
        conn.execute(
            """INSERT OR REPLACE INTO user_preferences
               (workspace_id, preference_type, key, value, updated_at)
               VALUES (?,?,?,?,datetime('now'))""",
            (ws_id, pref_type, key, value),
        )
        counts["prefs"] += 1

    conn.commit()
    return counts


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if not DB_PATH.exists():
        print(f"ERROR: database not found at {DB_PATH}", file=sys.stderr)
        print("Run the kernel migrations first: "
              "PYTHONPATH=. python3 -c 'from kernel.api import ApexKernel; ApexKernel()'")
        sys.exit(1)

    with _connect() as conn:
        c = seed(conn)

    print(
        f"Seeded {c['teams']} teams, {c['tasks']} tasks, "
        f"{c['evidence']} evidence entries, "
        f"{c['reviews_pending']} reviews pending approval"
    )
    print(
        f"  ↳ {c['sessions']} agent sessions  |  "
        f"{c['evals']} eval dimensions  |  "
        f"{c['messages']} chain messages  |  "
        f"{c['prefs']} preferences"
    )


if __name__ == "__main__":
    main()
