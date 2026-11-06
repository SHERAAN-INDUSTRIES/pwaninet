from rest_framework import viewsets, status, filters
from rest_framework.decorators import action, api_view, permission_classes, parser_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from django_filters.rest_framework import DjangoFilterBackend
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.urls import reverse
from django.db.models import Q, Count
from django.db import transaction, IntegrityError
from django.utils import timezone
import datetime
import logging
from django.views.decorators.http import require_http_methods, require_POST
from django.views.decorators.csrf import csrf_exempt
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from .models import (
    Group, Membership, MembershipRole, MembershipStatus, JoinPolicy,
    EditPermission, InvitePermission, Announcement,
    GroupMessage, GroupMessageAttachment, GroupMessageReaction,
    GroupJoinRequest, GroupInvitation
)
from .consumers import evict_group_member_socket
from .serializers import (
    GroupSerializer, GroupCreateSerializer, MembershipSerializer,
    MembershipCreateSerializer, MembershipActionSerializer, RoleAssignmentSerializer,
    AnnouncementSerializer, AnnouncementCreateSerializer, AnnouncementUpdateSerializer,
    GroupMessageSerializer, GroupMessageCreateSerializer,
    GroupMessageAttachmentSerializer, GroupMessageReactionSerializer
)
from .permissions import (
    CanManageGroup, CanManageMembership, CanJoinOfficialGroup,
    IsApprovedMember, IsGroupAdmin, CanViewAnnouncement, CanCreateAnnouncement,
    CanEditAnnouncement, CanDeleteAnnouncement
)
from posts.models import Post, Like
from users.models import User, Follow
from groups.forms import GroupForm, GroupDetailsForm, RoleAssignmentForm, GroupPrivacyForm
from groups.services.group_notification_service import (
    send_group_join_request_notification,
    send_group_approved_notification,
    send_group_rejected_notification,
    send_group_welcome_notification,
    send_group_invite_notification
)
from notifications.services.notification_service import (
    get_cached_unread_count,
    get_group_unread_counts,
    get_group_activity_by_type,
    mark_group_notifications_as_read_and_invalidate,
    mark_announcement_as_read_and_invalidate,
    invalidate_unread_count_cache
)
from notifications.queries.notification_queries import (
    get_unread_count_by_user_id,
    get_unread_announcement_ids_for_user
)
from notifications.models import NotificationObject
from django.core.paginator import Paginator
import json
from pwaninet.utils.htmx import htmx_location_response
from notifications.events import publish_event, EventSources

logger = logging.getLogger(__name__)


def _archive_group_request_notifications(group_id, requesting_user_id=None):
    """
    Archive pending join request notifications for admins of this group,
    and broadcast updated unread counts via WebSocket.
    """
    try:
        notifs = NotificationObject.objects.filter(
            context_type='GROUP',
            context_id=str(group_id)
        ).exclude(status__in=['READ', 'ARCHIVED'])

        channel_layer = get_channel_layer()
        affected_recipients = set()

        for notif in notifs:
            notif_user_id = notif.metadata.get('user_id') if notif.metadata else None
            if requesting_user_id is None or not notif_user_id or str(notif_user_id) == str(requesting_user_id):
                notif.status = 'ARCHIVED'
                notif.save(update_fields=['status'])
                affected_recipients.add(notif.recipient_id)

        for recipient_id in affected_recipients:
            invalidate_unread_count_cache(recipient_id)
            if channel_layer:
                try:
                    count = get_unread_count_by_user_id(recipient_id)
                    async_to_sync(channel_layer.group_send)(
                        f"notifications_{recipient_id}",
                        {
                            'type': 'unread_count_update',
                            'count': count
                        }
                    )
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"Error archiving group request notifications: {e}")



class GroupViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing groups.
    """
    permission_classes = [IsAuthenticated]
    queryset = Group.objects.all()

    def get_serializer_class(self):
        if self.action == 'create':
            return GroupCreateSerializer
        return GroupSerializer

    def get_permissions(self):
        if self.action in ['update', 'partial_update', 'destroy']:
            return [IsAuthenticated(), CanManageGroup()]
        return super().get_permissions()

    @action(detail=True, methods=['post'], url_path='join')
    def join(self, request, pk=None):
        """
        POST /groups/{id}/join/
        Request to join a group.
        """
        with transaction.atomic():
            group = Group.objects.select_for_update().get(id=pk)

            # Check official group restrictions
            grp_prog = group.programme or group.course
            grp_level = group.academic_level or group.year
            if group.is_official and grp_prog and grp_level:
                user_prog = getattr(request.user, 'programme', None) or getattr(request.user, 'course', None)
                user_level = getattr(request.user, 'academic_level', None) or getattr(request.user, 'year', None)
                if user_prog != grp_prog or user_level != grp_level:
                    return Response(
                        {'detail': 'You can only join official groups that match your course and year.'},
                        status=status.HTTP_403_FORBIDDEN
                    )

            # Check if already has approved membership
            if Membership.objects.filter(user=request.user, group=group, status=MembershipStatus.APPROVED).exists():
                return Response(
                    {'detail': 'You are already a member of this group.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Check if has pending join request
            if GroupJoinRequest.objects.filter(user=request.user, group=group, status='PENDING').exists():
                return Response(
                    {'detail': 'You already have a pending join request for this group.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Determine status based on join policy
            if group.join_policy == JoinPolicy.OPEN:
                membership = Membership.objects.filter(user=request.user, group=group).first()
                if not membership:
                    membership = Membership(
                        user=request.user,
                        group=group,
                        role=MembershipRole.MEMBER,
                        status=MembershipStatus.APPROVED
                    )
                    membership._skip_signal_notification = True
                    membership.save()
                elif membership.status != MembershipStatus.APPROVED:
                    membership.status = MembershipStatus.APPROVED
                    membership._skip_signal_notification = True
                    membership.save()
                send_group_welcome_notification(request.user, group)
                return Response({
                    'status': MembershipStatus.APPROVED,
                    'message': 'Joined successfully'
                }, status=status.HTTP_200_OK)

            elif group.join_policy == JoinPolicy.INVITE_ONLY:
                return Response(
                    {'detail': 'This group is invite-only.'},
                    status=status.HTTP_403_FORBIDDEN
                )

            else:
                # APPROVAL policy
                GroupJoinRequest.objects.get_or_create(
                    user=request.user,
                    group=group,
                    defaults={'status': 'PENDING'}
                )
                try:
                    Membership.objects.get_or_create(
                        user=request.user,
                        group=group,
                        defaults={'role': MembershipRole.MEMBER, 'status': MembershipStatus.PENDING}
                    )
                except IntegrityError:
                    pass

                send_group_join_request_notification(request.user, group)
                return Response({
                    'status': MembershipStatus.PENDING,
                    'message': 'Join request sent'
                }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='approve/(?P<user_id>[^/.]+)')
    def approve(self, request, pk=None, user_id=None):
        """
        POST /groups/{id}/approve/{user_id}/
        Approve a join request (admin only).
        """
        with transaction.atomic():
            group = Group.objects.select_for_update().get(id=pk)

            # Check if user is admin
            if not Membership.objects.filter(
                user=request.user,
                group=group,
                role=MembershipRole.ADMIN,
                status=MembershipStatus.APPROVED
            ).exists():
                return Response(
                    {'detail': 'Only group admins can approve requests.'},
                    status=status.HTTP_403_FORBIDDEN
                )

            join_request = GroupJoinRequest.objects.filter(
                group=group,
                user_id=user_id,
                status='PENDING'
            ).first()

            membership = Membership.objects.filter(
                user_id=user_id,
                group=group
            ).first()

            if not join_request and (not membership or membership.status != MembershipStatus.PENDING):
                return Response(
                    {'detail': 'No pending membership request found for this user.'},
                    status=status.HTTP_404_NOT_FOUND
                )

            if join_request:
                join_request.status = 'APPROVED'
                join_request.reviewed_by = request.user
                join_request.reviewed_at = timezone.now()
                join_request.save()

            if membership:
                membership.status = MembershipStatus.APPROVED
                membership.role = MembershipRole.MEMBER
                membership._skip_signal_notification = True
                membership.save()
            else:
                target_user = get_object_or_404(User, id=user_id)
                membership = Membership(
                    user=target_user,
                    group=group,
                    role=MembershipRole.MEMBER,
                    status=MembershipStatus.APPROVED
                )
                membership._skip_signal_notification = True
                membership.save()

            # Send welcome notification to the approved user
            send_group_approved_notification(membership.user, group, request.user)

            # Clean up pending request notifications for this group
            _archive_group_request_notifications(group.id, user_id)

            return Response(
                MembershipSerializer(membership).data,
                status=status.HTTP_200_OK
            )

    @action(detail=True, methods=['post'], url_path='reject/(?P<user_id>[^/.]+)')
    def reject(self, request, pk=None, user_id=None):
        """
        POST /groups/{id}/reject/{user_id}/
        Reject a join request (admin only).
        """
        with transaction.atomic():
            group = Group.objects.select_for_update().get(id=pk)

            # Check if user is admin
            if not Membership.objects.filter(
                user=request.user,
                group=group,
                role=MembershipRole.ADMIN,
                status=MembershipStatus.APPROVED
            ).exists():
                return Response(
                    {'detail': 'Only admins can reject join requests.'},
                    status=status.HTTP_403_FORBIDDEN
                )

            join_request = GroupJoinRequest.objects.filter(
                group=group,
                user_id=user_id,
                status='PENDING'
            ).first()

            membership = Membership.objects.filter(
                user_id=user_id,
                group=group
            ).first()

            if not join_request and (not membership or membership.status != MembershipStatus.PENDING):
                return Response(
                    {'detail': 'No pending membership request found for this user.'},
                    status=status.HTTP_404_NOT_FOUND
                )

            if join_request:
                join_request.status = 'REJECTED'
                join_request.reviewed_by = request.user
                join_request.reviewed_at = timezone.now()
                join_request.save()

            target_user = None
            if membership:
                target_user = membership.user
                membership.status = MembershipStatus.REJECTED
                membership.save()
            else:
                target_user = get_object_or_404(User, id=user_id)

            # Send rejection notification to the user
            send_group_rejected_notification(target_user, group, request.user)

            # Clean up pending request notifications for this group
            _archive_group_request_notifications(group.id, user_id)

            return Response(
                MembershipSerializer(membership).data if membership else {'status': 'REJECTED'},
                status=status.HTTP_200_OK
            )

    @action(detail=True, methods=['post'], url_path='assign-role')
    def assign_role(self, request, pk=None):
        """
        POST /groups/{id}/assign-role/
        Assign a role to a member (admin only).
        Enforces max 5 admins per group constraint under row lock.
        """
        with transaction.atomic():
            group = Group.objects.select_for_update().get(id=pk)

            # Check if caller is admin
            if not Membership.objects.filter(
                user=request.user,
                group=group,
                role=MembershipRole.ADMIN,
                status=MembershipStatus.APPROVED
            ).exists():
                return Response(
                    {'detail': 'Only admins can assign roles.'},
                    status=status.HTTP_403_FORBIDDEN
                )

            serializer = RoleAssignmentSerializer(
                data=request.data,
                context={'request': request, 'group_id': group.id}
            )
            serializer.is_valid(raise_exception=True)

            membership = serializer.validated_data['membership']
            new_role = serializer.validated_data['role']

            # Cannot demote yourself from admin
            if membership.user == request.user and membership.role == MembershipRole.ADMIN and new_role != MembershipRole.ADMIN:
                return Response(
                    {'detail': 'You cannot change your own admin role.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if new_role == MembershipRole.ADMIN and membership.role != MembershipRole.ADMIN:
                admin_count = Membership.objects.filter(
                    group=group,
                    role=MembershipRole.ADMIN,
                    status=MembershipStatus.APPROVED
                ).count()
                if admin_count >= 5:
                    return Response(
                        {'detail': 'Maximum of 5 admins allowed per group.'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            membership.role = new_role
            membership.save()

            return Response(
                MembershipSerializer(membership).data,
                status=status.HTTP_200_OK
            )

    @action(detail=True, methods=['get'], url_path='members')
    def members(self, request, pk=None):
        """
        GET /groups/{id}/members/
        List all members of a group.
        """
        group = self.get_object()
        memberships = group.memberships.filter(status=MembershipStatus.APPROVED)
        serializer = MembershipSerializer(memberships, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='leave')
    def leave(self, request, pk=None):
        """
        POST /groups/{id}/leave/
        Leave a group.
        """
        with transaction.atomic():
            group = Group.objects.select_for_update().get(id=pk)

            try:
                membership = Membership.objects.get(
                    user=request.user,
                    group=group,
                    status=MembershipStatus.APPROVED
                )
            except Membership.DoesNotExist:
                return Response(
                    {'detail': 'You are not an approved member of this group.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Check if user is the last admin
            if membership.role == MembershipRole.ADMIN:
                admin_count = Membership.objects.filter(
                    group=group,
                    role=MembershipRole.ADMIN,
                    status=MembershipStatus.APPROVED
                ).count()

                if admin_count == 1:
                    # Find potential successors
                    moderators = Membership.objects.filter(
                        group=group,
                        role=MembershipRole.MODERATOR,
                        status=MembershipStatus.APPROVED
                    ).select_related('user')[:3]

                    members = Membership.objects.filter(
                        group=group,
                        role=MembershipRole.MEMBER,
                        status=MembershipStatus.APPROVED
                    ).select_related('user').order_by('-id')[:5]

                    successors = []
                    for mod in moderators:
                        successors.append({
                            'id': mod.user.id,
                            'username': mod.user.username,
                            'name': f"{mod.user.first_name or ''} {mod.user.last_name or ''}".strip(),
                            'role': 'MODERATOR',
                            'priority': 1
                        })

                    for mem in members:
                        successors.append({
                            'id': mem.user.id,
                            'username': mem.user.username,
                            'name': f"{mem.user.first_name or ''} {mem.user.last_name or ''}".strip(),
                            'role': 'MEMBER',
                            'priority': 2
                        })

                    return Response({
                        'is_last_admin': True,
                        'detail': 'You are the last admin. Please assign a successor before leaving.',
                        'successors': successors
                    }, status=status.HTTP_403_FORBIDDEN)

            membership.delete()
            evict_group_member_socket(group.id, request.user.id, reason="You left the group.")
            return Response(
                {'detail': 'You have left the group.'},
                status=status.HTTP_200_OK
            )


    @action(detail=True, methods=['post'], url_path='photos/(?P<photo_type>[^/.]+)/like')
    def photo_like(self, request, pk=None, photo_type=None):
        """
        POST /groups/{id}/photos/{photo_type}/like/
        Like or unlike a group's profile or cover photo.
        """
        group = self.get_object()

        # Check if user is an approved member
        try:
            Membership.objects.get(
                user=request.user,
                group=group,
                status=MembershipStatus.APPROVED
            )
        except Membership.DoesNotExist:
            return Response(
                {'detail': 'You must be an approved member to like group photos.'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Validate photo type
        if photo_type not in ['group', 'cover']:
            return Response(
                {'detail': 'Invalid photo type. Must be group or cover.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if photo exists
        if photo_type == 'cover' and not group.cover_photo:
            return Response(
                {'detail': 'This group does not have a cover photo.'},
                status=status.HTTP_404_NOT_FOUND
            )

        from .models import GroupPhotoLike
        like, created = GroupPhotoLike.objects.get_or_create(
            user=request.user,
            group=group,
            photo_type=photo_type
        )

        likes_count = GroupPhotoLike.objects.filter(
            group=group,
            photo_type=photo_type
        ).count()

        if created:
            return Response(
                {'detail': 'Photo liked.', 'likes_count': likes_count},
                status=status.HTTP_201_CREATED
            )
        else:
            like.delete()
            return Response(
                {'detail': 'Photo unliked.', 'likes_count': likes_count},
                status=status.HTTP_200_OK
            )

    @action(detail=True, methods=['post'], url_path='assign-and-leave')
    def assign_and_leave(self, request, pk=None):
        """
        POST /groups/{id}/assign-and-leave/
        Assign a new admin and then leave the group.
        Used when the last admin wants to leave.
        """
        group = self.get_object()

        # Check if user is admin
        try:
            membership = Membership.objects.get(
                user=request.user,
                group=group,
                role=MembershipRole.ADMIN,
                status=MembershipStatus.APPROVED
            )
        except Membership.DoesNotExist:
            return Response(
                {'detail': 'Only admins can use this endpoint.'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Get the successor user_id
        successor_id = request.data.get('successor_id')
        if not successor_id:
            return Response(
                {'detail': 'successor_id is required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get the successor membership
        try:
            successor_membership = Membership.objects.get(
                user_id=successor_id,
                group=group,
                status=MembershipStatus.APPROVED
            )
        except Membership.DoesNotExist:
            return Response(
                {'detail': 'Successor is not a member of this group.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Promote successor to admin
        successor_membership.role = MembershipRole.ADMIN
        successor_membership.save()

        # Leave the group
        membership.delete()

        return Response(
            {'detail': f'Admin role transferred to {successor_membership.user.username}. You have left the group.'},
            status=status.HTTP_200_OK
        )


class AnnouncementViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing group announcements.
    """
    permission_classes = [IsAuthenticated]
    queryset = Announcement.objects.all()

    def get_serializer_class(self):
        if self.action == 'create':
            return AnnouncementCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return AnnouncementUpdateSerializer
        return AnnouncementSerializer

    def get_permissions(self):
        if self.action == 'create':
            return [IsAuthenticated(), CanCreateAnnouncement()]
        elif self.action in ['update', 'partial_update']:
            return [IsAuthenticated(), CanEditAnnouncement()]
        elif self.action == 'destroy':
            return [IsAuthenticated(), CanDeleteAnnouncement()]
        elif self.action in ['retrieve', 'list']:
            return [IsAuthenticated(), CanViewAnnouncement()]
        return super().get_permissions()

    def get_queryset(self):
        """
        Filter announcements by group_id from URL.
        Use select_related to avoid N+1 queries.
        """
        queryset = super().get_queryset()
        group_id = self.kwargs.get('group_id')

        if group_id:
            queryset = queryset.filter(group_id=group_id)

        # Optimize queries
        queryset = queryset.select_related('author', 'group')

        return queryset

    def create(self, request, *args, **kwargs):
        """
        Create an announcement for a group.
        """
        group_id = self.kwargs.get('group_id')
        if not group_id:
            return Response(
                {'detail': 'group_id is required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = self.get_serializer(
            data=request.data,
            context={'request': request, 'group_id': group_id}
        )

        # Handle validation errors
        if not serializer.is_valid():
            error_messages = []
            for field, errors in serializer.errors.items():
                for error in errors:
                    if isinstance(error, str):
                        error_messages.append(f"{field.replace('_', ' ').title()}: {error}")
                    else:
                        error_messages.append(f"{field.replace('_', ' ').title()}: {str(error)}")

            error_detail = '; '.join(error_messages) if error_messages else 'Validation failed'
            return Response(
                {'detail': error_detail},
                status=status.HTTP_400_BAD_REQUEST
            )

        self.perform_create(serializer)
        announcement = serializer.instance
        group = announcement.group
        transaction.on_commit(lambda: publish_event(
            event_type='groups.announcement.created',
            source=EventSources.GROUPS.value,
            action='created',
            actor=request.user,
            target_type='Announcement',
            target_id=str(announcement.id),
            context_type='GROUP',
            context_id=str(group.id),
            metadata={
                'group_name': group.name,
                'group_id': group.id,
                'announcement_id': announcement.id,
                'announcement_title': announcement.title,
                'title': announcement.title,
            }
        ))

        # Return HTML for HTMX requests
        if request.headers.get('HX-Request'):
            from django.template.loader import render_to_string
            group = serializer.instance.group
            announcements = Announcement.objects.filter(
                group=group
            ).select_related('author').prefetch_related('attachments').order_by('-is_pinned', '-created_at')

            pinned_announcements = [a for a in announcements if a.is_pinned]
            all_announcements = [a for a in announcements if not a.is_pinned]

            # Get user membership for role display
            try:
                membership = Membership.objects.get(
                    user=request.user,
                    group=group,
                    status=MembershipStatus.APPROVED
                )
                is_admin = membership.role == MembershipRole.ADMIN
            except Membership.DoesNotExist:
                is_admin = False

            html = render_to_string('groups/partials/announcement_list.html', {
                'pinned_announcements': pinned_announcements,
                'all_announcements': all_announcements,
                'is_admin': is_admin,
                'unread_announcement_ids': get_unread_announcement_ids_for_user(request.user, group.id),
            })
            return HttpResponse(html, content_type='text/html')

        return Response(
            AnnouncementSerializer(serializer.instance).data,
            status=status.HTTP_201_CREATED
        )

    def update(self, request, *args, **kwargs):
        """
        Update an announcement.
        """
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)

        # Handle validation errors
        if not serializer.is_valid():
            error_messages = []
            for field, errors in serializer.errors.items():
                for error in errors:
                    if isinstance(error, str):
                        error_messages.append(f"{field.replace('_', ' ').title()}: {error}")
                    else:
                        error_messages.append(f"{field.replace('_', ' ').title()}: {str(error)}")

            error_detail = '; '.join(error_messages) if error_messages else 'Validation failed'
            return Response(
                {'detail': error_detail},
                status=status.HTTP_400_BAD_REQUEST
            )

        self.perform_update(serializer)

        # Return HTML for HTMX requests
        if request.headers.get('HX-Request'):
            from django.template.loader import render_to_string
            group = instance.group
            announcements = Announcement.objects.filter(
                group=group
            ).select_related('author').prefetch_related('attachments').order_by('-is_pinned', '-created_at')

            pinned_announcements = [a for a in announcements if a.is_pinned]
            all_announcements = [a for a in announcements if not a.is_pinned]

            try:
                membership = Membership.objects.get(
                    user=request.user,
                    group=group,
                    status=MembershipStatus.APPROVED
                )
                is_admin = membership.role == MembershipRole.ADMIN
            except Membership.DoesNotExist:
                is_admin = False

            html = render_to_string('groups/partials/announcement_list.html', {
                'pinned_announcements': pinned_announcements,
                'all_announcements': all_announcements,
                'is_admin': is_admin,
                'unread_announcement_ids': get_unread_announcement_ids_for_user(request.user, group.id),
            })
            return HttpResponse(html, content_type='text/html')

        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        """
        Delete an announcement.
        """
        instance = self.get_object()
        group = instance.group
        self.perform_destroy(instance)

        # Return HTML for HTMX requests
        if request.headers.get('HX-Request'):
            from django.template.loader import render_to_string
            announcements = Announcement.objects.filter(
                group=group
            ).select_related('author').prefetch_related('attachments').order_by('-is_pinned', '-created_at')

            pinned_announcements = [a for a in announcements if a.is_pinned]
            all_announcements = [a for a in announcements if not a.is_pinned]

            try:
                membership = Membership.objects.get(
                    user=request.user,
                    group=group,
                    status=MembershipStatus.APPROVED
                )
                is_admin = membership.role == MembershipRole.ADMIN
            except Membership.DoesNotExist:
                is_admin = False

            html = render_to_string('groups/partials/announcement_list.html', {
                'pinned_announcements': pinned_announcements,
                'all_announcements': all_announcements,
                'is_admin': is_admin,
                'unread_announcement_ids': get_unread_announcement_ids_for_user(request.user, group.id),
            })
            return HttpResponse(html, content_type='text/html')

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'], url_path='pin')
    def pin(self, request, pk=None, group_id=None):
        """
        Pin an announcement (admin only).
        """
        announcement = self.get_object()

        # Check permission
        permission = CanEditAnnouncement()
        if not permission.has_object_permission(request, self, announcement):
            return Response(
                {'detail': 'Only admins can pin announcements.'},
                status=status.HTTP_403_FORBIDDEN
            )

        announcement.is_pinned = True
        announcement.save()

        # Return HTML for HTMX requests
        if request.headers.get('HX-Request'):
            from django.template.loader import render_to_string
            group = announcement.group
            announcements = Announcement.objects.filter(
                group=group
            ).select_related('author').prefetch_related('attachments').order_by('-is_pinned', '-created_at')

            pinned_announcements = [a for a in announcements if a.is_pinned]
            all_announcements = [a for a in announcements if not a.is_pinned]

            try:
                membership = Membership.objects.get(
                    user=request.user,
                    group=group,
                    status=MembershipStatus.APPROVED
                )
                is_admin = membership.role == MembershipRole.ADMIN
            except Membership.DoesNotExist:
                is_admin = False

            html = render_to_string('groups/partials/announcement_list.html', {
                'pinned_announcements': pinned_announcements,
                'all_announcements': all_announcements,
                'is_admin': is_admin,
                'unread_announcement_ids': get_unread_announcement_ids_for_user(request.user, group.id),
            })
            return HttpResponse(html, content_type='text/html')

        return Response(
            AnnouncementSerializer(announcement).data,
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=['post'], url_path='unpin')
    def unpin(self, request, pk=None, group_id=None):
        """
        Unpin an announcement (admin only).
        """
        announcement = self.get_object()

        # Check permission
        permission = CanEditAnnouncement()
        if not permission.has_object_permission(request, self, announcement):
            return Response(
                {'detail': 'Only admins can unpin announcements.'},
                status=status.HTTP_403_FORBIDDEN
            )

        announcement.is_pinned = False
        announcement.save()

        # Return HTML for HTMX requests
        if request.headers.get('HX-Request'):
            from django.template.loader import render_to_string
            group = announcement.group
            announcements = Announcement.objects.filter(
                group=group
            ).select_related('author').prefetch_related('attachments').order_by('-is_pinned', '-created_at')

            pinned_announcements = [a for a in announcements if a.is_pinned]
            all_announcements = [a for a in announcements if not a.is_pinned]

            try:
                membership = Membership.objects.get(
                    user=request.user,
                    group=group,
                    status=MembershipStatus.APPROVED
                )
                is_admin = membership.role == MembershipRole.ADMIN
            except Membership.DoesNotExist:
                is_admin = False

            html = render_to_string('groups/partials/announcement_list.html', {
                'pinned_announcements': pinned_announcements,
                'all_announcements': all_announcements,
                'is_admin': is_admin,
                'unread_announcement_ids': get_unread_announcement_ids_for_user(request.user, group.id),
            })
            return HttpResponse(html, content_type='text/html')

        return Response(
            AnnouncementSerializer(announcement).data,
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=['post'], url_path='mark-read')
    def mark_read(self, request, pk=None, group_id=None):
        """
        Mark an announcement notification as read for the current user.
        """
        announcement = self.get_object()
        mark_announcement_as_read_and_invalidate(request.user, announcement.id, group_id=announcement.group_id)
        return Response({
            'status': 'success',
            'announcement_id': announcement.id,
            'group_id': announcement.group_id
        }, status=status.HTTP_200_OK)


