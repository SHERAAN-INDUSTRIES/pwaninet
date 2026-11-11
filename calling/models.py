"""Persistent signaling state, independent of presentation and media transport."""
import uuid

from django.conf import settings
from django.db import models


class Call(models.Model):
    class Status(models.TextChoices):
        INVITING = 'inviting'
        ACTIVE = 'active'
        ENDED = 'ended'
        CANCELLED = 'cancelled'
        DECLINED = 'declined'
        MISSED = 'missed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey('messaging.Conversation', on_delete=models.PROTECT)
    caller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.INVITING)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    accepted_at = models.DateTimeField(null=True)
    ended_at = models.DateTimeField(null=True)
    history_message = models.OneToOneField('messaging.Message', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='voice_call_record')
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['conversation'], condition=models.Q(status__in=['inviting', 'active']),
            name='calling_one_open_per_conversation',
        )]

    @property
    def room_name(self):
        return f'call_{self.id.hex}'


class CallParticipant(models.Model):
    class Status(models.TextChoices):
        INVITED = 'invited'
        RINGING = 'ringing'
        ACCEPTED = 'accepted'
        DECLINED = 'declined'
        LEFT = 'left'
        MISSED = 'missed'
        CANCELLED = 'cancelled'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    call = models.ForeignKey(Call, on_delete=models.CASCADE, related_name='participants')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    status = models.CharField(max_length=12, choices=Status.choices)
    # Authenticated session key or hash of the API token. Never supplied by the client.
    answered_by = models.CharField(max_length=64, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['call', 'user'], name='calling_unique_participant')]
