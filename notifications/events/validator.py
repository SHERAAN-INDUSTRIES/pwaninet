"""
Event Validator for vasto Notification Engine v2

This module validates platform events before they are published.
Ensures events conform to the specification and contain required fields.
"""
from django.core.exceptions import ValidationError
from .registry import EventTypes, EventSources, EventActions


class EventValidator:
    """
    Validates platform events before publication.
    
    Ensures events conform to the specification:
    - Has required fields
    - Uses valid event types
    - Uses valid sources
    - Uses valid actions
    - Event type and action are compatible
    """
    
    REQUIRED_FIELDS = ['event_type', 'source', 'action', 'target_type', 'target_id']
    
    # Mapping of event types to their valid actions
    # This allows semantic flexibility while maintaining consistency
    EVENT_ACTION_COMPATIBILITY = {
        # Posts events
        'posts.post.created': ['created'],
        'posts.post.updated': ['updated'],
        'posts.post.deleted': ['deleted'],
        'posts.post.liked': ['liked'],
        'posts.post.shared': ['shared'],
        'posts.post.shared_to_group': ['shared'],
        'posts.comment.created': ['commented', 'created'],
        'posts.comment.deleted': ['deleted'],
        'posts.comment.liked': ['liked'],
        'posts.comment_reply.created': ['replied'],
        'posts.comment.replied': ['replied'],
        'posts.post.reposted': ['reposted', 'shared'],
        'posts.post.reported': ['reported'],
        'admin.feedback.submitted': ['submitted'],
        
        # Groups events
        'groups.group.created': ['created'],
        'groups.group.updated': ['updated'],
        'groups.group.deleted': ['deleted'],
        'groups.member.invited': ['invited'],
        'groups.member.requested': ['requested'],
        'groups.member.approved': ['approved'],
        'groups.member.rejected': ['rejected'],
        'groups.member.joined': ['joined'],
        'groups.member.left': ['left'],
        'groups.member.removed': ['removed'],
        'groups.announcement.created': ['created', 'published'],
        
        # Users events
        'users.user.followed': ['followed'],
        'users.user.unfollowed': ['unfollowed'],
        'users.user.pinched': ['pinched'],
        'users.profile.updated': ['updated', 'changed'],
        'users.settings.changed': ['changed'],
        
        # Documents events
        'documents.document.uploaded': ['uploaded'],
        'documents.document.updated': ['updated'],
        'documents.document.deleted': ['deleted'],
        'documents.document.downloaded': ['downloaded'],
        'documents.document.indexed': ['indexed'],
        'documents.document.published': ['published'],
        'documents.document.bookmarked': ['bookmarked'],
        'documents.document.rated': ['rated'],
        'documents.timetable.ready': ['completed'],
        
        # Courses events
        'courses.course.created': ['created'],
        'courses.unit.created': ['created'],
        'courses.assignment.published': ['published'],
        'courses.assignment.submitted': ['submitted'],
        'courses.assignment.graded': ['graded'],
        'courses.announcement.published': ['published'],
        
        # Messaging events
        'messaging.message.sent': ['sent'],
        'messaging.message.edited': ['edited'],
        'messaging.message.deleted': ['deleted'],
        'messaging.conversation.created': ['created'],
        'messaging.conversation.member_added': ['added'],
        
        # Projects events
        'projects.project.created': ['created'],
        'projects.member.invited': ['invited'],
        'projects.member.joined': ['joined'],
        'projects.task.completed': ['completed'],
        
        # Releases events
        'releases.release.created': ['created'],
        'releases.release.published': ['published'],
        'releases.release.updated': ['updated'],
        
        # Core system events
        'core.semester.changed': ['changed'],
        'core.academic_year.changed': ['changed'],
        'core.backup.completed': ['completed'],
        
        # Security events
        'security.login.detected': ['detected'],
        'security.password.changed': ['changed'],
        'security.account.locked': ['locked'],
        'security.device.login_new': ['detected'],
        'security.device.trusted': ['trusted', 'approved'],
        'security.device.blocked': ['blocked', 'rejected'],
    }
    
    @classmethod
    def validate_event_data(cls, event_data):
        """
        Validate event data before publication.
        
        Args:
            event_data: Dictionary containing event data
            
        Raises:
            ValidationError: If event data is invalid
            
        Returns:
            dict: Validated event data
        """
        errors = {}
        
        # Check required fields
        for field in cls.REQUIRED_FIELDS:
            if field not in event_data or event_data[field] is None:
                errors[field] = f"{field} is required"
        
        if errors:
            raise ValidationError(errors)
        
        # Validate event type
        if not EventTypes.is_valid_event_type(event_data['event_type']):
            errors['event_type'] = f"Invalid event type: {event_data['event_type']}"
        
        # Validate source
        if not EventSources.is_valid_source(event_data['source']):
            errors['source'] = f"Invalid source: {event_data['source']}"
        
        # Validate action
        if not EventActions.is_valid_action(event_data['action']):
            errors['action'] = f"Invalid action: {event_data['action']}"
        
        # Validate event type and action compatibility
        if not errors.get('event_type') and not errors.get('action'):
            if not cls._are_event_and_action_compatible(event_data['event_type'], event_data['action']):
                errors['event_type'] = (
                    f"Event type '{event_data['event_type']}' is not compatible "
                    f"with action '{event_data['action']}'. "
                    f"Valid actions for this event type: {cls.EVENT_ACTION_COMPATIBILITY.get(event_data['event_type'], [])}"
                )
        
        # Validate target
        if not event_data['target_type'] or not event_data['target_id']:
            errors['target'] = "Both target_type and target_id are required"
        
        # Validate metadata is a dict if provided
        if 'metadata' in event_data and event_data['metadata'] is not None:
            if not isinstance(event_data['metadata'], dict):
                errors['metadata'] = "Metadata must be a dictionary"
        
        if errors:
            raise ValidationError(errors)
        
        return event_data
    
    @classmethod
    def _are_event_and_action_compatible(cls, event_type, action):
        """
        Check if the action is compatible with the event type.
        
        Uses the compatibility mapping to allow semantic flexibility
        while maintaining consistency.
        
        Args:
            event_type: The event type string
            action: The action string
            
        Returns:
            bool: True if compatible, False otherwise
        """
        valid_actions = cls.EVENT_ACTION_COMPATIBILITY.get(event_type, [])
        
        # If no specific mapping exists, allow any valid action
        # (for new event types not yet added to the mapping)
        if not valid_actions:
            return True
        
        return action in valid_actions
    
    @classmethod
    def _event_type_matches_action(cls, event_type, action):
        """
        Check if the action in event type matches the provided action.
        
        Example:
        event_type = "social.post.created"
        action = "created"
        Returns True
        """
        # Extract action from event type (last part after last dot)
        event_type_action = event_type.split('.')[-1]
        return event_type_action == action
    
    @classmethod
    def validate_event_object(cls, event):
        """
        Validate a PlatformEvent model instance.
        
        Args:
            event: PlatformEvent instance
            
        Raises:
            ValidationError: If event is invalid
        """
        errors = {}
        
        # Check required fields
        if not event.event_type:
            errors['event_type'] = "Event type is required"
        
        if not event.source:
            errors['source'] = "Source is required"
        
        if not event.action:
            errors['action'] = "Action is required"
        
        if not event.target_type:
            errors['target_type'] = "Target type is required"
        
        if not event.target_id:
            errors['target_id'] = "Target ID is required"
        
        # Validate against registries
        if event.event_type and not EventTypes.is_valid_event_type(event.event_type):
            errors['event_type'] = f"Invalid event type: {event.event_type}"
        
        if event.source and not EventSources.is_valid_source(event.source):
            errors['source'] = f"Invalid source: {event.source}"
        
        if event.action and not EventActions.is_valid_action(event.action):
            errors['action'] = f"Invalid action: {event.action}"
        
        # Validate consistency
        if event.event_type and event.action:
            if not cls._are_event_and_action_compatible(event.event_type, event.action):
                errors['event_type'] = (
                    f"Event type '{event.event_type}' is not compatible "
                    f"with action '{event.action}'. "
                    f"Valid actions for this event type: {cls.EVENT_ACTION_COMPATIBILITY.get(event.event_type, [])}"
                )
        
        # Validate context consistency
        if event.context_type and not event.context_id:
            errors['context'] = "context_id is required when context_type is provided"
        
        if event.context_id and not event.context_type:
            errors['context'] = "context_type is required when context_id is provided"
        
        if errors:
            raise ValidationError(errors)
