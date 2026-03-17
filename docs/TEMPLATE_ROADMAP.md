# APEX Template Roadmap

## Vision
APEX is the Replit for AI agent teams. Anyone can launch a purpose-built agent team for work or life — no terminals, no YAML, no Docker. Pick a template, give it a mission, trust the output.

## Business Model
People pay to eliminate expensive labor, not to automate cheap tasks. Every paid template must save the user at least 10x its monthly cost in time or money.

---

## Template Architecture
Templates are organized into Engine Families. Each engine is a category sharing similar agent patterns. Most templates reuse 3-5 agent archetypes:

- **Scout** — finds information, monitors sources, discovers signals
- **Analyst/Writer** — synthesizes, drafts, processes
- **Critic** — quality gates, evidence verification, scoring
- **Scheduler** — timing, reminders, publishing coordination
- **Operator/Apex** — orchestrates, escalates, routes

Different templates = different domain rules + different heartbeat schedules + different tool grants. Same kernel, same workspace isolation, same approval queue.

---

## Currently Built (3 templates)
1. **Startup Chief of Staff** — 5 agents (apex, scout, analyst, builder, critic)
2. **Research Assistant** — 3 agents (scout, analyst, critic)
3. **Content Engine** — 4 agents (scout, writer, critic, scheduler) ← flagship

---

## Paid Templates — "Worth Paying For"
These replace something that currently costs more than $50/month in time, money, or missed opportunities.

### Tier 1: Core Revenue ($49-149/month)

**Content Agency Engine**
- Replaces: freelance content writer ($500-2K/month) or 10+ hrs/month of founder time
- Agents: scout (trend finder), writer (drafts posts/newsletters/tweets), critic (quality gate), scheduler (publishing cadence)
- Key value: approve content from your phone, never write a LinkedIn post again
- Already built — needs polish

**Sales Research & Outreach Engine**
- Replaces: part-time SDR or 15-20 hrs/month of founder prospecting
- Agents: scout (ICP-matched lead discovery), analyst (company enrichment), writer (personalized outreach drafts), critic (tone/accuracy check)
- Key value: wake up to qualified prospects with drafted outreach ready to send

**Competitive Intelligence Engine**
- Replaces: expensive tools (Crayon $15K/yr, Klue $20K/yr) or the analyst you don't have
- Agents: scout (daily competitor monitoring), analyst (change detection, trend flagging), critic (source verification)
- Key value: weekly competitive briefings delivered automatically

**Engineering Ops Engine**
- Replaces: tech lead overhead on PR summaries, release notes, bug triage, sprint reporting
- Agents: scout (monitors repos/issues), analyst (summarizes changes), writer (drafts release notes), critic (accuracy check)
- Key value: Monday morning engineering digest without the Sunday night prep

**GTM (Go-To-Market) Engine**
- Replaces: fractional CMO ($5-15K/month) or founder doing all GTM alone
- Agents: scout (market research, competitor monitoring, channel analysis), strategist (positioning, messaging, campaign planning), writer (landing pages, ads, emails, social posts), critic (brand consistency, claim verification), publisher (schedules and posts across channels)
- Key value: full marketing operations from research to published content, coordinated across channels
- **Priority: use this engine to bring APEX itself to market** — dogfood the product
- Combines Content Engine + Sales Engine + Competitive Intelligence into one coordinated team

### Tier 2: Premium Verticals ($199-499/month)

**PE Deal Analysis Engine**
- Replaces: junior analyst time ($200K/yr salary) on CIM review and memo drafting
- Agents: scout (CIM intake/parsing), analyst (memo generation, risk flagging, deal scoring), critic (quality gate, source verification)
- Key value: first-pass investment memo in 10 minutes vs 8 hours
- Abdul's domain expertise (Meridian)

**Recruiting Pipeline Engine**
- Replaces: sourcing hours ($50-100/hr recruiter time)
- Agents: scout (candidate discovery matching requirements), analyst (resume screening), writer (personalized outreach), critic (bias/quality check)
- Key value: qualified candidate shortlist with drafted outreach

