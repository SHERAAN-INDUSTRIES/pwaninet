"""
Elite Chat Notification Service for vasto (WhatsApp / Telegram / Messenger / Instagram Standard).

Provides a single unified pipeline for:
1. Rich message preview generation (Text, Photo, Multi-Photo Album, Video, Voice Note with duration,
   Audio, Document, Sticker, GIF, Link, Forwarded, Reply, @Mention, Reaction, Deleted, Encrypted).
2. Recipient resolution with block filtering, mute enforcement, and WhatsApp/Telegram-style
   mute override when a user is directly replied to or @mentioned in a group.
3. Real-time WebSocket broadcasts (`conversation_update`) to `notifications_{user_id}` driving:
   - Global `.messages-badge` across every page in vasto
   - Real-time conversation list ordering, rich snippets, and dynamic new-conversation insertion
   - In-app floating chat banner (when online outside the active conversation)
4. Grouped, stacked OS Push Notifications (WebPush + Native Android/iOS FCM) on the
   `vasto_messages` channel with per-conversation thread tags (`vasto-chat-{conversation_id}`).
"""

import json
import logging
import re
import threading
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from messaging.context_processors import (
    get_cached_unread_message_count,
    invalidate_unread_message_count_cache,
)
from messaging.models import Conversation, ConversationMember, Message
from messaging.presence import PresenceService

logger = logging.getLogger(__name__)

DEFAULT_AVATAR_PATH = '/static/images/web-app-manifest-192x192-rounded.png'


