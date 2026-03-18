# Conversational Core Sprint — Make Tinker Feel Like One Intelligent System

**This sprint is successful if a user types a goal, has a natural conversation with Tinker, uploads a document, and launches a team — all without ever seeing a form, a template picker, or a configuration screen.**

**Sprint Goal:** Replace keyword matching with a real LLM conversation. Replace form-based onboarding with a chat flow. Add document ingestion so users can attach context. When this sprint lands, Tinker should feel like talking to a smart assistant that assembles the right team, not like configuring a workflow tool.

**Three capabilities, not six:**
1. LLM-powered architect (real conversation, not keyword matching)
2. Chat-native onboarding UI (messages, not forms)
3. Document ingestion (upload files that become team context)

**Technical approach:**
- Backend: Claude Sonnet via Anthropic API for the architect conversation. Streaming SSE for real-time responses.
- Frontend: Chat interface that displays messages, team recommendations, and follow-up questions inline.
- Documents: File upload → text extraction → stored as workspace context → injected into agent prompts.

---

## Architecture Decisions (all agents read this)

### CRITICAL GUARDRAILS
1. **Fast convergence:** The architect must recommend a team within 2 assistant turns unless the user is genuinely ambiguous. No polite consulting. Be decisive.
2. **Parsing failure is first-class:** If structured block parsing fails at any point, render plain assistant text and continue the conversation. The product NEVER dead-ends on a parse error.
3. **File types this sprint:** PDF, TXT, MD only. No DOCX. Less extraction weirdness.
4. **Document injection:** Prompt injection uses summary + capped excerpt (max 3000 chars), NOT full raw extraction. A sloppy PDF must not pollute agent context.
5. **Documents influence recommendation:** If a user uploads a doc during chat, the architect reads the summary and uses it to shape the team recommendation. Documents are context for BOTH the conversation and the post-launch agents.
6. **Offline fallback:** If ANTHROPIC_API_KEY is not set, the chat still works using the keyword-matching fallback from the existing architect. Non-streaming, single response. The product works without an API key.

### Strict Event Contract (ALL agents must use these exact shapes)

**SSE Event Types (backend → frontend):**
```
data: {"type": "text_delta", "content": "partial text..."}\n\n
data: {"type": "structured", "block_type": "team_recommendation", "data": {TeamRecommendation}}\n\n
data: {"type": "structured", "block_type": "follow_up_question", "data": {FollowUpQuestion}}\n\n
data: {"type": "structured", "block_type": "launch_ready", "data": {LaunchConfig}}\n\n
data: {"type": "session_state", "state": "recommending"}\n\n
data: {"type": "done"}\n\n
data: {"type": "error", "message": "string"}\n\n
```

**TeamRecommendation shape:**
```json
{
  "template_id": "content-engine",
  "name": "Content Team",
  "why": "string — 1-2 sentences explaining why this team fits",
  "roles": [
    {"name": "Researcher", "icon": "🔭", "description": "one-line description"}
  ],
  "pipeline": "Research → Draft → Review → Publish"
}
```

**FollowUpQuestion shape:**
```json
{
  "id": "autonomy",
  "question": "How hands-on do you want to be?",
  "options": [
    {"value": "hands_on", "label": "Review everything before it goes out"}
  ]
}
```

**LaunchConfig shape:**
```json
{
  "template_id": "content-engine",
  "name": "My Content Team",
  "config": {
    "autonomy": "hands_on",
    "topics": "AI agents, technology trends",
    "platform": "linkedin"
  }
}
```

**Document upload response shape:**
```json
{
  "id": 1,
  "filename": "requirements.pdf",
  "content_type": "application/pdf",
  "char_count": 4200,
  "summary": "A product requirements document for a customer analytics dashboard with 12 features..."
}
```

### Session States (explicit state machine)

Every chat session has a `status` field that follows this progression:

```
collecting → recommending → awaiting_confirmation → launch_ready → launched
                                                                  ↗
                                           (any state) → error → (recoverable)
```

- **collecting**: User is describing their goal. No recommendation made yet.
- **recommending**: Architect has enough context and is presenting a team.
- **awaiting_confirmation**: Team was recommended, waiting for user to confirm or adjust.
- **launch_ready**: User confirmed. Launch button is enabled. Config is complete.
- **launched**: Team was created. Session is done. workspace_id is set.
- **error**: Something failed. Show error message with retry option.

