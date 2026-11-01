from rest_framework import serializers

from .models import UploadEvent, UploadRecord

# Document uploads are validated server-side before a single byte moves:
# the browser may lie about type or size, and R2 keeps whatever it is given.
DOCUMENT_ALLOWED_EXTENSIONS = ("pdf", "docx", "doc", "pptx", "ppt", "zip", "rar")
DOCUMENT_MAX_BYTES = 50 * 1024 * 1024
TIMETABLE_ALLOWED_EXTENSIONS = ("jpg", "jpeg", "png", "webp")
TIMETABLE_MAX_BYTES = 15 * 1024 * 1024


class CompleteTimetableSerializer(serializers.Serializer):
    """Metadata for a timetable image upload."""

    programme = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    academic_level = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    semester = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    change_summary = serializers.CharField(max_length=1000, required=False, allow_blank=True, default="")


class CompleteDocumentSerializer(serializers.Serializer):
    """Metadata for a document upload. Sent when the session is created (so
    bad input is rejected before any bytes move) and again at completion
    (authoritative for what gets published)."""

    # 'new' creates a Document; 'version' adds a version to an existing one.
    target = serializers.ChoiceField(choices=["new", "version"], default="new")
    # Required for target='version': the share_id of the document to extend.
    document_share_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    title = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    description = serializers.CharField(required=False, allow_blank=True, default="")
    change_notes = serializers.CharField(max_length=1000, required=False, allow_blank=True, default="")
    category = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    academic_unit = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    academic_level = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    academic_year = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    semester = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    tags = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False, allow_empty=True, default=list,
    )


class UploadCreateSerializer(serializers.Serializer):
    client_upload_id = serializers.UUIDField()
    post_group = serializers.UUIDField(required=False, allow_null=True, default=None)
    filename = serializers.CharField(max_length=255)
    mime_type = serializers.CharField(max_length=127, required=False, allow_blank=True, default="")
    file_size = serializers.IntegerField(min_value=1)
    sha256 = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    # What the bytes become once the transfer completes.
    kind = serializers.ChoiceField(
        choices=["post", "document", "timetable"], default="post",
    )
    # Document metadata (only used when kind='document').
    document = CompleteDocumentSerializer(required=False, default=dict)
    # Timetable metadata (only used when kind='timetable').
    timetable = CompleteTimetableSerializer(required=False, default=dict)

    # Server-side size caps by media class (mirrors posts/validators.py).
    SIZE_CAPS = (
        ("image/", 5 * 1024 * 1024),
        ("video/", 150 * 1024 * 1024),
        ("audio/", 20 * 1024 * 1024),
    )
    DEFAULT_CAP = 50 * 1024 * 1024

    def validate(self, data):
        kind = data.get("kind")
        if kind == "document":
            self._validate_document(data)
            return data
        if kind == "timetable":
            self._validate_timetable(data)
            return data

        mime = (data.get("mime_type") or "").lower()
        cap = self.DEFAULT_CAP
        for prefix, limit in self.SIZE_CAPS:
            if mime.startswith(prefix):
                cap = limit
                break
        if data["file_size"] > cap:
            raise serializers.ValidationError(
                {"file_size": f"File too large for {mime or 'unknown type'}. Maximum is {cap // (1024 * 1024)}MB."}
            )
        return data

    def _validate_timetable(self, data):
        filename = data.get("filename") or ""
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if extension not in TIMETABLE_ALLOWED_EXTENSIONS:
            raise serializers.ValidationError({
                "filename": (
                    f"File type '{extension or 'unknown'}' is not allowed. "
                    f"Supported formats: {', '.join(e.upper() for e in TIMETABLE_ALLOWED_EXTENSIONS)}"
                ),
            })
        if data["file_size"] > TIMETABLE_MAX_BYTES:
            raise serializers.ValidationError({
                "file_size": (
                    f"File size exceeds maximum of "
                    f"{TIMETABLE_MAX_BYTES // (1024 * 1024)}MB"
                ),
            })

    def _validate_document(self, data):
        """Reject unusable document uploads before the client spends bandwidth."""
        filename = data.get("filename") or ""
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if extension not in DOCUMENT_ALLOWED_EXTENSIONS:
            raise serializers.ValidationError({
                "filename": (
                    f"File type '{extension or 'unknown'}' is not allowed. "
                    f"Supported formats: {', '.join(e.upper() for e in DOCUMENT_ALLOWED_EXTENSIONS)}"
                ),
            })
        if data["file_size"] > DOCUMENT_MAX_BYTES:
            raise serializers.ValidationError({
                "file_size": (
                    f"File size exceeds maximum of "
                    f"{DOCUMENT_MAX_BYTES // (1024 * 1024)}MB"
                ),
            })

        metadata = data.get("document") or {}
        if metadata.get("target") == "version" and not metadata.get("document_share_id"):
            raise serializers.ValidationError({
                "document": "document_share_id is required when target='version'.",
            })


