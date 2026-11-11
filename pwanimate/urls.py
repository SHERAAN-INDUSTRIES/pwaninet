"""
Pwanimate UI URL Configuration.
"""

from django.urls import path
from pwanimate.views import (
    PwanimateConversationDeleteView,
    PwanimateConversationHistoryView,
    PwanimateSettingsAboutView,
    PwanimateSettingsPersonalizationView,
    PwanimateSettingsUsageView,
    PwanimateSettingsView,
    PwanimateSessionsView,
    PwanimateSettingsStudyView,
    PwanimateSettingsMemoryView,
    PwanimateSettingsPlansView,
    PwanimateUIView,
)

app_name = "pwanimate"

from django.views.generic import RedirectView

urlpatterns = [
    path('study-desk/', RedirectView.as_view(pattern_name='pwanimate:settings_memory', permanent=False), name='study_desk'),
    path('settings/study/', PwanimateSettingsStudyView.as_view(), name='settings_study'),
    path('settings/memory/', PwanimateSettingsMemoryView.as_view(), name='settings_memory'),
    path('settings/plans/', PwanimateSettingsPlansView.as_view(), name='settings_plans'),
    path("", PwanimateUIView.as_view(), name="index"),
    path("history/", PwanimateConversationHistoryView.as_view(), name="conversation_history"),
    path("settings/", PwanimateSettingsView.as_view(), name="settings"),
    path("settings/personalization/", PwanimateSettingsPersonalizationView.as_view(), name="settings_personalization"),
    path("settings/usage/", PwanimateSettingsUsageView.as_view(), name="settings_usage"),
    path("settings/about/", PwanimateSettingsAboutView.as_view(), name="settings_about"),
    path("sessions/", PwanimateSessionsView.as_view(), name="sessions"),
    path("<uuid:conversation_id>/", PwanimateUIView.as_view(), name="conversation_detail"),
    path("<uuid:conversation_id>/delete/", PwanimateConversationDeleteView.as_view(), name="delete_conversation"),
]
