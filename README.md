I use Microsoft Todo for personal task management. This MCP server connects Claude Desktop directly to the [Microsoft Todo API](https://learn.microsoft.com/en-us/graph/api/resources/todo-overview) via the [Microsoft Graph API](https://graph.microsoft.com), making it easy to create, review, and manage tasks through conversation.

Once set up, you can make queries like:

- "what tasks do I have due this week?"
- "add a daily recurring task for standup at 9am"
- "show me what I completed last week"
- "create a step to buy ingredients for my dinner task"
- "what lists do I have?"

## setup

#### step 1 — register an azure app

go to [portal.azure.com](https://portal.azure.com/) and register a new application:

- supported account types: **"accounts in any organizational directory and personal microsoft accounts"**
- add a redirect URI: platform = **web**, URI = `https://localhost/login/authorized`
- note the **application (client) id** from the app overview page
- go to **certificates & secrets** → new client secret → copy the **value** (not the secret id)

#### step 2 — get your refresh token

run the one-time authorization flow. requires [uv](https://docs.astral.sh/uv/):

```bash
uvx --from git+https://github.com/vicgarcia/todo-mcp todo-mcp \
  --auth --client-id YOUR_CLIENT_ID --client-secret YOUR_CLIENT_SECRET
```

this prints an authorization url. visit it in your browser, authorize the app, paste the full redirect url back into the terminal. your refresh token will be printed — copy it for the next step.

#### step 3 — configure claude desktop

##### option 1: install with uv

```bash
uv tool install git+https://github.com/vicgarcia/todo-mcp
```

claude desktop config:

```json
{
  "mcpServers": {
    "todo": {
      "command": "todo-mcp",
      "args": [
        "--client-id", "your-client-id",
        "--client-secret", "your-client-secret",
        "--refresh-token", "your-refresh-token"
      ]
    }
  }
}
```

##### option 2: docker

```json
{
  "mcpServers": {
    "todo": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-e", "TODO_CLIENT_ID=your-client-id",
        "-e", "TODO_CLIENT_SECRET=your-client-secret",
        "-e", "TODO_REFRESH_TOKEN=your-refresh-token",
        "ghcr.io/vicgarcia/todo-mcp:latest"
      ]
    }
  }
}
```

replace the three credential values with your actual values from steps 1 and 2.

the server authenticates at startup using your refresh token to obtain a live access token. tokens are held in memory only — nothing is written to disk. the access token is automatically refreshed in memory as needed throughout the session.

## features

this mcp server exposes 16 tools organized into categories.

### list management

#### get_lists

retrieve all of your task lists

**returns:**
- all lists with their ids, names, and ownership status
- use the `list_id` values from this response as input to the other tools

**example usage in claude:**
> "what task lists do I have?"

#### create_list

create a new task list

**parameters:**
- `name` (required): name for the new list

**example usage in claude:**
> "create a new list called 'vacation planning'"

#### update_list

rename a task list

**parameters:**
- `list_id` (required): id of the list to rename
- `name` (required): new name for the list

**example usage in claude:**
> "rename my 'work' list to 'projects'"

#### delete_list

delete a task list permanently

**parameters:**
- `list_id` (required): id of the list to delete

**example usage in claude:**
> "delete the 'old projects' list"

### task management

#### get_tasks

retrieve tasks from a specific list

**parameters:**
- `list_id` (required): id of the task list
- `status` (optional): filter by status — `not_completed` (default), `completed`, or `all`
- `limit` (optional): max tasks to return (default: 100, max: 1000)

**example usage in claude:**
> "show me all my incomplete tasks in my work list"

#### create_task

create a new task with optional recurrence

**parameters:**
- `title` (required): task title
- `list_id` (required): id of the list to add it to
- `due_date` (optional): due date in yyyy-mm-dd format
- `body_text` (optional): additional notes or description
- `importance` (optional): `low`, `normal` (default), or `high`
- `recurrence_type` (optional): `daily`, `weekly`, or `monthly`
- `recurrence_interval` (optional): repeat every N periods (default: 1)
- `recurrence_days_of_week` (optional): for weekly, e.g. `['monday', 'friday']`
- `recurrence_day_of_month` (optional): for monthly, day 1-31

**example usage in claude:**
> "add a high priority task to call the dentist to my personal list, due friday"
> "create a daily standup task that repeats every weekday"
> "add a task to pay rent on the 1st of every month"

#### update_task

update the properties of an existing task

**parameters:**
- `task_id` (required): id of the task
- `list_id` (required): id of the list containing the task
- `title` (optional): new title
- `due_date` (optional): new due date in yyyy-mm-dd format
- `body_text` (optional): new notes or description
- `importance` (optional): `low`, `normal`, or `high`
- `status` (optional): `notStarted`, `inProgress`, `completed`, `waitingOnOthers`, or `deferred`
- `recurrence_type` (optional): `daily`, `weekly`, or `monthly` to set/change recurrence
- `recurrence_interval` (optional): repeat every N periods
- `recurrence_days_of_week` (optional): for weekly recurrence
- `recurrence_day_of_month` (optional): for monthly recurrence
- `remove_recurrence` (optional): set True to remove recurrence

**example usage in claude:**
> "update the grocery task to be high importance and due tomorrow"
> "make the standup task repeat every monday, wednesday, and friday"

#### complete_task

mark a task as completed

**parameters:**
- `task_id` (required): id of the task
- `list_id` (required): id of the list containing the task

**example usage in claude:**
> "mark the 'submit report' task as done"

#### delete_task

permanently delete a task

**parameters:**
- `task_id` (required): id of the task
- `list_id` (required): id of the list containing the task

**example usage in claude:**
> "delete the task about the old project kickoff"

### cross-list views

#### get_tasks_by_due_date_range

get tasks due within a date range across ALL lists — great for daily/weekly planning

**parameters:**
- `start_date` (optional): start date in yyyy-mm-dd (default: today)
- `end_date` (optional): end date in yyyy-mm-dd (default: start_date)
- `include_overdue` (optional): include past-due tasks (default: True)
- `include_no_due_date` (optional): include tasks without due dates (default: False)

**returns:**
- tasks grouped by category (overdue, in_range, no_due_date)
- counts for each category

**example usage in claude:**
> "what do I have due today?"
> "show me my tasks for this week"
> "what's overdue?"

#### get_tasks_by_completed_date_range

get tasks completed within a date range across ALL lists — ideal for weekly reporting

**parameters:**
- `start_date` (required): start date in yyyy-mm-dd
- `end_date` (optional): end date in yyyy-mm-dd (default: today)

**returns:**
- completed tasks grouped by list
- daily completion counts
- daily average

**example usage in claude:**
> "what did I complete last week?"
> "summarize my completed tasks since monday"

### steps (checklist items)

#### get_steps

get all steps for a task

**parameters:**
- `task_id` (required): id of the parent task
- `list_id` (required): id of the task list

**example usage in claude:**
> "show me the steps for my dinner party task"

#### create_step

create a new step

**parameters:**
- `task_id` (required): id of the parent task
- `list_id` (required): id of the task list
- `name` (required): step description

**example usage in claude:**
> "add a step 'buy ingredients' to my dinner party task"

#### update_step

update a step

**parameters:**
- `item_id` (required): id of the step
- `task_id` (required): id of the parent task
- `list_id` (required): id of the task list
- `name` (optional): new description
- `is_checked` (optional): True/False

**example usage in claude:**
> "rename the first step to 'buy groceries'"

#### complete_step

mark a step as completed

**parameters:**
- `item_id` (required): id of the step
- `task_id` (required): id of the parent task
- `list_id` (required): id of the task list

**example usage in claude:**
> "check off the 'buy ingredients' step"

#### delete_step

delete a step permanently

**parameters:**
- `item_id` (required): id of the step
- `task_id` (required): id of the parent task
- `list_id` (required): id of the task list

**example usage in claude:**
> "remove the last step"

## development

```bash
git clone https://github.com/vicgarcia/todo-mcp
cd todo-mcp

# run the one-time auth flow to get your refresh token
uv run python -m todo_mcp.server --auth --client-id YOUR_ID --client-secret YOUR_SECRET

# run the server directly
uv run python -m todo_mcp.server \
  --client-id your-id \
  --client-secret your-secret \
  --refresh-token your-refresh-token
```

#### project structure

```
todo-mcp/
├── src/
│   └── todo_mcp/
│       ├── __init__.py       # package exports
│       ├── server.py         # fastmcp server + 17 tools
│       ├── graph_client.py   # microsoft graph api client
│       └── schema.py         # dataclasses
├── Dockerfile                # docker deployment
├── pyproject.toml            # project metadata and dependencies
├── README.md
├── CLAUDE.md                 # session documentation
└── CHANGELOG.md
```

#### building docker image locally

```bash
docker build -t todo-mcp:local .
```

to use the local build in claude desktop, replace `ghcr.io/vicgarcia/todo-mcp:latest` with `todo-mcp:local` in your mcp settings.

## token lifecycle

the refresh token you set up in step 2 is your long-lived credential. microsoft personal account refresh tokens have a 90-day sliding window — as long as you use the server regularly it stays valid indefinitely. if it ever expires (extended inactivity, password change, or app permission revocation), re-run the `--auth` step and update your `--refresh-token` value.

## recurrence notes

recurring tasks created via this server repeat indefinitely. this is due to a microsoft graph api limitation where end dates are silently ignored. if you need to stop a recurring task, complete or delete it, or edit it directly in the microsoft todo app.

for flexible scheduling:
- use `daily` with high intervals (e.g., `recurrence_interval=21` for every 3 weeks)
- use `weekly` with specific days (e.g., `recurrence_days_of_week=['monday', 'wednesday', 'friday']`)
- use `monthly` for fixed dates (e.g., `recurrence_day_of_month=15`)
