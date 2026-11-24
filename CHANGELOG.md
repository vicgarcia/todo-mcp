## v3.1.0

**added:**
- server instructions: how lists, tasks and steps relate, which tool answers which question, and when deleting is allowed
- annotations on every tool: read-only, adds (not destructive) or changes (destructive), none open world
- fuller tool descriptions: when to use each tool, how it relates to the others, and what it returns
- a schema description for every argument, declared with `Field` instead of docstring `args:` blocks, so it no longer depends on the fastmcp version

**changed:**
- a failed call is a tool error (`isError: true`) instead of a normal result holding `{"error": ..., "success": false}`; successful results are unchanged
- requires `fastmcp>=3.0.0` and declares `pydantic>=2.0`; the lockfile moves to fastmcp 4, as installed

## v3.0.0

**breaking:**
- `get_tasks_by_due_date_range` and `get_tasks_by_completed_date_range` now require `list_ids` and only read those lists; get ids from `get_lists`
- `get_tasks_by_due_date_range` leaves overdue tasks out unless `include_overdue` is true

**removed:**
- docker image and the container build workflow; install with `uv tool install`

**added:**
- `get_tasks_due_today` - open tasks due today in the given lists
- `clear_due_date` on `update_task`, and an empty `body_text` clears the notes
- `truncated` in `get_tasks` results

**fixed:**
- overlapping tool calls no longer close the shared connection on each other
- network and bad input failures come back as normal tool errors
- reads retry on 429, 503 and 504; writes only retry on 429
- `get_tasks` follows graph's paging up to `limit`
- "today" and the default completed end date use the machine's local day
- multi-list reads no longer lose lists to rate limiting: lists are read one at a time, with paging

## v2.0.0

major refactor with direct microsoft graph api access and expanded tool set.

**added:**
- modular `src/todo_mcp/` package structure (schema.py, graph_client.py, server.py)
- 10 new tools (16 total):
  - `create_list`, `update_list`, `delete_list` - list management
  - `get_tasks_by_due_date_range` - cross-list due date filtering
  - `get_tasks_by_completed_date_range` - completed task history for reporting
  - `get_steps`, `create_step`, `update_step`, `complete_step`, `delete_step` - checklist item management
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
