from django.db import models
from django.conf import settings
from django.core.validators import RegexValidator, MinValueValidator, MaxValueValidator, URLValidator


class Conversation(models.Model):
    """Conversation model for direct messages and group chats."""
    DIRECT = "direct"
    GROUP = "group"

    TYPE_CHOICES = [
        (DIRECT, "Direct"),
        (GROUP, "Group"),
    ]

    type = models.CharField(
        max_length=10,
        choices=TYPE_CHOICES,
        default=DIRECT
    )
    name = models.CharField(max_length=255, null=True, blank=True)
    group = models.OneToOneField(
        'groups.Group', null=True, blank=True, on_delete=models.CASCADE,
        related_name='chat_conversation',
    )
    is_encrypted = models.BooleanField(default=True, help_text="End-to-end encryption enabled")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        if self.type == self.GROUP and self.name:
            return self.name
        return f"Conversation ({self.id})"

    @property
    def last_message(self):
        """Get the most recent message in this conversation."""
        if hasattr(self, '_cached_last_message'):
            return self._cached_last_message
        return self.messages.order_by('-created_at').first()

    def get_last_message_read_status(self, user):
        """Get the read status of the last message for a specific user."""
        last_msg = self.last_message
        if not last_msg or last_msg.sender_id != user.id:
            return None

        # Check if any other member has read this message using prefetched members when available
        if 'members' in getattr(self, '_prefetched_objects_cache', {}):
            other_members = [m for m in self.members.all() if m.user_id != user.id]
        else:
            other_members = self.members.exclude(user=user).only('id', 'user_id', 'last_read_message_id')

        for member in other_members:
            if member.last_read_message_id and member.last_read_message_id >= last_msg.id:
                return 'read'

        # Preserve DB delivered state so refresh doesn't downgrade ✓✓ -> ✓
        if last_msg.status == 'delivered':
            return 'delivered'
        # If message exists but hasn't been read yet, it's just 'sent'
        return 'sent' if last_msg else None

    @classmethod
    def get_direct_conversation_between(cls, user1, user2):
        """
        Find existing direct conversation between two users.
        Returns the conversation if found, None otherwise.
        """
        return cls.objects.filter(
            type=cls.DIRECT,
            members__user=user1
        ).filter(
            members__user=user2
        ).distinct().first()


class ConversationMember(models.Model):
    """Model to track conversation participants and their state."""
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name='members'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='conversation_memberships'
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    last_read_message = models.ForeignKey(
        'Message',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='read_by_members'
    )
    is_muted = models.BooleanField(default=False)
    is_pinned = models.BooleanField(default=False)
    pinned_at = models.DateTimeField(null=True, blank=True)
    # E2E encryption: Store public key for each participant
    public_key = models.TextField(blank=True, null=True, help_text="User's public key for this conversation")

    class Meta:
        unique_together = ('conversation', 'user')
        indexes = [
            models.Index(fields=['conversation', 'user']),
            models.Index(fields=['user', 'conversation']),
            models.Index(fields=['user', 'is_pinned']),
        ]

    def __str__(self):
        return f"{self.user.username} in {self.conversation}"


class Message(models.Model):
    """Message model for conversations with E2E encryption support."""
    MESSAGE_TYPE_CHOICES = [
        ('text', 'Text'),
        ('media_group', 'Media Group'),
        ('system', 'System'),
        ('audio', 'Audio'),
    ]
    
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name='messages',
        db_index=True
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sent_messages',
        db_index=True
    )
    # Plaintext content (for non-encrypted or server-side storage)
    content = models.TextField(blank=True, null=True)
    # Encrypted content (for E2E encrypted messages)
    encrypted_content = models.TextField(blank=True, null=True)
    is_encrypted = models.BooleanField(default=False)
    
    # Legacy single attachment field (deprecated, kept for backward compatibility)
    attachment = models.FileField(
        upload_to='message_attachments/%Y/%m/%d/',
        null=True,
        blank=True
    )
    attachment_type = models.CharField(
        max_length=20,
        choices=[
            ('image', 'Image'),
            ('video', 'Video'),
            ('audio', 'Audio'),
            ('document', 'Document'),
        ],
        null=True,
        blank=True
    )
    
    # New fields for multi-file support
    global_caption = models.TextField(blank=True, null=True)
    message_type = models.CharField(
        max_length=20,
        choices=MESSAGE_TYPE_CHOICES,
        default='text'
    )
    
    # Link metadata for rich link previews (legacy fields - kept for backward compatibility)
    link_url = models.URLField(max_length=2048, null=True, blank=True)
    link_title = models.CharField(max_length=500, null=True, blank=True)
    link_description = models.TextField(null=True, blank=True)
    link_image = models.URLField(max_length=2048, null=True, blank=True)
    link_type = models.CharField(
        max_length=50,
        choices=[
            ('link', 'Link'),
            ('facebook', 'Facebook'),
            ('youtube', 'YouTube'),
            ('instagram', 'Instagram'),
            ('twitter', 'Twitter'),
            ('internal_post', 'Internal Post'),
            ('internal_profile', 'Internal Profile'),
        ],
        default='link',
        null=True,
        blank=True
    )
    
    # New dedicated link preview relationship
    link_preview = models.ForeignKey(
        'LinkPreview',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='messages'
    )
    reply_to = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='replies'
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False)
    is_forwarded = models.BooleanField(default=False)
    status = models.CharField(
        max_length=20,
        choices=[
            ('sent', 'Sent'),
            ('delivered', 'Delivered'),
            ('read', 'Read'),
        ],
        default='sent'
    )

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['conversation', 'created_at']),
            models.Index(fields=['sender', 'created_at']),
        ]

    def __str__(self):
        if self.is_encrypted:
            return f"Encrypted message from {self.sender.username}"
        preview = self.content[:50] + '...' if self.content and len(self.content) > 50 else self.content
        return f"Message from {self.sender.username}: {preview}"


