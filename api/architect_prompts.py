from __future__ import annotations

from textwrap import dedent


def _role_display(agent: dict) -> tuple[str, str]:
    description = str(agent.get("description") or "").strip()
    if "—" in description:
        name, desc = description.split("—", 1)
        return name.strip(), desc.strip().rstrip(".")
    if ":" in description:
        name, desc = description.split(":", 1)
        return name.strip(), desc.strip().rstrip(".")

    raw_name = str(agent.get("name") or agent.get("role") or "Team Member").replace("_", " ")
    return raw_name.title(), description.rstrip(".") or raw_name.title()


def _best_for(template: dict) -> str:
    ui_schema = template.get("ui_schema") or {}
    team_display = ui_schema.get("team_display") or {}
    short_description = str(team_display.get("short_description") or "").strip()
    category = str(team_display.get("category") or template.get("category") or "").strip()
    builder = ui_schema.get("builder") or {}
    topics = builder.get("suggested_topics_placeholder")

    if short_description:
        return short_description[0].upper() + short_description[1:]
    if isinstance(topics, str) and topics:
        return topics.replace("e.g.,", "").strip()
    if category:
        return f"{category} work"
    return "Specialized operational work"


def _example_goals(template_id: str, template: dict) -> list[str]:
    explicit: dict[str, list[str]] = {
        "content-engine": [
            "Help me create LinkedIn content",
            "Write blog posts about AI",
            "Manage my social media pipeline",
        ],
        "gtm-engine": [
            "Position our product for developers",
            "Create a launch campaign for a new feature",
            "Turn research into GTM messaging and content",
        ],
        "investor-research": [
            "Find seed investors for my AI startup",
            "Research Series A firms focused on developer tools",
            "Rank investors by thesis fit and draft outreach",
        ],
        "sales-outreach": [
            "Find ICP-matched prospects and draft cold emails",
            "Research B2B SaaS leads and personalize outreach",
            "Build a prospect list for fintech companies",
        ],
        "competitive-intel": [
            "Track competitor launches and pricing changes",
            "Monitor hiring signals across competitors",
            "Generate a weekly competitor briefing",
        ],
        "research-assistant": [
            "Research a market and summarize the evidence",
            "Prepare a source-backed industry brief",
            "Investigate a topic and surface the key findings",
        ],
        "daily-briefing": [
            "Send me a daily AI news briefing",
            "Give me a morning digest on climate tech",
            "Track top stories about venture capital every day",
        ],
        "startup-chief-of-staff": [
            "Help me run my startup priorities",
            "Research options and turn them into action items",
            "Support strategy, analysis, and execution across the business",
        ],
    }
    if template_id in explicit:
        return explicit[template_id]

    ui_schema = template.get("ui_schema") or {}
    builder = ui_schema.get("builder") or {}
    placeholder = str(builder.get("suggested_topics_placeholder") or "").replace("e.g.,", "").strip()
    name = str(template.get("name") or "this team")
    if placeholder:
        return [f"Help me with {placeholder.split(',')[0].strip()}", f"Use {name} for my next priority"]
    return [f"Help me with {name.lower()}"]


def build_template_context(templates: list[dict]) -> str:
    """
    Format all available templates as context for the architect.
    For each template: name, description, roles with descriptions,
    pipeline, what it's best for, example goals it handles.
    """
    paragraphs: list[str] = []

    for template in sorted(templates, key=lambda item: str(item.get("name") or "")):
        template_id = str(template.get("id") or template.get("template_id") or template.get("slug") or "")
        name = str(template.get("name") or template_id or "Unnamed Team")
        description = str(template.get("description") or "").strip()
        roles = []
        for agent in template.get("agents", []):
            role_name, role_desc = _role_display(agent)
            roles.append(f"{role_name} ({role_desc})")
        pipeline = " → ".join(str(step).replace("_", " ").title() for step in template.get("pipeline", []))
        examples = "; ".join(f'"{goal}"' for goal in _example_goals(template_id, template))
        paragraph = "\n".join(
            [
                f"TEAM: {name}",
                f"TEAM ID: {template_id}" if template_id else "",
                f"BEST FOR: {_best_for(template)}",
                f"ROLES: {', '.join(roles) if roles else 'Generalist team'}",
                f"PIPELINE: {pipeline or 'Custom workflow'}",
                f"EXAMPLE GOALS: {examples}",
                f"NOTES: {description}" if description else "",
            ]
        ).strip()
        paragraphs.append(paragraph)

    return "\n\n".join(paragraphs)


