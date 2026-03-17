import argparse
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from dateutil import parser as date_parser
from fastmcp import FastMCP
from pymstodo import ToDoConnection, TaskStatusFilter
from requests_oauthlib import OAuth2Session

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

# module-level client set during run()
_client: Optional[ToDoConnection] = None


# auth

def get_auth_url(client_id: str) -> str:
    oa_sess = OAuth2Session(client_id, scope=_SCOPE, redirect_uri=_REDIRECT_URI)
    authorization_url, _ = oa_sess.authorization_url(_AUTH_URL)
    return authorization_url


def get_token_from_code(client_id: str, client_secret: str, redirect_resp: str) -> dict:
    oa_sess = OAuth2Session(client_id, scope=_SCOPE, redirect_uri=_REDIRECT_URI)
    return oa_sess.fetch_token(_TOKEN_URL, client_secret=client_secret, authorization_response=redirect_resp)


def bootstrap_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    ''' exchange a refresh token for a full token object '''
    synthetic = {
        'token_type': 'Bearer',
        'refresh_token': refresh_token,
        'access_token': 'placeholder',
        'expires_at': 0,
    }
    oa_sess = OAuth2Session(client_id, scope=_SCOPE, token=synthetic, redirect_uri=_REDIRECT_URI)
    return oa_sess.refresh_token(_TOKEN_URL, client_id=client_id, client_secret=client_secret)


def run_auth_flow(client_id: str, client_secret: str) -> None:
    auth_url = get_auth_url(client_id)
    print(f'\nvisit this url to authorize:\n\n  {auth_url}\n')
    redirect_resp = input('paste the full redirect url here:\n> ').strip()
    token = get_token_from_code(client_id, client_secret, redirect_resp)
    print('\nauthorization complete! set this environment variable:\n')
    print(f'  TODO_REFRESH_TOKEN={token["refresh_token"]}\n')


# arg parsing

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


# formatters

def parse_due_date(due_date_str: str) -> datetime:
    return date_parser.parse(due_date_str).replace(tzinfo=timezone.utc)


def format_task(task) -> Dict[str, Any]:
    return {
        'task_id': task.task_id,
        'title': task.title,
        'status': task.status,
        'importance': task.importance,
        'due_date': task.due_date.date().isoformat() if task.due_date else None,
        'body_text': task.body_text,
        'created_date': task.created_date.date().isoformat() if task.created_date else None,
        'last_modified': task.last_mod_date.date().isoformat() if task.last_mod_date else None,
    }


def format_list(task_list) -> Dict[str, Any]:
    return {
        'list_id': task_list.list_id,
        'name': task_list.displayName,
        'is_owner': task_list.isOwner,
        'is_shared': task_list.isShared,
    }


def get_client() -> ToDoConnection:
    if _client is None:
        raise RuntimeError('client not initialized')
    return _client


# mcp server

mcp = FastMCP('todo MCP')


@mcp.tool
def get_lists() -> Dict[str, Any]:
    '''
    retrieve all microsoft todo task lists.

    returns:
        dictionary containing all task lists with their ids and names
    '''
    try:
        client = get_client()
        lists = client.get_lists()
        return {
            'count': len(lists),
            'lists': [format_list(task_list) for task_list in lists],
            'success': True
        }
    except Exception as e:
        logger.error(f'error retrieving lists: {e}')
        return {'error': str(e), 'success': False}


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
        status_map = {
            'not_completed': TaskStatusFilter.NOT_COMPLETED,
            'completed': TaskStatusFilter.COMPLETED,
            'all': TaskStatusFilter.ALL,
        }
        status_filter = status_map.get(status)
        if status_filter is None:
            return {'error': f"invalid status '{status}'. use 'not_completed', 'completed', or 'all'", 'success': False}

        if limit <= 0 or limit > 1000:
            return {'error': 'limit must be between 1 and 1000', 'success': False}

        client = get_client()
        tasks = client.get_tasks(list_id, limit=limit, status=status_filter)

        return {
            'count': len(tasks),
            'tasks': [format_task(task) for task in tasks],
            'list_id': list_id,
            'status_filter': status,
            'success': True
        }
    except Exception as e:
        logger.error(f'error retrieving tasks: {e}')
        return {'error': str(e), 'success': False}


