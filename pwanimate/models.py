"""Pwanimate Domain Models.

Phase 2C: MVP DocumentChunk Persistence.
Establishes a reliable, version-aware database representation for
structured document chunks produced by extraction and chunking pipelines.
"""

import hashlib
import uuid
from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.utils import timezone
from pgvector.django import VectorField


class DocumentChunk(models.Model):
    """
    Represents an atomic, ordered, version-aware structural chunk of a document.

    Each chunk belongs authoritatively to a specific DocumentVersion and inherits
    its relationship to the root Document. Chunks preserve structural provenance
    (page numbers, slide numbers, section headings, element types) without duplicating
    the full academic hierarchy, keeping retrieval grounded and citation-ready.
    """

    CHUNK_TYPE_CHOICES = [
        ('heading', 'Heading'),
        ('paragraph', 'Paragraph'),
        ('table', 'Table'),
        ('slide', 'Slide'),
        ('notes', 'Notes'),
        ('code', 'Code'),
        ('text', 'Text'),
    ]

    EMBEDDING_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    # Identity & Relationships
    id = models.BigAutoField(primary_key=True)
    document = models.ForeignKey(
        'documents.Document',
        on_delete=models.CASCADE,
        related_name='chunks',
        help_text="Source document"
    )
    document_version = models.ForeignKey(
        'documents.DocumentVersion',
        on_delete=models.CASCADE,
        related_name='chunks',
        help_text="Specific document version that produced this chunk"
    )

    # Deterministic Ordering
    chunk_index = models.PositiveIntegerField(
        validators=[MinValueValidator(0)],
        help_text="Zero-based sequence index within the document version"
    )

    # Content & Integrity
    content = models.TextField(
        help_text="Extracted text content of the chunk"
    )
    content_hash = models.CharField(
        max_length=64,
        db_index=True,
        help_text="SHA-256 hash of content for change and duplicate detection"
    )

    # Structural Provenance
    chunk_type = models.CharField(
        max_length=30,
        choices=CHUNK_TYPE_CHOICES,
        default='paragraph',
        db_index=True,
        help_text="Semantic structural element type"
    )
    page_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Starting page number (1-indexed, for paginated formats like PDF)"
    )
    page_end = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Ending page number if chunk spans multiple pages"
    )
    slide_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Slide number (1-indexed, for presentations like PPTX)"
    )
    section_heading = models.CharField(
        max_length=300,
        blank=True,
        default='',
        help_text="Active section or chapter heading context"
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional location provenance (bbox coordinates, table row count, notes flags)"
    )

    # Lifecycle State
    is_active = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Whether this chunk is active and eligible for retrieval"
    )

    # Embedding Metadata & Vector Storage (Phase 2F)
    embedding = VectorField(
        dimensions=768,
        null=True,
        blank=True,
        help_text="Vector embedding of chunk content (768 dimensions for text-embedding-004)"
    )
    embedding_status = models.CharField(
        max_length=20,
        choices=EMBEDDING_STATUS_CHOICES,
        default='pending',
        db_index=True,
        help_text="Lifecycle status of vector embedding generation"
    )
    embedding_model = models.CharField(
        max_length=100,
        blank=True,
        default='',
        help_text="Model identifier used to produce vector embeddings"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['document_version', 'chunk_index']
        verbose_name = "Document Chunk"
        verbose_name_plural = "Document Chunks"
        constraints = [
            models.UniqueConstraint(
                fields=['document_version', 'chunk_index'],
                name='pwanimate_chunk_version_index_uniq'
            ),
            models.CheckConstraint(
                condition=models.Q(chunk_index__gte=0),
                name='pwanimate_chunk_index_non_negative'
            ),
            models.CheckConstraint(
                condition=~models.Q(content=''),
                name='pwanimate_chunk_content_not_empty'
            ),
        ]
        indexes = [
            models.Index(fields=['document_version', 'chunk_index']),
            models.Index(fields=['document_version', 'is_active']),
            models.Index(fields=['document', 'is_active']),
            models.Index(fields=['page_number']),
            models.Index(fields=['slide_number']),
        ]

    def __str__(self):
        return f"{self.document.title} [v{self.document_version.version_number}:{self.chunk_index}]"

    def clean(self):
        super().clean()
        if self.document_version_id and self.document_id:
            if self.document_id != self.document_version.document_id:
                raise ValidationError({
                    'document_version': 'Document version must belong to the referenced document.'
                })

    def save(self, *args, **kwargs):
        # Auto-fill document from document_version if omitted
        if self.document_version_id and not self.document_id:
            self.document_id = self.document_version.document_id

        # Auto-compute SHA-256 content hash if not set
        if not self.content_hash and self.content:
            self.content_hash = hashlib.sha256(self.content.encode('utf-8')).hexdigest()

        self.clean()
        super().save(*args, **kwargs)

    @property
    def char_count(self) -> int:
        """Character count of chunk content."""
        return len(self.content) if self.content else 0

    def activate(self):
        """Activate chunk for retrieval."""
        self.is_active = True
        self.save(update_fields=['is_active', 'updated_at'])

    def deactivate(self):
        """Deactivate chunk from retrieval."""
        self.is_active = False
        self.save(update_fields=['is_active', 'updated_at'])


class PwanimateConversation(models.Model):
    """
    Persistent conversational thread between an authenticated student and Pwanimate.
    """
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pwanimate_conversations",
        db_index=True,
    )
    title = models.CharField(
        max_length=255,
        blank=True,
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        db_index=True,
    )

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["user", "-updated_at"]),
        ]

    def __str__(self):
        return f"{self.title or 'Conversation'} ({self.id})"


