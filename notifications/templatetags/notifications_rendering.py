"""
Template tags for notification rendering.
"""
from django import template
from notifications.rendering import render_notification as render_notification_service

register = template.Library()


@register.filter
def render_notification(notification):
    """
    Render a notification using the rendering service.
    
    Args:
        notification: NotificationObject instance
        
    Returns:
        Rendered HTML string
    """
    return render_notification_service(notification)

# Bootstrap icon names emitted by the payload adapters -> Lucide names.
# Values may arrive bare ("heart-fill") or prefixed ("bi-heart-fill").
_BI_TO_LUCIDE = {
    'people': 'users', 'person': 'user', 'person-fill': 'user-round',
    'person-plus': 'user-round-plus', 'person-plus-fill': 'user-round-plus',
    'person-dash': 'user-round-minus', 'grid-3x3': 'grid-3x3', 'book': 'book-open',
    'clipboard-check': 'clipboard-check', 'calendar-check': 'calendar-check',
    'chat-text': 'message-square-text', 'chat-square-text': 'message-square-text',
    'chat-dots': 'message-circle', 'chat-fill': 'message-circle',
    'chat-left-quote-fill': 'message-square-quote',
    'file-earmark-text': 'file-text', 'file-earmark-pdf': 'file-text',
    'file-earmark-word': 'file-text', 'file-earmark-image': 'image',
    'file-earmark-play': 'file-play', 'file-earmark': 'file',
    'check2-square': 'square-check-big', 'folder': 'folder',
    'bell': 'bell', 'bell-fill': 'bell', 'bell-slash': 'bell-off',
    'clock': 'clock', 'clock-fill': 'clock', 'clock-history': 'history',
    'chevron-down': 'chevron-down', 'chevron-up': 'chevron-up',
    'three-dots-vertical': 'ellipsis-vertical', 'star': 'star',
    'star-fill': 'star', 'search': 'search', 'plus': 'plus', 'x': 'x',
    'check-lg': 'check', 'check2': 'check', 'check2-all': 'check-check',
    'x-lg': 'x', 'x2': 'x', 'heart': 'heart', 'heart-fill': 'heart',
    'hand-index': 'pointer', 'eye': 'eye', 'box-arrow-up-right': 'external-link',
    'reply': 'reply', 'box-arrow-in-right': 'log-in', 'box-arrow-right': 'log-out',
    'share': 'share', 'bookmark': 'bookmark', 'download': 'download',
    'trash': 'trash-2', 'archive': 'archive', 'envelope': 'mail',
    'envelope-fill': 'mail', 'arrow-right': 'arrow-right',
}


@register.filter
def lucide_icon(value):
    """Map a Bootstrap icon name to its Lucide equivalent for data-lucide."""
    name = str(value or '').strip()
    for prefix in ('bi ', 'bi-'):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    # Unknown values pass through unchanged: callers may already supply a
    # Lucide name, and VastoIcons fails closed on anything unrecognised.
    return _BI_TO_LUCIDE.get(name, name)