The frontend must respect these states:
- Launch button ONLY appears in `launch_ready` state
- Team recommendation card ONLY appears in `recommending` or later
- Follow-up questions ONLY appear in `awaiting_confirmation`

### Chat State Management
The conversation between the user and Tinker's architect lives in a `chat_sessions` table:
```sql
CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT DEFAULT 'default',
    messages TEXT NOT NULL DEFAULT '[]',  -- JSON array of {role, content, timestamp}
    recommended_template_id TEXT,
    status TEXT DEFAULT 'active',  -- active | launched | abandoned
    workspace_id TEXT,  -- set after team launches
    created_at TEXT NOT NULL,
    updated_at TEXT
);
```

Each message in the array: `{ "role": "user" | "assistant", "content": "string", "timestamp": "ISO", "metadata": {} }`

The metadata field can contain structured data the frontend uses to render rich elements (team recommendations, follow-up questions, document references).

### LLM System Prompt for Architect
The architect is Claude Sonnet with a system prompt that:
- Knows all available templates (injected as context)
- Understands Tinker's team model (roles, pipelines, review gates)
- Asks clarifying questions before recommending
- Responds with structured JSON blocks embedded in natural language
- Never mentions "templates" or "workspaces" — only "teams" and "roles"

### Document Storage
```sql
CREATE TABLE IF NOT EXISTS workspace_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT,           -- null during chat, set after launch
    chat_session_id TEXT,        -- set during chat, before launch
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,  -- application/pdf, text/plain, text/markdown, etc.
    extracted_text TEXT,         -- full text extraction
    summary TEXT,               -- LLM-generated 2-3 sentence summary
    uploaded_at TEXT NOT NULL
);
```

Documents uploaded during chat are linked to the chat session. When the team launches, they're re-linked to the workspace. Agents read the extracted_text via spawn_context.py.

### Streaming
The chat endpoint uses Server-Sent Events (SSE) so the frontend can show Tinker "typing" in real time. FastAPI supports SSE via `StreamingResponse` with `text/event-stream` content type.

---

## Agent 1 — LLM Architect Backend

Read CLAUDE.md, docs/WEBAPP_SPEC.md, docs/DESIGN_SPRINT.md, and api/main.py.

**Files you own:**
- api/main.py (add new endpoints — do not modify existing endpoints)
- api/architect.py (new — the architect conversation logic)
- api/requirements.txt (add `anthropic` if not present)
- db/schema.sql (append chat_sessions table)
- Do NOT modify: kernel/, templates/, ui/, scripts/

### New file: api/architect.py

This is the brain of the conversational flow. It manages the multi-turn conversation between the user and Tinker's architect.

```python
class TinkerArchitect:
    """
    LLM-powered conversation that understands user goals,
    recommends teams, asks follow-ups, and prepares launch config.
    """
    
    def __init__(self):
        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
        self.templates = self._load_all_templates()
    
    def _build_system_prompt(self) -> str:
        """
        System prompt that makes Claude act as Tinker's team architect.
        Includes: all available templates with their roles and descriptions,
        the team model explanation, response format instructions.
        """
    
    async def chat(self, session_id: str, user_message: str, documents: list = None) -> AsyncGenerator[str, None]:
        """
        Send a message in the conversation. Yields SSE events.
        
        The LLM should:
        1. If first message: understand the goal, ask 1-2 clarifying questions
        2. If enough context: recommend a team with roles and explanations
        3. If user confirms: ask autonomy/update preferences
        4. If ready to launch: return a launch_ready event with config
        
        Response format: natural language with embedded JSON blocks for structured data.
        The frontend parses these blocks to render team cards, questions, etc.
        """
    
    def _extract_structured_blocks(self, response: str) -> list:
        """
        Parse the LLM response for structured JSON blocks like:
        ```team_recommendation
        {"template_id": "content-engine", "roles": [...], "why": "..."}
        ```
        or
        ```follow_up_question
        {"id": "autonomy", "question": "...", "options": [...]}
        ```
        or
        ```launch_ready
        {"template_id": "...", "name": "...", "config": {...}}
        ```
        """
```