**Financial Intelligence Engine**
- Replaces: manual portfolio monitoring, spending analysis, financial reporting
- Agents: scout (monitors accounts/markets), analyst (flags anomalies, generates insights), critic (verifies claims)
- Key value: proactive financial alerts and weekly digest

---

## Free Tier — Onboarding Hooks
These get people using the platform, understanding how agents work, then upgrading to paid templates. They are NOT the revenue model.

- **Daily Inspiration Agent** — Quran verse, motivational quote, or daily reflection. One agent, one cron. Simplest possible template.
- **Habit Tracker Agent** — configurable daily check-ins with accountability
- **Simple Research Agent** — limited searches per month, proves the quality of grounded research
- **Personal Digest Agent** — morning briefing from your chosen topics

Why these exist: they're the "Hello World" of APEX. The user thinks "if the free agent is this good, what would a full Content Engine do?" That's the conversion path.

---

## Custom Agent Builder — "Build Your Own"

The platform should support three paths to custom agents, ordered by complexity:

### Path 1: Describe It (No-Code)
The user describes what they want in natural language:
> "I want an agent that monitors Hacker News for posts about AI agents, summarizes the top 5 daily, and sends them to my Telegram at 8am"

APEX generates the template manifest, creates the agent team, configures rules, and launches it. The user approves the configuration and it's running.

This is the "Replit moment" — describe what you want, get something running.

**How it works technically:**
- Apex orchestrator agent reads the user's description
- Maps it to the closest template pattern (this is a 1-agent scout + scheduler pattern)
- Generates agent.json, constraints, heartbeat schedule
- Presents the config for user approval
- Launches the workspace

### Path 2: Remix a Template (Low-Code)
The user starts from an existing template and customizes:
- Change the domain (Content Engine but for healthcare industry)
- Add/remove agents (drop the scheduler, add a second writer)
- Adjust rules (more aggressive tone, different source preferences)
- Change heartbeat schedule (hourly instead of daily)

This is like forking a Replit project. You get a working starting point and tweak it.

### Path 3: Build from Scratch (Pro/Developer)
For power users who want full control:
- Define agents with AGENTS.md, constraints, and tool grants
- Write custom hard-rules and anti-patterns
- Configure inter-agent messaging allowlists
- Set custom heartbeats and approval gates
- Publish to the template marketplace

This is the developer path — full flexibility, compatible with skills.sh ecosystem (Phase 4).

### Path 4: Import / Discover Existing Agents
APEX can discover and import agents from the broader ecosystem:
- **Import from OpenClaw skills registry** — 13K+ community skills, adapt as APEX agent configs
- **Import from skills.sh** — convert skills into APEX-compatible agent templates
- **Community marketplace** — browse and install templates other APEX users published
- **Agent scraping/discovery** — when the ecosystem matures, automatically suggest relevant community agents based on user's described goal

The key principle: APEX shouldn't be a walled garden. If someone built a great GitHub analysis skill for OpenClaw, you should be able to run it as an APEX agent with quality gates and approval workflows wrapped around it.

---

## Template Marketplace (Phase 4)
Once custom agents work well:
- Users publish templates they built
- Rating system based on usage and satisfaction
- Revenue share: creator gets 30% of subscription tied to their template
- Categories mirror the engine families
- Featured templates curated by APEX team
- One-click install of full agent teams

---

## Build Order

### Now
1. Polish Content Engine until it's magical
2. Fix evidence verification, approval buttons, revision loop

### Next Sprint
3. **GTM Engine** — highest strategic priority. Use it to bring APEX to market. Combines content + sales + competitive intel. Dogfood the product.
4. Thin web UI — the "Replit moment" (template picker → launch → operate → approve)

### After That
5. Sales Research & Outreach Engine
6. "Describe It" custom agent builder (Path 1)
7. PE Deal Analysis Engine (leverages Meridian domain expertise)
8. Competitive Intelligence Engine
9. Template remixing (Path 2)

