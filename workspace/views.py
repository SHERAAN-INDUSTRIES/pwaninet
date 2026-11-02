from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import WorkspaceSyncState


@login_required
@require_GET
def workspace_sync(request):
    state, _ = WorkspaceSyncState.objects.get_or_create(user=request.user)
    context = {
        "user_id": request.user.pk,
        "user_email": request.user.email,
        # This is passed to the external workspace handoff; only send it over
        # HTTPS to a trusted origin.
        "session_key": request.session.session_key,
        "is_synced": state.is_synced,
        "workspace_base_url": getattr(settings, "VASTO_WORKSPACE_BASE_URL", ""),
    }
    return render(request, "workspace/sync.html", context)


@login_required
@require_POST
def workspace_dismiss(request):
    state, _ = WorkspaceSyncState.objects.get_or_create(user=request.user)
    state.dismissed_at = timezone.now()
    state.save(update_fields=["dismissed_at", "updated_at"])
    return redirect("posts:home")
