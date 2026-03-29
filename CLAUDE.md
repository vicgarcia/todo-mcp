# Claude Code Session Documentation

## Project Overview

Microsoft Todo MCP Server - A Python MCP server connecting Claude Desktop to Microsoft Todo via direct Graph API access. Implements 17 tools for task management, list operations, steps (checklist items), recurrence, and cross-list date-based queries.

Modular structure in `src/todo_mcp/`, installable via `uv tool install`.

## Project Structure

```
todo-mcp/
├── src/
│   └── todo_mcp/
│       ├── __init__.py       # Package exports, version
│       ├── server.py         # FastMCP server, 17 tools, CLI, run()
│       ├── graph_client.py   # GraphClient class for Microsoft Graph API
│       └── schema.py         # Dataclasses (TodoList, TodoTask, etc.)
├── pyproject.toml            # Package metadata and dependencies
├── Dockerfile                # Docker deployment
├── README.md                 # User-facing documentation
├── CLAUDE.md                 # This file
└── CHANGELOG.md              # Release notes
```

## External Documentation

### Key References

- **Microsoft Graph Todo API**: https://learn.microsoft.com/en-us/graph/api/resources/todo-overview
- **FastMCP**: https://github.com/PrefectHQ/fastmcp - Python MCP server framework
- **GRAPH_API_REFERENCE.md**: Local documentation of verified API behaviors and bugs

## Quick Reference

### Development Commands

```bash
uv tool install --editable .                    # install in dev mode
todo-mcp --auth --client-id X --client-secret Y # one-time auth
todo-mcp --client-id X --client-secret Y --refresh-token Z  # run server
docker build -t todo-mcp:local .                # build docker image
```

### Installation

```bash
uv tool install git+https://github.com/vicgarcia/todo-mcp
```

### Environment Variables

- `TODO_CLIENT_ID`: Azure app client id (required)
- `TODO_CLIENT_SECRET`: Azure app client secret (required)
- `TODO_REFRESH_TOKEN`: Refresh token from --auth flow (required)
- `LOG_LEVEL`: DEBUG or INFO (default: INFO)

## Tools Overview

16 MCP tools organized by category:

### List Management
- `get_lists` - Retrieve all task lists
- `create_list` - Create new list
- `update_list` - Rename list
- `delete_list` - Delete list

### Task Management
- `get_tasks` - Get tasks from specific list with filtering
- `create_task` - Create task with optional recurrence
- `update_task` - Update task properties including recurrence
- `complete_task` - Mark task completed
- `delete_task` - Delete task

### Cross-List Views
- `get_tasks_by_due_date_range` - Tasks due within date range across ALL lists
- `get_tasks_by_completed_date_range` - Completed task history for reporting

### Steps (Checklist Items)
- `get_steps` - Get checklist items for a task
- `create_step` - Create checklist item
- `update_step` - Update checklist item
- `complete_step` - Check item
- `delete_step` - Delete checklist item

## Implementation Notes

### Architecture Decisions

- **Modular package structure**: `src/todo_mcp/` with separate modules for server, client, schema
- **Direct Graph API**: Replaced pymstodo library with httpx for full API access
- **FastMCP**: Used for MCP server framework
- **STDIO Transport**: Default for Claude Desktop compatibility
- **Token auto-refresh**: 5-minute buffer before expiry

### Graph API Bug Workarounds

Verified bugs (tested 2026-03-17):

1. **PATCH recurrence fails with startDate**
   - Error: `Invalid JSON, Error converting value ... to type 'Microsoft.OData.Edm.Date'`
   - Workaround: Omit `range.startDate` from PATCH, include `dueDateTime` instead

2. **endDate is silently ignored**
   - Any endDate value becomes "noEnd"
   - Workaround: Only use `noEnd` range type; recurring tasks are indefinite

3. **completedDateTime filtering syntax**
   - Must use `completedDateTime/dateTime ge 'YYYY-MM-DD'` (with `/dateTime` path)

### Code Organization (server.py)

- **OAuth functions** - get_auth_url, get_token_from_code, bootstrap_token, run_auth_flow
- **Argument parsing** - parse_args(), _HELP constant
- **Helper functions** - parse_date, get_client, format_error, format_success
- **MCP server + tools** - mcp = FastMCP(...), 17 @mcp.tool definitions
- **Entry point** - run(), if __name__ == '__main__'

### Entry Point Flow

```python
run()
  → logging.basicConfig(...)
  → parse_args()
  → if --auth: run_auth_flow() and return
  → bootstrap_token() from refresh_token
  → GraphClient(...) initialization
  → verify connection with get_lists(top=1)
  → mcp.run()
```

## Design Decisions

### What We Built

- **17 tools**: Full CRUD for lists, tasks, steps + date range queries
- **Recurrence support**: Daily, weekly, monthly patterns (noEnd only due to API bug)
- **Batch API**: Cross-list queries use batch API for efficiency
- **Server-side filtering**: Completed task history uses Graph API filters

### What We Didn't Build

- No caching (stateless requests)
- No attachments (future enhancement)
- No delta queries (future enhancement)
- No end-date recurrence (API ignores it)

## Code Conventions

### Naming & Style

- Lowercase log messages except proper names (Microsoft, Graph, API)
- Consistent error responses: `{"error": "message", "success": False}`
- Consistent success responses: `{"success": True, ...data}`
- Descriptive variable names (`graph_client` not `gc`)

### Error Handling

- GraphAPIError exception with status_code, error_code, message
- All tools wrap in try/except and return format_error()
- Validation errors return early with format_error()