class PwanimateMessage(models.Model):
    """
    Individual conversational turn within a Pwanimate conversation.
    """
    ROLE_CHOICES = [
        ("user", "User"),
        ("assistant", "Assistant"),
    ]

    id = models.BigAutoField(primary_key=True)
    conversation = models.ForeignKey(
        PwanimateConversation,
        on_delete=models.CASCADE,
        related_name="messages",
        db_index=True,
    )
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        db_index=True,
    )
    content = models.TextField()
    citations = models.JSONField(
        default=list,
        blank=True,
    )
    sources = models.JSONField(
        default=list,
        blank=True,
    )
    # Which model actually served this turn, and what it cost.
    #
    # `UsageRecord` holds the billing ledger, but the answer itself did not
    # record who produced it, so answers could not be joined to models for
    # evaluation. Without this there is no way to answer "is the new model
    # better?" -- which is the question every fine-tune has to answer first.
    provider = models.CharField(
        max_length=32,
        blank=True,
        default="",
        db_index=True,
    )
    model_id = models.CharField(
        max_length=160,
        blank=True,
        default="",
        db_index=True,
    )
    task = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
    )
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["conversation", "created_at"]),
        ]

    def clean(self):
        super().clean()
        if self.role not in dict(self.ROLE_CHOICES):
            raise ValidationError(f"Invalid role '{self.role}'. Only 'user' and 'assistant' are allowed.")
        if not self.content or not self.content.strip():
            raise ValidationError("Message content cannot be empty.")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.role.capitalize()} message in {self.conversation_id}"

    @property
    def people(self) -> list:
        """
        Extract structured person profiles from persisted sources if present.
        Returns empty list if no people discovery items were associated with this turn.
        """
        if not self.sources or not isinstance(self.sources, list):
            return []
        people_list = []
        for src in self.sources:
            if isinstance(src, dict) and src.get("source") == "user":
                person = src.get("person")
                if isinstance(person, dict) and person.get("username"):
                    people_list.append(person)
        return people_list


class PwanimateMessageEmbedding(models.Model):
    """Search vector for one persisted conversation message."""

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    message = models.OneToOneField(
        PwanimateMessage,
        on_delete=models.CASCADE,
        related_name="embedding_record",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pwanimate_message_embeddings",
        db_index=True,
    )
    embedding = VectorField(dimensions=768, null=True, blank=True)
    embedding_model = models.CharField(max_length=100, blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        db_index=True,
    )
    error = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["user", "embedding_model"]),
        ]


