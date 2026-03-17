I use Microsoft Todo for personal task management. This MCP server connects Claude Desktop directly to the [Microsoft Todo API](https://learn.microsoft.com/en-us/graph/api/resources/todo-overview) via the [Microsoft Graph API](https://graph.microsoft.com), making it easy to create, review, and manage tasks through conversation.

Once set up, you can make queries like:

- "what tasks do I have due this week?"
- "add a task to buy groceries to my personal list"
- "mark the dentist appointment task as complete"
- "show me all my high priority tasks"
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

this mcp server exposes tools to interact with the microsoft todo api.

#### get_lists

retrieve all of your task lists

**returns:**
- all lists with their ids, names, and ownership status
- use the `list_id` values from this response as input to the other tools

**example usage in claude:**
> "what task lists do I have?"

#### get_tasks

retrieve tasks from a specific list

**parameters:**
- `list_id` (required): id of the task list
- `status` (optional): filter by status — `not_completed` (default), `completed`, or `all`
- `limit` (optional): max tasks to return (default: 100, max: 1000)

**example usage in claude:**
> "show me all my incomplete tasks in my work list"
> "what tasks did I complete this week?"

#### create_task

create a new task in a list

**parameters:**
- `title` (required): task title
- `list_id` (required): id of the list to add it to
- `due_date` (optional): due date in yyyy-mm-dd format
- `body_text` (optional): additional notes or description
- `importance` (optional): `low`, `normal` (default), or `high`

**example usage in claude:**
> "add a high priority task to call the dentist to my personal list, due friday"

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

**example usage in claude:**
> "update the grocery task to be high importance and due tomorrow"

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

## development

```bash
git clone https://github.com/vicgarcia/todo-mcp
cd todo-mcp

# run the one-time auth flow to get your refresh token
uv run todo_mcp.py --auth --client-id YOUR_ID --client-secret YOUR_SECRET

# run the server directly
uv run todo_mcp.py \
  --client-id your-id \
  --client-secret your-secret \
  --refresh-token your-refresh-token
```

#### project structure

```
todo-mcp/
├── todo_mcp.py          # single-file module (server + all logic)
├── Dockerfile           # docker deployment
├── pyproject.toml       # project metadata and dependencies
└── README.md
```

#### building docker image locally

```bash
docker build -t todo-mcp:local .
```

to use the local build in claude desktop, replace `ghcr.io/vicgarcia/todo-mcp:latest` with `todo-mcp:local` in your mcp settings.

## token lifecycle

the refresh token you set up in step 2 is your long-lived credential. microsoft personal account refresh tokens have a 90-day sliding window — as long as you use the server regularly it stays valid indefinitely. if it ever expires (extended inactivity, password change, or app permission revocation), re-run the `--auth` step and update your `--refresh-token` value.
