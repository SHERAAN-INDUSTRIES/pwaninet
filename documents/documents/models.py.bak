import uuid
import os
"""Document domain models.

These models represent academic content and are designed to be lightweight,
containing only metadata. Files, storage, and analytics are handled separately.
"""

from django.db import models
from django.conf import settings
from django.utils.text import slugify
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db.models import Q


class Category(models.Model):
    """Normalized document categories."""
    
    CATEGORY_CHOICES = [
        ('past_paper', 'Past Paper'),
        ('lecture_notes', 'Lecture Notes'),
        ('cat', 'CAT'),
        ('assignment', 'Assignment'),
        ('slides', 'Slides'),
        ('research', 'Research'),
        ('book', 'Book'),
        ('project', 'Project'),
        ('laboratory', 'Laboratory'),
        ('tutorial', 'Tutorial'),
        ('exam', 'Exam'),
        ('syllabus', 'Syllabus'),
        ('ai_generated', 'AI Generated Resource'),
        ('other', 'Other'),
    ]
    
    code = models.CharField(
        max_length=50,
        choices=CATEGORY_CHOICES,
        unique=True,
        help_text="Unique category code"
    )
    name = models.CharField(
        max_length=100,
        help_text="Human-readable category name"
    )
    description = models.TextField(
        blank=True,
        help_text="Description of this category"
    )
    icon = models.CharField(
        max_length=50,
        blank=True,
        help_text="Bootstrap icon class for this category"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this category is active"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['name']
        verbose_name = "Category"
        verbose_name_plural = "Categories"
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        return self.name


class Tag(models.Model):
    """Normalized tags for document categorization."""
    
    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Tag name"
    )
    slug = models.SlugField(
        max_length=120,
        unique=True,
        help_text="URL-friendly slug"
    )
    description = models.TextField(
        blank=True,
        help_text="Description of this tag"
    )
    usage_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of documents using this tag"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-usage_count', 'name']
        verbose_name = "Tag"
        verbose_name_plural = "Tags"
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['usage_count']),
        ]
    
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Document(models.Model):
    share_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    """Core document model representing academic content only.
    
    This model contains only metadata. Files, storage, analytics, and processing
    information are handled by separate models to maintain separation of concerns.
    """
    
    VISIBILITY_CHOICES = [
        ('public', 'Public'),
        ('private', 'Private'),
        ('restricted', 'Restricted'),
    ]
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('processing', 'Processing'),
        ('ready', 'Ready'),
        ('archived', 'Archived'),
    ]
    
    LANGUAGE_CHOICES = [
        ('en', 'English'),
        ('sw', 'Swahili'),
        ('fr', 'French'),
        ('other', 'Other'),
    ]
    
    title = models.CharField(
        max_length=500,
        help_text="Document title"
    )
    slug = models.SlugField(
        max_length=550,
        unique=True,
        help_text="URL-friendly slug"
    )
    description = models.TextField(
        blank=True,
        help_text="Document description"
    )
    
    # Academic metadata
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name='documents',
        help_text="Document category"
    )
    language = models.CharField(
        max_length=10,
        choices=LANGUAGE_CHOICES,
        default='en',
        help_text="Document language"
    )
    
    # Visibility and status
    visibility = models.CharField(
        max_length=20,
        choices=VISIBILITY_CHOICES,
        default='public',
        help_text="Document visibility"
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='draft',
        help_text="Document processing status"
    )
    is_available = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Whether this document is available for viewing/downloading"
    )
    
    # Metadata
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='uploaded_documents',
        help_text="User who uploaded the document"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the document was published"
    )
    is_ai_generated = models.BooleanField(default=False, db_index=True)
    generated_from_message_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    generated_format = models.CharField(max_length=10, blank=True, default="")
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = "Document"
        verbose_name_plural = "Documents"
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['status']),
            models.Index(fields=['is_available']),
            models.Index(fields=['visibility']),
            models.Index(fields=['category']),
            models.Index(fields=['uploaded_by']),
            models.Index(fields=['-created_at']),
            models.Index(fields=['-published_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['uploaded_by', 'generated_from_message_id', 'generated_format'],
                condition=Q(is_ai_generated=True),
                name='unique_generated_resource_per_message_format',
            ),
        ]
    
    def __str__(self):
        return self.title
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)
    
    def archive(self):
        """Soft-delete document by marking as archived."""
        self.status = 'archived'
        self.save(update_fields=['status'])

    def toggle_availability(self):
        """Toggle the availability of the document."""
        self.is_available = not self.is_available
        self.save(update_fields=['is_available'])
        return self.is_available

    @property
    def is_processing(self) -> bool:
        """Check if the document is currently in processing state."""
        return self.status == 'processing'

    @property
    def latest_version(self):
        """Get the latest version of this document."""
        return self.versions.filter(is_latest=True).first()
    
    @property
    def version_count(self):
        """Get the total number of versions."""
        return self.versions.count()

    @property
    def preview_url(self):
        """Get preview URL from latest version's first file."""
        ver = self.latest_version
        if ver:
            first_file = ver.files.first()
            if first_file:
                return first_file.preview_url
        return None

    @property
    def thumbnail_url(self):
        """Get thumbnail URL from latest version's first file."""
        ver = self.latest_version
        if ver:
            first_file = ver.files.first()
            if first_file:
                return first_file.thumbnail_url
        return None


