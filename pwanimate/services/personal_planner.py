"""Personal schedules are authoritative SQL records, independent of cohort timetables."""
import secrets
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from pwanimate.models import SparkReminder, SparkStudyBlock, SparkStudyPlan


def aware_time(value, timezone_name):
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, TypeError):
        raise ValueError('Use a valid IANA timezone, for example Africa/Nairobi.')
    dt = parse_datetime(str(value))
    if not dt or timezone.is_naive(dt):
        raise ValueError('Provide an ISO timestamp with an explicit UTC offset; ask the student when the time is unclear.')
    if dt.utcoffset() != timedelta(0) and dt.utcoffset() != dt.astimezone(zone).utcoffset():
        raise ValueError('Timestamp offset does not match the selected timezone.')
    return dt


RECURRENCES = ('none', 'daily', 'weekly', 'monthly')
MAX_REMIND_BEFORE_MINUTES = 10080  # one week


def next_recurrence(dt, recurrence):
    """Advance a due time by one recurrence step, preserving wall-clock fields."""
    from dateutil.relativedelta import relativedelta
    if recurrence == 'daily':
        return dt + timedelta(days=1)
    if recurrence == 'weekly':
        return dt + timedelta(weeks=1)
    if recurrence == 'monthly':
        return dt + relativedelta(months=1)
    return None


