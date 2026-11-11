"""
WebSocket consumers for real-time features.

ARCHITECTURAL RULE:
Each WebSocket consumer must have a single source of truth file.
Duplicate class names across modules are forbidden.
"""

import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
# Messaging presence - FROZEN FOR MVP
# from messaging.presence import PresenceService

logger = logging.getLogger(__name__)
User = get_user_model()


class NotificationConsumer(AsyncWebsocketConsumer):
    """Consumer for real-time notifications."""

    async def connect(self):
        """Handle WebSocket connection."""
        print(f'[NOTIFICATIONS] WebSocket connection attempt from user: {self.scope["user"]}')
        print(f'[NOTIFICATIONS] User is_anonymous: {self.scope["user"].is_anonymous}')

        if self.scope["user"].is_anonymous:
            print('[NOTIFICATIONS] Closing connection - user is anonymous')
            await self.close()
            return

        self.user = self.scope["user"]
        self.user_group_name = f"notifications_{self.user.id}"
        self.connection_id = self.channel_name

        print(f'[NOTIFICATIONS] User {self.user.id} connecting to group {self.user_group_name}')

        # Accept connection first before joining groups
        try:
            await self.accept()
            print(f'[NOTIFICATIONS] User {self.user.id} accepted connection')
        except Exception as e:
            print(f'[NOTIFICATIONS] Error accepting connection: {e}')
            await self.close()
            return

        # Join user's notification group
        await self.channel_layer.group_add(
            self.user_group_name,
            self.channel_name
        )

        # Send current unread count on connect
        unread_count = await self.get_unread_count()
        unread_messages_count = await self.get_unread_messages_count()
        await self.send(text_data=json.dumps({
            'type': 'unread_count',
            'count': unread_count,
            'unread_messages_count': unread_messages_count,
        }))
        print(f'[NOTIFICATIONS] Sent initial unread count: {unread_count} (messages: {unread_messages_count})')

        # Register connection - FROZEN FOR MVP
        # from messaging.ws_middleware import WebSocketConnectionTracker
        # WebSocketConnectionTracker.register_connection(
        #     self.user.id,
        #     self.channel_name
        # )

        # Record initial heartbeat for presence tracking - FROZEN FOR MVP
        # PresenceService.record_heartbeat(
        #     self.user.id,
        #     self.connection_id
        # )

        # Broadcast user online status based on heartbeat freshness - FROZEN FOR MVP
        # is_online = PresenceService.is_user_online(self.user.id)
        # if is_online:
        #     print(f'[NOTIFICATIONS] User {self.user.id} is online (heartbeat-based)')

    async def disconnect(self, close_code):
        """Handle WebSocket disconnection."""
        # Leave user's notification group
        try:
            if getattr(self, 'channel_layer', None) is not None and hasattr(self, 'user_group_name'):
                await self.channel_layer.group_discard(
                    self.user_group_name,
                    self.channel_name
                )
        except Exception:
            pass

        # Remove connection from presence tracking - FROZEN FOR MVP
        # PresenceService.remove_connection(self.user.id, self.channel_name)

        # Unregister connection - FROZEN FOR MVP
        # from messaging.ws_middleware import WebSocketConnectionTracker
        # WebSocketConnectionTracker.unregister_connection(self.user.id, self.channel_name)

        # Check if user is still online based on heartbeat freshness - FROZEN FOR MVP
        # connection_count = WebSocketConnectionTracker.get_connection_count(self.user.id)
        # is_online = PresenceService.is_user_online(self.user.id)

        user = getattr(self, 'user', None)
        if user and hasattr(user, 'id'):
            print(f'[NOTIFICATIONS] User {user.id} disconnect')

        # If user is truly offline (no heartbeat, no connections), persist to DB - FROZEN FOR MVP
        # if not is_online and connection_count == 0:
        #     await PresenceService.persist_last_seen_to_db(self.user.id)
        #     PresenceService.cleanup_stale_presence(self.user.id)
        #     print(f'[NOTIFICATIONS] User {self.user.id} marked as offline (heartbeat-based)')

    async def receive(self, text_data):
        """Handle incoming WebSocket messages."""
        try:
            data = json.loads(text_data)
            # Heartbeat: keep Cloudflare/proxy idle timeouts from killing idle sockets
            if data.get('type') in ('heartbeat', 'ping'):
                await self.send(text_data=json.dumps({'type': 'pong'}))
                return
            # Echo back for now - can be extended for specific commands
            await self.send(text_data=json.dumps({
                'type': 'echo',
                'data': data
            }))
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON received: {text_data}")

    async def notification(self, event):
        """Send notification to client."""
        await self.send(text_data=json.dumps({
            'type': 'notification',
            'notification': event['notification']
        }))

    async def call_update(self, event):
        """Private signaling hint; HTTP call state remains authoritative."""
        await self.send(text_data=json.dumps({'type': 'call_update', 'call': event['call']}))

    async def conversation_update(self, event):
        """Send conversation update to client for real-time list, badge, and banner updates."""
        try:
            payload = {
                'type': 'conversation_update',
                'event_kind': event.get('event_kind', 'message'),
                'conversation_id': event.get('conversation_id'),
                'conversation_type': event.get('conversation_type'),
                'conversation_name': event.get('conversation_name'),
                'conversation_avatar': event.get('conversation_avatar'),
                'peer_id': event.get('peer_id'),
                'peer_username': event.get('peer_username'),
                'message_id': event.get('message_id'),
                'message_preview': event.get('message_preview'),
                'clean_preview': event.get('clean_preview'),
                'banner_title': event.get('banner_title'),
                'banner_body': event.get('banner_body'),
                'preview_type': event.get('preview_type', 'text'),
                'preview_icon': event.get('preview_icon'),
                'thumbnail_url': event.get('thumbnail_url'),
                'sender_name': event.get('sender_name'),
                'sender_display_name': event.get('sender_display_name'),
                'sender_avatar': event.get('sender_avatar'),
                'sender_id': event.get('sender_id'),
                'is_sender': event.get('is_sender', False),
                'is_muted': event.get('is_muted', False),
                'is_pinned': event.get('is_pinned', False),
                'is_mention_or_reply': event.get('is_mention_or_reply', False),
                'should_alert': event.get('should_alert', False),
                'retract_tag': event.get('retract_tag'),
                'timestamp': event.get('timestamp'),
                'unread_count': event.get('unread_count', 0),
                'total_unread_count': event.get('total_unread_count'),
                'target_url': event.get('target_url'),
            }
            await self.send(text_data=json.dumps(payload))
        except Exception as e:
            logger.error(f"Error sending conversation_update to user {getattr(self, 'user', None)}: {e}")

    async def typing_indicator(self, event):
        """Send typing indicator to client for real-time list updates - FROZEN FOR MVP"""
        # Messaging feature - FROZEN FOR MVP
        # print(f'[NOTIFICATIONS] Sending typing indicator to user {self.user.id}: {event}')
        # await self.send(text_data=json.dumps({
        #     'type': 'typing_indicator',
        #     'conversation_id': event['conversation_id'],
        #     'user_id': event['user_id'],
        #     'username': event['username'],
        #     'is_typing': event['is_typing']
        # }))
        pass

    async def user_status(self, event):
        """Send user online status to client for real-time online indicators."""
        print(f'[NOTIFICATIONS] Sending user status to user {self.user.id}: {event}')
        await self.send(text_data=json.dumps({
            'type': 'user_status',
            'user_id': event['user_id'],
            'username': event['username'],
            'is_online': event['is_online'],
            'last_seen': event.get('last_seen')
        }))

    async def unread_count_update(self, event):
        """Send unread notification count update to client."""
        print(f'[NOTIFICATIONS] Sending unread count update to user {self.user.id}: {event}')
        await self.send(text_data=json.dumps({
            'type': 'unread_count',
            'count': event['count']
        }))

    async def force_logout(self, event):
        """
        Instant-logout broadcast (device security, Phase 1): tell every open
        tab which session keys are dead so session_guard.js can drop the
        matching device straight to the login screen.
        """
        print(f'[NOTIFICATIONS] Sending force_logout to user {self.user.id}: {event}')
        await self.send(text_data=json.dumps({
            'type': 'force_logout',
            'session_keys': event.get('session_keys') or [],
        }))

    @database_sync_to_async
    def get_unread_count(self):
        """Get unread count for current user."""
        from notifications.services.notification_service import get_cached_unread_count
        return get_cached_unread_count(self.user)

    @database_sync_to_async
    def get_unread_messages_count(self):
        """Get unread direct/group message count for current user."""
        from messaging.context_processors import get_cached_unread_message_count
        return get_cached_unread_message_count(self.user)



