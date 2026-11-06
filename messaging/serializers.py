from rest_framework import serializers
from django.conf import settings
from django.core.validators import FileExtensionValidator
import os
from .models import Conversation, ConversationMember, Message, MessageAttachment, MessageReaction, ConversationTheme, LinkPreview
from users.models import User
from users.serializers import UserSerializer


class ChatUserSerializer(serializers.ModelSerializer):
    """Lightweight user serializer for chat messages, reactions, and members (zero N+1 queries)."""
    profile_pic_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'username', 'first_name', 'second_name', 'last_name',
            'profile_pic', 'profile_pic_url'
        ]
        read_only_fields = fields

    def get_profile_pic_url(self, obj):
        if obj.profile_pic and hasattr(obj.profile_pic, 'url'):
            try:
                return obj.profile_pic.url
            except Exception:
                pass
        return f"{settings.STATIC_URL}images/default_pic1.jpg"


class LinkPreviewSerializer(serializers.ModelSerializer):
    """Serializer for link previews."""
    image_url = serializers.SerializerMethodField()
    favicon_url = serializers.SerializerMethodField()
    has_thumbnail = serializers.SerializerMethodField()
    has_favicon = serializers.SerializerMethodField()

    class Meta:
        model = LinkPreview
        fields = [
            'id', 'url', 'title', 'description', 'site_name', 'domain',
            'image_url', 'favicon_url', 'has_thumbnail', 'has_favicon',
            'cached_at', 'created_at', 'fetch_failed'
        ]
        read_only_fields = ['id', 'cached_at', 'created_at', 'fetch_failed']

    def get_image_url(self, obj):
        """Get the URL of the cached image."""
        if obj.image:
            return obj.image.url
        return None

    def get_favicon_url(self, obj):
        """Get the URL of the cached favicon."""
        if obj.favicon:
            return obj.favicon.url
        return None

    def get_has_thumbnail(self, obj):
        """Check if preview has a thumbnail."""
        return obj.has_thumbnail()

    def get_has_favicon(self, obj):
        """Check if preview has a favicon."""
        return obj.has_favicon()


class MessageReactionSerializer(serializers.ModelSerializer):
    """Serializer for message reactions."""
    user = ChatUserSerializer(read_only=True)

    class Meta:
        model = MessageReaction
        fields = ['id', 'message', 'user', 'emoji', 'created_at']
        read_only_fields = ['id', 'created_at']


class MessageAttachmentSerializer(serializers.ModelSerializer):
    """Serializer for message attachments."""
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = MessageAttachment
        fields = [
            'id', 'file', 'file_url', 'file_type', 'caption', 'order',
            'size', 'width', 'height', 'duration', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']

    def get_file_url(self, obj):
        """Get the URL of the attachment file."""
        if obj.file:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.file.url)
            return obj.file.url
        return None


