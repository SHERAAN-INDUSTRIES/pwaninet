"""
Pwanimate API URL Configuration.
"""

from django.urls import path
from pwanimate.api.views import (
    PwanimateAttachmentDownloadView,
    PwanimateAttachmentMediaView,
    PwanimateAttachmentStatusView,
    PwanimateAttachmentUploadView,
    PwanimateAttachmentVisionPolicyView,
    PwanimateChatView,
    PwanimateCollectionListCreateView,
    PwanimateConversationDetailView,
    PwanimateConversationListView,
    PwanimateContextDocumentSearchView,
    PwanimateActionView,
    PwanimateFeedbackSummaryView,
    PwanimateGeneratedResourceView,
    PwanimateMessageFeedbackView,
    PwanimateQuotaStatusView,
    PwanimateSpeechSynthesisView,
    PwanimateStudyCheckpointView,
    PwanimateStudySessionDetailView,
    PwanimateStudySessionListView,
    PwanimateStudySessionStatusView,
    PwanimateStudySummarySaveCollectionView,
    PwanimateStudySummaryView,
    PwanimateVoiceTranscriptionView,
)
from pwanimate.billing.api import (
    PwanimateBillingDarajaCallbackView,
    PwanimateBillingDemoCompleteView,
    PwanimateBillingQuotaView,
    PwanimateBillingStkView,
    PwanimateBillingSubscriptionView,
    PwanimateBillingTiersView,
    PwanimateBillingUsageView,
)

app_name = "pwanimate_api"

urlpatterns = [
    path("chat/", PwanimateChatView.as_view(), name="chat"),
    path("billing/quota/", PwanimateBillingQuotaView.as_view(), name="billing_quota"),
    path("billing/usage/", PwanimateBillingUsageView.as_view(), name="billing_usage"),
    path("billing/tiers/", PwanimateBillingTiersView.as_view(), name="billing_tiers"),
    path("billing/subscription/", PwanimateBillingSubscriptionView.as_view(), name="billing_subscription"),
    path("billing/mpesa/stk/", PwanimateBillingStkView.as_view(), name="billing_mpesa_stk"),
    path("billing/mpesa/callback/", PwanimateBillingDarajaCallbackView.as_view(), name="billing_mpesa_callback"),
    path("billing/mpesa/demo-complete/", PwanimateBillingDemoCompleteView.as_view(), name="billing_mpesa_demo"),
    path("messages/<int:message_id>/resources/", PwanimateGeneratedResourceView.as_view(), name="generated_resource"),
    path("messages/<int:message_id>/feedback/", PwanimateMessageFeedbackView.as_view(), name="message_feedback"),
    path("feedback/summary/", PwanimateFeedbackSummaryView.as_view(), name="feedback_summary"),
    path("actions/", PwanimateActionView.as_view(), name="action_list"),
    path("actions/<uuid:action_id>/", PwanimateActionView.as_view(), name="action_decision"),
    path("conversations/", PwanimateConversationListView.as_view(), name="conversation_list"),
    path("conversations/<uuid:conversation_id>/", PwanimateConversationDetailView.as_view(), name="conversation_detail"),
    path("study-sessions/", PwanimateStudySessionListView.as_view(), name="study_session_list"),
    path("study-sessions/<uuid:session_id>/", PwanimateStudySessionDetailView.as_view(), name="study_session_detail"),
    path("study-sessions/<uuid:session_id>/status/", PwanimateStudySessionStatusView.as_view(), name="study_session_status"),
    path("study-sessions/<uuid:session_id>/checkpoint/", PwanimateStudyCheckpointView.as_view(), name="study_session_checkpoint"),
    path("study-sessions/<uuid:session_id>/summary/", PwanimateStudySummaryView.as_view(), name="study_session_summary"),
    path(
        "study-sessions/<uuid:session_id>/summary/save-to-collection/",
        PwanimateStudySummarySaveCollectionView.as_view(),
        name="study_session_save_collection",
    ),
    path("collections/", PwanimateCollectionListCreateView.as_view(), name="collection_list_create"),
    path("quota-status/", PwanimateQuotaStatusView.as_view(), name="quota_status"),
    path("voice/transcribe/", PwanimateVoiceTranscriptionView.as_view(), name="voice_transcribe"),
    path("voice/synthesize/", PwanimateSpeechSynthesisView.as_view(), name="voice_synthesize"),
    path("context/documents/", PwanimateContextDocumentSearchView.as_view(), name="context_document_search"),
    path("attachments/upload/", PwanimateAttachmentUploadView.as_view(), name="attachment_upload"),
    path("attachments/<uuid:attachment_id>/status/", PwanimateAttachmentStatusView.as_view(), name="attachment_status"),
    path("attachments/<uuid:attachment_id>/vision-policy/", PwanimateAttachmentVisionPolicyView.as_view(), name="attachment_vision_policy"),
    path("attachments/<uuid:attachment_id>/view/", PwanimateAttachmentMediaView.as_view(), name="attachment_view"),
    path("attachments/<uuid:attachment_id>/download/", PwanimateAttachmentDownloadView.as_view(), name="attachment_download"),
]
