'''
fastmcp server for microsoft todo with 17 tools.

direct microsoft graph api access via httpx.
'''

import argparse
import logging
import os
import time
from datetime import date, timedelta
from typing import Annotated, Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from dateutil import parser as date_parser
from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from todo_mcp.graph_client import GraphAPIError, GraphClient
from todo_mcp.schema import DateTimeTimeZone, Recurrence, RecurrencePattern, TodoTask

logger = logging.getLogger(__name__)

_SCOPE = 'openid offline_access Tasks.ReadWrite'
_REDIRECT_URI = 'https://localhost/login/authorized'
_TOKEN_URL = 'https://login.microsoftonline.com/common/oauth2/v2.0/token'
_AUTH_URL = 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize'

_HELP = '''
setup:
  1. register an azure app at https://portal.azure.com/
       - supported account types: "accounts in any organizational directory
         and personal microsoft accounts"
       - add a redirect URI: platform = web,
         URI = https://localhost/login/authorized
       - note the application (client) id and create a client secret

  2. run the one-time authorization flow to get your refresh token:
       todo-mcp --auth --client-id YOUR_ID --client-secret YOUR_SECRET

  3. run the server:
       todo-mcp --client-id ID --client-secret SECRET --refresh-token TOKEN

environment variables:
  TODO_CLIENT_ID       azure app client id
  TODO_CLIENT_SECRET   azure app client secret
  TODO_REFRESH_TOKEN   refresh token obtained from the --auth flow
'''

# module-level client
_client: Optional[GraphClient] = None
_client_id: Optional[str] = None
_client_secret: Optional[str] = None


# =============================================================================
# oauth authentication
# =============================================================================

def get_auth_url(client_id: str) -> str:
    '''generate oauth authorization url.'''
    params = {
        'client_id': client_id,
        'response_type': 'code',
        'redirect_uri': _REDIRECT_URI,
        'scope': _SCOPE,
        'response_mode': 'query',
    }
    return f'{_AUTH_URL}?{urlencode(params)}'


def get_token_from_code(client_id: str, client_secret: str, redirect_resp: str) -> dict:
    '''exchange authorization code for tokens.'''
    parsed = urlparse(redirect_resp)
    code = parse_qs(parsed.query).get('code', [None])[0]
    if not code:
        raise ValueError('no authorization code in redirect URL')

    with httpx.Client() as http:
        response = http.post(_TOKEN_URL, data={
            'client_id': client_id,
            'client_secret': client_secret,
            'code': code,
            'redirect_uri': _REDIRECT_URI,
            'grant_type': 'authorization_code',
            'scope': _SCOPE,
        })
        response.raise_for_status()
        data = response.json()
        # add expires_at for token refresh logic
        if 'expires_in' in data:
            data['expires_at'] = time.time() + data['expires_in']
        return data


def bootstrap_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    '''exchange a refresh token for a full token object.'''
    with httpx.Client() as http:
        response = http.post(_TOKEN_URL, data={
            'client_id': client_id,
            'client_secret': client_secret,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
            'scope': _SCOPE,
        })
        response.raise_for_status()
        data = response.json()
        # preserve refresh_token if not returned
        if 'refresh_token' not in data:
            data['refresh_token'] = refresh_token
        # add expires_at for token refresh logic
        if 'expires_in' in data:
            data['expires_at'] = time.time() + data['expires_in']
        return data


def run_auth_flow(client_id: str, client_secret: str) -> None:
    '''run interactive oauth authorization flow.'''
    auth_url = get_auth_url(client_id)
    print(f'\nvisit this url to authorize:\n\n  {auth_url}\n')
    redirect_resp = input('paste the full redirect url here:\n> ').strip()
    token = get_token_from_code(client_id, client_secret, redirect_resp)
    print('\nauthorization complete! set this environment variable:\n')
    print(f'  TODO_REFRESH_TOKEN={token["refresh_token"]}\n')


# =============================================================================
# argument parsing
# =============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog='todo-mcp',
        description='Microsoft Todo MCP server',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_HELP
    )
    parser.add_argument(
        '--auth',
        action='store_true',
        help='run one-time OAuth authorization flow and print refresh token, then exit'
    )
    parser.add_argument(
        '--client-id',
        default=os.getenv('TODO_CLIENT_ID'),
        metavar='ID',
        help='azure app client id (or TODO_CLIENT_ID env var)'
    )
    parser.add_argument(
        '--client-secret',
        default=os.getenv('TODO_CLIENT_SECRET'),
        metavar='SECRET',
        help='azure app client secret (or TODO_CLIENT_SECRET env var)'
    )
    parser.add_argument(
        '--refresh-token',
        default=os.getenv('TODO_REFRESH_TOKEN'),
        metavar='TOKEN',
        help='refresh token (or TODO_REFRESH_TOKEN env var)'
    )
    return parser.parse_args()


