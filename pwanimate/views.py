"""
Pwanimate UI Views.

Exposes authenticated template-backed endpoints for the Pwanimate assistant interface,
supporting both full-page browser loads and HTMX partial swaps.
"""

from typing import Any, Dict, Optional

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View

from pwanimate.models import (
    PwanimateConversation,
    PwanimatePreferences,
    PwanimateResponseStyle,
    PwanimateTone,
    PwanimateVoiceGender,
    PwanimateVoiceStyle,
)
from pwanimate.services.preferences import PwanimatePreferenceService
from pwanimate.version import get_version as get_pwanimate_version
from documents.models import Document


def _get_settings_context(
    request: HttpRequest,
    active_tab: str = "personalization",
    success: bool = False,
    errors: Optional[Dict[str, Any]] = None,
    prefs: Optional[PwanimatePreferences] = None,
) -> Dict[str, Any]:
    """
    Construct standardized, unified settings context ensuring preferences,
    choices, usage telemetry, and version are always available.
    """
    if prefs is None:
        prefs = PwanimatePreferenceService.get_or_create_preferences(request.user)
    conversations_count = request.user.pwanimate_conversations.count()
    full_name = request.user.get_full_name().strip()
    user_display = full_name if full_name and full_name != "None None" else request.user.username

    # Real usage for the Usage tab: the same counters the quota check
    # enforces (never estimates), plus the tier catalogue for the upgrade
    # cards. Defensive: the billing tables may not be migrated yet.
    usage_snapshot = None
    tier_catalogue = []
    usage_bars = []
    usage_spark = None
    try:
        from pwanimate.billing.models import Tier
        from pwanimate.billing.services import QuotaService

        usage_snapshot = QuotaService.snapshot(request.user)
        # Human-readable entitlement lines for the upgrade cards.
        entitlement_labels = {
            "chats": "Chat turns",
            "images": "Image analysis",
            "premium_turns": "Premium model turns",
        }
        window_labels = {"day": "per day", "week": "per week", "period": "per month"}

        tier_catalogue = [
            {
                "code": tier.code,
                "name": tier.name,
                "rank": tier.rank,
                "price_kes": float(tier.price_kes),
                "duration_days": tier.duration_days,
                "description": tier.description,
                "entitlements": tier.entitlements or {},
                "includes": [
                    {
                        "label": entitlement_labels.get(key, key.replace("_", " ").title()),
                        "value": " · ".join(
                            f"{limit} {window_labels.get(window, window)}"
                            for window, limit in (caps or {}).items()
                        ),
                    }
                    for key, caps in (tier.entitlements or {}).items()
                ],
                "is_current": bool(usage_snapshot and usage_snapshot.get("tier", {}).get("code") == tier.code),
            }
            for tier in Tier.objects.filter(is_active=True).order_by("rank")
        ]

        # Quota balance bars: what is used against what is allowed, per window.
        windows = (usage_snapshot or {}).get("windows", {})
        for key, label in (
            ("chats", "Chat turns"),
            ("images", "Image analysis"),
            ("premium_turns", "Premium model turns"),
        ):
            for window in ("day", "week"):
                data = (windows.get(window) or {}).get(key) or {}
                limit = data.get("limit")
                if limit is None:
                    continue
                used = int(data.get("used") or 0)
                usage_bars.append(
                    {
                        "key": f"{key}_{window}",
                        "label": label,
                        "window": "Today" if window == "day" else "This week",
                        "used": used,
                        "limit": int(limit),
                        "remaining": max(0, int(limit) - used),
                        "pct": min(100, round(100 * used / int(limit))) if limit else 0,
                    }
                )

        # 14-day sparkline geometry (chats per day).
        series = (usage_snapshot or {}).get("series") or []
        if series:
            peak = max([int(point.get("chats") or 0) for point in series] + [1])
            width, height, pad = 280, 42, 3
            step = (width - pad * 2) / max(1, len(series) - 1)
            points = []
            for index, point in enumerate(series):
                chats = int(point.get("chats") or 0)
                x = pad + index * step
                y = height - pad - (chats / peak) * (height - pad * 2)
                points.append(f"{x:.1f},{y:.1f}")
            usage_spark = {
                "points": " ".join(points),
                "peak": peak,
                "width": width,
                "height": height,
                "last": series[-1],
            }
    except Exception:  # noqa: BLE001 - the usage tab must never break settings
        usage_snapshot = None
        tier_catalogue = []

    return {
        "preferences": prefs,
        "tone_choices": PwanimateTone.choices,
        "response_style_choices": PwanimateResponseStyle.choices,
        "voice_style_choices": PwanimateVoiceStyle.choices,
        "voice_gender_choices": PwanimateVoiceGender.choices,
        "conversations_count": conversations_count,
        "user_display": user_display,
        "student_username": request.user.get_username(),
        "student_interests": getattr(request.user, "interests", "") or "",
        "active_tab": active_tab,
        "success": success,
        "errors": errors or {},
        "pwanimate_version": get_pwanimate_version(),
        "usage_snapshot": usage_snapshot,
        "usage_bars": usage_bars,
        "usage_spark": usage_spark,
        "tier_catalogue": tier_catalogue,
    }


