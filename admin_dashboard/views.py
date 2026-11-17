"""Admin command-center views.

Every workspace page is rendered through :func:`render_workspace`, which
injects the shared live-ops context for both rails (``ops`` / ``engines`` /
``providers`` / ``registry``) and picks the response shape:

* full page template for a normal request (and always on entry — the
  workspace boundary forces native navigation into ``/dashboard/``),
* ``partials/admin_navigation_partial.html`` for HTMX (centre panel swap +
  out-of-band rail refresh, never the topbar).
"""

import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render

from notifications.events.publisher import EventPublisher
from notifications.events.registry import EventActions, EventSources, EventTypes
from posts.models import Report

from . import services
from .models import FeedbackReply, FeedbackTicket, UserDailyActivity
from users.services.session_service import revoke_all_user_sessions

User = get_user_model()


def is_admin(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser)


# --------------------------------------------------------------------------- shell
def workspace_context(context=None):
    """Shared command-center context — both rails read these keys."""
    base = {
        "ops": services.ops_summary(),
        "engines": services.engine_health(),
        "providers": services.provider_health(),
        "registry": services.registry_health(),
    }
    if context:
        base.update(context)
    return base


def render_workspace(request, page_template, content_partial, context=None, page_title="Admin"):
    ctx = workspace_context(context)
    ctx["content_partial"] = content_partial
    ctx["page_title"] = page_title
    if request.headers.get("HX-Request"):
        return render(request, "admin_dashboard/partials/admin_navigation_partial.html", ctx)
    return render(request, page_template, ctx)


# --------------------------------------------------------------------------- overview
@login_required
@user_passes_test(is_admin)
def dashboard_home(request):
    overview = services.platform_overview(days=14)
    context = dict(overview)
    # Chart payloads must be JSON literals inside the inline <script>.
    context["chart_labels"] = json.dumps(overview["chart_labels"])
    context["growth_data"] = json.dumps(overview["growth_data"])
    context["active_data"] = json.dumps(overview["active_data"])
    return render_workspace(
        request,
        "admin_dashboard/dashboard.html",
        "admin_dashboard/partials/dashboard_content.html",
        context,
        page_title="Overview",
    )


# --------------------------------------------------------------------------- AI console
def _chain_rows(setting_name):
    """Normalise a configured fallback chain into display rows."""
    rows = []
    for item in getattr(settings, setting_name, []) or []:
        if isinstance(item, dict):
            provider = (item.get("provider") or "").strip()
            model = (item.get("model") or "").strip()
        else:
            text = str(item)
            provider, model = (text.split(":", 1) if ":" in text else (text, ""))
            provider, model = provider.strip(), model.strip()
        if not provider:
            continue
        rows.append({
            "provider": provider,
            "provider_label": services.provider_label(provider.lower()),
            "model_id": model,
            "configured": services.provider_key_state(provider.lower())["configured"],
        })
    return rows


def _quota_status():
    try:
        from pwanimate.ai.gateway.quota_tracker import get_quota_tracker
        return get_quota_tracker().get_status()
    except Exception:  # pragma: no cover - quota tracking must never break the page
        return {}


@login_required
@user_passes_test(is_admin)
def ai_providers(request):
    provider_rows = services.provider_health()
    context = {
        "providers": provider_rows,
        "llm_chain": _chain_rows("PWANIMATE_LLM_FALLBACK_CHAIN"),
        "vision_chain": _chain_rows("PWANIMATE_VISION_FALLBACK_CHAIN"),
        "quota": _quota_status(),
        "providers_ready": sum(1 for p in provider_rows if p["configured"]),
        "providers_missing": sum(1 for p in provider_rows if p["state"] == "not_configured"),
        "rungs_limited": sum(1 for p in provider_rows for e in p["models"] if e["rate_limited"]),
        "rungs_total": sum(len(p["models"]) for p in provider_rows),
    }
    return render_workspace(
        request,
        "admin_dashboard/ai_providers.html",
        "admin_dashboard/partials/ai_providers_content.html",
        context,
        page_title="AI providers",
    )


