"""Read-only contracts for the bundled Android reader; normal permissions apply."""
from uuid import UUID

from django.http import FileResponse, JsonResponse
from django.views.decorators.http import require_GET


def private_json(data, status=200):
    response = JsonResponse(data, status=status)
    response['Cache-Control'] = 'no-store'
    return response


@require_GET
def state(request):
    if not request.user.is_authenticated:
        return private_json({'error': 'sign_in_required'}, 401)
    from django.conf import settings
    from urllib.parse import urlsplit
    from groups.services.chat_service import accessible_conversations
    from messaging.models import Message
    from users.services.privacy import visible_posts_for
    try:
        ids = [int(value) for value in request.GET.get('messages', '').split(',') if value][:1000]
        reels = [UUID(value) for value in request.GET.get('reels', '').split(',') if value][:20]
    except (ValueError, TypeError):
        return private_json({'error': 'invalid_ids'}, 400)
    conversations = accessible_conversations(request.user).select_related('group').prefetch_related('members__user').distinct()
    rows = []
    for conversation in conversations:
        peers = [member.user for member in conversation.members.all() if member.user_id != request.user.pk]
        name = (conversation.group.name if conversation.group_id else conversation.name) or ', '.join(
            peer.get_full_name() or peer.username for peer in peers
        ) or 'Saved messages'
        rows.append({'id': conversation.pk, 'name': name, 'type': conversation.type,
                     'updated_at': conversation.updated_at.isoformat()})
    messages = Message.objects.filter(id__in=ids, conversation_id__in=[row['id'] for row in rows]).select_related('sender', 'reply_to__sender')
    revisions = [{'id': message.pk, 'conversation': message.conversation_id,
                  'deleted': message.is_deleted,
                  'version': message.edited_at.isoformat() if message.edited_at else '',
                  'reply': message_record(message, request.user).get('reply')}
                 for message in messages]
    allowed_reels = visible_posts_for(request.user).filter(share_id__in=reels, allow_downloads=True).values_list('share_id', flat=True)
    history = []
    if request.GET.get('conversation'):
        try:
            conversation_id = int(request.GET['conversation'])
            before = int(request.GET.get('before', 9223372036854775807))
        except (ValueError, TypeError):
            return private_json({'error': 'invalid_conversation'}, 400)
        if conversation_id not in {row['id'] for row in rows}:
            return private_json({'error': 'conversation_unavailable'}, 403)
        history = [message_record(message, request.user) for message in Message.objects.filter(
            conversation_id=conversation_id, id__lt=before,
        ).select_related('sender', 'reply_to__sender').order_by('-id')[:100]][::-1]
    origins = {'https://pwaninet.app', 'https://cdn.jsdelivr.net', 'https://cdnjs.cloudflare.com',
               'https://fonts.googleapis.com', 'https://fonts.gstatic.com'}
    for configured in (getattr(settings, 'MEDIA_URL', '/media/'), getattr(settings, 'STATIC_URL', '/static/'), getattr(settings, 'AWS_S3_ENDPOINT_URL', ''),
                       'https://' + getattr(settings, 'AWS_S3_CUSTOM_DOMAIN', '')):
        parsed = urlsplit(configured)
        if parsed.scheme == 'https' and parsed.netloc:
            origins.add(f'{parsed.scheme}://{parsed.netloc}')
    memberships = getattr(request.user, 'group_memberships', None)
    groups = list(memberships.filter(status='APPROVED').values_list('group_id', flat=True)) if memberships is not None else []
    return private_json({'groups': groups, 'asset_origins': sorted(origins), 'owner': str(request.user.pk), 'name': request.user.get_full_name() or request.user.username,
                         'conversations': rows, 'revisions': revisions, 'messages': history,
                         'reels': [str(value) for value in allowed_reels]})


@require_GET
def reel(request, share_id):
    if not request.user.is_authenticated:
        return private_json({'error': 'sign_in_required'}, 401)
    from users.services.privacy import visible_posts_for
    post = visible_posts_for(request.user).filter(share_id=share_id, allow_downloads=True).first()
    if not post or not post.video:
        return private_json({'error': 'unavailable'}, 404)
    try:
        response = FileResponse(post.video.open('rb'), content_type='video/mp4')
    except (OSError, ValueError):
        return private_json({'error': 'unavailable'}, 404)
    response['Cache-Control'] = 'no-store'
    # The native downloader checks the account again before committing the file.
    response['X-Vasto-Account'] = str(request.user.pk)
    return response


def message_record(message, user):
    deleted = message.is_deleted
    encrypted = message.is_encrypted and not deleted
    text = ('This message was deleted' if deleted else
            'Open online to decrypt this message' if encrypted else message.content or '')
    result = {'id': message.pk, 'sender': message.sender.get_full_name() or message.sender.username,
              'own': message.sender_id == user.pk, 'text': text,
              'time': message.created_at.isoformat(), 'version': message.edited_at.isoformat() if message.edited_at else '',
              'encrypted': encrypted, 'attachment': '' if deleted else message.attachment_type or ''}
    reply = message.reply_to
    if reply and not deleted and reply.conversation_id == message.conversation_id:
        result['reply'] = {'sender': reply.sender.get_full_name() or reply.sender.username,
                           'text': 'This message was deleted' if reply.is_deleted else
                           'Encrypted message' if reply.is_encrypted else reply.content or 'Attachment'}
    return result
