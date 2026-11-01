import math
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from . import states


class UploadStatus(models.TextChoices):
    QUEUED = states.QUEUED, "Queued"
    PREPARING = states.PREPARING, "Preparing"
    UPLOADING = states.UPLOADING, "Uploading"
    VERIFYING = states.VERIFYING, "Verifying"
    PROCESSING = states.PROCESSING, "Processing"
    PUBLISHING = states.PUBLISHING, "Publishing"
    COMPLETED = states.COMPLETED, "Completed"
    PAUSED = states.PAUSED, "Paused"
    FAILED = states.FAILED, "Failed"
    CANCELLED = states.CANCELLED, "Cancelled"


class UploadRecord(models.Model):
    """Durable, account-wide upload history. Never auto-deleted."""

    # What the transferred bytes become once the transfer completes.
    KIND_POST = "post"
    KIND_DOCUMENT = "document"
    KIND_TIMETABLE = "timetable"
    KIND_CHOICES = (
        (KIND_POST, "Post media"),
        (KIND_DOCUMENT, "Document"),
        (KIND_TIMETABLE, "Timetable image"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="uploads",
    )
    # Client-generated UUID; idempotency key within the user's account.
    client_upload_id = models.UUIDField(db_index=True)
    # Optional group: all sessions sharing it attach to a single post.
    post_group = models.UUIDField(null=True, blank=True, db_index=True)
    # What this transfer produces. Posts publish a feed post; documents
    # publish a Document whose processing runs in the background.
    kind = models.CharField(
        max_length=20, choices=KIND_CHOICES, default=KIND_POST, db_index=True,
    )
    filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=127, blank=True, default="")
    file_size = models.BigIntegerField()
    sha256 = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(
        max_length=20, choices=UploadStatus.choices,
        default=UploadStatus.QUEUED, db_index=True,
    )
    progress_bytes = models.BigIntegerField(default=0)
    total_bytes = models.BigIntegerField(default=0)
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    retry_count = models.PositiveIntegerField(default=0)
    post = models.ForeignKey(
        "posts.Post", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="source_uploads",
    )
    document = models.ForeignKey(
        "documents.Document", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="source_uploads",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "client_upload_id"],
                name="unique_user_client_upload",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "status"], name="uploads_user_status_idx"),
        ]

    def __str__(self):
        return f"{self.filename} [{self.status}]"

    def transition_to(self, new_status, *, error_code="", error_message=""):
        """Enforce the lifecycle contract; raises ValidationError if illegal."""
        new_status = str(new_status)
        if not states.can_transition(self.status, new_status):
            raise ValidationError(
                f"Illegal upload transition: {self.status} -> {new_status}"
            )
        self.status = new_status
        if new_status == UploadStatus.FAILED:
            self.error_code = error_code
            self.error_message = error_message
        if new_status in (states.COMPLETED, states.CANCELLED):
            self.completed_at = self.completed_at or timezone.now()
        return self.status


class UploadSession(models.Model):
    """Recoverable direct-to-R2 transfer state for one UploadRecord."""

    PROTOCOL_S3_MULTIPART = "s3-multipart"
    PROTOCOL_SINGLE_PUT = "single-put"
    PROTOCOL_CHOICES = (
        (PROTOCOL_S3_MULTIPART, "S3 multipart"),
        (PROTOCOL_SINGLE_PUT, "Single PUT"),
    )

    record = models.OneToOneField(
        UploadRecord, on_delete=models.CASCADE, related_name="session",
    )
    protocol = models.CharField(
        max_length=20, choices=PROTOCOL_CHOICES,
        default=PROTOCOL_S3_MULTIPART,
    )
    r2_key = models.CharField(max_length=1024)
    r2_upload_id = models.CharField(
        max_length=1024, blank=True, default="",
        help_text="S3 multipart upload ID; empty for single-put.",
    )
    part_size = models.PositiveIntegerField(default=states.PART_SIZE_BYTES)
    total_parts = models.PositiveIntegerField(default=1)
    expires_at = models.DateTimeField(null=True, blank=True)
    finalized = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"session for {self.record_id} ({self.protocol})"

    @property
    def verified_parts(self):
        """Part numbers confirmed via part_verified events (server-side)."""
        return list(
            self.record.events.filter(event_type=UploadEvent.EventType.PART_VERIFIED)
            .order_by("created_at")
            .values_list("detail__part_number", flat=True)
        )

    @staticmethod
    def plan_protocol(file_size):
        if file_size <= states.SINGLE_PUT_MAX_BYTES:
            return UploadSession.PROTOCOL_SINGLE_PUT, 1
        return (
            UploadSession.PROTOCOL_S3_MULTIPART,
            max(math.ceil(file_size / states.PART_SIZE_BYTES), 1),
        )


class UploadPostGroup(models.Model):
    """Maps one client post_group UUID to the single post its sessions share.

    The unique (user, group_id) constraint plus row locking is what makes
    multi-file completion race-safe: concurrent completes attach to the same
    post instead of creating duplicates.
    """

    group_id = models.UUIDField(db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="upload_post_groups",
    )
    post = models.ForeignKey(
        "posts.Post", null=True, blank=True, on_delete=models.CASCADE,
        related_name="upload_groups",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "group_id"],
                name="unique_user_post_group",
            ),
        ]

    def __str__(self):
        return f"group {self.group_id} -> post {self.post_id}"


class UploadEvent(models.Model):
    """Operational history. Detailed progress is local-only; meaningful
    transitions are recorded here at a controlled rate."""

    class EventType(models.TextChoices):
        CREATED = "created", "Created"
        RECOVERED = "recovered", "Recovered"
        AUTHORIZED = "authorized", "Authorized"
        PART_VERIFIED = "part_verified", "Part verified"
        RESUMED = "resumed", "Resumed"
        RETRY_SCHEDULED = "retry_scheduled", "Retry scheduled"
        FINALIZED = "finalized", "Transfer finalized"
        PROCESSING_STARTED = "processing_started", "Processing started"
        PROCESSING_FAILED = "processing_failed", "Processing failed"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        DELETED = "deleted", "Deleted"

    record = models.ForeignKey(
        UploadRecord, on_delete=models.CASCADE, related_name="events",
    )
    event_type = models.CharField(max_length=32, choices=EventType.choices)
    # Safe diagnostics only: part numbers, attempt counts, error codes.
    # Never credentials, URLs, or file bytes.
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["record", "event_type"], name="upload_events_lookup_idx"),
        ]

    def __str__(self):
        return f"{self.record_id} {self.event_type}"
