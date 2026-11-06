"""
Web URLs for groups app.
Contains both Django web view routes and API routes.
"""
from django.urls import path
from . import views

urlpatterns = [
    path('dashboard/', views.groups_dashboard, name='groups_dashboard'),
    path('<int:group_id>/', views.groups_detail_view, name='groups_detail'),
    path('<int:group_id>/announcements/', views.group_announcements_view, name='group_announcements'),
    path('<int:group_id>/settings/', views.group_settings_view, name='group_settings'),
    path('<int:group_id>/settings/details/', views.group_settings_details_view, name='group_settings_details'),
    path('<int:group_id>/settings/members/', views.group_settings_members_view, name='group_settings_members'),
    path('<int:group_id>/settings/privacy/', views.group_settings_privacy_view, name='group_settings_privacy'),
    path('<int:group_id>/settings/announcements/', views.group_settings_announcements_view, name='group_settings_announcements'),
    path('<int:group_id>/settings/documents/', views.group_settings_documents_view, name='group_settings_documents'),
    path('<int:group_id>/settings/about/', views.group_settings_about_view, name='group_settings_about'),
    path('<int:group_id>/chat/', views.group_chat_view, name='group_chat'),
    path('create/', views.create_group_view, name='create_group'),
    path('toggle/<int:group_id>/', views.toggle_group_membership, name='toggle_membership'),
    path('<int:group_id>/edit/', views.edit_group, name='edit_group'),
    path('<int:group_id>/invite/<int:user_id>/', views.invite_to_group, name='invite_to_group'),
    path('invite/respond/<uuid:notif_id>/<str:action>/', views.respond_to_invite, name='respond_to_invite'),
    path('<int:group_id>/approve/<int:user_id>/', views.approve_from_notification, name='approve_from_notification'),
    path('<int:group_id>/reject/<int:user_id>/', views.reject_from_notification, name='reject_from_notification'),
    path('api/unread-counts/', views.group_unread_counts_api, name='group_unread_counts_api'),
    path('api/activity-status/', views.group_activity_status_api, name='group_activity_status_api'),
    path('search-users/', views.search_users_view, name='search_users'),
    path('clear-recent-searches/', views.clear_recent_group_searches, name='clear_recent_searches'),
    path('<int:group_id>/photo/<str:photo_type>/', views.view_group_photo_fullscreen, name='view_group_photo_fullscreen'),
    path('<int:group_id>/members/search/', views.group_members_search, name='group_members_search'),
    # API endpoints
    path('api/groups/<int:pk>/join/', views.GroupViewSet.as_view({'post': 'join'}), name='group_join_api'),
    path('api/groups/<int:pk>/approve/<int:user_id>/', views.GroupViewSet.as_view({'post': 'approve'}), name='group_approve_api'),
    path('api/groups/<int:pk>/reject/<int:user_id>/', views.GroupViewSet.as_view({'post': 'reject'}), name='group_reject_api'),
    path('api/groups/<int:pk>/members/', views.GroupViewSet.as_view({'get': 'members'}), name='group_members_api'),
    path('api/groups/<int:pk>/assign-role/', views.GroupViewSet.as_view({'post': 'assign_role'}), name='group_assign_role'),
    path('api/groups/<int:pk>/leave/', views.GroupViewSet.as_view({'post': 'leave'}), name='group_leave'),
    path('api/groups/<int:pk>/assign-and-leave/', views.GroupViewSet.as_view({'post': 'assign_and_leave'}), name='group_assign_and_leave'),
    path('api/groups/<int:pk>/photos/<str:photo_type>/like/', views.GroupViewSet.as_view({'post': 'photo_like'}), name='group_photo_like'),
    # Announcement API endpoints
    path('api/groups/<int:group_id>/announcements/', views.AnnouncementViewSet.as_view({'get': 'list', 'post': 'create'}), name='group_announcements_api'),
    path('api/groups/<int:group_id>/announcements/<int:pk>/', views.AnnouncementViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}), name='group_announcement_detail_api'),
    path('api/groups/<int:group_id>/announcements/<int:pk>/pin/', views.AnnouncementViewSet.as_view({'post': 'pin'}), name='group_announcement_pin_api'),
    path('api/groups/<int:group_id>/announcements/<int:pk>/unpin/', views.AnnouncementViewSet.as_view({'post': 'unpin'}), name='group_announcement_unpin_api'),
    path('api/groups/<int:group_id>/announcements/<int:pk>/mark-read/', views.AnnouncementViewSet.as_view({'post': 'mark_read'}), name='group_announcement_mark_read_api'),
    path('api/announcements/<int:announcement_id>/mark-read/', views.mark_announcement_read_api, name='mark_announcement_read_api'),
    # Group invite API endpoints
    path('api/groups/<int:group_id>/mutual-friends/', views.get_mutual_friends_api, name='get_mutual_friends_api'),
    path('api/groups/<int:group_id>/send-invites/', views.send_group_invites_api, name='send_group_invites_api'),
    # Group Chat API endpoints
    path('api/groups/<int:group_id>/messages/', views.retired_group_chat_api, name='group_messages_api'),
    path('api/groups/<int:group_id>/messages/<int:pk>/', views.retired_group_chat_api, name='group_message_detail_api'),
    path('api/groups/<int:group_id>/messages/<int:pk>/react/', views.retired_group_chat_api, name='group_message_react_api'),
    path('api/groups/<int:group_id>/messages/<int:pk>/mark-read/', views.retired_group_chat_api, name='group_message_mark_read_api'),
    path('api/groups/attachments/upload/', views.retired_group_chat_api, name='group_attachment_upload'),
    path('api/groups/attachments/batch-upload/', views.retired_group_chat_api, name='group_batch_attachment_upload'),
]