class MessageSerializer(serializers.ModelSerializer):
    """Serializer for messages."""
    sender = ChatUserSerializer(read_only=True)
    reactions = MessageReactionSerializer(many=True, read_only=True)
    reply_to_details = serializers.SerializerMethodField()
    attachment_url = serializers.SerializerMethodField()
    read_status = serializers.SerializerMethodField()
    attachments = MessageAttachmentSerializer(many=True, read_only=True)
    link_preview = LinkPreviewSerializer(read_only=True)
    is_voice_note = serializers.SerializerMethodField()
    file_size = serializers.SerializerMethodField()
    file_name = serializers.SerializerMethodField()
    is_forwarded = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            'id', 'conversation', 'sender', 'content', 'encrypted_content', 'is_encrypted',
            'attachment', 'attachment_type', 'reply_to', 'reactions',
            'reply_to_details', 'attachment_url', 'read_status', 'status', 'created_at', 'edited_at', 'is_deleted',
            'link_url', 'link_title', 'link_description', 'link_image', 'link_type',
            'global_caption', 'message_type', 'attachments', 'link_preview',
            'is_voice_note', 'file_size', 'file_name', 'is_forwarded'
        ]
        read_only_fields = ['id', 'created_at', 'edited_at', 'is_encrypted', 'status']

    def get_is_forwarded(self, obj):
        """Determine if this message was forwarded."""
        return bool(getattr(obj, 'is_forwarded', False) or getattr(obj, '_is_forwarded', False))

    def get_reply_to_details(self, obj):
        """Get lightweight details of the message being replied to without recursive N+1 queries."""
        if not obj.reply_to_id:
            return None
        reply = getattr(obj, 'reply_to', None)
        if not reply:
            return None
        sender = getattr(reply, 'sender', None)
        attachment_url = None
        if reply.attachment:
            try:
                attachment_url = reply.attachment.url
            except Exception:
                pass
        elif getattr(reply, 'attachment_type', None) in ('sticker', 'gif') and reply.link_image:
            attachment_url = reply.link_image
        return {
            'id': reply.id,
            'conversation': reply.conversation_id,
            'sender': ChatUserSerializer(sender).data if sender else {'id': reply.sender_id, 'username': ''},
            'content': 'This message was deleted' if reply.is_deleted else (reply.content or ''),
            'attachment_type': reply.attachment_type,
            'attachment_url': attachment_url,
            'message_type': reply.message_type,
            'is_deleted': reply.is_deleted,
            'created_at': reply.created_at.isoformat() if reply.created_at else None,
        }

    def get_attachment_url(self, obj):
        """Get the URL of the attachment (legacy single attachment, stickers, and GIFs)."""
        if obj.attachment:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.attachment.url)
            return obj.attachment.url
        if (getattr(obj, 'attachment_type', None) in ('sticker', 'gif') or
            getattr(obj, 'message_type', None) in ('sticker', 'gif')) and obj.link_image:
            return obj.link_image
        return None

    def get_is_voice_note(self, obj):
        """Determine if this message is a recorded voice note."""
        if getattr(obj, 'attachment_type', None) == 'voice_note':
            return True
        if getattr(obj, 'message_type', None) == 'voice_note':
            return True
        if obj.attachment:
            name = str(obj.attachment.name).lower()
            if 'voice_' in name or 'voice_note' in name or 'voice_message' in name:
                return True
        return False

    def _get_prefetched_attachments(self, obj):
        """Return attachments from prefetch cache if available, avoiding extra DB queries."""
        # Never truncate: group bubbles need the full attachment list to
        # choose the group layout. A [:1] fallback made groups render as
        # singles depending on which endpoint serialized them.
        return list(obj.attachments.all())

    def get_file_size(self, obj):
        """Get byte size of attachment without extra DB queries or unnecessary disk stat calls."""
        atts = self._get_prefetched_attachments(obj)
        if atts and atts[0].size:
            return atts[0].size
        if obj.attachment:
            try:
                return obj.attachment.size
            except Exception:
                pass
        return 0

    def get_file_name(self, obj):
        """Get display file name of attachment without extra DB queries."""
        if obj.attachment and getattr(obj.attachment, 'name', None):
            return os.path.basename(obj.attachment.name)
        atts = self._get_prefetched_attachments(obj)
        if atts and atts[0].file and getattr(atts[0].file, 'name', None):
            return os.path.basename(atts[0].file.name)
        return None

    def get_read_status(self, obj):
        """Get the read status of the message using prefetched ConversationMember.last_read_message_id."""
        request = self.context.get('request')

        # If there's no request user, fall back to message.status or sent
        if not request or not request.user.is_authenticated:
            return obj.status or 'sent'

        conv = obj.conversation
        if 'members' in getattr(conv, '_prefetched_objects_cache', {}):
            members = list(conv.members.all())
        else:
            # Cache members on conversation instance to avoid per-message queries
            if not hasattr(conv, '_cached_members_list'):
                conv._cached_members_list = list(
                    conv.members.only('id', 'user_id', 'last_read_message_id')
                )
            members = conv._cached_members_list

        # If the current user sent this message, check if others have read it
        if obj.sender_id == request.user.id:
            for member in members:
                if member.user_id != request.user.id and member.last_read_message_id and member.last_read_message_id >= obj.id:
                    return 'read'
            # Preserve delivered so history reload doesn't downgrade ticks
            if getattr(obj, 'status', None) == 'delivered':
                return 'delivered'
            return 'sent'

        # If the current user received this message, check if they've read it
        for member in members:
            if member.user_id == request.user.id and member.last_read_message_id and member.last_read_message_id >= obj.id:
                return 'read'

        return 'delivered'