### Phase 3: Data Layer + Integrations
9. **Airweave integration** — context retrieval layer that lets agents search the user's own data (Google Drive, Notion, Slack, CRM, Stripe, etc.) via a single API. Replaces building individual connectors for every SaaS tool. Enables: Sales Engine querying the user's CRM, PE Engine ingesting CIMs from Google Drive, Content Engine pulling from the user's content library. This is what makes agents work on YOUR data, not just web search.
10. **One-click OAuth integrations** — Adaptive.ai validated this is table stakes. Users connect Gmail, Slack, Sheets, etc. with one click. Agents get scoped access. No API keys exposed to users.

### Phase 4: Platform & Ecosystem
11. Developer agent builder (Path 3)
12. Import/discovery from ecosystem (Path 4)
13. Template marketplace with revenue share
14. Skills ecosystem compatibility (skills.sh, OpenClaw skills)
15. Skill graphs for deep vertical templates

---

## Pricing Framework

| Tier | Price | What You Get |
|------|-------|-------------|
| Free | $0 | 1 workspace, 1 simple agent (daily digest, habit tracker), limited searches |
| Starter | $29/mo | 1 workspace, 1 full template (Content Engine), 100 tasks/month |
| Pro | $49/mo | 3 workspaces, all templates, 500 tasks/month, priority model routing |
| Team | $149/mo | 10 workspaces, custom agents (Path 1-2), 2000 tasks/month, team members |
| Enterprise | Custom | Unlimited, self-hosted option, Path 3 builder, SSO, audit logs |

Users never see API keys. Budget primitives enforce per-user limits. Model costs absorbed into subscription.

---

## Key Principle
The platform scales by adding templates and enabling custom agents, not by adding infrastructure. Every new template is just a JSON manifest + agent rules + domain knowledge. The kernel stays the same.

One polished flagship (Content Engine) + clear paid value (saves you $500+/month) + custom builder (describe what you want) = a fundable, scalable platform.

---

## Agent Creation UX — "You're Hiring a Team"

The agent creation flow IS the product. Everything else is machinery. The user experience of building and configuring their agent team is what they actually interact with.

### Core Metaphor
Users aren't creating "agents" or "workspaces." They're hiring a team. The UX language should reflect this: team members, roles, capabilities, workflow.

### Entry Flow

**Step 1: What do you need help with?**
- Content & Marketing
- Sales & Outreach
- Research & Intelligence
- Engineering & Dev Ops
- Finance & Analysis
- Custom — describe what you need

**Step 2: See your recommended team**
Each team member shown with:
- Role name and icon
- One-line description of what they do
- "Why" explanation — why this role exists on the team
- Visual workflow showing how they connect (Scout → Writer → Editor → Publisher)

**Step 3: Launch or Customize**
- [Launch this team] — one click, agents running in 60 seconds
- [Customize →] — add/remove/adjust roles before launching

### Customization Options

**Add a role:**
- Browse role library (pre-built roles with descriptions, capabilities, example outputs)
- Custom role — describe in natural language, APEX generates the config
- Smart recommendations: "You added SEO Specialist. We recommend placing them between Writer and Editor. Grant web_search access. Run after Writer completes."

**Remove a role:**
- Click ✕ on any team member
- APEX warns if removal breaks the workflow chain ("Removing Editor means drafts won't be reviewed before publishing. Are you sure?")

**Adjust a role:**
- Change name, description, tone
- Adjust schedule (daily at 8am, after every task, hourly)
- Configure approval requirements (always approve, auto-approve if Critic score > 4.0, never approve automatically)
- Technical users: edit constraints directly (hard-rules, soft-preferences, anti-patterns)

### Role Library
Individual agent roles that can be mixed and matched across any team:

