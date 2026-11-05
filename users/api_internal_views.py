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
        from documents.models import Document, DocumentFile
        documents = (
            Document.objects.filter(uploaded_by=user)
            .select_related('category')
            .prefetch_related('versions__files')
            .order_by('-created_at')
        )

        def _serialize_file(doc_file):
            file_url = None
            try:
                file_url = doc_file.file.url if doc_file.file else None
            except Exception:
                file_url = doc_file.storage_path or None
            thumbnail_url = None
            preview_url = None
            try:
                thumbnail_url = doc_file.file.storage.url(doc_file.thumbnail_path) if doc_file.thumbnail_path else None
            except Exception:
                thumbnail_url = None
            try:
                preview_url = doc_file.file.storage.url(doc_file.preview_path) if doc_file.preview_path else None
            except Exception:
                preview_url = None
            return {
                'id': doc_file.id,
                'file_url': file_url,
                'storage_path': doc_file.storage_path,
                'storage_provider': doc_file.storage_provider,
                'original_filename': doc_file.original_filename,
                'mime_type': doc_file.mime_type,
                'extension': doc_file.extension,
                'size_bytes': doc_file.size_bytes,
                'thumbnail_url': thumbnail_url,
                'preview_url': preview_url,
                'page_count': doc_file.page_count,
                'processing_status': doc_file.processing_status,
                'uploaded_at': doc_file.uploaded_at.isoformat() if doc_file.uploaded_at else None,
            }

        data = {
            'documents': [
                {
                    'id': doc.id,
                    'share_id': str(doc.share_id) if doc.share_id else None,
                    'title': doc.title,
                    'slug': doc.slug,
                    'description': doc.description,
                    'category': {
                        'id': doc.category.id,
                        'name': doc.category.name,
                        'code': getattr(doc.category, 'code', ''),
                    } if doc.category else None,
                    'language': doc.language,
                    'visibility': doc.visibility,
                    'status': doc.status,
                    'is_available': doc.is_available,
                    'is_ai_generated': doc.is_ai_generated,
                    'generated_format': doc.generated_format,
                    'generated_from_message_id': doc.generated_from_message_id,
                    'files': [
                        _serialize_file(f)
                        for version in doc.versions.all()
                        if version.is_latest
                        for f in version.files.all()
                    ],
                    'created_at': doc.created_at.isoformat(),
                    'updated_at': doc.updated_at.isoformat(),
                    'published_at': doc.published_at.isoformat() if doc.published_at else None,
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
def internal_user_programmes(request, user_id):
    """Get user's academic programme(s)."""
    is_valid, error = check_service_account(request)
    if not is_valid:
        return Response({'error': error}, status=status.HTTP_403_FORBIDDEN)

    try:
        user = User.objects.select_related(
            'programme__department__school',
        ).get(id=user_id)
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

    programmes = []
    current_programme_id = None

    programme = getattr(user, 'programme', None)
    if programme:
        current_programme_id = programme.id
        department = getattr(programme, 'department', None)
        institution = None
        try:
            institution = programme.get_institution()
        except Exception:
            institution = None
        programmes.append({
            'id': programme.id,
            'code': programme.code,
            'name': programme.name,
            'slug': programme.slug,
            'degree_type': programme.degree_type,
            'duration_years': programme.duration_years,
            'description': programme.description,
            'is_active': programme.is_active,
            'department': {
                'id': department.id,
                'code': department.code,
                'name': department.name,
            } if department else None,
            'institution': {
                'id': institution.id,
                'name': institution.name,
            } if institution else None,
            'is_current': True,
        })

    return Response({
        'programmes': programmes,
        'current_programme_id': current_programme_id,
    })


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
        from groups.models import Membership, MembershipStatus
        memberships = Membership.objects.filter(
            user=user,
            status=MembershipStatus.APPROVED,
        ).select_related('group__programme', 'group__course', 'group__year')
        data = {
            'groups': [
                {
                    'id': membership.group.id,
                    'name': membership.group.name,
                    'description': membership.group.description,
                    'is_official': membership.group.is_official,
                    'group_pic': membership.group.group_pic.url if membership.group.group_pic else None,
                    'cover_photo': membership.group.cover_photo.url if membership.group.cover_photo else None,
                    'created_at': membership.group.created_at.isoformat(),
                    'programme': {
                        'id': membership.group.programme.id,
                        'code': membership.group.programme.code,
                        'name': membership.group.programme.name,
                    } if membership.group.programme else None,
                    'course': {
                        'id': membership.group.course.id,
                        'name': membership.group.course.name,
                        'code': getattr(membership.group.course, 'code', ''),
                    } if membership.group.course else None,
                    'year': membership.group.year.level if membership.group.year else None,
                    'role': membership.role,
                    'status': membership.status,
                    'joined_at': membership.joined_at.isoformat() if membership.joined_at else None,
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
        from notifications.models import NotificationObject
        notifications = (
            NotificationObject.objects.filter(recipient=user)
            .exclude(status='EXPIRED')
            .order_by('-created_at')[:50]
        )
        data = {
            'notifications': [
                {
                    'id': str(notif.id),
                    'type': notif.notification_type,
                    'category': notif.category,
                    'priority': notif.priority,
                    'title': notif.title,
                    'summary': notif.summary,
                    'status': notif.status,
                    'is_read': notif.is_read,
                    'target_type': notif.target_type,
                    'target_id': notif.target_id,
                    'context_type': notif.context_type,
                    'context_id': notif.context_id,
                    'metadata': notif.metadata or {},
                    'event_count': notif.event_count,
                    'created_at': notif.created_at.isoformat(),
                    'updated_at': notif.updated_at.isoformat() if notif.updated_at else None,
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


def _academic_context_filters(user):
    """Build ProgrammeUnit/ProgrammeTimetable filters from the user's academic context."""
    filters = {}
    if getattr(user, 'programme_id', None):
        filters['programme_id'] = user.programme_id
    if getattr(user, 'academic_level_id', None):
        filters['academic_level_id'] = user.academic_level_id
    if getattr(user, 'academic_year_id', None):
        filters['academic_year_id'] = user.academic_year_id
    if getattr(user, 'semester_id', None):
        filters['semester_id'] = user.semester_id
    return filters


def _serialize_academic_level(level):
    if not level:
        return None
    return {'id': level.id, 'level': level.level, 'name': level.name}


def _serialize_academic_year(year):
    if not year:
        return None
    return {'id': year.id, 'code': year.code, 'name': year.name, 'is_current': year.is_current}


def _serialize_semester(semester):
    if not semester:
        return None
    return {
        'id': semester.id,
        'number': semester.number,
        'is_current': semester.is_current,
        'academic_year': _serialize_academic_year(semester.academic_year),
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([InternalAPIRateThrottle])
def internal_user_programme_units(request, user_id):
    """Get programme units for the user's academic context."""
    is_valid, error = check_service_account(request)
    if not is_valid:
        return Response({'error': error}, status=status.HTTP_403_FORBIDDEN)

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

    try:
        from documents.models import ProgrammeUnit
        filters = _academic_context_filters(user)
        if not filters.get('programme_id'):
            return Response({'programme_units': []})

        units = (
            ProgrammeUnit.objects.filter(**filters)
            .select_related('academic_unit', 'academic_level', 'academic_year', 'semester')
            .order_by('academic_unit__code')
        )
        data = {
            'programme_units': [
                {
                    'id': unit.id,
                    'academic_unit': {
                        'id': unit.academic_unit.id,
                        'code': unit.academic_unit.code,
                        'name': unit.academic_unit.name,
                        'slug': unit.academic_unit.slug,
                        'description': unit.academic_unit.description,
                        'credit_hours': unit.academic_unit.credit_hours,
                        'is_active': unit.academic_unit.is_active,
                    },
                    'academic_level': _serialize_academic_level(unit.academic_level),
                    'academic_year': _serialize_academic_year(unit.academic_year),
                    'semester': _serialize_semester(unit.semester),
                    'is_core': unit.is_core,
                    'is_elective': unit.is_elective,
                    'created_at': unit.created_at.isoformat(),
                }
                for unit in units
            ]
        }
    except Exception as e:
        logger.error(f"Error fetching programme units for user {user_id}: {e}")
        data = {'programme_units': []}

    return Response(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([InternalAPIRateThrottle])
def internal_user_timetable(request, user_id):
    """Get active timetable(s) for the user's academic context."""
    is_valid, error = check_service_account(request)
    if not is_valid:
        return Response({'error': error}, status=status.HTTP_403_FORBIDDEN)

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

    try:
        from documents.models import ProgrammeTimetable
        filters = _academic_context_filters(user)
        filters['is_active'] = True
        if not filters.get('programme_id'):
            return Response({'timetables': []})

        timetables = (
            ProgrammeTimetable.objects.filter(**filters)
            .select_related('academic_level', 'academic_year', 'semester', 'programme')
            .order_by('-updated_at')
        )
        data = {
            'timetables': [
                {
                    'id': timetable.id,
                    'title': timetable.title,
                    'timetable_type': timetable.timetable_type,
                    'status': timetable.status,
                    'revision_number': timetable.revision_number,
                    'change_summary': timetable.change_summary,
                    'schedule_data': timetable.schedule_data,
                    'source_image_url': timetable.source_image.url if timetable.source_image else None,
                    'programme': {
                        'id': timetable.programme.id,
                        'code': timetable.programme.code,
                        'name': timetable.programme.name,
                    } if timetable.programme else None,
                    'academic_level': _serialize_academic_level(timetable.academic_level),
                    'academic_year': _serialize_academic_year(timetable.academic_year),
                    'semester': _serialize_semester(timetable.semester),
                    'created_at': timetable.created_at.isoformat(),
                    'updated_at': timetable.updated_at.isoformat() if timetable.updated_at else None,
                }
                for timetable in timetables
            ]
        }
    except Exception as e:
        logger.error(f"Error fetching timetable for user {user_id}: {e}")
        data = {'timetables': []}

    return Response(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([InternalAPIRateThrottle])
def internal_user_follows(request, user_id):
    """Get users the given user follows, plus follow counts."""
    is_valid, error = check_service_account(request)
    if not is_valid:
        return Response({'error': error}, status=status.HTTP_403_FORBIDDEN)

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

    try:
        from users.models import Follow
        follows = (
            Follow.objects.filter(follower=user)
            .select_related('followed__institution', 'followed__programme')
            .order_by('-created_at')
        )

        def _serialize_followed(followed):
            return {
                'id': followed.id,
                'username': followed.username,
                'first_name': followed.first_name,
                'last_name': followed.last_name,
                'headline': followed.headline,
                'bio': followed.bio,
                'profile_pic': followed.profile_pic.url if followed.profile_pic else None,
                'institution': {
                    'id': followed.institution.id,
                    'name': followed.institution.name,
                } if followed.institution else None,
                'programme': {
                    'id': followed.programme.id,
                    'name': followed.programme.name,
                } if followed.programme else None,
                'is_online': followed.is_online,
                'last_seen': followed.last_seen.isoformat() if followed.last_seen else None,
            }

        data = {
            'follows': [
                {
                    'id': follow.id,
                    **_serialize_followed(follow.followed),
                    'followed_at': follow.created_at.isoformat(),
                }
                for follow in follows
            ],
            'following_count': follows.count(),
            'followers_count': Follow.objects.filter(followed=user).count(),
        }
    except Exception as e:
        logger.error(f"Error fetching follows for user {user_id}: {e}")
        data = {'follows': [], 'following_count': 0, 'followers_count': 0}

    return Response(data)