class FeedConsumer(AsyncWebsocketConsumer):
    """Consumer for real-time feed updates."""

    async def connect(self):
        """Handle WebSocket connection."""
        if self.scope["user"].is_anonymous:
            await self.close()
            return

        self.user = self.scope["user"]
        self.user_group_name = f"feed_{self.user.id}"

        # Join user's feed group
        await self.channel_layer.group_add(
            self.user_group_name,
            self.channel_name
        )
        
        # Join global feed updates group for post metrics
        await self.channel_layer.group_add(
            "feed_updates",
            self.channel_name
        )

        await self.accept()

    async def disconnect(self, close_code):
        """Handle WebSocket disconnection."""
        # Leave user's feed group
        if hasattr(self, 'user_group_name'):
            await self.channel_layer.group_discard(
                self.user_group_name,
                self.channel_name
            )
        
        # Leave global feed updates group
        if hasattr(self, 'user'):
            await self.channel_layer.group_discard(
                "feed_updates",
                self.channel_name
            )

    async def receive(self, text_data):
        """Handle incoming WebSocket messages."""
        try:
            data = json.loads(text_data)
            # Heartbeat: keep Cloudflare/proxy idle timeouts from killing idle sockets
            if data.get('type') in ('heartbeat', 'ping'):
                await self.send(text_data=json.dumps({'type': 'pong'}))
                return
            # Echo back for now - can be extended for specific commands
            await self.send(text_data=json.dumps({
                'type': 'echo',
                'data': data
            }))
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON received: {text_data}")

    async def feed_update(self, event):
        """Send feed update to client."""
        await self.send(text_data=json.dumps({
            'type': 'feed_update',
            'post': event['post']
        }))

    async def post_like_update(self, event):
        """Send post like count update to client."""
        await self.send(text_data=json.dumps({
            'type': 'post_like_update',
            'post_id': event['post_id'],
            'share_id': event.get('share_id'),
            'like_count': event['like_count'],
            'is_liked': event['is_liked'],
            'user_id': event['user_id']
        }))

    async def post_repost_update(self, event):
        """Send post repost count update to client."""
        await self.send(text_data=json.dumps({
            'type': 'post_repost_update',
            'post_id': event['post_id'],
            'repost_count': event['repost_count'],
            'is_reposted': event['is_reposted'],
            'user_id': event['user_id']
        }))

    async def post_comment_update(self, event):
        """Send post comment count update to client."""
        await self.send(text_data=json.dumps({
            'type': 'post_comment_update',
            'post_id': event['post_id'],
            'comment_count': event['comment_count']
        }))

    async def video_progress(self, event):
        """
        Relay HLS transcoding progress from the Celery task to the browser.

        The ``process_large_video`` Celery task sends events to the
        ``feed_{user_id}`` channel group with this type.  The frontend
        upload banner subscribes to the feed WebSocket and listens for
        ``type === 'video_progress'`` messages to update its progress bar.

        Payload forwarded to the client::

            {
                "type": "video_progress",
                "post_id": 123,
                "status": "transcoding" | "ready" | "failed",
                "progress": 0-100,
                "message": "Encoding 480p…",
                "hls_url": "/media/posts/videos/123/hls/master.m3u8"  // only when ready
            }
        """
        await self.send(text_data=json.dumps({
            'type':     'video_progress',
            'post_id':  event.get('post_id'),
            'share_id': event.get('share_id'),
            'status':   event.get('status'),
            'progress': event.get('progress', 0),
            'message':  event.get('message', ''),
            'hls_url':  event.get('hls_url', ''),
        }))