class MessageAttachment(models.Model):
    """Attachment model for supporting multiple files per message."""
    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name='attachments'
    )
    file = models.FileField(
        upload_to='message_attachments/%Y/%m/%d/'
    )
    file_type = models.CharField(
        max_length=20,
        choices=[
            ('image', 'Image'),
            ('video', 'Video'),
            ('audio', 'Audio'),
            ('document', 'Document'),
        ]
    )
    caption = models.TextField(blank=True, null=True)
    order = models.IntegerField(default=0)
    size = models.BigIntegerField(default=0)
    width = models.IntegerField(null=True, blank=True)
    height = models.IntegerField(null=True, blank=True)
    duration = models.FloatField(null=True, blank=True)
    # Background video processing state (prevents silent failures)
    processing_status = models.CharField(
        max_length=20,
        choices=[('pending', 'Pending'), ('processing', 'Processing'), ('ready', 'Ready'), ('failed', 'Failed')],
        default='ready',
    )
    processing_error = models.TextField(blank=True, null=True)
    celery_task_id = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order']
        indexes = [
            models.Index(fields=['message', 'order']),
            models.Index(fields=['file_type']),
        ]

    def __str__(self):
        return f"Attachment {self.id} ({self.file_type}) for message {self.message.id}"


class MessageReaction(models.Model):
    """Reaction model for messages."""
    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name='reactions'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='message_reactions'
    )
    emoji = models.CharField(max_length=10, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('message', 'user', 'emoji')
        indexes = [
            models.Index(fields=['message', 'emoji']),
            models.Index(fields=['user', 'created_at']),
        ]

    def __str__(self):
        return f"{self.user.username} reacted {self.emoji} to message {self.message.id}"


class PendingMessage(models.Model):
    """Queue for outgoing messages with unified state machine."""
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('queued', 'Queued'),
        ('uploading', 'Uploading'),
        ('sending', 'Sending'),
        ('sent', 'Sent'),
        ('delivered', 'Delivered'),
        ('read', 'Read'),
        ('failed', 'Failed'),
        ('retrying', 'Retrying'),
    ]

    MESSAGE_TYPE_CHOICES = [
        ('text', 'Text'),
        ('image', 'Image'),
        ('video', 'Video'),
        ('audio', 'Audio'),
        ('document', 'Document'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='pending_messages'
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name='pending_messages'
    )
    temp_id = models.CharField(max_length=100, unique=True, db_index=True)
    message_type = models.CharField(
        max_length=20,
        choices=MESSAGE_TYPE_CHOICES,
        default='text'
    )
    content = models.TextField(blank=True, null=True)
    encrypted_content = models.TextField(blank=True, null=True)
    is_encrypted = models.BooleanField(default=False)
    reply_to_id = models.IntegerField(null=True, blank=True)
    
    # Attachment fields
    attachment = models.FileField(
        upload_to='pending_attachments/%Y/%m/%d/',
        null=True,
        blank=True
    )
    attachment_type = models.CharField(
        max_length=20,
        null=True,
        blank=True
    )
    attachment_url = models.URLField(max_length=2048, null=True, blank=True)  # For uploaded attachment URL
    
    # Media preview fields
    media_metadata = models.JSONField(default=dict, blank=True)  # Stores crop, rotation, trim data
    
    # Link fields
    link_url = models.URLField(max_length=2048, null=True, blank=True)
    link_title = models.CharField(max_length=500, null=True, blank=True)
    link_description = models.TextField(null=True, blank=True)
    link_image = models.URLField(max_length=2048, null=True, blank=True)
    link_type = models.CharField(max_length=50, null=True, blank=True)
    
    # State machine fields
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='draft'
    )
    retry_count = models.IntegerField(default=0)
    max_retries = models.IntegerField(default=3)
    last_error = models.TextField(blank=True, null=True)
    
    # Sync fields
    server_message_id = models.IntegerField(null=True, blank=True, db_index=True)  # Maps to actual Message.id after send
    synced_at = models.DateTimeField(null=True, blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    queued_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['conversation', 'status']),
            models.Index(fields=['temp_id']),
            models.Index(fields=['server_message_id']),
            models.Index(fields=['created_at']),
            models.Index(fields=['status', 'created_at']),
        ]
        ordering = ['created_at']

    def __str__(self):
        return f"PendingMessage {self.temp_id} ({self.status}) for user {self.user.username}"
    
    def can_retry(self):
        """Check if message can be retried."""
        return self.retry_count < self.max_retries and self.status in ['failed', 'retrying']
    
    def transition_to(self, new_status):
        """Transition to new status with timestamp updates."""
        old_status = self.status
        self.status = new_status
        
        if new_status == 'queued' and not self.queued_at:
            self.queued_at = timezone.now()
        elif new_status == 'sent' and not self.sent_at:
            self.sent_at = timezone.now()
        elif new_status in ['sent', 'delivered', 'read'] and not self.synced_at:
            self.synced_at = timezone.now()
        
        self.save()
        return old_status


