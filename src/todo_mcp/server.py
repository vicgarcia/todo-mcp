'''
fastmcp server for microsoft todo with 17 tools.

direct microsoft graph api access via httpx.
'''

import argparse
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from dateutil import parser as date_parser
from fastmcp import FastMCP

from todo_mcp.graph_client import GraphAPIError, GraphClient
from todo_mcp.schema import DateTimeTimeZone, Recurrence, RecurrencePattern

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
    return date_parser.parse(date_str).date()


def get_client() -> GraphClient:
    '''get the initialized graph client.'''
    if _client is None:
        raise RuntimeError('client not initialized')
    return _client


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

mcp = FastMCP('todo MCP')


# -----------------------------------------------------------------------------
# list management tools
# -----------------------------------------------------------------------------

@mcp.tool
def get_lists() -> Dict[str, Any]:
    '''
    retrieve all microsoft todo task lists.

    returns:
        dictionary containing all task lists with their ids and names
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


@mcp.tool
def create_list(name: str) -> Dict[str, Any]:
    '''
    create a new task list.

    args:
        name: the name for the new list (required)

    returns:
        dictionary containing the created list
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


@mcp.tool
def update_list(list_id: str, name: str) -> Dict[str, Any]:
    '''
    rename a task list.

    args:
        list_id: the id of the list to rename (required)
        name: the new name for the list (required)

    returns:
        dictionary containing the updated list
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


@mcp.tool
def delete_list(list_id: str) -> Dict[str, Any]:
    '''
    delete a task list permanently.

    args:
        list_id: the id of the list to delete (required)

    returns:
        dictionary confirming deletion
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

@mcp.tool
def get_tasks(
    list_id: str,
    status: str = 'not_completed',
    limit: int = 100
) -> Dict[str, Any]:
    '''
    retrieve tasks from a task list.

    args:
        list_id: the id of the task list (required)
        status: filter by status - 'not_completed' (default), 'completed', or 'all'
        limit: maximum number of tasks to return (default: 100, max: 1000)

    returns:
        dictionary containing the tasks
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
            tasks = client.get_tasks(list_id.strip(), filter_=filter_, top=limit)
            return format_success({
                'count': len(tasks),
                'tasks': [task.to_dict() for task in tasks],
                'list_id': list_id,
                'status_filter': status,
            })
    except GraphAPIError as e:
        logger.error(f'error retrieving tasks: {e}')
        return format_error(e)


@mcp.tool
def create_task(
    title: str,
    list_id: str,
    due_date: Optional[str] = None,
    body_text: Optional[str] = None,
    importance: str = 'normal',
    recurrence_type: Optional[str] = None,
    recurrence_interval: int = 1,
    recurrence_days_of_week: Optional[List[str]] = None,
    recurrence_day_of_month: Optional[int] = None,
) -> Dict[str, Any]:
    '''
    create a new task with optional recurrence.

    args:
        title: task title (required)
        list_id: the id of the task list (required)
        due_date: due date in yyyy-mm-dd format (optional, required for recurrence)
        body_text: additional notes or description (optional)
        importance: priority level - 'low', 'normal' (default), or 'high'
        recurrence_type: 'daily', 'weekly', or 'monthly' (optional)
        recurrence_interval: repeat every N days/weeks/months (default: 1)
        recurrence_days_of_week: for weekly, e.g. ['monday', 'friday'] (optional)
        recurrence_day_of_month: for monthly, day of month 1-31 (optional)

    returns:
        dictionary containing the created task

    examples:
        daily task: recurrence_type='daily', recurrence_interval=1
        every 3 weeks: recurrence_type='daily', recurrence_interval=21
        mon/wed/fri: recurrence_type='weekly', recurrence_days_of_week=['monday','wednesday','friday']
        15th monthly: recurrence_type='monthly', recurrence_day_of_month=15
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


