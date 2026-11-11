"""Authoritative call transitions. HTTP and future native adapters share these rules."""
import logging
from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied

from messaging.models import Conversation, Message
from users.models import Block
from .models import Call, CallParticipant
from .identity import display_name

logger = logging.getLogger(__name__)
OPEN = [Call.Status.INVITING, Call.Status.ACTIVE]


class Conflict(APIException):
    status_code = 409
    default_detail = 'This call action is no longer available.'


def snapshot(call):
    return {
        'id': str(call.pk), 'conversation_id': call.conversation_id,
        'caller_id': call.caller_id, 'status': call.status, 'revision': call.revision,
        'created_at': call.created_at.isoformat(), 'expires_at': call.expires_at.isoformat(),
        'accepted_at': call.accepted_at.isoformat() if call.accepted_at else None,
        'ended_at': call.ended_at.isoformat() if call.ended_at else None,
        'participants': [
            {'id': str(p.pk), 'user_id': p.user_id, 'status': p.status,
             'display_name': display_name(p.user),
             'avatar_url': p.user.profile_pic.url if p.user.profile_pic else ''}
            for p in call.participants.select_related('user').order_by('user_id')
        ],
    }


def publish(call):
    """Best-effort live hint; clients recover authoritative state over HTTP.

    This is deliberately not a background/closed-app push delivery mechanism.
    """
    payload = snapshot(call)
    user_ids = list(call.participants.values_list('user_id', flat=True))

    def send():
        try:
            layer = get_channel_layer()
            for user_id in user_ids:
                async_to_sync(layer.group_send)(f'notifications_{user_id}', {
                    'type': 'call_update', 'call': payload,
                })
        except Exception:
            logger.exception('Call event delivery failed for call %s', call.pk)
    transaction.on_commit(send)


def authorize(call, user):
    participant = call.participants.filter(user=user).first()
    if participant is None:
        raise NotFound()
    if not call.conversation.members.filter(user=user).exists():
        raise PermissionDenied('Conversation membership is required.')
    ids = list(call.participants.values_list('user_id', flat=True))
    if Block.objects.filter(
        Q(blocker_id=call.caller_id, blocked_id__in=ids) |
        Q(blocked_id=call.caller_id, blocker_id__in=ids)
    ).exists():
        raise PermissionDenied('This call is unavailable.')
    return participant


def finish(call, status):
    if call.status not in OPEN:
        return
    call.status = status
    call.ended_at = timezone.now()
    call.revision += 1
    call.save(update_fields=['status', 'ended_at', 'revision'])
    call.participants.filter(status__in=['invited', 'ringing']).update(
        status='missed' if status == 'missed' else 'cancelled')
    call.participants.filter(status='accepted').update(status='left')
    # The caller owns the bubble, independently of who hangs up.
    message = Message.objects.create(
        conversation=call.conversation, sender=call.caller,
        message_type='voice_call', content={
            'ended': 'Voice call', 'missed': 'Missed voice call',
            'declined': 'Declined voice call', 'cancelled': 'Cancelled voice call',
        }.get(status, 'Voice call'),
    )
    call.history_message = message
    call.save(update_fields=['history_message'])
    call.conversation.save()
    def deliver_history():
        try:
            from messaging.serializers import MessageSerializer
            from messaging.services.chat_notification_service import ChatNotificationService
            async_to_sync(get_channel_layer().group_send)(f'chat_{call.conversation_id}', {
                'type': 'chat_message', 'message': MessageSerializer(message).data,
            })
            ChatNotificationService.notify_new_message(message)
        except Exception:
            logger.exception('Call history delivery failed for call %s', call.pk)
    transaction.on_commit(deliver_history)
    publish(call)


def expire(call):
    if call.status == Call.Status.INVITING and call.expires_at <= timezone.now():
        finish(call, Call.Status.MISSED)


@transaction.atomic
def start(conversation_id, user, device):
    conversation = Conversation.objects.select_for_update().filter(pk=conversation_id).first()
    if not conversation or not conversation.members.filter(user=user).exists():
        raise NotFound()
    # Participant model supports groups; invitation policy is intentionally direct-only for now.
    if conversation.type != Conversation.DIRECT or conversation.group_id is not None:
        raise Conflict('Group calling is not enabled yet.')
    ids = sorted(conversation.members.values_list('user_id', flat=True))
    if len(ids) != 2:
        raise Conflict('A direct call requires exactly two conversation members.')
    # Stable user locking serializes starts across different conversations on PostgreSQL.
    list(get_user_model().objects.select_for_update().filter(pk__in=ids).order_by('pk'))
    stale_ids = CallParticipant.objects.filter(user_id__in=ids).values('call_id')
    for stale in Call.objects.select_for_update().filter(
        pk__in=stale_ids, status='inviting', expires_at__lte=timezone.now(),
    ).order_by('pk'):
        expire(stale)
    if CallParticipant.objects.filter(user_id__in=ids, call__status__in=OPEN).exists():
        raise Conflict('A participant is already in a call.')
    if Block.objects.filter(blocker_id__in=ids, blocked_id__in=ids).exists():
        raise PermissionDenied('This call is unavailable.')
    call = Call.objects.create(
        conversation=conversation, caller=user,
        expires_at=timezone.now() + timedelta(seconds=settings.CALLING_RING_SECONDS),
    )
    CallParticipant.objects.bulk_create([
        CallParticipant(call=call, user_id=uid,
                        status='accepted' if uid == user.pk else 'invited',
                        answered_by=device if uid == user.pk else '') for uid in ids
    ])
    publish(call)
    return call


def owned_call(call_id, user):
    call = Call.objects.select_for_update().filter(pk=call_id).first()
    if not call:
        raise NotFound()
    participant = authorize(call, user)
    expire(call)
    participant.refresh_from_db()
    return call, participant


@transaction.atomic
def transition(call_id, user, device, action):
    call, participant = owned_call(call_id, user)
    # Return terminal state to dismiss stale notifications without resurrecting a call.
    if call.status not in OPEN:
        return call
    if action == 'ringing':
        if user.pk == call.caller_id or participant.status not in ['invited', 'ringing']:
            raise Conflict()
        if participant.status == 'ringing':
            return call
        participant.status = 'ringing'
    elif action == 'accept':
        if user.pk == call.caller_id:
            raise Conflict('Only the invited participant can accept.')
        if participant.status == 'accepted':
            if participant.answered_by != device:
                raise Conflict({'detail': 'This call was answered on another device.', 'code': 'answered_elsewhere'})
            return call
        if user.pk == call.caller_id or participant.status not in ['invited', 'ringing']:
            raise Conflict()
        participant.status = 'accepted'
        participant.answered_by = device
        call.status = Call.Status.ACTIVE
        call.accepted_at = timezone.now()
    elif action == 'decline':
        if user.pk == call.caller_id or participant.status not in ['invited', 'ringing']:
            raise Conflict()
        participant.status = 'declined'
        participant.save(update_fields=['status'])
        finish(call, Call.Status.DECLINED)
        return call
    elif action == 'cancel':
        if user.pk != call.caller_id or call.status != Call.Status.INVITING:
            raise Conflict()
        finish(call, Call.Status.CANCELLED)
        return call
    elif action == 'leave':
        if participant.status != 'accepted' or participant.answered_by != device:
            raise Conflict()
        finish(call, Call.Status.ENDED if call.status == 'active' else Call.Status.CANCELLED)
        return call
    else:
        raise Conflict()
    participant.save(update_fields=['status', 'answered_by'])
    call.revision += 1
    call.save(update_fields=['status', 'accepted_at', 'revision'])
    publish(call)
    return call
