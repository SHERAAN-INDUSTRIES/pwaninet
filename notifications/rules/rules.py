"""
Notification Rules for vasto Notification Engine v2

This module defines notification rules that transform platform events into user notifications.
Following the specification, each rule has: trigger, conditions, recipients, priority, category, delivery policy, aggregation policy.
"""
from dataclasses import dataclass
from typing import List, Callable, Optional, Dict, Any
from enum import Enum
from django.contrib.auth import get_user_model
from django.db.models import Q

User = get_user_model()


def _post_url(post_pk, anchor=''):
    """Return the canonical post URL using UUID share_id for the given integer PK."""
    if not post_pk:
        return f'/post/{post_pk}/'
    try:
        from posts.models import Post
        share_id = Post.objects.values_list('share_id', flat=True).get(pk=post_pk)
        return f'/post/{share_id}/{anchor}'
    except Exception:
        return f'/post/{post_pk}/'


class AggregationPolicy(Enum):
    """Aggregation policy for notifications."""
    NEVER = "NEVER"
    ALLOWED = "ALLOWED"
    REQUIRED = "REQUIRED"


@dataclass
class NotificationRule:
    """
    A notification rule definition.
    
    Rules are evaluated against platform events to determine:
    - Whether a notification should be created
    - Who should receive it
    - Its priority, category, and policies
    """
    # Rule identification
    name: str
    trigger: str  # Event type that triggers this rule
    
    # Notification properties (no defaults)
    notification_type: str
    category: str
    priority: str
    recipients: Callable[[Dict[str, Any]], List[int]]  # Returns list of user IDs
    
    # Rule evaluation (with default)
    condition: Optional[Callable[[Dict[str, Any]], bool]] = None
    
    # Policies (with defaults)
    delivery_policy: str = "IMMEDIATE"
    aggregation_policy: str = "ALLOWED"
    
    # Title and summary generation (with default)
    title_template: Optional[str] = None
    summary_template: Optional[str] = None
    
    # Action generation (with default)
    actions: Optional[Callable[[Dict[str, Any]], List[Dict[str, Any]]]] = None


# ============================================================================
# Recipient Resolution Functions
# ============================================================================

def _post_owner_recipient(event_data: Dict[str, Any]) -> List[int]:
    """Recipient: Post owner."""
    from posts.models import Post
    
    # For comment events, the post ID is in context_id, not target_id
    context_id = event_data.get('context_id')
    target_type = event_data.get('target_type')
    
    post_id = None
    if target_type == 'Comment' and context_id:
        post_id = context_id
    else:
        post_id = event_data.get('target_id')
    
    if not post_id:
        return []
    
    try:
        post = Post.objects.get(id=post_id)
        return [post.author.id]
    except Post.DoesNotExist:
        return []


def _comment_author_recipient(event_data: Dict[str, Any]) -> List[int]:
    """Recipient: Comment author."""
    from posts.models import Comment
    
    target_id = event_data.get('target_id')
    if not target_id:
        return []
    
    try:
        comment = Comment.objects.get(id=target_id)
        return [comment.author.id]
    except Comment.DoesNotExist:
        return []


def _parent_comment_author_recipient(event_data: Dict[str, Any]) -> List[int]:
    """Recipient: Parent comment author."""
    from posts.models import Comment
    
    target_id = event_data.get('target_id')
    if not target_id:
        return []
    
    try:
        comment = Comment.objects.get(id=target_id)
        if comment.parent_comment:
            return [comment.parent_comment.author.id]
        return []
    except Comment.DoesNotExist:
        return []


def _group_admins_recipient(event_data: Dict[str, Any]) -> List[int]:
    """Recipient: Group admins (excluding the actor)."""
    from groups.models import Membership, MembershipRole, MembershipStatus
    
    target_id = (
        event_data.get('target_id') or 
        event_data.get('context_id') or 
        (event_data.get('metadata') or {}).get('group_id')
    )
    if not target_id:
        return []
    
    actor_id = event_data.get('actor_id')
    try:
        admin_memberships = Membership.objects.filter(
            group_id=target_id,
            role=MembershipRole.ADMIN,
            status=MembershipStatus.APPROVED
        ).select_related('user')
        admin_ids = [m.user.id for m in admin_memberships]
        if actor_id:
            admin_ids = [uid for uid in admin_ids if str(uid) != str(actor_id)]
        return admin_ids
    except Exception:
        return []


def _group_members_recipient(event_data: Dict[str, Any]) -> List[int]:
    """Recipient: All approved group members except actor."""
    from groups.models import Membership, MembershipStatus
    group_id = (
        event_data.get('context_id') or 
        event_data.get('target_id') or 
        (event_data.get('metadata') or {}).get('group_id')
    )
    actor_id = event_data.get('actor_id')
    if not group_id:
        return []
    try:
        memberships = Membership.objects.filter(
            group_id=group_id,
            status=MembershipStatus.APPROVED
        )
        if actor_id:
            memberships = memberships.exclude(user_id=actor_id)
        return list(memberships.values_list('user_id', flat=True))
    except Exception:
        return []