# =============================================================================
# helpers
# =============================================================================

def parse_date(date_str: str) -> date:
    '''parse a date string to a date object.'''
    try:
        return date_parser.parse(date_str).date()
    except OverflowError as e:
        raise ValueError(f'date out of range: {date_str}') from e


def get_client() -> GraphClient:
    '''get the initialized graph client.'''
    if _client is None:
        raise RuntimeError('client not initialized')
    return _client


def tasks_in_lists(
    client: GraphClient,
    list_ids: List[str],
    read: Callable[[str], List[TodoTask]],
) -> List[TodoTask]:
    '''read tasks from each list once, with list names, saying which list id graph rejects.'''
    names = {lst.id: lst.display_name for lst in client.get_lists()}
    tasks: List[TodoTask] = []
    for list_id in dict.fromkeys(list_ids):
        try:
            found = read(list_id)
        except GraphAPIError as e:
            if e.status_code in (400, 404):
                raise GraphAPIError(e.status_code, e.error_code, f'could not read list {list_id}: {e.message}') from e
            raise
        for task in found:
            task.list_name = names.get(list_id)
            tasks.append(task)
    return tasks


def format_error(error: Exception) -> Dict[str, Any]:
    '''format an error response.'''
    return {'error': str(error), 'success': False}


def format_success(data: Dict[str, Any], message: Optional[str] = None) -> Dict[str, Any]:
    '''format a success response.'''
    result = {'success': True, **data}
    if message:
        result['message'] = message
    return result


# =============================================================================
# mcp server and tools
# =============================================================================

# guidance sent to clients as the server instructions

INSTRUCTIONS = '''
these tools work on the user's microsoft to do task lists. get_lists returns the lists
themselves, with the list ids the other tools need. get_tasks returns the tasks inside one
list; it and the tools that find tasks by date give the task ids. a task can have steps, a
checklist inside it, which only get_steps shows; it gives the step ids. dates are YYYY-MM-DD.

common workflows:
- the user asks about their tasks for the day: use get_tasks_due_today.
- the user asks what's coming up, due this week, or overdue: use get_tasks_by_due_date_range,
  with include_overdue for tasks whose due date has passed.
- the user asks what they got done: use get_tasks_by_completed_date_range.
- the user says a task is done: use complete_task, not delete_task. for a step, complete_step.

deleting is permanent and can't be undone. use delete_task, delete_step or delete_list only
when the user asks to delete, never to tidy up, and confirm the name with the user before
deleting a list, since that deletes every task in it.
'''

# tool annotations: whether a tool only reads, and whether a write can overwrite or remove
# existing data. every tool works on the user's own task lists, so none is open world.

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
ADDS = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
CHANGES = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

# argument descriptions shared by several tools

ListId = Annotated[str, Field(description='the list id, from get_lists')]
ListIds = Annotated[List[str], Field(description='the ids of the lists to check, from get_lists')]
TaskId = Annotated[str, Field(
    description='the task id, from get_tasks or the tools that find tasks by date'
)]
TaskListId = Annotated[str, Field(description='the list the task is in, its list_id')]
StepId = Annotated[str, Field(description='the step id, from get_steps')]
StepTaskId = Annotated[str, Field(description='the task the step is in')]

mcp = FastMCP('todo MCP', instructions=INSTRUCTIONS)


# -----------------------------------------------------------------------------
# list management tools
# -----------------------------------------------------------------------------

@mcp.tool(annotations=READ_ONLY)
def get_lists() -> Dict[str, Any]:
    '''
    List the user's task lists themselves, with their ids.

    The list ids are what every other tool takes. For the tasks inside a list, use
    get_tasks.

    Returns:
        Dictionary with the lists, each with its list_id, name, and whether it is owned
        or shared
    '''
    try:
        with get_client() as client:
            lists = client.get_lists()
            return format_success({
                'count': len(lists),
                'lists': [lst.to_dict() for lst in lists],
            })
    except GraphAPIError as e:
        logger.error(f'error retrieving lists: {e}')
        return format_error(e)


@mcp.tool(annotations=ADDS)
def create_list(name: Annotated[str, Field(description='the name of the new list')]) -> Dict[str, Any]:
    '''
    Create a new task list.

    Returns:
        Dictionary with the created list and its list_id
    '''
    try:
        if not name or not name.strip():
            return format_error(ValueError('name is required'))

        with get_client() as client:
            lst = client.create_list(name.strip())
            logger.info(f"created list '{lst.display_name}'")
            return format_success({
                'list': lst.to_dict(),
            }, message='list created successfully')
    except GraphAPIError as e:
        logger.error(f'error creating list: {e}')
        return format_error(e)


