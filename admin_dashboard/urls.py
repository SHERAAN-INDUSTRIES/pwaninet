from django.urls import path
from . import views

app_name = 'admin_dashboard'

urlpatterns = [
    path('', views.dashboard_home, name='dashboard_home'),
    path('documents/', views.documents, name='documents'),
    path('documents/<int:document_id>/reindex/', views.reindex_document, name='reindex_document'),
    path('ai/providers/', views.ai_providers, name='ai_providers'),
    path('ai/models/', views.ai_models, name='ai_models'),
    path('ai/models/toggle/', views.toggle_model, name='toggle_model'),
    path('ai/usage/', views.ai_usage, name='ai_usage'),
    path('users/logins/', views.user_logins, name='user_logins'),
    path('admins/', views.admin_manager, name='admin_manager'),
    path('admins/<int:user_id>/revoke/', views.revoke_user_sessions, name='revoke_user_sessions'),
    path('feedback/', views.feedback_inbox, name='feedback_inbox'),
    path('feedback/<uuid:ticket_id>/', views.feedback_detail, name='feedback_detail'),
    path('feedback/user/<uuid:ticket_id>/', views.user_feedback_view, name='user_feedback_view'),
    path('feedback/submit/', views.submit_feedback, name='submit_feedback'),
]
