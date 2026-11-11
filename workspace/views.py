from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from oauth2_provider.views.base import AuthorizationView
from oauth2_provider.models import AccessToken, RefreshToken, Application

from .models import WorkspaceSyncState


class VastoWorkspaceAuthorizationView(AuthorizationView):
    """Use Vasto's Workspace connection page for the OAuth consent step."""

    template_name = "workspace/sync.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Check if user has already authorized this application
        application = getattr(self, 'oauth2_request', {}).get('application')
        if application and self.request.user.is_authenticated:
            # Check for active access tokens
            now = timezone.now()
            active_tokens = AccessToken.objects.filter(
                user=self.request.user,
                application=application,
                expires__gt=now
            ).exists()
            
            # Check for refresh tokens (longer-lived)
            active_refresh_tokens = RefreshToken.objects.filter(
                user=self.request.user,
                application=application,
                revoked__isnull=True
            ).exists()
            
            context['is_already_authorized'] = active_tokens or active_refresh_tokens
            context['has_active_tokens'] = active_tokens
            context['has_refresh_tokens'] = active_refresh_tokens
        else:
            context['is_already_authorized'] = False
            context['has_active_tokens'] = False
            context['has_refresh_tokens'] = False
        
        context['workspace_app_url'] = getattr(
            settings, "VASTO_WORKSPACE_APP_URL", "http://127.0.0.1:3000"
        )
        
        return context


@login_required
@require_GET
def workspace_sync(request):
    workspace_base_url = getattr(settings, "VASTO_WORKSPACE_BASE_URL", "").rstrip("/")
    
    # Check if user has already authorized Workspaces
    try:
        app = Application.objects.get(client_id='ltC7TdAlzIzQAPw0Xcz9nPe0tMkm5EVjY25WVRuY')
        now = timezone.now()
        is_authorized = AccessToken.objects.filter(
            user=request.user,
            application=app,
            expires__gt=now
        ).exists() or RefreshToken.objects.filter(
            user=request.user,
            application=app,
            revoked__isnull=True
        ).exists()
    except Application.DoesNotExist:
        is_authorized = False
    
    context = {
        "workspace_authorization_url": (
            f"{workspace_base_url}/api/auth/vasto/start/" if workspace_base_url else ""
        ),
        "is_already_authorized": is_authorized,
        "workspace_app_url": getattr(
            settings, "VASTO_WORKSPACE_APP_URL", "http://127.0.0.1:3000"
        ),
    }
    return render(request, "workspace/sync.html", context)


@login_required
@require_POST
def workspace_revoke(request):
    """Revoke user's authorization for Workspaces."""
    try:
        app = Application.objects.get(client_id='ltC7TdAlzIzQAPw0Xcz9nPe0tMkm5EVjY25WVRuY')
        
        # Delete all access tokens for this user and application
        AccessToken.objects.filter(
            user=request.user,
            application=app
        ).delete()
        
        # Revoke all refresh tokens for this user and application
        RefreshToken.objects.filter(
            user=request.user,
            application=app
        ).update(revoked=timezone.now())
        
        # Also revoke any grants
        from oauth2_provider.models import Grant
        Grant.objects.filter(
            user=request.user,
            application=app
        ).delete()
        
    except Application.DoesNotExist:
        pass
    
    return redirect("workspace:sync")


@login_required
@require_POST
def workspace_dismiss(request):
    state, _ = WorkspaceSyncState.objects.get_or_create(user=request.user)
    state.dismissed_at = timezone.now()
    state.save(update_fields=["dismissed_at", "updated_at"])
    return redirect("posts:home")