**Research roles:** Trend Scout, Deep Researcher, Competitor Monitor, Source Verifier
**Content roles:** Writer, Editor, SEO Optimizer, Social Media Manager, Newsletter Curator
**Sales roles:** Lead Finder, Company Enricher, Outreach Drafter, Follow-up Manager
**Engineering roles:** Code Reviewer, Bug Triager, Release Notes Writer, Incident Responder, ML Engineer
**Finance roles:** Market Monitor, Expense Tracker, Report Generator, Risk Flagger
**Operations roles:** Meeting Summarizer, Task Prioritizer, Status Reporter, Scheduler
**Quality roles:** Quality Editor (Critic), Fact Checker, Compliance Reviewer

Each role is a reusable agent config (AGENTS.md + constraints + tool grants). Teams are compositions of roles with defined workflow chains.

### "Explain My Team" Feature
At any point, user can ask "explain my team" and see:
- Plain English description of what each agent does
- Visual workflow chain
- Performance stats (last week: Scout found 12 topics, Writer drafted 8, Editor approved 5, you published 3)
- Cost breakdown (estimated tasks/month, model costs absorbed into subscription)

### Two User Paths

**Non-technical (default):**
Pick category → see recommended team with explanations → launch or lightly customize → operate from approval inbox

**Technical (opt-in):**
Start from any template or blank → add/remove/customize any role → edit agent rules directly → configure tool grants, heartbeats, approval gates → set inter-agent messaging rules → export/import as JSON

### Smart Defaults
- Every template comes with "why" explanations for each role
- Adding a role triggers recommendations for placement, tools, and schedule
- Removing a critical role triggers a warning
- APEX suggests team compositions based on the user's described goal
- Workflow chain is always visible and editable

---

## Media Generation — Tool Adapters

Agents should produce rich media, not just text. Video, images, and audio are tool adapters like web_search — the agent requests generation with a prompt, the adapter calls the backend, stores the result, and returns it for approval.

### Video Generation (`video_gen` tool)
| Backend | Cost | Quality | Notes |
|---|---|---|---|
| Local (Wan2.1 on 5090) | Free | Good short clips | Minutes to generate |
| Nano Banana 2 (Gemini 3.1 Flash Image) | Cheap | Great | Google API |
| Sora (OpenAI) | Expensive | Best quality | Rate limited |
| Runway Gen-4 | Mid-range | Strong | Good for marketing |
| Kling | Cheap | Good | Fast |

### Image Generation (`image_gen` tool)
| Backend | Cost | Notes |
|---|---|---|
| Nano Banana 2 | Cheap | Native Gemini image gen |
| DALL-E 3 / GPT Image | Mid-range | OpenAI API |
| Flux (local on 5090) | Free | Good quality |

### Audio/Voice (`audio_gen` tool)
| Backend | Cost | Notes |
|---|---|---|
| ElevenLabs | Mid-range | Best voice quality, cloning |
| Gemini TTS | Cheap | Good narration |
| Local TTS (Coqui/Bark on 5090) | Free | Decent |

### Content workflow with media:
1. Writer drafts post + generates a `media_prompt`
2. `image_gen` or `video_gen` tool creates the asset
3. Critic reviews text (media review is human-only for now)
4. Approval card shows draft + media preview + approve/reject

### Advanced: Producer Role
For heavy media production, add a **Producer** agent that takes Writer's script, generates video/images/thumbnails, handles format adaptation (square for Instagram, vertical for TikTok, landscape for YouTube).

### Implementation path:
- **Phase 2**: Image generation adapter (cheapest, most useful for social content)
- **Phase 3**: Video generation + Producer role
- **Phase 4**: Audio/voice, format adaptation

### Cost Gating
Video generation is expensive. Default behavior:
- Image generation: included in Pro tier and above
- Video generation: blocked behind upgrade button until user explicitly enables it
- When user clicks "Enable Video" → show cost estimate per video → require explicit opt-in
- Budget primitive enforces spend limits — agent cannot generate video if monthly video budget is exhausted
- Free/Starter tiers: no video. Pro: 10 videos/month. Team: 50 videos/month. Enterprise: unlimited.

