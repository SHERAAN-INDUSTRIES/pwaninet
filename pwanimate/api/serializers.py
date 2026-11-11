"""
Pwanimate API Serializers.
"""

from rest_framework import serializers
from pwanimate.models import PwanimateConversation, PwanimateMessage, PwanimateAttachment


class PwanimateAttachmentSerializer(serializers.ModelSerializer):
    """Serializer for Pwanimate attachments with secure view/download URL."""

    url = serializers.SerializerMethodField()

    class Meta:
        model = PwanimateAttachment
        fields = [
            "id",
            "file_name",
            "file_size",
            "mime_type",
            "attachment_type",
            "processing_status",
            "processing_error",
            "url",
            "created_at",
        ]
        read_only_fields = fields

    def get_url(self, obj) -> str:
        return f"/api/pwanimate/attachments/{obj.id}/view/"


class MessageSerializer(serializers.ModelSerializer):
    """Serializer for individual conversation messages."""

    people = serializers.ReadOnlyField()
    attachments = PwanimateAttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = PwanimateMessage
        fields = [
            "id",
            "role",
            "content",
            "activity",
            "citations",
            "sources",
            "people",
            "attachments",
            "created_at",
        ]
        read_only_fields = fields


class ConversationListSerializer(serializers.ModelSerializer):
    """Serializer for listing user conversations."""

    study_session = serializers.SerializerMethodField()

    class Meta:
        model = PwanimateConversation
        fields = [
            "id",
            "title",
            "created_at",
            "updated_at",
            "study_session",
        ]
        read_only_fields = fields

    def get_study_session(self, obj):
        try:
            session = obj.study_session
        except Exception:
            session = None
        if not session:
            return None
        from pwanimate.services.study_session import StudySessionService
        return StudySessionService.serialize_session(session)


class ConversationDetailSerializer(serializers.ModelSerializer):
    """Serializer for full conversation thread with messages."""

    messages = MessageSerializer(many=True, read_only=True)
    study_session = serializers.SerializerMethodField()

    class Meta:
        model = PwanimateConversation
        fields = [
            "id",
            "title",
            "created_at",
            "updated_at",
            "messages",
            "study_session",
        ]
        read_only_fields = fields

    def get_study_session(self, obj):
        try:
            session = obj.study_session
        except Exception:
            session = None
        if not session:
            return None
        from pwanimate.services.study_session import StudySessionService
        return StudySessionService.serialize_session(session)
