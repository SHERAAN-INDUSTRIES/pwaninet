from .tasks import process_video_attachment_task
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action, api_view, permission_classes, parser_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from django_filters.rest_framework import DjangoFilterBackend
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
from django.db import models
from django.http import JsonResponse, HttpResponse, StreamingHttpResponse
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
import requests
from urllib.parse import urlparse
import re
import logging

logger = logging.getLogger(__name__)

from .models import Conversation, ConversationMember, Message, MessageReaction, ConversationTheme, MessageAttachment
from .services.link_preview_service import LinkPreviewService
from .services.chat_notification_service import ChatNotificationService
from .serializers import (
    ConversationSerializer,
    ConversationDetailSerializer,
    ConversationMemberSerializer,
    MessageSerializer,
    MessageCreateSerializer,
    MessageUpdateSerializer,
    MessageReactionSerializer,
    ConversationThemeSerializer,
    ConversationThemeCreateUpdateSerializer,
    FileUploadValidator
)
from .pagination import BeforeMessageIdPagination
from .observability import metrics


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def share_post_to_conversation(request):
    """Send a visible post as a rich internal-post message to a conversation."""
    conversation_id = request.data.get('conversation_id')
    post_share_id = request.data.get('post_share_id')
    if not conversation_id or not post_share_id:
        return Response(
            {'error': 'conversation_id and post_share_id are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    conversation = get_object_or_404(
        Conversation,
        id=conversation_id,
        members__user=request.user,
    )

    from users.services.privacy import visible_posts_for

    post = get_object_or_404(
        visible_posts_for(request.user).select_related('author'),
        share_id=post_share_id,
    )
    post_url = request.build_absolute_uri(
        reverse('posts:post_details', kwargs={'share_id': post.share_id})
    )
    content = (request.data.get('content') or '').strip()
    message = Message.objects.create(
        conversation=conversation,
        sender=request.user,
        content=content or f'{request.user.get_full_name() or request.user.username} shared a post',
        link_url=post_url,
        link_title=(post.content or 'Post on vasto')[:500],
        link_description=(post.content or '')[:2000],
        link_type='internal_post',
        is_forwarded=True,
    )
    conversation.save(update_fields=['updated_at'])

    msg_data = MessageSerializer(message, context={'request': request}).data
    async_to_sync(get_channel_layer().group_send)(
        f"chat_{conversation.id}",
        {'type': 'chat_message', 'message': msg_data},
    )
    ChatNotificationService.notify_new_message(message)
    return Response(msg_data, status=status.HTTP_201_CREATED)


class ConversationViewSet(viewsets.ModelViewSet):
    """ViewSet for managing conversations."""
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['type']
    search_fields = ['name']
    ordering_fields = ['created_at', 'updated_at', 'last_message_at']
    ordering = ['-last_message_at', '-updated_at']

    def get_queryset(self):
        """Return conversations for the current user."""
        return Conversation.objects.filter(
            members__user=self.request.user,
            messages__isnull=False,
        ).annotate(
            last_message_at=models.Max('messages__created_at'),
        ).distinct()

    def get_serializer_class(self):
        """Return appropriate serializer based on action."""
        if self.action == 'retrieve':
            return ConversationDetailSerializer
        return ConversationSerializer

    def perform_create(self, serializer):
        """Create a new conversation and add the current user as member."""
        conversation = serializer.save()
        if getattr(conversation, '_existing', False):
            return conversation
        # Only add current user if not already in member_ids
        member_ids = self.request.data.get('member_ids', [])
        if self.request.user.id not in member_ids:
            ConversationMember.objects.get_or_create(
                conversation=conversation,
                user=self.request.user
            )
        # Notify newly added members when a group conversation is created
        if conversation.type == Conversation.GROUP:
            for member in conversation.members.select_related('user').exclude(user=self.request.user):
                ChatNotificationService.notify_member_added(
                    conversation=conversation,
                    added_user=member.user,
                    actor=self.request.user,
                )
        return conversation

    @action(detail=True, methods=['post'])
    def add_member(self, request, pk=None):
        """Add a member to the conversation."""
        conversation = self.get_object()
        user_id = request.data.get('user_id')
        
        if not user_id:
            return Response(
                {'error': 'user_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        from users.models import User
        try:
            user = User.objects.get(id=user_id)
            member, created = ConversationMember.objects.get_or_create(
                conversation=conversation,
                user=user
            )
            if created and user.id != request.user.id:
                ChatNotificationService.notify_member_added(
                    conversation=conversation,
                    added_user=user,
                    actor=request.user,
                )
            return Response(
                ConversationMemberSerializer(member).data,
                status=status.HTTP_200_OK
            )
        except User.DoesNotExist:
            return Response(
                {'error': 'User not found'},
                status=status.HTTP_404_NOT_FOUND
            )

    @action(detail=True, methods=['post'])
    def remove_member(self, request, pk=None):
        """Remove a member from the conversation."""
        conversation = self.get_object()
        user_id = request.data.get('user_id')
        
        if not user_id:
            return Response(
                {'error': 'user_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        ConversationMember.objects.filter(
            conversation=conversation,
            user_id=user_id
        ).delete()
        return Response(
            {'status': 'member removed'},
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        """Mark all messages in conversation as read for current user."""
        conversation = self.get_object()
        member = conversation.members.filter(user=request.user).first()

        if not member:
            return Response(
                {'error': 'You are not a member of this conversation'},
                status=status.HTTP_403_FORBIDDEN
            )

        last_message = conversation.messages.order_by('-id').first()
        if last_message:
            member.last_read_message = last_message
            member.save(update_fields=['last_read_message'])
            Message.objects.filter(
                conversation=conversation,
                id__lte=last_message.id,
            ).exclude(sender=request.user).exclude(status='read').update(status='read')

        ChatNotificationService.notify_read_receipt(conversation.id, request.user)

        return Response(
            {'status': 'marked as read'},
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=['post'])
    def pin(self, request, pk=None):
        """Pin conversation for current user (max 3 pinned)."""
        from django.utils import timezone
        conversation = self.get_object()
        member = conversation.members.filter(user=request.user).first()

        if not member:
            return Response(
                {'error': 'You are not a member of this conversation'},
                status=status.HTTP_403_FORBIDDEN
            )

        pinned_count = ConversationMember.objects.filter(
            user=request.user,
            is_pinned=True
        ).exclude(id=member.id).count()

        if pinned_count >= 3:
            return Response(
                {'error': 'You can only pin up to 3 conversations'},
                status=status.HTTP_400_BAD_REQUEST
            )

        member.is_pinned = True
        member.pinned_at = timezone.now()
        member.save()

        return Response({
            'status': 'pinned',
            'is_pinned': True,
            'conversation_id': conversation.id
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def unpin(self, request, pk=None):
        """Unpin conversation for current user."""
        conversation = self.get_object()
        member = conversation.members.filter(user=request.user).first()

        if not member:
            return Response(
                {'error': 'You are not a member of this conversation'},
                status=status.HTTP_403_FORBIDDEN
            )

        member.is_pinned = False
        member.pinned_at = None
        member.save()

        return Response({
            'status': 'unpinned',
            'is_pinned': False,
            'conversation_id': conversation.id
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def set_public_key(self, request, pk=None):
        """Set the public key for the current user in this conversation."""
        conversation = self.get_object()
        member = conversation.members.filter(user=request.user).first()

        if not member:
            return Response(
                {'error': 'You are not a member of this conversation'},
                status=status.HTTP_403_FORBIDDEN
            )

        public_key = request.data.get('public_key')
        if not public_key:
            return Response(
                {'error': 'public_key is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        member.public_key = public_key
        member.save()

        return Response(
            ConversationMemberSerializer(member).data,
            status=status.HTTP_200_OK
        )


class MessageViewSet(viewsets.ModelViewSet):
    """ViewSet for managing messages with pagination and rate limiting."""
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['conversation', 'sender']
    search_fields = ['content']
    ordering_fields = ['created_at']
    ordering = ['created_at']
    pagination_class = None  # Messages use custom pagination per view

    def get_queryset(self):
        """Return messages the user has access to with optimized queries."""
        user = self.request.user
        return Message.objects.filter(
            conversation__members__user=user
        ).select_related(
            'sender',
            'conversation',
            'reply_to',
            'reply_to__sender',
            'link_preview',
        ).prefetch_related(
            'attachments',
            'reactions__user',
            'conversation__members',
        ).distinct()

    def get_serializer_class(self):
        """Return appropriate serializer based on action."""
        if self.action == 'create':
            return MessageCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return MessageUpdateSerializer
        return MessageSerializer

    def list(self, request, *args, **kwargs):
        """List messages with support for limit and before_id cursor pagination."""
        queryset = self.filter_queryset(self.get_queryset())
        
        limit_param = request.query_params.get('limit')
        before_id = request.query_params.get('before_id')
        
        if limit_param:
            try:
                limit = max(1, min(100, int(limit_param)))
                if before_id:
                    queryset = queryset.filter(id__lt=before_id)
                # Fetch the most recent messages up to limit in reverse chronological order
                messages_slice = list(queryset.order_by('-created_at')[:limit])
                # Reverse back to chronological order (oldest to newest)
                messages_slice.reverse()
                serializer = self.get_serializer(messages_slice, many=True)
                return Response(serializer.data)
            except (ValueError, TypeError):
                pass
        
        messages_slice = list(queryset.order_by('-created_at')[:100])
        messages_slice.reverse()
        serializer = self.get_serializer(messages_slice, many=True)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        """Create a message and return full canonical MessageSerializer payload (including id, status, created_at, temp_id)."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = self.perform_create(serializer)
        response_data = getattr(self, '_last_created_msg_data', None) or MessageSerializer(message, context=self.get_serializer_context()).data
        headers = self.get_success_headers(response_data)
        return Response(response_data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_create(self, serializer):
        """Create a new message and broadcast via WebSocket with rate limiting."""
        # Handle conversation_id from FormData for file uploads
        conversation_id = self.request.data.get('conversation_id')
        if conversation_id and not serializer.validated_data.get('conversation'):
            from .models import Conversation
            serializer.validated_data['conversation'] = Conversation.objects.get(id=conversation_id)
        
        message = serializer.save(sender=self.request.user)
        
        # Generate link preview if message contains URLs
        try:
            LinkPreviewService.generate_preview_for_message(message)
        except Exception as e:
            # Log error but don't block message creation
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"[LINK_PREVIEW] Error generating preview: {str(e)}")
        
        # Update conversation timestamp
        message.conversation.save()
        
        # Broadcast message via WebSocket with temp_id preserved
        temp_id = self.request.data.get('temp_id') or self.request.data.get('client_id')
        msg_data = MessageSerializer(message, context=self.get_serializer_context()).data
        if temp_id:
            msg_data['temp_id'] = temp_id
        self._last_created_msg_data = msg_data
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"chat_{message.conversation.id}",
            {
                'type': 'chat_message',
                'message': msg_data,
                'temp_id': temp_id
            }
        )

        # Dispatch rich conversation_update + OS push notifications
        ChatNotificationService.notify_new_message(message)
        
        return message

    def perform_update(self, serializer):
        """Update message and mark as edited."""
        from django.utils import timezone
        updated_msg = serializer.save(edited_at=timezone.now())
        ChatNotificationService.notify_message_edited(updated_msg, self.request.user)
        return updated_msg

    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        """Mark a message as read for the current user using ConversationMember.last_read_message."""
        message = self.get_object()
        
        # Check if user is a member of the conversation
        if not message.conversation.members.filter(user=request.user).exists():
            return Response(
                {'error': 'You are not a member of this conversation'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Update member's last read message (this replaces per-message read receipts)
        member = message.conversation.members.filter(user=request.user).first()
        if member:
            member.last_read_message = message
            member.save(update_fields=['last_read_message'])
            Message.objects.filter(
                conversation_id=message.conversation_id,
                id__lte=message.id,
            ).exclude(sender=request.user).exclude(status='read').update(status='read')

        ChatNotificationService.notify_read_receipt(message.conversation_id, request.user)
        
        return Response(
            {'status': 'marked as read', 'message_id': message.id},
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=['post'])
    def add_reaction(self, request, pk=None):
        """Add or remove a reaction to a message."""
        message = self.get_object()
        emoji = request.data.get('emoji')
        
        if not emoji:
            return Response(
                {'error': 'emoji is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if reaction already exists
        existing_reaction = MessageReaction.objects.filter(
            message=message,
            user=request.user,
            emoji=emoji
        ).first()
        
        if existing_reaction:
            existing_reaction.delete()
            return Response(
                {'status': 'reaction removed'},
                status=status.HTTP_200_OK
            )
        
        # Create new reaction
        reaction_obj = MessageReaction.objects.create(
            message=message,
            user=request.user,
            emoji=emoji
        )
        ChatNotificationService.notify_reaction(message, request.user, emoji)
        
        return Response(
            MessageReactionSerializer(reaction_obj).data,
            status=status.HTTP_201_CREATED
        )

    @action(detail=False, methods=['get'])
    def paginated(self, request):
        """
        Get paginated messages for a conversation using cursor-based pagination.
        Uses before_message_id cursor strategy for efficient message history loading.
        """
        conversation_id = request.query_params.get('conversation_id')
        
        if not conversation_id:
            return Response(
                {'error': 'conversation_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Verify user is a member of the conversation
        conversation = get_object_or_404(
            Conversation,
            id=conversation_id,
            members__user=request.user
        )
        
        # Get messages for this conversation with all relations needed by MessageSerializer
        messages = Message.objects.filter(
            conversation=conversation
        ).select_related(
            'sender',
            'conversation',
            'reply_to',
            'reply_to__sender',
            'link_preview',
        ).prefetch_related(
            'attachments',
            'reactions__user',
            'conversation__members',
        )
        
        # Apply cursor-based pagination
        paginator = BeforeMessageIdPagination(default_limit=30, max_limit=100)
        paginated_messages = paginator.paginate_queryset(messages, request)
        
        # Serialize paginated messages
        serializer = MessageSerializer(paginated_messages, many=True, context={'request': request})
        
        return paginator.get_paginated_response(serializer.data)


class MessageReactionViewSet(viewsets.ModelViewSet):
    """ViewSet for managing message reactions."""
    serializer_class = MessageReactionSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['message', 'user', 'emoji']

    def get_queryset(self):
        """Return reactions for messages the user has access to."""
        user = self.request.user
        return MessageReaction.objects.filter(
            message__conversation__members__user=user
        ).distinct()


def _build_message_preview(last_msg, current_user, is_group=False):
    """Build a rich preview dict for a conversation's last message using ChatNotificationService."""
    if not last_msg:
        return None

    preview_info = ChatNotificationService.build_message_preview(
        last_msg,
        include_forwarded_prefix=False,
    )
    clean_label = preview_info['clean_text']

    if last_msg.sender_id == current_user.id:
        prefix = 'You: '
    elif is_group and getattr(last_msg, 'sender', None):
        prefix = f"{last_msg.sender.username}: "
    else:
        prefix = ''

    full_text = f"{prefix}{clean_label}"
    if len(full_text) > 58:
        full_text = full_text[:55] + '...'

    return {
        'icon': preview_info['icon'],
        'type': preview_info['type'],
        'text': full_text,
    }


def build_user_conversation_data(user, active_conversation_id=None):
    """
    Fetch user's conversations, last messages, read statuses, and unread counts
    in a constant 4 SQL queries instead of O(N) queries and full message table prefetches.
    """
    from django.db.models.functions import Coalesce

    conversations = list(
        Conversation.objects.filter(members__user=user)
        .prefetch_related(
            models.Prefetch(
                'members',
                queryset=ConversationMember.objects.select_related('user')
            )
        )
        .annotate(
            last_msg_id=models.Max('messages__id'),
            last_msg_time=models.Max('messages__created_at'),
        )
        .order_by('-last_msg_time')
        .distinct()
    )

    if not conversations:
        return []

    # Batch-fetch only the latest message per conversation (with attachments prefetched for album/duration previews)
    last_msg_ids = [c.last_msg_id for c in conversations if c.last_msg_id]
    last_messages_by_id = {}
    if last_msg_ids:
        for msg in Message.objects.filter(id__in=last_msg_ids).select_related('sender').prefetch_related('attachments'):
            last_messages_by_id[msg.id] = msg

    # Batch-fetch unread counts per conversation in a single grouped SQL query (excluding deleted messages)
    unread_rows = (
        Message.objects.filter(
            conversation__members__user=user,
            is_deleted=False,
            id__gt=Coalesce(models.F('conversation__members__last_read_message_id'), models.Value(0)),
        )
        .exclude(sender=user)
        .values('conversation_id')
        .annotate(cnt=models.Count('id', distinct=True))
    )
    unread_by_conv = {row['conversation_id']: row['cnt'] for row in unread_rows}

    conversation_data = []
    for conv in conversations:
        last_msg = last_messages_by_id.get(conv.last_msg_id)
        conv._cached_last_message = last_msg

        mem = next((m for m in conv.members.all() if m.user_id == user.id), None)

        read_status = None
        if last_msg and last_msg.sender_id == user.id:
            read_status = conv.get_last_message_read_status(user) or 'sent'

        if active_conversation_id and conv.id == int(active_conversation_id):
            unread_count = 0
        else:
            unread_count = unread_by_conv.get(conv.id, 0)

        is_pinned = bool(mem and mem.is_pinned)
        pinned_at = mem.pinned_at if mem else None

        conversation_data.append({
            'conversation': conv,
            'read_status': read_status,
            'unread_count': unread_count,
            'is_pinned': is_pinned,
            'pinned_at': pinned_at,
            'preview': _build_message_preview(last_msg, user, is_group=(conv.type == 'group')),
        })

    conversation_data.sort(
        key=lambda item: (
            not item['is_pinned'],
            -(item['conversation'].last_msg_time.timestamp() if item['conversation'].last_msg_time else 0)
        )
    )
    return conversation_data


# Template Views
@login_required
def conversation_list(request):
    """Display list of user's conversations with optimized constant-query loading."""
    active_conversation = request.GET.get('conversation')
    conversation_data = build_user_conversation_data(
        request.user,
        active_conversation_id=active_conversation
    )

    from users.models import User, Follow
    from users.services.friend_suggestion_service import get_friend_suggestions_for_user
    # Only show users that the current user is following
    users = User.objects.filter(
        follower_relationships__follower=request.user
    ).exclude(id=request.user.id).distinct()
    suggested_users = get_friend_suggestions_for_user(request.user, limit=10)

    # Extract existing direct chat user IDs from already-loaded conversation_data in memory
    existing_direct_user_ids = set()
    for item in conversation_data:
        conv = item['conversation']
        if conv.type == 'direct':
            for m in conv.members.all():
                if m.user_id != request.user.id:
                    existing_direct_user_ids.add(m.user_id)

    unmessaged_friends = users.exclude(id__in=existing_direct_user_ids)
    if existing_direct_user_ids:
        try:
            suggested_users = suggested_users.exclude(id__in=existing_direct_user_ids)
        except Exception:
            try:
                suggested_users = [u for u in suggested_users if getattr(u, 'id', None) not in existing_direct_user_ids]
            except Exception:
                pass

    # Detect desktop vs mobile client
    ua_string = request.META.get('HTTP_USER_AGENT', '')
    is_mobile = False
    try:
        from user_agents import parse
        user_agent = parse(ua_string)
        is_mobile = user_agent.is_mobile
    except Exception:
        is_mobile = any(k in ua_string.lower() for k in ['mobile', 'android', 'iphone', 'ipod'])

    # Desktop: always the three-panel layout. The conversation list
    # template is mobile-only (?view=list is ignored on desktop clients).
    if not is_mobile:
        from django.utils import timezone
        from datetime import timedelta
        today = timezone.now().date()
        yesterday = today - timedelta(days=1)
        context = {
            'conversation': None,
            'messages': [],
            'conversation_data': conversation_data,
            'users': users,
            'unmessaged_friends': unmessaged_friends,
            'suggested_users': suggested_users,
            'today': today.strftime('%Y-%m-%d'),
            'yesterday': yesterday.strftime('%Y-%m-%d'),
        }
        return render(request, 'messaging/conversation_detail_refactored.html', context)

    # Get IDs of users that current user is following
    following_ids = set(Follow.objects.filter(
        follower=request.user
    ).values_list('followed_id', flat=True))

    active_conversation_obj = None
    messages = []

    if active_conversation:
        active_conversation_obj = next(
            (item['conversation'] for item in conversation_data if str(item['conversation'].id) == str(active_conversation)),
            None
        )

    context = {
        'conversation_data': conversation_data,
        'users': users,
        'unmessaged_friends': unmessaged_friends,
        'suggested_users': suggested_users,
        'following_ids': following_ids,
        'active_conversation': active_conversation,
        'active_conversation_obj': active_conversation_obj,
        'messages': messages,
    }

    return render(request, 'messaging/conversation_list.html', context)



@login_required
def search_followed_users(request):
    """Search followed users for new conversation modal using HTMX."""
    from users.models import User
    search_query = request.GET.get('search', '')

    users = User.objects.filter(
        follower_relationships__follower=request.user
    ).exclude(id=request.user.id).distinct()

    if search_query:
        users = users.filter(
            username__icontains=search_query
        ) | users.filter(
            first_name__icontains=search_query
        ) | users.filter(
            last_name__icontains=search_query
        )

    return render(request, 'messaging/partials/user_search_results.html', {'users': users})


@login_required
def conversation_detail(request, conversation_id):
    """Display a specific conversation with fast O(1) query complexity."""
    from django.utils import timezone
    from datetime import timedelta
    import json

    conversation = get_object_or_404(
        Conversation.objects.prefetch_related(
            models.Prefetch(
                'members',
                queryset=ConversationMember.objects.select_related('user')
            )
        ),
        id=conversation_id,
        members__user=request.user
    )

    # Fetch the most recent 40 messages with all relations needed for serialization
    recent_messages_qs = (
        conversation.messages.select_related(
            'sender',
            'conversation',
            'reply_to',
            'reply_to__sender',
            'link_preview',
        )
        .prefetch_related(
            'attachments',
            'reactions__user',
        )
        .order_by('-created_at')[:40]
    )
    recent_messages_list = list(reversed(list(recent_messages_qs)))
    for msg in recent_messages_list:
        msg.conversation = conversation

    # Mark conversation as read only if there is a newer message than last_read_message_id
    member = next((m for m in conversation.members.all() if m.user_id == request.user.id), None)
    if member and recent_messages_list:
        last_message = recent_messages_list[-1]
        if member.last_read_message_id != last_message.id:
            member.last_read_message = last_message
            member.save(update_fields=['last_read_message'])
            Message.objects.filter(
                conversation_id=conversation.id,
                id__lte=last_message.id
            ).exclude(sender=request.user).exclude(status='read').update(status='read')
            ChatNotificationService.notify_read_receipt(conversation.id, request.user)

    # Calculate today and yesterday dates
    today = timezone.now().date()
    yesterday = today - timedelta(days=1)

    # Determine partner user for direct conversations using already-prefetched members
    partner_user = None
    partner_relationship = 'Connected on vasto'
    if conversation.type == 'direct':
        partner_member = next((m for m in conversation.members.all() if m.user_id != request.user.id), None)
        if partner_member:
            partner_user = partner_member.user
            try:
                from users.models import Follow
                follow_pairs = set(
                    Follow.objects.filter(
                        models.Q(follower=request.user, followed=partner_user) |
                        models.Q(follower=partner_user, followed=request.user)
                    ).values_list('follower_id', 'followed_id')
                )
                if (request.user.id, partner_user.id) in follow_pairs and (partner_user.id, request.user.id) in follow_pairs:
                    partner_relationship = 'Mutual Follower'
            except Exception:
                pass
            if getattr(request.user, 'course_id', None) and request.user.course_id == getattr(partner_user, 'course_id', None):
                partner_relationship = 'Classmate'

    # Serialize recent messages directly for 0ms DOM paint
    initial_messages_json = json.dumps(
        MessageSerializer(recent_messages_list, many=True, context={'request': request}).data
    )

    is_htmx = bool(request.headers.get('HX-Request'))

    # Fast path for HTMX chat switching (desktop middle pane or mobile instant chat):
    # Skip querying all conversations, friend suggestions, and unmessaged friends!
    if is_htmx:
        context = {
            'conversation': conversation,
            'messages': recent_messages_list,
            'initial_messages_json': initial_messages_json,
            'partner_user': partner_user,
            'partner_relationship': partner_relationship,
            'today': today.strftime('%Y-%m-%d'),
            'yesterday': yesterday.strftime('%Y-%m-%d'),
        }
        return render(request, 'messaging/partials/conversation_chat_partial.html', context)

    # Full page load: populate left rail conversation list and new-chat modal data
    conversation_data = build_user_conversation_data(
        request.user,
        active_conversation_id=conversation.id
    )

    from users.models import User
    users = User.objects.filter(
        follower_relationships__follower=request.user
    ).exclude(id=request.user.id).distinct()

    existing_direct_user_ids = set()
    for item in conversation_data:
        conv = item['conversation']
        if conv.type == 'direct':
            for m in conv.members.all():
                if m.user_id != request.user.id:
                    existing_direct_user_ids.add(m.user_id)

    unmessaged_friends = users.exclude(id__in=existing_direct_user_ids)

    from users.services.friend_suggestion_service import get_friend_suggestions_for_user
    suggested_users = get_friend_suggestions_for_user(request.user, limit=10)
    if existing_direct_user_ids:
        try:
            suggested_users = suggested_users.exclude(id__in=existing_direct_user_ids)
        except Exception:
            try:
                suggested_users = [u for u in suggested_users if getattr(u, 'id', None) not in existing_direct_user_ids]
            except Exception:
                pass

    context = {
        'conversation': conversation,
        'messages': recent_messages_list,
        'initial_messages_json': initial_messages_json,
        'partner_user': partner_user,
        'partner_relationship': partner_relationship,
        'conversation_data': conversation_data,
        'users': users,
        'unmessaged_friends': unmessaged_friends,
        'suggested_users': suggested_users,
        'today': today.strftime('%Y-%m-%d'),
        'yesterday': yesterday.strftime('%Y-%m-%d'),
    }

    return render(request, 'messaging/conversation_detail_refactored.html', context)


def get_conversation_media_items(conversation, media_type, page=1, page_size=24):
    """Helper to query and normalize media items for lazy loading."""
    from django.conf import settings
    offset = (page - 1) * page_size
    limit = page_size + 1

    if media_type == 'media':
        atts = list(MessageAttachment.objects.filter(
            message__conversation=conversation,
            file_type__in=['image', 'video']
        ).select_related('message'))

        legacy = list(Message.objects.filter(
            conversation=conversation,
            attachment_type__in=['image', 'video']
        ).exclude(attachment=''))

        normalized = []
        for a in atts:
            url = a.file.url if a.file else a.file_url
            if url:
                file_display_name = (getattr(a.file, 'name', '').split('/')[-1] if getattr(a.file, 'name', None) else '') or getattr(a, 'caption', '') or 'Media'
                normalized.append({
                    'id': a.id,
                    'type': a.file_type,
                    'url': url,
                    'created_at': a.created_at or a.message.created_at,
                    'name': file_display_name
                })
        for m in legacy:
            url = m.attachment.url if m.attachment else m.attachment_url
            if url:
                normalized.append({
                    'id': f"legacy_{m.id}",
                    'type': m.attachment_type,
                    'url': url,
                    'created_at': m.created_at,
                    'name': getattr(m.attachment, 'name', '') or 'Media'
                })

        normalized.sort(key=lambda x: x['created_at'], reverse=True)
        slice_items = normalized[offset:offset + limit]
        has_next = len(slice_items) > page_size
        return slice_items[:page_size], has_next

    elif media_type == 'docs':
        atts = list(MessageAttachment.objects.filter(
            message__conversation=conversation,
            file_type='document'
        ).select_related('message'))

        legacy = list(Message.objects.filter(
            conversation=conversation,
            attachment_type='document'
        ).exclude(attachment=''))

        normalized = []
        for a in atts:
            url = a.file.url if a.file else a.file_url
            if url:
                doc_display_name = (getattr(a.file, 'name', '').split('/')[-1] if getattr(a.file, 'name', None) else '') or getattr(a, 'caption', '') or 'Document'
                normalized.append({
                    'id': a.id,
                    'url': url,
                    'created_at': a.created_at or a.message.created_at,
                    'name': doc_display_name
                })
        for m in legacy:
            url = m.attachment.url if m.attachment else m.attachment_url
            if url:
                normalized.append({
                    'id': f"legacy_{m.id}",
                    'url': url,
                    'created_at': m.created_at,
                    'name': (getattr(m.attachment, 'name', '').split('/')[-1] if getattr(m.attachment, 'name', None) else 'Document')
                })

        normalized.sort(key=lambda x: x['created_at'], reverse=True)
        slice_items = normalized[offset:offset + limit]
        has_next = len(slice_items) > page_size
        return slice_items[:page_size], has_next

    elif media_type == 'audio':
        atts = list(MessageAttachment.objects.filter(
            message__conversation=conversation,
            file_type='audio'
        ).select_related('message'))

        legacy = list(Message.objects.filter(
            conversation=conversation,
            attachment_type='audio'
        ).exclude(attachment=''))

        normalized = []
        for a in atts:
            url = a.file.url if a.file else a.file_url
            if url:
                audio_display_name = (getattr(a.file, 'name', '').split('/')[-1] if getattr(a.file, 'name', None) else '') or getattr(a, 'caption', '') or 'Voice Note'
                normalized.append({
                    'id': a.id,
                    'url': url,
                    'created_at': a.created_at or a.message.created_at,
                    'name': audio_display_name
                })
        for m in legacy:
            url = m.attachment.url if m.attachment else m.attachment_url
            if url:
                normalized.append({
                    'id': f"legacy_{m.id}",
                    'url': url,
                    'created_at': m.created_at,
                    'name': 'Voice Note'
                })

        normalized.sort(key=lambda x: x['created_at'], reverse=True)
        slice_items = normalized[offset:offset + limit]
        has_next = len(slice_items) > page_size
        return slice_items[:page_size], has_next

    elif media_type == 'links':
        msgs = Message.objects.filter(
            models.Q(link_url__isnull=False) | models.Q(link_preview__isnull=False),
            conversation=conversation
        ).exclude(link_url='').select_related('link_preview').order_by('-created_at')

        normalized = []
        for m in msgs:
            preview = getattr(m, 'link_preview', None)
            url = m.link_url or (preview.url if preview else '')
            if url:
                normalized.append({
                    'id': m.id,
                    'url': url,
                    'title': (preview.title if preview and preview.title else url),
                    'description': (preview.description if preview and preview.description else ''),
                    'created_at': m.created_at
                })

        slice_items = normalized[offset:offset + limit]
        has_next = len(slice_items) > page_size
        return slice_items[:page_size], has_next

    return [], False


@login_required
def conversation_media(request, conversation_id):
    """Paginated lazy-loaded media endpoint for the dynamic right media rail."""
    conversation = get_object_or_404(
        Conversation,
        id=conversation_id,
        members__user=request.user
    )

    media_type = request.GET.get('type', 'media')
    try:
        page = max(1, int(request.GET.get('page', 1)))
    except (ValueError, TypeError):
        page = 1

    items, has_next = get_conversation_media_items(conversation, media_type, page=page, page_size=24)

    context = {
        'conversation': conversation,
        'media_type': media_type,
        'items': items,
        'page': page,
        'has_next': has_next,
        'next_page': page + 1 if has_next else None,
    }

    return render(request, 'messaging/partials/conversation_media_items.html', context)


@csrf_exempt
@login_required
def create_conversation(request):
    """Create a new conversation or redirect to existing one."""
    is_ajax = (
        request.headers.get('x-requested-with') == 'XMLHttpRequest' or
        'application/json' in request.headers.get('accept', '') or
        request.content_type == 'application/json'
    )
    if request.method == 'POST':
        user_id = request.POST.get('user_id')
        if not user_id and request.content_type == 'application/json':
            import json
            try:
                data = json.loads(request.body)
                user_id = data.get('user_id')
            except Exception:
                pass
        conversation_type = request.POST.get('type', 'direct')
        
        if not user_id:
            if is_ajax:
                return JsonResponse({'error': 'User ID is required.'}, status=400)
            messages.error(request, 'User ID is required.')
            return redirect('messaging:conversation_list')
        
        try:
            from users.models import User
            other_user = User.objects.get(id=user_id)
            
            # Check if conversation already exists
            existing = Conversation.get_direct_conversation_between(
                request.user, other_user
            )
            
            if existing:
                if is_ajax:
                    return JsonResponse({
                        'success': True,
                        'id': existing.id,
                        'conversation_id': existing.id,
                        'existing': True,
                        'redirect_url': reverse('messaging:conversation_detail', kwargs={'conversation_id': existing.id})
                    })
                messages.info(request, 'Existing conversation found.')
                return redirect('messaging:conversation_detail', conversation_id=existing.id)
            
            # Create new conversation
            conversation = Conversation.objects.create(type=conversation_type)
            
            # Add members safely
            ConversationMember.objects.get_or_create(conversation=conversation, user=request.user)
            ConversationMember.objects.get_or_create(conversation=conversation, user=other_user)
            
            if is_ajax:
                return JsonResponse({
                    'success': True,
                    'id': conversation.id,
                    'conversation_id': conversation.id,
                    'existing': False,
                    'redirect_url': reverse('messaging:conversation_detail', kwargs={'conversation_id': conversation.id})
                })
            messages.success(request, 'Conversation created successfully.')
            return redirect('messaging:conversation_detail', conversation_id=conversation.id)
            
        except User.DoesNotExist:
            if is_ajax:
                return JsonResponse({'error': 'User not found.'}, status=404)
            messages.error(request, 'User not found.')
        except Exception as e:
            if is_ajax:
                return JsonResponse({'error': f'Failed to create conversation: {str(e)}'}, status=500)
            messages.error(request, f'Failed to create conversation: {str(e)}')
    
    if is_ajax:
        return JsonResponse({'error': 'Invalid request method.'}, status=400)
    return redirect('messaging:conversation_list')


class ConversationThemeViewSet(viewsets.ModelViewSet):
    """ViewSet for managing conversation themes."""
    permission_classes = [IsAuthenticated]
    serializer_class = ConversationThemeSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['conversation']

    def get_queryset(self):
        """Return themes for the current user."""
        return ConversationTheme.objects.filter(user=self.request.user)

    def get_serializer_class(self):
        """Return appropriate serializer based on action."""
        if self.action in ['create', 'update', 'partial_update']:
            return ConversationThemeCreateUpdateSerializer
        return ConversationThemeSerializer

    def perform_create(self, serializer):
        """Create a theme for the current user and conversation."""
        conversation_id = self.request.data.get('conversation')
        conversation = get_object_or_404(Conversation, id=conversation_id)
        
        # Verify user is a member of this conversation
        if not conversation.members.filter(user=self.request.user).exists():
            raise PermissionError("You are not a member of this conversation")
        
        serializer.save(user=self.request.user, conversation=conversation)

    def perform_update(self, serializer):
        """Update a theme for the current user."""
        # Verify user owns this theme
        if serializer.instance.user != self.request.user:
            raise PermissionError("You can only edit your own themes")
        serializer.save()

    @action(detail=True, methods=['post'])
    def apply(self, request, pk=None):
        """Apply a theme to the conversation."""
        theme = self.get_object()
        conversation = theme.conversation
        
        # Verify user is a member of this conversation
        if not conversation.members.filter(user=request.user).exists():
            return Response(
                {'error': 'You are not a member of this conversation'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Theme is already applied since it's user-specific
        return Response({
            'message': 'Theme applied successfully',
            'theme': ConversationThemeSerializer(theme).data
        })

    @action(detail=False, methods=['get'])
    def by_conversation(self, request):
        """Get theme for a specific conversation."""
        conversation_id = request.query_params.get('conversation_id')
        if not conversation_id:
            return Response(
                {'error': 'conversation_id parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        conversation = get_object_or_404(Conversation, id=conversation_id)
        
        # Verify user is a member of this conversation
        if not conversation.members.filter(user=request.user).exists():
            return Response(
                {'error': 'You are not a member of this conversation'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        try:
            theme = ConversationTheme.objects.get(
                user=request.user,
                conversation=conversation
            )
            serializer = self.get_serializer(theme)
            return Response(serializer.data)
        except ConversationTheme.DoesNotExist:
            # Return default theme
            return Response({
                'theme_type': 'solid',
                'light_color': '#f8fafc',
                'dark_color': '#18191f',
                'overlay_opacity': 0.3,
                'light_overlay_color': '#ffffff',
                'dark_overlay_color': '#000000'
            })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def attachment_upload(request):
    """Handle file attachment uploads for messages."""
    try:
        file = request.FILES.get('file')
        conversation_id = request.data.get('conversation_id')
        
        if not file:
            metrics.record_upload_failure(request.user.id, 'NO_FILE', 0)
            return Response(
                {'error': 'No file provided', 'error_code': 'NO_FILE'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not conversation_id:
            metrics.record_upload_failure(request.user.id, 'NO_CONVERSATION_ID', 0)
            return Response(
                {'error': 'conversation_id is required', 'error_code': 'NO_CONVERSATION_ID'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Verify user is a member of the conversation
        conversation = get_object_or_404(Conversation, id=conversation_id)
        if not conversation.members.filter(user=request.user).exists():
            metrics.record_upload_failure(request.user.id, 'NOT_MEMBER', file.size)
            return Response(
                {'error': 'You are not a member of this conversation', 'error_code': 'NOT_MEMBER'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Validate file using FileUploadValidator
        is_valid, error_code, error_message, attachment_type = FileUploadValidator.validate(file)
        
        if not is_valid:
            metrics.record_upload_failure(request.user.id, error_code, file.size)
            return Response(
                {'error': error_message, 'error_code': error_code},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if this is a recorded voice note
        is_voice_note = request.data.get('is_voice_note') in ['true', 'True', True, '1', 1] or file.name.startswith('voice_')
        final_attachment_type = 'voice_note' if is_voice_note else attachment_type
        final_message_type = 'voice_note' if is_voice_note else ('audio' if attachment_type == 'audio' else 'media')
        raw_reply_to = request.data.get('reply_to_id') or request.POST.get('reply_to_id')
        reply_to_id = int(raw_reply_to) if raw_reply_to and str(raw_reply_to).isdigit() else None

        # Create message with attachment
        message = Message.objects.create(
            conversation=conversation,
            sender=request.user,
            attachment=file,
            attachment_type=final_attachment_type,
            message_type=final_message_type,
            reply_to_id=reply_to_id,
            content=file.name if final_attachment_type in ['document', 'audio'] else ''
        )

        # Also create MessageAttachment for media rail indexing
        try:
            MessageAttachment.objects.create(
                message=message,
                file=message.attachment,
                file_type='audio' if is_voice_note else attachment_type,
                caption='',
                order=0,
                size=file.size
            )
        except Exception as ma_err:
            logger.warning(f"Could not create secondary MessageAttachment: {ma_err}")
        
        # Update conversation timestamp
        conversation.save()
        
        # Extract temp_id for seamless client reconciliation
        temp_id = request.data.get('temp_id') or request.POST.get('temp_id')

        # Broadcast message via WebSocket
        channel_layer = get_channel_layer()
        msg_payload = MessageSerializer(message).data
        if temp_id:
            msg_payload['temp_id'] = temp_id

        async_to_sync(channel_layer.group_send)(
            f"chat_{conversation.id}",
            {
                'type': 'chat_message',
                'message': msg_payload,
                'temp_id': temp_id
            }
        )

        # Dispatch rich conversation_update + OS push notifications after attachment is created
        ChatNotificationService.notify_new_message(message)
        
        return Response(
            msg_payload,
            status=status.HTTP_201_CREATED
        )
        
    except Exception as e:
        return Response(
            {'error': str(e), 'error_code': 'SERVER_ERROR'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


def process_video_attachment(uploaded_file, trim_start=0.0, trim_end=None, is_muted=False, rotation=0, is_trimmed=False):
    """
    Trims, mutes, and/or rotates a video file using ffmpeg.
    Returns (processed_file, file_size, duration).
    Falls back safely to original file if processing is not required or fails.
    """
    needs_trim = bool(is_trimmed) or (trim_start and float(trim_start) > 0.05) or (trim_end and float(trim_end) > 0.05)
    needs_mute = bool(is_muted)
    needs_rotate = bool(rotation and (int(rotation) % 360) != 0)

    if not (needs_trim or needs_mute or needs_rotate):
        return uploaded_file, uploaded_file.size, None

    in_temp_path = None
    out_temp_path = None

    try:
        import os
        import subprocess
        import tempfile
        import logging
        from django.core.files.uploadedfile import SimpleUploadedFile

        logger = logging.getLogger(__name__)

        ext = os.path.splitext(uploaded_file.name)[1].lower() or '.mp4'
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as in_temp:
            in_temp_path = in_temp.name
            for chunk in uploaded_file.chunks():
                in_temp.write(chunk)

        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as out_temp:
            out_temp_path = out_temp.name

        cmd = ['/usr/bin/ffmpeg', '-y', '-i', in_temp_path, '-map', '0:v', '-map', '0:a?']

        start_val = max(0.0, float(trim_start)) if trim_start else 0.0
        if start_val > 0.05:
            cmd.extend(['-ss', f'{start_val:.3f}'])

        end_val = float(trim_end) if trim_end else 0.0
        if end_val > start_val:
            duration = end_val - start_val
            cmd.extend(['-t', f'{duration:.3f}'])
        elif start_val > 0.05 and not end_val:
            duration = None
        else:
            duration = None

        vf_filters = []
        if needs_rotate:
            rot = int(rotation) % 360
            if rot == 90:
                vf_filters.append('transpose=1')
            elif rot == 180:
                vf_filters.append('transpose=2,transpose=2')
            elif rot == 270:
                vf_filters.append('transpose=2')

        if vf_filters:
            cmd.extend(['-vf', ','.join(vf_filters)])

        cmd.extend(['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23'])

        if needs_mute:
            cmd.append('-an')
        else:
            cmd.extend(['-c:a', 'aac', '-b:a', '128k'])

        cmd.extend(['-movflags', '+faststart', out_temp_path])

        logger.info(f"[VIDEO_TRIM] Running: {' '.join(cmd)}")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)

        if res.returncode != 0:
            logger.error(f"[VIDEO_TRIM] FFmpeg error ({res.returncode}): {res.stderr.decode('utf-8', errors='ignore')[-400:]}")
            uploaded_file.seek(0)
            return uploaded_file, uploaded_file.size, None

        with open(out_temp_path, 'rb') as f:
            processed_data = f.read()

        out_name = os.path.splitext(uploaded_file.name)[0] + '.mp4'
        processed_file = SimpleUploadedFile(
            name=out_name,
            content=processed_data,
            content_type='video/mp4'
        )
        return processed_file, len(processed_data), duration
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"[VIDEO_TRIM] Processing exception: {e}", exc_info=True)
        uploaded_file.seek(0)
        return uploaded_file, uploaded_file.size, None
    finally:
        import os
        if in_temp_path and os.path.exists(in_temp_path):
            try:
                os.remove(in_temp_path)
            except Exception:
                pass
        if out_temp_path and os.path.exists(out_temp_path):
            try:
                os.remove(out_temp_path)
            except Exception:
                pass


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def batch_attachment_upload(request):
    """Handle batch file attachment uploads for media group messages."""
    try:
        from .serializers import MessageAttachmentCreateSerializer
        import json
        
        conversation_id = request.data.get('conversation_id')
        global_caption = request.data.get('global_caption', '')
        attachments_data = request.data.get('attachments_data')
        temp_id = request.data.get('temp_id') or request.data.get('client_id')
        
        if not conversation_id:
            return Response(
                {'error': 'conversation_id is required', 'error_code': 'NO_CONVERSATION_ID'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Verify user is a member of the conversation
        conversation = get_object_or_404(Conversation, id=conversation_id)
        if not conversation.members.filter(user=request.user).exists():
            return Response(
                {'error': 'You are not a member of this conversation', 'error_code': 'NOT_MEMBER'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Parse attachments data if provided as JSON string
        if attachments_data and isinstance(attachments_data, str):
            try:
                attachments_data = json.loads(attachments_data)
            except json.JSONDecodeError:
                return Response(
                    {'error': 'Invalid attachments_data format', 'error_code': 'INVALID_DATA'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # Get all uploaded files
        files = request.FILES.getlist('files')
        if not files:
            return Response(
                {'error': 'No files provided', 'error_code': 'NO_FILES'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate all files
        validated_attachments = []
        for idx, file in enumerate(files):
            is_valid, error_code, error_message, attachment_type = FileUploadValidator.validate(file)
            
            if not is_valid:
                return Response(
                    {'error': f'File {idx + 1}: {error_message}', 'error_code': error_code},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get metadata for this attachment if provided
            attachment_metadata = {}
            if attachments_data and idx < len(attachments_data):
                attachment_metadata = attachments_data[idx]

            final_file = file
            final_size = file.size
            final_duration = None

            if attachment_type == 'video':
                trim_start = attachment_metadata.get('trim_start', 0)
                trim_end = attachment_metadata.get('trim_end', 0)
                is_trimmed = attachment_metadata.get('is_trimmed', False)
                is_muted = attachment_metadata.get('is_muted', False)
                rotation = attachment_metadata.get('rotation', 0)

                # Keep original file temporarily for immediate response, process in background via Celery
                final_file = file
                final_size = file.size
                final_duration = None
            
            validated_attachments.append({
                'file': final_file,
                'file_type': attachment_type,
                'caption': attachment_metadata.get('caption', ''),
                'order': attachment_metadata.get('order', idx),
                'size': final_size,
                'duration': final_duration,
                'meta': {
                    'trim_start': attachment_metadata.get('trim_start', 0),
                    'trim_end': attachment_metadata.get('trim_end', 0),
                    'is_trimmed': attachment_metadata.get('is_trimmed', False),
                    'is_muted': attachment_metadata.get('is_muted', False),
                    'rotation': attachment_metadata.get('rotation', 0)
                } if attachment_type == 'video' else {}
            })
        
        # Partition validated attachments into:
        # 1. Photos & Videos (Grouped Collage or Single Media)
        # 2. Documents (Each document gets its OWN dedicated card / bubble)
        # 3. Audio (Each audio track gets its OWN dedicated bubble)
        media_group_attachments = [a for a in validated_attachments if a['file_type'] in ['image', 'video']]
        document_attachments = [a for a in validated_attachments if a['file_type'] == 'document']
        audio_attachments = [a for a in validated_attachments if a['file_type'] == 'audio']

        created_messages = []

        # 1. Handle Images & Videos
        if media_group_attachments:
            if len(media_group_attachments) == 1:
                single_att = media_group_attachments[0]
                caption_text = single_att['caption'] or global_caption or ''
                media_msg = Message.objects.create(
                    conversation=conversation,
                    sender=request.user,
                    attachment=single_att['file'],
                    attachment_type=single_att['file_type'],
                    message_type='media',
                    content=caption_text,
                    global_caption=caption_text
                )
                try:
                    single_rec = MessageAttachment.objects.create(
                        message=media_msg,
                        file=media_msg.attachment,
                        file_type=single_att['file_type'],
                        caption=caption_text,
                        order=0,
                        size=single_att['size'],
                        duration=single_att.get('duration'),
                        processing_status='pending' if single_att['file_type'] == 'video' else 'ready',
                    )
                    if single_att['file_type'] == 'video':
                        from .tasks import process_video_attachment_task
                        m = single_att.get('meta', {})
                        try:
                            res = process_video_attachment_task.apply_async(
                                kwargs=dict(
                                    attachment_id=single_rec.id,
                                    trim_start=m.get('trim_start', 0),
                                    trim_end=m.get('trim_end', 0),
                                    is_muted=m.get('is_muted', False),
                                    rotation=m.get('rotation', 0),
                                    is_trimmed=m.get('is_trimmed', False),
                                ),
                                queue='media_queue',
                            )
                            single_rec.celery_task_id = getattr(res, 'id', None)
                            single_rec.save(update_fields=['celery_task_id'])
                        except Exception as e:
                            logger.error(f"Video task enqueue failed, marking failed: {e}")
                            single_rec.processing_status = 'failed'
                            single_rec.processing_error = 'ENQUEUE_FAILED'
                            single_rec.save(update_fields=['processing_status', 'processing_error'])
                except Exception as ma_err:
                    logger.warning(f"Secondary MessageAttachment failed: {ma_err}")
                created_messages.append(media_msg)
            else:
                group_msg = Message.objects.create(
                    conversation=conversation,
                    sender=request.user,
                    global_caption=global_caption,
                    message_type='media_group',
                    content=global_caption or ''
                )
                for order_idx, att in enumerate(media_group_attachments):
                    created_att = MessageAttachment.objects.create(
                        message=group_msg,
                        file=att['file'],
                        file_type=att['file_type'],
                        caption=att['caption'],
                        order=order_idx,
                        size=att['size'],
                        duration=att.get('duration'),
                        processing_status='pending' if att['file_type'] == 'video' else 'ready',
                    )
                    if att['file_type'] == 'video':
                        from .tasks import process_video_attachment_task
                        m = att.get('meta', {})
                        try:
                            res = process_video_attachment_task.apply_async(
                                kwargs=dict(
                                    attachment_id=created_att.id,
                                    trim_start=m.get('trim_start', 0),
                                    trim_end=m.get('trim_end', 0),
                                    is_muted=m.get('is_muted', False),
                                    rotation=m.get('rotation', 0),
                                    is_trimmed=m.get('is_trimmed', False),
                                ),
                                queue='media_queue',
                            )
                            created_att.celery_task_id = getattr(res, 'id', None)
                            created_att.save(update_fields=['celery_task_id'])
                        except Exception as e:
                            logger.error(f"Video task enqueue failed, marking failed: {e}")
                            created_att.processing_status = 'failed'
                            created_att.processing_error = 'ENQUEUE_FAILED'
                            created_att.save(update_fields=['processing_status', 'processing_error'])
                    if order_idx == 0:
                        group_msg.attachment = created_att.file
                        group_msg.attachment_type = created_att.file_type
                group_msg.save(update_fields=['attachment', 'attachment_type'])
                created_messages.append(group_msg)

        # 2. Handle Documents (Each document gets its OWN card / bubble)
        for doc_att in document_attachments:
            doc_msg = Message.objects.create(
                conversation=conversation,
                sender=request.user,
                attachment=doc_att['file'],
                attachment_type='document',
                message_type='media',
                content=doc_att['caption'] or doc_att['file'].name
            )
            try:
                MessageAttachment.objects.create(
                    message=doc_msg,
                    file=doc_msg.attachment,
                    file_type='document',
                    caption=doc_att['caption'],
                    order=0,
                    size=doc_att['size']
                )
            except Exception as ma_err:
                logger.warning(f"Secondary MessageAttachment failed for document: {ma_err}")
            created_messages.append(doc_msg)

        # 3. Handle Audio (Each audio gets its OWN bubble)
        for audio_att in audio_attachments:
            is_vn = audio_att['file'].name.startswith('voice_')
            audio_msg = Message.objects.create(
                conversation=conversation,
                sender=request.user,
                attachment=audio_att['file'],
                attachment_type='voice_note' if is_vn else 'audio',
                message_type='voice_note' if is_vn else 'audio',
                content=audio_att['caption'] or audio_att['file'].name
            )
            try:
                MessageAttachment.objects.create(
                    message=audio_msg,
                    file=audio_msg.attachment,
                    file_type='audio',
                    caption=audio_att['caption'],
                    order=0,
                    size=audio_att['size'],
                    duration=audio_att.get('duration')
                )
            except Exception as ma_err:
                logger.warning(f"Secondary MessageAttachment failed for audio: {ma_err}")
            created_messages.append(audio_msg)

        # Update conversation timestamp
        conversation.save()

        # Broadcast all created messages via WebSocket and dispatch notifications
        channel_layer = get_channel_layer()
        for idx, msg in enumerate(created_messages):
            msg_data = MessageSerializer(msg).data
            # Attach temp_id to the primary message for seamless client reconciliation
            if temp_id and idx == 0:
                msg_data['temp_id'] = temp_id
            async_to_sync(channel_layer.group_send)(
                f"chat_{conversation.id}",
                {
                    'type': 'chat_message',
                    'message': msg_data,
                    'temp_id': temp_id if idx == 0 else None
                }
            )
            ChatNotificationService.notify_new_message(msg)

        if not created_messages:
            return Response(
                {'error': 'No messages created', 'error_code': 'NO_MESSAGES'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Return single object if 1 message created, or array if multiple
        if len(created_messages) == 1:
            resp_data = MessageSerializer(created_messages[0]).data
            if temp_id:
                resp_data['temp_id'] = temp_id
            return Response(
                resp_data,
                status=status.HTTP_201_CREATED
            )
        else:
            resp_data = MessageSerializer(created_messages, many=True).data
            if temp_id and len(resp_data) > 0:
                resp_data[0]['temp_id'] = temp_id
            return Response(
                resp_data,
                status=status.HTTP_201_CREATED
            )
        
    except Exception as e:
        return Response(
            {'error': str(e), 'error_code': 'SERVER_ERROR'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def fetch_link_metadata(request):
    """Fetch OpenGraph metadata for a URL."""
    try:
        url = request.data.get('url')
        if not url:
            return Response(
                {'error': 'URL is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate URL format
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return Response(
                {'error': 'Invalid URL format'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Detect link type
        link_type = detect_link_type(url)
        
        # For internal links, we can skip external fetching
        if link_type in ['internal_post', 'internal_profile']:
            # TODO: Fetch internal metadata from database
            return Response({
                'url': url,
                'title': 'Internal Link',
                'description': 'View this content',
                'image': None,
                'type': link_type
            })
        
        # Fetch page content
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            html = response.text
        except requests.RequestException as e:
            return Response(
                {'error': f'Failed to fetch URL: {str(e)}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Extract OpenGraph metadata
        metadata = extract_opengraph_metadata(html, url)
        metadata['type'] = link_type
        
        return Response(metadata, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


def detect_link_type(url):
    """Detect the type of link based on URL patterns."""
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    
    # Internal links
    if domain in ['localhost', '127.0.0.1'] or domain.endswith('.local'):
        if '/post/' in url or '/p/' in url:
            return 'internal_post'
        elif '/profile/' in url or '/u/' in url:
            return 'internal_profile'
    
    # Social media platforms
    if 'facebook.com' in domain or 'fb.com' in domain:
        return 'facebook'
    elif 'youtube.com' in domain or 'youtu.be' in domain:
        return 'youtube'
    elif 'instagram.com' in domain:
        return 'instagram'
    elif 'twitter.com' in domain or 'x.com' in domain:
        return 'twitter'
    
    return 'link'


def extract_opengraph_metadata(html, url):
    """Extract OpenGraph metadata from HTML."""
    metadata = {
        'url': url,
        'title': None,
        'description': None,
        'image': None
    }
    
    # Extract title
    title_match = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if not title_match:
        title_match = re.search(r'<title>([^<]+)</title>', html, re.IGNORECASE)
    if title_match:
        metadata['title'] = title_match.group(1).strip()
    
    # Extract description
    desc_match = re.search(r'<meta[^>]*property=["\']og:description["\'][^>]*content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if not desc_match:
        desc_match = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if desc_match:
        metadata['description'] = desc_match.group(1).strip()
    
    # Extract image
    image_match = re.search(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if image_match:
        image_url = image_match.group(1).strip()
        # Make image URL absolute if it's relative
        if image_url.startswith('//'):
            image_url = 'https:' + image_url
        elif image_url.startswith('/'):
            parsed = urlparse(url)
            image_url = f"{parsed.scheme}://{parsed.netloc}{image_url}"
        metadata['image'] = image_url
    
    # Fallback to URL as title if no title found
    if not metadata['title']:
        metadata['title'] = url
    
    return metadata


def unread_message_count(request):
    """Return HTML for unread message count badge (similar to notifications)."""
    if not request.user or not request.user.is_authenticated:
        return HttpResponse('<i class="bi bi-chat-dots-fill"></i>')
    from .context_processors import get_cached_unread_message_count
    total_unread = get_cached_unread_message_count(request.user)

    # Build HTML similar to notification badge
    html = '<i class="bi bi-chat-dots-fill"></i>'
    if total_unread > 0:
        html += f'''
            <span class="position-absolute top-0 end-0 translate-middle badge rounded-pill bg-danger border border-light"
                style="font-size: 0.6rem; padding: 0.35em 0.5em; min-width: 18px; text-align: center; margin-top: -2px; margin-right: -2px;">
                {total_unread}
                <span class="visually-hidden">unread messages</span>
            </span>'''

    return HttpResponse(html)


def proxy_chat_media(request):
    """
    Stream media attachments through same-origin for reliable client-side caching (IndexedDB/offline).
    Avoids CORS ERR_FAILED when testing or accessing via LAN IPs or origins not in CDN's CORS policy.
    Compatible with both WSGI and ASGI without event loop conflicts.
    """
    media_url = request.GET.get('url', '').strip()
    if not media_url:
        return HttpResponse('Missing url parameter', status=400)
    
    if media_url.startswith('/media/'):
        from django.conf import settings
        media_url = f"{settings.MEDIA_URL.rstrip('/')}/{media_url.lstrip('/media/')}"
    
    parsed = urlparse(media_url)
    if parsed.scheme not in ('http', 'https'):
        return HttpResponse('Invalid URL scheme', status=400)
    
    from django.conf import settings
    allowed_domains = {settings.CDN_DOMAIN} if hasattr(settings, 'CDN_DOMAIN') and settings.CDN_DOMAIN else set()
    allowed_domains.update(['cdn.pwaninet.app', 'pwaninet.app', 'localhost', '127.0.0.1'])
    if parsed.hostname not in allowed_domains and not (parsed.hostname and parsed.hostname.endswith('.pwaninet.app')):
        return HttpResponse('Domain not allowed for proxying', status=403)
        
    try:
        req = requests.get(media_url, stream=True, timeout=25)
        if req.status_code != 200:
            return HttpResponse(f'Upstream error: {req.status_code}', status=req.status_code)
            
        content_type = req.headers.get('Content-Type', 'application/octet-stream')
        
        def stream_content():
            try:
                for chunk in req.iter_content(chunk_size=65536):
                    if chunk:
                        yield chunk
            finally:
                req.close()

        response = StreamingHttpResponse(
            stream_content(),
            content_type=content_type,
            status=200
        )
        # Note: Do NOT set Content-Length on StreamingHttpResponse; chunked transfer encoding is used in ASGI/HTTP.
        response['Access-Control-Allow-Origin'] = '*'
        response['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response['Access-Control-Allow-Headers'] = '*'
        response['Cache-Control'] = 'public, max-age=604800'
        return response
    except Exception as e:
        logger.warning(f'Failed to proxy media URL {media_url}: {e}')
        return HttpResponse('Failed to fetch upstream media', status=502)