def _models_context(flash=None, flash_level="info", usage_days=30):
    catalog = services.model_catalog()
    usage = services.model_usage_map(days=usage_days)
    max_tokens = 0
    for row in catalog:
        record = usage.get((row["provider"], row["model_id"])) or {}
        row["usage_tokens"] = record.get("total") or 0
        row["usage_requests"] = record.get("requests") or 0
        row["usage_users"] = record.get("users") or 0
        row["usage_last_used"] = record.get("last_used")
        max_tokens = max(max_tokens, row["usage_tokens"])
    for row in catalog:
        row["usage_share"] = round(100.0 * row["usage_tokens"] / max_tokens, 1) if max_tokens else 0
    context = {
        "models": catalog,
        "usage_days": usage_days,
        "max_usage_tokens": max_tokens,
    }
    if flash:
        context["flash"] = flash
        context["flash_level"] = flash_level
    return context


@login_required
@user_passes_test(is_admin)
def ai_models(request):
    return render_workspace(
        request,
        "admin_dashboard/ai_models.html",
        "admin_dashboard/partials/ai_models_content.html",
        _models_context(),
        page_title="Models",
    )


@login_required
@user_passes_test(is_admin)
def toggle_model(request):
    """Switch a registered model on/off. POST-only, staff-only."""
    if request.method != "POST":
        return HttpResponseBadRequest("POST required.")

    provider = (request.POST.get("provider") or "").strip().lower()
    model_id = (request.POST.get("model_id") or "").strip()
    activate = request.POST.get("active") == "1"

    if not provider or not model_id:
        offering, error = None, "not_registered"
    else:
        offering, error = services.set_model_active(provider, model_id, activate)

    if error:
        flash = (
            f"{model_id} is not in the model registry — register it with "
            "`python manage.py seed_model_registry` before switching it."
        )
        level = "warning"
    else:
        EventPublisher.publish(
            event_type=EventTypes.ADMIN_MODEL_TOGGLED.value,
            source=EventSources.ADMIN.value,
            action=(EventActions.ENABLED.value if activate else EventActions.DISABLED.value),
            target_type="ModelOffering",
            target_id=f"{provider}:{model_id}",
            actor=request.user,
            context_type="Model",
            context_id=f"{provider}:{model_id}",
            metadata={
                "provider": provider,
                "model_id": model_id,
                "is_active": activate,
            },
        )
        flash = (
            f"{model_id} {'enabled' if activate else 'disabled'}. "
            "Registry caches refresh within 60 seconds across workers."
        )
        level = "success"

    if request.headers.get("HX-Request"):
        return render_workspace(
            request,
            "admin_dashboard/ai_models.html",
            "admin_dashboard/partials/ai_models_content.html",
            _models_context(flash=flash, flash_level=level),
            page_title="Models",
        )
    messages.add_message(request, messages.SUCCESS if level == "success" else messages.WARNING, flash)
    return redirect("admin_dashboard:ai_models")


@login_required
@user_passes_test(is_admin)
def ai_usage(request):
    try:
        days = int(request.GET.get("days", 30))
    except (TypeError, ValueError):
        days = 30
    if days not in (7, 30, 90):
        days = 30

    usage = services.token_usage(days=days)
    context = {
        "usage": usage,
        "days": days,
        "chart_labels": json.dumps(usage["labels"]),
        "chart_prompt": json.dumps(usage["prompt"]),
        "chart_completion": json.dumps(usage["completion"]),
        "chart_total": json.dumps(usage["total"]),
        "chart_cost": json.dumps(usage["cost"]),
    }
    return render_workspace(
        request,
        "admin_dashboard/ai_usage.html",
        "admin_dashboard/partials/ai_usage_content.html",
        context,
        page_title="Token usage",
    )


# --------------------------------------------------------------------------- feedback
@login_required
@user_passes_test(is_admin)
def feedback_inbox(request):
    status_filter = request.GET.get("status")
    tickets = FeedbackTicket.objects.all().order_by('-created_at')

    if status_filter:
        tickets = tickets.filter(status=status_filter)

    context = {
        'tickets': tickets,
        'current_filter': status_filter
    }
    return render_workspace(
        request,
        "admin_dashboard/inbox.html",
        "admin_dashboard/partials/inbox_content.html",
        context,
        page_title="Feedback inbox",
    )


