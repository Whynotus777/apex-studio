# APEX Web App — Product Spec v1

## Core Principle
APEX is where you hire and manage AI teams. The web app IS the product — not a dashboard, not an admin panel. This is where non-technical users go from "I need help" to "my team is working."

## Design Philosophy
- **Teams, not templates.** Users hire teams, not configure workspaces.
- **Chat or browse.** Users pick their comfort level for entry.
- **Light visibility.** Users see their team members and chain progress. They don't see eval scores, agent_messages internals, or spawn logs.
- **Always recommending.** APEX always suggests the right team structure with explanations. Users adjust if they want.
- **Mobile-first responsive.** Works on phone browser. Native app later.

---

## Tech Stack

**Frontend:** Next.js + Tailwind CSS + shadcn/ui components
**Backend API:** FastAPI (Python) wrapping kernel/api.py
**Database:** Existing SQLite (kernel's DB) — no new database
**Auth:** Simple email/password to start, OAuth later
**Realtime:** Polling every 3s on active pages, SSE/websockets later
**Hosting:** Vercel (frontend) + any VPS (backend API) for now

### Critical Technical Requirements
1. **SQLite WAL mode** — `PRAGMA journal_mode=WAL;` on every DB connection. Required for concurrent reads (FastAPI polling) while agents write. Without this, `database is locked` errors will crash the API.
2. **Edit feedback loop** — When user edits a draft before approving, POST /api/approvals/{id}/approve accepts optional `edited_content`. If present, store the diff between AI draft and user edit as a learning signal via kernel/learning.py. Writer learns from every edit.
3. **Agent micro-states** — The existing `agent_status` table already tracks idle/active per agent. The 3s poll reads this directly. For V1 this is sufficient. V2 adds finer states (searching, drafting, reviewing).
4. **No direct DB access from frontend** — Every endpoint calls kernel/api.py methods. The API layer is a thin wrapper, not a new business logic layer.

---

## Pages

### 1. Landing / Home

Two entry points side by side:

**Left: Chat entry**
```
"What do you need help with?"
[text input]
```
User types: "I need help managing my company's social media"
APEX responds with a recommended team (see Team Builder flow below)

**Right: Browse teams**
Categories:
- Content & Marketing
- Sales & Outreach
- Research & Intelligence
- Engineering & Dev Ops
- Finance & Analysis
- Personal & Lifestyle

Each category shows 2-3 pre-built teams with one-line descriptions.

**Below: Your active teams** (if logged in)
- My Marketing Team — 3 tasks this week, 1 awaiting approval
- My Research Team — daily briefing delivered at 8am
- My Sales Team — 12 leads found this week

---

### 2. Team Builder (the core interaction)

Triggered by chat entry OR clicking a pre-built team.

**Step 1: APEX recommends a team**

```
Based on what you need, here's the team I'd build:

🔭 Researcher
  Finds trending topics and industry news daily
  WHY: Without research, your content is based on guesses

✍️ Content Creator  
  Drafts posts matched to your voice and platform
  WHY: This is your core output engine

🛡️ Quality Editor
  Reviews every piece for accuracy, tone, and authenticity
  WHY: Catches errors and AI-sounding language before publishing

📅 Publisher
  Schedules and posts to your connected platforms
  WHY: Consistency matters more than volume

📊 Performance Analyst
  Tracks what's working and feeds learnings back to the team
  WHY: Your team gets smarter over time

How they work together:
Research → Draft → Review → Your Approval → Publish → Analyze → Improve

[Launch this team]  [Adjust roles ↓]
```

**Step 2 (optional): Adjust roles**

User can:
- Remove a role (with warning if it breaks the chain)
- Add a role from the role library
- Add a custom role by describing it
- Reorder the chain
- Change any role's description or focus

**Step 3: Configure basics**

```
A few quick questions to get your team started:

What topics should they focus on?
[AI agents, robotics, technology trends          ]

What platforms are you publishing to?
[✓] LinkedIn  [✓] X/Twitter  [ ] TikTok  [ ] Instagram

How should the tone feel?
( ) Professional authority
(•) Bold and opinionated  
( ) Casual and conversational
( ) Educational and clear

How often should they produce content?
(•) Daily
( ) 3x per week
( ) Weekly

[Launch team →]
```

**Step 4: Team is live**

```
✅ Your Marketing Team is live!

Your team is already working on their first task.
You'll be notified when something needs your approval.

[Go to your team →]
```

---

### 3. Teams Overview (Dashboard)

Shows all active teams.

```
My Teams

📱 Marketing Team                    🟢 Active
   Last: LinkedIn post drafted       ⏳ 1 awaiting approval
   This week: 5 posts, 3 published
   [Open →]

🔍 Research Team                     🟢 Active  
   Last: Morning briefing delivered   ✅ All clear
   This week: 7 briefings
   [Open →]

💼 Sales Team                        🟡 Needs attention
   Last: 3 new leads found          ⏳ 2 outreach drafts to review
   [Open →]

[+ Hire a new team]
```

---

### 4. Team Detail Page

The main workspace view for one team. Three panels:

**Left panel: Team members**
```
Marketing Team

🔭 Researcher          idle
✍️ Content Creator     drafting...
🛡️ Quality Editor     waiting
📅 Publisher           scheduled
📊 Performance Analyst  analyzing
```

Click any team member to see their recent activity and chat with them.

**Center panel: Activity feed + current work**
```
Today
  9:00 AM  🔭 Researcher found 8 trending topics
  9:02 AM  ✍️ Creator drafting LinkedIn post...
  9:03 AM  ✍️ Creator finished draft
  9:03 AM  🛡️ Editor reviewing (3.8/5 → requesting revision)
  9:04 AM  ✍️ Creator revising...
  9:05 AM  🛡️ Editor approved (4.4/5)
  
  📬 1 post ready for your approval
```

**Right panel: Quick actions**
```
📬 Pending Approvals (1)
📊 This Week: 5 drafted, 3 published
⚙️ Team Settings
📈 Performance
```

---

### 5. Output Review Page (Most Important Page)

When user clicks "pending approval" — this is where they decide.

**Layout: two columns**

**Left: The output**
```
LinkedIn Post — Draft

Traditional SaaS is dying. Not because it's bad, 
but because it's no longer enough.

For a decade, we've been sold 'Software as a Service.' 
But the 'service' was often just a digital shovel—you 
still had to do the digging.

The era of the Agentic Organization is here, and it's 
shifting...

[full post, scrollable, editable inline]
```

**Right: Context**
```
Sources (15 verified)
• AI Agents Market Size — marketsandmarkets.com
• Will AI Disrupt SaaS — bain.com  
• NVIDIA GTC Announcements — nvidia.com

Quality Score: 4.4/5
✅ Accuracy: 5/5
✅ Grounding: 4/5
✅ Authenticity: 4/5
⚠️ Completeness: 4/5

Editor Feedback:
"Strong opening hook. Sources well-cited. 
Consider adding a specific data point in 
paragraph 3."

Chain: Research → Draft → Review → ✅ Revised → ✅ Approved
```

**Bottom: Actions**
```
[✅ Approve & Publish]  [✏️ Edit & Approve]  [🔄 Request Revision]  [❌ Reject]

Publishing to: LinkedIn (connected) + X (connected)
Schedule: Post now | Tomorrow 9am | Custom
```

**Platform preview tabs:**
```
[LinkedIn preview]  [X preview]  [TikTok script]
```

Each tab shows how the post would look on that platform.

---

### 6. Team Settings / Preferences

```
Marketing Team Settings

📋 Topics
  AI agents, agentic infrastructure, robotics, economics, tech trends
  [Edit]

📎 Preferred Sources  
  arxiv.org, github.com, mckinsey.com, economist.com, bain.com
  [Edit]

🎯 Platforms
  [✓] LinkedIn  [✓] X/Twitter  [ ] TikTok  [ ] Instagram

🗣️ Voice & Style
  LinkedIn: 2 samples imported  [Manage]
  X: 0 samples  [Add samples]
  
🎚️ Optimization Mode
  LinkedIn: Authority  [Change]
  X: Virality  [Change]

📅 Schedule
  Research: Daily at 8am
  Publish: Daily at 10am
  Performance review: Weekly on Monday

🔗 Connected Accounts
  LinkedIn: Connected as Abdul Manan  [Disconnect]
  X: Connected as @TinkerAI  [Disconnect]
  TikTok: Not connected  [Connect]
```

---

### 7. Performance / Analytics

```
Marketing Team — Last 30 Days

📊 Overview
  Posts drafted: 45
  Posts published: 32
  Avg quality score: 4.1/5
  Avg authenticity: 3.8/5

📈 Engagement (LinkedIn)
  Total impressions: 12,400
  Total likes: 340
  Total comments: 89
  Best post: "Traditional SaaS is dying..." (2,400 impressions)

🧠 What your team learned
  • Posts with contrarian opening hooks get 2.3x more comments
  • Tuesday 10am posts outperform Thursday posts by 40%
  • arxiv-sourced posts get more saves, McKinsey-sourced get more shares
  • Posts under 1200 characters perform 30% better

🔄 Auto-adjustments made
  • Writer now defaults to contrarian hooks (learned from top 5 posts)
  • Scheduler shifted Tuesday posts to 10am (was 9am)
  • Scout increased arxiv query weight
```

---

## API Endpoints (FastAPI wrapping kernel/api.py)

```
# Teams (maps to kernel workspaces + templates)
GET    /api/teams                    — list user's active teams
POST   /api/teams                    — hire a new team (launches template into workspace)
GET    /api/teams/{id}               — team detail (members, status, recent activity)
DELETE /api/teams/{id}               — archive team
PATCH  /api/teams/{id}               — update team settings

# Team Members (maps to kernel agents)
GET    /api/teams/{id}/members       — list team members with current status
POST   /api/teams/{id}/members       — add a member (creates agent in workspace)
DELETE /api/teams/{id}/members/{mid}  — remove a member

# Tasks / Missions
POST   /api/teams/{id}/tasks         — give team a mission (creates task + spawns starting agent)
GET    /api/teams/{id}/tasks         — list tasks with chain progress
GET    /api/tasks/{id}               — task detail with full chain status

# Approvals
GET    /api/approvals                — pending approvals across all teams
POST   /api/approvals/{id}/approve   — approve (optional: edited_content for learning)
POST   /api/approvals/{id}/reject    — reject
POST   /api/approvals/{id}/revise    — request revision with feedback

# Outputs / Drafts
GET    /api/tasks/{id}/output        — get the full draft from agent_sessions
GET    /api/tasks/{id}/evidence      — get sources used
GET    /api/tasks/{id}/reviews       — get critic scores and feedback
GET    /api/tasks/{id}/chain         — get chain progress (which agents ran, status of each)

# Preferences
GET    /api/teams/{id}/preferences   — get all preferences (topics, sources, platform, voice, optimization)
PUT    /api/teams/{id}/preferences   — update preferences
POST   /api/teams/{id}/voice         — add voice sample (with platform tag)
GET    /api/teams/{id}/voice         — get voice samples by platform

# Publishing
POST   /api/publish/linkedin         — publish to LinkedIn (requires connected account)
POST   /api/publish/x                — publish to X (requires user's API keys)
GET    /api/teams/{id}/published     — published history with engagement metrics

# Analytics
GET    /api/teams/{id}/analytics     — performance data (posts, engagement, scores)
GET    /api/teams/{id}/learnings     — what the team learned (pattern changes, auto-adjustments)

# Auth
POST   /api/auth/signup              — create account
POST   /api/auth/login               — login (returns JWT)
GET    /api/auth/me                  — current user

# Team Builder
POST   /api/builder/recommend        — given user description, return recommended team structure
POST   /api/builder/launch           — launch a custom team from builder config
GET    /api/builder/categories       — list team categories with pre-built options
GET    /api/builder/roles            — list all available roles for custom teams
```

---

## Parallel Build Tracks

### Track 1: FastAPI backend (wraps kernel)
Create `api/` directory with FastAPI app exposing all endpoints above.
Thin wrapper — every endpoint calls kernel/api.py methods.
No new business logic in the API layer.

### Track 2: Next.js frontend shell
Landing page, navigation, auth screens, teams overview.
Use shadcn/ui components. Tailwind. Mobile-first.

### Track 3: Team Builder (chat + browse)
The core onboarding flow. Chat interface that builds teams.
Browse view with categories and pre-built teams.

### Track 4: Team Detail + Output Review
The most important pages. Activity feed, pending approvals,
full draft review with sources and quality score.

### Track 5: Preferences + Analytics
Settings panel and performance dashboard.
Can be built last — functional but not critical for launch.
