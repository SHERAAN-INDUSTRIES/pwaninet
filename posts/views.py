import logging
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse, FileResponse, HttpResponseForbidden, HttpResponseNotFound
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import ensure_csrf_cookie
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.urls import reverse
from urllib.parse import urlencode
from django.core import signing
from django.conf import settings
from celery.result import AsyncResult

logger = logging.getLogger(__name__)


def download_post_media(request, share_id):
    """Serve post media only through a short-lived, author-permission-checked URL."""
    token = request.GET.get('token', '')
    try:
        payload = signing.loads(token, salt='posts.media-download', max_age=900)
    except signing.BadSignature:
        return HttpResponseForbidden('This download link is invalid or expired.')
    if payload.get('post') != str(share_id):
        return HttpResponseForbidden('This download link is invalid.')
    # The signed, short-lived token is the authorization for native downloaders,
    # which do not consistently forward the WebView session cookie.
    download_user = get_user_model().objects.filter(pk=payload.get('user'), is_active=True).first()
    if not download_user:
        return HttpResponseForbidden('This download link is invalid or expired.')
    post = get_object_or_404(visible_posts_for(download_user), share_id=share_id)
    if not post.allow_downloads:
        return HttpResponseForbidden('The author has disabled downloads for this post.')
    media_type = payload.get('type')
    media = None
    if media_type == 'image':
        media = post.images.filter(pk=payload.get('media')).first()
        media = media.image if media else None
    elif media_type == 'video':
        media = post.video or post.video_preview
    elif media_type == 'audio':
        media = post.audio
    elif media_type == 'document':
        media = post.docs or (post.shared_document.file if post.shared_document_id and post.shared_document.file else None)
    if not media or not getattr(media, 'name', None):
        return HttpResponseNotFound('Media not found.')
    try:
        response = FileResponse(media.open('rb'), as_attachment=True, filename=media.name.rsplit('/', 1)[-1])
        return response
    except (OSError, ValueError):
        return HttpResponseNotFound('Media is unavailable.')


from .models import Post, Comment, Report, Like, Repost, HiddenPost, AuthorPreference, SharedPost, PostImage, PostImageLike, PostImageComment
from .serializers import (
    PostSerializer, PostCreateSerializer, PostUpdateSerializer,
    CommentSerializer, CommentCreateSerializer, ReportCreateSerializer,
    ReportSerializer, RepostSerializer, RepostCreateSerializer,
    HiddenPostSerializer, HiddenPostCreateSerializer,
    AuthorPreferenceSerializer, AuthorPreferenceCreateSerializer,
    SharedPostSerializer, SharedPostCreateSerializer
)
from .permissions import CanDeletePost, CanEditPost, IsPostAuthorOrReadOnly
from groups.permissions import IsApprovedMember
from courses.models import Unit
from users.models import User
from users.services.privacy import visible_posts_for
from posts.forms import PostForm
from posts.services.comment_service import build_comments_context, handle_add_comment_request, toggle_comment_like_for_user, add_comment_to_image
from posts.services.feed_service import build_home_feed_context
from posts.services.post_service import toggle_post_like_for_user, create_post_for_user
from posts.services.repost_service import create_repost, delete_repost, get_post_reposts, toggle_repost
from posts.services.hide_service import hide_post, unhide_post, is_post_hidden
from posts.services.author_preference_service import set_author_preference, get_author_preference, get_all_preferences
from posts.services.share_service import share_post, get_shared_posts, mark_share_as_viewed, get_user_received_shares
from notifications.services.notification_service import get_cached_unread_count
from pwaninet.utils.htmx import htmx_location_response


