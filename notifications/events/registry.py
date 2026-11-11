"""
Event Registry for vasto Notification Engine v2

This module provides a centralized registry for all platform event types.
Following the specification's recommendation to avoid string literals throughout the codebase.
"""
from enum import Enum


class EventTypes(Enum):
    """
    Canonical event type registry.
    
    All platform events must use these constants instead of string literals.
    This prevents spelling mistakes, enables IDE autocomplete, and provides
    a single source of truth for event types.
    
    Naming Convention: <domain>.<resource>.<action>
    
    vasto Domains:
    - posts: Posts, likes, comments
    - groups: Groups, memberships
    - users: Users, follows, pinches
    - documents: Document management
    - courses: Academic content
    - messaging: Chat and messaging
    - projects: Collaboration projects
    - core: Core platform functionality
    """
    
    # Posts Events
    POSTS_POST_CREATED = "posts.post.created"
    POSTS_POST_UPDATED = "posts.post.updated"
    POSTS_POST_DELETED = "posts.post.deleted"
    POSTS_POST_LIKED = "posts.post.liked"
    POSTS_POST_SHARED = "posts.post.shared"
    POSTS_POST_SHARED_TO_GROUP = "posts.post.shared_to_group"
    POSTS_COMMENT_CREATED = "posts.comment.created"
    POSTS_COMMENT_DELETED = "posts.comment.deleted"
    POSTS_COMMENT_LIKED = "posts.comment.liked"
    POSTS_COMMENT_REPLY_CREATED = "posts.comment_reply.created"
    POSTS_COMMENT_REPLIED = "posts.comment.replied"
    POSTS_POST_REPOSTED = "posts.post.reposted"
    POSTS_POST_REPORTED = "posts.post.reported"
    
    # Groups Events
    GROUPS_GROUP_CREATED = "groups.group.created"
    GROUPS_GROUP_UPDATED = "groups.group.updated"
    GROUPS_GROUP_DELETED = "groups.group.deleted"
    GROUPS_MEMBER_INVITED = "groups.member.invited"
    GROUPS_MEMBER_REQUESTED = "groups.member.requested"
    GROUPS_MEMBER_APPROVED = "groups.member.approved"
    GROUPS_MEMBER_REJECTED = "groups.member.rejected"
    GROUPS_MEMBER_JOINED = "groups.member.joined"
    GROUPS_MEMBER_LEFT = "groups.member.left"
    GROUPS_MEMBER_REMOVED = "groups.member.removed"
    GROUPS_ANNOUNCEMENT_CREATED = "groups.announcement.created"
    
    # Users Events
    USERS_USER_FOLLOWED = "users.user.followed"
    USERS_USER_UNFOLLOWED = "users.user.unfollowed"
    USERS_USER_PINCHED = "users.user.pinched"
    USERS_PROFILE_UPDATED = "users.profile.updated"
    USERS_SETTINGS_CHANGED = "users.settings.changed"
    
    # Documents Events
    DOCUMENTS_DOCUMENT_UPLOADED = "documents.document.uploaded"
    DOCUMENTS_DOCUMENT_UPDATED = "documents.document.updated"
    DOCUMENTS_DOCUMENT_DELETED = "documents.document.deleted"
    DOCUMENTS_DOCUMENT_DOWNLOADED = "documents.document.downloaded"
    DOCUMENTS_DOCUMENT_INDEXED = "documents.document.indexed"
    DOCUMENTS_DOCUMENT_PUBLISHED = "documents.document.published"
    DOCUMENTS_DOCUMENT_BOOKMARKED = "documents.document.bookmarked"
    DOCUMENTS_DOCUMENT_RATED = "documents.document.rated"
    DOCUMENTS_TIMETABLE_READY = "documents.timetable.ready"
    
    # Courses Events
    COURSES_COURSE_CREATED = "courses.course.created"
    COURSES_UNIT_CREATED = "courses.unit.created"
    COURSES_ASSIGNMENT_PUBLISHED = "courses.assignment.published"
    COURSES_ASSIGNMENT_SUBMITTED = "courses.assignment.submitted"
    COURSES_ASSIGNMENT_GRADED = "courses.assignment.graded"
    COURSES_ANNOUNCEMENT_PUBLISHED = "courses.announcement.published"
    
    # Messaging Events
    MESSAGING_MESSAGE_SENT = "messaging.message.sent"
    MESSAGING_MESSAGE_EDITED = "messaging.message.edited"
    MESSAGING_MESSAGE_DELETED = "messaging.message.deleted"
    MESSAGING_CONVERSATION_CREATED = "messaging.conversation.created"
    MESSAGING_CONVERSATION_MEMBER_ADDED = "messaging.conversation.member_added"
    
    # Projects Events
    PROJECTS_PROJECT_CREATED = "projects.project.created"
    PROJECTS_MEMBER_INVITED = "projects.member.invited"
    PROJECTS_MEMBER_JOINED = "projects.member.joined"
    PROJECTS_TASK_COMPLETED = "projects.task.completed"
    
    # Releases Events
    RELEASES_RELEASE_CREATED = "releases.release.created"
    RELEASES_RELEASE_PUBLISHED = "releases.release.published"
    RELEASES_RELEASE_UPDATED = "releases.release.updated"
    
    # Core System Events
    CORE_SEMESTER_CHANGED = "core.semester.changed"
    CORE_ACADEMIC_YEAR_CHANGED = "core.academic_year.changed"
    CORE_BACKUP_COMPLETED = "core.backup.completed"
    ADMIN_FEEDBACK_REPLIED = "admin.feedback.replied"
    ADMIN_FEEDBACK_SUBMITTED = "admin.feedback.submitted"
    
    # Security Events
    SECURITY_LOGIN_DETECTED = "security.login.detected"
    SECURITY_PASSWORD_CHANGED = "security.password.changed"
    SECURITY_ACCOUNT_LOCKED = "security.account.locked"
    SECURITY_DEVICE_LOGIN_NEW = "security.device.login_new"
    SECURITY_DEVICE_TRUSTED = "security.device.trusted"
    SECURITY_DEVICE_BLOCKED = "security.device.blocked"
    
    @classmethod
    def get_all_event_types(cls):
        """Return all event type strings."""
        return [event_type.value for event_type in cls]
    
    @classmethod
    def is_valid_event_type(cls, event_type_str):
        """Check if an event type string is valid."""
        return event_type_str in cls.get_all_event_types()
    
    @classmethod
    def get_domain_events(cls, domain):
        """Get all events for a specific domain."""
        return [event_type.value for event_type in cls if event_type.value.startswith(f"{domain}.")]