def _group_member_recipient(event_data: Dict[str, Any]) -> List[int]:
    """Recipient: Specific group member or target user."""
    # Check explicit recipient_id or user_id in event or metadata
    metadata = event_data.get('metadata') or {}
    recipient_id = (
        event_data.get('recipient_id') or 
        metadata.get('recipient_id') or 
        metadata.get('user_id') or 
        event_data.get('user_id')
    )
    if recipient_id:
        try:
            user = User.objects.get(id=recipient_id)
            return [user.id]
        except (User.DoesNotExist, ValueError):
            pass

    # Check context_id if context_type is User/USER
    context_type = str(event_data.get('context_type') or '').upper()
    if context_type == 'USER':
        context_id = event_data.get('context_id')
        if context_id:
            try:
                user = User.objects.get(id=context_id)
                return [user.id]
            except (User.DoesNotExist, ValueError):
                pass

    # Check target_id if target_type is User/USER
    target_type = str(event_data.get('target_type') or '').upper()
    if target_type == 'USER':
        target_id = event_data.get('target_id')
        if target_id:
            try:
                user = User.objects.get(id=target_id)
                return [user.id]
            except (User.DoesNotExist, ValueError):
                pass

    return []



def _followed_user_recipient(event_data: Dict[str, Any]) -> List[int]:
    """Recipient: User who was followed."""
    target_id = event_data.get('target_id')
    if not target_id:
        return []
    
    try:
        user = User.objects.get(id=target_id)
        return [user.id]
    except User.DoesNotExist:
        return []


def _followers_of_author_recipient(event_data: Dict[str, Any]) -> List[int]:
    """Recipient: Users who follow the post author (for new post notifications)."""
    actor_id = event_data.get('actor_id')
    context_type = event_data.get('context_type')
    group_id = event_data.get('context_id') if context_type == 'GROUP' else event_data.get('metadata', {}).get('group_id')
    
    if not actor_id:
        return []
    
    try:
        author = User.objects.get(id=actor_id)
        
        if group_id:
            # For group posts, send to group members
            from groups.models import Membership, MembershipStatus
            return list(Membership.objects.filter(
                group_id=group_id,
                status=MembershipStatus.APPROVED
            ).exclude(user_id=actor_id).values_list('user_id', flat=True))
        else:
            # For global posts, send to friends (followers and following) of the author
            from users.models import Follow
            followers = set(Follow.objects.filter(followed=author).values_list('follower_id', flat=True))
            following = set(Follow.objects.filter(follower=author).values_list('followed_id', flat=True))
            recipients = (followers | following) - {author.id}
            return list(recipients)
    except User.DoesNotExist:
        return []


def _pinched_user_recipient(event_data: Dict[str, Any]) -> List[int]:
    """Recipient: User who was pinched."""
    target_id = event_data.get('target_id')
    if not target_id:
        return []
    
    try:
        user = User.objects.get(id=target_id)
        return [user.id]
    except User.DoesNotExist:
        return []


def _post_sharer_recipient(event_data: Dict[str, Any]) -> List[int]:
    """Recipient: User who received the shared post."""
    # Get recipient from metadata (recipient_id) or audience field
    metadata = event_data.get('metadata', {})
    recipient_id = metadata.get('recipient_id')
    
    if recipient_id:
        try:
            user = User.objects.get(id=int(recipient_id))
            return [user.id]
        except (User.DoesNotExist, ValueError):
            return []
    
    # Fallback to audience field
    audience = event_data.get('audience')
    if audience:
        try:
            user = User.objects.get(id=int(audience))
            return [user.id]
        except (User.DoesNotExist, ValueError):
            return []
    
    return []


def _original_post_author_recipient(event_data: Dict[str, Any]) -> List[int]:
    """Recipient: Original post author (for reposts)."""
    from posts.models import Post
    
    target_id = event_data.get('target_id')
    if not target_id:
        return []
    
    try:
        post = Post.objects.get(id=target_id)
        return [post.author.id]
    except Post.DoesNotExist:
        return []


def _parent_comment_author_not_self_condition(event_data: Dict[str, Any]) -> bool:
    """Condition: Only notify if actor is not the parent comment author."""
    actor_id = event_data.get('actor_id')
    parent_comment_id = event_data.get('target_id')
    
    if not actor_id or not parent_comment_id:
        return False
    
    try:
        from posts.models import Comment
        parent_comment = Comment.objects.get(id=parent_comment_id)
        return int(actor_id) != parent_comment.author.id
    except Comment.DoesNotExist:
        return False