@mcp.tool(annotations=CHANGES)
def update_list(
    list_id: ListId,
    name: Annotated[str, Field(description='the new name')],
) -> Dict[str, Any]:
    '''
    Rename a task list. The tasks in it are unchanged.

    Returns:
        Dictionary with the renamed list
    '''
    try:
        if not list_id or not list_id.strip():
            return format_error(ValueError('list_id is required'))
        if not name or not name.strip():
            return format_error(ValueError('name is required'))

        with get_client() as client:
            lst = client.update_list(list_id.strip(), name.strip())
            logger.info(f"renamed list {list_id} to '{lst.display_name}'")
            return format_success({
                'list': lst.to_dict(),
            }, message='list renamed successfully')
    except GraphAPIError as e:
        logger.error(f'error updating list: {e}')
        return format_error(e)


@mcp.tool(annotations=CHANGES)
def delete_list(list_id: ListId) -> Dict[str, Any]:
    '''
    Delete a task list permanently, with every task in it. This can't be undone.

    Only when the user asks to delete the list, and confirm its name with the user first.

    Returns:
        Dictionary confirming the deletion
    '''
    try:
        if not list_id or not list_id.strip():
            return format_error(ValueError('list_id is required'))

        with get_client() as client:
            client.delete_list(list_id.strip())
            logger.info(f'deleted list {list_id}')
            return format_success({
                'list_id': list_id,
            }, message='list deleted successfully')
    except GraphAPIError as e:
        logger.error(f'error deleting list: {e}')
        return format_error(e)


# -----------------------------------------------------------------------------
# task management tools
# -----------------------------------------------------------------------------

@mcp.tool(annotations=READ_ONLY)
def get_tasks(
    list_id: ListId,
    status: Annotated[str, Field(description='one of not_completed, completed, all')] = 'not_completed',
    limit: Annotated[int, Field(description='how many to return, from 1 to 1000')] = 100,
) -> Dict[str, Any]:
    '''
    List the tasks inside one task list, open tasks by default.

    For the lists themselves, use get_lists. To find tasks by due or completed date
    across several lists, use get_tasks_due_today, get_tasks_by_due_date_range or
    get_tasks_by_completed_date_range. Steps are not included; use get_steps for those.

    Returns:
        Dictionary with the tasks, each with its task_id and list_id, and truncated true
        if the list holds more than limit
    '''
    try:
        if not list_id or not list_id.strip():
            return format_error(ValueError('list_id is required'))
        if limit <= 0 or limit > 1000:
            return format_error(ValueError('limit must be between 1 and 1000'))

        filter_map = {
            'not_completed': "status ne 'completed'",
            'completed': "status eq 'completed'",
            'all': None,
        }
        filter_ = filter_map.get(status)
        if status not in filter_map:
            return format_error(ValueError(
                f"invalid status '{status}'. use 'not_completed', 'completed', or 'all'"
            ))

        with get_client() as client:
            # one more than the limit shows whether anything was left out
            tasks = client.get_tasks(list_id.strip(), filter_=filter_, top=limit + 1)
            return format_success({
                'count': min(len(tasks), limit),
                'truncated': len(tasks) > limit,
                'tasks': [task.to_dict() for task in tasks[:limit]],
                'list_id': list_id,
                'status_filter': status,
            })
    except GraphAPIError as e:
        logger.error(f'error retrieving tasks: {e}')
        return format_error(e)


