"""Preserve legacy history; retain original tables as an archival copy.

Irreversible: rolling back must not silently delete conversations used since rollout.
"""
from django.db import migrations


def import_history(apps, schema_editor):
    alias = schema_editor.connection.alias
    Group = apps.get_model('groups', 'Group')
    Membership = apps.get_model('groups', 'Membership')
    Legacy = apps.get_model('groups', 'GroupMessage')
    Conversation = apps.get_model('messaging', 'Conversation')
    Member = apps.get_model('messaging', 'ConversationMember')
    Message = apps.get_model('messaging', 'Message')
    Attachment = apps.get_model('messaging', 'MessageAttachment')
    Reaction = apps.get_model('messaging', 'MessageReaction')
    group_ids = Legacy.objects.using(alias).values('group_id').distinct()
    for group in Group.objects.using(alias).filter(pk__in=group_ids).iterator():
        conversation, _ = Conversation.objects.using(alias).get_or_create(
            group_id=group.pk,
            defaults={'type': 'group', 'name': group.name, 'is_encrypted': False},
        )
        for user_id in Membership.objects.using(alias).filter(group_id=group.pk, status='APPROVED').values_list('user_id', flat=True):
            Member.objects.using(alias).get_or_create(conversation_id=conversation.pk, user_id=user_id)
        mapping, replies = {}, []
        for old in Legacy.objects.using(alias).filter(group_id=group.pk).prefetch_related('attachments', 'reactions').order_by('created_at', 'id').iterator(chunk_size=200):
            message = Message.objects.using(alias).create(
                conversation_id=conversation.pk, sender_id=old.sender_id,
                content=old.content or '', message_type=old.message_type,
                status=old.status, is_encrypted=False,
            )
            Message.objects.using(alias).filter(pk=message.pk).update(created_at=old.created_at)
            mapping[old.pk] = message.pk
            if old.reply_to_id:
                replies.append((message.pk, old.reply_to_id))
            for attachment in old.attachments.all():
                Attachment.objects.using(alias).create(
                    message_id=message.pk, file=attachment.file.name,
                    file_type=attachment.attachment_type, caption=attachment.caption,
                    order=attachment.order,
                )
            for reaction in old.reactions.all():
                Reaction.objects.using(alias).create(
                    message_id=message.pk, user_id=reaction.user_id, emoji=reaction.emoji,
                )
        for message_id, old_reply_id in replies:
            if old_reply_id in mapping:
                Message.objects.using(alias).filter(pk=message_id).update(reply_to_id=mapping[old_reply_id])


class Migration(migrations.Migration):
    dependencies = [('messaging', '0020_conversation_group')]
    operations = [migrations.RunPython(import_history)]