# ============================================================================
# DJANGO WEB VIEWS
# ============================================================================

@login_required
def groups_dashboard(request):
    from groups.queries.group_queries import get_following_ids

    # Get search query
    query = request.GET.get('q', '').strip()

    # Save search to session if query exists
    if query:
        recent_searches = request.session.get('recent_group_searches', [])
        # Add query if not already in recent searches
        if query not in recent_searches:
            recent_searches.insert(0, query)
            # Keep only last 5 searches
            recent_searches = recent_searches[:5]
            request.session['recent_group_searches'] = recent_searches

    user_groups = Group.objects.filter(memberships__user=request.user, memberships__status=MembershipStatus.APPROVED)
    pending_groups = Group.objects.filter(memberships__user=request.user, memberships__status=MembershipStatus.PENDING)

    if query:
        from search.services.unified_search_service import UnifiedSearchService
        search_service = UnifiedSearchService()
        matched_groups, _ = search_service.search_groups_models(
            query=query,
            user=request.user,
            limit=20,
            offset=0
        )
        display_groups = matched_groups
    else:
        from recommendations.services.engine import UnifiedRecommendationEngine
        display_groups = UnifiedRecommendationEngine.get_recommended_groups(
            request.user, limit=20, context='dashboard'
        )

    user_group_ids = set(user_groups.values_list('id', flat=True))
    pending_group_ids = set(pending_groups.values_list('id', flat=True))

    # Get unread notification counts and activity breakdown for both user groups and display groups
    display_group_ids = [g.id for g in display_groups]
    all_relevant_group_ids = list(set(list(user_group_ids) + display_group_ids))
    group_unread_counts = get_group_unread_counts(request.user, all_relevant_group_ids)
    group_activity = get_group_activity_by_type(request.user, all_relevant_group_ids)

    # Get recent searches from session
    recent_searches = request.session.get('recent_group_searches', [])

    context = {
        'user_groups': user_groups,
        'all_groups': display_groups,
        'user_group_ids': user_group_ids,
        'pending_group_ids': pending_group_ids,
        'unread_notifications_count': get_cached_unread_count(request.user),
        'group_unread_counts': group_unread_counts,
        'group_activity': group_activity,
        'query': query,
        'recent_searches': recent_searches,
    }

    if request.headers.get('HX-Request'):
        return render(request, 'groups/partials/groups_dashboard_navigation_partial.html', context)

    return render(request, 'groups/groups_dashboard.html', context)