@mcp.tool
def update_task(
    task_id: str,
    list_id: str,
    title: Optional[str] = None,
    due_date: Optional[str] = None,
    body_text: Optional[str] = None,
    importance: Optional[str] = None,
    status: Optional[str] = None,
    recurrence_type: Optional[str] = None,
    recurrence_interval: int = 1,
    recurrence_days_of_week: Optional[List[str]] = None,
    recurrence_day_of_month: Optional[int] = None,
    remove_recurrence: bool = False,
) -> Dict[str, Any]:
    '''
    update properties of an existing task.

    args:
        task_id: the id of the task (required)
        list_id: the id of the task list (required)
        title: new task title (optional)
        due_date: new due date in yyyy-mm-dd format (optional)
        body_text: new notes or description (optional)
        importance: 'low', 'normal', or 'high' (optional)
        status: 'notStarted', 'inProgress', 'completed', 'waitingOnOthers', 'deferred' (optional)
        recurrence_type: 'daily', 'weekly', or 'monthly' to set/change recurrence (optional)
        recurrence_interval: repeat every N days/weeks/months (default: 1)
        recurrence_days_of_week: for weekly recurrence (optional)
        recurrence_day_of_month: for monthly recurrence (optional)
        remove_recurrence: set True to remove recurrence from task (default: False)

    returns:
        dictionary containing the updated task
    '''
    try:
        if not task_id or not task_id.strip():
            return format_error(ValueError('task_id is required'))
        if not list_id or not list_id.strip():
            return format_error(ValueError('list_id is required'))
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
        ])
        if not has_updates:
            return format_error(ValueError('no updates provided'))

        with get_client() as client:
            task = client.update_task(
                list_id=list_id.strip(),
                task_id=task_id.strip(),
                title=title.strip() if title else None,
                body=body_text.strip() if body_text else None,
                due_date=parsed_due,
                importance=importance,
                status=status,
                recurrence=recurrence,
                remove_recurrence=remove_recurrence,
            )
            logger.info(f'updated task {task_id}')
            return format_success({
                'task': task.to_dict(),
            })
    except GraphAPIError as e:
        logger.error(f'error updating task {task_id}: {e}')
        return format_error(e)


@mcp.tool
def complete_task(task_id: str, list_id: str) -> Dict[str, Any]:
    '''
    mark a task as completed.

    args:
        task_id: the id of the task (required)
        list_id: the id of the task list (required)

    returns:
        dictionary containing the completed task
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


@mcp.tool
def delete_task(task_id: str, list_id: str) -> Dict[str, Any]:
    '''
    delete a task permanently.

    args:
        task_id: the id of the task (required)
        list_id: the id of the task list (required)

    returns:
        dictionary confirming deletion
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

@mcp.tool
def get_tasks_by_due_date_range(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    include_overdue: bool = True,
    include_no_due_date: bool = False,
) -> Dict[str, Any]:
    '''
    get tasks due within a date range across ALL lists.

    args:
        start_date: start of date range (yyyy-mm-dd), defaults to today
        end_date: end of date range (yyyy-mm-dd), defaults to start_date
        include_overdue: also return tasks with past due dates (default: True)
        include_no_due_date: include tasks without a due date (default: False)

    returns:
        dictionary with tasks grouped by category (overdue, in_range, no_due_date)

    examples:
        get_tasks_by_due_date_range() -> today's tasks
        get_tasks_by_due_date_range(start_date='2026-03-18') -> tomorrow's tasks
        get_tasks_by_due_date_range(start_date='2026-03-17', end_date='2026-03-23') -> this week
    '''
    try:
        today = datetime.now(timezone.utc).date()

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
            all_tasks = client.get_all_tasks()

        overdue_tasks: List[Dict] = []
        in_range_tasks: List[Dict] = []
        no_due_date_tasks: List[Dict] = []

        for task in all_tasks:
            if task.status == 'completed':
                continue

            task_dict = task.to_dict()

            if task.due_datetime:
                due = task.due_datetime.to_date()
                if due:
                    if due < start and include_overdue:
                        overdue_tasks.append(task_dict)
                    elif start <= due <= end:
                        in_range_tasks.append(task_dict)
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