def _parent_comment_author_recipient(event_data: Dict[str, Any]) -> List[int]:
    """Recipient: Parent comment author (for comment replies)."""
    from posts.models import Comment
    
    target_id = event_data.get('target_id')
    if not target_id:
        return []
    
    try:
        parent_comment = Comment.objects.get(id=target_id)
        return [parent_comment.author.id]
    except Comment.DoesNotExist:
        return []


def _comment_author_recipient(event_data: Dict[str, Any]) -> List[int]:
    """Recipient: Comment author (for comment likes)."""
    from posts.models import Comment
    
    target_id = event_data.get('target_id')
    if not target_id:
        return []
    
    try:
        comment = Comment.objects.get(id=target_id)
        return [comment.author.id]
    except Comment.DoesNotExist:
        return []


def _document_owner_recipient(event_data: Dict[str, Any]) -> List[int]:
    """Recipient: Document owner."""
    from documents.documents.models import Document
    
    target_id = event_data.get('target_id')
    if not target_id:
        return []
    
    try:
        document = Document.objects.get(id=target_id)
        return [document.uploaded_by.id]
    except Document.DoesNotExist:
        return []


def _timetable_owner_recipient(event_data: Dict[str, Any]) -> List[int]:
    """Recipient: Timetable uploader."""
    from documents.academic.models import ProgrammeTimetable

    target_id = event_data.get('target_id')
    if not target_id:
        return []

    try:
        timetable = ProgrammeTimetable.objects.get(id=target_id)
        return [timetable.uploaded_by.id] if timetable.uploaded_by else []
    except ProgrammeTimetable.DoesNotExist:
        return []


def _course_creator_recipient(event_data: Dict[str, Any]) -> List[int]:
    """Recipient: Course creator."""
    from courses.models import Course
    
    target_id = event_data.get('target_id')
    if not target_id:
        return []
    
    try:
        course = Course.objects.get(id=target_id)
        return [course.created_by.id]
    except Course.DoesNotExist:
        return []


def _course_members_recipient(event_data: Dict[str, Any]) -> List[int]:
    """Recipient: All members of a course."""
    from courses.models import Course
    
    context_id = event_data.get('context_id')
    if not context_id:
        return []
    
    try:
        course = Course.objects.get(id=context_id)
        # Get all enrolled students in the course
        enrolled_users = course.enrolled_users.all()
        return [user.id for user in enrolled_users]
    except Course.DoesNotExist:
        return []


def _academic_unit_members_recipient(event_data: Dict[str, Any]) -> List[int]:
    """Recipient: All members of an academic unit (for document uploads)."""
    from documents.documents.models import AcademicUnit
    from django.contrib.auth import get_user_model
    
    User = get_user_model()
    context_id = event_data.get('context_id')
    if not context_id:
        return []
    
    try:
        academic_unit = AcademicUnit.objects.get(id=context_id)
        # Get all users enrolled in courses that include this academic unit
        from courses.models import CourseAcademicUnit
        course_academic_units = CourseAcademicUnit.objects.filter(academic_unit=academic_unit)
        enrolled_users = set()
        for cau in course_academic_units:
            # Find users enrolled in this course
            enrolled_users.update(
                User.objects.filter(course=cau.course).values_list('id', flat=True)
            )
        return list(enrolled_users)
    except AcademicUnit.DoesNotExist:
        return []


def _message_recipient_recipient(event_data: Dict[str, Any]) -> List[int]:
    """Recipient: Conversation members (excluding sender and muted members)."""
    from messaging.models import Message, Conversation

    target_id = event_data.get('target_id')
    target_type = str(event_data.get('target_type') or '').lower()
    actor_id = event_data.get('actor_id')
    if not target_id:
        return []

    try:
        if target_type == 'conversation':
            conversation = Conversation.objects.get(id=target_id)
            qs = conversation.members.filter(is_muted=False)
            if actor_id:
                qs = qs.exclude(user_id=actor_id)
            return list(qs.values_list('user_id', flat=True))
        else:
            message = Message.objects.get(id=target_id)
            qs = message.conversation.members.filter(is_muted=False).exclude(user=message.sender)
            return list(qs.values_list('user_id', flat=True))
    except (Message.DoesNotExist, Conversation.DoesNotExist, ValueError):
        return []


def _conversation_member_recipient(event_data: Dict[str, Any]) -> List[int]:
    """Recipient: Added conversation member (excluding actor if self-added)."""
    metadata = event_data.get('metadata') or {}
    context_id = event_data.get('context_id') or metadata.get('member_id')
    actor_id = event_data.get('actor_id')
    if not context_id:
        return []

    try:
        user = User.objects.get(id=context_id)
        if actor_id and str(user.id) == str(actor_id):
            return []
        return [user.id]
    except (User.DoesNotExist, ValueError):
        return []


# ============================================================================
# Condition Functions
# ============================================================================

