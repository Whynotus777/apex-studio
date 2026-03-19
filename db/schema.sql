CREATE TABLE IF NOT EXISTS goals (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
    status TEXT DEFAULT 'active', created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, goal_id TEXT REFERENCES goals(id),
    name TEXT NOT NULL, description TEXT, pipeline_stage TEXT,
    status TEXT DEFAULT 'active', created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id),
    goal_id TEXT REFERENCES goals(id),
    title TEXT NOT NULL, description TEXT,
    pipeline_stage TEXT,
    assigned_to TEXT, checked_out_by TEXT, checked_out_at TEXT,
    status TEXT DEFAULT 'backlog', priority INTEGER DEFAULT 2,
    review_status TEXT, parent_task_id TEXT REFERENCES tasks(id),
    created_at TEXT DEFAULT (datetime('now')), completed_at TEXT,
    workspace_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, assigned_to);
CREATE INDEX IF NOT EXISTS idx_tasks_workspace ON tasks(workspace_id);
CREATE TABLE IF NOT EXISTS agent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT DEFAULT (datetime('now')),
    from_agent TEXT NOT NULL, to_agent TEXT NOT NULL,
    thread_id TEXT, msg_type TEXT NOT NULL,
    priority INTEGER DEFAULT 2, content TEXT NOT NULL,
    status TEXT DEFAULT 'pending', parent_id INTEGER,
    task_id TEXT REFERENCES tasks(id),
    workspace_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_msg_inbox ON agent_messages(to_agent, status);
CREATE INDEX IF NOT EXISTS idx_msg_thread ON agent_messages(thread_id);
CREATE TABLE IF NOT EXISTS agent_status (
    agent_name TEXT PRIMARY KEY, status TEXT DEFAULT 'idle',
    current_task TEXT, last_heartbeat TEXT,
    model_active TEXT, session_id TEXT, meta TEXT,
    workspace_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_status_workspace ON agent_status(workspace_id);
CREATE TABLE IF NOT EXISTS agent_sessions (
    id TEXT PRIMARY KEY, agent_name TEXT NOT NULL,
    task_id TEXT, context TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    last_active TEXT, status TEXT DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT, agent_name TEXT NOT NULL,
    output_ref TEXT NOT NULL, stakes TEXT DEFAULT 'low',
    triage_model TEXT, review_model TEXT,
    verdict TEXT, feedback TEXT,
    created_at TEXT DEFAULT (datetime('now')), reviewed_at TEXT,
    workspace_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_reviews_task ON reviews(task_id);
CREATE TABLE IF NOT EXISTS evals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT, agent_name TEXT NOT NULL,
    eval_layer TEXT NOT NULL,
    eval_type TEXT NOT NULL, dimension TEXT,
    score REAL, max_score REAL, notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    workspace_id TEXT
);
-- Phase B: Tool primitive
CREATE TABLE IF NOT EXISTS tools (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    adapter TEXT,
    auth_method TEXT,
    scopes TEXT,
    read_write TEXT DEFAULT 'read',
    cost_per_call REAL DEFAULT 0,
    approval_required INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS tool_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    tool_id TEXT NOT NULL REFERENCES tools(id),
    permission_level TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    workspace_id TEXT,
    UNIQUE(agent_id, tool_id)
);
CREATE INDEX IF NOT EXISTS idx_tool_grants_agent ON tool_grants(agent_id);
-- Phase B: Permission primitive
CREATE TABLE IF NOT EXISTS permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    resource TEXT NOT NULL,
    level TEXT NOT NULL,
    max_spend_per_day REAL,
    requires_approval INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    workspace_id TEXT,
    UNIQUE(agent_id, resource)
);
CREATE INDEX IF NOT EXISTS idx_permissions_agent ON permissions(agent_id);
-- Phase B: Budget primitive
CREATE TABLE IF NOT EXISTS budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    budget_type TEXT NOT NULL,
    limit_amount REAL NOT NULL,
    spent_amount REAL DEFAULT 0,
    period TEXT DEFAULT 'daily',
    alert_threshold REAL DEFAULT 0.8,
    created_at TEXT DEFAULT (datetime('now')),
    workspace_id TEXT,
    UNIQUE(agent_id, budget_type)
);
CREATE TABLE IF NOT EXISTS spend_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    budget_id INTEGER REFERENCES budgets(id),
    amount REAL NOT NULL,
    description TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_spend_log_agent ON spend_log(agent_id, created_at);
-- Phase D: Workspace scoping
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    autonomy_policy TEXT DEFAULT 'hands_on',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_workspaces_template ON workspaces(template_id);
-- Phase E: Mission Brief
CREATE TABLE IF NOT EXISTS mission_briefs (
    workspace_id TEXT PRIMARY KEY,
    objective TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    constraints TEXT DEFAULT '[]',
    definition_of_done TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT
);
-- Phase B: Evidence primitive
CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    agent_id TEXT,
    tool_name TEXT,
    query TEXT,
    results TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
-- Phase F: Architect chat sessions
CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'collecting',
    goal TEXT,
    recommended_template_id TEXT,
    workspace_id TEXT,
    conversation_json TEXT NOT NULL DEFAULT '[]',
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    launched_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions(user_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_status ON chat_sessions(status, updated_at);
-- Phase F: Document storage
CREATE TABLE IF NOT EXISTS workspace_documents (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    workspace_id TEXT,
    chat_session_id TEXT,
    extracted_text TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    summary TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_workspace_documents_workspace ON workspace_documents(workspace_id);
CREATE INDEX IF NOT EXISTS idx_workspace_documents_chat ON workspace_documents(chat_session_id);
-- Phase G: Third-party integrations (OAuth tokens, one row per provider+user)
CREATE TABLE IF NOT EXISTS integrations (
    id                TEXT PRIMARY KEY,          -- UUID
    provider          TEXT NOT NULL,             -- 'github', 'slack', ...
    user_id           TEXT NOT NULL DEFAULT 'default',
    access_token      TEXT NOT NULL,
    token_type        TEXT DEFAULT 'bearer',
    scope             TEXT,                      -- space/comma-separated granted scopes
    github_login      TEXT,                      -- GitHub: username
    github_name       TEXT,                      -- GitHub: display name
    github_avatar_url TEXT,                      -- GitHub: avatar URL
    created_at        TEXT DEFAULT (datetime('now')),
    updated_at        TEXT DEFAULT (datetime('now')),
    UNIQUE(provider, user_id)
);
CREATE INDEX IF NOT EXISTS idx_integrations_provider ON integrations(provider, user_id);
-- Wave 1: Explicit task queue (one active pipeline per workspace)
CREATE TABLE IF NOT EXISTS task_queue (
    id             TEXT PRIMARY KEY,
    workspace_id   TEXT NOT NULL,
    task_id        TEXT NOT NULL,
    queue_position INTEGER NOT NULL,
    queue_state    TEXT NOT NULL DEFAULT 'queued',  -- queued/active/completed/cancelled
    enqueued_at    TEXT NOT NULL,
    started_at     TEXT,
    completed_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_task_queue_workspace ON task_queue(workspace_id, queue_state);