@login_required
@user_passes_test(is_admin)
def feedback_detail(request, ticket_id):
    ticket = get_object_or_404(FeedbackTicket, id=ticket_id)

    if ticket.status == FeedbackTicket.Status.UNREAD:
        ticket.status = FeedbackTicket.Status.OPEN
        ticket.save()

    if request.method == 'POST':
        message = request.POST.get('message')
        if message:
            reply = FeedbackReply.objects.create(
                ticket=ticket,
                sender=request.user,
                message=message
            )
            ticket.status = FeedbackTicket.Status.IN_PROGRESS
            ticket.save()

            # Send notification
            EventPublisher.publish(
                event_type="admin.feedback.replied",
                source=EventSources.ADMIN.value,
                action="replied",
                target_type="FeedbackTicket",
                target_id=str(ticket.id),
                actor=request.user,
                context_type="Feedback",
                context_id=str(ticket.id),
                metadata={
                    "user_id": ticket.sender.id,
                    "ticket_id": str(ticket.id),
                    "context_name": ticket.subject
                }
            )

            messages.success(request, "Reply sent successfully.")
            return redirect('admin_dashboard:feedback_detail', ticket_id=ticket.id)

    context = {
        'ticket': ticket,
        'replies': ticket.replies.all()
    }
    return render_workspace(
        request,
        "admin_dashboard/ticket_detail.html",
        "admin_dashboard/partials/ticket_detail_content.html",
        context,
        page_title="Ticket",
    )


@login_required
def submit_feedback(request):
    if request.method == 'POST':
        subject = request.POST.get('subject', '').strip()
        category = request.POST.get('category', FeedbackTicket.Category.OTHER)
        message = request.POST.get('message', '').strip()
        current_url = request.POST.get('current_url', '').strip()[:500]
        device_info = request.META.get('HTTP_USER_AGENT', '')

        error_message = None
        if not subject:
            error_message = 'Add a short subject so we can identify your feedback.'
        elif len(subject) > 255:
            error_message = 'Keep the subject under 256 characters.'
        elif not message:
            error_message = 'Write a message before submitting.'
        elif len(message) > 10000:
            error_message = 'Keep your message under 10,000 characters.'
        elif category not in FeedbackTicket.Category.values:
            category = FeedbackTicket.Category.OTHER

        if error_message:
            return render(request, 'admin_dashboard/partials/feedback_composer.html', {
                'error_message': error_message,
                'category_choices': FeedbackTicket.Category.choices,
                'form_values': {
                    'category': category,
                    'subject': subject,
                    'message': message,
                    'current_url': current_url,
                },
            })

        ticket = FeedbackTicket.objects.create(
            sender=request.user,
            subject=subject,
            category=category,
            message=message,
            current_url=current_url,
            device_info=device_info,
        )
        EventPublisher.publish(
            event_type=EventTypes.ADMIN_FEEDBACK_SUBMITTED.value,
            source=EventSources.ADMIN.value,
            action='submitted',
            target_type='FeedbackTicket',
            target_id=str(ticket.id),
            actor=request.user,
            context_type='FeedbackTicket',
            context_id=str(ticket.id),
            audience='ADMINISTRATORS',
            metadata={
                'ticket_id': str(ticket.id),
                'context_name': ticket.subject,
            },
        )
        return render(request, 'admin_dashboard/partials/feedback_composer.html', {
            'submitted': True,
            'category_choices': FeedbackTicket.Category.choices,
        })

    return render(request, 'admin_dashboard/partials/feedback_composer.html', {
        'category_choices': FeedbackTicket.Category.choices,
    })


@login_required
def user_feedback_view(request, ticket_id):
    ticket = get_object_or_404(FeedbackTicket, id=ticket_id, sender=request.user)

    if request.method == 'POST':
        message = request.POST.get('message')
        if message:
            FeedbackReply.objects.create(
                ticket=ticket,
                sender=request.user,
                message=message
            )
            # Reopen ticket for admins
            ticket.status = FeedbackTicket.Status.OPEN
            ticket.save()
            return redirect('admin_dashboard:user_feedback_view', ticket_id=ticket.id)

    context = {
        'ticket': ticket,
        'replies': ticket.replies.all()
    }

    if request.headers.get('HX-Request'):
        return render(request, 'admin_dashboard/partials/user_feedback_content.html', context)
    return render(request, 'admin_dashboard/user_feedback.html', context)