@mcp.tool
def create_task(
    title: str,
    list_id: str,
    due_date: Optional[str] = None,
    body_text: Optional[str] = None,
    importance: str = 'normal'
) -> Dict[str, Any]:
    '''
    create a new task in a task list.

    args:
        title: the task title (required)
        list_id: the id of the task list (required)
        due_date: due date in yyyy-mm-dd format (optional)
        body_text: additional notes or description (optional)
        importance: priority level - 'low', 'normal' (default), or 'high'

    returns:
        dictionary containing the created task
    '''
    try:
        if not title or not title.strip():
            return {'error': 'title is required', 'success': False}
        if not list_id or not list_id.strip():
            return {'error': 'list_id is required', 'success': False}
        if importance not in ('low', 'normal', 'high'):
            return {'error': "importance must be 'low', 'normal', or 'high'", 'success': False}

        parsed_due = None
        if due_date:
            try:
                parsed_due = parse_due_date(due_date)
            except (ValueError, TypeError):
                return {'error': f"invalid due_date format: {due_date}. use yyyy-mm-dd format.", 'success': False}

        client = get_client()

        task = client.create_task(
            title=title.strip(),
            list_id=list_id.strip(),
            due_date=parsed_due,
            body_text=body_text.strip() if body_text else None
        )

        # importance is not supported in create_task, apply via update if non-default
        if importance != 'normal':
            task = client.update_task(task.task_id, list_id.strip(), importance=importance)

        logger.info(f"created task '{task.title}' in list {list_id}")
        return {
            'task': format_task(task),
            'message': 'task created successfully',
            'success': True
        }
    except Exception as e:
        logger.error(f'error creating task: {e}')
        return {'error': str(e), 'success': False}


@mcp.tool
def update_task(
    task_id: str,
    list_id: str,
    title: Optional[str] = None,
    due_date: Optional[str] = None,
    body_text: Optional[str] = None,
    importance: Optional[str] = None,
    status: Optional[str] = None
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
        status: 'notStarted', 'inProgress', 'completed', 'waitingOnOthers', or 'deferred' (optional)

    returns:
        dictionary containing the updated task
    '''
    try:
        updates: Dict[str, Any] = {}

        if title is not None:
            updates['title'] = title.strip()

        if importance is not None:
            if importance not in ('low', 'normal', 'high'):
                return {'error': "importance must be 'low', 'normal', or 'high'", 'success': False}
            updates['importance'] = importance

        if status is not None:
            valid_statuses = ('notStarted', 'inProgress', 'completed', 'waitingOnOthers', 'deferred')
            if status not in valid_statuses:
                return {'error': f"invalid status '{status}'. valid values: {', '.join(valid_statuses)}", 'success': False}
            updates['status'] = status

        if body_text is not None:
            updates['body'] = {'content': body_text.strip(), 'contentType': 'text'}

        if due_date is not None:
            try:
                parsed = parse_due_date(due_date)
                updates['dueDateTime'] = {
                    'dateTime': parsed.strftime('%Y-%m-%dT%H:%M:%S.0000000'),
                    'timeZone': 'UTC'
                }
            except (ValueError, TypeError):
                return {'error': f"invalid due_date format: {due_date}. use yyyy-mm-dd format.", 'success': False}

        if not updates:
            return {'error': 'no updates provided. at least one field must be specified.', 'success': False}

        client = get_client()
        task = client.update_task(task_id, list_id, **updates)

        logger.info(f"updated task {task_id}")
        return {
            'task': format_task(task),
            'success': True
        }
    except Exception as e:
        logger.error(f'error updating task {task_id}: {e}')
        return {'error': str(e), 'success': False}


@mcp.tool
def complete_task(
    task_id: str,
    list_id: str
) -> Dict[str, Any]:
    '''
    mark a task as completed.

    args:
        task_id: the id of the task (required)
        list_id: the id of the task list (required)

    returns:
        dictionary containing the completed task
    '''
    try:
        client = get_client()
        task = client.complete_task(task_id, list_id)

        logger.info(f"completed task {task_id}")
        return {
            'task': format_task(task),
            'message': 'task marked as completed',
            'success': True
        }
    except Exception as e:
        logger.error(f'error completing task {task_id}: {e}')
        return {'error': str(e), 'success': False}


@mcp.tool
def delete_task(
    task_id: str,
    list_id: str
) -> Dict[str, Any]:
    '''
    delete a task permanently.

    args:
        task_id: the id of the task (required)
        list_id: the id of the task list (required)

    returns:
        dictionary confirming deletion
    '''
    try:
        client = get_client()
        client.delete_task(task_id, list_id)

        logger.info(f"deleted task {task_id}")
        return {
            'task_id': task_id,
            'message': 'task deleted successfully',
            'success': True
        }
    except Exception as e:
        logger.error(f'error deleting task {task_id}: {e}')
        return {'error': str(e), 'success': False}


def run():
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

    global _client

    logger.info('starting microsoft todo mcp server')

    try:
        token = bootstrap_token(args.client_id, args.client_secret, args.refresh_token)
        _client = ToDoConnection(
            client_id=args.client_id,
            client_secret=args.client_secret,
            token=token
        )
        _client.get_lists(limit=1)
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