### Social Publishing Flow
Agents can post directly to social platforms via API after approval. Three modes per channel:

1. **Auto-publish after my approval** (default) — Critic reviews → operator taps ✅ Approve → agent posts via API automatically
2. **Auto-publish if Critic score > 4.0** (power users) — high-scoring content publishes without human approval. Operator gets notified after.
3. **Prepare only, I'll post manually** (cautious) — agent drafts and queues, operator copies and posts themselves

### OAuth Model — Users Connect Their Own Accounts
Users authenticate with THEIR credentials via OAuth2. API calls happen on THEIR token, against THEIR rate limits. APEX never holds a master account that posts on behalf of everyone.

This means:
- **Rate limits are per-user, not per-APEX.** Each user gets their own 15 TikTok posts/day, their own LinkedIn quota. 1,000 APEX users don't share one pool.
- **API costs are zero for APEX on most platforms.** Users OAuth into APEX's developer app with their own accounts. Free tiers apply per-user.
- **No cost stacking.** User 1's posting doesn't eat into User 2's quota.

Onboarding flow:
> "Connect your platforms"
> [Connect LinkedIn] → OAuth popup → user logs in → APEX stores their encrypted token
> [Connect TikTok] → OAuth popup → same
> [Connect X] → user enters their own API key (guided setup)
> "Your Content Engine can now publish directly after you approve each post."

Tokens encrypted at rest, auto-refreshed, revocable anytime by user.

### X (Twitter) — Bring Your Own API Key
X is the one platform where the developer app gets charged, not the user's token. To avoid APEX absorbing per-user X costs, users provide their own X API key.

Flow:
> "To publish to X, you'll need your own API key (free tier available)."
> [Get my X API key — step by step guide]
> 1. Go to developer.x.com and sign in with your X account
> 2. Click "Sign up for Free Account"
> 3. Describe your use case: "Personal content publishing via third-party app"
> 4. Accept the terms
> 5. Create a new project and app
> 6. Copy your API Key, API Secret, Access Token, and Access Secret
> 7. Paste them into APEX settings
>
> Takes ~5 minutes. Free tier gives you ~17 posts/day.

For non-technical users, APEX provides a visual walkthrough with screenshots. The guide should feel as simple as connecting a WiFi password.

### Full Platform Cost & Limits Matrix