@login_required
def groups_detail_view(request, group_id):
    from groups.services.group_service import build_group_detail_context
    mark_group_notifications_as_read_and_invalidate(request.user, group_id, exclude_types=['GROUP_ANNOUNCEMENT'])
    group = get_object_or_404(Group.objects.annotate(member_count=Count('memberships', filter=Q(memberships__status=MembershipStatus.APPROVED))), id=group_id)
    query = request.GET.get('search_user', '')
    post_query = request.GET.get('post_query') or request.GET.get('q_post') or request.GET.get('q') or ''
    page = int(request.GET.get('page', 1))
    context = build_group_detail_context(request.user, group, query, page, post_query=post_query)
    liked_post_ids = set()
    if request.user.is_authenticated and context.get('posts'):
        for pid, sid in Like.objects.filter(user=request.user, post__in=context['posts']).values_list('post_id', 'post__share_id'):
            liked_post_ids.add(pid)
            liked_post_ids.add(sid)
            liked_post_ids.add(str(sid))
    context['liked_post_ids'] = liked_post_ids
    context['unread_notifications_count'] = get_cached_unread_count(request.user)

    # Check if HTMX request
    if request.headers.get('HX-Request'):
        # If pagination request for infinite scroll or search query targeting posts container
        if request.headers.get('HX-Target') == 'posts-container' or (request.GET.get('page') and page > 1):
            return render(request, 'groups/partials/group_post_cards_list.html', {
                'group': group,
                'posts': context['posts'],
                'has_more_posts': context['has_next'],
                'next_page': context.get('next_page'),
                'liked_post_ids': liked_post_ids,
                'is_member': context.get('is_member'),
                'post_query': post_query,
            })
        response = render(request, 'groups/partials/groups_detail_navigation_partial.html', context)
        response['HX-Trigger'] = 'updateGroupActivity'
        return response

    return render(request, 'groups/groups_detail.html', context)


@login_required
def create_group_view(request):
    is_htmx = bool(request.headers.get('HX-Request'))
    if request.method == 'POST':
        form = GroupForm(request.POST, request.FILES)
        if form.is_valid():
            group = form.save(commit=False)
            group.created_by = request.user
            group.save()
            admin_mem = Membership(group=group, user=request.user, role=MembershipRole.ADMIN, status=MembershipStatus.APPROVED)
            admin_mem._skip_signal_notification = True
            admin_mem.save()
            messages.success(request, f'Squad "{group.name}" created successfully.')
            if is_htmx:
                return htmx_location_response(reverse('groups:groups_detail', kwargs={'group_id': group.id}))
            return redirect('groups:groups_detail', group_id=group.id)
    else:
        form = GroupForm()

    template = 'groups/partials/create_group_navigation_partial.html' if is_htmx else 'groups/create_group.html'
    return render(request, template, {'form': form})


# ============================================================================
# GROUP CHAT API VIEWS
# ============================================================================

