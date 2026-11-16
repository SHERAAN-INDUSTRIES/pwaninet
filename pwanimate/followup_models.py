"""Contextual follow-up actions attached to assistant messages.

A follow-up is a structured, persisted offer that the student can accept
with a click or a short reply ("yes", "continue", etc.). It's distinct from
SparkAgentInteraction (which pauses execution) and from freeform text.
"""
import uuid
from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone

ACTION_TYPES = [
    ('teaching_continue', 'Continue teaching current topic'),
    ('teaching_topic', 'Switch to teaching topic'),
    ('answer_followup', 'Follow up on answer'),
    ('practice_problem', 'Try a practice problem'),
    ('clarification', 'Offer clarification'),
]

STATUS_CHOICES = [
    ('pending', 'Pending'),
    ('executing', 'Executing'),
    ('failed', 'Failed'),
    ('interrupted', 'Interrupted'),
    ('consumed', 'Consumed'),
    ('expired', 'Expired'),
    ('cancelled', 'Cancelled'),
]


class SparkFollowUp(models.Model):
    """A structured conversational offer associated with an assistant message.

    The offer persists across page reloads and stream reconnection. When the
    student clicks a suggestion or replies with a short affirmative, the
    follow-up is resolved idempotently and the intended action executes.

    Payloads are validated at creation time and never trusted from the client.
    Ownership is enforced on every resolution attempt.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey('pwanimate.PwanimateMessage', on_delete=models.CASCADE,
        related_name='follow_ups', null=True, blank=True)
    conversation = models.ForeignKey('pwanimate.PwanimateConversation', on_delete=models.CASCADE,
        related_name='follow_ups')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='follow_ups')
    run = models.ForeignKey('pwanimate.SparkAgentRun', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='follow_ups')
    action_type = models.CharField(max_length=32, choices=ACTION_TYPES, db_index=True)
    label = models.CharField(max_length=120)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='pending', db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    execution = models.JSONField(default=dict, blank=True)
    execution_run = models.ForeignKey('pwanimate.SparkAgentRun', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+')
    claimed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['created_at', 'id']
        indexes = [
            models.Index(fields=['message', 'status'], name='spark_followup_msg_status_idx'),
            models.Index(fields=['conversation', 'status'], name='spark_followup_conv_status_idx'),
            models.Index(fields=['user', 'status'], name='spark_followup_user_status_idx'),
        ]
        constraints = [
            models.UniqueConstraint(fields=['id', 'version'], name='spark_followup_id_version_uniq'),
        ]

    def clean(self):
        super().clean()
        if self.message_id and self.message.role != 'assistant':
            raise ValidationError('Follow-ups must be attached to assistant messages.')
        if self.message_id and self.message.conversation_id != self.conversation_id:
            raise ValidationError('Follow-up message must belong to its conversation.')
        if self.message_id and self.message.conversation.user_id != self.user_id:
            raise ValidationError('Follow-up conversation must belong to the user.')
        if self.run_id and self.run.user_id != self.user_id:
            raise ValidationError('Follow-up run must belong to the user.')
        if self.run_id and self.run.user_message.conversation_id != self.conversation_id:
            raise ValidationError('Follow-up run must belong to the same conversation.')

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    def serialize(self):
        return {
            'id': str(self.id),
            'action_type': self.action_type,
            'label': self.label,
            'payload': self.payload,
            'status': self.status,
            'expires_at': self.expires_at.isoformat(),
            'version': self.version,
        }