class CommentConsumer(AsyncWebsocketConsumer):
    """Consumer for real-time comment updates on posts."""

    async def connect(self):
        """Handle WebSocket connection."""
        self.user = self.scope.get("user")
        # Get post_id from URL
        self.post_id = self.scope['url_route']['kwargs'].get('post_id')
        
        if not self.post_id:
            await self.close()
            return

        raw_post_id = str(self.post_id)
        numeric_post_id = raw_post_id
        if not raw_post_id.isdigit():
            from posts.models import Post
            try:
                post = await database_sync_to_async(
                    lambda: Post.objects.filter(share_id=raw_post_id).only('id').first()
                )()
                if post:
                    numeric_post_id = str(post.id)
            except Exception as e:
                logger.warning(f"[COMMENTS] Error resolving post share_id {raw_post_id}: {e}")

        self.post_groups = [f"post_comments_{numeric_post_id}"]
        if raw_post_id != numeric_post_id:
            self.post_groups.append(f"post_comments_{raw_post_id}")

        user_info = getattr(self.user, 'id', 'anonymous')
        print(f'[COMMENTS] User {user_info} connecting to post {self.post_id} groups {self.post_groups}')

        # Join post's comment groups
        for grp in self.post_groups:
            await self.channel_layer.group_add(
                grp,
                self.channel_name
            )

        await self.accept()

    async def disconnect(self, close_code):
        """Handle WebSocket disconnection."""
        # Leave post's comment groups
        if hasattr(self, 'post_groups'):
            for grp in self.post_groups:
                await self.channel_layer.group_discard(
                    grp,
                    self.channel_name
                )
        elif hasattr(self, 'post_group_name'):
            await self.channel_layer.group_discard(
                self.post_group_name,
                self.channel_name
            )
        user = getattr(self, 'user', None)
        post_id = getattr(self, 'post_id', None)
        if user and hasattr(user, 'id') and post_id:
            print(f'[COMMENTS] User {user.id} disconnected from post {post_id}')

    async def receive(self, text_data):
        """Handle incoming WebSocket messages."""
        try:
            data = json.loads(text_data)
            # Heartbeat: keep Cloudflare/proxy idle timeouts from killing idle sockets
            if data.get('type') in ('heartbeat', 'ping'):
                await self.send(text_data=json.dumps({'type': 'pong'}))
                return
            # Echo back for now - can be extended for specific commands
            await self.send(text_data=json.dumps({
                'type': 'echo',
                'data': data
            }))
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON received: {text_data}")

    async def new_comment(self, event):
        """Send new comment to all users viewing the post."""
        print(f'[COMMENTS] Sending new comment to user {self.user.id} for post {self.post_id}')
        await self.send(text_data=json.dumps({
            'type': 'new_comment',
            'comment': event['comment']
        }))

    async def comment_like_update(self, event):
        """Send comment like update to all users viewing the post."""
        await self.send(text_data=json.dumps({
            'type': 'comment_like_update',
            'comment_id': event['comment_id'],
            'likes_count': event['likes_count']
        }))