class PwanimateTurnTrace(models.Model):
    """Durable, privacy-scoped trace for replaying and evaluating one turn."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pwanimate_turn_traces",
        db_index=True,
    )
    conversation = models.ForeignKey(
        PwanimateConversation,
        on_delete=models.CASCADE,
        related_name="turn_traces",
    )
    user_message = models.ForeignKey(
        PwanimateMessage,
        on_delete=models.CASCADE,
        related_name="user_turn_traces",
    )
    assistant_message = models.ForeignKey(
        PwanimateMessage,
        on_delete=models.CASCADE,
        related_name="assistant_turn_traces",
        null=True,
        blank=True,
    )
    request_id = models.CharField(max_length=128, db_index=True)
    prompt_version = models.CharField(max_length=64, blank=True, default="")
    context_version = models.CharField(max_length=64, blank=True, default="")
    retrieval_trace = models.JSONField(default=list, blank=True)
    memory_trace = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["conversation", "created_at"]),
        ]


class PwanimateMessageFeedback(models.Model):
    """
    A student's verdict on one Spark answer.

    This is the outcome signal. Right now the platform records what was
    served and what it cost, but nothing records whether the answer was
    *right* -- so there is no label to fine-tune on and no way to prove that
    a new model is better than the one it replaces.

    One row per student per answer. A student may change their mind; the
    last verdict wins.
    """

    REASON_CHOICES = [
        ("wrong_answer", "The answer is wrong"),
        ("not_my_document", "It ignored my document"),
        ("too_slow", "It took too long"),
        ("wrong_language", "Wrong language or register"),
        ("too_long", "Too long or too short"),
        ("other", "Something else"),
    ]

    message = models.ForeignKey(
        PwanimateMessage,
        on_delete=models.CASCADE,
        related_name="feedback",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pwanimate_feedback",
    )
    helpful = models.BooleanField()
    reason = models.CharField(
        max_length=32,
        choices=REASON_CHOICES,
        blank=True,
        default="",
    )
    comment = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["message", "user"],
                name="uniq_pwanimate_feedback_per_user_per_message",
            ),
        ]
        indexes = [
            models.Index(fields=["message"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self):
        verdict = "helpful" if self.helpful else "not helpful"
        return f"{verdict} on message {self.message_id}"


class PwanimateTone(models.TextChoices):
    """Controlled tone choices for Pwanimate assistant interactions."""
    NEUTRAL = "neutral", "Neutral"
    FRIENDLY = "friendly", "Friendly"
    PROFESSIONAL = "professional", "Professional"
    ACADEMIC = "academic", "Academic"


class PwanimateResponseStyle(models.TextChoices):
    """Controlled response style choices for Pwanimate assistant interactions."""
    CONCISE = "concise", "Concise"
    BALANCED = "balanced", "Balanced"
    DETAILED = "detailed", "Detailed"


class PwanimateVoiceStyle(models.TextChoices):
    """Voice presentation styles available for Spark speech."""

    WARM = "warm", "Warm Guide"
    ACADEMIC = "academic", "Focused Academic"


class PwanimateVoiceGender(models.TextChoices):
    """Voice gender preferences available for Spark speech."""

    FEMALE = "female", "Female"
    MALE = "male", "Male"


class PwanimatePreferences(models.Model):
    """
    Dedicated AI assistant personalization preferences for an authenticated student.

    Strictly decoupled from Vasto platform account settings.
    Enforces a strict 1-to-1 relationship with the Django User model.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pwanimate_preferences",
        primary_key=True,
    )
    nickname = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Optional preferred name Pwanimate uses when addressing the student",
    )
    tone = models.CharField(
        max_length=20,
        choices=PwanimateTone.choices,
        default=PwanimateTone.NEUTRAL,
        db_index=True,
        help_text="Assistant conversational tone",
    )
    response_style = models.CharField(
        max_length=20,
        choices=PwanimateResponseStyle.choices,
        default=PwanimateResponseStyle.BALANCED,
        db_index=True,
        help_text="Assistant response formatting style",
    )
    personal_instructions = models.TextField(
        max_length=500,
        blank=True,
        default="",
        help_text="User-authored stylistic guidance for Pwanimate (maximum 500 characters)",
    )
    voice_style = models.CharField(
        max_length=20,
        choices=PwanimateVoiceStyle.choices,
        default=PwanimateVoiceStyle.WARM,
        help_text="Preferred Spark speech style",
    )
    voice_gender = models.CharField(
        max_length=20,
        choices=PwanimateVoiceGender.choices,
        default=PwanimateVoiceGender.FEMALE,
        help_text="Preferred Spark speech voice gender",
    )
    auto_tts_enabled = models.BooleanField(
        default=False,
        help_text="Automatically read new Spark replies aloud",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Pwanimate Preferences"
        verbose_name_plural = "Pwanimate Preferences"

    def clean(self):
        super().clean()
        if self.nickname:
            self.nickname = self.nickname.strip()
            if len(self.nickname) > 50:
                raise ValidationError({"nickname": "Nickname cannot exceed 50 characters."})
        else:
            self.nickname = ""

        if self.tone not in dict(PwanimateTone.choices):
            raise ValidationError({"tone": f"Invalid tone '{self.tone}'. Allowed choices: {PwanimateTone.values}."})

        if self.response_style not in dict(PwanimateResponseStyle.choices):
            raise ValidationError(
                {"response_style": f"Invalid response style '{self.response_style}'. Allowed choices: {PwanimateResponseStyle.values}."}
            )

        if self.personal_instructions:
            self.personal_instructions = self.personal_instructions.strip()
            if len(self.personal_instructions) > 500:
                raise ValidationError({"personal_instructions": "Personal instructions cannot exceed 500 characters."})
        else:
            self.personal_instructions = ""

        if self.voice_style not in dict(PwanimateVoiceStyle.choices):
            raise ValidationError({"voice_style": "Invalid voice style."})
        if self.voice_gender not in dict(PwanimateVoiceGender.choices):
            raise ValidationError({"voice_gender": "Invalid voice gender."})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        user_ident = getattr(self.user, "username", str(self.user_id)) if hasattr(self, "user") else str(self.pk)
        return f"PwanimatePreferences({user_ident}, tone={self.tone}, style={self.response_style})"


def pwanimate_attachment_upload_path(instance, filename):
    """
    Generate a safe, collision-free, UUID-based storage path for Pwanimate attachments.
    Never uses untrusted user filenames as filesystem identifiers.
    Format: pwanimate/attachments/%Y/%m/<uuid>.<ext>
    """
    import os
    from django.utils import timezone
    now = timezone.now()
    ext = os.path.splitext(filename)[1].lower()
    return f"pwanimate/attachments/{now.year:04d}/{now.month:02d}/{instance.id}{ext}"


class PwanimateAttachment(models.Model):
    """
    Dedicated model for user chat attachments in Pwanimate.

    Supports images and documents attached to conversational turns,
    strictly bounded to authenticated users and private storage paths.
    """
    ATTACHMENT_TYPE_CHOICES = [
        ('image', 'Image'),
        ('document', 'Document'),
    ]
    PROCESSING_STATUS_CHOICES = [
        ("not_required", "Not required"),
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("ready", "Ready"),
        ("failed", "Failed"),
    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pwanimate_attachments",
        db_index=True,
    )
    conversation = models.ForeignKey(
        PwanimateConversation,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="attachments",
        db_index=True,
    )
    message = models.ForeignKey(
        PwanimateMessage,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attachments",
        db_index=True,
    )
    file = models.FileField(
        upload_to=pwanimate_attachment_upload_path,
        max_length=500,
        help_text="Secure, UUID-addressed file path",
    )
    file_name = models.CharField(
        max_length=255,
        help_text="Sanitized original display filename",
    )
    file_size = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text="File size in bytes",
    )
    mime_type = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Validated MIME type",
    )
    attachment_type = models.CharField(
        max_length=20,
        choices=ATTACHMENT_TYPE_CHOICES,
        default='document',
        db_index=True,
    )
    processing_status = models.CharField(
        max_length=20,
        choices=PROCESSING_STATUS_CHOICES,
        default="not_required",
        db_index=True,
    )
    processing_error = models.TextField(blank=True, default="")
    processed_at = models.DateTimeField(null=True, blank=True)
    # The one-time vision analysis of an uploaded image. Captured once at
    # upload and reused by every later turn, so a photo is analysed ONCE
    # instead of once per question.
    vision_summary = models.TextField(blank=True, default="")
    vision_model = models.CharField(max_length=120, blank=True, default="")
    # Set when the analysis failed. The image is then dropped from the
    # context (never re-sent) and later turns just carry the note.
    vision_error = models.TextField(blank=True, default="")
    # What to do with an image whose analysis failed: 'ask' (the default —
    # the student is offered the choice once), 'keep' (they want to reuse
    # it, so it stays in the context), 'drop' (never send it again).
    vision_context_policy = models.CharField(max_length=16, default="ask")
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["conversation", "created_at"]),
            models.Index(fields=["message"]),
        ]

    def __str__(self):
        return f"{self.file_name} ({self.attachment_type}, {self.id})"

    @property
    def extension(self) -> str:
        import os
        return os.path.splitext(self.file_name)[1].lower()

    @property
    def url(self) -> str:
        return f"/api/pwanimate/attachments/{self.id}/view/"

    @property
    def download_url(self) -> str:
        return f"/api/pwanimate/attachments/{self.id}/download/"

    def get_url(self) -> str:
        return self.url

    def get_download_url(self) -> str:
        return self.download_url


