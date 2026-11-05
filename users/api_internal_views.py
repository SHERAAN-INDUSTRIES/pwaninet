"""
Internal API views for Workspaces integration.
These endpoints are protected by service token authentication and are
intended for server-to-server communication only.
"""
import logging
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.authtoken.models import Token

logger = logging.getLogger(__name__)

User = get_user_model()


class InternalAPIRateThrottle(UserRateThrottle):
    """Custom rate limiter for internal API."""
    rate = '100/minute'


def check_service_account(request):
    """Verify request comes from service account."""
    if not settings.VASTO_INTERNAL_API_ENABLED:
        return False, "Internal API is disabled"

    # Check IP whitelist in production
    if not settings.DEBUG:
        client_ip = request.META.get('REMOTE_ADDR')
        allowed_ips = getattr(settings, 'INTERNAL_API_ALLOWED_IPS', ['127.0.0.1'])
        if client_ip not in allowed_ips:
            return False, f"IP {client_ip} not in whitelist"

    # Check if user is a service account
    try:
        service_account = request.user.service_account
        if not service_account.is_active:
            return False, "Service account is inactive"
        return True, None
    except Exception:
        return False, "User is not a service account"


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([InternalAPIRateThrottle])
def internal_user_profile(request, user_id):
    """Get user profile data."""
    is_valid, error = check_service_account(request)
    if not is_valid:
        return Response({'error': error}, status=status.HTTP_403_FORBIDDEN)

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

    data = {
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'email_verified': getattr(user, 'email_verified', False),
        'first_name': user.first_name,
        'last_name': user.last_name,
        'profile_pic': user.profile_pic.url if user.profile_pic else None,
        'cover_photo': user.cover_photo.url if user.cover_photo else None,
        'bio': user.bio,
        'headline': user.headline,
        'interests': user.interests,
        'skills': user.skills,
        'projects': user.projects,
        'github_url': user.github_url,
        'linkedin_url': user.linkedin_url,
        'portfolio_url': user.portfolio_url,
        'twitter_url': user.twitter_url,
        'institution': {
            'id': user.institution.id,
            'name': user.institution.name,
            'code': getattr(user.institution, 'code', ''),
        } if user.institution else None,
        'programme': {
            'id': user.programme.id,
            'name': user.programme.name,
        } if user.programme else None,
        'academic_level': user.academic_level.name if user.academic_level else None,
        'academic_year': user.academic_year.name if user.academic_year else None,
        'semester': user.semester.number if user.semester else None,
        'theme_preference': user.theme_preference,
        'language_preference': user.language_preference,
        'profile_privacy': user.profile_privacy,
        'is_online': user.is_online,
        'last_seen': user.last_seen.isoformat() if user.last_seen else None,
    }

    return Response(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([InternalAPIRateThrottle])
def internal_user_documents(request, user_id):
    """Get user's documents."""
    is_valid, error = check_service_account(request)
    if not is_valid:
        return Response({'error': error}, status=status.HTTP_403_FORBIDDEN)

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

    try:
        from documents.models import Document
        documents = Document.objects.filter(user=user).select_related('institution', 'programme')
        data = {
            'documents': [
                {
                    'id': doc.id,
                    'title': doc.title,
                    'description': doc.description,
                    'file_url': doc.file.url if doc.file else None,
                    'file_type': doc.file_type,
                    'file_size': doc.file_size,
                    'uploaded_at': doc.uploaded_at.isoformat(),
                    'updated_at': doc.updated_at.isoformat(),
                    'category': doc.category,
                    'tags': doc.tags,
                    'institution': {
                        'id': doc.institution.id,
                        'name': doc.institution.name,
                    } if doc.institution else None,
                    'programme': {
                        'id': doc.programme.id,
                        'name': doc.programme.name,
                    } if doc.programme else None,
                }
                for doc in documents
            ]
        }
    except Exception as e:
        logger.error(f"Error fetching documents for user {user_id}: {e}")
        data = {'documents': []}

    return Response(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([InternalAPIRateThrottle])
def internal_user_institution(request, user_id):
    """Get user's institution."""
    is_valid, error = check_service_account(request)
    if not is_valid:
        return Response({'error': error}, status=status.HTTP_403_FORBIDDEN)

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

    if not user.institution:
        return Response({'error': 'User has no institution'}, status=status.HTTP_404_NOT_FOUND)

    inst = user.institution
    data = {
        'id': inst.id,
        'name': inst.name,
        'code': getattr(inst, 'code', ''),
        'logo': inst.logo.url if inst.logo else None,
        'description': getattr(inst, 'description', ''),
        'location': getattr(inst, 'location', ''),
        'website': getattr(inst, 'website', ''),
        'programmes': [
            {'id': p.id, 'name': p.name, 'code': getattr(p, 'code', '')}
            for p in inst.programme_set.all()
        ] if hasattr(inst, 'programme_set') else [],
    }

    return Response(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([InternalAPIRateThrottle])
def internal_user_courses(request, user_id):
    """Get user's enrolled courses."""
    is_valid, error = check_service_account(request)
    if not is_valid:
        return Response({'error': error}, status=status.HTTP_403_FORBIDDEN)

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

    try:
        from courses.models import Course, Enrollment
        enrollments = Enrollment.objects.filter(user=user).select_related('course', 'course__institution')
        data = {
            'courses': [
                {
                    'id': enrollment.course.id,
                    'name': enrollment.course.name,
                    'code': enrollment.course.code,
                    'description': enrollment.course.description,
                    'credits': enrollment.course.credits,
                    'institution': {
                        'id': enrollment.course.institution.id,
                        'name': enrollment.course.institution.name,
                    } if enrollment.course.institution else None,
                    'programme': {
                        'id': enrollment.programme.id,
                        'name': enrollment.programme.name,
                    } if enrollment.programme else None,
                    'instructor': enrollment.course.instructor.username if enrollment.course.instructor else None,
                    'semester': enrollment.semester.name if enrollment.semester else None,
                    'enrolled_at': enrollment.enrolled_at.isoformat(),
                    'status': enrollment.status,
                }
                for enrollment in enrollments
            ]
        }
    except Exception as e:
        logger.error(f"Error fetching courses for user {user_id}: {e}")
        data = {'courses': []}

    return Response(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([InternalAPIRateThrottle])
def internal_user_groups(request, user_id):
    """Get user's groups."""
    is_valid, error = check_service_account(request)
    if not is_valid:
        return Response({'error': error}, status=status.HTTP_403_FORBIDDEN)

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

    try:
        from groups.models import Group, GroupMembership
        memberships = GroupMembership.objects.filter(user=user).select_related('group')
        data = {
            'groups': [
                {
                    'id': membership.group.id,
                    'name': membership.group.name,
                    'description': membership.group.description,
                    'type': membership.group.type,
                    'created_at': membership.group.created_at.isoformat(),
                    'updated_at': membership.group.updated_at.isoformat(),
                    'institution': {
                        'id': membership.group.institution.id,
                        'name': membership.group.institution.name,
                    } if membership.group.institution else None,
                    'programme': {
                        'id': membership.group.programme.id,
                        'name': membership.group.programme.name,
                    } if membership.group.programme else None,
                    'role': membership.role,
                    'joined_at': membership.joined_at.isoformat(),
                }
                for membership in memberships
            ]
        }
    except Exception as e:
        logger.error(f"Error fetching groups for user {user_id}: {e}")
        data = {'groups': []}

    return Response(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([InternalAPIRateThrottle])
def internal_user_notifications(request, user_id):
    """Get user's notifications."""
    is_valid, error = check_service_account(request)
    if not is_valid:
        return Response({'error': error}, status=status.HTTP_403_FORBIDDEN)

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

    try:
        from notifications.models import Notification
        notifications = Notification.objects.filter(user=user).order_by('-created_at')[:50]
        data = {
            'notifications': [
                {
                    'id': notif.id,
                    'type': notif.notification_type,
                    'message': notif.message,
                    'actor': {
                        'id': notif.actor.id,
                        'username': notif.actor.username,
                    } if notif.actor else None,
                    'target_type': notif.target_type,
                    'target_id': notif.target_id,
                    'is_read': notif.is_read,
                    'created_at': notif.created_at.isoformat(),
                }
                for notif in notifications
            ]
        }
    except Exception as e:
        logger.error(f"Error fetching notifications for user {user_id}: {e}")
        data = {'notifications': []}

    return Response(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([InternalAPIRateThrottle])
def internal_user_conversations(request, user_id):
    """Get user's conversations."""
    is_valid, error = check_service_account(request)
    if not is_valid:
        return Response({'error': error}, status=status.HTTP_403_FORBIDDEN)

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

    try:
        from messaging.models import Conversation, ConversationParticipant
        participations = ConversationParticipant.objects.filter(user=user).select_related('conversation')
        data = {
            'conversations': [
                {
                    'id': participation.conversation.id,
                    'type': participation.conversation.conversation_type,
                    'name': participation.conversation.name,
                    'participants': [
                        {'id': p.user.id, 'username': p.user.username}
                        for p in participation.conversation.participants.all()
                    ],
                    'last_message_at': participation.conversation.last_message_at.isoformat() if participation.conversation.last_message_at else None,
                    'unread_count': participation.unread_count,
                }
                for participation in participations
            ]
        }
    except Exception as e:
        logger.error(f"Error fetching conversations for user {user_id}: {e}")
        data = {'conversations': []}

    return Response(data)