class FileUploadValidator:
    """Validator for file uploads with size, MIME type, and extension checks."""
    
    FILE_SIZE_LIMITS = {
        'image': 10 * 1024 * 1024,      # 10MB for images
        'video': 100 * 1024 * 1024,     # 100MB for videos
        'audio': 25 * 1024 * 1024,      # 25MB for audio
        'document': 50 * 1024 * 1024,    # 50MB for documents
    }
    
    ALLOWED_TYPES = {
        'image': ['image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/svg+xml'],
        'video': ['video/mp4', 'video/webm', 'video/quicktime', 'video/x-matroska',
                  'video/ogg', 'video/3gpp', 'video/3gpp2', 'video/x-m4v'],
        'audio': [
            'audio/mpeg', 'audio/mp3', 'audio/wav', 'audio/x-wav', 'audio/vnd.wave',
            'audio/webm', 'audio/ogg', 'application/ogg', 'audio/aac',
            'audio/mp4', 'audio/x-m4a', 'audio/opus', 'audio/3gpp', 'audio/flac',
            'audio/x-matroska'
        ],
        'document': [
            'application/pdf', 'text/plain', 'text/csv', 'text/markdown',
            'application/msword', 
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.ms-excel',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/vnd.ms-powerpoint',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            'application/zip', 'application/x-zip-compressed',
            'application/x-rar-compressed', 'application/x-tar', 'application/gzip',
            'application/json'
        ]
    }
    
    MIME_EXT_MAP = {
        'image/jpeg': ['.jpg', '.jpeg'],
        'image/png': ['.png'],
        'image/gif': ['.gif'],
        'image/webp': ['.webp'],
        'image/svg+xml': ['.svg'],
        'video/mp4': ['.mp4', '.m4v'],
        'video/webm': ['.webm'],
        'video/quicktime': ['.mov'],
        'video/x-matroska': ['.mkv'],
        'video/ogg': ['.ogv', '.ogg'],
        'video/3gpp': ['.3gp'],
        'video/3gpp2': ['.3g2'],
        'video/x-m4v': ['.m4v'],
        'audio/mpeg': ['.mp3'],
        'audio/mp3': ['.mp3'],
        'audio/wav': ['.wav'],
        'audio/x-wav': ['.wav'],
        'audio/vnd.wave': ['.wav'],
        'audio/webm': ['.webm', '.weba'],
        'audio/ogg': ['.ogg', '.opus', '.oga'],
        'application/ogg': ['.ogg', '.opus', '.oga'],
        'audio/opus': ['.opus', '.ogg'],
        'audio/aac': ['.aac'],
        'audio/mp4': ['.m4a', '.mp4'],
        'audio/x-m4a': ['.m4a'],
        'audio/3gpp': ['.3gp'],
        'audio/flac': ['.flac'],
        'application/pdf': ['.pdf'],
        'text/plain': ['.txt'],
        'text/csv': ['.csv'],
        'text/markdown': ['.md'],
        'application/msword': ['.doc'],
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
        'application/vnd.ms-excel': ['.xls'],
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'],
        'application/vnd.ms-powerpoint': ['.ppt'],
        'application/vnd.openxmlformats-officedocument.presentationml.presentation': ['.pptx'],
        'application/zip': ['.zip'],
        'application/x-zip-compressed': ['.zip'],
        'application/json': ['.json'],
    }
    
    @classmethod
    def validate(cls, file):
        """Validate a file upload. Returns (is_valid, error_code, error_message, attachment_type)."""
        if not file:
            return False, 'NO_FILE', 'No file provided', None
        
        raw_content_type = (getattr(file, 'content_type', '') or '').lower().strip()
        # Clean parameter parts like ;codecs=opus or charset=utf-8
        content_type = raw_content_type.split(';')[0].strip()

        file_name = getattr(file, 'name', '') or ''
        file_ext = os.path.splitext(file_name)[1].lower() if file_name else ''

        # If MIME type is missing or generic octet-stream, try to guess from file name
        if (not content_type or content_type == 'application/octet-stream') and file_name:
            import mimetypes
            guessed_type, _ = mimetypes.guess_type(file_name)
            if guessed_type:
                content_type = guessed_type.lower().split(';')[0].strip()

        # Determine attachment type and validate MIME type
        attachment_type = None
        max_size = None
        
        if content_type.startswith('image/'):
            if content_type not in cls.ALLOWED_TYPES['image']:
                return False, 'INVALID_IMAGE_TYPE', f'Image type {content_type} is not allowed. Allowed types: JPEG, PNG, GIF, WebP, SVG', None
            attachment_type = 'image'
            max_size = cls.FILE_SIZE_LIMITS['image']
        elif content_type.startswith('video/'):
            if content_type not in cls.ALLOWED_TYPES['video']:
                return False, 'INVALID_VIDEO_TYPE', f'Video type {content_type} is not allowed. Allowed types: MP4, WebM, QuickTime', None
            attachment_type = 'video'
            max_size = cls.FILE_SIZE_LIMITS['video']
        elif content_type.startswith('audio/') or content_type == 'application/ogg':
            if content_type not in cls.ALLOWED_TYPES['audio']:
                return False, 'INVALID_AUDIO_TYPE', f'Audio type {content_type} is not allowed. Allowed types: MP3, WAV, WebM, OGG, AAC', None
            attachment_type = 'audio'
            max_size = cls.FILE_SIZE_LIMITS['audio']
        elif content_type in cls.ALLOWED_TYPES['document'] or file_ext in [
            '.pdf', '.txt', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
            '.zip', '.csv', '.json', '.md'
        ]:
            attachment_type = 'document'
            max_size = cls.FILE_SIZE_LIMITS['document']
        else:
            return False, 'INVALID_FILE_TYPE', f'File type {content_type} is not allowed', None
        
        # Validate file size
        if file.size > max_size:
            size_mb = max_size / (1024 * 1024)
            return False, 'FILE_TOO_LARGE', f'File size exceeds {size_mb:.0f}MB limit for {attachment_type}s', attachment_type
        
        # Validate file extension matches MIME type (only when extension is provided)
        if file_ext and content_type in cls.MIME_EXT_MAP:
            if file_ext not in cls.MIME_EXT_MAP[content_type]:
                return False, 'EXTENSION_MISMATCH', f'File extension {file_ext} does not match MIME type {content_type}', attachment_type
        
        return True, None, None, attachment_type


class MessageCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating messages."""
    def validate(self, attrs):
        from groups.services.chat_service import can_access_conversation
        conversation = attrs['conversation']
        if not can_access_conversation(conversation, self.context['request'].user):
            raise serializers.ValidationError('Conversation unavailable.')
        reply = attrs.get('reply_to')
        if reply and reply.conversation_id != conversation.pk:
            raise serializers.ValidationError('Reply must belong to this conversation.')
        return attrs

    class Meta:
        model = Message
        fields = ['id', 'status', 'created_at', 'conversation', 'content', 'encrypted_content', 'is_encrypted', 'reply_to', 'attachment', 'attachment_type',
                  'link_url', 'link_title', 'link_description', 'link_image', 'link_type',
                  'global_caption', 'message_type']
        read_only_fields = ['id', 'status', 'created_at']


class MessageAttachmentCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating message attachments."""
    class Meta:
        model = MessageAttachment
        fields = ['file', 'file_type', 'caption', 'order', 'size', 'width', 'height', 'duration']


class MessageUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating messages."""
    class Meta:
        model = Message
        fields = ['content']
        read_only_fields = ['id', 'created_at', 'sender', 'conversation']


class ConversationMemberSerializer(serializers.ModelSerializer):
    """Serializer for conversation members."""
    user = ChatUserSerializer(read_only=True)

    class Meta:
        model = ConversationMember
        fields = ['id', 'conversation', 'user', 'joined_at', 'last_read_message', 'is_muted', 'public_key']
        read_only_fields = ['id', 'joined_at']


class ConversationSerializer(serializers.ModelSerializer):
    """Serializer for conversations."""
    members = ConversationMemberSerializer(many=True, read_only=True)
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    existing = serializers.SerializerMethodField()
    member_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False
    )

    class Meta:
        model = Conversation
        fields = [
            'id', 'type', 'name', 'is_encrypted', 'members', 'member_ids',
            'created_at', 'updated_at', 'last_message', 'unread_count', 'existing'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'is_encrypted']

    def get_last_message(self, obj):
        """Get the last message in the conversation."""
        last_message = obj.last_message
        if last_message:
            return MessageSerializer(last_message).data
        return None

    def get_unread_count(self, obj):
        """Get the count of unread messages for the current user (excluding deleted messages)."""
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            member = obj.members.filter(user=request.user).first()
            base_qs = obj.messages.filter(is_deleted=False).exclude(sender=request.user)
            if member and member.last_read_message_id:
                return base_qs.filter(id__gt=member.last_read_message_id).count()
            return base_qs.count()
        return 0

    def get_existing(self, obj):
        """Check if this conversation already existed (for direct conversations)."""
        return getattr(obj, '_existing', False)

    def create(self, validated_data):
        """
        Create a new conversation or return existing one for direct messages.
        For direct conversations between two users, only one conversation is allowed.
        """
        member_ids = validated_data.pop('member_ids', [])
        request = self.context.get('request')
        current_user = request.user if request else None

        # For direct conversations, check if one already exists between these users
        if validated_data.get('type') == Conversation.DIRECT and current_user:
            # Include current user in the check
            all_member_ids = set(member_ids + [current_user.id])

            # Only check for duplicates if there are exactly 2 members (1-on-1 conversation)
            if len(all_member_ids) == 2:
                other_user_id = list(all_member_ids - {current_user.id})[0]
                from users.models import User
                try:
                    other_user = User.objects.get(id=other_user_id)
                    existing = Conversation.get_direct_conversation_between(
                        current_user, other_user
                    )
                    if existing:
                        # Mark as existing and return it
                        existing._existing = True
                        return existing
                except User.DoesNotExist:
                    pass

        # Create new conversation
        conversation = Conversation.objects.create(**validated_data)
        conversation._existing = False

        # Add members
        if member_ids:
            from users.models import User
            members = User.objects.filter(id__in=member_ids)
            for member in members:
                ConversationMember.objects.create(
                    conversation=conversation,
                    user=member
                )

        return conversation


class ConversationThemeSerializer(serializers.ModelSerializer):
    """Serializer for conversation themes."""
    light_image_url = serializers.SerializerMethodField()
    dark_image_url = serializers.SerializerMethodField()
    css_variables_light = serializers.SerializerMethodField()
    css_variables_dark = serializers.SerializerMethodField()
    overlay_light = serializers.SerializerMethodField()
    overlay_dark = serializers.SerializerMethodField()

    class Meta:
        model = ConversationTheme
        fields = [
            'id', 'conversation', 'theme_type', 'light_color', 'dark_color',
            'light_gradient_start', 'light_gradient_end', 'dark_gradient_start', 
            'dark_gradient_end', 'gradient_angle', 'light_image_url', 'dark_image_url',
            'image_fit', 'overlay_opacity', 'light_overlay_color', 'dark_overlay_color',
            'css_variables_light', 'css_variables_dark', 'overlay_light', 'overlay_dark',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_light_image_url(self, obj):
        if obj.light_image:
            return obj.light_image.url
        return None

    def get_dark_image_url(self, obj):
        if obj.dark_image:
            return obj.dark_image.url
        return None

    def get_css_variables_light(self, obj):
        return obj.get_css_variables('light')

    def get_css_variables_dark(self, obj):
        return obj.get_css_variables('dark')

    def get_overlay_light(self, obj):
        return obj.get_overlay_css('light')

    def get_overlay_dark(self, obj):
        return obj.get_overlay_css('dark')


class ConversationThemeCreateUpdateSerializer(serializers.ModelSerializer):
    """Serializer for creating and updating conversation themes."""
    
    class Meta:
        model = ConversationTheme
        fields = [
            'theme_type', 'light_color', 'dark_color',
            'light_gradient_start', 'light_gradient_end', 'dark_gradient_start', 
            'dark_gradient_end', 'gradient_angle', 'light_image', 'dark_image',
            'image_fit', 'overlay_opacity', 'light_overlay_color', 'dark_overlay_color'
        ]

    def validate(self, data):
        theme_type = data.get('theme_type')
        
        if theme_type == 'solid':
            if not data.get('light_color') or not data.get('dark_color'):
                raise serializers.ValidationError(
                    "Solid color theme requires both light_color and dark_color"
                )
        elif theme_type == 'gradient':
            required_fields = ['light_gradient_start', 'light_gradient_end', 
                             'dark_gradient_start', 'dark_gradient_end']
            if not all(data.get(field) for field in required_fields):
                raise serializers.ValidationError(
                    "Gradient theme requires all gradient color fields"
                )
        elif theme_type == 'image':
            if not data.get('light_image') and not data.get('dark_image'):
                raise serializers.ValidationError(
                    "Image theme requires at least one image (light or dark mode)"
                )
        
        return data


class ConversationDetailSerializer(ConversationSerializer):
    """Detailed serializer for conversations with messages."""
    messages = MessageSerializer(many=True, read_only=True)

    class Meta(ConversationSerializer.Meta):
        fields = ConversationSerializer.Meta.fields + ['messages']