class AuthorizeSerializer(serializers.Serializer):
    parts = serializers.ListField(
        child=serializers.IntegerField(min_value=1, max_value=10000),
        required=False,
        allow_empty=True,
    )


class CompletePartSerializer(serializers.Serializer):
    part_number = serializers.IntegerField(min_value=1, max_value=10000)
    etag = serializers.CharField(max_length=64)


class CompletePostSerializer(serializers.Serializer):
    content = serializers.CharField(required=False, allow_blank=True, default="")
    visibility = serializers.ChoiceField(
        choices=["PUBLIC", "AUTHENTICATED", "FOLLOWERS", "PRIVATE"],
        default="AUTHENTICATED",
    )
    group = serializers.IntegerField(required=False, allow_null=True, default=None)
    unit = serializers.IntegerField(required=False, allow_null=True, default=None)
    gradient_class = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    allow_downloads = serializers.BooleanField(required=False, default=True)
    # Provisional client-measured video dims (ffprobe truth overwrites on ready).
    # Lets post_card is_reel + carousel classify at publish, not just post-transcode.
    video_width = serializers.IntegerField(required=False, allow_null=True, default=None, min_value=0)
    video_height = serializers.IntegerField(required=False, allow_null=True, default=None, min_value=0)
    video_duration = serializers.IntegerField(required=False, allow_null=True, default=None, min_value=0)


class CompleteSerializer(serializers.Serializer):
    parts = CompletePartSerializer(many=True, required=False, default=list)
    post = CompletePostSerializer(required=False, default=dict)
    document = CompleteDocumentSerializer(required=False, default=dict)
    timetable = CompleteTimetableSerializer(required=False, default=dict)


class UploadRecordSerializer(serializers.ModelSerializer):
    verified_parts = serializers.SerializerMethodField()
    session_part_size = serializers.SerializerMethodField()
    session_total_parts = serializers.SerializerMethodField()
    session_protocol = serializers.SerializerMethodField()
    queue_position = serializers.SerializerMethodField()
    post_share_id = serializers.SerializerMethodField()
    document_share_id = serializers.SerializerMethodField()

    class Meta:
        model = UploadRecord
        fields = (
            "id", "client_upload_id", "filename", "mime_type", "file_size",
            "sha256", "status", "progress_bytes", "total_bytes",
            "error_code", "error_message", "retry_count", "post", "kind",
            "verified_parts", "session_part_size", "session_total_parts",
            "session_protocol", "created_at", "updated_at", "completed_at",
            "queue_position", "post_share_id", "document_share_id",
        )
        read_only_fields = fields

    def get_verified_parts(self, obj):
        try:
            return obj.session.verified_parts
        except Exception:
            return []

    def _session(self, obj):
        try:
            return obj.session
        except Exception:
            return None

    def get_session_part_size(self, obj):
        session = self._session(obj)
        return session.part_size if session else None

    def get_session_total_parts(self, obj):
        session = self._session(obj)
        return session.total_parts if session else None

    def get_session_protocol(self, obj):
        session = self._session(obj)
        return session.protocol if session else None

    def get_queue_position(self, obj):
        """Calculate queue position for queued/preparing uploads."""
        if obj.status not in ('queued', 'preparing'):
            return None
        # Count uploads ahead of this one (created before this one)
        position = UploadRecord.objects.filter(
            user=obj.user,
            status__in=('queued', 'preparing'),
            created_at__lt=obj.created_at
        ).count() + 1
        return position

    def get_post_share_id(self, obj):
        """Get the share_id of the linked post if it exists."""
        if obj.post:
            return getattr(obj.post, 'share_id', None)
        return None

    def get_document_share_id(self, obj):
        """Get the share_id of the linked document if it exists."""
        if obj.document:
            return getattr(obj.document, 'share_id', None)
        return None


class UploadEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = UploadEvent
        fields = ("event_type", "detail", "created_at")
        read_only_fields = fields