@mcp.tool(annotations=ADDS)
def create_task(
    title: Annotated[str, Field(description='the task, in the words the user wants recorded')],
    list_id: ListId,
    due_date: Annotated[Optional[str], Field(
        description='the due date, as YYYY-MM-DD; required for recurrence'
    )] = None,
    body_text: Annotated[Optional[str], Field(description='notes on the task')] = None,
    importance: Annotated[str, Field(description='one of low, normal, high')] = 'normal',
    recurrence_type: Annotated[Optional[str], Field(
        description='one of daily, weekly, monthly, to make the task repeat'
    )] = None,
    recurrence_interval: Annotated[int, Field(
        description='repeat every this many days, weeks or months'
    )] = 1,
    recurrence_days_of_week: Annotated[Optional[List[str]], Field(
        description='for weekly, lowercase day names, e.g. ["monday", "friday"]'
    )] = None,
    recurrence_day_of_month: Annotated[Optional[int], Field(
        description='for monthly, the day from 1 to 31'
    )] = None,
) -> Dict[str, Any]:
    '''
    Add a task to a task list, optionally with a due date, notes, importance or recurrence.

    A repeating task needs a due_date, the first day it is due. For example:

        every day: recurrence_type='daily'
        every 3 weeks: recurrence_type='daily', recurrence_interval=21
        monday, wednesday and friday: recurrence_type='weekly',
            recurrence_days_of_week=['monday', 'wednesday', 'friday']
        the 15th of each month: recurrence_type='monthly', recurrence_day_of_month=15

    Returns:
        Dictionary with the created task and its task_id
    '''
    try:
        if not title or not title.strip():
            return format_error(ValueError('title is required'))
        if not list_id or not list_id.strip():
            return format_error(ValueError('list_id is required'))
        if importance not in ('low', 'normal', 'high'):
            return format_error(ValueError("importance must be 'low', 'normal', or 'high'"))

        parsed_due = None
        if due_date:
            try:
                parsed_due = parse_date(due_date)
            except (ValueError, TypeError):
                return format_error(ValueError(f'invalid due_date format: {due_date}'))

        recurrence = None
        if recurrence_type:
            if not parsed_due:
                return format_error(ValueError('due_date is required for recurring tasks'))

            if recurrence_type == 'daily':
                pattern = RecurrencePattern.daily(recurrence_interval)
            elif recurrence_type == 'weekly':
                if not recurrence_days_of_week:
                    return format_error(ValueError(
                        'recurrence_days_of_week required for weekly recurrence'
                    ))
                pattern = RecurrencePattern.weekly(recurrence_days_of_week, recurrence_interval)
            elif recurrence_type == 'monthly':
                if not recurrence_day_of_month:
                    return format_error(ValueError(
                        'recurrence_day_of_month required for monthly recurrence'
                    ))
                pattern = RecurrencePattern.monthly(recurrence_day_of_month, recurrence_interval)
            else:
                return format_error(ValueError(
                    f"invalid recurrence_type '{recurrence_type}'. "
                    "use 'daily', 'weekly', or 'monthly'"
                ))

            recurrence = Recurrence(pattern=pattern, start_date=parsed_due.isoformat())

        with get_client() as client:
            task = client.create_task(
                list_id=list_id.strip(),
                title=title.strip(),
                body=body_text.strip() if body_text else None,
                due_date=parsed_due,
                importance=importance,
                recurrence=recurrence,
            )
            logger.info(f"created task '{task.title}' in list {list_id}")
            return format_success({
                'task': task.to_dict(),
            }, message='task created successfully')
    except GraphAPIError as e:
        logger.error(f'error creating task: {e}')
        return format_error(e)


