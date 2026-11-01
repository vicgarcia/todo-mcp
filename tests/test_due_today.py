'''
tests for get_tasks_due_today and the paging behind it, with graph stubbed out.
'''

from datetime import date, timedelta

import pytest

from todo_mcp import graph_client, server
from todo_mcp.graph_client import GraphAPIError, GraphClient


def api_task(title, due=None, id_=None):
    task = {
        'id': id_ or title,
        'title': title,
        'status': 'notStarted',
        'importance': 'normal',
        'createdDateTime': '2026-01-01T00:00:00Z',
        'lastModifiedDateTime': '2026-01-01T00:00:00Z',
    }
    if due:
        task['dueDateTime'] = {'dateTime': f'{due.isoformat()}T04:00:00.0000000', 'timeZone': 'UTC'}
    return task


class StubGraph(GraphClient):
    ''' a graph client answering from canned pages instead of the network '''

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def get_lists(self, top=None):
        class Lst:
            def __init__(self, id_, name):
                self.id, self.display_name = id_, name
        return [Lst('a', 'Chores'), Lst('b', 'Health'), Lst('c', 'Other')]

    def _get(self, path, params=None):
        self.calls.append((path, params))
        return self.pages[path]


@pytest.fixture
def graph(monkeypatch):
    today = date.today()
    pages = {
        '/me/todo/lists/a/tasks': {
            'value': [api_task('today a', today), api_task('tomorrow', today + timedelta(days=1)),
                      api_task('overdue', today - timedelta(days=1)), api_task('no date')],
            '@odata.nextLink': f'{GraphClient.GRAPH_URL}/me/todo/lists/a/tasks?$skip=4',
        },
        '/me/todo/lists/a/tasks?$skip=4': {'value': [api_task('today page two', today)]},
        '/me/todo/lists/b/tasks': {'value': [api_task('today b', today)]},
        '/me/todo/lists/c/tasks': {'value': [api_task('today other list', today)]},
    }
    stub = StubGraph(pages)
    monkeypatch.setattr(server, '_client', stub)
    return stub


def test_only_tasks_due_today_in_the_given_lists(graph):
    result = server.get_tasks_due_today(['a', 'b'])
    assert result['success'] is True
    assert result['date'] == date.today().isoformat()
    assert result['count'] == 3
    assert [t['title'] for t in result['tasks']] == ['today a', 'today page two', 'today b']
    assert [t['list_name'] for t in result['tasks']] == ['Chores', 'Chores', 'Health']


def test_paging_filters_for_open_tasks_and_follows_next_links(graph):
    assert [t.title for t in graph.get_open_tasks('a')][-1] == 'today page two'
    assert graph.calls[0] == ('/me/todo/lists/a/tasks', {'$filter': "status ne 'completed'"})
    assert graph.calls[1] == ('/me/todo/lists/a/tasks?$skip=4', None)


def test_today_is_the_local_day_not_utc(graph, monkeypatch):
    # stand in for an evening when utc has already moved on to tomorrow
    tomorrow = date.today() + timedelta(days=1)

    class Tomorrow(date):
        @classmethod
        def today(cls):
            return tomorrow

    monkeypatch.setattr(server, 'date', Tomorrow)
    result = server.get_tasks_due_today(['a'])
    assert result['date'] == tomorrow.isoformat()
    assert [t['title'] for t in result['tasks']] == ['tomorrow']


def test_empty_list_ids_is_an_error(graph):
    assert server.get_tasks_due_today([])['success'] is False


def test_due_range_covers_every_list_and_page(graph):
    today = date.today()
    result = server.get_tasks_by_due_date_range(start_date=today.isoformat(), include_overdue=True, include_no_due_date=True)
    assert [t['title'] for t in result['tasks']] == ['today a', 'today page two', 'today b', 'today other list']
    assert [t['title'] for t in result['overdue']] == ['overdue']
    assert [t['title'] for t in result['no_due_date']] == ['no date']
    assert graph.calls[0][1] == {'$filter': "status ne 'completed'"}


def test_due_range_defaults_to_the_local_day(graph, monkeypatch):
    tomorrow = date.today() + timedelta(days=1)

    class Tomorrow(date):
        @classmethod
        def today(cls):
            return tomorrow

    monkeypatch.setattr(server, 'date', Tomorrow)
    result = server.get_tasks_by_due_date_range()
    assert result['start_date'] == tomorrow.isoformat()
    assert [t['title'] for t in result['tasks']] == ['tomorrow']


def test_due_range_reports_a_list_it_cannot_read(graph):
    del graph.pages['/me/todo/lists/b/tasks']

    def failing_get(path, params=None):
        if path not in graph.pages:
            raise GraphAPIError(429, 'TooManyRequests', 'slow down')
        return graph.pages[path]

    graph._get = failing_get
    result = server.get_tasks_by_due_date_range()
    assert result['success'] is False
    assert '429' in result['error'] or 'slow down' in result['error']


class FakeResponse:
    def __init__(self, status_code, body=None, headers=None):
        self.status_code = status_code
        self._body = body or {}
        self.headers = headers or {}
        self.text = ''

    def json(self):
        return self._body


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.count = 0

    def request(self, *args, **kwargs):
        self.count += 1
        return self.responses.pop(0)


def raw_client(responses, monkeypatch):
    delays = []
    monkeypatch.setattr(graph_client.time, 'sleep', delays.append)
    client = GraphClient.__new__(GraphClient)
    client._http = FakeHttp(responses)
    client._headers = lambda: {}
    return client, delays


def test_rate_limited_requests_are_retried_after_the_given_delay(monkeypatch):
    client, delays = raw_client([FakeResponse(429, headers={'Retry-After': '2'}), FakeResponse(200, {'value': []})], monkeypatch)
    assert client._get('/x') == {'value': []}
    assert delays == [2.0]


def test_retries_give_up_and_raise(monkeypatch):
    client, delays = raw_client([FakeResponse(429, headers={'Retry-After': '999'}) for _ in range(GraphClient.MAX_RETRIES + 1)], monkeypatch)
    with pytest.raises(GraphAPIError):
        client._get('/x')
    assert delays == [GraphClient.MAX_RETRY_DELAY] * GraphClient.MAX_RETRIES