class LinkPreview(models.Model):
    """Model for storing cached link preview metadata and images."""
    
    url = models.URLField(max_length=2048, unique=True, db_index=True)
    title = models.CharField(max_length=500, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    site_name = models.CharField(max_length=255, blank=True, null=True)
    
    # Locally cached images (not hotlinked)
    image = models.ImageField(
        upload_to='link_previews/%Y/%m/%d/',
        null=True,
        blank=True,
        help_text="Cached OG image"
    )
    favicon = models.ImageField(
        upload_to='link_favicons/%Y/%m/%d/',
        null=True,
        blank=True,
        help_text="Cached favicon"
    )
    
    # Domain extraction for fallback UI
    domain = models.CharField(max_length=255, blank=True, null=True)
    media_type = models.CharField(max_length=50, blank=True, default='link')
    remote_image_url = models.URLField(
        max_length=2048,
        blank=True,
        null=True,
        help_text="Fallback remote image URL"
    )
    
    # Cache management
    cached_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Error tracking
    fetch_error = models.TextField(blank=True, null=True)
    fetch_failed = models.BooleanField(default=False)
    
    class Meta:
        ordering = ['-cached_at']
        indexes = [
            models.Index(fields=['url']),
            models.Index(fields=['cached_at']),
        ]
        verbose_name = "Link Preview"
        verbose_name_plural = "Link Previews"
    
    def __str__(self):
        return f"LinkPreview for {self.url[:50]}..."
    
    @property
    def thumbnail_url(self):
        """Return cached image URL if available, else remote_image_url."""
        if self.image:
            try:
                return self.image.url
            except Exception:
                pass
        return self.remote_image_url or ''

    def has_thumbnail(self):
        """Check if preview has a cached thumbnail image or remote image."""
        return bool(self.image) or bool(self.remote_image_url)
    
    def has_favicon(self):
        """Check if preview has a cached favicon."""
        return bool(self.favicon)


class ConversationTheme(models.Model):
    """Per-user per-conversation theme settings."""
    
    THEME_TYPES = [
        ('solid', 'Solid Color'),
        ('gradient', 'CSS Gradient'),
        ('image', 'Image Wallpaper'),
    ]
    
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='conversation_themes'
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name='themes'
    )
    theme_type = models.CharField(
        max_length=10,
        choices=THEME_TYPES,
        default='solid'
    )
    
    # Solid color theme
    light_color = models.CharField(
        max_length=7,
        validators=[RegexValidator(r'^#[0-9A-Fa-f]{6}$', 'Enter a valid hex color')],
        blank=True,
        null=True,
        help_text="Hex color for light mode"
    )
    dark_color = models.CharField(
        max_length=7,
        validators=[RegexValidator(r'^#[0-9A-Fa-f]{6}$', 'Enter a valid hex color')],
        blank=True,
        null=True,
        help_text="Hex color for dark mode"
    )
    
    # Gradient theme
    light_gradient_start = models.CharField(
        max_length=7,
        validators=[RegexValidator(r'^#[0-9A-Fa-f]{6}$', 'Enter a valid hex color')],
        blank=True,
        null=True,
        help_text="Start color for light mode gradient"
    )
    light_gradient_end = models.CharField(
        max_length=7,
        validators=[RegexValidator(r'^#[0-9A-Fa-f]{6}$', 'Enter a valid hex color')],
        blank=True,
        null=True,
        help_text="End color for light mode gradient"
    )
    dark_gradient_start = models.CharField(
        max_length=7,
        validators=[RegexValidator(r'^#[0-9A-Fa-f]{6}$', 'Enter a valid hex color')],
        blank=True,
        null=True,
        help_text="Start color for dark mode gradient"
    )
    dark_gradient_end = models.CharField(
        max_length=7,
        validators=[RegexValidator(r'^#[0-9A-Fa-f]{6}$', 'Enter a valid hex color')],
        blank=True,
        null=True,
        help_text="End color for dark mode gradient"
    )
    gradient_angle = models.IntegerField(
        default=45,
        help_text="Gradient angle in degrees"
    )
    
    # Image theme
    light_image = models.ImageField(
        upload_to='chat_themes/light/%Y/%m/',
        blank=True,
        null=True,
        help_text="Image for light mode",
        validators=[
            RegexValidator(
                r'.*\.(jpg|jpeg|png|gif|webp)$',
                'Only image files (JPG, PNG, GIF, WebP) are allowed'
            )
        ]
    )
    dark_image = models.ImageField(
        upload_to='chat_themes/dark/%Y/%m/',
        blank=True,
        null=True,
        help_text="Image for dark mode",
        validators=[
            RegexValidator(
                r'.*\.(jpg|jpeg|png|gif|webp)$',
                'Only image files (JPG, PNG, GIF, WebP) are allowed'
            )
        ]
    )
    
    # Image optimization fields
    light_image_hash = models.CharField(
        max_length=32,
        blank=True,
        null=True,
        help_text="Hash for image deduplication"
    )
    dark_image_hash = models.CharField(
        max_length=32,
        blank=True,
        null=True,
        help_text="Hash for image deduplication"
    )
    light_image_size = models.PositiveIntegerField(
        blank=True,
        null=True,
        help_text="Optimized image size in bytes"
    )
    dark_image_size = models.PositiveIntegerField(
        blank=True,
        null=True,
        help_text="Optimized image size in bytes"
    )
    image_fit = models.CharField(
        max_length=10,
        choices=[
            ('cover', 'Cover'),
            ('contain', 'Contain'),
            ('repeat', 'Repeat'),
        ],
        default='cover',
        help_text="How to fit the image"
    )
    
    # Overlay settings for readability
    overlay_opacity = models.FloatField(
        default=0.3,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Overlay opacity (0.0-1.0)"
    )
    light_overlay_color = models.CharField(
        max_length=7,
        default='#ffffff',
        validators=[RegexValidator(r'^#[0-9A-Fa-f]{6}$', 'Enter a valid hex color')],
        help_text="Overlay color for light mode"
    )
    dark_overlay_color = models.CharField(
        max_length=7,
        default='#000000',
        validators=[RegexValidator(r'^#[0-9A-Fa-f]{6}$', 'Enter a valid hex color')],
        help_text="Overlay color for dark mode"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ('user', 'conversation')
        indexes = [
            models.Index(fields=['user', 'conversation']),
        ]
    
    def __str__(self):
        return f"{self.user.username}'s theme for conversation {self.conversation.id}"
    
    def get_css_variables(self, theme_mode='light'):
        """Generate CSS variables for this theme."""
        if self.theme_type == 'solid':
            color = self.light_color if theme_mode == 'light' else self.dark_color
            return {
                '--chat-bg': color or '#f8fafc',
                '--chat-bg-type': 'solid',
            }
        elif self.theme_type == 'gradient':
            start = self.light_gradient_start if theme_mode == 'light' else self.dark_gradient_start
            end = self.light_gradient_end if theme_mode == 'light' else self.dark_gradient_end
            return {
                '--chat-bg': f'linear-gradient({self.gradient_angle}deg, {start}, {end})',
                '--chat-bg-type': 'gradient',
            }
        elif self.theme_type == 'image':
            image = self.light_image.url if theme_mode == 'light' and self.light_image else \
                   (self.dark_image.url if theme_mode == 'dark' and self.dark_image else None)
            return {
                '--chat-bg': f'url({image})' if image else 'none',
                '--chat-bg-type': 'image',
                '--chat-bg-fit': self.image_fit,
            }
        
        return {'--chat-bg': '#f8fafc', '--chat-bg-type': 'solid'}
    
    def get_overlay_css(self, theme_mode='light'):
        """Generate overlay CSS variables."""
        overlay_color = self.light_overlay_color if theme_mode == 'light' else self.dark_overlay_color
        return {
            '--chat-overlay-color': overlay_color,
            '--chat-overlay-opacity': str(self.overlay_opacity),
        }