@mcp.tool(annotations=CHANGES)
def update_task(
    task_id: TaskId,
    list_id: TaskListId,
    title: Annotated[Optional[str], Field(description="the new title; it can't be blank")] = None,
    due_date: Annotated[Optional[str], Field(description='the new due date, as YYYY-MM-DD')] = None,
    body_text: Annotated[Optional[str], Field(
        description='new notes, replacing the existing notes; an empty string removes them'
    )] = None,
    importance: Annotated[Optional[str], Field(description='one of low, normal, high')] = None,
    status: Annotated[Optional[str], Field(
        description='one of notStarted, inProgress, completed, waitingOnOthers, deferred'
    )] = None,
    recurrence_type: Annotated[Optional[str], Field(
        description='one of daily, weekly, monthly, to make the task repeat or change how'
    )] = None,
    recurrence_interval: Annotated[int, Field(
        description='repeat every this many days, weeks or months'
    )] = 1,
    recurrence_days_of_week: Annotated[Optional[List[str]], Field(
        description='for weekly, lowercase day names, e.g. ["monday", "friday"]'
    )] = None,
    recurrence_day_of_month: Annotated[Optional[int], Field(
        description='for monthly, the day from 1 to 31'
    )] = None,
    remove_recurrence: Annotated[bool, Field(description='true to stop the task repeating')] = False,
    clear_due_date: Annotated[bool, Field(
        description="true to remove the due date; can't be combined with due_date or recurrence_type"
    )] = False,
) -> Dict[str, Any]:
    '''
    Change a task's title, due date, notes, importance, status or recurrence, or remove its
    due date or notes.

    Only the arguments given change; new values replace the old ones. To mark a task done,
    use complete_task.

    Returns:
        Dictionary with the updated task
    '''
    try:
        if not task_id or not task_id.strip():
            return format_error(ValueError('task_id is required'))
        if not list_id or not list_id.strip():
            return format_error(ValueError('list_id is required'))
        if title is not None and not title.strip():
            return format_error(ValueError("title can't be blank"))
        if clear_due_date and (due_date or recurrence_type):
            return format_error(ValueError("clear_due_date can't be combined with due_date or recurrence_type"))
        if importance is not None and importance not in ('low', 'normal', 'high'):
            return format_error(ValueError("importance must be 'low', 'normal', or 'high'"))
        if status is not None:
            valid_statuses = ('notStarted', 'inProgress', 'completed', 'waitingOnOthers', 'deferred')
            if status not in valid_statuses:
                return format_error(ValueError(f"invalid status. valid values: {', '.join(valid_statuses)}"))

        parsed_due = None
        if due_date:
            try:
                parsed_due = parse_date(due_date)
            except (ValueError, TypeError):
                return format_error(ValueError(f'invalid due_date format: {due_date}'))

        recurrence = None
        if recurrence_type and not remove_recurrence:
            if recurrence_type == 'daily':
                pattern = RecurrencePattern.daily(recurrence_interval)
            elif recurrence_type == 'weekly':
                if not recurrence_days_of_week:
                    return format_error(ValueError(
                        'recurrence_days_of_week required for weekly recurrence'
                    ))
                pattern = RecurrencePattern.weekly(recurrence_days_of_week, recurrence_interval)
            elif recurrence_type == 'monthly':
                if not recurrence_day_of_month:
                    return format_error(ValueError(
                        'recurrence_day_of_month required for monthly recurrence'
                    ))
                pattern = RecurrencePattern.monthly(recurrence_day_of_month, recurrence_interval)
            else:
                return format_error(ValueError(
                    f"invalid recurrence_type '{recurrence_type}'. "
                    "use 'daily', 'weekly', or 'monthly'"
                ))
            recurrence = Recurrence(pattern=pattern)

        # check if any updates provided
        has_updates = any([
            title is not None,
            due_date is not None,
            body_text is not None,
            importance is not None,
            status is not None,
            recurrence is not None,
            remove_recurrence,
            clear_due_date,
        ])
        if not has_updates:
            return format_error(ValueError('no updates provided'))

        with get_client() as client:
            task = client.update_task(
                list_id=list_id.strip(),
                task_id=task_id.strip(),
                title=title.strip() if title else None,
                body=body_text.strip() if body_text is not None else None,
                due_date=parsed_due,
                importance=importance,
                status=status,
                recurrence=recurrence,
                remove_recurrence=remove_recurrence,
                clear_due_date=clear_due_date,
            )
            logger.info(f'updated task {task_id}')
            return format_success({
                'task': task.to_dict(),
            })
    except (GraphAPIError, ValueError) as e:
        logger.error(f'error updating task {task_id}: {e}')
        return format_error(e)


@mcp.tool(annotations=ADDS)
def complete_task(task_id: TaskId, list_id: TaskListId) -> Dict[str, Any]:
    '''
    Mark a task as completed.

    Use this when the user says a task is done, not delete_task.

    Returns:
        Dictionary with the completed task
    '''
    try:
        if not task_id or not task_id.strip():
            return format_error(ValueError('task_id is required'))
        if not list_id or not list_id.strip():
            return format_error(ValueError('list_id is required'))

        with get_client() as client:
            task = client.complete_task(list_id.strip(), task_id.strip())
            logger.info(f'completed task {task_id}')
            return format_success({
                'task': task.to_dict(),
            }, message='task marked as completed')
    except GraphAPIError as e:
        logger.error(f'error completing task {task_id}: {e}')
        return format_error(e)


@mcp.tool(annotations=CHANGES)
def delete_task(task_id: TaskId, list_id: TaskListId) -> Dict[str, Any]:
    '''
    Delete a task permanently, with its steps. This can't be undone.

    To mark it done instead, use complete_task. Only when the user asks to delete it.

    Returns:
        Dictionary confirming the deletion
    '''
    try:
        if not task_id or not task_id.strip():
            return format_error(ValueError('task_id is required'))
        if not list_id or not list_id.strip():
            return format_error(ValueError('list_id is required'))

        with get_client() as client:
            client.delete_task(list_id.strip(), task_id.strip())
            logger.info(f'deleted task {task_id}')
            return format_success({
                'task_id': task_id,
            }, message='task deleted successfully')
    except GraphAPIError as e:
        logger.error(f'error deleting task {task_id}: {e}')
        return format_error(e)


# -----------------------------------------------------------------------------
# cross-list view tools
# -----------------------------------------------------------------------------