class EventSources(Enum):
    """
    Canonical event source registry.
    
    These are the approved subsystem names that can publish events.
    Based on actual vasto Django apps.
    """
    
    POSTS = "POSTS"
    GROUPS = "GROUPS"
    USERS = "USERS"
    DOCUMENTS = "DOCUMENTS"
    COURSES = "COURSES"
    MESSAGING = "MESSAGING"
    PROJECTS = "PROJECTS"
    RELEASES = "RELEASES"
    CORE = "CORE"
    NOTIFICATIONS = "NOTIFICATIONS"
    AUTHENTICATION = "AUTHENTICATION"
    ADMIN = "ADMIN"
    STORAGE = "STORAGE"
    SYSTEM = "SYSTEM"
    
    @classmethod
    def get_all_sources(cls):
        """Return all source strings."""
        return [source.value for source in cls]
    
    @classmethod
    def is_valid_source(cls, source_str):
        """Check if a source string is valid."""
        return source_str in cls.get_all_sources()


class EventActions(Enum):
    """
    Canonical action registry.
    
    All actions must be past tense as they represent completed facts.
    """
    
    # Common actions
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    LIKED = "liked"
    COMMENTED = "commented"
    REPLIED = "replied"
    JOINED = "joined"
    LEFT = "left"
    REMOVED = "removed"
    COMPLETED = "completed"
    UPLOADED = "uploaded"
    DOWNLOADED = "downloaded"
    STARTED = "started"
    ENDED = "ended"
    PUBLISHED = "published"
    ASSIGNED = "assigned"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    INVITED = "invited"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    FOLLOWED = "followed"
    UNFOLLOWED = "unfollowed"
    PINCHED = "pinched"
    CHANGED = "changed"
    INDEXED = "indexed"
    REVIEWED = "reviewed"
    BOOKMARKED = "bookmarked"
    SENT = "sent"
    EDITED = "edited"
    GENERATED = "generated"
    TAGGED = "tagged"
    DETECTED = "detected"
    ADDED = "added"
    LOCKED = "locked"
    SHARED = "shared"
    REQUESTED = "requested"
    RATED = "rated"
    REPOSTED = "reposted"
    REPORTED = "reported"
    TRUSTED = "trusted"
    BLOCKED = "blocked"
    
    @classmethod
    def get_all_actions(cls):
        """Return all action strings."""
        return [action.value for action in cls]
    
    @classmethod
    def is_valid_action(cls, action_str):
        """Check if an action string is valid."""
        return action_str in cls.get_all_actions()
