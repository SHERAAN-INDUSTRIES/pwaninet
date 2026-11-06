"""Deliberately narrow policy for private, rendered offline copies.

Never opt settings, searches, or write endpoints in. Messaging and notification
read views are explicitly supported; account isolation applies to every copy.
Query strings remain part of a snapshot's key (including pagination/filters).
"""
import re

# Offline snapshots are aggressive: every same-origin GET page is eligible
# unless it falls into an auth/admin/API/file surface or an action segment.
OFFLINE_DENIED_PREFIX = re.compile(
    r"^/(?:admin|api|accounts|o|\.well-known|static|media|download|apk|"
    r"clear-cache|test|system|dashboard|login|logout|register|offline(?:\.html)?|"
    r"i/[^/]+/?$|app/[^/]+/?$|invite/[^/]+/?$|share/app-to-users)(?:/|$)"
)
OFFLINE_DENIED_ACTION = re.compile(
    r"/(?:read|delete|edit|settings|new|create|write|send|mark|archive|block|"
    r"revoke|leave|join|privacy|download|comment|like|reply|repost|hide|unhide|"
    r"search|follow|unfollow|report|pin|mute|invite|remove|switch|verify|reset|"
    r"password|2fa|device|approve|reject|kick|ban|assign|role|auth|token|"
    r"upload|media|logout|login|register)(?:/|$)"
)


def eligible_route(path):
    return not (
        OFFLINE_DENIED_PREFIX.match(path) or OFFLINE_DENIED_ACTION.search(path)
    )


def offline_post_access(request):
    """Revalidate saved post references against current access, without exporting data."""
    import json
    from uuid import UUID
    from django.http import JsonResponse
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'sign_in_required'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'post_required'}, status=405)
    try:
        payload = json.loads(request.body)
        ids = [str(UUID(str(value))) for value in payload['ids'][:500]]
    except (ValueError, TypeError, KeyError, AttributeError):
        return JsonResponse({'error': 'invalid_ids'}, status=400)
    from users.services.privacy import visible_posts_for
    posts = visible_posts_for(request.user).filter(share_id__in=ids).values_list('share_id', 'allow_downloads')
    response = JsonResponse({'posts': [{'id': str(pk), 'downloadable': allowed} for pk, allowed in posts]})
    response['Cache-Control'] = 'no-store'
    return response


def offline_message_access(request):
    """Refresh only previously saved messages after rechecking membership.

    Returns the existing serializer payload (including encrypted ciphertext),
    preserving the application's normal decryption and deleted-message handling.
    """
    import json
    from django.http import JsonResponse
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'sign_in_required'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'post_required'}, status=405)
    try:
        payload = json.loads(request.body)
        conversation_id = int(payload['conversation'])
        ids = [int(value) for value in payload['ids'][:1000]]
    except (ValueError, TypeError, KeyError, AttributeError):
        return JsonResponse({'error': 'invalid_ids'}, status=400)
    from messaging.models import Conversation, Message
    from messaging.serializers import MessageSerializer
    from groups.services.chat_service import accessible_conversations
    if not accessible_conversations(request.user).filter(pk=conversation_id).exists():
        return JsonResponse({'error': 'conversation_unavailable'}, status=403)
    messages = Message.objects.filter(conversation_id=conversation_id, id__in=ids).select_related(
        'sender', 'conversation', 'reply_to', 'reply_to__sender', 'link_preview'
    ).prefetch_related('attachments', 'reactions__user', 'conversation__members').order_by('created_at', 'id')
    response = JsonResponse({'messages': MessageSerializer(messages, many=True, context={'request': request}).data})
    response['Cache-Control'] = 'no-store'
    return response


def offline_notification_access(request):
    """Refresh retained notification rows using the existing renderer and policy."""
    import json
    from uuid import UUID
    from django.http import JsonResponse
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'sign_in_required'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'post_required'}, status=405)
    try:
        payload = json.loads(request.body)
        ids = [str(UUID(str(value))) for value in payload['ids'][:300]]
    except (ValueError, TypeError, KeyError, AttributeError):
        return JsonResponse({'error': 'invalid_ids'}, status=400)
    from notifications.queries.notification_queries import get_notifications_for_user
    from notifications.rendering import render_notification
    read = payload.get('read')
    notifications = get_notifications_for_user(
        request.user, notification_type=payload.get('type') or None,
        is_read=True if read == 'true' else False if read == 'false' else None,
    ).filter(notification_id__in=ids)
    response = JsonResponse({'notifications': [
        {'id': str(item.notification_id), 'html': render_notification(item)} for item in notifications
    ]})
    response['Cache-Control'] = 'no-store'
    return response