class DocumentVersion(models.Model):
    """Represents a version of a document.
    
    Every modification creates a new version. This supports version history
    and allows users to access previous versions.
    """
    
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name='versions',
        help_text="The document this version belongs to"
    )
    version_number = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text="Sequential version number"
    )
    change_notes = models.TextField(
        blank=True,
        help_text="Notes about changes in this version"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='created_document_versions',
        help_text="User who created this version"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_latest = models.BooleanField(
        default=False,
        help_text="Whether this is the latest version"
    )
    
    class Meta:
        ordering = ['-version_number']
        unique_together = ['document', 'version_number']
        verbose_name = "Document Version"
        verbose_name_plural = "Document Versions"
        indexes = [
            models.Index(fields=['document', 'version_number']),
            models.Index(fields=['is_latest']),
            models.Index(fields=['-created_at']),
        ]
    
    def __str__(self):
        return f"{self.document.title} v{self.version_number}"
    
    def save(self, *args, **kwargs):
        # If this is marked as latest, unmark other versions
        if self.is_latest:
            DocumentVersion.objects.filter(
                document=self.document,
                is_latest=True
            ).exclude(id=self.id).update(is_latest=False)
        super().save(*args, **kwargs)


def document_file_upload_path(instance, filename):
    """Store generated resources under a user-private, opaque storage key."""
    if instance.storage_private:
        document = instance.document_version.document
        return f"private-documents/{document.share_id}/{instance.pk}/{os.path.basename(filename)}"
    from django.utils import timezone
    return f"documents/{timezone.now():%Y/%m/%d}/{filename}"


class DocumentFile(models.Model):
    """Represents a physical file associated with a document version.
    
    Files are independent objects that belong to a document version.
    Storage-specific information is abstracted away from the document model.
    """
    
    PROCESSING_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('uploading', 'Uploading'),
        ('processing', 'Processing'),
        ('ready', 'Ready'),
        ('failed', 'Failed'),
    ]
    
    STORAGE_PROVIDER_CHOICES = [
        ('local', 'Local Storage'),
        ('s3', 'Amazon S3'),
        ('r2', 'Cloudflare R2'),
        ('b2', 'Backblaze B2'),
        ('azure', 'Azure Blob Storage'),
        ('minio', 'MinIO'),
    ]
    
    document_version = models.ForeignKey(
        DocumentVersion,
        on_delete=models.CASCADE,
        related_name='files',
        help_text="The document version this file belongs to"
    )
    
    # Actual file storage
    file = models.FileField(
        upload_to=document_file_upload_path,
        help_text="The actual file"
    )
    storage_private = models.BooleanField(default=False, db_index=True)
    
    # File metadata
    original_filename = models.CharField(
        max_length=500,
        help_text="Original filename when uploaded"
    )
    storage_path = models.CharField(
        max_length=1000,
        help_text="Storage path/identifier for the file"
    )
    mime_type = models.CharField(
        max_length=100,
        help_text="MIME type of the file"
    )
    extension = models.CharField(
        max_length=20,
        help_text="File extension (e.g., pdf, docx)"
    )
    size_bytes = models.BigIntegerField(
        help_text="File size in bytes"
    )
    checksum = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        help_text="SHA-256 checksum for duplicate detection"
    )
    
    # Storage abstraction
    storage_provider = models.CharField(
        max_length=20,
        choices=STORAGE_PROVIDER_CHOICES,
        default='local',
        help_text="Storage backend used"
    )
    
    # Processing
    thumbnail_path = models.CharField(
        max_length=1000,
        blank=True,
        help_text="Path to generated thumbnail"
    )
    preview_path = models.CharField(
        max_length=1000,
        blank=True,
        help_text="Path to generated preview"
    )
    page_count = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Number of pages in the document"
    )
    processing_status = models.CharField(
        max_length=20,
        choices=PROCESSING_STATUS_CHOICES,
        default='pending',
        help_text="Processing status"
    )
    processing_error = models.TextField(
        blank=True,
        help_text="Error message if processing failed"
    )
    
    # Metadata
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='uploaded_files',
        help_text="User who uploaded this file"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When processing completed"
    )
    
    class Meta:
        ordering = ['-uploaded_at']
        verbose_name = "Document File"
        verbose_name_plural = "Document Files"
        indexes = [
            models.Index(fields=['document_version']),
            models.Index(fields=['checksum']),
            models.Index(fields=['processing_status']),
            models.Index(fields=['storage_provider']),
            models.Index(fields=['-uploaded_at']),
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.storage_private:
            from documents.private_storage import get_private_resource_storage
            self.file.storage = get_private_resource_storage()
    
    def __str__(self):
        return self.original_filename
    
    @property
    def size_mb(self):
        """Return file size in megabytes."""
        return round(self.size_bytes / (1024 * 1024), 2)

    @property
    def preview_url(self):
        """Return public URL for the preview image, supporting both local and remote storage."""
        if self.preview_path:
            try:
                if self.storage_private:
                    from django.urls import reverse
                    return reverse('documents:private_resource_asset', kwargs={'file_id': self.pk, 'asset': 'preview'})
                from django.core.files.storage import default_storage
                return default_storage.url(self.preview_path)
            except Exception:
                if self.storage_private:
                    return None
                from django.conf import settings
                media_url = getattr(settings, 'MEDIA_URL', '/media/')
                return f"{media_url.rstrip('/')}/{self.preview_path.lstrip('/')}"
        if self.thumbnail_path:
            return self.thumbnail_url
        return None

    @property
    def thumbnail_url(self):
        """Return public URL for the thumbnail image, supporting both local and remote storage."""
        if self.thumbnail_path:
            try:
                if self.storage_private:
                    from django.urls import reverse
                    return reverse('documents:private_resource_asset', kwargs={'file_id': self.pk, 'asset': 'thumbnail'})
                from django.core.files.storage import default_storage
                return default_storage.url(self.thumbnail_path)
            except Exception:
                if self.storage_private:
                    return None
                from django.conf import settings
                media_url = getattr(settings, 'MEDIA_URL', '/media/')
                return f"{media_url.rstrip('/')}/{self.thumbnail_path.lstrip('/')}"
        if self.preview_path:
            return self.preview_url
        return None


class DocumentAuthor(models.Model):
    """Represents authors/contributors of a document.
    
    Uploader and author are different concepts. A document can have multiple authors.
    """
    
    AUTHOR_TYPE_CHOICES = [
        ('original', 'Original Author'),
        ('uploader', 'Uploader'),
        ('contributor', 'Contributor'),
        ('editor', 'Editor'),
    ]
    
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name='authors',
        help_text="The document this author contributed to"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='authored_documents',
        null=True,
        blank=True,
        help_text="User author (if registered)"
    )
    name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Author name (if not a registered user)"
    )
    author_type = models.CharField(
        max_length=20,
        choices=AUTHOR_TYPE_CHOICES,
        default='uploader',
        help_text="Type of authorship"
    )
    contribution_notes = models.TextField(
        blank=True,
        help_text="Notes about the contribution"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['author_type', 'created_at']
        verbose_name = "Document Author"
        verbose_name_plural = "Document Authors"
        indexes = [
            models.Index(fields=['document']),
            models.Index(fields=['user']),
            models.Index(fields=['author_type']),
        ]
    
    def __str__(self):
        if self.user:
            return f"{self.user.get_full_name()} ({self.get_author_type_display()})"
        return f"{self.name} ({self.get_author_type_display()})"


class DocumentAcademicUnit(models.Model):
    """Junction table mapping documents to academic units.
    
    A document can be relevant to multiple academic units (e.g., shared units).
    This prevents duplicate uploads for shared units and includes full academic context.
    """
    
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name='academic_units'
    )
    academic_unit = models.ForeignKey(
        'documents.AcademicUnit',
        on_delete=models.CASCADE,
        related_name='documents'
    )
    academic_level = models.ForeignKey(
        'documents.AcademicLevel',
        on_delete=models.PROTECT,
        related_name='documents',
        null=True,
        blank=True,
        help_text="The academic level this document is relevant to"
    )
    semester = models.ForeignKey(
        'documents.Semester',
        on_delete=models.PROTECT,
        related_name='documents',
        null=True,
        blank=True,
        help_text="The semester this document is relevant to"
    )
    academic_year = models.ForeignKey(
        'documents.AcademicYear',
        on_delete=models.PROTECT,
        related_name='documents',
        null=True,
        blank=True,
        help_text="The academic year this document is relevant to"
    )
    is_primary = models.BooleanField(
        default=True,
        help_text="Whether this is the primary academic unit for the document"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['document', 'academic_unit', 'academic_level', 'semester', 'academic_year']
        verbose_name = "Document Academic Unit"
        verbose_name_plural = "Document Academic Units"
        indexes = [
            models.Index(fields=['document']),
            models.Index(fields=['academic_unit']),
            models.Index(fields=['academic_level']),
            models.Index(fields=['semester']),
            models.Index(fields=['academic_year']),
            models.Index(fields=['academic_unit', 'academic_level', 'academic_year', 'semester']),
        ]
    
    def __str__(self):
        return f"{self.document.title} - {self.academic_unit.code}"


class DocumentTag(models.Model):
    """Junction table mapping documents to tags."""
    
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name='document_tags'
    )
    tag = models.ForeignKey(
        Tag,
        on_delete=models.CASCADE,
        related_name='document_tags'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['document', 'tag']
        verbose_name = "Document Tag"
        verbose_name_plural = "Document Tags"
        indexes = [
            models.Index(fields=['document']),
            models.Index(fields=['tag']),
        ]
    
    def __str__(self):
        return f"{self.document.title} - {self.tag.name}"
    
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Increment tag usage count
        self.tag.usage_count = self.tag.document_tags.count()
        self.tag.save()
    
    def delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)
        # Decrement tag usage count
        self.tag.usage_count = self.tag.document_tags.count()
        self.tag.save()
