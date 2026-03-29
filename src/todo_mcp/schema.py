'''
dataclasses for microsoft graph api todo resources.

based on live api testing documented in GRAPH_API_REFERENCE.md.
'''

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional


@dataclass
class DateTimeTimeZone:
    '''graph api datetime with timezone.'''
    date_time: str  # ISO 8601 format: YYYY-MM-DDTHH:MM:SS
    time_zone: str = 'UTC'

    @classmethod
    def from_date(cls, d: date, time: str = '17:00:00') -> 'DateTimeTimeZone':
        '''create from a date object with optional time.'''
        return cls(date_time=f'{d.isoformat()}T{time}', time_zone='UTC')

    @classmethod
    def from_datetime(cls, dt: datetime) -> 'DateTimeTimeZone':
        '''create from a datetime object.'''
        return cls(date_time=dt.strftime('%Y-%m-%dT%H:%M:%S'), time_zone='UTC')

    @classmethod
    def from_api(cls, data: Optional[Dict]) -> Optional['DateTimeTimeZone']:
        '''parse from graph api response.'''
        if not data:
            return None
        return cls(
            date_time=data['dateTime'],
            time_zone=data.get('timeZone', 'UTC')
        )

    def to_api(self) -> Dict[str, str]:
        '''serialize for graph api request.'''
        return {'dateTime': self.date_time, 'timeZone': self.time_zone}

    def to_date(self) -> Optional[date]:
        '''extract date portion.'''
        try:
            return datetime.fromisoformat(self.date_time.split('T')[0]).date()
        except (ValueError, IndexError):
            return None


@dataclass
class RecurrencePattern:
    '''
    recurrence pattern configuration.

    pattern types:
        - daily: every N days
        - weekly: every N weeks on specific days
        - absoluteMonthly: every N months on day X
        - relativeMonthly: every N months on Xth weekday
        - absoluteYearly: every N years on month/day
        - relativeYearly: every N years on Xth weekday of month
    '''
    type: str
    interval: int = 1
    days_of_week: Optional[List[str]] = None
    day_of_month: Optional[int] = None
    month: Optional[int] = None
    first_day_of_week: str = 'sunday'
    index: Optional[str] = None  # first, second, third, fourth, last

    @classmethod
    def daily(cls, interval: int = 1) -> 'RecurrencePattern':
        '''every N days.'''
        return cls(type='daily', interval=interval)

    @classmethod
    def weekly(cls, days_of_week: List[str], interval: int = 1) -> 'RecurrencePattern':
        '''every N weeks on specific days.'''
        return cls(type='weekly', interval=interval, days_of_week=days_of_week)

    @classmethod
    def monthly(cls, day_of_month: int, interval: int = 1) -> 'RecurrencePattern':
        '''every N months on day X.'''
        return cls(type='absoluteMonthly', interval=interval, day_of_month=day_of_month)

    @classmethod
    def from_api(cls, data: Optional[Dict]) -> Optional['RecurrencePattern']:
        '''parse from graph api response.'''
        if not data:
            return None
        return cls(
            type=data['type'],
            interval=data.get('interval', 1),
            days_of_week=data.get('daysOfWeek'),
            day_of_month=data.get('dayOfMonth'),
            month=data.get('month'),
            first_day_of_week=data.get('firstDayOfWeek', 'sunday'),
            index=data.get('index')
        )

    def to_api(self) -> Dict[str, Any]:
        '''serialize for graph api request.'''
        result: Dict[str, Any] = {'type': self.type, 'interval': self.interval}
        if self.days_of_week:
            result['daysOfWeek'] = self.days_of_week
            result['firstDayOfWeek'] = self.first_day_of_week
        if self.day_of_month is not None:
            result['dayOfMonth'] = self.day_of_month
        if self.month is not None:
            result['month'] = self.month
        if self.index:
            result['index'] = self.index
        return result


@dataclass
class Recurrence:
    '''
    full recurrence configuration with pattern and range.

    NOTE: due to graph api bugs (verified 2026-03-17):
    - endDate is silently ignored; only noEnd works
    - PATCH with range.startDate fails; omit it and use dueDateTime instead
    '''
    pattern: RecurrencePattern
    start_date: Optional[str] = None  # YYYY-MM-DD, omit on PATCH!

    @classmethod
    def from_api(cls, data: Optional[Dict]) -> Optional['Recurrence']:
        '''parse from graph api response.'''
        if not data:
            return None
        pattern = RecurrencePattern.from_api(data.get('pattern'))
        if not pattern:
            return None
        range_data = data.get('range', {})
        return cls(
            pattern=pattern,
            start_date=range_data.get('startDate')
        )

    def to_api(self, include_range: bool = True) -> Dict[str, Any]:
        '''
        serialize for graph api request.

        args:
            include_range: include range with startDate (False for PATCH requests)
        '''
        result: Dict[str, Any] = {'pattern': self.pattern.to_api()}
        if include_range and self.start_date:
            result['range'] = {
                'type': 'noEnd',  # endDate ignored by API
                'startDate': self.start_date
            }
        return result


