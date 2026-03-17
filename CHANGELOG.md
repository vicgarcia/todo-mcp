## v1.0.0

initial release. single-file module (`todo_mcp.py`) using pymstodo library for Microsoft Graph API access.

install via `uv tool install` or docker. credentials settable via `--client-id`, `--client-secret`, `--refresh-token` args or environment variables.

6 tools: get_lists, get_tasks, create_task, update_task, complete_task, delete_task.

oauth authorization flow with `--auth` flag.
