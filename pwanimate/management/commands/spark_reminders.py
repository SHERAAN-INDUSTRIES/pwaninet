"""Restart-safe due reminder sweep. Run under a process supervisor in production."""
import secrets
import time
import logging
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.db import transaction, close_old_connections
from django.utils import timezone
from django.db.models import Q
from pwanimate.models import SparkReminder
from pwanimate.services.personal_planner import next_recurrence
from notifications.models import NotificationObject
logger = logging.getLogger(__name__)


def _spawn_next(item):
    """Create the following occurrence of a recurring reminder, if any."""
    if item.recurrence == 'none':
        return
    nxt = item.due_at
    # Skip occurrences already in the past after downtime; cap the catch-up.
    for _ in range(400):
        nxt = next_recurrence(nxt, item.recurrence)
        if nxt is None:
            return
        if nxt > timezone.now():
            break
    else:
        return
    while True:
        native_id = secrets.randbelow(2147483646) + 1
        if not SparkReminder.objects.filter(notification_id=native_id).exists():
            break
    SparkReminder.objects.get_or_create(user_id=item.user_id,
        idempotency_key=f'{item.idempotency_key[:80]}:next:{nxt.date().isoformat()}'[:100], defaults={
            'title': item.title, 'due_at': nxt, 'timezone_name': item.timezone_name,
            'notification_id': native_id, 'remind_before_minutes': item.remind_before_minutes,
            'recurrence': item.recurrence,
            'alert_at': (nxt - timedelta(minutes=item.remind_before_minutes)) if item.remind_before_minutes else None})


def deliver_due():
    count = 0
    now = timezone.now()
    # alert_at is the fire time (due_at minus lead time); legacy rows have none.
    due_q = Q(alert_at__isnull=False, alert_at__lte=now) | Q(alert_at__isnull=True, due_at__lte=now)
    # Persist notification and fired state in one transaction. Notification UUID
    # is the reminder UUID, so crash/retry cannot create a second inbox item.
    with transaction.atomic():
        due = SparkReminder.objects.select_for_update(skip_locked=True).filter(due_q, status='scheduled').order_by('due_at')[:100]
        for item in due:
            NotificationObject.objects.get_or_create(notification_id=item.id, defaults={
                'recipient_id': item.user_id, 'notification_type': 'AI', 'category': 'AI', 'priority': 'NORMAL',
                'title': 'Spark study reminder', 'summary': item.title, 'context_type': 'PWANIMATE',
                'context_id': str(item.id), 'status': 'DELIVERED', 'delivery_policy': 'IMMEDIATE',
                'metadata': {'source': 'spark_reminder', 'action_url': '/pwanimate/'}})
            item.status = 'fired'
            item.delivered_at = timezone.now()  # Inbox delivery, not proof of phone receipt.
            item.save(update_fields=['status', 'delivered_at'])
            _spawn_next(item)
            from notifications.services.notification_service import invalidate_unread_count_cache
            transaction.on_commit(lambda uid=item.user_id: invalidate_unread_count_cache(uid))
            count += 1
    # Durable retry marker. A provider timeout may still duplicate a push; never
    # describe push transport as exactly-once delivery.
    for pk in SparkReminder.objects.filter(status='fired', push_status='pending', push_attempts__lt=5).values_list('pk', flat=True)[:100]:
        with transaction.atomic():
            item = SparkReminder.objects.select_for_update(skip_locked=True).filter(pk=pk, push_status='pending').first()
            if not item:
                continue
            if item.devices.exists():
                item.push_status = 'native_scheduled'
            else:
                from notifications.models import PushSubscription
                from notifications.delivery.adapters import PushAdapter
                if not PushSubscription.objects.filter(user_id=item.user_id, is_active=True).exists():
                    item.push_status = 'no_subscription'
                else:
                    item.push_attempts += 1
                    if PushAdapter().deliver(NotificationObject.objects.get(notification_id=item.id)):
                        item.push_status = 'sent'
                    elif item.push_attempts >= 5:
                        item.push_status = 'failed'
            item.save(update_fields=['push_status', 'push_attempts'])
    return count


class Command(BaseCommand):
    help = 'Deliver due Spark reminders to the inbox with native/push fallback; --watch polls every 15 seconds.'
    def add_arguments(self, parser):
        parser.add_argument('--watch', action='store_true')
    def handle(self, *args, **options):
        while True:
            try:
                count = deliver_due()
                if count:
                    self.stdout.write(f'Delivered {count} reminder(s) to inbox.')
            except Exception:
                logger.exception('Reminder sweep failed; scheduled rows remain retryable')
                if not options['watch']:
                    raise
            if not options['watch']:
                return
            close_old_connections()
            time.sleep(15)