class PostViewSet(viewsets.ModelViewSet):
    lookup_field = "share_id"
    """
    ViewSet for managing posts.
    """
    permission_classes = [IsAuthenticated]
    queryset = Post.objects.select_related('author', 'group', 'course', 'unit').all()

    def get_serializer_class(self):
        if self.action == 'create':
            return PostCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return PostUpdateSerializer
        return PostSerializer

    def get_queryset(self):
        return visible_posts_for(self.request.user, self.queryset)

    def get_permissions(self):
        if self.action == 'destroy':
            return [IsAuthenticated(), CanDeletePost()]
        elif self.action in ['update', 'partial_update']:
            return [IsAuthenticated(), CanEditPost()]
        return super().get_permissions()

    def create(self, request, *args, **kwargs):
        import logging
        logger = logging.getLogger(__name__)
        logger.info('[PostViewSet.create] Starting post creation')
        logger.info('[PostViewSet.create] Request data: %s', request.data)
        logger.info('[PostViewSet.create] Request files: %s', request.FILES)
        logger.info('[PostViewSet.create] Custom gradient fields: custom_gradient_text=%s, custom_gradient_color1=%s, custom_gradient_color2=%s, custom_gradient_text_color=%s',
                    request.data.get('custom_gradient_text'),
                    request.data.get('custom_gradient_color1'),
                    request.data.get('custom_gradient_color2'),
                    request.data.get('custom_gradient_text_color'))

        try:
            response = super().create(request, *args, **kwargs)
            logger.info('[PostViewSet.create] Post created successfully')
            logger.info('[PostViewSet.create] Response data: %s', response.data)
            # Mark this as a new post in session for back button logic
            request.session['is_new_post'] = True
            share_id = response.data.get('id') or response.data.get('pk')
            if share_id:
                request.session['new_share_id'] = share_id
                logger.info('[PostViewSet.create] Session flags set for new post ID: %s', share_id)
            else:
                logger.warning('[PostViewSet.create] No post ID found in response data')
            return response
        except Exception as e:
            logger.error('[PostViewSet.create] Error during post creation: %s', str(e))
            logger.error('[PostViewSet.create] Error type: %s', type(e).__name__)
            import traceback
            logger.error('[PostViewSet.create] Traceback: %s', traceback.format_exc())
            raise

    @action(detail=True, methods=['get'], url_path='status')
    def status_detail(self, request, share_id=None):
        """
        GET /api/posts/<share_id>/status/
        Fast, lightweight status endpoint for polling post and video processing readiness.
        """
        post = self.get_object()
        hls_url = ''
        if post.hls_playlist:
            hls_url = f"{settings.MEDIA_URL.rstrip('/')}/{post.hls_playlist.lstrip('/')}"
        return Response({
            'id': str(post.share_id),
            'post_id': post.id,
            'share_id': str(post.share_id),
            'video_status': post.video_status,
            'is_ready': post.video_status == Post.VIDEO_STATUS_READY,
            'hls_url': hls_url,
            'video_duration': post.video_duration,
        })

    @action(detail=True, methods=['post'], url_path='report')
    def report(self, request, *args, **kwargs):
        """
        POST /posts/{id}/report/
        Report a post.
        """
        post = self.get_object()

        # Check if user already reported this post
        if Report.objects.filter(reporter=request.user, post=post).exists():
            return Response(
                {'detail': 'You have already reported this post.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = ReportCreateSerializer(
            data=request.data,
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save(post=post)

        return Response(
            ReportSerializer(serializer.instance).data,
            status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=['post'], url_path='like')
    def like(self, request, *args, **kwargs):
        """
        POST /posts/{id}/like/
        Like a post.
        """
        post = self.get_object()

        # Check if user is approved member of the group
        if post.group:
            from groups.models import Membership, MembershipStatus
            try:
                Membership.objects.get(
                    user=request.user,
                    group=post.group,
                    status=MembershipStatus.APPROVED
                )
            except Membership.DoesNotExist:
                return Response(
                    {'detail': 'You must be an approved member to like posts in this group.'},
                    status=status.HTTP_403_FORBIDDEN
                )

        like, created = Like.objects.get_or_create(
            user=request.user,
            post=post
        )

        if created:
            return Response(
                {'detail': 'Post liked.'},
                status=status.HTTP_201_CREATED
            )
        else:
            like.delete()
            return Response(
                {'detail': 'Post unliked.'},
                status=status.HTTP_200_OK
            )

    @action(detail=True, methods=['post'], url_path='images/(?P<image_id>[^/.]+)/like')
    def image_like(self, request, image_id=None, *args, **kwargs):
        """
        POST /posts/{id}/images/{image_id}/like/
        Like or unlike a specific post image.
        """
        post = self.get_object()

        try:
            post_image = post.images.get(id=image_id)
        except PostImage.DoesNotExist:
            return Response(
                {'detail': 'Image not found.'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Check if user is approved member of the group
        if post.group:
            from groups.models import Membership, MembershipStatus
            try:
                Membership.objects.get(
                    user=request.user,
                    group=post.group,
                    status=MembershipStatus.APPROVED
                )
            except Membership.DoesNotExist:
                return Response(
                    {'detail': 'You must be an approved member to like images in this group.'},
                    status=status.HTTP_403_FORBIDDEN
                )

        like, created = PostImageLike.objects.get_or_create(
            user=request.user,
            post_image=post_image
        )

        likes_count = post_image.likes.count()

        if created:
            return Response(
                {'detail': 'Image liked.', 'likes_count': likes_count},
                status=status.HTTP_201_CREATED
            )
        else:
            like.delete()
            return Response(
                {'detail': 'Image unliked.', 'likes_count': likes_count},
                status=status.HTTP_200_OK
            )

    @action(detail=True, methods=['get', 'post'], url_path='images/(?P<image_id>[^/.]+)/comments')
    def image_comments(self, request, image_id=None, *args, **kwargs):
        """
        GET /posts/{id}/images/{image_id}/comments/
        List all comments for a specific post image.

        POST /posts/{id}/images/{image_id}/comments/
        Create a new comment for a specific post image.
        """
        post = self.get_object()

        try:
            post_image = post.images.get(id=image_id)
        except PostImage.DoesNotExist:
            return Response(
                {'detail': 'Image not found.'},
                status=status.HTTP_404_NOT_FOUND
            )

        if request.method == 'GET':
            comments = post_image.comments.select_related('author').all()

            # Format comments for response
            comments_data = []
            for comment in comments:
                comments_data.append({
                    'id': comment.id,
                    'content': comment.content,
                    'attachment_type': comment.attachment_type,
                    'attachment_url': comment.media_url,
                    'attachment_meta': comment.attachment_meta,
                    'author': {
                        'id': comment.author.id,
                        'username': comment.author.username,
                        'profile_pic': comment.author.profile_pic.url if comment.author.profile_pic else None
                    },
                    'created_at': comment.created_at.strftime('%B %d, %Y at %I:%M %p')
                })

            return Response({
                'comments': comments_data,
                'total_count': len(comments_data)
            }, status=status.HTTP_200_OK)

        elif request.method == 'POST':
            content = request.data.get('content', '').strip()
            attachment_type = request.data.get('attachment_type', Comment.ATTACHMENT_NONE)
            attachment_url = request.data.get('attachment_url', '')
            attachment_image = request.FILES.get('attachment_image') or request.FILES.get('image')
            attachment_meta = request.data.get('attachment_meta', {})
            if isinstance(attachment_meta, str):
                import json
                try:
                    attachment_meta = json.loads(attachment_meta)
                except Exception:
                    attachment_meta = {}

            has_attachment = attachment_type != Comment.ATTACHMENT_NONE and (bool(attachment_image) or bool(attachment_url))
            if not content and not has_attachment:
                return Response(
                    {'detail': 'Comment content or attachment is required.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Check if user is approved member of the group
            if post.group:
                from groups.models import Membership, MembershipStatus
                try:
                    Membership.objects.get(
                        user=request.user,
                        group=post.group,
                        status=MembershipStatus.APPROVED
                    )
                except Membership.DoesNotExist:
                    return Response(
                        {'detail': 'You must be an approved member to comment on images in this group.'},
                        status=status.HTTP_403_FORBIDDEN
                    )

            # Use the service function to add comment (mirrors post comment functionality)
            comment = add_comment_to_image(
                post_image,
                request.user,
                content,
                attachment_type=attachment_type,
                attachment_image=attachment_image,
                attachment_url=attachment_url,
                attachment_meta=attachment_meta
            )

            if not comment:
                return Response(
                    {'detail': 'Failed to add comment.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            return Response({
                'id': comment.id,
                'content': comment.content,
                'author': {
                    'id': comment.author.id,
                    'username': comment.author.username,
                    'full_name': comment.author.get_full_name(),
                    'profile_pic': comment.author.profile_pic.url if comment.author.profile_pic else None
                },
                'created_at': comment.created_at.strftime('%B %d, %Y at %I:%M %p')
            }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='comments')
    def comments(self, request, *args, **kwargs):
        """
        GET /posts/{id}/comments/
        List all comments for a post.
        """
        post = self.get_object()
        comments = post.comments.all()
        serializer = CommentSerializer(comments, many=True, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='repost')
    def repost(self, request, *args, **kwargs):
        """
        POST /posts/{share_id}/repost/
        Toggle repost for user on the original post.
        Does NOT duplicate the Post object - references original post.
        """
        original_post = self.get_object()
        content = request.data.get('content', '') or request.POST.get('content', '')
        group_id = request.data.get('group_id') or request.POST.get('group_id')
        group = None
        if group_id:
            from groups.models import Group
            group = Group.objects.filter(id=group_id).first()

        try:
            result = toggle_repost(request.user, original_post, group=group, content=content)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # Support HTMX response
        if request.headers.get('HX-Request') and not (request.headers.get('Accept') == 'application/json'):
            context = {
                'post': original_post,
                'user_has_reposted': result['is_reposted'],
                'request': request,
            }
            template = 'posts/partials/reel_card.html' if original_post.is_reel else 'posts/partials/post_card.html'
            return render(request, template, context)

        user_avatar = '/static/images/default-avatar.png'
        if request.user.is_authenticated and request.user.profile_pic:
            try:
                user_avatar = request.user.profile_pic.url
            except Exception:
                pass

        return Response({
            'status': 'success',
            'is_reposted': result['is_reposted'],
            'repost_count': result['repost_count'],
            'share_id': str(original_post.share_id),
            'post_id': original_post.id,
            'user_id': request.user.id,
            'user_username': request.user.username,
            'user_name': request.user.get_full_name() or request.user.username,
            'user_avatar': user_avatar,
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['delete'], url_path='repost')
    def delete_repost(self, request, *args, **kwargs):
        """
        DELETE /posts/{share_id}/repost/
        Delete a repost.
        """
        original_post = self.get_object()
        from posts.services.repost_service import delete_repost as svc_delete_repost
        deleted = svc_delete_repost(request.user, original_post)
        return Response({
            'status': 'success',
            'is_reposted': False,
            'repost_count': original_post.repost_count,
            'share_id': str(original_post.share_id),
            'post_id': original_post.id,
        }, status=status.HTTP_200_OK if deleted else status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'], url_path='reposts')
    def reposts(self, request, *args, **kwargs):
        """
        GET /posts/{id}/reposts/
        List all reposts of a post.
        """
        post = self.get_object()
        reposts = get_post_reposts(post)
        serializer = RepostSerializer(reposts, many=True, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='hide')
    def hide(self, request, *args, **kwargs):
        """
        POST /posts/{id}/hide/
        Hide a post from feed.
        """
        post = self.get_object()
        try:
            hidden_post = hide_post(request.user, post)
            if request.headers.get('HX-Request'):
                return HttpResponse('', status=status.HTTP_200_OK)
            return Response(
                {'detail': 'Post hidden successfully.'},
                status=status.HTTP_201_CREATED
            )
        except Exception as e:
            return Response(
                {'detail': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['delete'], url_path='hide')
    def unhide(self, request, *args, **kwargs):
        """
        DELETE /posts/{id}/hide/
        Unhide a post.
        """
        post = self.get_object()
        unhidden = unhide_post(request.user, post)
        if unhidden:
            return Response(
                {'detail': 'Post unhidden.'},
                status=status.HTTP_200_OK
            )
        else:
            return Response(
                {'detail': 'Post was not hidden.'},
                status=status.HTTP_404_NOT_FOUND
            )

    @action(detail=True, methods=['post'], url_path='share')
    def share(self, request, *args, **kwargs):
        """
        POST /posts/{id}/share/
        Share a post to multiple users or groups.
        Accepts either direct IDs (shared_to, shared_to_group) or lookups (shared_to_usernames, shared_to_group_ids).
        For bulk sharing, use comma-separated values in shared_to_usernames or shared_to_group_ids.
        """
        post = self.get_object()
        share_data = request.data.copy()
        message = share_data.get('message', '')

        # Handle bulk user sharing
        if 'shared_to_usernames' in share_data:
            usernames_str = share_data['shared_to_usernames']
            usernames = [u.strip() for u in str(usernames_str).split(',') if u.strip()]

            shared_posts = []
            errors = []

            for username in usernames:
                try:
                    user = User.objects.get(username=username)
                    shared_post = share_post(request.user, post, shared_to_user=user, message=message)
                    shared_posts.append(shared_post)
                except User.DoesNotExist:
                    errors.append(f'User {username} not found')
                except Exception as e:
                    errors.append(f'Error sharing to {username}: {str(e)}')

            if shared_posts:
                return Response({
                    'detail': f'Shared to {len(shared_posts)} user(s) successfully.',
                    'shared_posts': SharedPostSerializer(shared_posts, many=True, context={'request': request}).data,
                    'errors': errors if errors else None
                }, status=status.HTTP_201_CREATED)
            else:
                return Response(
                    {'detail': 'No successful shares.', 'errors': errors},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Handle bulk group sharing
        elif 'shared_to_group_ids' in share_data:
            group_ids_str = share_data['shared_to_group_ids']
            group_ids = [g.strip() for g in str(group_ids_str).split(',') if g.strip()]

            shared_posts = []
            errors = []

            from groups.models import Group
            for group_id in group_ids:
                try:
                    group = Group.objects.get(id=group_id)
                    shared_post = share_post(request.user, post, shared_to_group=group, message=message)
                    shared_posts.append(shared_post)
                except Group.DoesNotExist:
                    errors.append(f'Group {group_id} not found')
                except Exception as e:
                    errors.append(f'Error sharing to group {group_id}: {str(e)}')

            if shared_posts:
                return Response({
                    'detail': f'Shared to {len(shared_posts)} group(s) successfully.',
                    'shared_posts': SharedPostSerializer(shared_posts, many=True, context={'request': request}).data,
                    'errors': errors if errors else None
                }, status=status.HTTP_201_CREATED)
            else:
                return Response(
                    {'detail': 'No successful shares.', 'errors': errors},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Handle single user/group sharing (backward compatibility)
        else:
            # If username is provided, look up the user ID
            if 'shared_to_username' in share_data and not share_data.get('shared_to'):
                try:
                    user = User.objects.get(username=share_data['shared_to_username'])
                    share_data['shared_to'] = user.id
                    del share_data['shared_to_username']
                except User.DoesNotExist:
                    return Response(
                        {'detail': 'User not found.'},
                        status=status.HTTP_404_NOT_FOUND
                    )

            # If group_id is provided, look up the group
            if 'shared_to_group_id' in share_data and not share_data.get('shared_to_group'):
                from groups.models import Group
                try:
                    group = Group.objects.get(id=share_data['shared_to_group_id'])
                    share_data['shared_to_group'] = group.id
                    del share_data['shared_to_group_id']
                except Group.DoesNotExist:
                    return Response(
                        {'detail': 'Group not found.'},
                        status=status.HTTP_404_NOT_FOUND
                    )

            serializer = SharedPostCreateSerializer(
                data=share_data,
                context={'request': request}
            )
            serializer.is_valid(raise_exception=True)
            serializer.save(original_post=post)

            return Response(
                SharedPostSerializer(serializer.instance, context={'request': request}).data,
                status=status.HTTP_201_CREATED
            )


class CommentViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing comments.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = CommentSerializer

    def get_queryset(self):
        # Only filter for top-level comments on list actions
        if self.action == 'list':
            return Comment.objects.select_related('author', 'post').filter(parent_comment__isnull=True)
        # For detail actions (retrieve, update, delete, custom actions), include all comments
        return Comment.objects.select_related('author', 'post')

    def get_serializer_class(self):
        if self.action == 'create':
            return CommentCreateSerializer
        return CommentSerializer

    def get_permissions(self):
        from posts.permissions import CanDeleteComment, CanEditComment
        if self.action in ['destroy']:
            return [IsAuthenticated(), CanDeleteComment()]
        elif self.action in ['update', 'partial_update']:
            return [IsAuthenticated(), CanEditComment()]
        return super().get_permissions()

    def perform_destroy(self, instance):
        post = instance.post
        parent = instance.parent_comment
        instance.delete()
        if parent:
            parent.reply_count = parent.replies.count()
            parent.save(update_fields=['reply_count'])

        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            "feed_updates",
            {
                'type': 'post_comment_update',
                'post_id': post.id,
                'comment_count': post.comments.count()
            }
        )

    def perform_create(self, serializer):
        comment = serializer.save(author=self.request.user)

        # Broadcast new comment via WebSocket for real-time updates
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync

        channel_layer = get_channel_layer()

        # Broadcast to post author's feed
        if comment.post.author.id != self.request.user.id:
            async_to_sync(channel_layer.group_send)(
                f"feed_{comment.post.author.id}",
                {
                    'type': 'feed_update',
                    'post': {
                        'id': comment.post.id,
                        'comment': {
                            'id': comment.id,
                            'content': comment.content,
                            'author': {
                                'id': comment.author.id,
                                'username': comment.author.username,
                                'profile_pic': comment.author.profile_pic.url if comment.author.profile_pic else None
                            },
                            'created_at': comment.created_at.isoformat()
                        }
                    }
                }
            )

    @action(detail=True, methods=['post'], url_path='reply')
    def reply(self, request, pk=None):
        """
        POST /comments/{id}/reply/
        Create a reply to a comment.
        """
        parent_comment = self.get_object()
        content = request.data.get('content', '').strip()
        attachment_type = request.data.get('attachment_type', Comment.ATTACHMENT_NONE)
        attachment_url = request.data.get('attachment_url', '')
        attachment_image = request.FILES.get('attachment_image') or request.FILES.get('image')
        attachment_meta = request.data.get('attachment_meta', {})
        if isinstance(attachment_meta, str):
            import json
            try:
                attachment_meta = json.loads(attachment_meta)
            except Exception:
                attachment_meta = {}

        has_attachment = attachment_type != Comment.ATTACHMENT_NONE and (bool(attachment_image) or bool(attachment_url))
        if not content and not has_attachment:
            return Response(
                {'detail': 'Reply content or attachment is required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if user can comment on the post (group membership check)
        if parent_comment.post.group:
            from groups.models import Membership, MembershipStatus
            try:
                Membership.objects.get(
                    user=request.user,
                    group=parent_comment.post.group,
                    status=MembershipStatus.APPROVED
                )
            except Membership.DoesNotExist:
                return Response(
                    {'detail': 'You must be an approved member to comment on posts in this group.'},
                    status=status.HTTP_403_FORBIDDEN
                )

        # Create the reply using the service
        from posts.services.comment_service import add_comment_to_post
        reply = add_comment_to_post(
            parent_comment.post,
            request.user,
            content,
            parent_comment=parent_comment,
            attachment_type=attachment_type,
            attachment_image=attachment_image,
            attachment_url=attachment_url,
            attachment_meta=attachment_meta
        )

        serializer = CommentSerializer(reply, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='like')
    def like(self, request, pk=None):
        """
        POST /comments/{id}/like/
        Toggle like on a comment.
        """
        comment = self.get_object()
        from posts.services.comment_service import toggle_comment_like_for_user
        result = toggle_comment_like_for_user(comment, request.user)

        if result['comment'].is_liked_by(request.user):
            return Response(
                {'detail': 'Comment liked.', 'likes_count': comment.likes.count()},
                status=status.HTTP_200_OK
            )
        else:
            return Response(
                {'detail': 'Comment unliked.', 'likes_count': comment.likes.count()},
                status=status.HTTP_200_OK
            )

    @action(detail=True, methods=['get'], url_path='replies')
    def replies(self, request, pk=None):
        """
        GET /comments/{id}/replies/
        List direct replies to a comment.
        """
        parent_comment = self.get_object()
        replies = parent_comment.replies.select_related('author').order_by('created_at')

        # Pagination
        page = self.paginate_queryset(replies)
        if page is not None:
            serializer = CommentSerializer(page, many=True, context={'request': request})
            return self.get_paginated_response(serializer.data)

        serializer = CommentSerializer(replies, many=True, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class ReportViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing reports.
    """
    permission_classes = [IsAuthenticated]
    queryset = Report.objects.select_related('reporter', 'post').all()

    def get_serializer_class(self):
        if self.action == 'create':
            return ReportCreateSerializer
        return ReportSerializer

    def get_queryset(self):
        # Only allow admins to see reports
        user = self.request.user
        if user.is_staff or user.is_superuser:
            return self.queryset
        from groups.models import Membership, MembershipRole, MembershipStatus

        # Get all groups where user is admin or moderator
        admin_groups = Membership.objects.filter(
            user=user,
            role__in=[MembershipRole.ADMIN, MembershipRole.MODERATOR],
            status=MembershipStatus.APPROVED
        ).values_list('group_id', flat=True)

        # Filter reports for posts in those groups
        return self.queryset.filter(post__group_id__in=admin_groups)

    def perform_create(self, serializer):
        # Save the report
        report = serializer.save(reporter=self.request.user)

        # Send notification to group admins/moderators
        if report.post.group:
            from groups.models import Membership, MembershipRole, MembershipStatus
            from notifications.events import publish_event, EventTypes, EventSources, EventActions

            # Get all admins and moderators of the group
            officials = Membership.objects.filter(
                group=report.post.group,
                role__in=[MembershipRole.ADMIN, MembershipRole.MODERATOR],
                status=MembershipStatus.APPROVED
            ).exclude(user=report.reporter)

            # Get thumbnail URL for notification preview
            thumbnail_url = None
            if report.post:
                if report.post.thumbnail:
                    thumbnail_url = report.post.thumbnail.url
                elif report.post.images.exists():
                    first_img = report.post.images.first()
                    thumbnail_url = getattr(first_img, 'get_thumbnail_url', lambda s: None)('400') or (first_img.image.url if getattr(first_img, 'image', None) else None)
                elif report.post.video_poster:
                    thumbnail_url = report.post.video_poster.url

            # Emit event for each official - the rules engine will create notifications
            for membership in officials:
                publish_event(
                    event_type=EventTypes.POSTS_POST_REPORTED.value,
                    source=EventSources.POSTS.value,
                    action=EventActions.REPORTED.value,
                    actor=report.reporter,
                    target_type='Post',
                    target_id=str(report.post.id),
                    context_type='GROUP',
                    context_id=str(report.post.group.id),
                    audience=str(membership.user.id),
                    metadata={
                        'report_reason': report.reason,
                        'reporter_username': report.reporter.username,
                        'post_content': report.post.content[:100] if report.post.content else '',
                        'thumbnail_url': thumbnail_url,
                        'resource_type': 'POST',
                    }
                )


class AuthorPreferenceViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing author preferences.
    """
    permission_classes = [IsAuthenticated]
    queryset = AuthorPreference.objects.select_related('user', 'author').all()
    serializer_class = AuthorPreferenceSerializer

    def get_serializer_class(self):
        if self.action == 'create':
            return AuthorPreferenceCreateSerializer
        return AuthorPreferenceSerializer

    def get_queryset(self):
        # Only show current user's preferences
        return self.queryset.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class SharedPostViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing shared posts.
    """
    permission_classes = [IsAuthenticated]
    queryset = SharedPost.objects.select_related('original_post', 'sharer', 'shared_to').all()
    serializer_class = SharedPostSerializer

    def get_queryset(self):
        # Only show posts shared to current user
        return self.queryset.filter(shared_to=self.request.user)

    @action(detail=True, methods=['post'], url_path='view')
    def mark_viewed(self, request, pk=None):
        """
        POST /shared-posts/{id}/view/
        Mark a shared post as viewed.
        """
        shared_post = self.get_object()
        marked = mark_share_as_viewed(shared_post.id)
        if marked:
            return Response(
                {'detail': 'Shared post marked as viewed.'},
                status=status.HTTP_200_OK
            )
        else:
            return Response(
                {'detail': 'Shared post not found.'},
                status=status.HTTP_404_NOT_FOUND
            )


# ============================================================================
# DJANGO WEB VIEWS
# ============================================================================

def home_view(request):
    # If unauthenticated user arrives with a shared reel link, redirect seamlessly to guest reel viewer
    if not request.user.is_authenticated:
        target_reel_id = request.GET.get('reel')
        if target_reel_id:
            try:
                import uuid
                valid_uuid = str(uuid.UUID(str(target_reel_id).strip()))
                redirect_url = reverse('posts:post_details', args=[valid_uuid]) + "?reel=1"
                shared_by = request.GET.get('shared_by', '')
                if shared_by:
                    redirect_url += f"&shared_by={shared_by}"
                return redirect(redirect_url)
            except (ValueError, AttributeError):
                pass
        return redirect(f"{reverse('login_direct')}?{urlencode({'next': request.get_full_path()})}")

    # Enforce onboarding before landing on feed
    if not getattr(request.user, 'has_completed_onboarding', True):
        if request.headers.get('HX-Request'):
            return htmx_location_response(reverse('users:onboarding'))
        return redirect('users:onboarding')

    cursor = request.GET.get('cursor')
    context = build_home_feed_context(request.user, cursor=cursor)

    # If arrived via shared reel link, ensure target reel is in posts and set auto launch
    target_reel_id = request.GET.get('reel')
    shared_by = request.GET.get('shared_by', '')
    if target_reel_id:
        target_reel = Post.objects.filter(share_id=target_reel_id).first()
        if target_reel:
            existing_ids = {p.id for p in context['posts']}
            if target_reel.id not in existing_ids:
                context['posts'] = [target_reel] + list(context['posts'])
                # The injected reel was not part of the slice used to compute
                # liked_post_ids — augment the set or check_is_liked will
                # trust the (incomplete) set and render it as un-liked.
                if request.user.is_authenticated and target_reel.is_liked_by(request.user):
                    liked_ids = context.setdefault('liked_post_ids', set())
                    liked_ids.add(target_reel.id)
                    liked_ids.add(target_reel.share_id)
                    liked_ids.add(str(target_reel.share_id))
            context['auto_launch_reel_id'] = str(target_reel.share_id)
            context['shared_by'] = shared_by

    # Add explore_groups for initial page load and HTMX navigation (no cursor)
    # Exclude for infinite scroll (has cursor) and search (has query)
    if cursor is None and not request.GET.get('q'):
        from recommendations.services.engine import UnifiedRecommendationEngine
        explore_groups = UnifiedRecommendationEngine.get_recommended_groups(
            request.user, limit=8, context='explore'
        )
        context['explore_groups'] = explore_groups

    # If arriving via HTMX from Pwanimate, enforce full-page browser navigation
    current_url = request.headers.get('HX-Current-URL', '')
    if request.headers.get('HX-Request') and current_url and '/pwanimate' in current_url:
        response = HttpResponse()
        response['HX-Redirect'] = reverse('posts:home')
        return response

    # HTMX Navigation Request: Return full navigation partial for page navigation
    # Distinguished from infinite scroll (has cursor) and search (has query)
    if request.headers.get('HX-Request') and not cursor and not request.GET.get('q'):
        return render(request, 'posts/partials/home_navigation_partial.html', context)

    # For HTMX infinite scroll: render only the posts partial
    if request.headers.get('HX-Request') and cursor:
        return render(request, 'posts/partials/post_list.html', context)

    # If HTMX requests the home feed (e.g. when clearing search), return the inner content
    if request.headers.get('HX-Request'):
        return render(request, 'posts/partials/home_content.html', context)

    context['unread_notifications_count'] = get_cached_unread_count(request.user)
    return render(request, 'posts/home.html', context)


@login_required
def create_post_view(request):
    import logging
    logger = logging.getLogger(__name__)

    is_htmx = bool(request.headers.get('HX-Request'))

    # Get user's groups for the dropdown
    from groups.models import Group, Membership, MembershipStatus
    user_groups = Group.objects.filter(
        memberships__user=request.user,
        memberships__status=MembershipStatus.APPROVED
    ).distinct()

    selected_group_id = request.GET.get('group') or ''
    selected_group = None
    if selected_group_id:
        try:
            selected_group = user_groups.filter(id=int(selected_group_id)).first()
        except (ValueError, TypeError):
            pass

    if request.method == 'POST':
        logger.info(f"POST request received. FILES keys: {list(request.FILES.keys())}")
        logger.info(f"POST data keys: {list(request.POST.keys())}")

        form = PostForm(request.POST, request.FILES, user=request.user)
        logger.info(f"Form is valid: {form.is_valid()}")
        if not form.is_valid():
            logger.error(f"Form errors: {form.errors}")

        if form.is_valid():
            logger.info(f"Calling create_post_for_user with FILES: {request.FILES}")
            # Get group from POST data (hidden input)
            group_id = request.POST.get('group') or selected_group_id
            post = create_post_for_user(form, request.user, request.FILES, group_id=group_id)
            logger.info(f"Post created with ID: {post.id}")
            logger.info(f"Post group: {post.group}")
            logger.info(f"Post images count: {post.images.count()}")
            logger.info(f"Post video: {post.video}")
            logger.info(f"Post docs: {post.docs}")
            logger.info(f"Post audio: {post.audio}")
            messages.success(request, 'Post created successfully.')
            # Mark this as a new post in session for back button logic
            request.session['is_new_post'] = True
            request.session['new_share_id'] = str(post.share_id)
            if is_htmx:
                return htmx_location_response(reverse('posts:post_details', kwargs={'share_id': post.share_id}))
            return redirect('posts:post_details', share_id=post.share_id)
    else:
        form = PostForm(user=request.user)

    template = 'posts/partials/create_post_navigation_partial.html' if is_htmx else 'posts/create_post.html'
    return render(request, template, {
        'form': form,
        'user_groups': user_groups,
        'selected_group': selected_group,
        'selected_group_id': str(selected_group.id) if selected_group else selected_group_id,
    })


def storage_manager_view(request):
    """View for storage manager - redirects to unified offline media library"""
    if request.headers.get('HX-Request'):
        return render(request, 'posts/partials/offline_media_viewer_content.html')
    return redirect('posts:offline_media_viewer')


def offline_media_viewer_view(request):
    """View for the offline media viewer page - accessible offline without requiring active session"""
    template = 'posts/partials/offline_media_viewer_content.html' if request.headers.get('HX-Request') else 'posts/offline_media_viewer.html'
    return render(request, template)


from urllib.parse import urlparse

def _is_safe_previous_page(url_str, request_host, share_id):
    """Validate that previous_page is an internal HTML page and not a script/worker/API."""
    if not url_str or not isinstance(url_str, str):
        return False
    url_str = url_str.strip()
    if not url_str:
        return False

    # Block foreign schemes
    if url_str.startswith(('javascript:', 'data:', 'android-app:', 'mailto:', 'tel:')):
        return False

    try:
        parsed = urlparse(url_str)
    except Exception:
        return False

    # If host is specified, ensure it matches current host
    if parsed.netloc and parsed.netloc.lower() != request_host.lower():
        return False

    path = parsed.path.lower()

    # Block non-HTML resources, service workers, manifests, APIs
    blocked_keywords = [
        'service-worker', 'manifest', 'offline', '/api/', '/static/', '/media/',
        '/ws/', 'clear-cache', 'download/app', '/apk'
    ]
    if any(k in path for k in blocked_keywords):
        return False

    # Block static file extensions
    blocked_extensions = ('.js', '.css', '.json', '.xml', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.map', '.apk')
    if path.endswith(blocked_extensions):
        return False

    # Do not loop back to the current post detail page
    if str(share_id) in path:
        return False

    return True


@ensure_csrf_cookie
def post_detail_view(request, share_id):
    post = get_object_or_404(visible_posts_for(request.user), share_id=share_id)
    shared_by = request.GET.get('shared_by', '')
    show_all = request.GET.get('show_all') == '1'
    is_reel_requested = (post.is_reel or request.GET.get('reel') == '1') and not show_all

    # If post is a reel, opening from external platform should open in fullscreen reel player on exact reel
    if is_reel_requested:
        if request.user.is_authenticated:
            target_url = reverse('posts:home') + f"?reel={post.share_id}"
            if shared_by:
                target_url += f"&shared_by={shared_by}"
            if request.headers.get('HX-Request'):
                return htmx_location_response(target_url)
            return redirect(target_url)

    is_guest = not request.user.is_authenticated
    page = request.GET.get('page', 1)
    context = build_comments_context(post, request.user, show_all_comments=show_all, page=page)
    # Multi-image posts open as a feed of image cards. Preserve the image tapped
    # in the collage so the detail feed can position it as the starting card.
    try:
        context['selected_image_index'] = max(0, int(request.GET.get('image', 0)))
    except (TypeError, ValueError):
        context['selected_image_index'] = 0
    context['post_images'] = list(post.images.all())
    if context['post_images']:
        context['selected_image_index'] = min(
            context['selected_image_index'], len(context['post_images']) - 1
        )
    context['is_multi_image_post'] = len(context['post_images']) > 1
    for post_image in context['post_images']:
        post_image.viewer_has_liked = (
            request.user.is_authenticated and post_image.likes.filter(user=request.user).exists()
        )
    context['is_guest'] = is_guest
    context['is_liked'] = post.is_liked_by(request.user) if not is_guest else False
    context['unread_notifications_count'] = get_cached_unread_count(request.user) if not is_guest else 0
    if is_reel_requested:
        context['auto_launch_reel_id'] = str(post.share_id)
        context['shared_by'] = shared_by

    # Track previous page for back button logic
    if is_guest:
        context['is_new_post'] = False
        context['previous_page'] = '#'
    else:
        is_new_post = request.session.pop('is_new_post', False)
        new_share_id = request.session.pop('new_share_id', None)

        request_host = request.get_host()
        default_home = reverse('posts:home')

        # If not a new post, save the referring URL for back button IF safe
        if not is_new_post and request.META.get('HTTP_REFERER'):
            referer = request.META.get('HTTP_REFERER')
            if _is_safe_previous_page(referer, request_host, share_id):
                request.session['previous_page'] = referer
            else:
                request.session['previous_page'] = default_home

        context['is_new_post'] = is_new_post

        # Ensure previous_page is safe (clears any legacy corrupted session values)
        candidate_prev = request.session.get('previous_page', default_home)
        if not _is_safe_previous_page(candidate_prev, request_host, share_id):
            candidate_prev = default_home
            request.session['previous_page'] = default_home

        context['previous_page'] = candidate_prev

    # Return comments section whenever show_all=1 is requested
    if show_all:
        if request.GET.get('chunk_only') == '1':
            return render(request, 'posts/partials/comments_chunk.html', context)
        return render(request, 'posts/partials/comments_section.html', context)

    # Open Graph preview: thumbnail straight from the DB row (post images,
    # video poster, gradient/text thumbnail). Media URLs are public CDN links
    # so preview fetchers need no login; the page itself stays gated.
    og_image = ''
    try:
        if context.get('post_images'):
            og_image = context['post_images'][0].image.url or ''
        if not og_image:
            poster = post.get_video_poster or ''
            # A bare video URL (with '#poster' marker, no poster file) is not
            # a loadable image — skip it so crawlers don't choke.
            if poster and not poster.endswith('#poster'):
                og_image = poster
        if not og_image and getattr(post, 'thumbnail', None):
            og_image = post.thumbnail.url or ''
    except Exception:
        og_image = ''
    if og_image:
        og_image = request.build_absolute_uri(og_image)
    author_name = getattr(post.author, 'username', 'vasto')
    post_text = (post.content or '').strip()
    context['og_title'] = f"Post by @{author_name} on Vasto"
    context['og_description'] = (post_text[:200] + '…') if len(post_text) > 200 else (post_text or 'See this post on Vasto.')
    context['og_image'] = og_image

    # For HTMX page navigation requests, return navigation partial
    if request.headers.get('HX-Request'):
        return render(request, 'posts/partials/post_detail_navigation_partial.html', context)

    return render(request, 'posts/post_detail.html', context)


@login_required
def add_comment(request, share_id):
    post = get_object_or_404(visible_posts_for(request.user), share_id=share_id)
    if request.method == 'POST':
        comment = handle_add_comment_request(request, post)
        if request.headers.get('HX-Request'):
            if request.GET.get('from_card'):
                return render(request, 'posts/partials/card_comment_response.html', {
                    'post': post,
                    'comment': comment,
                    'comment_count': post.comments.count(),
                })
            context = build_comments_context(post, request.user, show_all_comments=True)
            context['post'] = post
            return render(request, 'posts/partials/comments_section.html', context)
    return redirect('posts:post_details', share_id=share_id)


@login_required
def toggle_comment_like(request, comment_id):
    comment = get_object_or_404(Comment, id=comment_id)
    result = toggle_comment_like_for_user(comment, request.user)
    liked_comment_ids = result.get('liked_comment_ids', set())
    return render(request, 'posts/partials/comment_item.html', {
        'comment': comment,
        'liked_comment_ids': liked_comment_ids,
    })


@login_required
def toggle_like(request, share_id):
    post = get_object_or_404(visible_posts_for(request.user), share_id=share_id)
    result = toggle_post_like_for_user(post, request.user)
    is_reel = request.GET.get('is_reel') == '1' or request.POST.get('is_reel') == '1'

    if request.headers.get('Accept') == 'application/json' or request.GET.get('format') == 'json' or request.POST.get('format') == 'json':
        from django.http import JsonResponse
        canonical = result['post']
        return JsonResponse({
            'status': 'success',
            'is_liked': result['is_liked'],
            'like_count': result['like_count'],
            'share_id': str(canonical.share_id),
            'post_id': canonical.id
        })

    return render(request, 'posts/partials/like_button.html', {
        'post': result['post'],
        'is_liked': result['is_liked'],
        'like_count': result['like_count'],
        'is_reel': is_reel,
    })


@login_required
def post_likers_list(request, share_id):
    from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
    post = get_object_or_404(visible_posts_for(request.user), share_id=share_id)
    likers = User.objects.filter(id__in=post.likes.values_list("user_id", flat=True)).order_by('username')

    page = request.GET.get('page', 1)
    paginator = Paginator(likers, 20)  # Show 20 likers per page

    try:
        likers_page = paginator.page(page)
    except PageNotAnInteger:
        likers_page = paginator.page(1)
    except EmptyPage:
        likers_page = paginator.page(paginator.num_pages)

    return render(request, 'posts/partials/likers_modal_content.html', {
        'likers': likers_page,
        'page_obj': likers_page,
        'paginator': paginator,
        'post': post
    })


@login_required
def unit_posts_view(request, unit_id):
    unit = get_object_or_404(Unit, id=unit_id)
    posts = visible_posts_for(request.user, Post.objects.filter(unit=unit)).select_related('author', 'unit').order_by('-created_at')
    liked_post_ids = set()
    if request.user.is_authenticated:
        for pid, sid in Like.objects.filter(user=request.user, post__in=posts).values_list('post_id', 'post__share_id'):
            liked_post_ids.add(pid)
            liked_post_ids.add(sid)
            liked_post_ids.add(str(sid))
    context = {
        'unit': unit,
        'posts': posts,
        'liked_post_ids': liked_post_ids,
        'unread_notifications_count': get_cached_unread_count(request.user),
    }
    if request.headers.get('HX-Request'):
        return render(request, 'courses/partials/unit_detail_navigation_partial.html', context)
    return render(request, 'courses/unit_detail.html', context)


@login_required
def search_view(request):
    from posts.queries.search_queries import search_users, search_groups, get_user_groups, get_following_ids
    query = request.GET.get('q', '')

    users = search_users(query, request.user)
    groups = search_groups(query)
    user_groups = get_user_groups(request.user)
    following_ids = list(get_following_ids(request.user))

    context = {
        'query': query,
        'users': users,
        'groups': groups,
        'user_groups': user_groups,
        'following_ids': following_ids,
        'unread_notifications_count': get_cached_unread_count(request.user),
    }

    if request.headers.get('HX-Target') == 'page-content-target':
        return render(request, 'posts/partials/search_navigation_partial.html', context)

    if request.headers.get('HX-Request'):
        return render(request, 'posts/partials/search_results_inner.html', context)

    return render(request, 'posts/search_results.html', context)


@login_required
def view_image_fullscreen(request, share_id, image_index):
    post = get_object_or_404(visible_posts_for(request.user), share_id=share_id)
    all_images = list(post.images.all())

    # Handle both index and ID (for backward compatibility with existing links)
    if image_index < len(all_images):
        current_index = image_index
        post_image = all_images[current_index]
    else:
        # If image_index is actually an ID (from old links), find by ID
        post_image = get_object_or_404(post.images, id=image_index)
        current_index = all_images.index(post_image)

    # Get likes for the individual image
    from .models import PostImageLike
    image_likes = post_image.likes.select_related('user').all()
    liked_users = [like.user for like in image_likes]

    # Check if current user liked the image
    is_liked = post_image.likes.filter(user=request.user).exists()

    # Get users that the current user follows
    from users.models import Follow
    following_ids = set(Follow.objects.filter(follower=request.user).values_list('followed_id', flat=True))

    # Separate likes into followed users and others
    followed_likers = [user for user in liked_users if user.id in following_ids]
    other_likers = [user for user in liked_users if user.id not in following_ids]

    context = {
        'post': post,
        'post_image': post_image,
        'all_images': all_images,
        'current_index': current_index,
        'has_prev': current_index > 0,
        'has_next': current_index < len(all_images) - 1,
        'prev_index': current_index - 1 if current_index > 0 else None,
        'next_index': current_index + 1 if current_index < len(all_images) - 1 else None,
        'followed_likers': followed_likers,
        'other_likers_count': len(other_likers),
        'total_likes': len(liked_users),
        'is_liked': is_liked,
        'current_user_profile_pic': request.user.profile_pic.url if request.user.profile_pic else None,
    }

    if request.headers.get('HX-Request'):
        return render(request, 'posts/partials/image_fullscreen_navigation_partial.html', context)
    return render(request, 'posts/image_fullscreen.html', context)


@login_required
def share_post_view(request, share_id):
    """Django view to handle post sharing to multiple users or groups"""
    if request.method == 'POST':
        post = get_object_or_404(visible_posts_for(request.user), share_id=share_id)
        share_type = request.POST.get('share_type')  # 'user' or 'group'
        message = request.POST.get('message', '')

        # Debug: Log the POST data
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Share request - share_type: {share_type}, POST data: {dict(request.POST)}")

        try:
            if share_type == 'user':
                # Handle multiple users (comma-separated)
                usernames_str = request.POST.get('shared_to_usernames', '')
                logger.info(f"Usernames string: {usernames_str}")
                usernames = [u.strip() for u in usernames_str.split(',') if u.strip()]
                logger.info(f"Parsed usernames: {usernames}")

                shared_count = 0
                for username in usernames:
                    try:
                        shared_to_user = User.objects.get(username=username)
                        share_post(request.user, post, shared_to_user=shared_to_user, message=message)
                        shared_count += 1
                    except User.DoesNotExist:
                        continue  # Skip invalid usernames

                is_json = request.headers.get('Accept') == 'application/json' or request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.content_type == 'application/json'
                if shared_count > 0:
                    if is_json:
                        return JsonResponse({'status': 'success', 'message': f'Post shared to {shared_count} user(s) successfully.'})
                    if request.headers.get('HX-Request'):
                        return HttpResponse(
                            f'<div class="alert alert-success">Post shared to {shared_count} user(s) successfully.</div>'
                        )
                    messages.success(request, f'Post shared to {shared_count} user(s) successfully.')
                else:
                    if is_json:
                        return JsonResponse({'status': 'error', 'message': 'No valid users found.'}, status=400)
                    if request.headers.get('HX-Request'):
                        return HttpResponse(
                            '<div class="alert alert-danger">No valid users found.</div>'
                        )
                    messages.error(request, 'No valid users found.')

            elif share_type == 'group':
                # Handle multiple groups (comma-separated)
                group_ids_str = request.POST.get('shared_to_group_ids', '')
                group_ids = [int(g.strip()) for g in group_ids_str.split(',') if g.strip()]

                shared_count = 0
                errors = []
                from groups.models import Group
                for group_id in group_ids:
                    try:
                        shared_to_group = Group.objects.get(id=group_id)
                        share_post(request.user, post, shared_to_group=shared_to_group, message=message)
                        shared_count += 1
                    except (Group.DoesNotExist, ValueError) as e:
                        errors.append(str(e))
                        continue  # Skip invalid group IDs
                    except ValidationError as e:
                        errors.append(str(e))
                        continue  # Skip groups that can't be shared to

                is_json = request.headers.get('Accept') == 'application/json' or request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.content_type == 'application/json'
                if shared_count > 0:
                    if is_json:
                        return JsonResponse({'status': 'success', 'message': f'Post shared to {shared_count} group(s) successfully.'})
                    if request.headers.get('HX-Request'):
                        return HttpResponse(
                            f'<div class="alert alert-success">Post shared to {shared_count} group(s) successfully.</div>'
                        )
                    messages.success(request, f'Post shared to {shared_count} group(s) successfully.')
                else:
                    error_msg = errors[0] if errors else 'No valid groups found.'
                    if is_json:
                        return JsonResponse({'status': 'error', 'message': error_msg}, status=400)
                    if request.headers.get('HX-Request'):
                        return HttpResponse(
                            f'<div class="alert alert-danger">{error_msg}</div>'
                        )
                    messages.error(request, error_msg)
            else:
                is_json = request.headers.get('Accept') == 'application/json' or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                if is_json:
                    return JsonResponse({'status': 'error', 'message': f'Invalid share type: {share_type}'}, status=400)
                if request.headers.get('HX-Request'):
                    return HttpResponse(
                        f'<div class="alert alert-danger">Invalid share type: {share_type}</div>'
                    )
                messages.error(request, 'Invalid share type.')
        except Exception as e:
            import traceback
            logger.error(f"Share error: {str(e)}\n{traceback.format_exc()}")
            is_json = request.headers.get('Accept') == 'application/json' or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            if is_json:
                return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
            if request.headers.get('HX-Request'):
                return HttpResponse(
                    f'<div class="alert alert-danger">Error: {str(e)}</div>'
                )
            messages.error(request, str(e))

    # Only redirect if not an HTMX request
    if not request.headers.get('HX-Request'):
        return redirect('posts:post_details', share_share_id=share_id)
    return HttpResponse('')


@login_required
def share_app_to_users_view(request):
    """
    Handle sharing the mobile app referral link directly to friends on vasto.
    Creates SharedPost entries of type 'app_link' which display on the recipient's
    profile under the 'Shared with me' tab as a clickable download card.
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)

    usernames_str = request.POST.get('shared_to_usernames', '')
    usernames = [u.strip() for u in usernames_str.split(',') if u.strip()]
    message = request.POST.get('message', '').strip()

    if not usernames:
        return JsonResponse({'status': 'error', 'message': 'No recipient users specified.'}, status=400)

    try:
        from users.services.invite_service import get_or_create_invite, InviteType
        invite = get_or_create_invite(request.user, InviteType.APP_DOWNLOAD)
        app_url = request.build_absolute_uri(reverse('encrypted_invite_landing', kwargs={'token': invite.token}))
    except Exception as e:
        logger.error(f"Error resolving invite link for app share: {e}")
        app_url = request.build_absolute_uri(reverse('downloads'))

    shared_count = 0
    from users.models import User
    from posts.models import SharedPost

    for username in usernames:
        try:
            recipient = User.objects.get(username=username)
            if recipient == request.user:
                continue

            existing = SharedPost.objects.filter(
                sharer=request.user,
                shared_to=recipient,
                share_type='app_link',
                shared_link=app_url
            ).first()

            if not existing:
                shared_post = SharedPost.objects.create(
                    sharer=request.user,
                    shared_to=recipient,
                    share_type='app_link',
                    shared_link=app_url,
                    shared_link_title='vasto Mobile App (Android APK)',
                    shared_link_description='Download the official vasto Android app for campus updates, course groups, and offline access.',
                    message=message or 'Check out the vasto Android app!'
                )
                shared_count += 1

                try:
                    from notifications.events import publish_event, EventTypes, EventSources, EventActions
                    publish_event(
                        event_type=EventTypes.POSTS_POST_SHARED.value,
                        source=EventSources.POSTS.value,
                        action=EventActions.SHARED.value,
                        actor=request.user,
                        target_type='AppDownload',
                        target_id=str(invite.id) if 'invite' in locals() and invite else '0',
                        context_type='APP',
                        context_id=str(invite.id) if 'invite' in locals() and invite else '0',
                        audience=str(recipient.id),
                        metadata={
                            'message': message[:100] if message else 'Shared the vasto Mobile App with you',
                            'sharer_username': request.user.username,
                            'recipient_username': recipient.username,
                            'recipient_id': str(recipient.id),
                            'thumbnail_url': '/static/images/vasto-app-icon.png',
                            'resource_type': 'APP',
                            'post_content': 'Download the official vasto Android app',
                            'actor_username': request.user.username,
                            'download_url': '/download/app/latest/',
                            'shared_post_id': shared_post.id,
                        }
                    )
                except Exception as notify_err:
                    logger.warning(f"Could not emit notification for app share: {notify_err}")
            else:
                shared_count += 1
        except User.DoesNotExist:
            continue
        except Exception as share_err:
            logger.error(f"Error sharing app link to user '{username}': {share_err}")
            continue

    if shared_count > 0:
        return JsonResponse({
            'status': 'success',
            'message': f'Mobile app link shared to {shared_count} friend(s) successfully.'
        })
    return JsonResponse({'status': 'error', 'message': 'No valid users could be shared to.'}, status=400)


@login_required
def shared_posts_view(request):
    """View to show all posts shared to the user (direct shares and group shares)"""
    shared_posts = get_user_received_shares(request.user)

    context = {
        'shared_posts': shared_posts,
        'unread_notifications_count': get_cached_unread_count(request.user),
    }

    if request.headers.get('HX-Request'):
        return render(request, 'posts/partials/shared_posts_navigation_partial.html', context)

    return render(request, 'posts/shared_posts.html', context)


@login_required
def search_following_users(request):
    """HTMX search endpoint for users the current user is following — uses unified search engine"""
    query = request.GET.get('user_search', '') or request.GET.get('q', '')

    try:
        from search.services.unified_search_service import UnifiedSearchService
        service = UnifiedSearchService()
        users_page, _, _, _, _, _ = service.search_people_models(
            query=query,
            user=request.user,
            connection_type='following',
            profile_username=request.user.username,
            page=1,
            page_size=10,
        )
        users = list(users_page)
    except Exception:
        # Fallback to simple filter if unified search is unavailable
        from users.models import Follow
        following_ids = Follow.objects.filter(follower=request.user).values_list('followed_id', flat=True)
        qs = User.objects.filter(id__in=following_ids)
        if query:
            qs = qs.filter(username__icontains=query)
        users = list(qs[:10])

    return render(request, 'posts/partials/share_user_results.html', {'users': users})


@login_required
def search_user_groups(request):
    """HTMX search endpoint for groups the current user is a member of"""
    query = request.GET.get('group_search', '') or request.GET.get('q', '')

    from groups.models import Membership, MembershipStatus, Group
    from django.db.models import Q

    group_ids = Membership.objects.filter(
        user=request.user,
        status=MembershipStatus.APPROVED
    ).values_list('group_id', flat=True)

    groups = Group.objects.filter(id__in=group_ids)

    if query:
        groups = groups.filter(
            Q(name__icontains=query) | Q(description__icontains=query)
        )

    groups = groups.order_by('name')[:15]

    return render(request, 'posts/partials/share_group_results.html', {'groups': groups})


@login_required
def get_share_targets(request):
    """Returns following users and user groups for the TikTok-style share sheet"""
    try:
        from django.db.models import Q
        query = request.GET.get('q', '').strip()

        # 1. Following users (friends)
        from users.models import Follow
        following_ids = list(Follow.objects.filter(follower=request.user).values_list('followed_id', flat=True))

        if query:
            users_qs = User.objects.filter(id__in=following_ids).filter(
                Q(username__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query)
            )
            # If search in followings returns few/no results, search all active users
            if users_qs.count() < 5:
                extra_qs = User.objects.exclude(id=request.user.id).filter(is_active=True).filter(
                    Q(username__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query)
                ).exclude(id__in=following_ids)
                users_qs = list(users_qs) + list(extra_qs[:10])
        elif following_ids:
            users_qs = User.objects.filter(id__in=following_ids)[:20]
        else:
            # Fallback to active users if user is not following anyone yet
            users_qs = User.objects.exclude(id=request.user.id).filter(is_active=True).order_by('-date_joined')[:20]

        users_data = []
        for u in users_qs[:20]:
            try:
                pic_url = u.profile_pic.url if (u.profile_pic and hasattr(u.profile_pic, 'url')) else '/static/images/default_avatar.png'
            except Exception:
                pic_url = '/static/images/default_avatar.png'
            users_data.append({
                'id': u.id,
                'username': u.username,
                'name': u.get_full_name() or u.username,
                'avatar': pic_url,
                'type': 'user',
            })

        # 2. User's approved groups
        from groups.models import Membership, MembershipStatus, Group
        group_ids = list(Membership.objects.filter(
            user=request.user,
            status=MembershipStatus.APPROVED
        ).values_list('group_id', flat=True))

        if query:
            groups_qs = Group.objects.filter(id__in=group_ids).filter(
                Q(name__icontains=query) | Q(description__icontains=query)
            )
            if groups_qs.count() < 5:
                extra_groups = Group.objects.filter(is_official=True).filter(
                    Q(name__icontains=query) | Q(description__icontains=query)
                ).exclude(id__in=group_ids)
                groups_qs = list(groups_qs) + list(extra_groups[:10])
        elif group_ids:
            groups_qs = Group.objects.filter(id__in=group_ids).order_by('name')[:15]
        else:
            # Fallback: public or official groups
            groups_qs = Group.objects.filter(is_official=True).order_by('name')[:15]

        groups_data = []
        for g in groups_qs[:15]:
            try:
                photo_attr = getattr(g, 'get_photo_url', None)
                pic_url = photo_attr() if callable(photo_attr) else photo_attr
                if not pic_url:
                    pic_url = g.group_pic.url if (g.group_pic and hasattr(g.group_pic, 'url')) else '/static/images/default_group.jpg'
            except Exception:
                pic_url = '/static/images/default_group.jpg'

            groups_data.append({
                'id': g.id,
                'name': g.name,
                'avatar': pic_url,
                'is_official': getattr(g, 'is_official', False),
                'type': 'group',
            })

        return JsonResponse({
            'status': 'success',
            'users': users_data,
            'groups': groups_data
        })
    except Exception as e:
        import traceback
        logger.error(f"[get_share_targets] Error: {str(e)}\n{traceback.format_exc()}")
        return JsonResponse({
            'status': 'error',
            'message': str(e),
            'users': [],
            'groups': []
        }, status=500)



@login_required
def task_status_view(request, task_id):
    """API endpoint to check Celery task status for progress tracking"""
    from .tasks import create_post_with_media

    task = AsyncResult(task_id, app=create_post_with_media)

    response_data = {
        'state': task.state,
        'meta': task.info if task.state != 'FAILURE' else {'status': str(task.info)}
    }

    return JsonResponse(response_data)


@api_view(['GET'])
@permission_classes([AllowAny])
def klipy_media_proxy(request):
    """
    Proxy endpoint for KLIPY GIFs and Stickers API.
    Keeps API key secure on the server, adds caching for search/trending queries.
    """
    import urllib.request
    import urllib.error
    import urllib.parse
    import json
    from django.conf import settings
    from django.core.cache import cache

    media_type = request.GET.get('type', 'gifs').lower()
    if media_type not in ('gifs', 'stickers'):
        media_type = 'gifs'

    q = request.GET.get('q', '').strip()
    requested_action = request.GET.get('action', '').strip().lower()
    if requested_action in ('categories', 'trending', 'search'):
        action = requested_action
    else:
        action = 'search' if q else 'trending'

    page = request.GET.get('page', '1')
    per_page = request.GET.get('per_page', '24')

    customer_id = 'anon_guest'
    if hasattr(request, 'user') and request.user.is_authenticated:
        customer_id = f"user_{request.user.id}"
    elif hasattr(request, 'session'):
        if not getattr(request.session, 'session_key', None):
            try:
                request.session.save()
            except Exception:
                pass
        customer_id = f"anon_{getattr(request.session, 'session_key', 'guest') or 'guest'}"

    api_key = getattr(settings, 'KLIPY_API_KEY', '') or 'eTJSGDKLf98x2n9QMKeNQoObF8fxavwQC2wGrfbWyinrYSH8mTtlySyZ7zlPW1Yf'
    if not api_key:
        return JsonResponse({'result': False, 'error': 'KLIPY API key not configured', 'data': {'data': []}})

    # Check cache (normalize key)
    cache_key = f"klipy_{media_type}_{action}_{q}_{page}_{per_page}".replace(' ', '_')
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return JsonResponse(cached_data)

    if action == 'categories':
        query_params = {'customer_id': customer_id}
    else:
        query_params = {
            'page': page,
            'per_page': per_page,
            'customer_id': customer_id,
        }
        if action == 'search' and q:
            query_params['q'] = q

    encoded_params = urllib.parse.urlencode(query_params)
    url = f"{settings.KLIPY_API_BASE_URL}/{api_key}/{media_type}/{action}?{encoded_params}"

    try:
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                'Accept': 'application/json'
            }
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            ttl = 3600 if action == 'categories' else (180 if action == 'search' else 600)
            cache.set(cache_key, data, ttl)
            return JsonResponse(data)
    except urllib.error.HTTPError as he:
        err_body = ''
        try:
            err_body = he.read().decode('utf-8')
        except Exception:
            pass
        logger.warning(f"[KLIPY] HTTPError {he.code} fetching {url}: {err_body or he}")
        resp_data = {'result': False, 'error': f"HTTP {he.code}: {err_body or str(he)}", 'data': {'data': []}}
        cache.set(cache_key, resp_data, 30)
        return JsonResponse(resp_data, status=200)
    except Exception as e:
        logger.warning(f"[KLIPY] Proxy error fetching {url}: {e}")
        resp_data = {'result': False, 'error': str(e), 'data': {'data': []}}
        cache.set(cache_key, resp_data, 30)
        return JsonResponse(resp_data, status=200)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def user_stickers_view(request):
    """
    List user's custom stickers or upload a new sticker.
    """
    from posts.models import UserSticker
    from posts.serializers import UserStickerSerializer
    from PIL import Image
    import io
    import time
    from django.core.files.uploadedfile import InMemoryUploadedFile

    if request.method == 'GET':
        stickers = UserSticker.objects.filter(user=request.user)
        serializer = UserStickerSerializer(stickers, many=True)
        return Response(serializer.data)

    elif request.method == 'POST':
        image_file = request.FILES.get('image') or request.FILES.get('sticker')
        if not image_file:
            return Response({'detail': 'Image file is required.'}, status=status.HTTP_400_BAD_REQUEST)

        # Enforce max 100 stickers per user
        if UserSticker.objects.filter(user=request.user).count() >= 100:
            return Response({'detail': 'Maximum 100 custom stickers allowed.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            img = Image.open(image_file)
            if img.mode not in ('RGBA', 'RGB'):
                img = img.convert('RGBA')

            img.thumbnail((512, 512), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            img.save(output, format='WEBP', quality=85, optimize=True)
            output.seek(0)

            filename = f"sticker_{request.user.id}_{int(time.time())}.webp"
            processed_file = InMemoryUploadedFile(
                output, 'ImageField', filename, 'image/webp', output.getbuffer().nbytes, None
            )

            sticker = UserSticker.objects.create(
                user=request.user,
                image=processed_file,
                name=request.data.get('name', '')
            )
            serializer = UserStickerSerializer(sticker)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({'detail': f'Failed to process sticker image: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_user_sticker_view(request, sticker_id):
    """
    Delete a user's custom sticker.
    """
    from posts.models import UserSticker
    sticker = get_object_or_404(UserSticker, id=sticker_id, user=request.user)
    sticker.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)

