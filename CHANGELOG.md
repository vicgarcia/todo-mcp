## v2.0.0

major refactor with direct microsoft graph api access and expanded tool set.

**added:**
- modular `src/todo_mcp/` package structure (schema.py, graph_client.py, server.py)
- 10 new tools (16 total):
  - `create_list`, `update_list`, `delete_list` - list management
  - `get_tasks_by_due_date_range` - cross-list due date filtering
  - `get_tasks_by_completed_date_range` - completed task history for reporting
  - `get_subtasks`, `create_subtask`, `update_subtask`, `complete_subtask`, `delete_subtask` - checklist item management
- recurrence support in `create_task` and `update_task` (daily, weekly, monthly patterns)
- batch api support for efficient cross-list queries
- CLAUDE.md session documentation

**changed:**
- replaced pymstodo library with direct microsoft graph api access via httpx
- removed requests-oauthlib dependency (oauth now uses httpx directly)
- updated pyproject.toml entry point to `todo_mcp.server:run`

**removed:**
- pymstodo dependency
- requests-oauthlib dependency
- single-file architecture (now modular package)

**notes:**
- cli arguments unchanged - existing configurations work without modification
- recurring tasks are indefinite only (endDate ignored by graph api)

## v1.0.0

initial release. single-file module (`todo_mcp.py`) using pymstodo library for Microsoft Graph API access.

install via `uv tool install` or docker. credentials settable via `--client-id`, `--client-secret`, `--refresh-token` args or environment variables.

6 tools: get_lists, get_tasks, create_task, update_task, complete_task, delete_task.

oauth authorization flow with `--auth` flag.