class PwanimateAttachmentChunk(models.Model):
    """Private, page-aware extracted text belonging to one chat upload."""

    attachment = models.ForeignKey(
        PwanimateAttachment,
        on_delete=models.CASCADE,
        related_name="chunks",
    )
    chunk_index = models.PositiveIntegerField()
    content = models.TextField()
    content_hash = models.CharField(max_length=64, db_index=True)
    page_number = models.PositiveIntegerField(null=True, blank=True)
    page_end = models.PositiveIntegerField(null=True, blank=True)
    chunk_type = models.CharField(max_length=30, default="text")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["attachment_id", "chunk_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["attachment", "chunk_index"],
                name="pwanimate_attachment_chunk_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["attachment", "page_number"], name="pwan_attach_page_idx"),
        ]


class PwanimateStudySession(models.Model):
    """
    Persistent Study Mode session associated one-to-one with a PwanimateConversation.

    Tracks learning lifecycle (active, paused, completed), objective/current topic,
    persisted Context Rail state (authorized resources and page positions),
    resume-banner dismissal, and the latest valid learning checkpoint.
    """

    STATUS_ACTIVE = "active"
    STATUS_PAUSED = "paused"
    STATUS_COMPLETED = "completed"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_PAUSED, "Paused"),
        (STATUS_COMPLETED, "Completed"),
    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pwanimate_study_sessions",
        db_index=True,
    )
    conversation = models.OneToOneField(
        PwanimateConversation,
        on_delete=models.CASCADE,
        related_name="study_session",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        db_index=True,
    )
    learning_objective = models.TextField(
        blank=True,
        default="",
        help_text="Primary learning objective for this study session",
    )
    current_topic = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Current topic or focus question",
    )
    context_state = models.JSONField(
        default=dict,
        blank=True,
        help_text="Persisted Context Rail state (active/pinned resource descriptors and page positions)",
    )
    latest_checkpoint = models.ForeignKey(
        "PwanimateStudyCheckpoint",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    resume_banner_dismissed_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    started_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )
    last_active_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )
    ended_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["-last_active_at", "-started_at"]
        indexes = [
            models.Index(
                fields=["user", "status", "-last_active_at"],
                name="pwan_study_usr_st_act_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status="active"),
                name="pwanimate_unique_active_study_session_per_user",
            ),
        ]

    def __str__(self):
        title = getattr(self.conversation, "title", "") or self.current_topic or "Study Session"
        return f"{title} ({self.status}, {self.id})"


