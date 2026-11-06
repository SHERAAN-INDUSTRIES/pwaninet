import json
import asyncio
import logging
from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils import timezone
from django.db.models import Count
from django.contrib.auth import get_user_model

from .models import Conversation, ConversationMember, Message, MessageReaction, MessageAttachment
from .serializers import MessageSerializer
from .presence import PresenceService
from .ws_middleware import WebSocketConnectionTracker, WebSocketRateLimiter
from .observability import metrics
from .services.link_preview_service import LinkPreviewService
from .services.chat_notification_service import ChatNotificationService

logger = logging.getLogger(__name__)
User = get_user_model()

HEARTBEAT_INTERVAL = 25
WS_MESSAGE_RATE = 120  # messages per minute
SERVER_PING_INTERVAL = 20
HEARTBEAT_TYPES = {'heartbeat', 'ping'}


class ChatConsumer(AsyncWebsocketConsumer):
    """
    High-performance real-time 1:1 and group WebSocket consumer.
    Provides instant message transmission, real WhatsApp-style checkmark receipts
    (Sent -> Delivered -> Read), Telegram-speed optimistic ACKs, typing indicators,
    audio recording states, and floating emoji reactions.
    """

    async def connect(self):
        self.user = self.scope.get('user')
        self.user_id = getattr(self.user, 'id', None)
        self.conversation_id = self.scope['url_route']['kwargs']['conversation_id']
        self.room_group_name = f'chat_{self.conversation_id}'
        self.connection_id = self.channel_name

        if not self.user or not self.user.is_authenticated:
            await self.close(code=4001)
            return

        is_member = await self._is_member()
        if not is_member:
            logger.warning(f"User {self.user_id} denied access to conversation {self.conversation_id}")
            await self.close(code=4003)
            return

        # Register connection tracking & presence
        await sync_to_async(WebSocketConnectionTracker.register_connection)(
            self.user_id,
            self.connection_id,
            metadata={
                'conversation_id': self.conversation_id,
                'ip': self.scope.get('client', ['unknown'])[0],
            }
        )

        await sync_to_async(PresenceService.record_heartbeat)(
            self.user_id,
            self.connection_id,
            metadata={'conversation_id': self.conversation_id}
        )

        # Join conversation room group (guard: channel_layer may be None in tests)
        if self.channel_layer is not None:
            await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        try:
            await self.accept()
        except RuntimeError:
            return
        # Server-side ping keeps NAT/proxies alive even when client tab is throttled
        self._ping_task = asyncio.create_task(self._server_ping_loop())

        # Mark all pending messages sent to this user as 'delivered'
        delivered_ids = await self._mark_received_messages_delivered()
        for msg_id in delivered_ids:
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'message_status_event',
                    'message_id': msg_id,
                    'status': 'delivered'
                }
            )

        # Broadcast online status
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'user_status_event',
                'user_id': self.user_id,
                'username': self.user.username,
                'is_online': True,
                'last_seen': None
            }
        )

        # Send peer's current presence status to the newly connected user
        peer_status = await self._get_peer_presence()
        if peer_status:
            await self.send(text_data=json.dumps({
                'type': 'peer_status',
                **peer_status
            }))

        # Send peer's last read status if available
        peer_last_read = await self._get_peer_last_read()
        if peer_last_read:
            await self.send(text_data=json.dumps({
                'type': 'read_receipt',
                **peer_last_read
            }))

    async def _server_ping_loop(self):
        try:
            while True:
                await asyncio.sleep(SERVER_PING_INTERVAL)
                try:
                    await self.send(text_data=json.dumps({'type': 'ping'}))
                except Exception:
                    break
        except asyncio.CancelledError:
            pass

    async def disconnect(self, close_code):
        if getattr(self, '_ping_task', None):
            try:
                self._ping_task.cancel()
            except Exception:
                pass
        if not getattr(self, 'user_id', None):
            return

        # Discard from room group — never let cleanup errors mask the disconnect
        try:
            if self.channel_layer is not None and getattr(self, 'room_group_name', None):
                await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
        except Exception:
            logger.exception("group_discard failed")
        finally:
            try:
                await sync_to_async(PresenceService.remove_connection)(self.user_id, self.channel_name)
            except Exception:
                pass
            try:
                await sync_to_async(WebSocketConnectionTracker.unregister_connection)(self.user_id, self.channel_name)
            except Exception:
                pass

        try:
            is_still_online = await sync_to_async(PresenceService.is_user_online)(self.user_id)
            last_seen = await PresenceService.aget_last_seen(self.user_id) or timezone.now()
        except Exception:
            is_still_online, last_seen = True, timezone.now()

        if not is_still_online:
            try:
                await PresenceService.persist_last_seen_to_db(self.user_id)
            except Exception:
                pass
            try:
                username = getattr(getattr(self, 'user', None), 'username', '') or ''
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'user_status_event',
                        'user_id': self.user_id,
                        'username': username,
                        'is_online': False,
                        'last_seen': last_seen.isoformat() if hasattr(last_seen, 'isoformat') else str(last_seen)
                    }
                )
            except Exception:
                logger.exception("offline broadcast failed")

    async def receive(self, text_data):
        """Receive message from WebSocket and dispatch action."""
        if not await self._is_member():
            await self.close(code=4003)
            return
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        msg_type = data.get('type')

        if msg_type in HEARTBEAT_TYPES:
            await sync_to_async(PresenceService.record_heartbeat)(self.user_id, self.connection_id)
            await self.send(text_data=json.dumps({'type': 'pong'}))
            return

        if WebSocketRateLimiter.is_rate_limited(self.user_id, self.connection_id, WS_MESSAGE_RATE):
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Rate limit exceeded. Please wait a moment.'
            }))
            return

        if msg_type == 'get_peer_presence':
            peer_status = await self._get_peer_presence()
            if peer_status:
                await self.send(text_data=json.dumps({
                    'type': 'peer_status',
                    **peer_status
                }))

        elif msg_type == 'chat_message':
            await self._handle_chat_message(data)

        elif msg_type == 'message_delivered':
            await self._handle_message_delivered(data)

        elif msg_type == 'read_receipt':
            await self._handle_read_receipt(data)

        elif msg_type in ('typing', 'typing_indicator'):
            await self._handle_typing(data)

        elif msg_type == 'recording_audio':
            await self._handle_recording_audio(data)

        elif msg_type == 'reaction':
            await self._handle_reaction(data)

        elif msg_type == 'edit_message':
            await self._handle_edit_message(data)

        elif msg_type == 'delete_message':
            await self._handle_delete_message(data)

        elif msg_type == 'forward_message':
            await self._handle_forward_message(data)

    # -------------------------------------------------------------
    # Action Handlers
    # -------------------------------------------------------------

    async def _handle_chat_message(self, data):
        temp_id = data.get('temp_id')
        content = data.get('content', '').strip()
        reply_to_id = data.get('reply_to_id')
        message_type = data.get('message_type', 'text')
        link_url = data.get('link_url')

        media_url = data.get('attachment_url') or data.get('metadata', {}).get('url') or data.get('metadata', {}).get('preview_url')

        if not content and not media_url and not data.get('attachments'):
            return

        # Save to database
        saved_msg, serialized = await self._save_message(data)
        if not saved_msg:
            return

        # 1. Send immediate ACK back to sender (Telegram instant feel)
        await self.send(text_data=json.dumps({
            'type': 'message_ack',
            'temp_id': temp_id,
            'message_id': saved_msg.id,
            'status': saved_msg.status,
            'created_at': saved_msg.created_at.isoformat()
        }))

        # 2. Broadcast message to all conversation members in the room
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'chat_message_event',
                'message': serialized,
                'temp_id': temp_id
            }
        )

        # 3. Check if recipient is active to mark delivered automatically
        other_members = await self._get_other_members()
        any_peer_online = False
        for uid in other_members:
            try:
                if await sync_to_async(PresenceService.is_user_online)(uid):
                    any_peer_online = True
                    break
            except Exception:
                continue
        if any_peer_online:
            await self._set_message_status(saved_msg.id, 'delivered')
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'message_status_event',
                    'message_id': saved_msg.id,
                    'status': 'delivered'
                }
            )

        # 4. Dispatch rich conversation_update + OS push notifications via ChatNotificationService
        await ChatNotificationService.notify_new_message_async(saved_msg)

    async def _handle_message_delivered(self, data):
        message_id = data.get('message_id')
        if not message_id:
            return

        updated = await self._set_message_status(message_id, 'delivered')
        if updated:
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'message_status_event',
                    'message_id': message_id,
                    'status': 'delivered'
                }
            )

    async def _handle_read_receipt(self, data):
        message_id = data.get('message_id')
        message_ids = data.get('message_ids')
        target_id = message_id
        if message_ids and isinstance(message_ids, list):
            valid_ids = [int(i) for i in message_ids if str(i).isdigit()]
            if valid_ids:
                max_list_id = max(valid_ids)
                target_id = max(int(target_id), max_list_id) if target_id and str(target_id).isdigit() else max_list_id

        last_id, reader_avatar = await self._update_last_read(target_id)
        if last_id:
            # 1. Broadcast read receipt to chat room
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'read_receipt_event',
                    'user_id': self.user_id,
                    'last_read_message_id': last_id,
                    'read_avatar': reader_avatar
                }
            )

            # 2. Update reader's sidebar unread badge & global messages badge immediately + retract push
            await ChatNotificationService.notify_read_receipt_async(self.conversation_id, self.user)

    async def _handle_typing(self, data):
        is_typing = bool(data.get('is_typing', data.get('typing', False)))
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'typing_event',
                'user_id': self.user_id,
                'username': self.user.username,
                'is_typing': is_typing
            }
        )

    async def _handle_recording_audio(self, data):
        is_recording = bool(data.get('is_recording', False))
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'recording_audio_event',
                'user_id': self.user_id,
                'username': self.user.username,
                'is_recording': is_recording
            }
        )

    async def _handle_reaction(self, data):
        message_id = data.get('message_id')
        emoji = data.get('emoji')
        if not message_id or not emoji:
            return

        grouped_reactions, reacted_msg = await self._toggle_reaction(message_id, emoji)
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'reaction_event',
                'message_id': message_id,
                'reactions': grouped_reactions
            }
        )
        if reacted_msg:
            await ChatNotificationService.notify_reaction_async(reacted_msg, self.user, emoji)

    async def _handle_edit_message(self, data):
        message_id = data.get('message_id')
        content = data.get('content', '').strip()
        if not message_id or not content:
            return

        edited = await self._edit_message(message_id, content)
        if edited:
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'message_edited_event',
                    'message_id': message_id,
                    'content': content,
                    'edited_at': edited.edited_at.isoformat()
                }
            )
            await ChatNotificationService.notify_message_edited_async(edited, self.user)

    async def _handle_delete_message(self, data):
        message_id = data.get('message_id')
        if not message_id:
            return

        deleted_msg = await self._delete_message(message_id)
        if deleted_msg:
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'message_deleted_event',
                    'message_id': message_id
                }
            )
            await ChatNotificationService.notify_message_deleted_async(deleted_msg, self.user)

    async def _handle_forward_message(self, data):
        message_id = data.get('message_id')
        target_conv_ids = data.get('conversation_ids', [])
        if not message_id or not target_conv_ids:
            return

        orig_msg = await self._get_message_for_forward(message_id)
        if not orig_msg:
            return

        for target_id in target_conv_ids:
            forwarded_msg, serialized = await self._forward_message_to_conv(orig_msg, target_id)
            if forwarded_msg and serialized:
                await self.channel_layer.group_send(
                    f"chat_{target_id}",
                    {
                        'type': 'chat_message_event',
                        'message': serialized,
                        'temp_id': None
                    }
                )
                await ChatNotificationService.notify_new_message_async(forwarded_msg)

    # -------------------------------------------------------------
    # Group Broadcast Dispatches (Downlink to WebSocket)
    # -------------------------------------------------------------

    async def send(self, text_data=None, bytes_data=None, close=False):
        # Existing sockets must stop receiving after removal, including bulk
        # membership changes that bypass Django signals.
        if not await self._is_member():
            await self.close(code=4003)
            return
        await super().send(text_data=text_data, bytes_data=bytes_data, close=close)

    async def chat_message_event(self, event):
        await self.send(text_data=json.dumps({
            'type': 'chat_message',
            'message': event['message'],
            'temp_id': event.get('temp_id')
        }))

    async def chat_message(self, event):
        await self.chat_message_event(event)

    async def message_status_event(self, event):
        await self.send(text_data=json.dumps({
            'type': 'message_status',
            'message_id': event['message_id'],
            'status': event['status']
        }))

    async def message_status(self, event):
        await self.message_status_event(event)

    async def read_receipt_event(self, event):
        await self.send(text_data=json.dumps({
            'type': 'read_receipt',
            'user_id': event['user_id'],
            'last_read_message_id': event['last_read_message_id'],
            'read_avatar': event.get('read_avatar')
        }))

    async def video_ready_event(self, event):
        await self.send(text_data=json.dumps({
            'type': 'video_ready',
            'message_id': event.get('message_id'),
            'attachment_id': event.get('attachment_id'),
            'file_url': event.get('file_url'),
            'duration': event.get('duration'),
        }))

    async def typing_event(self, event):
        if event['user_id'] != self.user_id:
            await self.send(text_data=json.dumps({
                'type': 'typing',
                'user_id': event['user_id'],
                'username': event['username'],
                'is_typing': event['is_typing']
            }))

    async def recording_audio_event(self, event):
        if event['user_id'] != self.user_id:
            await self.send(text_data=json.dumps({
                'type': 'recording_audio',
                'user_id': event['user_id'],
                'username': event['username'],
                'is_recording': event['is_recording']
            }))

    async def recording_audio(self, event):
        await self.recording_audio_event(event)

    async def user_status_event(self, event):
        await self.send(text_data=json.dumps({
            'type': 'user_status',
            'user_id': event['user_id'],
            'username': event['username'],
            'is_online': event['is_online'],
            'last_seen': event.get('last_seen')
        }))

    async def reaction_event(self, event):
        await self.send(text_data=json.dumps({
            'type': 'reaction_update',
            'message_id': event['message_id'],
            'reactions': event['reactions']
        }))

    async def message_edited_event(self, event):
        await self.send(text_data=json.dumps({
            'type': 'message_edited',
            'message_id': event['message_id'],
            'content': event['content'],
            'edited_at': event['edited_at']
        }))

    async def message_deleted_event(self, event):
        await self.send(text_data=json.dumps({
            'type': 'message_deleted',
            'message_id': event['message_id']
        }))

    # -------------------------------------------------------------
    # Database Operations (Async-Safe)
    # -------------------------------------------------------------

    @database_sync_to_async
    def _is_member(self):
        from groups.services.chat_service import accessible_conversations
        return accessible_conversations(self.user).filter(pk=self.conversation_id).exists()

    @database_sync_to_async
    def _get_other_members(self):
        return list(
            ConversationMember.objects.filter(conversation_id=self.conversation_id)
            .exclude(user_id=self.user_id)
            .values_list('user_id', flat=True)
        )

    @database_sync_to_async
    def _get_peer_presence(self):
        other_member = ConversationMember.objects.filter(
            conversation_id=self.conversation_id
        ).exclude(user_id=self.user_id).select_related('user').first()

        if not other_member:
            return None

        peer_user = other_member.user
        is_online = PresenceService.is_user_online(peer_user.id)
        last_seen = PresenceService.get_last_seen(peer_user.id) or getattr(peer_user, 'last_seen', None)

        return {
            'user_id': peer_user.id,
            'username': peer_user.username,
            'is_online': is_online,
            'last_seen': last_seen.isoformat() if hasattr(last_seen, 'isoformat') else str(last_seen) if last_seen else None
        }

    @database_sync_to_async
    def _save_message(self, data):
        try:
            conv = Conversation.objects.get(id=self.conversation_id)
            reply_to = None
            if data.get('reply_to_id'):
                reply_to = Message.objects.filter(id=data['reply_to_id'], conversation=conv).first()

            media_url = data.get('attachment_url') or data.get('metadata', {}).get('url') or data.get('metadata', {}).get('preview_url')
            attachment_type = data.get('attachment_type') or data.get('metadata', {}).get('type') or data.get('message_type')
            is_sticker_or_gif = attachment_type in ('sticker', 'gif')

            msg = Message.objects.create(
                conversation=conv,
                sender=self.user,
                content=data.get('content', ''),
                reply_to=reply_to,
                message_type=attachment_type if is_sticker_or_gif else data.get('message_type', 'text'),
                attachment_type=attachment_type if is_sticker_or_gif else data.get('attachment_type'),
                global_caption=data.get('global_caption', ''),
                link_url=data.get('link_url'),
                link_title=data.get('link_title'),
                link_description=data.get('link_description'),
                link_image=media_url if is_sticker_or_gif else data.get('link_image'),
                link_type=attachment_type if is_sticker_or_gif else data.get('link_type', 'link'),
                status='sent'
            )

            # Link preview fetch if applicable
            if msg.link_url:
                try:
                    LinkPreviewService.generate_preview_for_message(msg)
                except Exception as e:
                    logger.debug(f"Link preview generation skipped: {e}")

            if data.get('is_forwarded') or data.get('metadata', {}).get('is_forwarded'):
                msg.is_forwarded = True
                msg.save(update_fields=['is_forwarded'])
            conv.save(update_fields=['updated_at'])
            serialized = MessageSerializer(msg).data
            if msg.is_forwarded:
                serialized['is_forwarded'] = True
            return msg, serialized
        except Exception as e:
            logger.error(f"Error saving message: {e}", exc_info=True)
            return None, None

    @database_sync_to_async
    def _set_message_status(self, message_id, new_status):
        try:
            rows = Message.objects.filter(id=message_id, conversation_id=self.conversation_id).update(status=new_status)
            return rows > 0
        except Exception:
            return False

    @database_sync_to_async
    def _mark_received_messages_delivered(self):
        delivered_ids = list(
            Message.objects.filter(
                conversation_id=self.conversation_id,
                status='sent'
            ).exclude(sender_id=self.user_id).values_list('id', flat=True)
        )
        if delivered_ids:
            Message.objects.filter(id__in=delivered_ids).update(status='delivered')
        return delivered_ids

    @database_sync_to_async
    def _get_peer_last_read(self):
        try:
            member = ConversationMember.objects.filter(
                conversation_id=self.conversation_id
            ).exclude(user_id=self.user_id).select_related('user', 'last_read_message').first()
            if member and member.last_read_message_id:
                reader_avatar = None
                if hasattr(member.user, 'profile_pic') and member.user.profile_pic:
                    try:
                        reader_avatar = member.user.profile_pic.url
                    except Exception:
                        reader_avatar = None
                return {
                    'user_id': member.user_id,
                    'last_read_message_id': member.last_read_message_id,
                    'read_avatar': reader_avatar
                }
            return None
        except Exception as e:
            logger.error(f"Error getting peer last read: {e}")
            return None

    @database_sync_to_async
    def _update_last_read(self, message_id=None):
        try:
            member = ConversationMember.objects.filter(
                conversation_id=self.conversation_id,
                user_id=self.user_id
            ).first()
            if not member:
                return None, None

            if message_id:
                target_msg = Message.objects.filter(id=message_id, conversation_id=self.conversation_id).first()
            else:
                target_msg = Message.objects.filter(conversation_id=self.conversation_id).order_by('-id').first()

            if target_msg:
                member.last_read_message = target_msg
                member.save(update_fields=['last_read_message'])

                # Update messages up to this id as read
                Message.objects.filter(
                    conversation_id=self.conversation_id,
                    id__lte=target_msg.id
                ).exclude(sender_id=self.user_id).update(status='read')

                try:
                    from .context_processors import invalidate_unread_message_count_cache
                    invalidate_unread_message_count_cache(self.user_id)
                except Exception:
                    pass

                reader_avatar = None
                if hasattr(self.user, 'profile_pic') and self.user.profile_pic:
                    try:
                        reader_avatar = self.user.profile_pic.url
                    except Exception:
                        reader_avatar = None

                return target_msg.id, reader_avatar
            return None, None
        except Exception as e:
            logger.error(f"Error updating read receipt: {e}")
            return None, None

    @database_sync_to_async
    def _toggle_reaction(self, message_id, emoji):
        try:
            msg = Message.objects.select_related('sender', 'conversation').get(id=message_id, conversation_id=self.conversation_id)
            existing = MessageReaction.objects.filter(message=msg, user=self.user, emoji=emoji).first()
            reacted_msg = None
            if existing:
                existing.delete()
            else:
                MessageReaction.objects.create(message=msg, user=self.user, emoji=emoji)
                reacted_msg = msg

            # Build aggregated summary: [{'emoji': '❤️', 'count': 2, 'user_ids': [1, 2]}]
            reactions = msg.reactions.all().select_related('user')
            agg = {}
            for r in reactions:
                if r.emoji not in agg:
                    agg[r.emoji] = {'emoji': r.emoji, 'count': 0, 'user_ids': [], 'usernames': []}
                agg[r.emoji]['count'] += 1
                agg[r.emoji]['user_ids'].append(r.user.id)
                agg[r.emoji]['usernames'].append(r.user.username)
            return list(agg.values()), reacted_msg
        except Exception as e:
            logger.error(f"Error toggling reaction: {e}")
            return [], None

    @database_sync_to_async
    def _edit_message(self, message_id, content):
        try:
            msg = Message.objects.select_related('sender', 'conversation').get(id=message_id, conversation_id=self.conversation_id, sender=self.user)
            # Enforce 15-minute window for message editing
            if (timezone.now() - msg.created_at).total_seconds() > 15 * 60:
                logger.warning(f"Edit attempt past 15-minute window for message {message_id}")
                return None
            msg.content = content
            msg.edited_at = timezone.now()
            msg.save(update_fields=['content', 'edited_at'])
            return msg
        except Exception as e:
            logger.error(f"Error editing message: {e}")
            return None

    @database_sync_to_async
    def _delete_message(self, message_id):
        try:
            msg = Message.objects.select_related('sender', 'conversation').filter(id=message_id, conversation_id=self.conversation_id, sender=self.user).first()
            if not msg:
                return None
            # Enforce 48-hour window for delete for everyone
            if (timezone.now() - msg.created_at).total_seconds() > 48 * 3600:
                logger.warning(f"Delete for everyone attempt past 48-hour window for message {message_id}")
                return None
            msg.is_deleted = True
            msg.content = 'This message was deleted'
            msg.save(update_fields=['is_deleted', 'content'])
            return msg
        except Exception as e:
            logger.error(f"Error deleting message: {e}")
            return None

    @database_sync_to_async
    def _get_message_for_forward(self, message_id):
        return Message.objects.filter(id=message_id, conversation_id=self.conversation_id).first()

    @database_sync_to_async
    def _forward_message_to_conv(self, orig_msg, target_conv_id):
        try:
            from groups.services.chat_service import accessible_conversations
            conv = accessible_conversations(self.user).filter(id=target_conv_id).first()
            if not conv:
                return None, None
            new_msg = Message.objects.create(
                conversation=conv,
                sender=self.user,
                content=orig_msg.content,
                message_type=orig_msg.message_type,
                attachment=orig_msg.attachment,
                attachment_type=orig_msg.attachment_type,
                global_caption=orig_msg.global_caption,
                link_url=orig_msg.link_url,
                link_title=orig_msg.link_title,
                link_description=orig_msg.link_description,
                link_image=orig_msg.link_image,
                link_type=orig_msg.link_type,
                link_preview=orig_msg.link_preview,
                is_forwarded=True,
                status='sent'
            )
            # Duplicate any MessageAttachment entries (for multi-file attachments)
            for att in orig_msg.attachments.all():
                MessageAttachment.objects.create(
                    message=new_msg,
                    file=att.file,
                    file_type=att.file_type,
                    caption=att.caption,
                    order=att.order,
                    size=att.size,
                    width=att.width,
                    height=att.height,
                    duration=att.duration
                )
            conv.save(update_fields=['updated_at'])
            serialized = MessageSerializer(new_msg).data
            serialized['is_forwarded'] = True
            return new_msg, serialized
        except Exception as e:
            logger.error(f"Error forwarding message: {e}", exc_info=True)
            return None, None

    @database_sync_to_async
    def _get_unread_count(self, user_id):
        try:
            conv = Conversation.objects.get(id=self.conversation_id)
            member = conv.members.filter(user_id=user_id).first()
            base_qs = conv.messages.filter(is_deleted=False).exclude(sender_id=user_id)
            if member and member.last_read_message_id:
                return base_qs.filter(id__gt=member.last_read_message_id).count()
            return base_qs.count()
        except Exception:
            return 0
