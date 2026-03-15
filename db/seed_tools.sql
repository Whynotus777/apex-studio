INSERT OR IGNORE INTO tools (
    id,
    name,
    adapter,
    auth_method,
    scopes,
    read_write,
    cost_per_call,
    approval_required
) VALUES (
    'web_search',
    'Web Search',
    'adapters.tools.web_search.search',
    'none',
    '["search", "research", "evidence"]',
    'read_only',
    0,
    0
);
