from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import WorkspaceSyncState


class WorkspaceSyncViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="workspace-user",
            email="workspace@example.com",
            password="test-password",
            has_completed_onboarding=True,
        )
        self.client.force_login(self.user)

    def test_sync_page_links_to_workspaces_authorization(self):
        with self.settings(VASTO_WORKSPACE_BASE_URL="http://127.0.0.1:8001"):
            response = self.client.get(reverse("workspace:sync"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "workspace/sync.html")
        self.assertContains(response, 'href="http://127.0.0.1:8001/api/auth/vasto/start/"')
        self.assertNotContains(response, "session_key")

    def test_sync_page_explains_when_workspace_is_not_configured(self):
        with self.settings(VASTO_WORKSPACE_BASE_URL=""):
            response = self.client.get(reverse("workspace:sync"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "workspace/sync.html")
        self.assertContains(response, "VASTO_WORKSPACE_BASE_URL")

    def test_sync_page_requires_authentication(self):
        self.client.logout()

        response = self.client.get(reverse("workspace:sync"))

        self.assertRedirects(
            response,
            f"{reverse('login_direct')}?next={reverse('workspace:sync')}",
        )

    def test_sync_page_rejects_post(self):
        response = self.client.post(reverse("workspace:sync"))

        self.assertEqual(response.status_code, 405)

    def test_dismissal_records_state_and_redirects_home(self):
        response = self.client.post(reverse("workspace:dismiss"))

        self.assertRedirects(response, reverse("posts:home"))
        state = WorkspaceSyncState.objects.get(user=self.user)
        self.assertIsNotNone(state.dismissed_at)

    def test_home_shows_notification_and_desktop_workspace_link(self):
        response = self.client.get(reverse("posts:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sync your vasto account to vasto Workspace")
        self.assertContains(response, reverse("workspace:sync"))
        self.assertContains(response, "d-none d-lg-flex")

    def test_dismissed_user_no_longer_sees_home_notification(self):
        self.client.post(reverse("workspace:dismiss"))

        response = self.client.get(reverse("posts:home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Sync your vasto account to vasto Workspace")