@mcp.tool(annotations=READ_ONLY)
def get_tasks_by_due_date_range(
    list_ids: ListIds,
    start_date: Annotated[Optional[str], Field(
        description='the first day, as YYYY-MM-DD; today if left out'
    )] = None,
    end_date: Annotated[Optional[str], Field(
        description='the last day, as YYYY-MM-DD; the same as start_date if left out'
    )] = None,
    include_overdue: Annotated[bool, Field(
        description='true to also include open tasks whose due date is before today'
    )] = False,
    include_no_due_date: Annotated[bool, Field(
        description='true to also include open tasks with no due date'
    )] = False,
) -> Dict[str, Any]:
    '''
    Find open tasks due between two dates in the given lists.

    Use it for what's coming up, due this week, or overdue. With no dates it finds the
    tasks due today. For example:

        list_ids=['abc'] -> due today
        list_ids=['abc'], start_date='2026-03-18' -> due on march 18
        list_ids=['abc', 'def'], start_date='2026-03-17', end_date='2026-03-23' -> due that week
        list_ids=['abc'], include_overdue=True -> due today, and everything overdue

    Returns:
        Dictionary with the tasks grouped as overdue, in_range and no_due_date, each with
        its task_id, list_id and list_name
    '''
    try:
        if not list_ids:
            return format_error(ValueError('list_ids is required'))

        # the machine's local day, so this stays right in the evening when utc has moved on
        today = date.today()

        if start_date:
            try:
                start = parse_date(start_date)
            except (ValueError, TypeError):
                return format_error(ValueError(f'invalid start_date format: {start_date}'))
        else:
            start = today

        if end_date:
            try:
                end = parse_date(end_date)
            except (ValueError, TypeError):
                return format_error(ValueError(f'invalid end_date format: {end_date}'))
        else:
            end = start

        if end < start:
            return format_error(ValueError('end_date must be >= start_date'))

        with get_client() as client:
            open_tasks = tasks_in_lists(client, list_ids, client.get_open_tasks)

        overdue_tasks: List[Dict] = []
        in_range_tasks: List[Dict] = []
        no_due_date_tasks: List[Dict] = []

        for task in open_tasks:
            task_dict = task.to_dict()

            if task.due_datetime:
                due = task.due_datetime.to_date()
                if due:
                    if start <= due <= end:
                        in_range_tasks.append(task_dict)
                    elif due < today and include_overdue:
                        overdue_tasks.append(task_dict)
            elif include_no_due_date:
                no_due_date_tasks.append(task_dict)

        result: Dict[str, Any] = {
            'start_date': start.isoformat(),
            'end_date': end.isoformat(),
            'counts': {
                'in_range': len(in_range_tasks),
                'overdue': len(overdue_tasks),
                'no_due_date': len(no_due_date_tasks),
                'total': len(in_range_tasks) + len(overdue_tasks) + len(no_due_date_tasks),
            },
            'tasks': in_range_tasks,
        }

        if include_overdue and overdue_tasks:
            result['overdue'] = overdue_tasks
        if include_no_due_date and no_due_date_tasks:
            result['no_due_date'] = no_due_date_tasks

        return format_success(result)
    except GraphAPIError as e:
        logger.error(f'error getting tasks by due date range: {e}')
        return format_error(e)


@mcp.tool(annotations=READ_ONLY)
def get_tasks_due_today(list_ids: ListIds) -> Dict[str, Any]:
    '''
    Find open tasks due today in the given lists.

    Overdue tasks are left out; for those, use get_tasks_by_due_date_range with
    include_overdue.

    Returns:
        Dictionary with today's date, the count, and the tasks, each with its task_id,
        list_id and list_name
    '''
    try:
        if not list_ids:
            return format_error(ValueError('list_ids is required'))

        # the machine's local day, so this stays right in the evening when utc has moved on
        today = date.today()

        with get_client() as client:
            open_tasks = tasks_in_lists(client, list_ids, client.get_open_tasks)

        tasks = [
            task.to_dict() for task in open_tasks
            if task.due_datetime and task.due_datetime.to_date() == today
        ]

        return format_success({
            'date': today.isoformat(),
            'count': len(tasks),
            'tasks': tasks,
        })
    except GraphAPIError as e:
        logger.error(f'error getting tasks due today: {e}')
        return format_error(e)