class PwanimateUIView(View):
    """
    Main user-facing view for Pwanimate.

    Renders the two-pane assistant workspace.
    If conversation_id is supplied, validates ownership and loads historical messages.
    Supports both direct full-page loads and HTMX inner swaps.
    """

    @method_decorator(login_required)
    def get(self, request: HttpRequest, conversation_id=None) -> HttpResponse:
        from pwanimate.services.study_session import StudySessionService

        active_conversation = None
        messages = []
        active_study_session = None
        resumable_study_session = None
        unavailable_resource_notices = []

        if conversation_id:
            try:
                active_conversation = PwanimateConversation.objects.select_related(
                    "study_session", "study_session__latest_checkpoint"
                ).get(
                    id=conversation_id,
                    user=request.user,
                )
                messages = list(active_conversation.messages.all().order_by("created_at"))
            except PwanimateConversation.DoesNotExist:
                raise Http404("Conversation not found.")

        history_filter = (
            "study"
            if (request.GET.get("filter") or "").strip().lower() == "study"
            else "all"
        )
        user_conversations = (
            PwanimateConversation.objects.filter(user=request.user)
            .select_related("study_session", "study_session__latest_checkpoint")
            .order_by("-updated_at")
        )
        if history_filter == "study":
            user_conversations = user_conversations.filter(study_session__isnull=False)

        paginator = Paginator(user_conversations, 15)
        page_obj = paginator.get_page(1)

        initial_prompt = request.GET.get("prompt", "").strip()
        initial_study_mode = request.GET.get("study", "").strip().lower() in {"1", "true", "yes"}

        initial_context_resources = []
        context_document_id = request.GET.get("context_document", "").strip()
        if context_document_id:
            document_qs = Document.objects.filter(
                share_id=context_document_id,
                status="ready",
                is_available=True,
            ).select_related("category", "uploaded_by")
            user = request.user
            is_admin_or_leader = (
                user.is_staff
                or user.is_superuser
                or getattr(user, "global_role", None) in ["PRESIDENT", "DELEGATE"]
            )
            if not is_admin_or_leader:
                visibility_q = Q(visibility="public") | Q(uploaded_by=user)
                restricted_q = Q(visibility="restricted")
                enrolled_unit_ids = []
                if hasattr(user, "get_enrolled_units"):
                    try:
                        enrolled_unit_ids = [
                            e.academic_unit_id for e in user.get_enrolled_units()
                        ]
                    except Exception:
                        enrolled_unit_ids = []
                if enrolled_unit_ids:
                    visibility_q |= restricted_q & Q(
                        academic_units__academic_unit_id__in=enrolled_unit_ids
                    )
                programme = getattr(user, "programme", None)
                if programme:
                    visibility_q |= restricted_q & Q(
                        academic_units__academic_unit__programme_units__programme=programme
                    )
                elif getattr(user, "course", None):
                    visibility_q |= restricted_q & Q(
                        academic_units__academic_unit__code__icontains=user.course.name
                    )
                document_qs = document_qs.filter(visibility_q).distinct()

            context_document = document_qs.first()
            if not context_document:
                raise Http404("Document not found or unavailable.")
            latest_version = context_document.latest_version
            first_file = latest_version.files.first() if latest_version else None
            if first_file and first_file.file:
                try:
                    media_url = first_file.file.url
                except (ValueError, OSError):
                    media_url = ""
                if media_url:
                    try:
                        page_number = max(1, int(request.GET.get("page", "1")))
                    except (TypeError, ValueError):
                        page_number = 1

                    doc_unit_link = (
                        context_document.academic_units.select_related("academic_unit").first()
                    )
                    unit_code = (
                        doc_unit_link.academic_unit.code
                        if doc_unit_link and doc_unit_link.academic_unit_id
                        else ""
                    )
                    unit_name = (
                        doc_unit_link.academic_unit.name
                        if doc_unit_link and doc_unit_link.academic_unit_id
                        else ""
                    )
                    category_name = (
                        context_document.category.name
                        if context_document.category_id
                        else "Document"
                    )
                    citation_label = (
                        f"{unit_code} — {unit_name}"
                        if unit_code and unit_name
                        else (unit_code or category_name)
                    )

                    initial_context_resources.append({
                        "resourceType": "document",
                        "sourceType": "document",
                        "documentId": str(context_document.share_id),
                        "documentShareId": str(context_document.share_id),
                        "documentVersionId": str(latest_version.id),
                        "fileId": str(first_file.id),
                        "fileType": (first_file.extension or "").lstrip("."),
                        "mediaUrl": media_url,
                        "thumbnailUrl": first_file.preview_url or "",
                        "title": context_document.title,
                        "category": category_name,
                        "citation": citation_label,
                        "unitCode": unit_code,
                        "unitName": unit_name,
                        "url": reverse(
                            "documents:document_detail",
                            args=[context_document.share_id],
                        ),
                        "pageNumber": page_number,
                    })

        if active_conversation:
            study_session_obj = StudySessionService.get_session_for_conversation(
                request.user, active_conversation
            )
            if study_session_obj:
                if (
                    request.GET.get("resume", "").strip().lower() in {"1", "true", "yes"}
                    and study_session_obj.status == "paused"
                ):
                    study_session_obj = StudySessionService.resume_session(
                        study_session_obj, user=request.user
                    )
                restored_resources, unavailable_resource_notices = (
                    StudySessionService.restore_context_state(
                        session=study_session_obj,
                        user=request.user,
                    )
                )
                existing_keys = {
                    (r.get("sourceType"), r.get("documentShareId") or r.get("attachmentId"))
                    for r in initial_context_resources
                }
                for item in restored_resources:
                    key = (
                        item.get("sourceType"),
                        item.get("documentShareId") or item.get("attachmentId"),
                    )
                    if key not in existing_keys:
                        initial_context_resources.append(item)
                        existing_keys.add(key)
                active_study_session = StudySessionService.serialize_session(study_session_obj)
        else:
            resumable_obj = StudySessionService.get_resumable_session(request.user)
            if resumable_obj:
                resumable_study_session = StudySessionService.serialize_session(resumable_obj)

        settings_ctx = _get_settings_context(request)
        context = {
            **settings_ctx,
            "conversations": page_obj.object_list,
            "has_next": page_obj.has_next(),
            "next_page": 2 if page_obj.has_next() else None,
            "active_conversation": active_conversation,
            "active_conversation_id": str(active_conversation.id) if active_conversation else "",
            "messages": messages,
            "initial_prompt": initial_prompt,
            "initial_study_mode": initial_study_mode,
            "initial_context_resources": initial_context_resources,
            "active_study_session": active_study_session,
            "resumable_study_session": resumable_study_session,
            "unavailable_resource_notices": unavailable_resource_notices,
            "history_filter": history_filter,
        }

        if request.headers.get("HX-Request"):
            current_url = request.headers.get("HX-Current-URL", "")
            if current_url and "/pwanimate" not in current_url:
                response = HttpResponse()
                response["HX-Redirect"] = request.get_full_path()
                return response
            return render(request, "pwanimate/partials/chat_navigation_partial.html", context)
        return render(request, "pwanimate/index.html", context)


