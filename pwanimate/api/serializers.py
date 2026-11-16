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

    teaching = serializers.SerializerMethodField()
    activity = serializers.SerializerMethodField()
    follow_ups = serializers.SerializerMethodField()
    academic_actions = serializers.SerializerMethodField()

    def get_teaching(self, obj):
        from pwanimate.services.teaching_output import persisted_teaching
        return persisted_teaching(obj)

    def get_activity(self, obj):
        from pwanimate.orchestrator.activity import public_events
        return public_events(obj.activity)

    def get_follow_ups(self, obj):
        """Return active follow-up suggestions for this message."""
        if obj.role != 'assistant':
            return []
        from pwanimate.models import SparkFollowUp
        from django.utils import timezone
        now = timezone.now()
        follow_ups = obj.follow_ups.filter(
            status__in=['pending', 'executing', 'failed', 'interrupted'],
            expires_at__gt=now
        ).order_by('created_at')
        return [fu.serialize() for fu in follow_ups]

    def get_academic_actions(self, obj):
        """Pending academic-tracking consent proposals attached to this reply."""
        if obj.role != 'assistant':
            return []
        return self.context.get('academic_actions_map', {}).get(obj.id, [])

    people = serializers.ReadOnlyField()
    attachments = PwanimateAttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = PwanimateMessage
        fields = [
            "id",
            "role",
            "content",
            "activity",
            "teaching",
            "citations",
            "sources",
            "people",
            "attachments",
            "follow_ups",
            "academic_actions",
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
            from pwanimate.services.study_session import StudySessionService
            session = StudySessionService.get_session_for_conversation(obj.user,obj)
        except Exception:
            session = None
        if not session:
            return None
        from pwanimate.services.study_session import StudySessionService
        return StudySessionService.serialize_session(session)


class ConversationDetailSerializer(serializers.ModelSerializer):
    """Serializer for full conversation thread with messages."""

    messages = serializers.SerializerMethodField()

    def get_messages(self, obj):
        from pwanimate.models import PendingAction
        from django.utils import timezone
        from pwanimate.services.actions import ActionService

        messages = list(obj.messages.exclude(task__in=['agent_interim','academic_execution','academic_authorization']).order_by('created_at'))
        # Pending track-unit proposals attach to the assistant reply that
        # answered their source user turn, mirroring the live chat response.
        academic_map = {}
        pending = list(PendingAction.objects.filter(
            user=obj.user, conversation=obj,
            action_type=PendingAction.ACTION_TRACK_UNIT,
            status=PendingAction.STATUS_PENDING,
            expires_at__gt=timezone.now(),
        ).order_by('created_at'))
        if pending:
            positions = {m.id: i for i, m in enumerate(messages)}
            for action in pending:
                idx = positions.get((action.params or {}).get('source_message_id'))
                if idx is None:
                    continue
                for msg in messages[idx + 1:]:
                    if msg.role == 'assistant':
                        academic_map.setdefault(msg.id, []).append(ActionService.serialize(action))
                        break
        context = dict(self.context)
        context['academic_actions_map'] = academic_map
        return MessageSerializer(messages, many=True, context=context).data
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
            from pwanimate.services.study_session import StudySessionService
            session = StudySessionService.get_session_for_conversation(obj.user,obj)
        except Exception:
            session = None
        if not session:
            return None
        from pwanimate.services.study_session import StudySessionService
        return StudySessionService.serialize_session(session)
