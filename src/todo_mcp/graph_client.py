'''
microsoft graph api client for todo operations.

encapsulates authentication, request handling, and api bug workarounds.
based on live testing documented in GRAPH_API_REFERENCE.md.
'''

import logging
import time
from datetime import date
from typing import Any, Dict, List, Optional

import httpx

from todo_mcp.schema import (
    ChecklistItem,
    DateTimeTimeZone,
    Recurrence,
    RecurrencePattern,
    TodoList,
    TodoTask,
)

logger = logging.getLogger(__name__)


class GraphAPIError(Exception):
    '''exception for microsoft graph api errors.'''

    def __init__(self, status_code: int, error_code: str, message: str):
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        super().__init__(f'{error_code}: {message}')


class GraphClient:
    '''
    microsoft graph api client for todo operations.

    handles authentication, token refresh, and api bug workarounds.

    usage:
        with GraphClient(client_id, client_secret, token) as client:
            lists = client.get_lists()
    '''

    TOKEN_URL = 'https://login.microsoftonline.com/common/oauth2/v2.0/token'
    GRAPH_URL = 'https://graph.microsoft.com/v1.0'
    MAX_RETRIES = 3
    MAX_RETRY_DELAY = 10.0  # seconds; claude is waiting on the call
    SCOPE = 'openid offline_access Tasks.ReadWrite'

    def __init__(self, client_id: str, client_secret: str, token: Dict[str, Any]):
        self._client_id = client_id
        self._client_secret = client_secret
        self._token = token
        self._http: Optional[httpx.Client] = None

    def __enter__(self) -> 'GraphClient':
        self._http = httpx.Client(timeout=30.0)
        return self

    def __exit__(self, *args) -> None:
        if self._http:
            self._http.close()
            self._http = None

    @property
    def token(self) -> Dict[str, Any]:
        '''current token object.'''
        return self._token

    # =========================================================================
    # authentication
    # =========================================================================

    def _ensure_valid_token(self) -> None:
        '''refresh token if expiring within 5 minutes.'''
        expires_at = self._token.get('expires_at', 0)
        if time.time() >= expires_at - 300:
            self._refresh_token()

    def _refresh_token(self) -> None:
        '''refresh the access token.'''
        logger.debug('refreshing access token')
        response = self._http.post(self.TOKEN_URL, data={
            'client_id': self._client_id,
            'client_secret': self._client_secret,
            'refresh_token': self._token['refresh_token'],
            'grant_type': 'refresh_token',
            'scope': self.SCOPE,
        })
        if response.status_code >= 400:
            raise GraphAPIError(
                response.status_code,
                'TokenRefreshError',
                f'failed to refresh token: {response.text}'
            )
        data = response.json()
        # preserve refresh_token if not returned (some flows don't return it)
        if 'refresh_token' not in data:
            data['refresh_token'] = self._token['refresh_token']
        # calculate expires_at from expires_in
        if 'expires_in' in data and 'expires_at' not in data:
            data['expires_at'] = time.time() + data['expires_in']
        self._token = data
        logger.debug('token refreshed successfully')

    def _headers(self) -> Dict[str, str]:
        '''get request headers with auth.'''
        self._ensure_valid_token()
        return {
            'Authorization': f"Bearer {self._token['access_token']}",
            'Content-Type': 'application/json',
        }

    # =========================================================================
    # low-level request methods
    # =========================================================================

    def _retry_delay(self, response: httpx.Response) -> float:
        '''seconds to wait before retrying; retry-after may also be an http date.'''
        try:
            delay = float(response.headers.get('Retry-After', 1))
        except ValueError:
            delay = 1.0
        return min(max(delay, 0.0), self.MAX_RETRY_DELAY)

    def _request(
        self,
        method: str,
        path: str,
        json: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        '''make a request to graph api.'''
        url = f'{self.GRAPH_URL}{path}'
        for attempt in range(self.MAX_RETRIES + 1):
            response = self._http.request(
                method,
                url,
                headers=self._headers(),
                json=json,
                params=params
            )
            if response.status_code != 429 or attempt == self.MAX_RETRIES:
                break
            # graph limits how many requests can run against a mailbox at once
            delay = self._retry_delay(response)
            logger.warning(f'rate limited by graph, retrying in {delay}s')
            time.sleep(delay)

        if response.status_code >= 400:
            try:
                error = response.json().get('error', {})
                error_code = error.get('code', 'UnknownError')
                error_message = error.get('message', response.text)
            except Exception:
                error_code = 'UnknownError'
                error_message = response.text
            raise GraphAPIError(response.status_code, error_code, error_message)

        if response.status_code == 204:  # no content (delete)
            return {}
        return response.json()

    def _get(self, path: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        return self._request('GET', path, params=params)

    def _post(self, path: str, json: Dict) -> Dict[str, Any]:
        return self._request('POST', path, json=json)

    def _patch(self, path: str, json: Dict) -> Dict[str, Any]:
        return self._request('PATCH', path, json=json)

    def _delete(self, path: str) -> Dict[str, Any]:
        return self._request('DELETE', path)

    # =========================================================================
    # list operations
    # =========================================================================

    def get_lists(self, top: int = 100) -> List[TodoList]:
        '''get all task lists.'''
        data = self._get('/me/todo/lists', params={'$top': top})
        return [TodoList.from_api(item) for item in data.get('value', [])]

    def get_list(self, list_id: str) -> TodoList:
        '''get a specific task list.'''
        data = self._get(f'/me/todo/lists/{list_id}')
        return TodoList.from_api(data)

    def create_list(self, display_name: str) -> TodoList:
        '''create a new task list.'''
        data = self._post('/me/todo/lists', {'displayName': display_name})
        return TodoList.from_api(data)

    def update_list(self, list_id: str, display_name: str) -> TodoList:
        '''rename a task list.'''
        data = self._patch(f'/me/todo/lists/{list_id}', {'displayName': display_name})
        return TodoList.from_api(data)

    def delete_list(self, list_id: str) -> None:
        '''delete a task list.'''
        self._delete(f'/me/todo/lists/{list_id}')

    # =========================================================================
    # task operations
    # =========================================================================

    def get_tasks(
        self,
        list_id: str,
        filter_: Optional[str] = None,
        top: Optional[int] = None,
    ) -> List[TodoTask]:
        '''
        get tasks from a list with optional filtering.

        args:
            list_id: the list to get tasks from
            filter_: OData filter string (e.g., "status eq 'completed'")
            top: max number of tasks to return
        '''
        params: Dict[str, Any] = {}
        if filter_:
            params['$filter'] = filter_
        if top:
            params['$top'] = top

        data = self._get(f'/me/todo/lists/{list_id}/tasks', params=params or None)
        return [TodoTask.from_api(item, list_id) for item in data.get('value', [])]

    def get_open_tasks(self, list_id: str) -> List[TodoTask]:
        '''get every open task in a list.'''
        return self._get_all_pages(list_id, "status ne 'completed'")

    def _get_all_pages(self, list_id: str, filter_: Optional[str] = None) -> List[TodoTask]:
        '''get every task in a list matching a filter, following graph's paging.'''
        tasks: List[TodoTask] = []
        path: Optional[str] = f'/me/todo/lists/{list_id}/tasks'
        params: Optional[Dict[str, Any]] = {'$filter': filter_} if filter_ else None

        while path:
            data = self._get(path, params=params)
            tasks.extend(TodoTask.from_api(item, list_id) for item in data.get('value', []))
            next_link = data.get('@odata.nextLink')
            # the next link is a full url that already carries the filter
            path = next_link.removeprefix(self.GRAPH_URL) if next_link else None
            params = None

        return tasks

    def get_task(self, list_id: str, task_id: str) -> TodoTask:
        '''get a specific task.'''
        data = self._get(f'/me/todo/lists/{list_id}/tasks/{task_id}')
        return TodoTask.from_api(data, list_id)

    def create_task(
        self,
        list_id: str,
        title: str,
        body: Optional[str] = None,
        due_date: Optional[date] = None,
        importance: str = 'normal',
        reminder: Optional[DateTimeTimeZone] = None,
        recurrence: Optional[Recurrence] = None,
    ) -> TodoTask:
        '''
        create a new task.

        note: recurrence requires due_date to be set.
        '''
        payload: Dict[str, Any] = {'title': title, 'importance': importance}

        if body:
            payload['body'] = {'content': body, 'contentType': 'text'}

        if due_date:
            payload['dueDateTime'] = DateTimeTimeZone.from_date(due_date).to_api()

        if reminder:
            payload['isReminderOn'] = True
            payload['reminderDateTime'] = reminder.to_api()

        if recurrence:
            if not due_date:
                raise ValueError('due_date required for recurring tasks')
            payload['recurrence'] = recurrence.to_api(include_range=True)

        data = self._post(f'/me/todo/lists/{list_id}/tasks', payload)
        return TodoTask.from_api(data, list_id)

    def update_task(
        self,
        list_id: str,
        task_id: str,
        title: Optional[str] = None,
        body: Optional[str] = None,
        due_date: Optional[date] = None,
        importance: Optional[str] = None,
        status: Optional[str] = None,
        recurrence: Optional[Recurrence] = None,
        remove_recurrence: bool = False,
    ) -> TodoTask:
        '''
        update a task.

        note: uses workaround for recurrence PATCH bug - omits range.startDate
        and includes dueDateTime instead.
        '''
        payload: Dict[str, Any] = {}

        if title is not None:
            payload['title'] = title
        if body is not None:
            payload['body'] = {'content': body, 'contentType': 'text'}
        if importance is not None:
            payload['importance'] = importance
        if status is not None:
            payload['status'] = status

        if due_date is not None:
            payload['dueDateTime'] = DateTimeTimeZone.from_date(due_date).to_api()

        if remove_recurrence:
            payload['recurrence'] = None
        elif recurrence:
            # WORKAROUND: omit range.startDate to avoid PATCH bug
            # API derives startDate from dueDateTime automatically
            if due_date is None:
                # need to include dueDateTime for recurrence updates
                existing = self.get_task(list_id, task_id)
                if existing.due_datetime:
                    payload['dueDateTime'] = existing.due_datetime.to_api()
                else:
                    raise ValueError('due_date required when updating recurrence')

            payload['recurrence'] = recurrence.to_api(include_range=False)

        if not payload:
            raise ValueError('no updates provided')

        data = self._patch(f'/me/todo/lists/{list_id}/tasks/{task_id}', payload)
        return TodoTask.from_api(data, list_id)

    def complete_task(self, list_id: str, task_id: str) -> TodoTask:
        '''mark a task as completed.'''
        return self.update_task(list_id, task_id, status='completed')

    def delete_task(self, list_id: str, task_id: str) -> None:
        '''delete a task.'''
        self._delete(f'/me/todo/lists/{list_id}/tasks/{task_id}')

    # =========================================================================
    # checklist item (step) operations
    # =========================================================================

    def get_checklist_items(self, list_id: str, task_id: str) -> List[ChecklistItem]:
        '''get all checklist items (steps) for a task.'''
        data = self._get(f'/me/todo/lists/{list_id}/tasks/{task_id}/checklistItems')
        return [ChecklistItem.from_api(item) for item in data.get('value', [])]

    def create_checklist_item(
        self,
        list_id: str,
        task_id: str,
        display_name: str
    ) -> ChecklistItem:
        '''create a new checklist item (step).'''
        data = self._post(
            f'/me/todo/lists/{list_id}/tasks/{task_id}/checklistItems',
            {'displayName': display_name}
        )
        return ChecklistItem.from_api(data)

    def update_checklist_item(
        self,
        list_id: str,
        task_id: str,
        item_id: str,
        display_name: Optional[str] = None,
        is_checked: Optional[bool] = None,
    ) -> ChecklistItem:
        '''update a checklist item (step).'''
        payload: Dict[str, Any] = {}
        if display_name is not None:
            payload['displayName'] = display_name
        if is_checked is not None:
            payload['isChecked'] = is_checked

        if not payload:
            raise ValueError('no updates provided')

        data = self._patch(
            f'/me/todo/lists/{list_id}/tasks/{task_id}/checklistItems/{item_id}',
            payload
        )
        return ChecklistItem.from_api(data)

    def complete_checklist_item(
        self,
        list_id: str,
        task_id: str,
        item_id: str
    ) -> ChecklistItem:
        '''mark a checklist item as completed.'''
        return self.update_checklist_item(list_id, task_id, item_id, is_checked=True)

    def delete_checklist_item(
        self,
        list_id: str,
        task_id: str,
        item_id: str
    ) -> None:
        '''delete a checklist item (step).'''
        self._delete(f'/me/todo/lists/{list_id}/tasks/{task_id}/checklistItems/{item_id}')

    # =========================================================================
    # cross-list queries
    # =========================================================================

    def get_tasks_by_completed_range(
        self,
        list_id: str,
        start_date: date,
        end_date: date,
    ) -> List[TodoTask]:
        '''
        get completed tasks within a date range from a list.
        uses server-side filtering for efficiency.
        '''
        filter_str = (
            f"status eq 'completed' and "
            f"completedDateTime/dateTime ge '{start_date.isoformat()}' and "
            f"completedDateTime/dateTime le '{end_date.isoformat()}'"
        )
        return self._get_all_pages(list_id, filter_str)
