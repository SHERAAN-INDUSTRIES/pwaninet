from django.core.cache import cache
from notifications.models import NotificationObject
from notifications.notifications.registry import NotificationStatuses
from notifications.queries.notification_queries import (
    get_notification_for_user,
    get_notifications_for_user,
    get_unread_count,
    get_unread_count_by_user_id,
    mark_user_notifications_as_read,
    delete_notification,
    delete_all_notifications,
    delete_read_notifications,
    get_grouped_notifications,
    get_unread_counts_for_groups,
    get_unread_group_activity_by_type,
    has_any_unread_group_activity,
    mark_group_notifications_as_read,
    get_unread_announcement_ids_for_user,
    mark_announcement_as_read
)
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

def _unread_count_cache_key(user_id):
    return f'''notif:unread_count:user:{user_id}'''


def get_cached_unread_count(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return 0
    cache_key = _unread_count_cache_key(user.id)
    cached_value = cache.get(cache_key)
    if cached_value is not None:
        return cached_value
    count = get_unread_count(user)
    cache.set(cache_key, count, timeout=30)
    return count


def invalidate_unread_count_cache(user_id):
    cache.delete(_unread_count_cache_key(user_id))


def build_notifications_context(user, mark_read=False, notification_type=None, is_read=None, grouped=False, use_canonical_payload=False):
    if mark_read:
        mark_user_notifications_as_read(user)
        cache.set(_unread_count_cache_key(user.id), 0, timeout=30)
    
    if grouped:
        notifications = get_grouped_notifications(user, notification_type=notification_type, is_read=is_read)
    else:
        notifications = get_notifications_for_user(user, notification_type=notification_type, is_read=is_read)
    
    context = {
        'notifications': notifications,
        'filter_type': notification_type,
        'filter_read': is_read,
        'grouped': grouped,
        'use_canonical_payload': use_canonical_payload
    }
    
    return context


def build_unread_notification_html(user):
    count = get_cached_unread_count(user)
    html = '<span style="position: relative; display: inline-block;"><i aria-hidden="true" data-lucide="bell" class="vasto-icon"></i>'
    if count > 0:
        html += f'''\n            <span class="position-absolute badge rounded-pill bg-danger border border-light"\n                style="font-size: 0.65rem; padding: 0.3em 0.45em; min-width: 18px; text-align: center; top: -4px; right: -6px; z-index: 10;">\n                {count}\n                <span class="visually-hidden">unread messages</span>\n            </span>'''
    html += '</span><span style="font-size: 0.55rem; font-weight: 600; white-space: nowrap;">Notifications</span>'
    return html


def broadcast_unread_count(user_id, count=None):
    """Broadcast unread count update via WebSocket to notifications_{user_id}."""
    if count is None:
        count = get_unread_count_by_user_id(user_id)
    channel_layer = get_channel_layer()
    if channel_layer:
        try:
            async_to_sync(channel_layer.group_send)(
                f"notifications_{user_id}",
                {
                    'type': 'unread_count_update',
                    'count': count
                }
            )
        except Exception:
            pass


def mark_single_notification_as_read(user, notif_id):
    notification = get_notification_for_user(user, notif_id)
    if notification:
        notification.status = NotificationStatuses.READ.value
        notification.save(update_fields=['status'])
        invalidate_unread_count_cache(user.id)
        invalidate_group_unread_cache(user.id)
        broadcast_unread_count(user.id)
    return notification


def mark_all_user_notifications_as_read(user):
    updated = mark_user_notifications_as_read(user)
    invalidate_unread_count_cache(user.id)
    invalidate_group_unread_cache(user.id)
    broadcast_unread_count(user.id, count=0)
    return updated


def delete_single_notification(user, notif_id):
    notification = delete_notification(user, notif_id)
    if notification:
        invalidate_unread_count_cache(user.id)
        invalidate_group_unread_cache(user.id)
        broadcast_unread_count(user.id)
    return notification


def delete_all_user_notifications(user):
    result = delete_all_notifications(user)
    invalidate_unread_count_cache(user.id)
    invalidate_group_unread_cache(user.id)
    broadcast_unread_count(user.id, count=0)
    return result


def delete_user_read_notifications(user):
    result = delete_read_notifications(user)
    invalidate_unread_count_cache(user.id)
    invalidate_group_unread_cache(user.id)
    broadcast_unread_count(user.id)
    return result


def _group_unread_cache_key(user_id):
    return f'notif:group_unread:user:{user_id}'


def _group_activity_cache_key(user_id):
    return f'notif:group_activity:user:{user_id}'


def _group_any_unread_cache_key(user_id):
    return f'notif:group_any_unread:user:{user_id}'


def get_group_unread_counts(user, group_ids):
    """
    Get unread notification counts for multiple groups with caching.
    Returns a dictionary mapping group_id -> count.
    """
    if not user or not user.is_authenticated or not group_ids:
        return {}
    cache_key = _group_unread_cache_key(user.id)
    cached_value = cache.get(cache_key)
    
    if cached_value is not None:
        # Filter cached results to only requested groups
        return {gid: cached_value.get(gid, 0) for gid in group_ids}
    
    # Query database
    counts = get_unread_counts_for_groups(user, group_ids)
    
    # Cache for 10 seconds
    cache.set(cache_key, counts, timeout=10)
    
    return counts


def get_group_activity_by_type(user, group_ids):
    """
    Get unread notification counts per group broken down by type with caching.
    Returns a dictionary mapping group_id -> {'announcement': int, 'post': int, 'total': int}.
    """
    if not user or not user.is_authenticated or not group_ids:
        return {}
    cache_key = _group_activity_cache_key(user.id)
    cached_value = cache.get(cache_key)
    
    if cached_value is not None:
        return {gid: cached_value.get(gid, {'announcement': 0, 'post': 0, 'total': 0}) for gid in group_ids}
    
    activity = get_unread_group_activity_by_type(user, group_ids)
    cache.set(cache_key, activity, timeout=10)
    return activity


def check_any_unread_group_activity(user):
    """
    Check if the user has any unread notifications for any group.
    Cached check used by context processors for navigation red dot.
    """
    if not user or not user.is_authenticated:
        return False
    cache_key = _group_any_unread_cache_key(user.id)
    cached_value = cache.get(cache_key)
    if cached_value is not None:
        return cached_value
    
    has_any = has_any_unread_group_activity(user)
    cache.set(cache_key, has_any, timeout=15)
    return has_any


def mark_group_notifications_as_read_and_invalidate(user, group_id, exclude_types=None):
    """
    Mark unread notifications for a group as read and clear caches,
    optionally excluding specific notification types (e.g. GROUP_ANNOUNCEMENT).
    """
    if not user or not user.is_authenticated or not group_id:
        return 0
    updated = mark_group_notifications_as_read(user, group_id, exclude_types=exclude_types)
    invalidate_unread_count_cache(user.id)
    invalidate_group_unread_cache(user.id)
    broadcast_unread_count(user.id)
    return updated


def mark_announcement_as_read_and_invalidate(user, announcement_id, group_id=None):
    """
    Mark unread announcement notification as read and clear caches.
    """
    if not user or not user.is_authenticated or not announcement_id:
        return 0
    updated = mark_announcement_as_read(user, announcement_id, group_id=group_id)
    invalidate_unread_count_cache(user.id)
    invalidate_group_unread_cache(user.id)
    broadcast_unread_count(user.id)
    return updated


def invalidate_group_unread_cache(user_id):
    cache.delete(_group_unread_cache_key(user_id))
    cache.delete(_group_activity_cache_key(user_id))
    cache.delete(_group_any_unread_cache_key(user_id))


def resolve_notification_target_url(notification):
    """
    Resolve the intended destination URL for a notification.
    Ensures safe, valid redirects to posts, comments, groups, documents,
    profiles, or releases. Falls back to /notifications/.
    """
    if not notification:
        return '/notifications/'

    # 1. Explicit target_url attribute or metadata['url']
    target_url = getattr(notification, 'target_url', None)
    if not target_url and notification.metadata and isinstance(notification.metadata, dict):
        target_url = notification.metadata.get('url')
    
    # Avoid self-referencing loop if target_url points to /notifications/<id>
    notif_id_str = str(getattr(notification, 'notification_id', getattr(notification, 'id', '')))
    if target_url:
        target_url_str = str(target_url).strip()
        if notif_id_str and not target_url_str.startswith(f'/notifications/{notif_id_str}'):
            return target_url_str

    # 2. Try Standard Payload Adapter (extracts resource URL or navigation URL)
    try:
        from notifications.rendering.adapters import get_payload_adapter
        adapter = get_payload_adapter(notification)
        payload = adapter.to_standard_payload(notification)

        # Primary navigation URL
        if payload.navigation and payload.navigation.primary and payload.navigation.primary.url:
            nav_url = str(payload.navigation.primary.url).strip()
            if nav_url and nav_url != '#' and not (notif_id_str and nav_url.startswith(f'/notifications/{notif_id_str}')):
                return nav_url

        # Resource URL (Post, Document, etc.)
        if payload.resource and payload.resource.url:
            res_url = str(payload.resource.url).strip()
            if res_url and res_url != '#' and not (notif_id_str and res_url.startswith(f'/notifications/{notif_id_str}')):
                return res_url
    except Exception:
        payload = None

    # 3. Context-based destination resolution
    meta = notification.metadata if isinstance(notification.metadata, dict) else {}
    ctx_type = str(notification.context_type or '').upper()
    ctx_id = notification.context_id
    ntype = str(getattr(notification, 'notification_type', '') or '').upper()
    target_type_str = str(getattr(notification, 'target_type', '') or '').upper()

    # Messaging / Conversation targets
    if (
        ctx_type in ('CONVERSATION', 'MESSAGE', 'CHAT')
        or target_type_str in ('CONVERSATION', 'MESSAGE')
        or ntype in ('CHAT_MESSAGE', 'MESSAGE_SENT', 'CONVERSATION_MEMBER_ADDED')
    ):
        conv_id = (
            meta.get('conversation_id')
            or (ctx_id if ctx_type == 'CONVERSATION' else None)
            or (getattr(notification, 'target_id', None) if target_type_str == 'CONVERSATION' else None)
        )
        if conv_id:
            return f'/messaging/conversation/{conv_id}/'

    # App Download shares
    is_app_share = (
        ctx_type == 'APP'
        or target_type_str in ('APPDOWNLOAD', 'APP')
        or meta.get('resource_type') == 'APP'
    )
    if is_app_share:
        recipient = getattr(notification, 'recipient', None)
        username = getattr(recipient, 'username', '') if recipient else meta.get('recipient_username', '')
        if username:
            return f'/users/user/{username}/?tab=shared#shared-content'
        return '/downloads/'

    # Shared to group
    if ntype in ('POST_SHARED_TO_GROUP', 'SHARE_GROUP') or meta.get('group_id'):
        gid = meta.get('group_id')
        repost_id = meta.get('repost_share_id') or ctx_id or meta.get('target_id')
        if gid:
            if repost_id:
                try:
                    from notifications.rendering.adapters import _post_share_id
                    repost_sid = _post_share_id(repost_id) or repost_id
                except Exception:
                    repost_sid = repost_id
                return f'/groups/{gid}/#post-card-{repost_sid}'
            return f'/groups/{gid}/'

    # Direct post share
    if ntype in ('POST_SHARED', 'POST_SHARE', 'SHARE') and not meta.get('group_id'):
        recipient = getattr(notification, 'recipient', None)
        username = getattr(recipient, 'username', '') if recipient else meta.get('recipient_username', '')
        shared_post_id = meta.get('shared_post_id')
        if username:
            anchor = f'#shared-post-card-{shared_post_id}' if shared_post_id else '#shared-content'
            return f'/users/user/{username}/?tab=shared{anchor}'

    # Posts
    if ctx_type in ('POST', 'POSTS') and ctx_id:
        try:
            from notifications.rendering.adapters import _post_share_id
            sid = _post_share_id(ctx_id) or ctx_id
        except Exception:
            sid = ctx_id
        cid = meta.get('target_id') or meta.get('comment_id') or meta.get('parent_comment_id')
        if cid:
            return f'/post/{sid}/#comment-{cid}'
        return f'/post/{sid}/'

    # Documents
    if ctx_type == 'DOCUMENT' and ctx_id:
        doc_share_id = meta.get('doc_share_id') or meta.get('share_id')
        if doc_share_id:
            return f'/documents/document/{doc_share_id}/'
        return f'/documents/{ctx_id}/'

    # Groups
    if ctx_type in ('GROUP', 'GROUPS') and ctx_id:
        if 'ANNOUNCEMENT' in ntype:
            return f'/groups/{ctx_id}/#announcements'
        return f'/groups/{ctx_id}/'

    # Profiles (Follow, Pinch, etc.)
    actor_username = meta.get('actor_username')
    if actor_username:
        return f'/users/user/{actor_username}/'

    # Releases / What's new
    if 'RELEASE' in ntype:
        rel_id = meta.get('target_id') or meta.get('release_id')
        if rel_id:
            return f'/system/releases/{rel_id}/'
        return '/'

    # 4. Action URLs (only GET navigation actions like VIEW_*, SEE_*)
    if payload and payload.actions:
        for action in payload.actions:
            if getattr(action, 'method', 'GET') == 'GET':
                act_url = str(action.url).strip() if action.url else None
                if act_url and act_url != '#' and not act_url.startswith('/notifications/'):
                    return act_url

    return '/notifications/'