class PwanimateStudyCheckpoint(models.Model):
    """
    Compact, structured snapshot of learning progress within a PwanimateStudySession.

    Anchored to the conversation message boundary (`up_to_message`) so that
    resumed sessions can combine the latest valid checkpoint with a bounded
    recent-message window without re-sending the entire transcript.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    session = models.ForeignKey(
        PwanimateStudySession,
        on_delete=models.CASCADE,
        related_name="checkpoints",
        db_index=True,
    )
    up_to_message = models.ForeignKey(
        PwanimateMessage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="study_checkpoints",
    )
    learning_objective = models.TextField(
        blank=True,
        default="",
    )
    current_topic = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    concepts_explained = models.JSONField(
        default=list,
        blank=True,
        help_text="Concepts explained by the assistant",
    )
    concepts_demonstrated = models.JSONField(
        default=list,
        blank=True,
        help_text="Concepts demonstrated by the student, with supporting user-message references",
    )
    inferred_understanding = models.TextField(
        blank=True,
        default="",
        help_text="Unverified assistant inference about learner understanding, separated from verified mastery",
    )
    misconceptions = models.JSONField(
        default=list,
        blank=True,
        help_text="Misconceptions or unresolved questions",
    )
    key_discoveries = models.JSONField(
        default=list,
        blank=True,
        help_text="Relevant decisions and discoveries",
    )
    recommended_next_step = models.TextField(
        blank=True,
        default="",
    )
    document_state = models.JSONField(
        default=dict,
        blank=True,
        help_text="Relevant document and page state at the checkpoint boundary",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["session", "-created_at"],
                name="pwan_study_ckpt_sess_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "up_to_message"],
                condition=models.Q(up_to_message__isnull=False),
                name="pwanimate_unique_study_ckpt_per_msg",
            ),
        ]

    def __str__(self):
        return f"Checkpoint for {self.session_id} (up_to_msg={self.up_to_message_id})"


class PendingAction(models.Model):
    """
    An action Spark proposes and the student has not yet approved.

    Every action Spark takes on the student's behalf goes through here
    first. The tool layer only ever *proposes*; nothing is sent, followed or
    posted until the student confirms. That is the difference between an
    assistant and an agent that can embarrass you.

    The five rules this design implements:

    1. **Confirmation.** The tool creates a pending action and returns it.
       Nothing else happens until the student confirms.
    2. **Idempotency.** One action per (student, action, content). A retry
       returns the existing action instead of sending twice.
    3. **A real dry run.** The proposal carries the exact message that
       would be sent, so the student sees the real thing.
    4. **Rate limits.** Both the number of proposals and the number of
       executed actions are capped per student per hour.
    5. **Undo.** A confirmed action can be undone inside a short window --
       a sent message is soft-deleted, a follow is released.
    """

    ACTION_SEND_MESSAGE = "send_message"
    ACTION_FOLLOW_USER = "follow_user"
    ACTION_CHOICES = [
        (ACTION_SEND_MESSAGE, "Send a message"),
        (ACTION_FOLLOW_USER, "Follow a user"),
    ]

    STATUS_PENDING = "pending"
    STATUS_CONFIRMED = "confirmed"
    STATUS_CANCELLED = "cancelled"
    STATUS_EXPIRED = "expired"
    STATUS_FAILED = "failed"
    STATUS_UNDONE = "undone"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_CONFIRMED, "Confirmed"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_FAILED, "Failed"),
        (STATUS_UNDONE, "Undone"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pending_actions",
    )
    conversation = models.ForeignKey(
        "PwanimateConversation",
        on_delete=models.CASCADE,
        related_name="pending_actions",
        null=True,
        blank=True,
    )
    action_type = models.CharField(max_length=32, choices=ACTION_CHOICES, db_index=True)
    params = models.JSONField(default=dict, blank=True)
    # What the student is being asked to approve, in plain words.
    summary = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    idempotency_key = models.CharField(max_length=64, unique=True)
    result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    undone_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["user", "action_type", "created_at"]),
        ]

    def __str__(self):
        return f"{self.action_type} for user {self.user_id} ({self.status})"

    @property
    def is_actionable(self) -> bool:
        return self.status == self.STATUS_PENDING and self.expires_at > timezone.now()
