from django.conf import settings
from django.db import models


class WorkspaceSyncState(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workspace_sync_state",
    )
    synced_at = models.DateTimeField(null=True, blank=True)
    dismissed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def is_synced(self) -> bool:
        return self.synced_at is not None

    @property
    def notification_dismissed(self) -> bool:
        return self.dismissed_at is not None or self.is_synced