def _post_author_not_self_condition(event_data: Dict[str, Any]) -> bool:
    """Condition: Actor is not the post author and it's not a reply."""
    actor_id = event_data.get('actor_id')
    post_author_id = event_data.get('post_author_id')
    parent_comment_id = event_data.get('parent_comment_id')
    
    # Exclude replies (they should be handled by POST_COMMENT_REPLY_RULE)
    if parent_comment_id:
        return False
    
    return actor_id != post_author_id


def _actor_not_recipient_condition(event_data: Dict[str, Any]) -> bool:
    """Condition: Actor is not the recipient."""
    actor_id = event_data.get('actor_id')
    if not actor_id:
        return True
    
    metadata = event_data.get('metadata') or {}
    recipient_id = (
        event_data.get('recipient_id') or 
        metadata.get('recipient_id') or 
        metadata.get('user_id') or 
        event_data.get('user_id')
    )
    if recipient_id and str(actor_id) == str(recipient_id):
        return False
        
    recipient_ids = event_data.get('recipient_user_ids', [])
    if recipient_ids and any(str(actor_id) == str(r) for r in recipient_ids):
        return False
        
    return True


# Posts Rules
POST_LIKE_RULE = NotificationRule(
    name="post_like",
    trigger="posts.post.liked",
    condition=_post_author_not_self_condition,
    notification_type="LIKE",
    category="SOCIAL",
    priority="LOW",
    delivery_policy="IMMEDIATE",
    aggregation_policy="ALLOWED",
    recipients=_post_owner_recipient,
    title_template="{actor_username} liked your post",
    summary_template="Your post received new likes",
    actions=lambda event: [
        {
            'action_type': 'VIEW',
            'label': 'View Post',
            'url': _post_url(event.get('target_id')),
            'method': 'GET',
            'is_primary': True,
            'order': 0,
            'style': 'primary'
        }
    ]
)

POST_COMMENT_RULE = NotificationRule(
    name="post_comment",
    trigger="posts.comment.created",
    condition=_post_author_not_self_condition,
    notification_type="COMMENT",
    category="SOCIAL",
    priority="NORMAL",
    delivery_policy="IMMEDIATE",
    aggregation_policy="ALLOWED",
    recipients=_post_owner_recipient,
    title_template="{actor_username} commented on your post",
    summary_template="Your post has new comments"
)

POST_COMMENT_REPLY_RULE = NotificationRule(
    name="post_comment_reply",
    trigger="posts.comment.replied",
    condition=_parent_comment_author_not_self_condition,
    notification_type="COMMENT_REPLY",
    category="SOCIAL",
    priority="NORMAL",
    delivery_policy="IMMEDIATE",
    aggregation_policy="ALLOWED",
    recipients=_parent_comment_author_recipient,
    title_template="{actor_username} replied to your comment",
    summary_template="Your comment has new replies"
)

COMMENT_LIKED_RULE = NotificationRule(
    name="comment_liked",
    trigger="posts.comment.liked",
    condition=None,
    notification_type="COMMENT_LIKE",
    category="SOCIAL",
    priority="LOW",
    delivery_policy="IMMEDIATE",
    aggregation_policy="ALLOWED",
    recipients=_comment_author_recipient,
    title_template="{actor_username} liked your comment",
    summary_template="Your comment received likes"
)

POST_SHARED_RULE = NotificationRule(
    name="post_shared",
    trigger="posts.post.shared",
    condition=None,
    notification_type="SHARE",
    category="SOCIAL",
    priority="NORMAL",
    delivery_policy="IMMEDIATE",
    aggregation_policy="ALLOWED",
    recipients=_post_sharer_recipient,
    title_template="{actor_username} shared your post",
    summary_template="Your post was shared"
)

POST_SHARED_TO_GROUP_RULE = NotificationRule(
    name="post_shared_to_group",
    trigger="posts.post.shared_to_group",
    condition=None,
    notification_type="SHARE",
    category="SOCIAL",
    priority="NORMAL",
    delivery_policy="IMMEDIATE",
    aggregation_policy="ALLOWED",
    recipients=_group_member_recipient,
    title_template="{actor_username} shared a post to the group",
    summary_template="New post shared to group"
)

POST_REPORTED_RULE = NotificationRule(
    name="post_reported",
    trigger="posts.post.reported",
    condition=None,
    notification_type="SECURITY",
    category="SECURITY",
    priority="HIGH",
    delivery_policy="IMMEDIATE",
    aggregation_policy="NEVER",
    recipients=lambda event_data: [int(event_data.get('audience', 0))] if event_data.get('audience') else [],
    title_template="Post reported: {report_reason}",
    summary_template="New post report",
)

POST_REPOSTED_RULE = NotificationRule(
    name="post_reposted",
    trigger="posts.post.reposted",
    condition=None,
    notification_type="POST_REPOSTED",
    category="SOCIAL",
    priority="NORMAL",
    delivery_policy="IMMEDIATE",
    aggregation_policy="ALLOWED",
    recipients=_original_post_author_recipient,
    title_template="{actor_username} reposted your post",
    summary_template="Your post was reposted"
)