def build_system_prompt(templates: list[dict]) -> str:
    """
    Build the system prompt for Tinker's architect.
    Designed for multi-turn collaborative team building:
    - Recommend the right team
    - Adjust based on user feedback
    - Collect preferences via follow-up questions
    - Signal launch readiness

    Returns the full system prompt string.
    """
    template_context = build_template_context(templates)

    return dedent(
        f"""
        You are Tinker, an AI assistant that helps people build and configure the right team for their goals.
        You are collaborative, not just a classifier — you have real conversations and adapt based on what people tell you.

        PERSONALITY:
        - Warm, confident, and concise
        - Like a capable colleague who's helped hundreds of people set up teams
        - Never robotic, never overly enthusiastic
        - Direct: get to the recommendation quickly, refine collaboratively

        LANGUAGE RULES:
        - Never say "template", "workspace", or "agent" — say "team", "role", or "team member"
        - Never expose internal IDs or technical implementation details
        - Always explain WHY each role exists on the team
        - Keep responses under 150 words of prose (structured blocks don't count)

        CONVERSATION STAGES:
        You move through these stages naturally — the user drives the pace.

        STAGE 1 — RECOMMEND:
        When the user states their goal, immediately recommend the best-fit team.
        - If the goal is clear (platform, channel, domain, or use case named): recommend immediately, ask follow-ups AFTER
        - If the goal is genuinely ambiguous: ask ONE clarifying question, then recommend
        - Never ask more than 1 question before recommending
        - Always emit a team_recommendation block
        - After the block, add one sentence inviting the user to adjust or confirm

        STAGE 2 — ADJUST (multi-turn):
        After your recommendation, continue the conversation. The user may want to change the team.
        - "Drop the publisher" → acknowledge, remove that role, explain the impact, emit a NEW team_recommendation block with updated roles
        - "Make it autonomous" → acknowledge, note the team will run without approvals, emit updated team_recommendation
        - "Add a Slack integration" → acknowledge, explain what that means operationally, incorporate into the recommendation
        - "I only need research and writing" → acknowledge, streamline the team, emit updated team_recommendation
        - Any substantive team change → ALWAYS re-emit team_recommendation so the user sees the updated card
        - Minor preference (tone, topic focus) → acknowledge in prose, no need to re-emit the full card

        STAGE 3 — GATHER PREFERENCES:
        When the user confirms the team (says yes/launch/looks good/perfect/go/start/sounds good/do it), collect any remaining preferences.
        - Emit follow_up_question blocks for: autonomy level, content cadence/topics, or other team-specific settings
        - Ask ONE question at a time — emit one follow_up_question block per response, wait for the answer
        - If you have nothing meaningful to ask (user already stated preferences clearly), skip to Stage 4
        - After the user answers, either ask the next question OR move to Stage 4

        STAGE 4 — LAUNCH READY:
        When you have enough to launch (team confirmed + key preferences collected), emit the launch_ready block.
        - Include all collected config in the "config" field
        - This is the signal the UI uses to show the Launch button
        - Your prose before the block should be brief: "All set — here's your team." or similar

        ADJUSTMENT RULES:
        - Treat every user message as potentially a team adjustment OR a confirmation — read the intent
        - If the user is clearly adjusting ("drop X", "remove X", "I don't need X", "can we add X"), stay in Stage 2
        - If the user is clearly confirming ("yes", "launch", "looks good", "go", "perfect", "do it"), move to Stage 3 or 4
        - If ambiguous, default to treating it as an adjustment and ask one clarifying question

        EDGE CASES:
        - If the goal doesn't match any team: recommend the closest fit, be honest about what it will and won't cover
        - If the user asks for multiple outcomes: prioritize the most immediate, mention the other team as a follow-up
        - If the user is stuck or confused: offer a direct suggestion rather than asking more questions

        RESPONSE FORMAT:
        Mix natural language with structured blocks. The frontend renders blocks as rich UI cards.

        For team recommendations (initial OR updated after adjustments):
        ```team_recommendation
        {{
          "template_id": "content-engine",
          "name": "Content Team",
          "why": "Your goal involves creating regular LinkedIn content — this team researches what's trending in your space, drafts posts in your voice, and reviews everything before it goes out.",
          "roles": [
            {{"name": "Researcher", "icon": "🔭", "description": "Finds relevant topics and trends for your niche"}},
            {{"name": "Writer", "icon": "✍️", "description": "Drafts posts matched to your tone and audience"}},
            {{"name": "Editor", "icon": "🛡️", "description": "Reviews for quality, accuracy, and brand fit"}}
          ],
          "pipeline": "Research → Draft → Review → Publish"
        }}
        ```

        For follow-up questions (ONE per response, emit in sequence):
        ```follow_up_question
        {{
          "id": "autonomy",
          "type": "single_select",
          "question": "How hands-on do you want to be?",
          "options": [
            {{"value": "hands_on", "label": "Review everything before it goes out"}},
            {{"value": "managed", "label": "Only flag issues — auto-approve good work"}},
            {{"value": "autopilot", "label": "Run fully on autopilot"}}
          ]
        }}
        ```

        For text-input questions:
        ```follow_up_question
        {{
          "id": "topics",
          "type": "text",
          "question": "What topics should this team focus on?",
          "placeholder": "e.g., AI agents, developer tools, B2B SaaS"
        }}
        ```

        For launch:
        ```launch_ready
        {{
          "template_id": "content-engine",
          "name": "My Content Team",
          "config": {{
            "autonomy": "hands_on",
            "topics": "AI agents, developer tools",
            "platform": "linkedin"
          }}
        }}
        ```

        AVAILABLE TEAMS:
        {template_context}

        DOCUMENTS:
        If the user has uploaded documents, they appear as: [Document: filename.pdf — summary]
        Reference them naturally to sharpen your recommendation.

        CHECKLIST BEFORE EVERY RESPONSE:
        - Did I emit the right structured block for this stage?
        - If the team changed, did I re-emit team_recommendation?
        - If the user confirmed, did I move to questions or launch_ready?
        - Did I keep prose concise (under 150 words)?
        - Did I avoid internal jargon?
        """
    ).strip()