@dataclass
class TodoList:
    '''microsoft todo task list.'''
    id: str
    display_name: str
    is_owner: bool = True
    is_shared: bool = False
    wellknown_name: Optional[str] = None  # defaultList, flaggedEmails, etc

    @classmethod
    def from_api(cls, data: Dict) -> 'TodoList':
        '''parse from graph api response.'''
        return cls(
            id=data['id'],
            display_name=data['displayName'],
            is_owner=data.get('isOwner', True),
            is_shared=data.get('isShared', False),
            wellknown_name=data.get('wellknownListName')
        )

    def to_dict(self) -> Dict[str, Any]:
        '''serialize for tool response.'''
        return {
            'list_id': self.id,
            'name': self.display_name,
            'is_owner': self.is_owner,
            'is_shared': self.is_shared,
        }


@dataclass
class TodoTask:
    '''microsoft todo task.'''
    id: str
    title: str
    status: str  # notStarted, inProgress, completed, waitingOnOthers, deferred
    importance: str  # low, normal, high
    created_datetime: datetime
    last_modified_datetime: datetime
    body_content: Optional[str] = None
    due_datetime: Optional[DateTimeTimeZone] = None
    completed_datetime: Optional[DateTimeTimeZone] = None
    reminder_datetime: Optional[DateTimeTimeZone] = None
    is_reminder_on: bool = False
    recurrence: Optional[Recurrence] = None
    list_id: Optional[str] = None  # added for convenience
    list_name: Optional[str] = None  # added for convenience

    @classmethod
    def from_api(cls, data: Dict, list_id: Optional[str] = None) -> 'TodoTask':
        '''parse from graph api response.'''
        return cls(
            id=data['id'],
            title=data['title'],
            status=data['status'],
            importance=data['importance'],
            created_datetime=datetime.fromisoformat(data['createdDateTime'].rstrip('Z')),
            last_modified_datetime=datetime.fromisoformat(data['lastModifiedDateTime'].rstrip('Z')),
            body_content=data.get('body', {}).get('content'),
            due_datetime=DateTimeTimeZone.from_api(data.get('dueDateTime')),
            completed_datetime=DateTimeTimeZone.from_api(data.get('completedDateTime')),
            reminder_datetime=DateTimeTimeZone.from_api(data.get('reminderDateTime')),
            is_reminder_on=data.get('isReminderOn', False),
            recurrence=Recurrence.from_api(data.get('recurrence')),
            list_id=list_id
        )

    def to_dict(self) -> Dict[str, Any]:
        '''serialize for tool response.'''
        result: Dict[str, Any] = {
            'task_id': self.id,
            'title': self.title,
            'status': self.status,
            'importance': self.importance,
            'created_date': self.created_datetime.date().isoformat(),
            'last_modified': self.last_modified_datetime.date().isoformat(),
        }
        if self.body_content:
            result['body_text'] = self.body_content
        if self.due_datetime:
            due_date = self.due_datetime.to_date()
            result['due_date'] = due_date.isoformat() if due_date else None
        if self.completed_datetime:
            completed_date = self.completed_datetime.to_date()
            result['completed_date'] = completed_date.isoformat() if completed_date else None
        if self.recurrence:
            result['recurrence'] = {
                'type': self.recurrence.pattern.type,
                'interval': self.recurrence.pattern.interval,
            }
            if self.recurrence.pattern.days_of_week:
                result['recurrence']['days_of_week'] = self.recurrence.pattern.days_of_week
        if self.list_id:
            result['list_id'] = self.list_id
        if self.list_name:
            result['list_name'] = self.list_name
        return result


@dataclass
class ChecklistItem:
    '''step (checklist item) within a task.'''
    id: str
    display_name: str
    is_checked: bool
    created_datetime: datetime
    checked_datetime: Optional[datetime] = None

    @classmethod
    def from_api(cls, data: Dict) -> 'ChecklistItem':
        '''parse from graph api response.'''
        checked_dt = None
        if data.get('checkedDateTime'):
            checked_dt = datetime.fromisoformat(data['checkedDateTime'].rstrip('Z'))
        return cls(
            id=data['id'],
            display_name=data['displayName'],
            is_checked=data.get('isChecked', False),
            created_datetime=datetime.fromisoformat(data['createdDateTime'].rstrip('Z')),
            checked_datetime=checked_dt
        )

    def to_dict(self) -> Dict[str, Any]:
        '''serialize for tool response.'''
        result: Dict[str, Any] = {
            'item_id': self.id,
            'name': self.display_name,
            'is_checked': self.is_checked,
            'created_date': self.created_datetime.date().isoformat(),
        }
        if self.checked_datetime:
            result['checked_date'] = self.checked_datetime.date().isoformat()
        return result