POST_CREATED_RULE = NotificationRule(
    name="post_created",
    trigger="posts.post.created",
    condition=lambda event: not bool(event.get('metadata', {}).get('is_document_share')),
    notification_type="POST_CREATED",
    category="SOCIAL",
    priority="NORMAL",
    delivery_policy="IMMEDIATE",
    aggregation_policy="ALLOWED",
    recipients=_followers_of_author_recipient,
    title_template="{actor_username} posted a new update",
    summary_template="New post from someone you follow"
)

POST_DOCUMENT_SHARED_RULE = NotificationRule(
    name="post_document_shared",
    trigger="posts.post.created",
    condition=lambda event: bool(event.get('metadata', {}).get('is_document_share')),
    notification_type="DOCUMENT_SHARED",
    category="SOCIAL",
    priority="NORMAL",
    delivery_policy="IMMEDIATE",
    aggregation_policy="ALLOWED",
    recipients=_followers_of_author_recipient,
    title_template="{actor_username} shared a document to view: {document_title}",
    summary_template="{actor_username} shared '{document_title}'. Tap to view."
)

# Groups Rules
GROUP_INVITE_RULE = NotificationRule(
    name="group_invite",
    trigger="groups.member.invited",
    condition=_actor_not_recipient_condition,
    notification_type="INVITE",
    category="WORKSPACE",
    priority="HIGH",
    delivery_policy="IMMEDIATE",
    aggregation_policy="NEVER",
    recipients=_group_member_recipient,
    title_template="You have been invited to join {group_name}",
    summary_template="Group invitation",
    actions=lambda event: [
        {
            'action_type': 'ACCEPT',
            'label': 'Accept',
            'url': f"/groups/invite/respond/{event.get('notification_id')}/accept/",
            'method': 'POST',
            'is_primary': True,
            'order': 0
        },
        {
            'action_type': 'DECLINE',
            'label': 'Decline',
            'url': f"/groups/invite/respond/{event.get('notification_id')}/decline/",
            'method': 'POST',
            'is_primary': False,
            'order': 1
        }
    ]
)

GROUP_REQUEST_RULE = NotificationRule(
    name="group_request",
    trigger="groups.member.requested",
    condition=None,
    notification_type="GROUP_REQUEST",
    category="WORKSPACE",
    priority="HIGH",
    delivery_policy="IMMEDIATE",
    aggregation_policy="ALLOWED",
    recipients=_group_admins_recipient,
    title_template="{actor_username} requested to join {group_name}",
    summary_template="Group join request",
    actions=lambda event: [
        {
            'action_type': 'APPROVE',
            'label': 'Approve',
            'url': f"/groups/{event.get('target_id') or event.get('group_id')}/approve/{event.get('user_id') or event.get('actor_id')}/",
            'method': 'POST',
            'is_primary': True,
            'order': 0
        },
        {
            'action_type': 'REJECT',
            'label': 'Reject',
            'url': f"/groups/{event.get('target_id') or event.get('group_id')}/reject/{event.get('user_id') or event.get('actor_id')}/",
            'method': 'POST',
            'is_primary': False,
            'order': 1
        }
    ]
)

GROUP_APPROVED_RULE = NotificationRule(
    name="group_approved",
    trigger="groups.member.approved",
    condition=_actor_not_recipient_condition,
    notification_type="GROUP_APPROVED",
    category="WORKSPACE",
    priority="HIGH",
    delivery_policy="IMMEDIATE",
    aggregation_policy="NEVER",
    recipients=_group_member_recipient,
    title_template="You have been approved to join {group_name}",
    summary_template="Group request approved",
    actions=lambda event: [
        {
            'action_type': 'VIEW_GROUP',
            'label': 'View Group',
            'url': f"/groups/{event.get('target_id') or event.get('group_id')}/",
            'method': 'GET',
            'is_primary': True,
            'order': 0
        }
    ]
)

GROUP_REJECTED_RULE = NotificationRule(
    name="group_rejected",
    trigger="groups.member.rejected",
    condition=_actor_not_recipient_condition,
    notification_type="GROUP_REJECTED",
    category="WORKSPACE",
    priority="NORMAL",
    delivery_policy="IMMEDIATE",
    aggregation_policy="NEVER",
    recipients=_group_member_recipient,
    title_template="Your request to join {group_name} was declined",
    summary_template="Group request rejected"
)

GROUP_ANNOUNCEMENT_RULE = NotificationRule(
    name="group_announcement",
    trigger="groups.announcement.created",
    condition=None,
    notification_type="GROUP_ANNOUNCEMENT",
    category="WORKSPACE",
    priority="HIGH",
    delivery_policy="IMMEDIATE",
    aggregation_policy="ALLOWED",
    recipients=_group_members_recipient,
    title_template="New announcement in {group_name}",
    summary_template="Group Announcement",
    actions=lambda event: [
        {
            'action_type': 'VIEW_ANNOUNCEMENT',
            'label': 'Read Announcement',
            'url': f"/groups/{event.get('context_id') or event.get('group_id')}/#announcements",
            'method': 'GET',
            'is_primary': True,
            'order': 0
        }
    ]
)

