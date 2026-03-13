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
    created_at TEXT DEFAULT (datetime('now')), completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, assigned_to);
CREATE TABLE IF NOT EXISTS agent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT DEFAULT (datetime('now')),
    from_agent TEXT NOT NULL, to_agent TEXT NOT NULL,
    thread_id TEXT, msg_type TEXT NOT NULL,
    priority INTEGER DEFAULT 2, content TEXT NOT NULL,
    status TEXT DEFAULT 'pending', parent_id INTEGER,
    task_id TEXT REFERENCES tasks(id)
);
CREATE INDEX IF NOT EXISTS idx_msg_inbox ON agent_messages(to_agent, status);
CREATE INDEX IF NOT EXISTS idx_msg_thread ON agent_messages(thread_id);
CREATE TABLE IF NOT EXISTS agent_status (
    agent_name TEXT PRIMARY KEY, status TEXT DEFAULT 'idle',
    current_task TEXT, last_heartbeat TEXT,
    model_active TEXT, session_id TEXT, meta TEXT
);
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
    created_at TEXT DEFAULT (datetime('now')), reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_reviews_task ON reviews(task_id);
CREATE TABLE IF NOT EXISTS evals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT, agent_name TEXT NOT NULL,
    eval_layer TEXT NOT NULL,
    eval_type TEXT NOT NULL, dimension TEXT,
    score REAL, max_score REAL, notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
