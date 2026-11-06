"""
Signal handlers for Groups app to emit PlatformEvents for the notification engine.
"""
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from groups.models import Membership, MembershipStatus
from notifications.events import publish_event, EventTypes, EventSources, EventActions


@receiver(post_save, sender=Membership)
@receiver(post_delete, sender=Membership)
def sync_chat_membership(sender, instance, **kwargs):
    from messaging.models import Conversation, ConversationMember
    conversation = Conversation.objects.filter(group_id=instance.group_id).first()
    if not conversation:
        return
    approved = Membership.objects.filter(
        group_id=instance.group_id, user_id=instance.user_id,
        status=MembershipStatus.APPROVED,
    ).exists()
    if approved:
        ConversationMember.objects.get_or_create(conversation=conversation, user_id=instance.user_id)
    else:
        ConversationMember.objects.filter(conversation=conversation, user_id=instance.user_id).delete()


from groups.models import Group


@receiver(post_save, sender=Group)
def sync_chat_name(sender, instance, **kwargs):
    from messaging.models import Conversation
    Conversation.objects.filter(group_id=instance.pk).update(name=instance.name)


@receiver(post_save, sender=Membership)
def membership_status_changed(sender, instance, created, **kwargs):
    """
    Emit event when a new membership is created (e.g., via background auto-enrollment).
    Explicit user workflows (approvals, rejections, invites) dispatch via group_notification_service
    to maintain accurate actor context and avoid duplicate notification events.
    """
    if getattr(instance, '_skip_signal_notification', False):
        return

    if created:
        if instance.status == MembershipStatus.APPROVED:
            # Never notify the group creator about their own group creation
            if instance.group.created_by_id and instance.user_id == instance.group.created_by_id:
                return

            # Direct enrollment without prior pending state (e.g. background course auto-enrollment)
            publish_event(
                event_type=EventTypes.GROUPS_MEMBER_APPROVED.value,
                source=EventSources.GROUPS.value,
                action=EventActions.APPROVED.value,
                actor=instance.group.created_by if instance.group.created_by else instance.user,
                target_type='Group',
                target_id=str(instance.group.id),
                context_type='GROUP',
                context_id=str(instance.group.id),
                metadata={
                    'group_name': instance.group.name,
                    'group_id': instance.group.id,
                    'user_id': str(instance.user.id),
                    'recipient_id': instance.user.id,
                    'user_username': instance.user.username,
                }
            )

