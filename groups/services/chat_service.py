"""Bridge group membership to the shared messaging infrastructure."""
from django.core.exceptions import PermissionDenied
from django.db import transaction

from groups.models import Group, Membership, MembershipStatus
from messaging.models import Conversation, ConversationMember


def accessible_conversations(user):
    """Check current group membership even if a bulk update bypassed signals."""
    from django.db.models import Q
    approved = Membership.objects.filter(
        user=user, status=MembershipStatus.APPROVED,
    ).values('group_id')
    return Conversation.objects.filter(members__user=user).filter(
        Q(group__isnull=True) | Q(group_id__in=approved)
    )


def can_access_conversation(conversation, user):
    return accessible_conversations(user).filter(pk=conversation.pk).exists()


@transaction.atomic
def open_group_chat(group_id, user):
    # Serialize first opens and membership reconciliation for this group.
    group = Group.objects.select_for_update().get(pk=group_id)
    approved_ids = set(Membership.objects.filter(
        group=group, status=MembershipStatus.APPROVED,
    ).values_list('user_id', flat=True))
    if user.pk not in approved_ids:
        raise PermissionDenied('You must be an approved member to access this chat.')
    conversation, _ = Conversation.objects.get_or_create(
        group=group,
        defaults={'type': Conversation.GROUP, 'name': group.name, 'is_encrypted': False},
    )
    ConversationMember.objects.filter(conversation=conversation).exclude(user_id__in=approved_ids).delete()
    ConversationMember.objects.bulk_create([
        ConversationMember(conversation=conversation, user_id=user_id)
        for user_id in approved_ids
    ], ignore_conflicts=True)
    return conversation