The system prompt should instruct Claude to:
- Never say "template" — always say "team"
- When recommending a team, format as a ```team_recommendation JSON block
- When asking follow-ups, format as ```follow_up_question JSON blocks
- When the user is ready to launch, format as a ```launch_ready JSON block
- Be warm, confident, and concise — like a capable colleague, not a chatbot
- If the user uploads a document, acknowledge it and explain how it'll be used
- If the user's goal doesn't match any template well, say so honestly and suggest what's closest

### New endpoints in api/main.py:

**POST /api/chat/sessions** — Create a new chat session. Returns `{ session_id }`.

**POST /api/chat/{session_id}/message** — Send a message. Returns SSE stream. Request body: `{ "content": "string" }`. The endpoint:
1. Loads the session's message history
2. Appends the user message
3. Calls `architect.chat()` with the full history
4. Streams the response as SSE events: `data: {"type": "text_delta", "content": "..."}\n\n` for text, `data: {"type": "structured", "block_type": "team_recommendation", "data": {...}}\n\n` for structured blocks
5. After streaming completes, saves the assistant message to the session

**GET /api/chat/{session_id}** — Get full session with message history.

**POST /api/chat/{session_id}/launch** — Launch the team from the chat. Reads the session's recommended template and config, calls kernel.launch_template(), links any documents to the workspace, creates the mission brief if a goal was stated. Returns `{ workspace_id, team_name }`.

**Fallback when no API key:** If ANTHROPIC_API_KEY is not set, fall back to the existing keyword-matching architect logic. The chat endpoint returns a single non-streaming response using the keyword matcher. This keeps the product working in offline/dev mode.

Test by starting the API and using curl:
```bash
# Create session
curl -X POST http://localhost:8000/api/chat/sessions | python3 -m json.tool

# Send message
curl -X POST http://localhost:8000/api/chat/{session_id}/message \
  -H "Content-Type: application/json" \
  -d '{"content": "I need help creating LinkedIn content every week"}' \
  --no-buffer