@mcp.tool(annotations=READ_ONLY)
def get_tasks_by_completed_date_range(
    list_ids: ListIds,
    start_date: Annotated[str, Field(description='the first day, as YYYY-MM-DD')],
    end_date: Annotated[Optional[str], Field(
        description='the last day, as YYYY-MM-DD; today if left out'
    )] = None,
) -> Dict[str, Any]:
    '''
    Find tasks completed between two dates in the given lists.

    Use it for what the user got done, such as a weekly review. For example:

        list_ids=['abc'], start_date='2026-03-10' -> done since march 10
        list_ids=['abc', 'def'], start_date='2026-03-01', end_date='2026-03-07' -> done that week

    Returns:
        Dictionary with total_completed, the tasks grouped by list (by_list), and how many
        were done each day (by_day)
    '''
    try:
        if not list_ids:
            return format_error(ValueError('list_ids is required'))

        try:
            start = parse_date(start_date)
        except (ValueError, TypeError):
            return format_error(ValueError(f'invalid start_date format: {start_date}'))

        if end_date:
            try:
                end = parse_date(end_date)
            except (ValueError, TypeError):
                return format_error(ValueError(f'invalid end_date format: {end_date}'))
        else:
            # the machine's local day, so this stays right in the evening when utc has moved on
            end = date.today()

        if end < start:
            return format_error(ValueError('end_date must be >= start_date'))

        with get_client() as client:
            tasks = tasks_in_lists(
                client, list_ids, lambda list_id: client.get_tasks_by_completed_range(list_id, start, end)
            )

        # group by list
        by_list: Dict[str, List[Dict]] = {}
        by_day: Dict[str, int] = {}

        for task in tasks:
            list_name = task.list_name or 'Unknown'
            if list_name not in by_list:
                by_list[list_name] = []
            by_list[list_name].append(task.to_dict())

            # count by day
            if task.completed_datetime:
                completed_date = task.completed_datetime.to_date()
                if completed_date:
                    day_str = completed_date.isoformat()
                    by_day[day_str] = by_day.get(day_str, 0) + 1

        # calculate daily average
        total_days = (end - start).days + 1
        daily_average = round(len(tasks) / total_days, 1) if total_days > 0 else 0

        return format_success({
            'start_date': start.isoformat(),
            'end_date': end.isoformat(),
            'total_completed': len(tasks),
            'daily_average': daily_average,
            'by_list': [
                {'list_name': name, 'count': len(task_list), 'tasks': task_list}
                for name, task_list in sorted(by_list.items())
            ],
            'by_day': dict(sorted(by_day.items())),
        })
    except GraphAPIError as e:
        logger.error(f'error getting completed tasks: {e}')
        return format_error(e)


# -----------------------------------------------------------------------------
# step (checklist item) tools
# -----------------------------------------------------------------------------

@mcp.tool(annotations=READ_ONLY)
def get_steps(task_id: TaskId, list_id: TaskListId) -> Dict[str, Any]:
    '''
    List a task's steps, the checklist inside it, with whether each is checked off.

    The other tools don't show steps.

    Returns:
        Dictionary with the steps, each with its item_id, name and is_checked
    '''
    try:
        if not task_id or not task_id.strip():
            return format_error(ValueError('task_id is required'))
        if not list_id or not list_id.strip():
            return format_error(ValueError('list_id is required'))

        with get_client() as client:
            items = client.get_checklist_items(list_id.strip(), task_id.strip())
            return format_success({
                'count': len(items),
                'steps': [item.to_dict() for item in items],
                'task_id': task_id,
                'list_id': list_id,
            })
    except GraphAPIError as e:
        logger.error(f'error getting steps: {e}')
        return format_error(e)


@mcp.tool(annotations=ADDS)
def create_step(
    task_id: TaskId,
    list_id: TaskListId,
    name: Annotated[str, Field(description='the step, in the words the user wants recorded')],
) -> Dict[str, Any]:
    '''
    Add a step to a task's checklist.

    Returns:
        Dictionary with the created step and its item_id
    '''
    try:
        if not task_id or not task_id.strip():
            return format_error(ValueError('task_id is required'))
        if not list_id or not list_id.strip():
            return format_error(ValueError('list_id is required'))
        if not name or not name.strip():
            return format_error(ValueError('name is required'))

        with get_client() as client:
            item = client.create_checklist_item(list_id.strip(), task_id.strip(), name.strip())
            logger.info(f"created step '{item.display_name}' for task {task_id}")
            return format_success({
                'step': item.to_dict(),
            }, message='step created successfully')
    except GraphAPIError as e:
        logger.error(f'error creating step: {e}')
        return format_error(e)