# Users Rules
USER_FOLLOW_RULE = NotificationRule(
    name="user_follow",
    trigger="users.user.followed",
    condition=None,
    notification_type="FOLLOW",
    category="SOCIAL",
    priority="LOW",
    delivery_policy="IMMEDIATE",
    aggregation_policy="ALLOWED",
    recipients=_followed_user_recipient,
    title_template="{actor_username} started following you",
    summary_template="You have a new follower"
)

USER_PINCH_RULE = NotificationRule(
    name="user_pinch",
    trigger="users.user.pinched",
    condition=None,
    notification_type="PINCH",
    category="SOCIAL",
    priority="LOW",
    delivery_policy="IMMEDIATE",
    aggregation_policy="ALLOWED",
    recipients=_pinched_user_recipient,
    title_template="{actor_username} pinched your profile",
    summary_template="Your profile was pinched"
)

# Documents Rules
DOCUMENT_UPLOADED_RULE = NotificationRule(
    name="document_uploaded",
    trigger="documents.document.uploaded",
    condition=None,
    notification_type="DOCUMENT",
    category="DOCUMENT",
    priority="NORMAL",
    delivery_policy="IMMEDIATE",
    aggregation_policy="ALLOWED",
    recipients=_academic_unit_members_recipient,
    title_template="{actor_username} uploaded a new document to your academic unit",
    summary_template="New document available in {academic_unit_code}"
)

DOCUMENT_PUBLISHED_RULE = NotificationRule(
    name="document_published",
    trigger="documents.document.published",
    condition=None,
    notification_type="DOCUMENT",
    category="DOCUMENT",
    priority="NORMAL",
    delivery_policy="IMMEDIATE",
    aggregation_policy="ALLOWED",
    recipients=_document_owner_recipient,
    title_template="{actor_username} published a document",
    summary_template="Document published"
)

DOCUMENT_DOWNLOADED_RULE = NotificationRule(
    name="document_downloaded",
    trigger="documents.document.downloaded",
    condition=None,
    notification_type="DOCUMENT",
    category="DOCUMENT",
    priority="LOW",
    delivery_policy="IMMEDIATE",
    aggregation_policy="ALLOWED",
    recipients=_document_owner_recipient,
    title_template="{actor_username} downloaded your document",
    summary_template="Your document was downloaded"
)

DOCUMENT_BOOKMARKED_RULE = NotificationRule(
    name="document_bookmarked",
    trigger="documents.document.bookmarked",
    condition=None,
    notification_type="DOCUMENT",
    category="DOCUMENT",
    priority="LOW",
    delivery_policy="IMMEDIATE",
    aggregation_policy="ALLOWED",
    recipients=_document_owner_recipient,
    title_template="{actor_username} bookmarked your document",
    summary_template="Your document was bookmarked"
)

DOCUMENT_RATED_RULE = NotificationRule(
    name="document_rated",
    trigger="documents.document.rated",
    condition=None,
    notification_type="DOCUMENT",
    category="DOCUMENT",
    priority="LOW",
    delivery_policy="IMMEDIATE",
    aggregation_policy="ALLOWED",
    recipients=_document_owner_recipient,
    title_template="{actor_username} rated your document",
    summary_template="Your document was rated"
)

# Courses Rules
COURSE_CREATED_RULE = NotificationRule(
    name="course_created",
    trigger="courses.course.created",
    condition=None,
    notification_type="WORKSPACE",
    category="ACADEMIC",
    priority="NORMAL",
    delivery_policy="IMMEDIATE",
    aggregation_policy="ALLOWED",
    recipients=_course_creator_recipient,
    title_template="{actor_username} created a new course",
    summary_template="New course created"
)

UNIT_CREATED_RULE = NotificationRule(
    name="unit_created",
    trigger="courses.unit.created",
    condition=None,
    notification_type="WORKSPACE",
    category="ACADEMIC",
    priority="NORMAL",
    delivery_policy="IMMEDIATE",
    aggregation_policy="ALLOWED",
    recipients=_course_creator_recipient,
    title_template="{actor_username} created a new unit",
    summary_template="New unit created"
)

# Messaging Rules
MESSAGE_SENT_RULE = NotificationRule(
    name="message_sent",
    trigger="messaging.message.sent",
    condition=None,
    notification_type="CHAT_MESSAGE",
    category="MESSAGING",
    priority="HIGH",
    delivery_policy="IMMEDIATE",
    aggregation_policy="NEVER",
    recipients=_message_recipient_recipient,
    title_template="{actor_username} sent you a message",
    summary_template="New message"
)

