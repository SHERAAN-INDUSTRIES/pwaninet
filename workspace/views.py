from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from oauth2_provider.views.base import AuthorizationView

from .models import WorkspaceSyncState


class VastoWorkspaceAuthorizationView(AuthorizationView):
    """Use Vasto's Workspace connection page for the OAuth consent step."""

    template_name = "workspace/sync.html"


@login_required
@require_GET
def workspace_sync(request):
    workspace_base_url = getattr(settings, "VASTO_WORKSPACE_BASE_URL", "").rstrip("/")
    context = {
        "workspace_authorization_url": (
            f"{workspace_base_url}/api/auth/vasto/start/" if workspace_base_url else ""
        ),
    }
    return render(request, "workspace/sync.html", context)


@login_required
@require_POST
def workspace_dismiss(request):
    state, _ = WorkspaceSyncState.objects.get_or_create(user=request.user)
    state.dismissed_at = timezone.now()
    state.save(update_fields=["dismissed_at", "updated_at"])
    return redirect("posts:home")