class PwanimateConversationHistoryView(View):
    """
    Paginated conversation history partial endpoint for infinite scroll lazy loading.
    Accepts page, active_id, and filter query parameters.
    """

    @method_decorator(login_required)
    def get(self, request: HttpRequest) -> HttpResponse:
        page = request.GET.get("page", 1)
        active_id = request.GET.get("active_id", "").strip()
        history_filter = (
            "study"
            if (request.GET.get("filter") or "").strip().lower() == "study"
            else "all"
        )

        user_conversations = (
            PwanimateConversation.objects.filter(user=request.user)
            .select_related("study_session", "study_session__latest_checkpoint")
            .order_by("-updated_at")
        )
        if history_filter == "study":
            user_conversations = user_conversations.filter(study_session__isnull=False)

        paginator = Paginator(user_conversations, 15)
        try:
            page_obj = paginator.get_page(page)
        except Exception:
            page_obj = paginator.get_page(1)

        context = {
            "conversations": page_obj.object_list,
            "has_next": page_obj.has_next(),
            "next_page": page_obj.next_page_number() if page_obj.has_next() else None,
            "active_conversation_id": active_id,
            "history_filter": history_filter,
            "is_paginated_chunk": True,
        }
        return render(request, "pwanimate/partials/conversation_list.html", context)


class PwanimateConversationDeleteView(View):
    """
    Delete a conversation thread owned by the authenticated user.
    Returns 204 No Content on success, or 404 if not found or unauthorized.
    Supports both DELETE and POST methods (e.g. from HTMX or fetch).
    """

    @method_decorator(login_required)
    def delete(self, request: HttpRequest, conversation_id) -> HttpResponse:
        from pwanimate.services.conversation import ConversationService

        conversation = ConversationService.get_owned_conversation(
            user=request.user,
            conversation_id=conversation_id,
        )
        if conversation is None:
            raise Http404("Conversation not found.")

        conversation.delete()
        return HttpResponse(status=204)

    @method_decorator(login_required)
    def post(self, request: HttpRequest, conversation_id) -> HttpResponse:
        return self.delete(request, conversation_id)