class GroupMessageViewSet(viewsets.ModelViewSet):
    """ViewSet for managing group messages"""
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['group', 'message_type', 'status']
    ordering_fields = ['created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        """Return messages for groups the user is a member of"""
        user_groups = Group.objects.filter(
            memberships__user=self.request.user,
            memberships__status=MembershipStatus.APPROVED
        )
        return GroupMessage.objects.filter(group__in=user_groups).select_related(
            'sender', 'reply_to'
        ).prefetch_related('attachments', 'reactions')

    def get_serializer_class(self):
        if self.action == 'create':
            return GroupMessageCreateSerializer
        return GroupMessageSerializer

    def list(self, request, *args, **kwargs):
        """List messages with support for limit and before_id cursor pagination."""
        group_id = self.kwargs.get('group_id')
        queryset = self.filter_queryset(self.get_queryset())
        if group_id:
            queryset = queryset.filter(group_id=group_id)

        limit_param = request.query_params.get('limit')
        before_id = request.query_params.get('before_id')

        if limit_param:
            try:
                limit = max(1, min(100, int(limit_param)))
                if before_id:
                    queryset = queryset.filter(id__lt=before_id)
                messages_slice = list(queryset.order_by('-created_at')[:limit])
                messages_slice.reverse()
                serializer = self.get_serializer(messages_slice, many=True)
                return Response(serializer.data)
            except (ValueError, TypeError):
                pass

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def perform_create(self, serializer):
        """Create a new message and broadcast via WebSocket"""
        group_id = self.kwargs.get('group_id')
        message = serializer.save(
            group_id=group_id,
            sender=self.request.user
        )

        # Broadcast message to group via WebSocket
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'group_{group_id}',
            {
                'type': 'group_message',
                'message': GroupMessageSerializer(message).data
            }
        )
        return message

    @action(detail=True, methods=['post'], url_path='react')
    def react(self, request, pk=None):
        """Add or remove a reaction to a message"""
        message = self.get_object()
        emoji = request.data.get('emoji')

        if not emoji:
            return Response(
                {'error': 'emoji is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if user is a member of the group
        if not Membership.objects.filter(
            user=request.user,
            group=message.group,
            status=MembershipStatus.APPROVED
        ).exists():
            return Response(
                {'error': 'You must be a member of this group to react'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Toggle reaction
        reaction, created = GroupMessageReaction.objects.get_or_create(
            message=message,
            user=request.user,
            emoji=emoji
        )

        if not created:
            reaction.delete()
            return Response({'status': 'removed'}, status=status.HTTP_200_OK)

        # Broadcast reaction update
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'group_{message.group.id}',
            {
                'type': 'group_reaction',
                'message_id': message.id,
                'reaction': GroupMessageReactionSerializer(reaction).data
            }
        )

        return Response(
            GroupMessageReactionSerializer(reaction).data,
            status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=['post'], url_path='mark-read')
    def mark_read(self, request, pk=None):
        """Mark a message as read"""
        message = self.get_object()

        # Check if user is a member of the group
        if not Membership.objects.filter(
            user=request.user,
            group=message.group,
            status=MembershipStatus.APPROVED
        ).exists():
            return Response(
                {'error': 'You must be a member of this group'},
                status=status.HTTP_403_FORBIDDEN
            )

        message.status = 'read'
        message.save()

        return Response({'status': 'marked as read'}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def group_attachment_upload(request):
    """Upload a single attachment for a group message"""
    group_id = request.data.get('group_id')
    message_id = request.data.get('message_id')
    file = request.data.get('file')

    if not group_id or not file:
        return Response(
            {'error': 'group_id and file are required'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Check if user is a member of the group
    if not Membership.objects.filter(
        user=request.user,
        group_id=group_id,
        status=MembershipStatus.APPROVED
    ).exists():
        return Response(
            {'error': 'You must be a member of this group to upload attachments'},
            status=status.HTTP_403_FORBIDDEN
        )

    # Create or get message
    if message_id:
        try:
            message = GroupMessage.objects.get(id=message_id, group_id=group_id)
        except GroupMessage.DoesNotExist:
            return Response(
                {'error': 'Message not found'},
                status=status.HTTP_404_NOT_FOUND
            )
    else:
        message = GroupMessage.objects.create(
            group_id=group_id,
            sender=request.user,
            message_type='media_group'
        )

    # Determine attachment type
    content_type = file.content_type
    if content_type.startswith('image/'):
        attachment_type = 'image'
    elif content_type.startswith('video/'):
        attachment_type = 'video'
    elif content_type.startswith('audio/'):
        attachment_type = 'audio'
    else:
        attachment_type = 'document'

    # Create attachment
    attachment = GroupMessageAttachment.objects.create(
        message=message,
        attachment_type=attachment_type,
        file=file
    )

    # Broadcast message update
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f'group_{group_id}',
        {
            'type': 'group_message',
            'message': GroupMessageSerializer(message).data
        }
    )

    return Response(
        GroupMessageAttachmentSerializer(attachment).data,
        status=status.HTTP_201_CREATED
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def group_batch_attachment_upload(request):
    """Batch upload attachments for a group message (media groups)"""
    group_id = request.data.get('group_id')
    files = request.FILES.getlist('files')
    global_caption = request.data.get('global_caption', '')

    if not group_id or not files:
        return Response(
            {'error': 'group_id and files are required'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Check if user is a member of the group
    if not Membership.objects.filter(
        user=request.user,
        group_id=group_id,
        status=MembershipStatus.APPROVED
    ).exists():
        return Response(
            {'error': 'You must be a member of this group to upload attachments'},
            status=status.HTTP_403_FORBIDDEN
        )

    # Create message
    message = GroupMessage.objects.create(
        group_id=group_id,
        sender=request.user,
        content=global_caption,
        message_type='media_group'
    )

    # Create attachments
    attachments_data = []
    for index, file in enumerate(files):
        content_type = file.content_type
        if content_type.startswith('image/'):
            attachment_type = 'image'
        elif content_type.startswith('video/'):
            attachment_type = 'video'
        elif content_type.startswith('audio/'):
            attachment_type = 'audio'
        else:
            attachment_type = 'document'

        attachment = GroupMessageAttachment.objects.create(
            message=message,
            attachment_type=attachment_type,
            file=file,
            order=index
        )
        attachments_data.append(GroupMessageAttachmentSerializer(attachment).data)

    # Broadcast message
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f'group_{group_id}',
        {
            'type': 'group_message',
            'message': GroupMessageSerializer(message).data
        }
    )

    return Response(
        {
            'message': GroupMessageSerializer(message).data,
            'attachments': attachments_data
        },
        status=status.HTTP_201_CREATED
    )


@login_required
@require_http_methods(["POST"])
@transaction.atomic
def toggle_group_membership(request, group_id):
    group = Group.objects.select_for_update().get(id=group_id)

    # Check official group restrictions
    grp_prog = group.programme or group.course
    grp_level = group.academic_level or group.year
    if group.is_official and grp_prog and grp_level:
        user_prog = getattr(request.user, 'programme', None) or getattr(request.user, 'course', None)
        user_level = getattr(request.user, 'academic_level', None) or getattr(request.user, 'year', None)
        if user_prog != grp_prog or user_level != grp_level:
            messages.error(request, 'You can only join official groups that match your course and year.')
            if request.headers.get('HX-Request'):
                return htmx_location_response(reverse('groups:groups_detail', kwargs={'group_id': group_id}))
            return redirect('groups:groups_detail', group_id=group_id)

    # Check if already has membership
    existing_membership = Membership.objects.filter(group=group, user=request.user).first()

    if existing_membership:
        # Sole admin protection
        if existing_membership.role == MembershipRole.ADMIN and existing_membership.status == MembershipStatus.APPROVED:
            other_admins = Membership.objects.filter(
                group=group,
                role=MembershipRole.ADMIN,
                status=MembershipStatus.APPROVED
            ).exclude(id=existing_membership.id).exists()
            if not other_admins:
                messages.error(request, 'You cannot leave the group as you are the sole administrator. Promote another member to admin first.')
                if request.headers.get('HX-Request'):
                    return htmx_location_response(reverse('groups:groups_detail', kwargs={'group_id': group_id}))
                return redirect('groups:groups_detail', group_id=group_id)

        existing_membership.delete()
        evict_group_member_socket(group.id, request.user.id, reason="You left the group.")
        messages.success(request, f'You left {group.name}.')
    else:
        # Determine status based on join policy
        if group.join_policy == JoinPolicy.OPEN:
            membership = Membership.objects.filter(group=group, user=request.user).first()
            if not membership:
                membership = Membership(
                    group=group,
                    user=request.user,
                    role=MembershipRole.MEMBER,
                    status=MembershipStatus.APPROVED
                )
                membership._skip_signal_notification = True
                membership.save()
            elif membership.status != MembershipStatus.APPROVED:
                membership.status = MembershipStatus.APPROVED
                membership._skip_signal_notification = True
                membership.save()
            messages.success(request, f'You joined {group.name}!')
            send_group_welcome_notification(request.user, group)
        elif group.join_policy == JoinPolicy.APPROVAL:
            # Check if pending request exists
            join_req, created = GroupJoinRequest.objects.get_or_create(
                group=group,
                user=request.user,
                defaults={'status': 'PENDING'}
            )
            try:
                Membership.objects.get_or_create(
                    group=group,
                    user=request.user,
                    defaults={'status': MembershipStatus.PENDING, 'role': MembershipRole.MEMBER}
                )
            except IntegrityError:
                pass

            if created:
                messages.info(request, f'Your request to join {group.name} is pending approval.')
                send_group_join_request_notification(request.user, group)
            else:
                messages.info(request, f'Your request to join {group.name} is already pending approval.')
        elif group.join_policy == JoinPolicy.INVITE_ONLY:
            messages.error(request, 'This group is invite-only.')
        else:
            messages.info(request, f'Your request to join {group.name} is pending approval.')

    # Invalidate recommendation caches
    from recommendations.services.engine import UnifiedRecommendationEngine
    UnifiedRecommendationEngine.invalidate_all_user_caches(request.user.id)

    if request.headers.get('HX-Request'):
        if request.GET.get('source') == 'onboarding':
            membership = Membership.objects.filter(group=group, user=request.user).first()
            is_member = membership and membership.status == MembershipStatus.APPROVED
            is_pending = membership and membership.status == MembershipStatus.PENDING
            return render(request, 'groups/partials/join_button_onboarding.html', {
                'group': group,
                'is_member': is_member,
                'is_pending': is_pending,
            })
        return htmx_location_response(reverse('groups:groups_detail', kwargs={'group_id': group_id}))
    return redirect('groups:groups_detail', group_id=group_id)


@login_required
def edit_group(request, group_id):
    group = get_object_or_404(Group, id=group_id)

    # Check if user is admin (creator or has admin membership)
    is_admin = (group.created_by == request.user) or Membership.objects.filter(
        group=group,
        user=request.user,
        role=MembershipRole.ADMIN,
        status=MembershipStatus.APPROVED
    ).exists()

    if not is_admin:
        messages.error(request, 'Only admins can edit this group.')
        if request.headers.get('HX-Request'):
            return htmx_location_response(reverse('groups:groups_detail', kwargs={'group_id': group_id}))
        return redirect('groups:groups_detail', group_id=group_id)

    if request.method == 'POST':
        form = GroupForm(request.POST, request.FILES, instance=group)
        if form.is_valid():
            form.save()
            messages.success(request, 'Squad updated.')
            if request.headers.get('HX-Request'):
                return htmx_location_response(reverse('groups:groups_detail', kwargs={'group_id': group_id}))
            return redirect('groups:groups_detail', group_id=group_id)
    else:
        form = GroupForm(instance=group)
    return render(request, 'groups/create_group.html', {'form': form, 'group': group})


@login_required
@transaction.atomic
def invite_to_group(request, group_id, user_id):
    group = get_object_or_404(Group, id=group_id)
    target = get_object_or_404(User, id=user_id)

    # Check caller is member
    try:
        caller_membership = Membership.objects.get(
            user=request.user,
            group=group,
            status=MembershipStatus.APPROVED
        )
    except Membership.DoesNotExist:
        messages.error(request, 'You must be a member to invite users.')
        if request.headers.get('HX-Request'):
            return htmx_location_response(reverse('groups:groups_detail', kwargs={'group_id': group_id}))
        return redirect('groups:groups_detail', group_id=group_id)

    if group.invite_permission == InvitePermission.ADMINS_ONLY and caller_membership.role != MembershipRole.ADMIN:
        messages.error(request, 'Only admins can invite users to this group.')
        if request.headers.get('HX-Request'):
            return htmx_location_response(reverse('groups:groups_detail', kwargs={'group_id': group_id}))
        return redirect('groups:groups_detail', group_id=group_id)

    # Check if target is already a member
    if Membership.objects.filter(group=group, user=target, status=MembershipStatus.APPROVED).exists():
        messages.info(request, f'{target.username} is already a member.')
    else:
        expires = timezone.now() + datetime.timedelta(days=7)
        invitation, created = GroupInvitation.objects.get_or_create(
            group=group,
            invitee=target,
            status='PENDING',
            defaults={'inviter': request.user, 'expires_at': expires}
        )
        if created:
            try:
                Membership.objects.get_or_create(
                    group=group,
                    user=target,
                    defaults={'status': MembershipStatus.PENDING, 'role': MembershipRole.MEMBER}
                )
            except IntegrityError:
                pass
            send_group_invite_notification(target, request.user, group)
            messages.success(request, f'Invite sent to {target.username}.')
        else:
            messages.info(request, f'An invitation for {target.username} is already pending.')

    if request.headers.get('HX-Request'):
        return htmx_location_response(reverse('groups:groups_detail', kwargs={'group_id': group_id}))
    return redirect('groups:groups_detail', group_id=group_id)


@login_required
@transaction.atomic
def respond_to_invite(request, notif_id, action):
    notif = get_object_or_404(NotificationObject, notification_id=notif_id, recipient=request.user)
    group_id = notif.context_id or (notif.metadata and notif.metadata.get('group_id'))
    if not group_id:
        messages.error(request, 'Invalid invitation: group not found.')
        return redirect('notifications:notifications')

    group = get_object_or_404(Group, id=group_id)

    invitation = GroupInvitation.objects.filter(
        group=group,
        invitee=request.user,
        status='PENDING'
    ).order_by('-created_at').first()

    if action == 'accept':
        if invitation and invitation.expires_at and invitation.expires_at < timezone.now():
            invitation.status = 'EXPIRED'
            invitation.save()
            notif.status = 'EXPIRED'
            notif.save(update_fields=['status'])
            messages.error(request, 'This invitation has expired.')
            if request.headers.get('HX-Request'):
                return HttpResponse('')
            return redirect('notifications:notifications')

        if invitation:
            invitation.status = 'ACCEPTED'
            invitation.save()

        membership = Membership.objects.filter(group=group, user=request.user).first()
        if not membership:
            membership = Membership(
                group=group,
                user=request.user,
                role=MembershipRole.MEMBER,
                status=MembershipStatus.APPROVED
            )
            membership._skip_signal_notification = True
            membership.save()
        elif membership.status != MembershipStatus.APPROVED:
            membership.status = MembershipStatus.APPROVED
            membership._skip_signal_notification = True
            membership.save()

        messages.success(request, f'You joined {group.name}!')
    else:
        # decline / reject
        if invitation:
            invitation.status = 'DECLINED'
            invitation.save()

        Membership.objects.filter(
            group=group,
            user=request.user,
            status=MembershipStatus.PENDING
        ).delete()
        messages.info(request, 'Invite declined.')

    notif.status = 'ARCHIVED'
    notif.save(update_fields=['status'])
    invalidate_unread_count_cache(request.user.id)

    if request.headers.get('HX-Request'):
        response = HttpResponse('')
        response['HX-Trigger'] = 'updateUnreadCount'
        return response
    return redirect('notifications:notifications')


@login_required
@transaction.atomic
def approve_from_notification(request, group_id, user_id):
    """Approve a group join request from notification"""
    group = Group.objects.select_for_update().get(id=group_id)

    # Check if user is admin
    if not Membership.objects.filter(
        user=request.user,
        group=group,
        role=MembershipRole.ADMIN,
        status=MembershipStatus.APPROVED
    ).exists():
        messages.error(request, 'Only admins can approve join requests.')
        return redirect('notifications:notifications')

    join_req = GroupJoinRequest.objects.filter(
        group=group,
        user_id=user_id,
        status='PENDING'
    ).first()

    membership = Membership.objects.filter(
        user_id=user_id,
        group=group
    ).first()

    if not join_req and (not membership or membership.status != MembershipStatus.PENDING):
        messages.error(request, f'No pending membership request found for this user in {group.name}.')
        return redirect('notifications:notifications')

    if join_req:
        join_req.status = 'APPROVED'
        join_req.reviewed_by = request.user
        join_req.reviewed_at = timezone.now()
        join_req.save()

    if membership:
        membership.status = MembershipStatus.APPROVED
        membership.role = MembershipRole.MEMBER
        membership._skip_signal_notification = True
        membership.save()
    else:
        target_user = get_object_or_404(User, id=user_id)
        membership = Membership(
            user=target_user,
            group=group,
            role=MembershipRole.MEMBER,
            status=MembershipStatus.APPROVED
        )
        membership._skip_signal_notification = True
        membership.save()

    # Send welcome notification to the approved user
    send_group_approved_notification(membership.user, group, request.user)

    # Peer admin notification archival & WebSocket broadcast
    _archive_group_request_notifications(group.id, user_id)

    messages.success(request, f'{membership.user.username} has been approved to join {group.name}.')
    if request.headers.get('HX-Request'):
        response = HttpResponse('')
        response['HX-Trigger'] = 'updateUnreadCount'
        return response
    return redirect('notifications:notifications')


@login_required
@transaction.atomic
def reject_from_notification(request, group_id, user_id):
    """Reject a group join request from notification"""
    group = Group.objects.select_for_update().get(id=group_id)

    # Check if user is admin
    if not Membership.objects.filter(
        user=request.user,
        group=group,
        role=MembershipRole.ADMIN,
        status=MembershipStatus.APPROVED
    ).exists():
        messages.error(request, 'Only admins can reject join requests.')
        return redirect('notifications:notifications')

    join_req = GroupJoinRequest.objects.filter(
        group=group,
        user_id=user_id,
        status='PENDING'
    ).first()

    membership = Membership.objects.filter(
        user_id=user_id,
        group=group
    ).first()

    if not join_req and (not membership or membership.status != MembershipStatus.PENDING):
        messages.error(request, 'No pending membership request found for this user.')
        return redirect('notifications:notifications')

    if join_req:
        join_req.status = 'REJECTED'
        join_req.reviewed_by = request.user
        join_req.reviewed_at = timezone.now()
        join_req.save()

    target_user = None
    if membership:
        target_user = membership.user
        membership.status = MembershipStatus.REJECTED
        membership._skip_signal_notification = True
        membership.save()
    else:
        target_user = get_object_or_404(User, id=user_id)

    # Send rejection notification to the user
    send_group_rejected_notification(target_user, group, request.user)

    # Peer admin notification archival & WebSocket broadcast
    _archive_group_request_notifications(group.id, user_id)

    messages.info(request, f'{target_user.username}\'s request to join {group.name} was rejected.')
    if request.headers.get('HX-Request'):
        response = HttpResponse('')
        response['HX-Trigger'] = 'updateUnreadCount'
        return response
    return redirect('notifications:notifications')



@login_required
def group_unread_counts_api(request):
    """
    API endpoint to get unread notification counts for user's groups.
    Returns JSON with group_id -> count mapping.
    """
    user_groups = Group.objects.filter(memberships__user=request.user, memberships__status=MembershipStatus.APPROVED)
    all_group_ids = list(user_groups.values_list('id', flat=True))
    group_unread_counts = get_group_unread_counts(request.user, all_group_ids)
    return JsonResponse(group_unread_counts)


@login_required
def group_activity_status_api(request):
    """
    API endpoint to check if user has any unread group activity for the navigation red dot.
    """
    from notifications.services.notification_service import check_any_unread_group_activity
    return JsonResponse({
        'has_unread': check_any_unread_group_activity(request.user)
    })



@login_required
def search_users_view(request):
    """
    Search users by username or global role for role assignment.
    Returns JSON results.
    Supports role keywords: president, delegate, verified
    """
    from django.conf import settings
    from users.models import GlobalRole

    query = request.GET.get('q', '').strip().lower()
    if len(query) < 2:
        return JsonResponse([], safe=False)

    # Check for role keywords
    role_keywords = {
        'president': GlobalRole.PRESIDENT,
        'delegate': GlobalRole.DELEGATE,
        'verified': GlobalRole.VERIFIED,
    }

    # If query matches a role keyword, search by role
    if query in role_keywords:
        users = User.objects.filter(
            global_role=role_keywords[query]
        ).values('id', 'username', 'first_name', 'last_name', 'profile_pic')[:20]
    else:
        # Otherwise search by username
        users = User.objects.filter(
            username__icontains=query
        ).values('id', 'username', 'first_name', 'last_name', 'profile_pic')[:20]

    # Convert profile_pic paths to full URLs
    user_list = list(users)
    for user in user_list:
        if user['profile_pic']:
            user['profile_pic'] = request.build_absolute_uri(settings.MEDIA_URL + str(user['profile_pic']))
        else:
            user['profile_pic'] = '/static/images/default_user.jpg'

    return JsonResponse(user_list, safe=False)


@login_required
def clear_recent_group_searches(request):
    """
    Clear recent group searches from session.
    """
    request.session['recent_group_searches'] = []
    from django.http import HttpResponseRedirect
    return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/groups/dashboard/'))


@login_required
def view_group_photo_fullscreen(request, group_id, photo_type):
    """
    View group or cover photo in full screen mode.
    Only accessible if the viewer is a member of the group or is an admin.
    """
    group = get_object_or_404(Group, id=group_id)

    # Check if user is allowed to view the photo (must be an approved member)
    is_member = Membership.objects.filter(
        user=request.user,
        group=group,
        status=MembershipStatus.APPROVED
    ).exists()

    if not is_member:
        messages.error(request, 'You need to be a member of this group to view photos in full screen.')
        return redirect('groups:groups_detail', group_id=group_id)

    # Determine which photo to show
    if photo_type == 'group':
        photo_url = group.get_photo_url
        photo_title = f"{group.name}'s Group Photo"
    elif photo_type == 'cover':
        if not group.cover_photo:
            messages.error(request, 'This group does not have a cover photo.')
            return redirect('groups:groups_detail', group_id=group_id)
        photo_url = group.cover_photo.url
        photo_title = f"{group.name}'s Cover Photo"
    else:
        messages.error(request, 'Invalid photo type.')
        return redirect('groups:groups_detail', group_id=group_id)

    # Get like count and check if current user liked the photo
    from .models import GroupPhotoLike
    like_count = GroupPhotoLike.objects.filter(
        group=group,
        photo_type=photo_type
    ).count()

    is_liked = GroupPhotoLike.objects.filter(
        user=request.user,
        group=group,
        photo_type=photo_type
    ).exists()

    context = {
        'group': group,
        'photo_url': photo_url,
        'photo_type': photo_type,
        'photo_title': photo_title,
        'like_count': like_count,
        'is_liked': is_liked,
    }

    if request.headers.get('HX-Request'):
        return render(request, 'groups/partials/group_photo_fullscreen_navigation_partial.html', context)
    return render(request, 'groups/group_photo_fullscreen.html', context)


@login_required
def group_members_search(request, group_id):
    """Search for group members via HTMX for the members modal"""
    from django.core.paginator import Paginator
    from django.db.models import Q
    import logging

    logger = logging.getLogger(__name__)

    search_query = request.GET.get('q', '').strip()
    page = int(request.GET.get('page', 1))
    page_size = 20

    logger.info(f"group_members_search called - group_id={group_id}, q={search_query}, page={page}")

    group = get_object_or_404(Group, id=group_id)

    # Check if user is a member
    try:
        membership = Membership.objects.get(
            user=request.user,
            group=group,
            status=MembershipStatus.APPROVED
        )
    except Membership.DoesNotExist:
        return JsonResponse({'error': 'You must be a member to view group members'}, status=403)

    # Get approved memberships with search filter
    memberships_queryset = group.memberships.filter(status=MembershipStatus.APPROVED).select_related('user', 'user__course', 'user__year').order_by('-id')

    if search_query:
        memberships_queryset = memberships_queryset.filter(
            Q(user__username__icontains=search_query) |
            Q(user__first_name__icontains=search_query) |
            Q(user__last_name__icontains=search_query)
        )

    paginator = Paginator(memberships_queryset, page_size)
    memberships_page = paginator.get_page(page)

    # Get following IDs for display
    def get_following_ids(user):
        from users.models import Follow
        return Follow.objects.filter(follower=user).values_list('followed_id', flat=True)

    following_ids = list(get_following_ids(request.user))

    # Build next page URL
    next_url = None
    if memberships_page.has_next():
        url_params = []
        if search_query:
            url_params.append(f"q={search_query}")
        url_params.append(f"page={memberships_page.next_page_number()}")
        next_url = f"?{'&'.join(url_params)}"

    logger.info(f"Rendering group_members_list.html with {len(memberships_page)} memberships")

    return render(request, 'groups/partials/group_members_list.html', {
        'memberships': memberships_page,
        'group': group,
        'following_ids': following_ids,
        'has_more': memberships_page.has_next(),
        'next_url': next_url,
        'search_query': search_query,
    })


@login_required
def group_announcements_view(request, group_id):
    """View group announcements page"""
    group = get_object_or_404(Group, id=group_id)

    # Check if user is a member
    try:
        membership = Membership.objects.get(
            user=request.user,
            group=group,
            status=MembershipStatus.APPROVED
        )
    except Membership.DoesNotExist:
        messages.error(request, 'You must be a member to view group announcements.')
        return redirect('groups:groups_detail', group_id=group.id)

    # Check if user is admin
    is_admin = membership.role == MembershipRole.ADMIN

    # Fetch announcements with optimized queries
    announcements = Announcement.objects.filter(
        group=group
    ).select_related('author').order_by('-is_pinned', '-created_at')

    # Separate pinned and unpinned announcements
    pinned_announcements = [a for a in announcements if a.is_pinned]
    all_announcements = [a for a in announcements if not a.is_pinned]

    unread_announcement_ids = get_unread_announcement_ids_for_user(request.user, group.id)

    context = {
        'group': group,
        'pinned_announcements': pinned_announcements,
        'all_announcements': all_announcements,
        'announcements': announcements,
        'is_admin': is_admin,
        'unread_announcement_ids': unread_announcement_ids,
    }

    if request.headers.get('HX-Request'):
        response = render(request, 'groups/partials/group_announcements_navigation_partial.html', context)
        return response

    return render(request, 'groups/group_announcements.html', context)


@login_required
def group_settings_view(request, group_id):
    """View group settings page"""
    group = get_object_or_404(Group, id=group_id)

    # Check if user is a member
    try:
        membership = Membership.objects.get(
            user=request.user,
            group=group,
            status=MembershipStatus.APPROVED
        )
    except Membership.DoesNotExist:
        messages.error(request, 'You must be a member to view group settings.')
        if request.headers.get('HX-Request'):
            return htmx_location_response(reverse('groups:groups_detail', kwargs={'group_id': group.id}))
        return redirect('groups:groups_detail', group_id=group.id)

    # Check if user is admin
    is_admin = membership.role == MembershipRole.ADMIN

    context = {
        'group': group,
        'is_admin': is_admin,
    }

    if request.headers.get('HX-Request'):
        return render(request, 'groups/partials/group_settings_navigation_partial.html', context)
    return render(request, 'groups/group_settings.html', context)


@login_required
def group_chat_view(request, group_id):
    """Open the group's canonical conversation in the shared messaging UI."""
    from groups.services.chat_service import open_group_chat
    group = get_object_or_404(Group, pk=group_id)
    conversation = open_group_chat(group.pk, request.user)
    url = reverse('messaging:conversation_detail', args=[conversation.pk])
    if request.headers.get('HX-Request'):
        response = HttpResponse(status=200)
        response['HX-Redirect'] = url
        return response
    return redirect(url)


@login_required
def group_settings_details_view(request, group_id):
    """View group settings details page"""
    group = get_object_or_404(Group, id=group_id)

    # Check if user is a member
    try:
        membership = Membership.objects.get(
            user=request.user,
            group=group,
            status=MembershipStatus.APPROVED
        )
    except Membership.DoesNotExist:
        messages.error(request, 'You must be a member to view group settings.')
        if request.headers.get('HX-Request'):
            return htmx_location_response(reverse('groups:groups_detail', kwargs={'group_id': group.id}))
        return redirect('groups:groups_detail', group_id=group.id)

    # Check if user is admin
    is_admin = membership.role == MembershipRole.ADMIN

    # Check if user is moderator
    is_moderator = membership.role == MembershipRole.MODERATOR

    # Determine if user can edit based on group's edit_permission setting
    can_edit = False
    if group.edit_permission == EditPermission.ADMINS_ONLY:
        can_edit = is_admin
    elif group.edit_permission == EditPermission.ADMINS_MODERATORS:
        can_edit = is_admin or is_moderator

    if request.method == 'POST' and can_edit:
        form = GroupDetailsForm(request.POST, request.FILES, instance=group)
        if form.is_valid():
            form.save()
            messages.success(request, 'Group details updated successfully.')
            if request.headers.get('HX-Request'):
                return htmx_location_response(reverse('groups:group_settings_details', kwargs={'group_id': group.id}))
            return redirect('groups:group_settings_details', group_id=group.id)
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = GroupDetailsForm(instance=group)

    context = {
        'group': group,
        'form': form,
        'can_edit': can_edit,
        'subpage_content_partial': 'groups/settings/partials/group_settings_details_content.html',
    }

    if request.headers.get('HX-Request'):
        return render(request, 'groups/settings/partials/group_settings_subpage_navigation_partial.html', context)
    return render(request, 'groups/settings/group_settings_details.html', context)


@login_required
def group_settings_members_view(request, group_id):
    """View group settings members page"""
    group = get_object_or_404(Group, id=group_id)

    # Check if user is a member
    try:
        membership = Membership.objects.get(
            user=request.user,
            group=group,
            status=MembershipStatus.APPROVED
        )
    except Membership.DoesNotExist:
        messages.error(request, 'You must be a member to view group settings.')
        if request.headers.get('HX-Request'):
            return htmx_location_response(reverse('groups:groups_detail', kwargs={'group_id': group.id}))
        return redirect('groups:groups_detail', group_id=group.id)

    # Check if user is admin (only admins can manage members)
    is_admin = membership.role == MembershipRole.ADMIN

    # Check if user can invite based on group's invite_permission setting
    can_invite = False
    if group.invite_permission == InvitePermission.ADMINS_ONLY:
        can_invite = is_admin
    elif group.invite_permission == InvitePermission.ALL_MEMBERS:
        can_invite = True

    # Handle role assignment
    if request.method == 'POST' and is_admin:
        action = request.POST.get('action')
        user_id = request.POST.get('user_id')

        if action == 'assign_role' and user_id:
            try:
                with transaction.atomic():
                    locked_group = Group.objects.select_for_update().get(id=group.id)
                    target_membership = Membership.objects.get(
                        user_id=user_id,
                        group=locked_group,
                        status=MembershipStatus.APPROVED
                    )
                    new_role = request.POST.get('role')

                    # Cannot demote yourself from admin
                    if target_membership.user == request.user and target_membership.role == MembershipRole.ADMIN and new_role != MembershipRole.ADMIN:
                        messages.error(request, 'You cannot change your own admin role.')
                    else:
                        # Check max admin constraint
                        if new_role == MembershipRole.ADMIN and target_membership.role != MembershipRole.ADMIN:
                            admin_count = Membership.objects.filter(
                                group=locked_group,
                                role=MembershipRole.ADMIN,
                                status=MembershipStatus.APPROVED
                            ).count()
                            if admin_count >= 5:
                                messages.error(request, 'Maximum of 5 admins allowed per group.')
                            else:
                                target_membership.role = new_role
                                target_membership.save()
                                messages.success(request, f'Role updated for {target_membership.user.username}.')
                        else:
                            target_membership.role = new_role
                            target_membership.save()
                            messages.success(request, f'Role updated for {target_membership.user.username}.')
            except Membership.DoesNotExist:
                messages.error(request, 'Member not found.')

        elif action == 'remove_member' and user_id:
            try:
                with transaction.atomic():
                    locked_group = Group.objects.select_for_update().get(id=group.id)
                    target_membership = Membership.objects.get(
                        user_id=user_id,
                        group=locked_group,
                        status=MembershipStatus.APPROVED
                    )

                    # Cannot remove yourself if you're the last admin
                    if target_membership.role == MembershipRole.ADMIN:
                        admin_count = Membership.objects.filter(
                            group=locked_group,
                            role=MembershipRole.ADMIN,
                            status=MembershipStatus.APPROVED
                        ).count()
                        if admin_count == 1:
                            messages.error(request, 'You cannot leave as the last admin. Assign a successor first.' if target_membership.user == request.user else 'Cannot remove the last admin.')
                        else:
                            target_user = target_membership.user
                            target_membership.delete()
                            evict_group_member_socket(group.id, target_user.id, reason="Removed from group.")
                            messages.success(request, f'{target_user.username} removed from group.')
                            if target_user == request.user:
                                if request.headers.get('HX-Request'):
                                    return htmx_location_response(reverse('groups:group_settings_members', kwargs={'group_id': group.id}))
                                return redirect('groups:group_settings_members', group_id=group.id)
                    else:
                        target_user = target_membership.user
                        target_membership.delete()
                        evict_group_member_socket(group.id, target_user.id, reason="Removed from group.")
                        messages.success(request, f'{target_user.username} removed from group.')
            except Membership.DoesNotExist:
                messages.error(request, 'Member not found.')


        if request.headers.get('HX-Request'):
            return htmx_location_response(reverse('groups:group_settings_members', kwargs={'group_id': group.id}))

    # Get all approved members with their roles
    members = Membership.objects.filter(
        group=group,
        status=MembershipStatus.APPROVED
    ).select_related('user').order_by(
        # Custom ordering: ADMIN -> MODERATOR -> DELEGATE -> MEMBER
        # Django doesn't have a direct way to order by enum values, so we use case statements
        # For now, we'll use a simple approach and let Python handle the sorting
    )

    # Sort members by role priority: ADMIN > MODERATOR > DELEGATE > MEMBER
    role_priority = {
        MembershipRole.ADMIN: 0,
        MembershipRole.MODERATOR: 1,
        MembershipRole.DELEGATE: 2,
        MembershipRole.MEMBER: 3,
    }
    members = sorted(members, key=lambda m: (role_priority.get(m.role, 99), m.joined_at))

    # Paginate members (20 per page)
    paginator = Paginator(members, 20)
    page_number = request.GET.get('page', 1)
    members_page = paginator.get_page(page_number)

    context = {
        'group': group,
        'members': members_page,
        'is_admin': is_admin,
        'can_invite': can_invite,
        'membership_roles': MembershipRole,
        'subpage_content_partial': 'groups/settings/partials/group_settings_members_content.html',
    }

    if request.headers.get('HX-Request'):
        return render(request, 'groups/settings/partials/group_settings_subpage_navigation_partial.html', context)
    return render(request, 'groups/settings/group_settings_members.html', context)


@login_required
def get_mutual_friends_api(request, group_id):
    """API endpoint to get mutual friends for group invites"""
    group = get_object_or_404(Group, id=group_id)

    # Check if user is member
    try:
        membership = Membership.objects.get(
            user=request.user,
            group=group,
            status=MembershipStatus.APPROVED
        )
    except Membership.DoesNotExist:
        return JsonResponse({'error': 'You must be a member to invite users'}, status=403)

    # Check invite permission
    if group.invite_permission == InvitePermission.ADMINS_ONLY:
        if membership.role != MembershipRole.ADMIN:
            return JsonResponse({'error': 'Only admins can invite users'}, status=403)

    # Get mutual friends (users who follow request.user and are followed by request.user)
    following_ids = Follow.objects.filter(follower=request.user).values_list('followed_id', flat=True)
    follower_ids = Follow.objects.filter(followed=request.user).values_list('follower_id', flat=True)

    # Mutual friends are in both lists
    mutual_friend_ids = set(following_ids) & set(follower_ids)

    # Exclude existing group members (both APPROVED and PENDING)
    existing_member_ids = Membership.objects.filter(
        group=group
    ).values_list('user_id', flat=True)

    available_friend_ids = mutual_friend_ids - set(existing_member_ids)

    # Get user details with profile_pic URL
    friends = []
    for user in User.objects.filter(id__in=available_friend_ids):
        friend_data = {
            'id': user.id,
            'username': user.username,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'profile_pic': user.profile_pic.url if user.profile_pic else None
        }
        friends.append(friend_data)

    return JsonResponse({'friends': friends})


@login_required
@require_POST
def send_group_invites_api(request, group_id):
    """API endpoint to send group invites"""
    group = get_object_or_404(Group, id=group_id)

    # Check if user is member
    try:
        membership = Membership.objects.get(
            user=request.user,
            group=group,
            status=MembershipStatus.APPROVED
        )
    except Membership.DoesNotExist:
        return JsonResponse({'error': 'You must be a member to invite users'}, status=403)

    # Check invite permission
    if group.invite_permission == InvitePermission.ADMINS_ONLY:
        if membership.role != MembershipRole.ADMIN:
            return JsonResponse({'error': 'Only admins can invite users'}, status=403)

    try:
        data = json.loads(request.body)
        user_ids = data.get('user_ids', [])

        # Debug: Log received user IDs
        print(f"DEBUG: Received user_ids: {user_ids}")

        # Validate max 10 invites
        if len(user_ids) > 10:
            return JsonResponse({'error': 'Maximum 10 invites at once'}, status=400)

        sent_count = 0
        skipped_count = 0
        not_found_count = 0
        errors = []

        for user_id in user_ids:
            try:
                user = User.objects.get(id=user_id)

                # Check if user is already an approved member
                if Membership.objects.filter(user=user, group=group, status=MembershipStatus.APPROVED).exists():
                    skipped_count += 1
                    errors.append(f"{user.username} is already a member")
                    continue

                # Check if an active invitation is already pending
                if GroupInvitation.objects.filter(
                    group=group,
                    invitee=user,
                    status='PENDING',
                    expires_at__gt=timezone.now()
                ).exists():
                    skipped_count += 1
                    errors.append(f"{user.username} is already invited")
                    continue

                # Create GroupInvitation with 7 days expiration
                expires = timezone.now() + datetime.timedelta(days=7)
                GroupInvitation.objects.create(
                    group=group,
                    inviter=request.user,
                    invitee=user,
                    status='PENDING',
                    expires_at=expires
                )

                # Create pending membership for backwards compatibility
                try:
                    Membership.objects.get_or_create(
                        user=user,
                        group=group,
                        defaults={
                            'status': MembershipStatus.PENDING,
                            'role': MembershipRole.MEMBER
                        }
                    )
                except IntegrityError:
                    pass

                # Send notification
                send_group_invite_notification(user, request.user, group)
                sent_count += 1

            except User.DoesNotExist:
                not_found_count += 1
                errors.append(f"User ID {user_id} not found")
                continue
            except Exception as e:
                errors.append(f"Error inviting user {user_id}: {str(e)}")
                continue

        print(f"DEBUG: Final counts - sent: {sent_count}, skipped: {skipped_count}, not_found: {not_found_count}")

        return JsonResponse({
            'success': True,
            'sent_count': sent_count,
            'skipped_count': skipped_count,
            'not_found_count': not_found_count,
            'errors': errors
        })

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)


@login_required
def group_settings_privacy_view(request, group_id):
    """View group settings privacy page"""
    group = get_object_or_404(Group, id=group_id)

    # Check if user is a member
    try:
        membership = Membership.objects.get(
            user=request.user,
            group=group,
            status=MembershipStatus.APPROVED
        )
    except Membership.DoesNotExist:
        messages.error(request, 'You must be a member to view group settings.')
        if request.headers.get('HX-Request'):
            return htmx_location_response(reverse('groups:groups_detail', kwargs={'group_id': group.id}))
        return redirect('groups:groups_detail', group_id=group.id)

    # Check if user is admin (only admins can access privacy settings)
    is_admin = membership.role == MembershipRole.ADMIN
    if not is_admin:
        messages.error(request, 'Only admins can access privacy settings.')
        if request.headers.get('HX-Request'):
            return htmx_location_response(reverse('groups:group_settings', kwargs={'group_id': group.id}))
        return redirect('groups:group_settings', group_id=group.id)

    if request.method == 'POST':
        form = GroupPrivacyForm(request.POST, instance=group)
        if form.is_valid():
            form.save()
            messages.success(request, 'Privacy settings updated successfully.')
            if request.headers.get('HX-Request'):
                return htmx_location_response(reverse('groups:group_settings_privacy', kwargs={'group_id': group.id}))
            return redirect('groups:group_settings_privacy', group_id=group.id)
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = GroupPrivacyForm(instance=group)

    context = {
        'group': group,
        'form': form,
        'is_admin': is_admin,
        'subpage_content_partial': 'groups/settings/partials/group_settings_privacy_content.html',
    }

    if request.headers.get('HX-Request'):
        return render(request, 'groups/settings/partials/group_settings_subpage_navigation_partial.html', context)
    return render(request, 'groups/settings/group_settings_privacy.html', context)


@login_required
def group_settings_announcements_view(request, group_id):
    """View group settings announcements page"""
    group = get_object_or_404(Group, id=group_id)

    # Check if user is a member
    try:
        membership = Membership.objects.get(
            user=request.user,
            group=group,
            status=MembershipStatus.APPROVED
        )
    except Membership.DoesNotExist:
        messages.error(request, 'You must be a member to view group settings.')
        if request.headers.get('HX-Request'):
            return htmx_location_response(reverse('groups:groups_detail', kwargs={'group_id': group.id}))
        return redirect('groups:groups_detail', group_id=group.id)

    context = {
        'group': group,
        'subpage_content_partial': 'groups/settings/partials/group_settings_announcements_content.html',
    }

    if request.headers.get('HX-Request'):
        return render(request, 'groups/settings/partials/group_settings_subpage_navigation_partial.html', context)
    return render(request, 'groups/settings/group_settings_announcements.html', context)


@login_required
def group_settings_documents_view(request, group_id):
    """View group settings documents page"""
    group = get_object_or_404(Group, id=group_id)

    # Check if user is a member
    try:
        membership = Membership.objects.get(
            user=request.user,
            group=group,
            status=MembershipStatus.APPROVED
        )
    except Membership.DoesNotExist:
        messages.error(request, 'You must be a member to view group settings.')
        if request.headers.get('HX-Request'):
            return htmx_location_response(reverse('groups:groups_detail', kwargs={'group_id': group.id}))
        return redirect('groups:groups_detail', group_id=group.id)

    context = {
        'group': group,
        'subpage_content_partial': 'groups/settings/partials/group_settings_documents_content.html',
    }

    if request.headers.get('HX-Request'):
        return render(request, 'groups/settings/partials/group_settings_subpage_navigation_partial.html', context)
    return render(request, 'groups/settings/group_settings_documents.html', context)


@login_required
def group_settings_about_view(request, group_id):
    """View group settings about page"""
    group = get_object_or_404(Group, id=group_id)

    # Check if user is a member
    try:
        membership = Membership.objects.get(
            user=request.user,
            group=group,
            status=MembershipStatus.APPROVED
        )
    except Membership.DoesNotExist:
        messages.error(request, 'You must be a member to view group settings.')
        if request.headers.get('HX-Request'):
            return htmx_location_response(reverse('groups:groups_detail', kwargs={'group_id': group.id}))
        return redirect('groups:groups_detail', group_id=group.id)

    context = {
        'group': group,
        'subpage_content_partial': 'groups/settings/partials/group_settings_about_content.html',
    }

    if request.headers.get('HX-Request'):
        return render(request, 'groups/settings/partials/group_settings_subpage_navigation_partial.html', context)
    return render(request, 'groups/settings/group_settings_about.html', context)


@login_required
@require_POST
def mark_announcement_read_api(request, announcement_id):
    """API endpoint to mark an announcement notification as read"""
    announcement = get_object_or_404(Announcement, id=announcement_id)

    # Check if user is a member of the group
    is_member = Membership.objects.filter(
        user=request.user,
        group=announcement.group,
        status=MembershipStatus.APPROVED
    ).exists()
    if not is_member and not request.user.is_superuser:
        return JsonResponse({'error': 'You must be a member of this group'}, status=403)

    mark_announcement_as_read_and_invalidate(request.user, announcement.id, group_id=announcement.group_id)
    return JsonResponse({
        'status': 'success',
        'announcement_id': announcement.id,
        'group_id': announcement.group_id
    })



@login_required
def retired_group_chat_api(request, **kwargs):
    """Legacy message IDs must never be interpreted as shared Message IDs."""
    return JsonResponse({'error': 'This endpoint has moved. Open the group Chat link to use messaging.'}, status=410)