CONVERSATION_MEMBER_ADDED_RULE = NotificationRule(
    name="conversation_member_added",
    trigger="messaging.conversation.member_added",
    condition=None,
    notification_type="CHAT_MESSAGE",
    category="MESSAGING",
    priority="NORMAL",
    delivery_policy="IMMEDIATE",
    aggregation_policy="ALLOWED",
    recipients=_conversation_member_recipient,
    title_template="{actor_username} added you to a conversation",
    summary_template="Added to conversation"
)

# Courses Rules
COURSE_ASSIGNMENT_PUBLISHED_RULE = NotificationRule(
    name="course_assignment_published",
    trigger="courses.assignment.published",
    condition=None,
    notification_type="ASSIGNMENT",
    category="ACADEMIC",
    priority="HIGH",
    delivery_policy="IMMEDIATE",
    aggregation_policy="NEVER",
    recipients=_group_member_recipient,
    title_template="New assignment available: {assignment_title}",
    summary_template="{course_name} - {assignment_title}",
    actions=lambda event: [
        {
            'action_type': 'OPEN',
            'label': 'Open Assignment',
            'url': f"/courses/{event.get('course_id')}/assignments/{event.get('assignment_id')}",
            'method': 'GET',
            'is_primary': True,
            'order': 0
        }
    ]
)

def _all_users_recipient(event_data: Dict[str, Any]) -> List[int]:
    """Recipient: All users (for system-wide announcements like releases)."""
    return list(User.objects.filter(is_active=True).values_list('id', flat=True))


# ============================================================================
# Release Rules
# ============================================================================

RELEASE_PUBLISHED_RULE = NotificationRule(
    name="release_published",
    trigger="releases.release.published",
    condition=None,
    notification_type="RELEASE",
    category="SYSTEM",
    priority="HIGH",
    delivery_policy="IMMEDIATE",
    aggregation_policy="NEVER",
    recipients=_all_users_recipient,
    title_template="New version {version} is now available",
    summary_template="New release available",
    actions=lambda event: [
        {
            'action_type': 'SEE_WHATS_NEW',
            'label': "See What's New",
            'url': f"/system/releases/{event.get('target_id')}/",
            'method': 'GET',
            'is_primary': True,
            'order': 0,
            'style': 'primary'
        }
    ]
)


ADMIN_FEEDBACK_REPLY_RULE = NotificationRule(
    name="Admin Feedback Reply",
    trigger="admin.feedback.replied",
    notification_type="ADMIN_FEEDBACK_REPLY",
    category="SYSTEM",
    priority="HIGH",
    recipients=lambda data: [data.get('metadata', {}).get('user_id')] if data.get('metadata', {}).get('user_id') else [],
    title_template="Admin replied to your feedback",
    summary_template="An admin has responded to your feedback: '{{ context_name }}'",
    delivery_policy="IMMEDIATE",
    aggregation_policy="NEVER",
    actions=lambda data: [
        {
            'action_type': 'LINK',
            'label': 'View Reply',
            'url': f"/dashboard/feedback/user/{data.get('metadata', {}).get('ticket_id', '')}/",
            'style': 'primary'
        }
    ]
)


def _active_admin_recipients(event_data: Dict[str, Any]) -> List[int]:
    """Notify active staff and superusers when a student sends feedback."""
    actor_id = event_data.get('actor_id')
    admins = User.objects.filter(is_active=True).filter(Q(is_staff=True) | Q(is_superuser=True))
    if actor_id:
        admins = admins.exclude(pk=actor_id)
    return list(admins.values_list('pk', flat=True))


ADMIN_FEEDBACK_SUBMITTED_RULE = NotificationRule(
    name="New Student Feedback",
    trigger="admin.feedback.submitted",
    notification_type="SYSTEM",
    category="SYSTEM",
    priority="HIGH",
    recipients=_active_admin_recipients,
    title_template="New feedback: {context_name}",
    summary_template="{actor_username} submitted feedback for the admin team to review.",
    delivery_policy="IMMEDIATE",
    aggregation_policy="NEVER",
    actions=lambda data: [{
        'action_type': 'LINK',
        'label': 'Open Feedback',
        'url': f"/dashboard/feedback/{data.get('target_id', '')}/",
        'style': 'primary',
    }],
)

# ============================================================================
# Device Security Rules (Phase 1 — new-device login alerts)
# ============================================================================