class ChatNotificationService:
    """Unified notification and real-time inbox service for vasto Messaging."""

    # ------------------------------------------------------------------
    # 1. Identity & URL Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def get_user_display_name(user) -> str:
        """Return user's full name if set, otherwise username."""
        if not user:
            return 'vasto User'
        try:
            full_name = (user.get_full_name() or '').strip()
            if full_name and full_name.lower() != 'none none':
                return full_name
        except Exception:
            pass
        return getattr(user, 'username', None) or 'vasto User'

    @staticmethod
    def get_user_avatar_url(user) -> str:
        """Return user's profile picture URL or fallback icon."""
        if not user:
            return DEFAULT_AVATAR_PATH
        try:
            if getattr(user, 'profile_pic', None) and user.profile_pic.name:
                return user.profile_pic.url
        except Exception:
            pass
        return DEFAULT_AVATAR_PATH

    @staticmethod
    def make_absolute_url(url: Optional[str]) -> Optional[str]:
        """Convert relative paths into absolute HTTPS URLs for WebPush / FCM."""
        if not url:
            return None
        url_str = str(url).strip()
        if not url_str:
            return None
        if url_str.startswith(('http://', 'https://')):
            return url_str
        base_url = (
            getattr(settings, 'SITE_URL', '')
            or getattr(settings, 'BASE_URL', '')
            or 'https://pwaninet.app'
        ).rstrip('/')
        if not url_str.startswith('/'):
            url_str = '/' + url_str
        return f"{base_url}{url_str}"

    @staticmethod
    def format_duration(seconds: Optional[float]) -> Optional[str]:
        """Format duration in seconds (e.g. 14.2 -> '0:14')."""
        if seconds is None:
            return None
        try:
            total_sec = max(0, int(round(float(seconds))))
            mins, secs = divmod(total_sec, 60)
            return f"{mins}:{secs:02d}"
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------
    # 2. Rich Preview Builder (WhatsApp / Telegram / Instagram Standard)
    # ------------------------------------------------------------------

    @classmethod
    def build_message_preview(
        cls,
        message: Message,
        include_forwarded_prefix: bool = True,
    ) -> Dict[str, Any]:
        """
        Build a structured rich preview for a Message across all content types.

        Returns dict:
        {
            'text': str,           # Sidebar snippet text (with emoji prefix)
            'clean_text': str,     # Text without emoji prefix (for icon-rendered DOM)
            'push_text': str,      # Push / banner body text
            'type': str,           # 'text'|'image'|'video'|'audio'|'document'|'sticker'|'gif'|'link'|'deleted'|'encrypted'
            'icon': str | None,    # Bootstrap icon class for sidebar
            'thumbnail_url': str | None,  # Media preview thumbnail URL if applicable
        }
        """
        if not message:
            return {
                'text': 'New message',
                'clean_text': 'New message',
                'push_text': 'New message',
                'type': 'text',
                'icon': None,
                'thumbnail_url': None,
            }

        # 1. Soft-deleted message
        if getattr(message, 'is_deleted', False):
            return {
                'text': '🚫 This message was deleted',
                'clean_text': 'This message was deleted',
                'push_text': '🚫 This message was deleted',
                'type': 'deleted',
                'icon': 'bi-slash-circle',
                'thumbnail_url': None,
            }

        raw_content = (getattr(message, 'content', None) or '').strip()
        global_caption = (getattr(message, 'global_caption', None) or '').strip()
        caption_or_text = raw_content or global_caption

        # 2. Encrypted message without plaintext fallback
        if getattr(message, 'is_encrypted', False) and not caption_or_text:
            return {
                'text': '🔒 Encrypted message',
                'clean_text': 'Encrypted message',
                'push_text': '🔒 New message',
                'type': 'encrypted',
                'icon': 'bi-lock-fill',
                'thumbnail_url': None,
            }

        msg_type = (getattr(message, 'message_type', None) or 'text').lower()
        att_type = (getattr(message, 'attachment_type', None) or '').lower()

        # Fetch attachments safely (uses prefetch cache if available)
        attachments = []
        try:
            attachments = list(message.attachments.all())
        except Exception:
            attachments = []

        first_att = attachments[0] if attachments else None
        thumbnail_url = None

        # Detect voice note
        att_name = ''
        if getattr(message, 'attachment', None) and getattr(message.attachment, 'name', None):
            att_name = message.attachment.name.split('/')[-1]
        elif first_att and getattr(first_att, 'file', None) and getattr(first_att.file, 'name', None):
            att_name = first_att.file.name.split('/')[-1]

        is_voice_note = (
            msg_type in ('voice_note', 'voice')
            or att_type in ('voice_note', 'voice')
            or att_name.startswith('voice_')
        )

        # Resolve duration if voice note / audio
        duration_str = None
        if first_att and getattr(first_att, 'duration', None):
            duration_str = cls.format_duration(first_att.duration)

        # 3. Sticker / GIF
        if msg_type == 'sticker' or att_type == 'sticker' or getattr(message, 'link_type', '') == 'sticker':
            thumbnail_url = getattr(message, 'link_image', None)
            res = {
                'text': '😊 Sticker',
                'clean_text': 'Sticker',
                'push_text': '😊 Sticker',
                'type': 'sticker',
                'icon': 'bi-emoji-smile',
                'thumbnail_url': thumbnail_url,
            }
        elif msg_type == 'gif' or att_type == 'gif' or getattr(message, 'link_type', '') == 'gif':
            thumbnail_url = getattr(message, 'link_image', None)
            res = {
                'text': '🎬 GIF',
                'clean_text': 'GIF',
                'push_text': '🎬 GIF',
                'type': 'gif',
                'icon': 'bi-filetype-gif',
                'thumbnail_url': thumbnail_url,
            }

        # 4. Voice Note
        elif is_voice_note:
            dur_suffix = f" ({duration_str})" if duration_str else ""
            label = f"Voice message{dur_suffix}"
            res = {
                'text': f"🎤 {label}",
                'clean_text': label,
                'push_text': f"🎤 {label}",
                'type': 'audio',
                'icon': 'bi-mic-fill',
                'thumbnail_url': None,
            }

        # 5. Audio File
        elif msg_type == 'audio' or att_type == 'audio' or (first_att and first_att.file_type == 'audio'):
            clean_label = caption_or_text or att_name or 'Audio'
            if len(clean_label) > 60:
                clean_label = clean_label[:57] + '...'
            res = {
                'text': f"🎵 {clean_label}",
                'clean_text': clean_label,
                'push_text': f"🎵 {clean_label}",
                'type': 'audio',
                'icon': 'bi-music-note-beamed',
                'thumbnail_url': None,
            }

        # 6. Multi-Photo / Media Album
        elif msg_type in ('media_group', 'album') or len(attachments) > 1:
            img_count = sum(1 for a in attachments if a.file_type == 'image')
            vid_count = sum(1 for a in attachments if a.file_type == 'video')
            total_count = len(attachments) or 2

            # Pick first image attachment as thumbnail
            for a in attachments:
                if a.file_type == 'image' and getattr(a, 'file', None):
                    try:
                        thumbnail_url = a.file.url
                        break
                    except Exception:
                        pass

            if caption_or_text:
                short_cap = caption_or_text[:70] + ('...' if len(caption_or_text) > 70 else '')
                clean_label = short_cap
                emoji_prefix = '🎥' if (vid_count > 0 and img_count == 0) else '📷'
            else:
                if img_count == total_count:
                    clean_label = f"{total_count} photos"
                    emoji_prefix = '📷'
                elif vid_count == total_count:
                    clean_label = f"{total_count} videos"
                    emoji_prefix = '🎥'
                else:
                    clean_label = f"{total_count} media items"
                    emoji_prefix = '📷'

            res = {
                'text': f"{emoji_prefix} {clean_label}",
                'clean_text': clean_label,
                'push_text': f"{emoji_prefix} {clean_label}",
                'type': 'image' if img_count >= vid_count else 'video',
                'icon': 'bi-images',
                'thumbnail_url': thumbnail_url,
            }

        # 7. Single Photo
        elif att_type == 'image' or (first_att and first_att.file_type == 'image'):
            try:
                if getattr(message, 'attachment', None) and message.attachment.name:
                    thumbnail_url = message.attachment.url
                elif first_att and getattr(first_att, 'file', None):
                    thumbnail_url = first_att.file.url
            except Exception:
                thumbnail_url = None

            clean_label = (caption_or_text[:70] + ('...' if len(caption_or_text) > 70 else '')) if caption_or_text else 'Photo'
            res = {
                'text': f"📷 {clean_label}",
                'clean_text': clean_label,
                'push_text': f"📷 {clean_label}",
                'type': 'image',
                'icon': 'bi-camera-fill',
                'thumbnail_url': thumbnail_url,
            }

        # 8. Single Video
        elif att_type == 'video' or (first_att and first_att.file_type == 'video'):
            clean_label = (caption_or_text[:70] + ('...' if len(caption_or_text) > 70 else '')) if caption_or_text else 'Video'
            res = {
                'text': f"🎥 {clean_label}",
                'clean_text': clean_label,
                'push_text': f"🎥 {clean_label}",
                'type': 'video',
                'icon': 'bi-camera-video-fill',
                'thumbnail_url': None,
            }

        # 9. Document
        elif att_type == 'document' or (first_att and first_att.file_type == 'document'):
            clean_label = caption_or_text or att_name or 'Document'
            if len(clean_label) > 65:
                clean_label = clean_label[:62] + '...'
            res = {
                'text': f"📄 {clean_label}",
                'clean_text': clean_label,
                'push_text': f"📄 {clean_label}",
                'type': 'document',
                'icon': 'bi-file-earmark-text-fill',
                'thumbnail_url': None,
            }

        # 10. Link Message
        elif getattr(message, 'link_url', None) or getattr(message, 'link_preview_id', None):
            link_url = getattr(message, 'link_url', '') or ''
            link_title = getattr(message, 'link_title', None)
            link_preview_obj = getattr(message, 'link_preview', None)
            if link_preview_obj:
                if not link_title and link_preview_obj.title:
                    link_title = link_preview_obj.title
                if link_preview_obj.thumbnail_url:
                    thumbnail_url = link_preview_obj.thumbnail_url
            if not thumbnail_url and getattr(message, 'link_image', None):
                thumbnail_url = message.link_image

            domain = ''
            if link_url:
                try:
                    domain = urlparse(link_url).netloc
                except Exception:
                    domain = link_url

            # If message content has user commentary beyond just the raw URL, show commentary
            if caption_or_text and caption_or_text != link_url:
                clean_label = caption_or_text[:90] + ('...' if len(caption_or_text) > 90 else '')
            else:
                clean_label = link_title or domain or link_url or 'Shared link'
                if len(clean_label) > 75:
                    clean_label = clean_label[:72] + '...'

            res = {
                'text': f"🔗 {clean_label}",
                'clean_text': clean_label,
                'push_text': f"🔗 {clean_label}",
                'type': 'link',
                'icon': 'bi-link-45deg',
                'thumbnail_url': thumbnail_url,
            }

        # 11. Standard Text Message
        else:
            clean_label = caption_or_text or 'New message'
            if len(clean_label) > 120:
                clean_label = clean_label[:117] + '...'
            res = {
                'text': clean_label,
                'clean_text': clean_label,
                'push_text': clean_label,
                'type': 'text',
                'icon': None,
                'thumbnail_url': None,
            }

        # Apply Forwarded prefix if applicable
        if include_forwarded_prefix and getattr(message, 'is_forwarded', False):
            res['text'] = f"↪️ {res['text']}"
            res['push_text'] = f"↪️ Forwarded: {res['push_text']}"

        return res

    # ------------------------------------------------------------------
    # 3. Recipient & Mention / Reply / Mute Evaluation
    # ------------------------------------------------------------------

    @staticmethod
    def is_user_mentioned(content: str, user) -> bool:
        """Return True if `@username` appears in message content."""
        if not content or not user or not getattr(user, 'username', None):
            return False
        pattern = rf'(?:^|[\s(])@{re.escape(user.username)}\b'
        return bool(re.search(pattern, content, flags=re.IGNORECASE))

    @staticmethod
    def get_blocked_user_ids(user_id: int) -> set:
        """Return set of user IDs that have blocked or been blocked by `user_id`."""
        if not user_id:
            return set()
        try:
            from users.models import Block
            pairs = Block.objects.filter(
                Q(blocker_id=user_id) | Q(blocked_id=user_id)
            ).values_list('blocker_id', 'blocked_id')
            blocked = set()
            for blocker_id, blocked_id in pairs:
                if blocker_id != user_id:
                    blocked.add(blocker_id)
                if blocked_id != user_id:
                    blocked.add(blocked_id)
            return blocked
        except Exception:
            return set()

    @staticmethod
    def get_conversation_unread_count(conversation_id: int, user_id: int, last_read_message_id: Optional[int] = None) -> int:
        """Compute accurate unread message count for a user in a specific conversation (excluding deleted messages)."""
        try:
            qs = Message.objects.filter(
                conversation_id=conversation_id,
                is_deleted=False,
            ).exclude(sender_id=user_id)
            if last_read_message_id:
                qs = qs.filter(id__gt=last_read_message_id)
            return qs.count()
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # 4. Event Dispatchers (New Message, Reaction, Delete, Edit, Member Added)
    # ------------------------------------------------------------------

    @classmethod
    def notify_new_message(cls, message: Message) -> None:
        """
        Primary entry point when any message (text, media, voice note, album, forward) is created.
        Updates unread caches, broadcasts `conversation_update` to all members, and dispatches Push.
        """
        if not message or not message.conversation_id:
            return

        try:
            conversation = (
                Conversation.objects.prefetch_related(
                    models.Prefetch(
                        'members',
                        queryset=ConversationMember.objects.select_related('user'),
                    )
                )
                .filter(id=message.conversation_id)
                .first()
            )
            if not conversation:
                return

            sender = message.sender
            sender_id = sender.id
            sender_username = sender.username
            sender_display_name = cls.get_user_display_name(sender)
            sender_avatar = cls.get_user_avatar_url(sender)

            is_group = conversation.type == Conversation.GROUP
            group_name = (conversation.name or f"Group ({conversation.id})") if is_group else None

            preview = cls.build_message_preview(message, include_forwarded_prefix=True)
            raw_text = (message.content or message.global_caption or '').strip()

            # Check reply target author
            reply_author_id = None
            if message.reply_to_id:
                try:
                    if getattr(message, 'reply_to', None):
                        reply_author_id = message.reply_to.sender_id
                    else:
                        reply_author_id = (
                            Message.objects.filter(id=message.reply_to_id)
                            .values_list('sender_id', flat=True)
                            .first()
                        )
                except Exception:
                    reply_author_id = None

            blocked_ids = cls.get_blocked_user_ids(sender_id)
            members = list(conversation.members.all())
            if conversation.group_id:
                from groups.models import Membership, MembershipStatus
                approved = set(Membership.objects.filter(
                    group_id=conversation.group_id, status=MembershipStatus.APPROVED,
                ).values_list('user_id', flat=True))
                members = [member for member in members if member.user_id in approved]

            # Identify peer user for 1:1 conversations (so sender's sidebar row has peer name/avatar)
            peer_member = next((m for m in members if m.user_id != sender_id), None)
            peer_display_name = cls.get_user_display_name(peer_member.user) if peer_member else sender_display_name
            peer_avatar = cls.get_user_avatar_url(peer_member.user) if peer_member else sender_avatar
            peer_username = peer_member.user.username if peer_member else sender_username

            channel_layer = get_channel_layer()
            push_jobs = []

            for member in members:
                uid = member.user_id
                user_obj = member.user

                # Always invalidate cached total unread count for every participant
                invalidate_unread_message_count_cache(uid)
                total_unread = get_cached_unread_message_count(user_obj)

                # Case A: The Sender
                if uid == sender_id:
                    if channel_layer:
                        conv_title = group_name if is_group else peer_display_name
                        conv_avatar = DEFAULT_AVATAR_PATH if is_group else peer_avatar
                        async_to_sync(channel_layer.group_send)(
                            f"notifications_{uid}",
                            {
                                'type': 'conversation_update',
                                'event_kind': 'message',
                                'conversation_id': int(conversation.id),
                                'conversation_type': conversation.type,
                                'conversation_name': conv_title,
                                'conversation_avatar': conv_avatar,
                                'peer_id': peer_member.user_id if peer_member else None,
                                'peer_username': peer_username,
                                'message_id': message.id,
                                'message_preview': f"You: {preview['text']}",
                                'clean_preview': f"You: {preview['clean_text']}",
                                'preview_type': preview['type'],
                                'preview_icon': preview['icon'],
                                'thumbnail_url': preview['thumbnail_url'],
                                'sender_name': 'You',
                                'sender_display_name': 'You',
                                'sender_avatar': sender_avatar,
                                'sender_id': sender_id,
                                'is_sender': True,
                                'is_muted': bool(member.is_muted),
                                'is_pinned': bool(member.is_pinned),
                                'should_alert': False,
                                'timestamp': message.created_at.isoformat(),
                                'unread_count': 0,
                                'total_unread_count': total_unread,
                                'target_url': f"/messaging/conversation/{conversation.id}/",
                            },
                        )
                    continue

                # Case B: Blocked relationship -> skip recipient completely
                if uid in blocked_ids:
                    continue

                # Compute recipient's unread count for this conversation
                conv_unread = cls.get_conversation_unread_count(
                    conversation.id,
                    uid,
                    member.last_read_message_id,
                )

                # Evaluate direct reply & @mention override
                is_reply_to_recipient = bool(reply_author_id and reply_author_id == uid)
                is_mention = cls.is_user_mentioned(raw_text, user_obj)
                is_mention_or_reply = is_reply_to_recipient or is_mention

                # Mute rule: Muted conversations suppress alerts UNLESS the user was directly
                # replied to or @mentioned in a group chat (WhatsApp / Telegram standard)
                is_muted = bool(member.is_muted)
                should_alert = (not is_muted) or (is_group and is_mention_or_reply)

                # Format sidebar snippet & banner copy
                if is_group:
                    sidebar_preview = f"{sender_username}: {preview['text']}"
                    clean_sidebar_preview = f"{sender_username}: {preview['clean_text']}"
                    banner_title = group_name
                    if is_reply_to_recipient:
                        banner_body = f'{sender_display_name} replied to you: "{preview["push_text"]}"'
                    elif is_mention:
                        banner_body = f'@{sender_username} mentioned you: "{preview["push_text"]}"'
                    else:
                        banner_body = f'{sender_display_name}: {preview["push_text"]}'
                else:
                    sidebar_preview = preview['text']
                    clean_sidebar_preview = preview['clean_text']
                    banner_title = sender_display_name
                    if is_reply_to_recipient:
                        banner_body = f'↩️ Replied to you: "{preview["push_text"]}"'
                    else:
                        banner_body = preview['push_text']

                if channel_layer:
                    async_to_sync(channel_layer.group_send)(
                        f"notifications_{uid}",
                        {
                            'type': 'conversation_update',
                            'event_kind': 'message',
                            'conversation_id': int(conversation.id),
                            'conversation_type': conversation.type,
                            'conversation_name': group_name if is_group else sender_display_name,
                            'conversation_avatar': sender_avatar,
                            'peer_id': sender_id if not is_group else None,
                            'peer_username': sender_username if not is_group else None,
                            'message_id': message.id,
                            'message_preview': sidebar_preview,
                            'clean_preview': clean_sidebar_preview,
                            'banner_title': banner_title,
                            'banner_body': banner_body,
                            'preview_type': preview['type'],
                            'preview_icon': preview['icon'],
                            'thumbnail_url': preview['thumbnail_url'],
                            'sender_name': sender_username,
                            'sender_display_name': sender_display_name,
                            'sender_avatar': sender_avatar,
                            'sender_id': sender_id,
                            'is_sender': False,
                            'is_muted': is_muted,
                            'is_pinned': bool(member.is_pinned),
                            'is_mention_or_reply': is_mention_or_reply,
                            'should_alert': should_alert,
                            'timestamp': message.created_at.isoformat(),
                            'unread_count': conv_unread,
                            'total_unread_count': total_unread,
                            'target_url': f"/messaging/conversation/{conversation.id}/",
                        },
                    )

                # Queue OS Push Notification if allowed and user is not actively focused in this conversation
                if should_alert and not PresenceService.is_user_in_conversation(uid, conversation.id):
                    push_jobs.append({
                        'recipient': user_obj,
                        'conversation': conversation,
                        'last_read_message_id': member.last_read_message_id,
                        'conv_unread': conv_unread,
                        'banner_title': banner_title,
                        'banner_body': banner_body,
                        'sender_avatar': sender_avatar,
                        'thumbnail_url': preview['thumbnail_url'],
                        'preview_type': preview['type'],
                        'is_group': is_group,
                        'group_name': group_name,
                        'sender_display_name': sender_display_name,
                        'is_encrypted_locked': bool(getattr(message, 'is_encrypted', False) and not raw_text),
                    })

            if push_jobs:
                cls._dispatch_push_jobs_async(push_jobs)

        except Exception as e:
            logger.error(f"[ChatNotificationService] Error in notify_new_message: {e}", exc_info=True)

    @classmethod
    def notify_reaction(cls, message: Message, reactor, emoji: str) -> None:
        """
        Notify ONLY the author of the message being reacted to (never the reactor or whole group).
        Does not increment unread message counts.
        """
        if not message or not reactor or not emoji:
            return
        if message.sender_id == reactor.id:
            return  # Never notify on self-reaction

        try:
            conversation = message.conversation
            recipient = message.sender
            recipient_id = recipient.id

            # Check block
            if recipient_id in cls.get_blocked_user_ids(reactor.id):
                return

            # Check membership & mute
            member = ConversationMember.objects.filter(
                conversation_id=conversation.id,
                user_id=recipient_id,
            ).first()
            if not member or member.is_muted:
                return

            reactor_display_name = cls.get_user_display_name(reactor)
            reactor_avatar = cls.get_user_avatar_url(reactor)
            is_group = conversation.type == Conversation.GROUP
            group_name = (conversation.name or f"Group ({conversation.id})") if is_group else None

            msg_preview = cls.build_message_preview(message, include_forwarded_prefix=False)
            snippet_target = msg_preview['push_text']
            if len(snippet_target) > 45:
                snippet_target = snippet_target[:42] + '...'

            if is_group:
                banner_title = group_name
                banner_body = f'{reactor_display_name} reacted {emoji} to "{snippet_target}"'
            else:
                banner_title = reactor_display_name
                banner_body = f'Reacted {emoji} to "{snippet_target}"'

            conv_unread = cls.get_conversation_unread_count(
                conversation.id,
                recipient_id,
                member.last_read_message_id,
            )
            total_unread = get_cached_unread_message_count(recipient)

            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(
                    f"notifications_{recipient_id}",
                    {
                        'type': 'conversation_update',
                        'event_kind': 'reaction',
                        'conversation_id': int(conversation.id),
                        'conversation_type': conversation.type,
                        'conversation_name': banner_title,
                        'conversation_avatar': reactor_avatar,
                        'message_id': message.id,
                        'message_preview': banner_body,
                        'clean_preview': banner_body,
                        'banner_title': banner_title,
                        'banner_body': banner_body,
                        'preview_type': 'reaction',
                        'preview_icon': 'bi-heart-fill',
                        'thumbnail_url': msg_preview['thumbnail_url'],
                        'sender_name': reactor.username,
                        'sender_display_name': reactor_display_name,
                        'sender_avatar': reactor_avatar,
                        'sender_id': reactor.id,
                        'is_sender': False,
                        'is_muted': False,
                        'should_alert': True,
                        'timestamp': timezone.now().isoformat(),
                        'unread_count': conv_unread,
                        'total_unread_count': total_unread,
                        'target_url': f"/messaging/conversation/{conversation.id}/",
                    },
                )

            if not PresenceService.is_user_in_conversation(recipient_id, conversation.id):
                cls._dispatch_push_jobs_async([{
                    'recipient': recipient,
                    'conversation': conversation,
                    'last_read_message_id': member.last_read_message_id,
                    'conv_unread': 1,  # Single-line reaction push
                    'force_single_line': True,
                    'banner_title': banner_title,
                    'banner_body': banner_body,
                    'sender_avatar': reactor_avatar,
                    'thumbnail_url': msg_preview['thumbnail_url'],
                    'preview_type': 'reaction',
                    'is_group': is_group,
                    'group_name': group_name,
                    'sender_display_name': reactor_display_name,
                    'is_encrypted_locked': False,
                }])

        except Exception as e:
            logger.error(f"[ChatNotificationService] Error in notify_reaction: {e}", exc_info=True)

    @classmethod
    def notify_message_deleted(cls, message: Message, actor) -> None:
        """
        Handle 'Delete for Everyone':
        - Invalidates unread count cache for all members (since deleted messages are no longer unread)
        - Updates conversation list snippet to '🚫 This message was deleted'
        - Instructs client Service Worker to retract any open push notification for this conversation
        """
        if not message or not message.conversation_id:
            return

        try:
            conversation = (
                Conversation.objects.prefetch_related(
                    models.Prefetch(
                        'members',
                        queryset=ConversationMember.objects.select_related('user'),
                    )
                )
                .filter(id=message.conversation_id)
                .first()
            )
            if not conversation:
                return

            channel_layer = get_channel_layer()
            if not channel_layer:
                return

            for member in conversation.members.all():
                uid = member.user_id
                invalidate_unread_message_count_cache(uid)
                total_unread = get_cached_unread_message_count(member.user)
                conv_unread = cls.get_conversation_unread_count(
                    conversation.id,
                    uid,
                    member.last_read_message_id,
                )

                is_sender = (actor and uid == actor.id)
                async_to_sync(channel_layer.group_send)(
                    f"notifications_{uid}",
                    {
                        'type': 'conversation_update',
                        'event_kind': 'delete',
                        'conversation_id': int(conversation.id),
                        'conversation_type': conversation.type,
                        'message_id': message.id,
                        'message_preview': '🚫 You deleted this message' if is_sender else '🚫 This message was deleted',
                        'clean_preview': 'You deleted this message' if is_sender else 'This message was deleted',
                        'preview_type': 'deleted',
                        'preview_icon': 'bi-slash-circle',
                        'sender_id': actor.id if actor else message.sender_id,
                        'is_sender': is_sender,
                        'should_alert': False,
                        'retract_tag': f"vasto-chat-{conversation.id}",
                        'timestamp': timezone.now().isoformat(),
                        'unread_count': 0 if is_sender else conv_unread,
                        'total_unread_count': total_unread,
                        'target_url': f"/messaging/conversation/{conversation.id}/",
                    },
                )
        except Exception as e:
            logger.error(f"[ChatNotificationService] Error in notify_message_deleted: {e}", exc_info=True)

    @classmethod
    def notify_message_edited(cls, message: Message, actor) -> None:
        """
        Update sidebar preview silently when the latest message in a conversation is edited.
        Never triggers a sound, banner, or push notification.
        """
        if not message or not message.conversation_id:
            return

        try:
            conversation = (
                Conversation.objects.prefetch_related(
                    models.Prefetch(
                        'members',
                        queryset=ConversationMember.objects.select_related('user'),
                    )
                )
                .filter(id=message.conversation_id)
                .first()
            )
            if not conversation:
                return

            latest_msg_id = (
                Message.objects.filter(conversation_id=conversation.id)
                .order_by('-id')
                .values_list('id', flat=True)
                .first()
            )
            if latest_msg_id != message.id:
                return

            preview = cls.build_message_preview(message, include_forwarded_prefix=True)
            is_group = conversation.type == Conversation.GROUP
            sender_username = message.sender.username

            channel_layer = get_channel_layer()
            if not channel_layer:
                return

            for member in conversation.members.all():
                uid = member.user_id
                is_sender = (uid == message.sender_id)
                conv_unread = cls.get_conversation_unread_count(
                    conversation.id,
                    uid,
                    member.last_read_message_id,
                )
                total_unread = get_cached_unread_message_count(member.user)

                if is_sender:
                    snippet = f"You: {preview['text']}"
                elif is_group:
                    snippet = f"{sender_username}: {preview['text']}"
                else:
                    snippet = preview['text']

                async_to_sync(channel_layer.group_send)(
                    f"notifications_{uid}",
                    {
                        'type': 'conversation_update',
                        'event_kind': 'edit',
                        'conversation_id': int(conversation.id),
                        'conversation_type': conversation.type,
                        'message_id': message.id,
                        'message_preview': snippet,
                        'clean_preview': preview['clean_text'],
                        'preview_type': preview['type'],
                        'preview_icon': preview['icon'],
                        'sender_id': message.sender_id,
                        'is_sender': is_sender,
                        'should_alert': False,
                        'timestamp': (message.edited_at or timezone.now()).isoformat(),
                        'unread_count': 0 if is_sender else conv_unread,
                        'total_unread_count': total_unread,
                        'target_url': f"/messaging/conversation/{conversation.id}/",
                    },
                )
        except Exception as e:
            logger.error(f"[ChatNotificationService] Error in notify_message_edited: {e}", exc_info=True)

    @classmethod
    def notify_read_receipt(cls, conversation_id: int, reader_user) -> None:
        """
        Broadcast updated unread count (0 for this conversation + fresh total_unread_count)
        to the reader's personal `notifications_{user_id}` channel so all tabs clear `.messages-badge`.
        Also push read_receipt to every OTHER member's notification channel so
        offline senders update ticks without re-entering the chat room.
        """
        if not reader_user or not getattr(reader_user, 'is_authenticated', False):
            return
        try:
            invalidate_unread_message_count_cache(reader_user.id)
            total_unread = get_cached_unread_message_count(reader_user)
            channel_layer = get_channel_layer()
            if not channel_layer:
                return
            async_to_sync(channel_layer.group_send)(
                f"notifications_{reader_user.id}",
                {
                    'type': 'conversation_update',
                    'event_kind': 'read_receipt',
                    'conversation_id': int(conversation_id),
                    'unread_count': 0,
                    'total_unread_count': total_unread,
                    'should_alert': False,
                    'retract_tag': f"vasto-chat-{conversation_id}",
                },
            )
            try:
                from messaging.models import ConversationMember
                other_ids = list(ConversationMember.objects.filter(
                    conversation_id=conversation_id).exclude(user_id=reader_user.id
                    ).values_list('user_id', flat=True))
                member = ConversationMember.objects.filter(
                    conversation_id=conversation_id, user_id=reader_user.id).first()
                last_read_id = getattr(member, 'last_read_message_id', None)
                avatar = None
                try:
                    if getattr(reader_user, 'profile_pic', None):
                        avatar = reader_user.profile_pic.url
                except Exception:
                    avatar = None
                for oid in other_ids:
                    async_to_sync(channel_layer.group_send)(
                        f"notifications_{oid}",
                        {
                            'type': 'conversation_update',
                            'event_kind': 'read_receipt',
                            'conversation_id': int(conversation_id),
                            'user_id': reader_user.id,
                            'last_read_message_id': last_read_id,
                            'read_avatar': avatar,
                            'should_alert': False,
                        },
                    )
            except Exception as inner:
                logger.warning(f"[ChatNotificationService] sender read fan-out failed: {inner}")
        except Exception as e:
            logger.error(f"[ChatNotificationService] Error in notify_read_receipt: {e}", exc_info=True)

    @classmethod
    def notify_member_added(cls, conversation: Conversation, added_user, actor=None) -> None:
        """
        Notify ONLY the newly added user when they are added to a group conversation.
        Broadcasts a `conversation_update` so the group appears in their conversation list
        immediately and sends a push notification.
        """
        if not conversation or not added_user:
            return
        if conversation.type != Conversation.GROUP:
            return
        if actor and actor.id == added_user.id:
            return

        try:
            if actor and added_user.id in cls.get_blocked_user_ids(actor.id):
                return

            group_name = conversation.name or f"Group ({conversation.id})"
            actor_display = cls.get_user_display_name(actor) if actor else 'Someone'
            actor_avatar = cls.get_user_avatar_url(actor) if actor else DEFAULT_AVATAR_PATH
            banner_title = group_name
            banner_body = f'{actor_display} added you to "{group_name}"'

            invalidate_unread_message_count_cache(added_user.id)
            total_unread = get_cached_unread_message_count(added_user)

            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(
                    f"notifications_{added_user.id}",
                    {
                        'type': 'conversation_update',
                        'event_kind': 'member_added',
                        'conversation_id': int(conversation.id),
                        'conversation_type': conversation.type,
                        'conversation_name': group_name,
                        'conversation_avatar': actor_avatar,
                        'message_preview': banner_body,
                        'clean_preview': banner_body,
                        'banner_title': banner_title,
                        'banner_body': banner_body,
                        'preview_type': 'text',
                        'preview_icon': 'bi-people-fill',
                        'sender_name': getattr(actor, 'username', 'System'),
                        'sender_display_name': actor_display,
                        'sender_avatar': actor_avatar,
                        'sender_id': getattr(actor, 'id', None),
                        'is_sender': False,
                        'is_muted': False,
                        'should_alert': True,
                        'timestamp': timezone.now().isoformat(),
                        'unread_count': 1,
                        'total_unread_count': total_unread,
                        'target_url': f"/messaging/conversation/{conversation.id}/",
                    },
                )

            if not PresenceService.is_user_in_conversation(added_user.id, conversation.id):
                cls._dispatch_push_jobs_async([{
                    'recipient': added_user,
                    'conversation': conversation,
                    'last_read_message_id': None,
                    'conv_unread': 1,
                    'force_single_line': True,
                    'banner_title': banner_title,
                    'banner_body': banner_body,
                    'sender_avatar': actor_avatar,
                    'thumbnail_url': None,
                    'preview_type': 'text',
                    'is_group': True,
                    'group_name': group_name,
                    'sender_display_name': actor_display,
                    'is_encrypted_locked': False,
                }])
        except Exception as e:
            logger.error(f"[ChatNotificationService] Error in notify_member_added: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # 5. Stacked WebPush & FCM Delivery (WhatsApp / Telegram Style)
    # ------------------------------------------------------------------

    @classmethod
    def _dispatch_push_jobs_async(cls, push_jobs: List[Dict[str, Any]]) -> None:
        """Run WebPush and FCM network requests in a daemon thread so WS ACKs never block."""
        if not push_jobs:
            return

        def _worker():
            for job in push_jobs:
                try:
                    cls._send_push_for_recipient(job)
                except Exception as err:
                    logger.warning(f"[ChatNotificationService] Push job failed: {err}")

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

    @classmethod
    def _send_push_for_recipient(cls, job: Dict[str, Any]) -> None:
        """Build stacked thread payload and deliver via WebPush (VAPID) and Native FCM."""
        from notifications.models import PushSubscription, NotificationPreference
        from notifications.delivery.adapters import get_firebase_app
        from notifications.delivery.image_utils import get_rounded_avatar_url, get_rounded_thumbnail_url

        recipient = job['recipient']
        conversation = job['conversation']

        # Check active push subscriptions first
        subscriptions = list(
            PushSubscription.objects.filter(user=recipient, is_active=True)
        )
        if not subscriptions:
            return

        # Respect DND and Quiet Hours from NotificationPreference
        prefs = NotificationPreference.objects.filter(user=recipient).first()
        if prefs:
            if prefs.is_do_not_disturb() or (prefs.quiet_hours_enabled and prefs.is_quiet_hours()):
                return
            # Check if user explicitly turned off messaging push in type_preferences
            msg_pref = (prefs.type_preferences or {}).get('messaging.message.sent')
            if isinstance(msg_pref, dict) and msg_pref.get('push') is False:
                return

        conv_unread = int(job.get('conv_unread') or 1)
        is_group = bool(job.get('is_group'))
        group_name = job.get('group_name') or 'Group Chat'
        sender_display_name = job.get('sender_display_name') or 'New Message'
        force_single_line = bool(job.get('force_single_line'))

        # Build Title & Stacked Body (WhatsApp / Telegram style)
        if job.get('is_encrypted_locked'):
            title = group_name if is_group else sender_display_name
            body = f"🔒 {conv_unread} new messages" if conv_unread > 1 else "🔒 New message"
        elif conv_unread > 1 and not force_single_line:
            base_title = group_name if is_group else sender_display_name
            title = f"{base_title} ({conv_unread} new messages)"

            # Fetch up to last 4 unread messages in chronological order for stacked preview
            last_read_id = job.get('last_read_message_id') or 0
            recent_msgs = list(
                Message.objects.filter(
                    conversation_id=conversation.id,
                    is_deleted=False,
                    id__gt=last_read_id,
                )
                .exclude(sender_id=recipient.id)
                .select_related('sender')
                .prefetch_related('attachments')
                .order_by('-id')[:4]
            )
            recent_msgs.reverse()

            lines = []
            for m in recent_msgs:
                p = cls.build_message_preview(m, include_forwarded_prefix=False)
                if is_group:
                    s_name = (m.sender.first_name or m.sender.username) if m.sender else 'Member'
                    lines.append(f"{s_name}: {p['push_text']}")
                else:
                    lines.append(p['push_text'])
            body = "\n".join(lines) if lines else job['banner_body']
        else:
            title = job['banner_title']
            body = job['banner_body']

        # Resolve Avatar & Media Thumbnail URLs
        icon_url = cls.make_absolute_url(job.get('sender_avatar') or DEFAULT_AVATAR_PATH)
        try:
            rounded_av = get_rounded_avatar_url(icon_url)
            if rounded_av:
                icon_url = cls.make_absolute_url(rounded_av)
        except Exception:
            pass

        image_url = cls.make_absolute_url(job.get('thumbnail_url')) if job.get('thumbnail_url') else None
        if image_url:
            try:
                rounded_thumb = get_rounded_thumbnail_url(image_url)
                if rounded_thumb:
                    image_url = cls.make_absolute_url(rounded_thumb)
            except Exception:
                pass

        target_url = f"/messaging/conversation/{conversation.id}/"
        tag = f"vasto-chat-{conversation.id}"
        channel_id = 'vasto_messages'

        for sub in subscriptions:
            if sub.token_type == PushSubscription.TokenType.VAPID:
                cls._send_webpush(
                    sub=sub,
                    title=title,
                    body=body,
                    icon_url=icon_url,
                    image_url=image_url,
                    tag=tag,
                    target_url=target_url,
                    conversation_id=conversation.id,
                )
            elif sub.token_type == PushSubscription.TokenType.FCM:
                cls._send_fcm(
                    sub=sub,
                    title=title,
                    body=body,
                    icon_url=icon_url,
                    image_url=image_url,
                    tag=tag,
                    channel_id=channel_id,
                    target_url=target_url,
                    conversation_id=conversation.id,
                    get_firebase_app=get_firebase_app,
                )

    @classmethod
    def _send_webpush(
        cls,
        sub,
        title: str,
        body: str,
        icon_url: str,
        image_url: Optional[str],
        tag: str,
        target_url: str,
        conversation_id: int,
    ) -> bool:
        from pywebpush import webpush, WebPushException

        vapid_private_key = getattr(settings, 'VAPID_PRIVATE_KEY', '')
        vapid_subject = getattr(settings, 'VAPID_SUBJECT', 'mailto:admin@pwaninet.app')
        if not vapid_private_key or not sub.endpoint or not sub.p256dh or not sub.auth:
            return False

        push_data = {
            'title': title,
            'body': body,
            'icon': icon_url,
            'image': image_url,
            'badge': cls.make_absolute_url('/static/images/vastomonochrome.png'),
            'vibrate': [180, 80, 180],
            'requireInteraction': False,
            'tag': tag,
            'renotify': True,
            'actions': [
                {'action': 'view', 'title': 'Reply', 'icon': '/static/images/favicon-96x96.png'},
                {'action': 'mark_read', 'title': 'Mark as Read', 'icon': '/static/images/favicon-96x96.png'},
            ],
            'data': {
                'url': target_url,
                'destination_url': target_url,
                'conversation_id': str(conversation_id),
                'notification_type': 'CHAT_MESSAGE',
                'category': 'MESSAGING',
                'image': image_url,
                'tag': tag,
            },
            'timestamp': timezone.now().isoformat(),
        }

        try:
            webpush(
                subscription_info={
                    'endpoint': sub.endpoint,
                    'keys': {'p256dh': sub.p256dh, 'auth': sub.auth},
                },
                data=json.dumps(push_data),
                vapid_private_key=vapid_private_key,
                vapid_claims={'sub': vapid_subject},
                timeout=10,
            )
            return True
        except WebPushException as ex:
            if ex.response and getattr(ex.response, 'status_code', None) in (404, 410):
                sub.is_active = False
                sub.save(update_fields=['is_active'])
            return False
        except Exception as ex:
            logger.debug(f"[ChatNotificationService] WebPush error for sub {sub.id}: {ex}")
            return False

    @classmethod
    def _send_fcm(
        cls,
        sub,
        title: str,
        body: str,
        icon_url: str,
        image_url: Optional[str],
        tag: str,
        channel_id: str,
        target_url: str,
        conversation_id: int,
        get_firebase_app,
    ) -> bool:
        app = get_firebase_app()
        if not app or not sub.fcm_token:
            return False

        try:
            from firebase_admin import messaging

            fcm_data = {
                'title': str(title),
                'body': str(body),
                'url': str(target_url),
                'destination_url': str(target_url),
                'conversation_id': str(conversation_id),
                'notification_type': 'CHAT_MESSAGE',
                'category': 'MESSAGING',
                'icon': 'ic_stat_vasto',
                'avatar_url': str(icon_url or ''),
                'image': str(image_url or ''),
                'channel_id': str(channel_id),
                'tag': str(tag),
                'color': '#2563eb',
            }

            is_android = str(getattr(sub, 'platform', '')).upper() in ('ANDROID_NATIVE', 'ANDROID')
            if is_android:
                msg = messaging.Message(
                    android=messaging.AndroidConfig(priority='high', data=fcm_data),
                    data=fcm_data,
                    token=sub.fcm_token,
                )
            else:
                notif_kwargs = {'title': title, 'body': body}
                android_notif_kwargs = {
                    'icon': 'ic_stat_vasto',
                    'channel_id': channel_id,
                    'tag': tag,
                    'color': '#2563eb',
                    'default_sound': True,
                    'default_vibrate_timings': True,
                    'priority': 'high',
                    'visibility': 'public',
                }
                display_img = image_url or icon_url
                if display_img:
                    notif_kwargs['image'] = display_img
                    android_notif_kwargs['image'] = display_img

                msg = messaging.Message(
                    notification=messaging.Notification(**notif_kwargs),
                    android=messaging.AndroidConfig(
                        priority='high',
                        notification=messaging.AndroidNotification(**android_notif_kwargs),
                        data=fcm_data,
                    ),
                    data=fcm_data,
                    token=sub.fcm_token,
                )

            messaging.send(msg, app=app)
            return True
        except Exception as ex:
            err_msg = str(ex)
            ex_name = type(ex).__name__
            if (
                'Unregistered' in ex_name
                or 'NotFound' in ex_name
                or 'Unregistered' in err_msg
                or 'registration-token-not-registered' in err_msg
                or 'not a valid FCM registration token' in err_msg
            ):
                sub.is_active = False
                sub.save(update_fields=['is_active'])
            return False

    # ------------------------------------------------------------------
    # 6. Async Wrappers for WebSocket Consumers (`ChatConsumer`)
    # ------------------------------------------------------------------
    # NOTE: plain @staticmethods, NOT `database_sync_to_async(lambda...)`
    # class attributes. SyncToAsync implements __get__, so class-level
    # wrappers silently inject an extra first arg (None) and every call
    # dies with "takes N positional arguments but N+1 were given",
    # killing the consumer handler mid-receive.

    @staticmethod
    async def notify_new_message_async(message):
        await database_sync_to_async(ChatNotificationService.notify_new_message)(message)

    @staticmethod
    async def notify_reaction_async(message, reactor, emoji):
        await database_sync_to_async(ChatNotificationService.notify_reaction)(message, reactor, emoji)

    @staticmethod
    async def notify_message_deleted_async(message, actor):
        await database_sync_to_async(ChatNotificationService.notify_message_deleted)(message, actor)

    @staticmethod
    async def notify_message_edited_async(message, actor):
        await database_sync_to_async(ChatNotificationService.notify_message_edited)(message, actor)

    @staticmethod
    async def notify_read_receipt_async(conversation_id, reader_user):
        await database_sync_to_async(ChatNotificationService.notify_read_receipt)(conversation_id, reader_user)

    @staticmethod
    async def notify_member_added_async(conversation, added_user, actor=None):
        await database_sync_to_async(ChatNotificationService.notify_member_added)(conversation, added_user, actor)
