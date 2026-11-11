"""Owner-scoped personal organization tools. No model-supplied user IDs."""
from pwanimate.tools.base import BaseDomainTool, ToolResult
from pwanimate.tools.exceptions import ToolPermissionError
from pwanimate.services.personal_planner import (PersonalPlannerService, reminder_payload,
                                                 block_payload)


def auth(user):
    if not user or not user.is_authenticated:
        raise ToolPermissionError('Sign in to use personal study tools.')


def enrolled_units(user):
    """The student's enrolled academic units as {code, name} pairs."""
    try:
        units = []
        for enrollment in user.get_enrolled_units():
            au = getattr(enrollment, 'academic_unit', None)
            if au is not None:
                units.append({'code': getattr(au, 'code', '') or '', 'name': getattr(au, 'name', '') or ''})
        return units
    except Exception:  # noqa: BLE001 - enrollment lookup must never break a read tool
        return []


class PersonalScheduleTool(BaseDomainTool):
    name = 'personal_schedule'
    description = 'Read your saved personal study plans, upcoming self-study blocks, enrolled units, and reminders. Separate from your official timetable.'
    parameters_schema = {'type': 'object', 'properties': {}}
    def execute(self, user, **kwargs):
        auth(user)
        from pwanimate.models import SparkStudyPlan, SparkReminder
        return ToolResult.ok({'plans': list(SparkStudyPlan.objects.filter(user=user).order_by('-created_at').values('id', 'title', 'objective', 'sessions')[:10]),
            'study_blocks': [block_payload(b) for b in PersonalPlannerService.upcoming_study_blocks(user)],
            'enrolled_units': enrolled_units(user),
            'reminders': [reminder_payload(i) for i in SparkReminder.objects.filter(user=user, status='scheduled').order_by('due_at')[:20]]})


class CreateStudyPlanTool(BaseDomainTool):
    name = 'create_study_plan'
    description = 'Save a personal study plan only when the student explicitly asks to create or save one. Does not schedule phone alerts.'
    parameters_schema = {'type': 'object', 'properties': {'title': {'type': 'string'}, 'objective': {'type': 'string'},
        'sessions': {'type': 'array', 'maxItems': 30, 'items': {'type': 'object', 'properties': {
            'topic': {'type': 'string'}, 'minutes': {'type': 'integer', 'minimum': 5, 'maximum': 240}}, 'required': ['topic', 'minutes'], 'additionalProperties': False}}}, 'required': ['title', 'sessions']}
    def execute(self, user, title, sessions, objective='', execution_key=None, **kwargs):
        auth(user)
        item = PersonalPlannerService.create_plan(user, title, objective, sessions, execution_key)
        return ToolResult.ok({'id': str(item.id), 'title': item.title, 'sessions': item.sessions, 'saved': True})


class CreateReminderTool(BaseDomainTool):
    name = 'create_reminder'
    description = 'Save a future personal reminder on explicit request. Ask for missing date/time; never guess. Supports remind_before_minutes (alert lead time) and recurrence none|daily|weekly|monthly. Phone alert requires device scheduling acknowledgment.'
    parameters_schema = {'type': 'object', 'properties': {
        'title': {'type': 'string'},
        'due_at': {'type': 'string', 'description': 'ISO 8601 datetime with explicit UTC offset'},
        'due_datetime': {'type': 'string', 'description': 'Alias for due_at'},
        'timezone': {'type': 'string'},
        'description': {'type': 'string'},
        'remind_before_minutes': {'type': 'integer', 'minimum': 0, 'maximum': 10080},
        'recurrence': {'type': 'string', 'enum': ['none', 'daily', 'weekly', 'monthly']}}, 'required': ['title']}
    def execute(self, user, title, due_at='', due_datetime='', timezone='', description='',
                remind_before_minutes=0, recurrence='none', execution_key='', **kwargs):
        auth(user)
        moment = str(due_at or due_datetime or '').strip()
        if not moment:
            raise ValueError('Provide the reminder date and time (due_at), e.g. 2026-11-15T19:00:00+03:00.')
        from django.conf import settings
        item = PersonalPlannerService.create_reminder(user, title, moment,
            timezone or getattr(settings, 'TIME_ZONE', 'Africa/Nairobi'), execution_key,
            remind_before_minutes=remind_before_minutes, recurrence=recurrence)
        return ToolResult.ok(reminder_payload(item))