@login_required
@user_passes_test(is_admin)
def user_logins(request):
    """One merged feed of logins: successes, failures and new devices."""
    outcome = (request.GET.get('outcome') or 'all').strip().lower()
    search = (request.GET.get('q') or '').strip()[:60]
    try:
        days = int(request.GET.get('days', 7))
    except (TypeError, ValueError):
        days = 7
    if days not in (7, 30, 90):
        days = 7
    if outcome not in ('all', 'success', 'failed', 'new_device'):
        outcome = 'all'

    rows = services.recent_logins(days=days, outcome=outcome, search=search)
    context = {
        'login_rows': rows,
        'days': days,
        'outcome': outcome,
        'search': search,
        'outcome_options': [
            ('all', 'All'),
            ('success', 'Successful'),
            ('failed', 'Failed / blocked'),
            ('new_device', 'New devices'),
        ],
    }
    return render_workspace(
        request,
        'admin_dashboard/user_logins.html',
        'admin_dashboard/partials/user_logins_content.html',
        context,
        page_title='User logins',
    )


@login_required
@user_passes_test(is_admin)
def admin_manager(request):
    """Read-only for regular staff; superusers get session revocation."""
    context = {
        'admins': services.admin_roster(),
    }
    return render_workspace(
        request,
        'admin_dashboard/admin_manager.html',
        'admin_dashboard/partials/admin_manager_content.html',
        context,
        page_title='Admin manager',
    )


@login_required
@user_passes_test(lambda u: u.is_authenticated and u.is_superuser)
def revoke_user_sessions(request, user_id):
    if request.method != 'POST':
        return HttpResponseBadRequest('POST required.')

    target = get_object_or_404(User, id=user_id, is_staff=True)
    if target == request.user:
        messages.add_message(
            request, messages.WARNING,
            'That is your own account — refusing to revoke all sessions from here.',
        )
        return redirect('admin_dashboard:admin_manager')

    revoked = revoke_all_user_sessions(target)
    messages.success(request, f'Revoked {revoked} session(s) for {target.username}.')
    return redirect('admin_dashboard:admin_manager')


# --------------------------------------------------------------------------- documents
@login_required
@user_passes_test(is_admin)
def documents(request):
    tab = request.GET.get('tab', 'all')
    search = request.GET.get('q', '')
    doc_status = request.GET.get('status', '')
    visibility = request.GET.get('visibility', '')
    sort = request.GET.get('sort', 'newest')
    page = request.GET.get('page', 1)
    flash = request.session.pop('admin_documents_flash', None)
    flash_level = request.session.pop('admin_documents_flash_level', 'info')

    context = services.document_index_overview(
        tab=tab,
        search=search,
        doc_status=doc_status,
        visibility=visibility,
        sort=sort,
        page=page,
    )
    context['page_title'] = 'Documents'
    if flash:
        context['flash'] = flash
        context['flash_level'] = flash_level
    return render_workspace(
        request,
        'admin_dashboard/documents.html',
        'admin_dashboard/partials/documents_content.html',
        context,
        page_title='Documents',
    )


@login_required
@user_passes_test(is_admin)
def reindex_document(request, document_id):
    if request.method != 'POST':
        return HttpResponseBadRequest('POST required.')
    mode = (request.POST.get('mode') or 'ingest').strip().lower()
    info, error = services.queue_document_reindex(document_id, mode=mode)
    if error == 'not_found':
        flash, level = 'Document not found.', 'warning'
    elif error == 'no_latest_version':
        flash, level = 'This document has no latest version to index.', 'warning'
    elif error:
        flash, level = f'Could not queue indexing: {error}', 'warning'
    else:
        label = 'Embedding' if mode == 'embed' else 'Re-indexing'
        ver = info['version']
        task = info.get('task_id') or 'queued'
        flash = f"{label} queued for “{info['document'].title}” (v{ver.version_number}, task {task})."
        level = 'success'

    # Preserve current filters on redirect
    params = request.POST.get('return_query', '').strip()
    base_url = redirect('admin_dashboard:documents')['Location']
    if params:
        base_url = base_url.rstrip('/') + '/?' + params.lstrip('?')
    if request.headers.get('HX-Request'):
        request.session['admin_documents_flash'] = flash
        request.session['admin_documents_flash_level'] = level
        response = redirect(base_url)
        response['HX-Redirect'] = base_url
        return response
    messages.add_message(request, messages.SUCCESS if level == 'success' else messages.WARNING, flash)
    return redirect(base_url)