```

---

## Agent 2 — Chat Onboarding Frontend

Read CLAUDE.md, docs/WEBAPP_SPEC.md, docs/DESIGN_SPRINT.md, and the current onboarding at ui/web/src/app/onboard/page.tsx.

**Files you own:**
- ui/web/src/app/page.tsx (modify the input submission to route to chat)
- ui/web/src/app/chat/page.tsx (new — the chat interface)
- ui/web/src/app/chat/ (new directory)
- ui/web/src/components/chat/ (new directory for chat components)
- ui/web/src/lib/api.ts (add chat-related functions prefixed with `chat`. Do not modify existing functions.)
- ui/web/src/lib/types.ts (add chat-related types prefixed with `Chat`. Do not modify existing types.)
- Do NOT modify: api/, kernel/, templates/, scripts/, or any existing page except page.tsx

### New page: /chat and /chat/[sessionId]

This replaces the form-based onboarding with a real chat interface.

**URL routing:**
- `/chat?goal=encoded_goal` — creates a new session and sends the goal as first message
- `/chat/[sessionId]` — resumes an existing session (for returning users)

**Layout:** Full width within the app shell. Chat messages centered (max-width 720px). Messages alternate left (Tinker) and right (user). Input at the bottom, sticky.

**CRITICAL UX RULES:**
- Only ONE major CTA visible at a time (team card OR follow-up question OR launch button, never multiple)
- Only ONE question at a time — don't stack questions
- Only ONE recommendation card at a time
- No wall-of-chat effect — keep it tight and progressive
- The frontend must respect session states: launch button ONLY in launch_ready, team card ONLY in recommending+, questions ONLY in awaiting_confirmation

**Message types the frontend must render:**

1. **Text message** — plain text from Tinker or user. Render as a chat bubble. Tinker's messages on the left with a small "T" avatar. User's on the right.

2. **Team recommendation** — when the SSE stream includes a `team_recommendation` block, render it inline in the chat as a rich card: template icon, team name, role cards in a compact grid, pipeline description, and "Why this team" explanation. Below the card: "Looks good? Say 'launch' or tell me what you'd change."

3. **Follow-up question** — when the stream includes a `follow_up_question` block, render it as interactive options inline in the chat. The user can click an option (which sends it as a message) or type a free-form response.

4. **Launch ready** — when the stream includes a `launch_ready` block, show a prominent "Launch your team →" button inline in the chat. Clicking it calls POST /api/chat/{session_id}/launch and redirects to /teams/{workspace_id}.

5. **Document reference** — when a user uploads a file, show it as a compact file card in the chat (filename, size, "Processing..." then "✓ Ready").

**Streaming:** Connect to POST /api/chat/{session_id}/message using fetch with streaming. Parse SSE events. For text_delta events, append to the current Tinker message character by character (typewriter effect). For structured events, render the appropriate component after the text completes.

**Input area:**
- Textarea (not single-line input) with placeholder "Describe what you need..."
- File upload button (paperclip icon) that accepts .pdf, .txt, .md
- Send button
- ⌘↵ shortcut to send

**Landing page modification (page.tsx):**
When the user submits a goal from the landing page, redirect to /chat?goal={encoded_goal} instead of /onboard. The chat page reads the goal from the URL, creates a session, and sends it as the first message automatically.

**Returning users:** If a user has an active chat session (status=active, no workspace_id yet), show a "Continue your conversation" card on the landing page linking to /chat/{session_id}.

**Design:** Follow the design system from DESIGN_SPRINT.md. Chat bubbles should be clean and minimal — Tinker messages on bg-white with stone-200 border, user messages on bg-zinc-900 with white text. The structured blocks (team recommendation, questions) should use the existing card styles.

---

## Agent 3 — Document Ingestion Backend

Read CLAUDE.md, kernel/api.py, kernel/spawn_context.py, and db/schema.sql.

**Files you own:**
- kernel/documents.py (new — document storage and extraction)
- kernel/spawn_context.py (modify — inject document context into agent prompts)
- kernel/api.py (add document methods)
- db/schema.sql (append workspace_documents table)
- api/main.py (add document upload endpoint — append only, do not modify existing endpoints)
- api/requirements.txt (add PyPDF2 or pdfplumber if needed)
- Do NOT modify: ui/, templates/, scripts/

### New file: kernel/documents.py

```python
class DocumentStore:
    """
    Stores and retrieves documents uploaded by users.
    Extracts text from PDFs and other formats.
    Documents are context that agents read during their work.
    """
    
    def upload_document(self, file_bytes: bytes, filename: str, content_type: str,
                       workspace_id: str = None, chat_session_id: str = None) -> dict:
        """
        Store a document and extract its text content.
        
        Supports: .pdf, .txt, .md (NO .docx this sprint)
        
        Returns: { id, filename, content_type, char_count, summary }
        """
    
    def get_documents(self, workspace_id: str) -> list:
        """Get all documents for a workspace."""
    
    def get_document_context(self, workspace_id: str, max_chars: int = 3000) -> str | None:
        """
        Returns a formatted text block for injection into agent prompts.
        Uses SUMMARY + CAPPED EXCERPT, not full raw text.
        This prevents sloppy PDFs from polluting context.
        
        Format:
        ## Uploaded Documents
        
        ### requirements.pdf
        Summary: A product requirements document describing a customer analytics dashboard...
        
        Key content (excerpt):
        [first ~2000 chars of extracted text, truncated at a sentence boundary]
        """
    
    def get_document_summary_for_chat(self, chat_session_id: str) -> str | None:
        """
        Returns a concise summary of uploaded documents for the architect.
        Used during conversation so the architect can reference docs
        when recommending a team.
        
        Format:
        [Document: requirements.pdf - PRD for customer analytics dashboard, 4200 chars]
        [Document: notes.md - Design notes about API structure, 890 chars]
        """
    
    def link_to_workspace(self, chat_session_id: str, workspace_id: str):
        """
        After a team launches from chat, re-link all documents
        from the chat session to the workspace.
        """
    
    def _extract_text(self, file_bytes: bytes, content_type: str) -> str:
        """
        Extract text from various file formats.
        PDF: use PyPDF2 or pdfplumber
        TXT/MD: decode as UTF-8
        DOCX: use python-docx or just extract raw text
        """
```

### Modify kernel/spawn_context.py

After injecting the mission brief and before injecting evidence, add document context:

```python
# In the prompt-building section:
doc_context = document_store.get_document_context(workspace_id)
if doc_context:
    prompt_parts.append(doc_context)