@mcp.tool
def get_tasks_by_completed_date_range(
    start_date: str,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    '''
    get tasks completed within a date range across ALL lists.

    uses server-side filtering for efficiency. ideal for weekly reporting.

    args:
        start_date: start of date range (yyyy-mm-dd), required
        end_date: end of date range (yyyy-mm-dd), defaults to today

    returns:
        dictionary with completed tasks grouped by list, with summary stats

    examples:
        get_tasks_by_completed_date_range(start_date='2026-03-10') -> completed since march 10
        get_tasks_by_completed_date_range(start_date='2026-03-01', end_date='2026-03-07') -> first week
    '''
    try:
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
            end = datetime.now(timezone.utc).date()

        if end < start:
            return format_error(ValueError('end_date must be >= start_date'))

        with get_client() as client:
            tasks = client.get_tasks_by_completed_range(start, end)

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
# subtask (checklist item) tools
# -----------------------------------------------------------------------------

@mcp.tool
def get_subtasks(task_id: str, list_id: str) -> Dict[str, Any]:
    '''
    get all subtasks (checklist items) for a task.

    args:
        task_id: the id of the parent task (required)
        list_id: the id of the task list (required)

    returns:
        dictionary containing the subtasks
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
                'subtasks': [item.to_dict() for item in items],
                'task_id': task_id,
                'list_id': list_id,
            })
    except GraphAPIError as e:
        logger.error(f'error getting subtasks: {e}')
        return format_error(e)


@mcp.tool
def create_subtask(task_id: str, list_id: str, name: str) -> Dict[str, Any]:
    '''
    create a new subtask (checklist item) for a task.

    args:
        task_id: the id of the parent task (required)
        list_id: the id of the task list (required)
        name: the subtask name/description (required)

    returns:
        dictionary containing the created subtask
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
            logger.info(f"created subtask '{item.display_name}' for task {task_id}")
            return format_success({
                'subtask': item.to_dict(),
            }, message='subtask created successfully')
    except GraphAPIError as e:
        logger.error(f'error creating subtask: {e}')
        return format_error(e)


@mcp.tool
def update_subtask(
    item_id: str,
    task_id: str,
    list_id: str,
    name: Optional[str] = None,
    is_checked: Optional[bool] = None,
) -> Dict[str, Any]:
    '''
    update a subtask (checklist item).

    args:
        item_id: the id of the subtask (required)
        task_id: the id of the parent task (required)
        list_id: the id of the task list (required)
        name: new name for the subtask (optional)
        is_checked: True to mark as completed, False to uncheck (optional)

    returns:
        dictionary containing the updated subtask
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
            logger.info(f'updated subtask {item_id}')
            return format_success({
                'subtask': item.to_dict(),
            })
    except GraphAPIError as e:
        logger.error(f'error updating subtask {item_id}: {e}')
        return format_error(e)


@mcp.tool
def complete_subtask(item_id: str, task_id: str, list_id: str) -> Dict[str, Any]:
    '''
    mark a subtask (checklist item) as completed.

    args:
        item_id: the id of the subtask (required)
        task_id: the id of the parent task (required)
        list_id: the id of the task list (required)

    returns:
        dictionary containing the completed subtask
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
            logger.info(f'completed subtask {item_id}')
            return format_success({
                'subtask': item.to_dict(),
            }, message='subtask marked as completed')
    except GraphAPIError as e:
        logger.error(f'error completing subtask {item_id}: {e}')
        return format_error(e)


@mcp.tool
def delete_subtask(item_id: str, task_id: str, list_id: str) -> Dict[str, Any]:
    '''
    delete a subtask (checklist item) permanently.

    args:
        item_id: the id of the subtask (required)
        task_id: the id of the parent task (required)
        list_id: the id of the task list (required)

    returns:
        dictionary confirming deletion
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
            logger.info(f'deleted subtask {item_id}')
            return format_success({
                'item_id': item_id,
            }, message='subtask deleted successfully')
    except GraphAPIError as e:
        logger.error(f'error deleting subtask {item_id}: {e}')
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
