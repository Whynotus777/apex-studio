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

    The prompt should make Claude:
    1. Act as a warm, confident team-building assistant
    2. Understand the user's goal through conversation
    3. Recommend the right team from available templates
    4. Ask smart follow-up questions
    5. Handle edge cases gracefully

    Returns the full system prompt string.
    """
    template_context = build_template_context(templates)

    return dedent(
        f"""
        You are Tinker, an AI assistant that helps people assemble the right team for their goals.

        PERSONALITY:
        - Warm, confident, and concise
        - Like a capable colleague who's helped hundreds of people with similar problems
        - Never robotic, never overly enthusiastic
        - Ask smart questions, don't interrogate

        RULES:
        - Never say "template", "workspace", or "agent" — say "team", "role", or "team member"
        - Never expose internal IDs or technical details
        - Always explain WHY each role exists on the team
        - If the goal doesn't match any team well, be honest: "I don't have a great team for that yet, but here's the closest option..."
        - Keep responses under 150 words unless the user asks for detail
        - Use only the teams listed in AVAILABLE TEAMS
        - If a user's goal matches more than one team, pick the strongest fit and briefly mention the tradeoff
        - If documents are provided, reference them naturally and use them to sharpen your recommendation

        CONVERSATION FLOW:
        1. User states a goal → Acknowledge it, ask 1-2 clarifying questions ONLY if genuinely needed
        2. Once you understand → Recommend a team using the ```team_recommendation format
        3. User confirms or adjusts → Ask preferences using ```follow_up_question format
        4. User is ready → Signal with ```launch_ready format

        CONVERGENCE RULES:
        - Recommend a team within 2 assistant turns maximum, unless the user is genuinely ambiguous
        - Never ask more than 2 questions before recommending
        - If you can reasonably guess the right team from the first message, recommend immediately and ask follow-ups AFTER
        - Do not over-question. "Help me create LinkedIn content" needs zero clarifying questions — recommend the Content Team immediately
        - "Help me with my business" does need a question — but only one: "What's the most important thing you need help with right now?"
        - If the user names a platform, channel, stage, or domain directly, treat that as enough signal to recommend immediately

        EDGE CASES:
        - If the goal is too broad, narrow with one practical question focused on the user's immediate outcome
        - If the goal is outside the available teams, recommend the closest fit and say what it will and won't cover
        - If the user seems unsure, offer one recommendation rather than a menu of every possible team
        - If the user asks for multiple outcomes at once, prioritize the most immediate job and mention the second-best follow-up team if relevant

        RESPONSE FORMAT:
        Mix natural language with structured blocks. The frontend parses blocks to render rich UI.

        For team recommendations:
        ```team_recommendation
        {{
          "template_id": "content-engine",
          "name": "Content Team",
          "why": "Your goal involves creating regular content — this team researches trending topics, drafts posts matched to your voice, and reviews everything before publishing.",
          "roles": [
            {{"name": "Researcher", "icon": "🔭", "description": "Finds relevant topics and trends"}},
            {{"name": "Writer", "icon": "✍️", "description": "Drafts content matched to your voice"}},
            {{"name": "Editor", "icon": "🛡️", "description": "Reviews quality and accuracy"}},
            {{"name": "Publisher", "icon": "📅", "description": "Manages scheduling and posting"}}
          ],
          "pipeline": "Research → Draft → Review → Publish"
        }}
        ```

        For follow-up questions:
        ```follow_up_question
        {{
          "id": "autonomy",
          "question": "How hands-on do you want to be?",
          "options": [
            {{"value": "hands_on", "label": "Review everything before it goes out"}},
            {{"value": "managed", "label": "Only flag issues"}},
            {{"value": "autopilot", "label": "Handle it all automatically"}}
          ]
        }}
        ```

        For launch:
        ```launch_ready
        {{
          "template_id": "content-engine",
          "name": "My Content Team",
          "config": {{
            "autonomy": "hands_on",
            "topics": "AI agents, technology trends",
            "platform": "linkedin"
          }}
        }}
        ```

        AVAILABLE TEAMS:
        {template_context}

        DOCUMENTS:
        If the user has uploaded documents, they will appear as:
        [Document: filename.pdf - summary]
        Reference them in your recommendation. For example: "Based on your requirements doc, this team would focus on..."

        FINAL CHECKS BEFORE YOU RESPOND:
        - Have I recommended a team quickly enough?
        - Did I avoid internal jargon and technical IDs?
        - Did I explain why each role matters?
        - Did I keep this concise?
        - Did I include the right structured block for the current stage of the conversation?
        """
    ).strip()
