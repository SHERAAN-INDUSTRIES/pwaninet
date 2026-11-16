"""Delivery metadata only; message bodies and facts remain in existing models."""
import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone

class SparkMemoryDispatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    conversation = models.ForeignKey('pwanimate.PwanimateConversation', null=True, on_delete=models.SET_NULL)
    message = models.ForeignKey('pwanimate.PwanimateMessage', null=True, on_delete=models.SET_NULL)
    source_message_id = models.PositiveBigIntegerField()
    source_conversation_id = models.CharField(max_length=64)
    consent_revision = models.PositiveIntegerField()
    eligibility_version = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=16, default='pending', choices=[(s,s) for s in
        ['pending','dispatched','processing','completed','cancelled','failed']])
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    claim_token = models.CharField(max_length=32, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['source_message_id','consent_revision'], name='spark_mem_dispatch_once')]
        indexes = [models.Index(fields=['status','next_attempt_at'], name='spark_mem_dispatch_due'),
            models.Index(fields=['status','lease_until'], name='spark_mem_dispatch_lease')]