@mcp.tool(annotations=CHANGES)
def update_step(
    item_id: StepId,
    task_id: StepTaskId,
    list_id: TaskListId,
    name: Annotated[Optional[str], Field(description='new text for the step')] = None,
    is_checked: Annotated[Optional[bool], Field(
        description='true to check it off, false to uncheck it'
    )] = None,
) -> Dict[str, Any]:
    '''
    Rename a step, or check or uncheck it.

    Only the arguments given change.

    Returns:
        Dictionary with the updated step
    '''
    try:
        if not item_id or not item_id.strip():
            return format_error(ValueError('item_id is required'))
        if not task_id or not task_id.strip():
            return format_error(ValueError('task_id is required'))
        if not list_id or not list_id.strip():
            return format_error(ValueError('list_id is required'))
        if name is None and is_checked is None:
            return format_error(ValueError('at least one of name or is_checked must be provided'))

        with get_client() as client:
            item = client.update_checklist_item(
                list_id.strip(),
                task_id.strip(),
                item_id.strip(),
                display_name=name.strip() if name else None,
                is_checked=is_checked,
            )
            logger.info(f'updated step {item_id}')
            return format_success({
                'step': item.to_dict(),
            })
    except (GraphAPIError, ValueError) as e:
        logger.error(f'error updating step {item_id}: {e}')
        return format_error(e)


@mcp.tool(annotations=ADDS)
def complete_step(item_id: StepId, task_id: StepTaskId, list_id: TaskListId) -> Dict[str, Any]:
    '''
    Check a step off.

    Use this when the user says a step is done, not delete_step.

    Returns:
        Dictionary with the completed step
    '''
    try:
        if not item_id or not item_id.strip():
            return format_error(ValueError('item_id is required'))
        if not task_id or not task_id.strip():
            return format_error(ValueError('task_id is required'))
        if not list_id or not list_id.strip():
            return format_error(ValueError('list_id is required'))

        with get_client() as client:
            item = client.complete_checklist_item(list_id.strip(), task_id.strip(), item_id.strip())
            logger.info(f'completed step {item_id}')
            return format_success({
                'step': item.to_dict(),
            }, message='step marked as completed')
    except GraphAPIError as e:
        logger.error(f'error completing step {item_id}: {e}')
        return format_error(e)


@mcp.tool(annotations=CHANGES)
def delete_step(item_id: StepId, task_id: StepTaskId, list_id: TaskListId) -> Dict[str, Any]:
    '''
    Delete a step from a task's checklist permanently. This can't be undone.

    To check it off instead, use complete_step. Only when the user asks to delete it.

    Returns:
        Dictionary confirming the deletion
    '''
    try:
        if not item_id or not item_id.strip():
            return format_error(ValueError('item_id is required'))
        if not task_id or not task_id.strip():
            return format_error(ValueError('task_id is required'))
        if not list_id or not list_id.strip():
            return format_error(ValueError('list_id is required'))

        with get_client() as client:
            client.delete_checklist_item(list_id.strip(), task_id.strip(), item_id.strip())
            logger.info(f'deleted step {item_id}')
            return format_success({
                'item_id': item_id,
            }, message='step deleted successfully')
    except GraphAPIError as e:
        logger.error(f'error deleting step {item_id}: {e}')
        return format_error(e)


# =============================================================================
# entry point
# =============================================================================

def run():
    '''main entry point for todo-mcp server.'''
    logging.basicConfig(
        level=logging.DEBUG if os.getenv('LOG_LEVEL', 'info').lower() == 'debug' else logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )

    args = parse_args()

    if not args.client_id:
        logger.error('client id is required (--client-id or TODO_CLIENT_ID)')
        raise SystemExit(1)
    if not args.client_secret:
        logger.error('client secret is required (--client-secret or TODO_CLIENT_SECRET)')
        raise SystemExit(1)

    if args.auth:
        try:
            run_auth_flow(args.client_id, args.client_secret)
        except Exception as e:
            logger.error(f'authorization failed: {e}')
            raise SystemExit(1)
        return

    if not args.refresh_token:
        logger.error('refresh token is required (--refresh-token or TODO_REFRESH_TOKEN)')
        logger.error('run with --auth to complete the one-time authorization flow')
        raise SystemExit(1)

    global _client, _client_id, _client_secret

    logger.info('starting microsoft todo mcp server')

    try:
        token = bootstrap_token(args.client_id, args.client_secret, args.refresh_token)
        _client_id = args.client_id
        _client_secret = args.client_secret
        _client = GraphClient(args.client_id, args.client_secret, token)

        # verify connection
        with _client as client:
            client.get_lists(top=1)
        logger.info('successfully connected to microsoft todo')
    except Exception as e:
        logger.error(f'failed to connect to microsoft todo: {e}')
        raise SystemExit(1)

    try:
        mcp.run()
    except KeyboardInterrupt:
        logger.info('server shutdown requested')
    except Exception as e:
        logger.error(f'server error: {e}')
        raise SystemExit(1)


if __name__ == '__main__':
    run()