class OnlineStatusConsumer(AsyncWebsocketConsumer):
    """Consumer for tracking online status."""

    async def connect(self):
        """Handle WebSocket connection."""
        if self.scope["user"].is_anonymous:
            await self.close()
            return

        self.user = self.scope["user"]

        # Update user's online status
        await self.update_online_status(True)

        # Join online users group
        await self.channel_layer.group_add(
            "online_users",
            self.channel_name
        )

        await self.accept()

        # Broadcast that user is now online
        await self.channel_layer.group_send(
            "online_users",
            {
                'type': 'user_joined',
                'user_id': self.user.id,
                'username': self.user.username
            }
        )

    async def disconnect(self, close_code):
        """Handle WebSocket disconnection."""
        user = getattr(self, 'user', None)
        if not user or getattr(user, 'is_anonymous', True):
            return

        # Update user's offline status
        await self.update_online_status(False)

        # Leave online users group
        await self.channel_layer.group_discard(
            "online_users",
            self.channel_name
        )

        # Broadcast that user is now offline
        await self.channel_layer.group_send(
            "online_users",
            {
                'type': 'user_left',
                'user_id': self.user.id,
                'username': self.user.username
            }
        )

    async def receive(self, text_data):
        """Handle incoming WebSocket messages."""
        try:
            data = json.loads(text_data)
            # Heartbeat: keep Cloudflare/proxy idle timeouts from killing idle sockets
            if data.get('type') in ('heartbeat', 'ping'):
                await self.send(text_data=json.dumps({'type': 'pong'}))
                return
            # Echo back for now - can be extended for specific commands
            await self.send(text_data=json.dumps({
                'type': 'echo',
                'data': data
            }))
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON received: {text_data}")

    async def user_joined(self, event):
        """Broadcast that a user joined."""
        await self.send(text_data=json.dumps({
            'type': 'user_joined',
            'user_id': event['user_id'],
            'username': event['username']
        }))

    async def user_left(self, event):
        """Broadcast that a user left."""
        await self.send(text_data=json.dumps({
            'type': 'user_left',
            'user_id': event['user_id'],
            'username': event['username']
        }))

    @database_sync_to_async
    def update_online_status(self, is_online):
        """Update user's online status in database."""
        from users.models import User
        try:
            user = User.objects.get(id=self.user.id)
            user.is_online = is_online
            user.save(update_fields=['is_online'])
        except User.DoesNotExist:
            pass
