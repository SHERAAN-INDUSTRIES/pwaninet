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

    def test_sync_page_renders_account_and_session_values(self):
        response = self.client.get(reverse("workspace:sync"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "workspace/sync.html")
        self.assertEqual(response.context["user_id"], self.user.pk)
        self.assertEqual(response.context["session_key"], self.client.session.session_key)
        self.assertContains(response, str(self.user.pk))
        self.assertContains(response, self.client.session.session_key)

    def test_sync_page_requires_authentication(self):
        self.client.logout()

        response = self.client.get(reverse("workspace:sync"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(settings.LOGIN_URL, response.url)

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