| Platform | Cost to APEX | Cost to User | Posts/Day | Auth Method | Auto-Publish |
|---|---|---|---|---|---|
| **LinkedIn** | $0 | $0 | ~100 req/day | OAuth2 (user's account) | ✅ |
| **X (Twitter)** | $0 | $0 (free tier) | ~17 | User's own API key | ✅ |
| **TikTok** | $0 | $0 | 15 | OAuth2 (user's account) | ✅ (after APEX audit) |
| **Instagram** | $0 | $0 | 25 | OAuth2 (business account) | ✅ |
| **Facebook** | $0 | $0 | ~200 req/hr | OAuth2 (user's page) | ✅ |
| **Reddit** | $0 | $0 (personal) | Variable | OAuth2 | ✅ |
| **Email (SendGrid)** | ~$0.001/email | $0 | 100/day free | APEX account | ✅ |
| **SMS (Twilio)** | $0.008/msg | $0 | Unlimited | APEX account | ✅ |
| **WordPress** | $0 | $0 | Unlimited | User's REST API | ✅ |
| **Medium** | $0 | $0 | 1/30sec | OAuth2 | ✅ |

**Who pays for what:**
| Cost | Who Pays |
|---|---|
| Model inference (Gemini Flash) | APEX — absorbed into subscription |
| Web search | APEX — absorbed into subscription |
| Social publishing (LinkedIn, TikTok, IG, FB) | User's own OAuth token — free |
| X publishing | User's own API key — free tier |
| Email sending | APEX — very cheap, absorbed |
| SMS alerts | APEX — pass through or absorbed at scale |
| Image generation | APEX — tiered, gated behind upgrade |
| Video generation | APEX — tiered, gated behind upgrade |

Bottom line: the only meaningful costs APEX absorbs are model inference and media generation. All social publishing rides on the user's own free API access.

---

## Multi-Channel Support

Telegram is the MVP interface but NOT the product. Users operate through whatever channel they prefer.

### Operator Channels (control and monitor teams)
| Channel | Priority | Status | Notes |
|---|---|---|---|
| **Web App** | P0 | Planned | The "Replit moment." This IS the product. |
| **Telegram** | P0 | ✅ Live | Current MVP. Keep as supported channel. |
| **WhatsApp** | P1 | Planned | Largest messaging platform globally. WhatsApp Business API. |
| **SMS** | P1 | Planned | Universal reach. Approvals, alerts. Twilio. |
| **Email** | P1 | Planned | "Reply APPROVE to publish." Weekly digests. SendGrid/SES. |
| **Slack** | P1 | Planned | Workplace teams. Approvals in threads. |
| **Discord** | P2 | Planned | Community/creator audiences. |
| **iOS / Android** | P2 | Planned | Native mobile. Push notifications for approvals. |
| **Microsoft Teams** | P3 | Planned | Enterprise requirement. |
| **Voice (phone)** | P3 | Planned | "Call your agent." Twilio Voice + Gemini Live. |

### Output Delivery Channels (where agents send finished work)
| Channel | Use Case |
|---|---|
| Email | Reports, digests, research briefs |
| LinkedIn | Published posts (API or manual copy) |
| Twitter/X | Published tweets |
| Google Docs/Drive | Long-form content, files |
| Notion | Tasks, research, notes |
| Slack | Team updates, summaries |
| WhatsApp / SMS | Notifications, reminders |
| Google Sheets | Data outputs, lead lists |
| CRM (HubSpot/Salesforce) | Lead data, outreach logs |
| Calendar | Scheduled events, reminders |
| Webhook | Custom — send output anywhere |

### Architecture
Each channel is an adapter in `adapters/`. The kernel is channel-agnostic — adding a new channel means writing an adapter that translates between the channel's API and kernel methods.

### User Onboarding Preference
During onboarding: "How do you want to interact with your team?" and "Where should finished work be delivered?" Users pick channels, APEX configures adapters.

### Agent Communication — Direct Chat & Team Channel (Phase 2/3)
Beyond the mission-based task flow, users should be able to communicate directly with their agents.

**1:1 Chat with individual agents:**
- Open Writer → "use a more aggressive tone going forward" → Writer stores as preference
- Open Scout → "stop using Forbes, focus on primary research" → Scout updates soft-preferences
- These are preference-setting conversations, not task execution
- User can flag which agents they want direct communication with during team setup
- Default: Critic (for feedback discussion) and Writer (for tone/style guidance)
- Power users: all agents accessible

**Group Chat (Team Channel):**
- Shows all inter-agent messages in one readable stream
- User sees the full chain: Scout → Writer "here are 5 trending topics", Writer → Critic "draft ready", Critic → Writer "revise: weak opening"
- User can interject at any point: "Writer, lead with a statistic not a question"
- Basically a human-readable view of the existing `agent_messages` table
- Team builder / operator can track everything happening across the team in real time

**Agent communication preferences (set during team creation):**
> "Which team members do you want to chat with directly?"
> - ✅ Writer (style and tone guidance)
> - ✅ Critic (discuss feedback)
> - ☐ Scout (adjust research focus)
> - ☐ Scheduler (change publishing cadence)
>
> "Do you want to see the team channel?"
> - ✅ Yes — show me all inter-agent messages
> - ☐ No — just show me final outputs and approvals

**Technical implementation:**
- 1:1 chats are new message types in `agent_messages` table with `from_user=true`
- Agent receives user message in next spawn's inbox, processes it as a preference update or direct instruction
- Group chat is a read-only view of all `agent_messages` for the workspace, with user messages interleaved
- Web UI: chat panel per agent + team channel tab
- Telegram: `/chat <agent>` command for 1:1, `/team <workspace>` for group view

**When to build:** After web UI exists. Chat in Telegram is clunky; chat in a web app is natural. This is a Phase 2/3 feature that makes the product feel like managing a real team.

### Implementation Path
- **Now**: Telegram (built) + Web App (next priority)
- **Phase 2**: Email + SMS + WhatsApp
- **Phase 3**: Slack + LinkedIn/Twitter publishing + Google Drive
- **Phase 4**: Discord, native apps, voice, Teams

---

Three layers of learning, each preserving user data privacy.

### Layer 1: Individual Agent Learning (Per Workspace)
Agents accumulate preferences and patterns within a user's workspace.

**Source preferences (Scout):**
- User sets preferred sources: arxiv, GitHub, McKinsey, Bain, Economist
- Scout prioritizes these in search queries
- Over time, tracks which sources get approved vs rejected and auto-adjusts

**Voice & style (Writer):**
- User imports 5-10 of their best posts per platform → stored as voice samples
- Edit feedback loop: diff between Writer's draft and user's edit stored as style correction
- Explicit preferences: tone, length, hook style, hashtag usage, emoji patterns
- After 10 edits, Writer has a strong model of user's preferences

**Platform-specific optimization:**
Every platform has different rules for what works:

| | LinkedIn | X (Twitter) | TikTok | Instagram |
|---|---|---|---|---|
| Format | Long-form, carousels | Short tweets, threads | 15-60s video scripts | Captions + visual concepts |
| Tone | Professional authority | Sharp, provocative | Casual educational | Visual-first, aspirational |
| Length | 1000-2000 chars | 280 per tweet | 50-150 word scripts | Under 300 chars |
| Hook | Contrarian stat | Hot take | Visual hook in 0.5s | Visual-first |
| Optimize | Comments, saves | Reposts, replies | Watch time, shares | Saves, reach |
| Frequency | 1-2/day | 3-5/day | 1-2/day | 1-2/day |

Each workspace stores platform profiles with format rules, tone, and optimization mode. Writer gets platform-specific instructions injected into prompt. Critic adjusts scoring rubric per platform (TikTok Critic weighs hook strength higher, LinkedIn Critic weighs depth of insight higher).

**Performance feedback loop:**
After posting, track engagement via platform APIs (user's OAuth token). Each post gets a performance score relative to user's baseline. System extracts patterns from top performers: topic, structure, hook style, sources cited, posting time. Feeds back into Scout (source rankings), Writer (format preferences), and Critic (quality thresholds).

**Auto-experiment loop (Karpathy autoresearch pattern):**

Karpathy's autoresearch has three primitives: an editable asset (what the agent can change), a scalar metric (how we know if it improved), and a time-boxed cycle (fixed duration so experiments are comparable). Applied to APEX:

| Karpathy's Loop | APEX Equivalent |
|---|---|
| Editable asset (train.py) | Agent's soft-preferences.md + prompt patterns |
| Scalar metric (val_bpb) | Critic overall_score (out of 5.0) |
| Time-boxed cycle (5 min) | One task cycle (Scout → Writer → Critic) |
| Git commit on improvement | Preference record stored on approval |
| Revert on regression | Discard pattern on rejection |

**Passive loop (human in loop):**
- Writer drafts → Critic scores → user approves/rejects → preference committed or reverted

**Active loop (no human needed):**
- At 2 AM, Writer generates 3 variations of the same topic using different structures
- Critic scores all three independently
- Highest-scoring structure becomes the new default soft-preference
- This runs continuously — agents experiment overnight while the user sleeps
- Karpathy ran 100 ML experiments overnight; APEX runs 100 content experiments overnight

The bottleneck isn't the model — it's the agent's constraints and preferences. Autoresearch on those constraints is how agents genuinely get smarter over time.

**Storage:** preference records in workspace DB, read into agent prompts as soft preferences.

### Layer 2: Cross-Workspace Learning (Per User)
User-level preference store that spans all of a user's workspaces.

- Brand voice learned in Content Engine available to Sales Outreach Engine's Writer
- Source preferences from Research Assistant available to PE Deal Engine's Scout
- Communication style from one workspace applies everywhere

**What's shared across workspaces:** writing style, industry context, company details, preferred sources, tone preferences.
**What stays workspace-scoped:** task content, evidence, drafts, missions.

### Layer 3: Federated Intelligence (Across All Users)
Aggregate anonymous signals that improve templates for everyone. No user data crosses boundaries.

**What gets aggregated (anonymized):**
- Template-level Critic score distributions: "Content Engine Writer drafts score 15% higher when they lead with a question"
- Source domain reliability index: "bain.com passes Critic evidence check 94% of the time"
- Prompt pattern effectiveness: "agents with structured numbered evidence score 20% higher on grounding"
- Anti-pattern frequencies: "citing >3 sources per paragraph triggers Critic completeness flags 80% of the time"
- Search query pattern effectiveness: "adding 'site:mckinsey.com' to PE queries produces 3x higher grounding scores"

**What NEVER crosses user boundaries:**
- Task content, missions, or drafts
- Evidence URLs or search queries
- User preferences or brand voice
- Any workspace data

**Cross-template hypothesis propagation (Research DAG concept):**
- Sales Engine discovers prospect funding mentions improve response rates → propagates as hypothesis to Recruiting Engine
- Content Engine discovers 3-data-point posts perform best → propagates to Sales Engine email structure
- PE Engine discovers reading qualitative sections first improves analysis → propagates to Research Assistant

These are structural insights about what works, not user data. Like Waze: every route is private, aggregate traffic data improves routing for everyone.

### Implementation Path
- **Phase 2**: Approve/reject/edit feedback stored per workspace. Preference accumulation in prompts.
- **Phase 3**: User-level preference store. Auto-experiment loop (Writer tries variations, Critic scores, best pattern persists). Per-user only.
- **Phase 4**: Federated Research DAG. Anonymized quality signals across all users. Template-level auto-optimization. Cross-template hypothesis generation.

---

### Direct Competitors
- **Adaptive.ai** — the closest shipped product to APEX's vision. Polished web UI, 3-step onboarding, 20+ OAuth integrations, iOS app. Single-agent-per-workflow, no multi-agent teams, no Critic/quality gates. This is the UX benchmark.
- **Paperclip** — open-source agent orchestration with org charts, budgets, governance. Developer-focused (Node.js + React). 14K stars in first week. Closest on orchestration features but no non-technical UX.
- **Okara AI CMO** — vertical agent product for marketing. Proves the productized agent team model works. Marketing-only, not horizontal.

### Runtime Layer (don't compete, leverage)
- **OpenClaw** — 280K stars, dominant personal agent runtime. Consider building on top of.
- **OpenFang** — Rust-based Agent OS, 7 pre-built Hands. Potential future runtime swap.
- **ZeroClaw** — lightweight Rust runtime for edge. Interesting for low-resource deployments.

### Infrastructure to Integrate
- **Airweave** — context retrieval layer. Connects to 20+ data sources, exposes unified search API. Solves "agents accessing user's own data" problem. Phase 3 integration.
- **skills.sh** — CLI for managing agent skills across platforms. Phase 4 compatibility target.

### APEX Differentiation
What nobody else has:
1. **Multi-agent teams from templates** — coordinated Scout → Writer → Critic chains, not single agents
2. **Critic pipeline with evidence verification** — automated quality scoring, citation checking, trust override
3. **Non-technical UX vision** — "Replit for agent teams" where you pick a template and launch from your phone
4. **Horizontal platform** — same system runs Content Engine, Sales Ops, PE Analysis, or a custom agent you describe