class AddStudyBlockTool(BaseDomainTool):
    name = 'add_study_block'
    description = 'Schedule a personal self-study block for one of the student\'s units, only on explicit request. Complements the official timetable; does not create phone alerts.'
    parameters_schema = {'type': 'object', 'properties': {
        'title': {'type': 'string'},
        'scheduled_at': {'type': 'string', 'description': 'ISO 8601 start time with explicit UTC offset'},
        'due_datetime': {'type': 'string', 'description': 'Alias for scheduled_at'},
        'timezone': {'type': 'string'},
        'unit': {'type': 'string', 'description': 'Unit code (e.g. CSC201) or exact unit name'},
        'unit_label': {'type': 'string', 'description': 'Custom unit label when no enrolled unit applies'},
        'minutes': {'type': 'integer', 'minimum': 5, 'maximum': 240},
        'recurrence': {'type': 'string', 'enum': ['none', 'daily', 'weekly']}}, 'required': ['title']}
    def execute(self, user, title, scheduled_at='', due_datetime='', timezone='', unit='', unit_label='',
                minutes=25, recurrence='none', execution_key=None, **kwargs):
        auth(user)
        moment = str(scheduled_at or due_datetime or '').strip()
        if not moment:
            raise ValueError('Provide the block start time (scheduled_at), e.g. 2026-11-12T17:00:00+03:00.')
        from django.conf import settings
        item = PersonalPlannerService.create_study_block(user, title, moment,
            timezone or getattr(settings, 'TIME_ZONE', 'Africa/Nairobi'),
            unit=unit or None, unit_label=unit_label, minutes=minutes, recurrence=recurrence,
            source='spark', key=execution_key)
        payload = block_payload(item)
        try:
            PersonalPlannerService.sync_block_reminder(user, item)
            payload['reminder_scheduled'] = True
        except Exception:  # noqa: BLE001 - the block stands even if its reminder fails
            payload['reminder_scheduled'] = False
        return ToolResult.ok(payload)


class UpdateStudyBlockTool(BaseDomainTool):
    name = 'update_study_block'
    description = 'Change the time, duration, unit, or recurrence of a scheduled personal study block.'
    parameters_schema = {'type': 'object', 'properties': {
        'block_id': {'type': 'string'},
        'title': {'type': 'string'},
        'scheduled_at': {'type': 'string'},
        'due_datetime': {'type': 'string', 'description': 'Alias for scheduled_at'},
        'timezone': {'type': 'string'},
        'unit': {'type': 'string'},
        'unit_label': {'type': 'string'},
        'minutes': {'type': 'integer', 'minimum': 5, 'maximum': 240},
        'recurrence': {'type': 'string', 'enum': ['none', 'daily', 'weekly']}}, 'required': ['block_id']}
    def execute(self, user, block_id, title=None, scheduled_at=None, due_datetime='', timezone='',
                unit=None, unit_label=None, minutes=None, recurrence=None, **kwargs):
        auth(user)
        moment = scheduled_at or due_datetime or None
        item = PersonalPlannerService.update_study_block(user, block_id, title=title,
            scheduled_at=moment, timezone_name=timezone or None, unit=unit, unit_label=unit_label,
            minutes=minutes, recurrence=recurrence)
        return ToolResult.ok(block_payload(item))


class RemoveStudyBlockTool(BaseDomainTool):
    name = 'remove_study_block'
    description = 'Cancel a scheduled personal study block when the student asks to remove or delete it.'
    parameters_schema = {'type': 'object', 'properties': {'block_id': {'type': 'string'}}, 'required': ['block_id']}
    def execute(self, user, block_id, **kwargs):
        auth(user)
        item = PersonalPlannerService.cancel_study_block(user, block_id)
        return ToolResult.ok(block_payload(item))


class CompleteStudyBlockTool(BaseDomainTool):
    name = 'complete_study_block'
    description = 'Mark a scheduled personal study block as done; recurring blocks roll forward to their next occurrence.'
    parameters_schema = {'type': 'object', 'properties': {'block_id': {'type': 'string'}}, 'required': ['block_id']}
    def execute(self, user, block_id, **kwargs):
        auth(user)
        item = PersonalPlannerService.complete_study_block(user, block_id)
        return ToolResult.ok(block_payload(item))


class UnreadChatsTool(BaseDomainTool):
    name = 'unread_chats'
    description = 'Read current unread message and conversation counts for the signed-in student.'
    parameters_schema = {'type': 'object', 'properties': {}}
    def execute(self, user, **kwargs):
        auth(user)
        from django.db.models import Count, F, Q
        from messaging.models import ConversationMember
        unread = ConversationMember.objects.filter(user=user).annotate(unread_count=Count('conversation__messages',
            filter=Q(conversation__messages__is_deleted=False) & ~Q(conversation__messages__sender=user) &
            (Q(last_read_message__isnull=True) | Q(conversation__messages__id__gt=F('last_read_message_id')))))
        counts = list(unread.values_list('unread_count', flat=True))
        return ToolResult.ok({'unread_messages': sum(counts), 'unread_conversations': sum(c > 0 for c in counts)})