class PwanimateSettingsView(View):
    """
    Landing endpoint for Pwanimate settings.
    Redirects to the default personalization settings tab.
    """

    @method_decorator(login_required)
    def get(self, request: HttpRequest) -> HttpResponse:
        return redirect("pwanimate:settings_personalization")


class PwanimateSettingsPersonalizationView(View):
    """
    View to display and update student AI preferences (nickname, tone, response style, instructions).
    Supports desktop modal body swaps, mobile page content swaps, and full-page loads.
    """

    @method_decorator(login_required)
    def get(self, request: HttpRequest) -> HttpResponse:
        context = _get_settings_context(request, active_tab="personalization")
        hx_target = request.headers.get("HX-Target", "")
        if request.headers.get("HX-Request"):
            if hx_target in ("pwanimate-settings-modal-body", "pwanimate-settings-mobile-content", "pwanimate-personalization-form"):
                return render(request, "pwanimate/settings/partials/personalization_content.html", context)
            return render(request, "pwanimate/settings/partials/settings_mobile_page.html", context)
        return render(request, "pwanimate/settings/index.html", context)

    @method_decorator(login_required)
    def post(self, request: HttpRequest) -> HttpResponse:
        nickname = request.POST.get("nickname")
        tone = request.POST.get("tone")
        response_style = request.POST.get("response_style")
        personal_instructions = request.POST.get("personal_instructions")
        voice_style = request.POST.get("voice_style")
        voice_gender = request.POST.get("voice_gender")
        auto_tts_enabled = request.POST.get("auto_tts_enabled") == "on"

        errors = {}
        success = False
        try:
            prefs = PwanimatePreferenceService.update_preferences(
                user=request.user,
                nickname=nickname,
                tone=tone,
                response_style=response_style,
                personal_instructions=personal_instructions,
                voice_style=voice_style,
                voice_gender=voice_gender,
                auto_tts_enabled=auto_tts_enabled,
            )
            success = True
        except ValidationError as exc:
            saved_prefs = PwanimatePreferenceService.get_preferences_or_defaults(request.user)
            prefs = PwanimatePreferences(
                user=request.user,
                nickname=nickname if nickname is not None else saved_prefs.nickname,
                tone=tone if tone in PwanimateTone.values else saved_prefs.tone,
                response_style=response_style if response_style in PwanimateResponseStyle.values else saved_prefs.response_style,
                personal_instructions=personal_instructions if personal_instructions is not None else saved_prefs.personal_instructions,
                voice_style=voice_style if voice_style in PwanimateVoiceStyle.values else saved_prefs.voice_style,
                voice_gender=voice_gender if voice_gender in PwanimateVoiceGender.values else saved_prefs.voice_gender,
                auto_tts_enabled=auto_tts_enabled,
            )
            if hasattr(exc, "message_dict"):
                errors = exc.message_dict
            elif hasattr(exc, "messages"):
                errors = {"__all__": exc.messages}
            else:
                errors = {"__all__": [str(exc)]}

        context = _get_settings_context(
            request,
            active_tab="personalization",
            success=success,
            errors=errors,
            prefs=prefs,
        )

        if request.headers.get("HX-Request"):
            return render(request, "pwanimate/settings/partials/personalization_content.html", context)
        return render(request, "pwanimate/settings/index.html", context)


class PwanimateSettingsUsageView(View):
    """
    View displaying student usage context and accounting status.
    """

    @method_decorator(login_required)
    def get(self, request: HttpRequest) -> HttpResponse:
        context = _get_settings_context(request, active_tab="usage")
        hx_target = request.headers.get("HX-Target", "")
        if request.headers.get("HX-Request"):
            if hx_target in ("pwanimate-settings-modal-body", "pwanimate-settings-mobile-content"):
                return render(request, "pwanimate/settings/partials/usage_content.html", context)
            return render(request, "pwanimate/settings/partials/settings_mobile_page.html", context)
        return render(request, "pwanimate/settings/index.html", context)


class PwanimateSettingsAboutView(View):
    """
    View displaying about information, capabilities, and boundaries for Pwanimate.
    """

    @method_decorator(login_required)
    def get(self, request: HttpRequest) -> HttpResponse:
        context = _get_settings_context(request, active_tab="about")
        hx_target = request.headers.get("HX-Target", "")
        if request.headers.get("HX-Request"):
            if hx_target in ("pwanimate-settings-modal-body", "pwanimate-settings-mobile-content"):
                return render(request, "pwanimate/settings/partials/about_content.html", context)
            return render(request, "pwanimate/settings/partials/settings_mobile_page.html", context)
        return render(request, "pwanimate/settings/index.html", context)