```

This is additive — agents without documents work exactly as before. Documents are capped at 5000 chars to prevent context flooding (same principle as evidence capping).

### Add to kernel/api.py

- `upload_document(file_bytes, filename, content_type, workspace_id, chat_session_id)` → dict
- `get_workspace_documents(workspace_id)` → list
- `get_document_context(workspace_id)` → str | None
- `link_documents_to_workspace(chat_session_id, workspace_id)` → None

### New endpoint in api/main.py

**POST /api/chat/{session_id}/documents** — Upload a file during chat. Accepts multipart form data. Extracts text, stores document linked to the chat session. Returns `{ id, filename, char_count, summary }`.

**POST /api/teams/{id}/documents** — Upload a file directly to a team (for after launch). Same behavior but links to workspace instead of chat session.

**GET /api/teams/{id}/documents** — List documents for a team.

Test:
```bash
curl -X POST http://localhost:8000/api/chat/{session_id}/documents \
  -F "file=@requirements.pdf"
```

---

## Agent 4 — Chat Components + Streaming Handler

Read CLAUDE.md, docs/DESIGN_SPRINT.md, and the chat page structure from Agent 2.

**Files you own:**
- ui/web/src/components/chat/chat-message.tsx (new)
- ui/web/src/components/chat/chat-input.tsx (new)
- ui/web/src/components/chat/team-recommendation-card.tsx (new)
- ui/web/src/components/chat/follow-up-question.tsx (new)
- ui/web/src/components/chat/launch-button.tsx (new)
- ui/web/src/components/chat/document-card.tsx (new)
- ui/web/src/components/chat/streaming-handler.ts (new — SSE parsing utility)
- Do NOT modify: api/, kernel/, templates/, scripts/, or any existing components

### streaming-handler.ts

A utility that connects to the SSE chat endpoint and yields parsed events:

```typescript
export async function* streamChat(sessionId: string, content: string): AsyncGenerator<ChatEvent> {
  const response = await fetch(`${API_URL}/api/chat/${sessionId}/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  });
  
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n\n');
    buffer = lines.pop() || '';
    
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        const event = JSON.parse(line.slice(6));
        yield event;
      }
    }
  }
}
```

Event types:
- `{ type: "text_delta", content: "..." }` — append to current message
- `{ type: "structured", block_type: "team_recommendation", data: {...} }` — render team card
- `{ type: "structured", block_type: "follow_up_question", data: {...} }` — render question
- `{ type: "structured", block_type: "launch_ready", data: {...} }` — render launch button
- `{ type: "session_state", state: "..." }` — update session state for UI gating
- `{ type: "done" }` — message complete
- `{ type: "error", message: "..." }` — show error with retry

**Streaming resilience (REQUIRED):**
- Loading state: show "Tinker is thinking..." with subtle animation while waiting for first event
- Partial stream failure: if the stream disconnects mid-message, show what was received + "Connection lost. Retrying..." with automatic retry (max 2 attempts)
- Parse failure: if a structured block fails to parse, skip it and render any surrounding text normally. Log the parse error. NEVER crash or show a blank screen.
- Duplicate event protection: track event IDs or positions to prevent rendering the same content twice on retry
- Timeout: if no events received for 30 seconds, show "Taking longer than expected..." with a cancel/retry option

### Components

**chat-message.tsx** — Renders a single message bubble. Left-aligned for Tinker (bg-white border-stone-200), right-aligned for user (bg-zinc-900 text-white). Supports embedded rich content (team cards, questions, etc.) rendered below the text.

**chat-input.tsx** — Sticky bottom input. Textarea with auto-resize. Paperclip icon for file upload. Send button. ⌘↵ shortcut. Disabled state while Tinker is responding.

**team-recommendation-card.tsx** — Compact inline card showing: icon + team name + description, role grid (2-col, icon + name + one-line description), pipeline flow, "why" explanation. Matches design system card styles.

**follow-up-question.tsx** — Renders question text + clickable option pills. When user clicks an option, it sends the option text as a chat message. Also shows a "or type your own answer..." hint.

**launch-button.tsx** — Prominent button: "Launch your team →" in Primary button style. Shows a brief animation on click, then redirects.

**document-card.tsx** — Compact file card: icon (PDF/doc icon based on type), filename, size, status ("Processing..." with spinner, then "✓ Ready" with char count).

All components follow DESIGN_SPRINT.md tokens. No custom hex colors. Use stone/zinc/blue/green/amber palette.

---

## Agent 5 — Architect System Prompt + Template Context Builder

Read CLAUDE.md, all template.json files in templates/, and api/architect.py (Agent 1 creates this).

**Files you own:**
- api/architect_prompts.py (new — system prompt and template context builder)
- Do NOT modify: kernel/, ui/, templates/, scripts/, db/

This agent writes the intelligence layer — the system prompt that makes Claude act as Tinker's architect.

### api/architect_prompts.py

```python
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

def build_template_context(templates: list[dict]) -> str:
    """
    Format all available templates as context for the architect.
    For each template: name, description, roles with descriptions,
    pipeline, what it's best for, example goals it handles.
    """
```

**The system prompt should include:**

```
You are Tinker, an AI assistant that helps people assemble the right team for their goals.

PERSONALITY:
- Warm, confident, and concise
- Like a capable colleague who's helped hundreds of people with similar problems
- Never robotic, never overly enthusiastic
- Ask smart questions, don't interrogate

RULES:
- Never say "template", "workspace", or "agent" — say "team", "role", "team member"
- Never expose internal IDs or technical details
- Always explain WHY each role exists on the team
- If the goal doesn't match any team well, be honest: "I don't have a great team for that yet, but here's the closest option..."
- Keep responses under 150 words unless the user asks for detail

CONVERSATION FLOW:
1. User states a goal → You acknowledge it, ask 1-2 clarifying questions ONLY if genuinely needed
2. Once you understand → Recommend a team using the ```team_recommendation format
3. User confirms or adjusts → Ask preferences using ```follow_up_question format
4. User is ready → Signal with ```launch_ready format

CONVERGENCE RULES:
- Recommend a team within 2 assistant turns maximum, unless the user is genuinely ambiguous
- Never ask more than 2 questions before recommending
- If you can reasonably guess the right team from the first message, recommend immediately and ask follow-ups AFTER
- Do not over-question. "Help me create LinkedIn content" needs zero clarifying questions — recommend the Content Team immediately
- "Help me with my business" DOES need a question — but only one: "What's the most important thing you need help with right now?"

RESPONSE FORMAT:
Mix natural language with structured blocks. The frontend parses blocks to render rich UI.

For team recommendations:
```team_recommendation
{
  "template_id": "content-engine",
  "name": "Content Team",
  "why": "Your goal involves creating regular content — this team researches trending topics, drafts posts matched to your voice, and reviews everything before publishing.",
  "roles": [
    {"name": "Researcher", "icon": "🔭", "description": "Finds relevant topics and trends"},
    {"name": "Writer", "icon": "✍️", "description": "Drafts content matched to your voice"},
    {"name": "Editor", "icon": "🛡️", "description": "Reviews quality and accuracy"},
    {"name": "Publisher", "icon": "📅", "description": "Manages scheduling and posting"}
  ],
  "pipeline": "Research → Draft → Review → Publish"
}
```

For follow-up questions:
```follow_up_question
{
  "id": "autonomy",
  "question": "How hands-on do you want to be?",
  "options": [
    {"value": "hands_on", "label": "Review everything before it goes out"},
    {"value": "managed", "label": "Only flag issues"},
    {"value": "autopilot", "label": "Handle it all automatically"}
  ]
}
```

For launch:
```launch_ready
{
  "template_id": "content-engine",
  "name": "My Content Team",
  "config": {
    "autonomy": "hands_on",
    "topics": "AI agents, technology trends",
    "platform": "linkedin"
  }
}
```

AVAILABLE TEAMS:
{template_context}

DOCUMENTS:
If the user has uploaded documents, they will appear as:
[Document: filename.pdf - summary]
Reference them in your recommendations. For example: "Based on your requirements doc, this team would focus on..."
```

**The template context builder** should format each template as:

```
TEAM: Content Engine
BEST FOR: Creating and publishing content (LinkedIn, X, blog posts)
ROLES: Researcher (finds trends), Writer (drafts content), Editor (quality review), Publisher (scheduling)
PIPELINE: Research → Draft → Review → Publish
EXAMPLE GOALS: "Help me create LinkedIn content", "Write blog posts about AI", "Manage my social media"
```

Keep the context concise — one team per paragraph. The architect doesn't need the full ui_schema or constraint files, just enough to make good recommendations.

Test the prompt by reviewing it manually and checking that it covers all templates, handles ambiguous goals, and produces the right structured blocks.

---

## Agent 6 — Integration Glue + Chat Session Management

Read CLAUDE.md, api/main.py, kernel/api.py, and the new files from Agents 1 and 3.

**Files you own:**
- api/chat_sessions.py (new — session CRUD)
- kernel/api.py (add chat session methods — append only)
- db/schema.sql (verify chat_sessions table exists from Agent 1, add if missing)
- scripts/seed_demo.py (add a seeded chat session with example conversation)
- Do NOT modify: ui/, templates/, or existing api/ endpoints

### api/chat_sessions.py

```python
class ChatSessionManager:
    """
    Manages chat sessions between users and Tinker's architect.
    Sessions persist conversation history so users can return to them.
    """
    
    def create_session(self, user_id: str = "default") -> dict:
        """Create a new chat session. Returns { session_id, created_at }."""
    
    def get_session(self, session_id: str) -> dict | None:
        """Get session with full message history."""
    
    def add_message(self, session_id: str, role: str, content: str, metadata: dict = None) -> dict:
        """Append a message to the session. Returns the updated session."""
    
    def set_recommendation(self, session_id: str, template_id: str):
        """Store which template was recommended (for launch)."""
    
    def launch_from_session(self, session_id: str, name: str, config: dict = None) -> dict:
        """
        Launch a team from a chat session.
        ONLY allowed when session status is 'launch_ready'.
        
        1. Verify session status is 'launch_ready'
        2. Get the recommended template
        3. Call kernel.launch_template()
        4. Create mission brief from the conversation goal
        5. Link any uploaded documents to the workspace
        6. Update session status to 'launched' and store workspace_id
        Returns { workspace_id, team_name }
        Raises ValueError if session is not in launch_ready state.
        """
    
    def update_status(self, session_id: str, status: str):
        """
        Update session status. Must follow the state machine:
        collecting → recommending → awaiting_confirmation → launch_ready → launched
        Raises ValueError on invalid transitions.
        """
    
    def get_active_sessions(self, user_id: str = "default") -> list:
        """Get sessions that haven't been launched or abandoned yet."""
```

### Add to kernel/api.py

- `create_chat_session(user_id)` → dict
- `get_chat_session(session_id)` → dict
- `get_active_chat_sessions(user_id)` → list

Test:
```bash
PYTHONPATH=. python3 -c "
from api.chat_sessions import ChatSessionManager
mgr = ChatSessionManager()
session = mgr.create_session()
print(session)
mgr.add_message(session['session_id'], 'user', 'Help me create content')
mgr.update_status(session['session_id'], 'recommending')
session = mgr.get_session(session['session_id'])
print(f'{len(session[\"messages\"])} messages, status: {session[\"status\"]}')
"
```

---

# WAVE STRUCTURE

**All 6 agents fire in parallel.**

File ownership is clean:
- Agent 1: api/main.py (new endpoints), api/architect.py (new), db/schema.sql (append)
- Agent 2: ui/web/src/app/page.tsx, ui/web/src/app/chat/ (new), ui/web/src/lib/api.ts, types.ts
- Agent 3: kernel/documents.py (new), kernel/spawn_context.py, kernel/api.py, api/requirements.txt
- Agent 4: ui/web/src/components/chat/ (new directory, all new files)
- Agent 5: api/architect_prompts.py (new, standalone)
- Agent 6: api/chat_sessions.py (new), kernel/api.py (append), scripts/seed_demo.py (extend)

**Potential overlap: kernel/api.py** — Agents 3 and 6 both add methods. Use the same rule as before: Agent 3 prefixes document methods, Agent 6 prefixes chat session methods. Append only, no refactoring.

**Potential overlap: api/main.py** — Agents 1 and 3 both add endpoints. Agent 1 adds /api/chat/* endpoints. Agent 3 adds /api/teams/{id}/documents and /api/chat/{session_id}/documents. Different URL prefixes, append only.

**Potential overlap: db/schema.sql** — Agents 1 and 3 both append tables. Agent 1 appends chat_sessions, Agent 3 appends workspace_documents. Both append only, no modification of existing tables.

**After all 6 land:** Test the full flow:
1. Create a chat session
2. Send a message ("Help me create LinkedIn content")
3. Verify the LLM responds with a team recommendation
4. Upload a document
5. Launch the team
6. Verify the team exists and the document is linked

That's the conversational core working end to end.