class PersonalPlannerService:
    @staticmethod
    def create_reminder(user, title, due_at, timezone_name, key, remind_before_minutes=0, recurrence='none'):
        title = str(title).strip()
        if not title or len(title) > 180 or not key or len(key) > 100:
            raise ValueError('A title and an idempotency key are required.')
        try:
            remind_before_minutes = int(remind_before_minutes or 0)
        except (TypeError, ValueError):
            raise ValueError('Reminder lead time must be a whole number of minutes.')
        if not 0 <= remind_before_minutes <= MAX_REMIND_BEFORE_MINUTES:
            raise ValueError(f'Reminder lead time must be 0–{MAX_REMIND_BEFORE_MINUTES} minutes.')
        recurrence = str(recurrence or 'none').strip().lower()
        if recurrence not in RECURRENCES:
            raise ValueError('Recurrence must be none, daily, weekly, or monthly.')
        existing = SparkReminder.objects.filter(user=user, idempotency_key=key).first()
        if existing:
            return existing
        dt = aware_time(due_at, timezone_name)
        if dt <= timezone.now() or dt > timezone.now() + timedelta(days=366):
            raise ValueError('Reminder must be in the future, within one year.')
        # Global unique positive signed 32-bit IDs meet native notification requirements.
        while True:
            native_id = secrets.randbelow(2147483646) + 1
            if not SparkReminder.objects.filter(notification_id=native_id).exists():
                break
        return SparkReminder.objects.get_or_create(user=user, idempotency_key=key, defaults={
            'title': title, 'due_at': dt, 'timezone_name': timezone_name, 'notification_id': native_id,
            'remind_before_minutes': remind_before_minutes, 'recurrence': recurrence,
            'alert_at': (dt - timedelta(minutes=remind_before_minutes)) if remind_before_minutes else None})[0]

    @staticmethod
    def create_plan(user, title, objective, sessions, key=None):
        if not title.strip() or len(title) > 180 or not isinstance(sessions, list) or not 1 <= len(sessions) <= 30:
            raise ValueError('A plan needs a title and between 1 and 30 sessions.')
        clean = []
        for session in sessions:
            if not isinstance(session, dict) or not session.get('topic'):
                raise ValueError('Each session needs a topic.')
            minutes = session.get('minutes', 25)
            if type(minutes) is not int or not 5 <= minutes <= 240:
                raise ValueError('Session duration must be 5–240 minutes.')
            clean.append({'topic': str(session['topic'])[:200], 'minutes': minutes})
        if key:
            return SparkStudyPlan.objects.get_or_create(user=user, idempotency_key=key, defaults={'title': title, 'objective': str(objective)[:2000], 'sessions': clean})[0]
        return SparkStudyPlan.objects.create(user=user, title=title, objective=str(objective)[:2000], sessions=clean)

    # ---- personal study blocks (self-study alongside the official timetable) ----

    BLOCK_RECURRENCES = ('none', 'daily', 'weekly')

    @staticmethod
    def resolve_unit(unit=None, unit_label=''):
        """Resolve a unit reference (AcademicUnit, code string, or free text) to (unit, label)."""
        from documents.academic.models import AcademicUnit
        label = str(unit_label or '').strip()[:120]
        if unit is None and not label:
            return None, ''
        if hasattr(unit, 'pk'):
            return unit, label or getattr(unit, 'code', '') or getattr(unit, 'name', '')
        raw = str(unit or label).strip()
        match = AcademicUnit.objects.filter(code__iexact=raw).first()
        if match is None:
            match = AcademicUnit.objects.filter(name__iexact=raw).first()
        if match is not None:
            return match, label or match.code
        return None, (label or raw)[:120]

    @staticmethod
    def unit_scope_for(unit=None, unit_label=''):
        """Stable dedup key for the one-active-session-per-unit constraint."""
        if unit is not None:
            return str(getattr(unit, 'code', '') or getattr(unit, 'pk', '') or 'unit')[:140]
        return (str(unit_label or '').strip().lower() or 'general')[:140]

    @staticmethod
    def _clean_minutes(minutes):
        try:
            minutes = int(minutes)
        except (TypeError, ValueError):
            raise ValueError('Study block duration must be a whole number of minutes (5–240).')
        if not 5 <= minutes <= 240:
            raise ValueError('Study block duration must be 5–240 minutes.')
        return minutes

    @staticmethod
    def create_study_block(user, title, scheduled_at, timezone_name, unit=None, unit_label='',
                           minutes=25, recurrence='none', source='user', key=None):
        title = str(title or '').strip()
        if not title or len(title) > 180:
            raise ValueError('A study block needs a title (max 180 characters).')
        minutes = PersonalPlannerService._clean_minutes(minutes)
        recurrence = str(recurrence or 'none').strip().lower()
        if recurrence not in PersonalPlannerService.BLOCK_RECURRENCES:
            raise ValueError('Study block recurrence must be none, daily, or weekly.')
        unit_obj, label = PersonalPlannerService.resolve_unit(unit, unit_label)
        dt = aware_time(scheduled_at, timezone_name)
        if dt <= timezone.now() or dt > timezone.now() + timedelta(days=366):
            raise ValueError('Study block must start in the future, within one year.')
        if key:
            existing = SparkStudyBlock.objects.filter(user=user, idempotency_key=key).first()
            if existing:
                return existing
        return SparkStudyBlock.objects.create(
            user=user, unit=unit_obj, unit_label=label, title=title, scheduled_at=dt,
            timezone_name=timezone_name, minutes=minutes, recurrence=recurrence,
            source='spark' if source == 'spark' else 'user', idempotency_key=key)

    @staticmethod
    def _get_block(user, block_id):
        from django.shortcuts import get_object_or_404
        return get_object_or_404(SparkStudyBlock, pk=block_id, user=user)

    @staticmethod
    def update_study_block(user, block_id, title=None, scheduled_at=None, timezone_name=None,
                           unit=None, unit_label=None, minutes=None, recurrence=None):
        item = PersonalPlannerService._get_block(user, block_id)
        if item.status != SparkStudyBlock.STATUS_SCHEDULED:
            raise ValueError('Only scheduled study blocks can be updated.')
        if title is not None:
            title = str(title).strip()
            if not title or len(title) > 180:
                raise ValueError('A study block needs a title (max 180 characters).')
            item.title = title
        if minutes is not None:
            item.minutes = PersonalPlannerService._clean_minutes(minutes)
        if recurrence is not None:
            recurrence = str(recurrence).strip().lower()
            if recurrence not in PersonalPlannerService.BLOCK_RECURRENCES:
                raise ValueError('Study block recurrence must be none, daily, or weekly.')
            item.recurrence = recurrence
        if unit is not None or unit_label is not None:
            unit_obj, label = PersonalPlannerService.resolve_unit(unit if unit is not None else item.unit,
                unit_label if unit_label is not None else item.unit_label)
            item.unit, item.unit_label = unit_obj, label
        if scheduled_at is not None:
            tz_name = timezone_name or item.timezone_name
            item.scheduled_at = aware_time(scheduled_at, tz_name)
            item.timezone_name = tz_name
            if item.scheduled_at <= timezone.now():
                raise ValueError('Study block must start in the future.')
        item.save()
        return item

    @staticmethod
    def sync_block_reminder(user, block):
        """Ensure a reminder fires at a study block's start so the inbox/push
        pipeline notifies the student when the session is due."""
        key = f'studyblock:{block.id}'
        existing = SparkReminder.objects.filter(user=user, idempotency_key=key).first()
        if existing:
            return existing
        return PersonalPlannerService.create_reminder(
            user, f"Study block: {block.title}", block.scheduled_at.isoformat(),
            block.timezone_name, key=key,
            recurrence=block.recurrence if block.recurrence in RECURRENCES else 'none')

    @staticmethod
    def cancel_study_block(user, block_id):
        item = PersonalPlannerService._get_block(user, block_id)
        if item.status == SparkStudyBlock.STATUS_SCHEDULED:
            item.status = SparkStudyBlock.STATUS_CANCELLED
            item.save(update_fields=['status'])
            SparkReminder.objects.filter(user=user, idempotency_key=f'studyblock:{item.id}',
                                         status='scheduled').update(status='cancelled')
        return item

    @staticmethod
    def complete_study_block(user, block_id):
        """Mark a block done; recurring blocks materialize their next occurrence."""
        item = PersonalPlannerService._get_block(user, block_id)
        if item.status != SparkStudyBlock.STATUS_SCHEDULED:
            raise ValueError('Only scheduled study blocks can be completed.')
        item.status = SparkStudyBlock.STATUS_DONE
        item.save(update_fields=['status'])
        if item.recurrence != 'none':
            nxt = item.scheduled_at
            for _ in range(400):
                nxt = next_recurrence(nxt, item.recurrence)
                if nxt is None:
                    break
                if nxt > timezone.now():
                    break
            else:
                nxt = None
            if nxt:
                key = f'{item.idempotency_key or item.id}:next:{nxt.date().isoformat()}'[:100]
                block, _ = SparkStudyBlock.objects.get_or_create(user=user, idempotency_key=key, defaults={
                    'unit': item.unit, 'unit_label': item.unit_label, 'title': item.title,
                    'scheduled_at': nxt, 'timezone_name': item.timezone_name, 'minutes': item.minutes,
                    'recurrence': item.recurrence, 'source': item.source})
                PersonalPlannerService.sync_block_reminder(user, block)
        return item

    @staticmethod
    def upcoming_study_blocks(user, days=14):
        horizon = timezone.now() + timedelta(days=max(1, min(int(days), 90)))
        return list(SparkStudyBlock.objects.filter(
            user=user, status=SparkStudyBlock.STATUS_SCHEDULED,
            scheduled_at__gt=timezone.now() - timedelta(hours=6), scheduled_at__lte=horizon,
        ).order_by('scheduled_at')[:50])


def block_payload(item):
    unit = getattr(item, 'unit', None)
    return {'id': str(item.id), 'title': item.title, 'unit': getattr(unit, 'code', None),
            'unit_label': item.unit_label or (getattr(unit, 'code', '') or ''),
            'unit_name': getattr(unit, 'name', ''), 'scheduled_at': item.scheduled_at.isoformat(),
            'timezone': item.timezone_name, 'minutes': item.minutes, 'recurrence': item.recurrence,
            'status': item.status, 'source': item.source}


def reminder_payload(item):
    return {'id': str(item.id), 'title': item.title, 'due_at': item.due_at.isoformat(),
            'timezone': item.timezone_name, 'status': item.status, 'notification_id': item.notification_id,
            'remind_before_minutes': item.remind_before_minutes, 'recurrence': item.recurrence,
            'alert_at': item.alert_at.isoformat() if item.alert_at else None,
            'phone_scheduled': item.devices.exists()}