SECURITY_DEVICE_LOGIN_NEW_RULE = NotificationRule(
    name="security_device_login_new",
    trigger="security.device.login_new",
    condition=None,
    notification_type="SECURITY",
    category="SECURITY",
    priority="CRITICAL",
    delivery_policy="IMMEDIATE",
    aggregation_policy="NEVER",
    recipients=lambda event_data: (
        [int(event_data['target_user_id'])] if event_data.get('target_user_id') else []
    ),
    title_template="New sign-in detected",
    summary_template="{device_label} · {location_label} · {event_time}",
    actions=lambda event: [
        {
            'action_type': 'ACCEPT',
            'label': 'Yes, it was me',
            'url': f"/users/api/security/device-verifications/{event.get('challenge_id')}/approve/",
            'method': 'POST',
            'payload': {'decision': 'approve'},
            'is_primary': True,
            'order': 0,
        },
        {
            'action_type': 'DECLINE',
            'label': "It wasn't me",
            'url': f"/users/api/security/device-verifications/{event.get('challenge_id')}/deny/",
            'method': 'POST',
            'payload': {'decision': 'deny'},
            'is_primary': False,
            'order': 1,
        },
        {
            'action_type': 'OPEN',
            'label': 'View details',
            'url': f"/users/security/device-verifications/{event.get('challenge_id')}/",
            'method': 'GET',
            'is_primary': False,
            'order': 2,
        },
    ],
)

SECURITY_DEVICE_TRUSTED_RULE = NotificationRule(
    name="security_device_trusted",
    trigger="security.device.trusted",
    condition=None,
    notification_type="SECURITY",
    category="SECURITY",
    priority="NORMAL",
    delivery_policy="IMMEDIATE",
    aggregation_policy="NEVER",
    recipients=lambda event_data: (
        [int(event_data['target_user_id'])] if event_data.get('target_user_id') else []
    ),
    title_template="Device trusted",
    summary_template="{device_label} won't trigger sign-in alerts anymore.",
)

SECURITY_DEVICE_BLOCKED_RULE = NotificationRule(
    name="security_device_blocked",
    trigger="security.device.blocked",
    condition=None,
    notification_type="SECURITY",
    category="SECURITY",
    priority="CRITICAL",
    delivery_policy="IMMEDIATE",
    aggregation_policy="NEVER",
    recipients=lambda event_data: (
        [int(event_data['target_user_id'])] if event_data.get('target_user_id') else []
    ),
    title_template="Device blocked",
    summary_template="The blocked device was signed out and cannot sign in again.",
)

TIMETABLE_READY_RULE = NotificationRule(
    name="timetable_ready",
    trigger="documents.timetable.ready",
    condition=None,
    notification_type="DOCUMENT",
    category="DOCUMENT",
    priority="NORMAL",
    delivery_policy="IMMEDIATE",
    aggregation_policy="ALLOWED",
    recipients=_timetable_owner_recipient,
    title_template="Your timetable is ready",
    summary_template="Spark finished analyzing your timetable — it's live in your academic workspace.",
    actions=lambda event: [
        {
            'action_type': 'VIEW',
            'label': 'View Timetable',
            'url': '/documents/academic/dashboard/?section=timetable',
            'method': 'GET',
            'is_primary': True,
            'order': 0,
            'style': 'primary'
        }
    ]
)

# All rules registry (device security rules appended further down)
RULES_REGISTRY = [
    POST_LIKE_RULE,
    POST_COMMENT_RULE,
    POST_COMMENT_REPLY_RULE,
    COMMENT_LIKED_RULE,
    POST_SHARED_RULE,
    POST_SHARED_TO_GROUP_RULE,
    POST_REPORTED_RULE,
    POST_REPOSTED_RULE,
    POST_CREATED_RULE,
    POST_DOCUMENT_SHARED_RULE,
    GROUP_INVITE_RULE,
    GROUP_REQUEST_RULE,
    GROUP_APPROVED_RULE,
    GROUP_REJECTED_RULE,
    GROUP_ANNOUNCEMENT_RULE,
    USER_FOLLOW_RULE,
    USER_PINCH_RULE,
    DOCUMENT_UPLOADED_RULE,
    DOCUMENT_PUBLISHED_RULE,
    DOCUMENT_DOWNLOADED_RULE,
    DOCUMENT_BOOKMARKED_RULE,
    DOCUMENT_RATED_RULE,
    TIMETABLE_READY_RULE,
    COURSE_CREATED_RULE,
    UNIT_CREATED_RULE,
    MESSAGE_SENT_RULE,
    CONVERSATION_MEMBER_ADDED_RULE,
    COURSE_ASSIGNMENT_PUBLISHED_RULE,
    RELEASE_PUBLISHED_RULE,
    ADMIN_FEEDBACK_REPLY_RULE,
    ADMIN_FEEDBACK_SUBMITTED_RULE,
    SECURITY_DEVICE_LOGIN_NEW_RULE,
    SECURITY_DEVICE_TRUSTED_RULE,
    SECURITY_DEVICE_BLOCKED_RULE,
]


def get_rules_for_event(event_type: str) -> List[NotificationRule]:
    """Get all rules that match a given event type."""
    return [rule for rule in RULES_REGISTRY if rule.trigger == event_type]


def get_all_rules() -> List[NotificationRule]:
    """Get all registered rules."""
    return RULES_REGISTRY
