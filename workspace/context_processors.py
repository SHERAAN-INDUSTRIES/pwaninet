from .models import WorkspaceSyncState


def workspace_notification(request):
    if not request.user.is_authenticated:
        return {"workspace_show_notification": False}

    state = WorkspaceSyncState.objects.filter(user=request.user).first()
    show = state is None or not state.notification_dismissed
    return {"workspace_show_notification": show}
