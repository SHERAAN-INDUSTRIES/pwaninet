"""
Phase D Test Suite: Pwanimate Student-Facing Settings UI.

Tests coverage:
1. Authentication & Route Accessibility (302 redirects vs 200 OK)
2. Routing & HTMX Target Negotiation:
   - Desktop modal body swap (#pwanimate-settings-modal-body)
   - Mobile page swap (#page-content-target)
   - Mobile tab swap (#pwanimate-settings-mobile-content)
   - Full non-HTMX GET loads (settings/index.html)
3. Personalization Defaults and Rendering (nickname, tone, style, instructions)
4. Personalization Form Submissions (POST):
   - Valid updates persist to PwanimatePreferences
   - Success alert rendered
   - Decoupled from Vasto User model (username, email unchanged)
5. Validation Errors:
   - Invalid tone choice
   - Invalid response style choice
   - Nickname > 50 characters
   - Personal instructions > 500 characters
   - Input preserved on error
6. End-to-End UserContext Integration:
   - Preference saved in Settings UI immediately reflects in UserContextService.build()
7. Usage & About Tab Boundaries:
   - No mock billing, tokens, or pricing
   - Accurate preparation notices and privacy disclaimers
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from pwanimate.context import UserContextService
from pwanimate.models import (
    PwanimatePreferences,
    PwanimateResponseStyle,
    PwanimateTone,
)
from pwanimate.services.preferences import PwanimatePreferenceService

User = get_user_model()


class PwanimateSettingsAuthTests(TestCase):
    """Verify authentication enforcement across all Pwanimate Settings endpoints."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="student_mwangi",
            email="mwangi@Vasto.local",
            password="testpassword123",
            first_name="Mwangi",
            last_name="Kamau",
        )

    def test_unauthenticated_requests_redirect_to_login(self):
        """Unauthenticated requests to any settings URL must redirect to login."""
        urls = [
            reverse("pwanimate:settings"),
            reverse("pwanimate:settings_personalization"),
            reverse("pwanimate:settings_usage"),
            reverse("pwanimate:settings_about"),
        ]
        for url in urls:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302, f"URL {url} did not redirect unauthenticated user")
            self.assertIn("/accounts/login/", resp.url)

        # POST to personalization must also redirect to login
        post_resp = self.client.post(reverse("pwanimate:settings_personalization"), {"nickname": "Hacker"})
        self.assertEqual(post_resp.status_code, 302)
        self.assertIn("/accounts/login/", post_resp.url)

    def test_settings_root_redirects_to_personalization(self):
        """Authenticated GET /pwanimate/settings/ redirects to personalization tab."""
        self.client.force_login(self.user)
        resp = self.client.get(reverse("pwanimate:settings"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("pwanimate:settings_personalization"))


class PwanimateSettingsHTMXNegotiationTests(TestCase):
    """Verify HTMX target-based rendering (desktop modal vs mobile page vs full load)."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="student_mwangi",
            email="mwangi@Vasto.local",
            password="testpassword123",
            first_name="Mwangi",
        )
        self.client.force_login(self.user)

    def test_desktop_modal_personalization_swap(self):
        """Desktop modal tab click swaps only personalization partial into modal body."""
        resp = self.client.get(
            reverse("pwanimate:settings_personalization"),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="pwanimate-settings-modal-body",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "pwanimate/settings/partials/personalization_content.html")
        self.assertTemplateNotUsed(resp, "pwanimate/settings/partials/settings_mobile_page.html")
        self.assertTemplateNotUsed(resp, "pwanimate/settings/index.html")
        self.assertContains(resp, 'id="pwanimate-personalization-form"')
        self.assertContains(resp, "Conversational Tone")

    def test_desktop_modal_usage_swap(self):
        """Desktop modal tab click swaps usage partial into modal body."""
        resp = self.client.get(
            reverse("pwanimate:settings_usage"),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="pwanimate-settings-modal-body",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "pwanimate/settings/partials/usage_content.html")
        self.assertContains(resp, "Usage & Activity")
        # The real dashboard: the balance, the token counters and the upgrade
        # path (the "in preparation" placeholder is only the no-data fallback).
        self.assertContains(resp, "Tokens")
        self.assertContains(resp, "Prompts today")
        self.assertContains(resp, "See upgrade options")

    def test_desktop_modal_about_swap(self):
        """Desktop modal tab click swaps about partial into modal body."""
        resp = self.client.get(
            reverse("pwanimate:settings_about"),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="pwanimate-settings-modal-body",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "pwanimate/settings/partials/about_content.html")
        self.assertContains(resp, "What is Pwanimate?")
        self.assertContains(resp, "Document Retrieval & Citations")

    def test_mobile_page_swap_into_page_content_target(self):
        """Mobile navigation swaps complete mobile settings page into #page-content-target."""
        resp = self.client.get(
            reverse("pwanimate:settings_personalization"),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="page-content-target",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "pwanimate/settings/partials/settings_mobile_page.html")
        self.assertTemplateUsed(resp, "pwanimate/settings/partials/personalization_content.html")
        self.assertContains(resp, 'class="pwanimate-workspace pwanimate-settings-mobile"')
        self.assertContains(resp, "Back to Chat")
        self.assertContains(resp, 'id="pwanimate-mobile-tabs"')

    def test_mobile_tab_swapping(self):
        """Switching tabs on mobile swaps only the content pane #pwanimate-settings-mobile-content."""
        # Usage tab
        resp_usage = self.client.get(
            reverse("pwanimate:settings_usage"),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="pwanimate-settings-mobile-content",
        )
        self.assertEqual(resp_usage.status_code, 200)
        self.assertTemplateUsed(resp_usage, "pwanimate/settings/partials/usage_content.html")
        self.assertTemplateNotUsed(resp_usage, "pwanimate/settings/partials/settings_mobile_page.html")

        # About tab
        resp_about = self.client.get(
            reverse("pwanimate:settings_about"),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="pwanimate-settings-mobile-content",
        )
        self.assertEqual(resp_about.status_code, 200)
        self.assertTemplateUsed(resp_about, "pwanimate/settings/partials/about_content.html")
        self.assertTemplateNotUsed(resp_about, "pwanimate/settings/partials/settings_mobile_page.html")

    def test_full_page_load_without_htmx(self):
        """Direct browser navigation renders full standalone template."""
        resp = self.client.get(reverse("pwanimate:settings_personalization"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "pwanimate/settings/index.html")
        self.assertTemplateUsed(resp, "pwanimate/settings/partials/settings_mobile_page.html")
        self.assertTemplateNotUsed(resp, "pwanimate/settings/partials/settings_modal.html")


class PwanimatePersonalizationFormTests(TestCase):
    """Verify form rendering, safe defaults, submissions, validation, and error states."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="student_mwangi",
            email="mwangi@Vasto.local",
            password="testpassword123",
            first_name="Mwangi",
            last_name="Kamau",
        )
        self.client.force_login(self.user)

    def test_default_values_rendering(self):
        """User without configured preferences sees expected default choices and empty inputs."""
        resp = self.client.get(
            reverse("pwanimate:settings_personalization"),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="pwanimate-settings-modal-body",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="nickname"')
        self.assertContains(resp, 'maxlength="50"')
        self.assertContains(resp, 'value=""')
        self.assertContains(resp, 'value="neutral" checked')
        self.assertContains(resp, 'value="balanced" checked')
        self.assertContains(resp, 'id="pwanimate-char-count">0</span>')
        self.assertContains(resp, "do not override university policies")

    def test_persisted_values_rendering(self):
        """User with existing preferences sees their configured values loaded into the form."""
        PwanimatePreferences.objects.create(
            user=self.user,
            nickname="Mwangi K.",
            tone=PwanimateTone.ACADEMIC,
            response_style=PwanimateResponseStyle.DETAILED,
            personal_instructions="Prioritize research papers and formal theorems.",
        )

        resp = self.client.get(
            reverse("pwanimate:settings_personalization"),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="pwanimate-settings-modal-body",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'value="Mwangi K."')
        self.assertContains(resp, 'value="academic" checked')
        self.assertContains(resp, 'value="detailed" checked')
        self.assertContains(resp, "Prioritize research papers and formal theorems.")
        self.assertContains(resp, f'>{len("Prioritize research papers and formal theorems.")}</span> / 500')

    def test_successful_form_submission_persists_preferences(self):
        """Valid POST updates preferences, returns success banner, and leaves User model intact."""
        post_data = {
            "nickname": "Captain Mwangi",
            "tone": PwanimateTone.FRIENDLY,
            "response_style": PwanimateResponseStyle.CONCISE,
            "personal_instructions": "Focus on concise Python code snippets.",
        }

        resp = self.client.post(
            reverse("pwanimate:settings_personalization"),
            data=post_data,
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Saved!")
        self.assertContains(resp, "Your Pwanimate preferences have been updated.")
        self.assertContains(resp, 'value="Captain Mwangi"')
        self.assertContains(resp, 'value="friendly" checked')
        self.assertContains(resp, 'value="concise" checked')
        self.assertContains(resp, "Focus on concise Python code snippets.")

        # Database verification
        prefs = PwanimatePreferences.objects.get(user=self.user)
        self.assertEqual(prefs.nickname, "Captain Mwangi")
        self.assertEqual(prefs.tone, PwanimateTone.FRIENDLY)
        self.assertEqual(prefs.response_style, PwanimateResponseStyle.CONCISE)
        self.assertEqual(prefs.personal_instructions, "Focus on concise Python code snippets.")

        # Decoupled verification: User model unchanged
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "student_mwangi")
        self.assertEqual(self.user.first_name, "Mwangi")
        self.assertEqual(self.user.last_name, "Kamau")

    def test_validation_error_invalid_tone(self):
        """Submitting an invalid tone choice returns validation error message."""
        post_data = {
            "nickname": "Mwangi",
            "tone": "sarcastic",
            "response_style": PwanimateResponseStyle.BALANCED,
            "personal_instructions": "Normal instructions",
        }
        resp = self.client.post(
            reverse("pwanimate:settings_personalization"),
            data=post_data,
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Please correct the following errors")
        self.assertContains(resp, "Invalid tone")
        self.assertContains(resp, "Allowed choices")

    def test_validation_error_invalid_response_style(self):
        """Submitting an invalid response style returns validation error message."""
        post_data = {
            "nickname": "Mwangi",
            "tone": PwanimateTone.NEUTRAL,
            "response_style": "super_long",
            "personal_instructions": "Normal instructions",
        }
        resp = self.client.post(
            reverse("pwanimate:settings_personalization"),
            data=post_data,
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Please correct the following errors")
        self.assertContains(resp, "Invalid response style")
        self.assertContains(resp, "Allowed choices")

    def test_validation_error_nickname_too_long(self):
        """Submitting nickname > 50 characters returns validation error."""
        post_data = {
            "nickname": "M" * 51,
            "tone": PwanimateTone.NEUTRAL,
            "response_style": PwanimateResponseStyle.BALANCED,
        }
        resp = self.client.post(
            reverse("pwanimate:settings_personalization"),
            data=post_data,
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Nickname cannot exceed 50 characters.")

    def test_validation_error_personal_instructions_too_long_preserves_input(self):
        """Submitting instructions > 500 characters returns validation error and preserves entered text."""
        excessive_instructions = "I" * 501
        post_data = {
            "nickname": "Mwangi",
            "tone": PwanimateTone.NEUTRAL,
            "response_style": PwanimateResponseStyle.BALANCED,
            "personal_instructions": excessive_instructions,
        }
        resp = self.client.post(
            reverse("pwanimate:settings_personalization"),
            data=post_data,
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Personal instructions cannot exceed 500 characters.")
        # Preserves user input in the textarea so effort is not lost
        self.assertContains(resp, excessive_instructions)


class PwanimateSettingsEndToEndIntegrationTests(TestCase):
    """Verify that preference updates made through the settings UI flow directly into UserContext."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="student_mwangi",
            password="testpassword123",
            first_name="Mwangi",
        )
        self.client.force_login(self.user)

    def test_settings_ui_update_immediately_reflects_in_user_context(self):
        """Saving preferences via Settings POST immediately updates UserContextService output."""
        post_data = {
            "nickname": "Dr. Mwangi",
            "tone": PwanimateTone.PROFESSIONAL,
            "response_style": PwanimateResponseStyle.DETAILED,
            "personal_instructions": "Cite primary academic references whenever answering.",
        }
        resp = self.client.post(
            reverse("pwanimate:settings_personalization"),
            data=post_data,
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)

        # Build context directly
        user_ctx = UserContextService().build(self.user)
        self.assertEqual(user_ctx.nickname, "Dr. Mwangi")
        self.assertEqual(user_ctx.tone, "professional")
        self.assertEqual(user_ctx.response_style, "detailed")
        self.assertEqual(user_ctx.personal_instructions, "Cite primary academic references whenever answering.")

        # Check prompt context block
        prompt_block = user_ctx.format_context_block()
        self.assertIn("- Nickname: Dr. Mwangi", prompt_block)
        self.assertIn("- Tone: The user prefers a professional tone.", prompt_block)
        self.assertIn("- Response Style: The user prefers detailed responses.", prompt_block)
        self.assertIn("- Personal Instructions: Cite primary academic references whenever answering.", prompt_block)


class PwanimateSettingsProductBoundaryTests(TestCase):
    """Verify Usage and About tabs maintain strict product boundaries without fake pricing/billing."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="student_mwangi",
            password="testpassword123",
        )
        self.client.force_login(self.user)

    def test_usage_tab_shows_real_counters_not_fabricated_numbers(self):
        """The Usage tab renders the REAL counters (tier, balance, tokens).

        The old tab said "in preparation" because there was nothing to show.
        Now the numbers come from the usage ledger, so the test asserts they
        are data-driven instead of invented — and that nothing about card
        billing or dollar prices sneaks in.
        """
        resp = self.client.get(
            reverse("pwanimate:settings_usage"),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="pwanimate-settings-modal-body",
        )
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8").lower()

        # The fair-use statement stays.
        self.assertIn("acceptable academic use", content)

        # No card-based billing language, no dollar prices.
        self.assertNotIn("credit card", content)
        self.assertNotIn("$", content)

        # Real usage, taken from the ledger, is on the page.
        from pwanimate.billing.services import UsageService

        UsageService.record_turn(
            user=self.user,
            provider="gemini",
            model_id="gemini-3.8-flash",
            prompt_tokens=1234,
            completion_tokens=56,
            total_tokens=1290,
        )
        resp = self.client.get(
            reverse("pwanimate:settings_usage"),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="pwanimate-settings-modal-body",
        )
        content = resp.content.decode("utf-8")
        self.assertIn("1234", content)   # prompts
        self.assertIn("56", content)     # answers

    def test_about_tab_describes_pwanimate_capabilities_honestly(self):
        """About tab provides educational, grounded context on Pwanimate capabilities and safety."""
        resp = self.client.get(
            reverse("pwanimate:settings_about"),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="pwanimate-settings-modal-body",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Document Retrieval & Citations")
        self.assertContains(resp, "Student Peer Discovery")
        self.assertContains(resp, "Campus & Group Awareness")
        self.assertContains(resp, "Personalized Preferences")
        self.assertContains(resp, "Academic Boundaries & Privacy")


class PwanimateSettingsNavigationAndHeaderTests(TestCase):
    """Verify simplified navigation (single Settings item, no Vasto Settings) and clean headers."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="student_mwangi",
            password="testpassword123",
            first_name="Mwangi",
            last_name="Kamau",
        )
        self.client.force_login(self.user)

    def test_left_rail_contains_single_settings_link_and_no_Vasto_settings(self):
        """Desktop rail contains single Settings item with gear icon and no Vasto Settings."""
        resp = self.client.get(reverse("pwanimate:index"))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")

        # Must have Settings with gear icon
        self.assertIn("bi-gear", content)
        self.assertIn("Settings", content)

        # Must NOT have separate Vasto Settings
        self.assertNotIn("Vasto Settings", content)

    def test_mobile_menu_contains_single_settings_link_and_no_Vasto_settings(self):
        """Mobile offcanvas menu contains single Settings item and no Vasto Settings."""
        resp = self.client.get(reverse("pwanimate:index"))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertNotIn("Vasto Settings", content)

    def test_modal_header_is_clean_settings(self):
        """Desktop modal header renders 'Settings' with close filter."""
        resp = self.client.get(reverse("pwanimate:index"))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertIn('id="pwanimateSettingsModalTitle"', content)
        self.assertIn("Settings", content)
        self.assertIn("--pwanimate-close-filter", content)

    def test_mobile_page_header_is_clean_settings(self):
        """Mobile page header renders 'Settings'."""
        resp = self.client.get(
            reverse("pwanimate:settings_personalization"),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="page-content-target",
        )
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertIn("Back to Chat", content)
        self.assertIn("Settings", content)
        self.assertNotIn("Pwanimate Settings", content)

    def test_save_button_loading_and_success_states(self):
        """Save button has hx-disabled-elt, spinner, and transitions from Save Preferences to Saved."""
        # Initial GET
        resp = self.client.get(
            reverse("pwanimate:settings_personalization"),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="pwanimate-settings-modal-body",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'hx-disabled-elt="#pwanimate-save-pref-btn"')
        self.assertContains(resp, 'id="pwanimate-save-pref-btn"')
        self.assertContains(resp, 'btn-primary')
        self.assertContains(resp, 'Save Preferences')
        self.assertContains(resp, 'pwanimate-save-spinner')

        # Successful POST
        post_data = {
            "nickname": "Mwangi",
            "tone": PwanimateTone.NEUTRAL,
            "response_style": PwanimateResponseStyle.BALANCED,
            "personal_instructions": "Hello",
        }
        resp_post = self.client.post(
            reverse("pwanimate:settings_personalization"),
            data=post_data,
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp_post.status_code, 200)
        self.assertContains(resp_post, 'btn-success')
        self.assertContains(resp_post, 'Saved')

        # Failed POST
        resp_fail = self.client.post(
            reverse("pwanimate:settings_personalization"),
            data={"nickname": "Mwangi", "tone": "invalid_tone"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp_fail.status_code, 200)
        self.assertContains(resp_fail, 'btn-primary')
        self.assertContains(resp_fail, 'Save Preferences')

    def test_usage_conversations_count(self):
        """Usage tab accurately counts active threads for the user."""
        from pwanimate.models import PwanimateConversation
        PwanimateConversation.objects.create(user=self.user, title="Thread 1")
        PwanimateConversation.objects.create(user=self.user, title="Thread 2")

        resp = self.client.get(
            reverse("pwanimate:settings_usage"),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="pwanimate-settings-modal-body",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "2 threads")
        self.assertContains(resp, "Mwangi Kamau")

    def test_usage_profile_picture_rendering(self):
        """Usage tab renders authentic student profile picture with fallback."""
        resp = self.client.get(
            reverse("pwanimate:settings_usage"),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="pwanimate-settings-modal-body",
        )
        self.assertEqual(resp.status_code, 200)
        # Verify an img tag is rendered with default_pic1.jpg (either as src or fallback in onerror)
        self.assertContains(resp, "default_pic1.jpg")
        self.assertContains(resp, "rounded-circle")


class PwanimateVersionAndSettingsRenderingTests(TestCase):
    """Verify dynamic subsystem versioning and mobile scrollable container markup."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="student_mwangi",
            email="mwangi@Vasto.local",
            password="testpassword123",
            first_name="Mwangi",
            last_name="Kamau",
        )
        self.client.force_login(self.user)

    def tearDown(self):
        import os
        from pwanimate.version import clear_version_cache
        os.environ.pop("PWANIMATE_VERSION", None)
        clear_version_cache()

    def test_version_resolution_default(self):
        """Default version resolves to 2.0.0."""
        from pwanimate.version import clear_version_cache, get_version
        clear_version_cache()
        self.assertEqual(get_version(), "2.0.0")

    def test_version_resolution_env_override(self):
        """PWANIMATE_VERSION environment variable overrides default."""
        import os
        from pwanimate.version import clear_version_cache, get_version
        os.environ["PWANIMATE_VERSION"] = "2.5.1"
        clear_version_cache()
        self.assertEqual(get_version(), "2.5.1")

    def test_about_view_renders_dynamic_version(self):
        """About view context contains pwanimate_version and HTML renders dynamic badge."""
        import os
        from pwanimate.version import clear_version_cache
        os.environ["PWANIMATE_VERSION"] = "2.3.0-rc1"
        clear_version_cache()

        resp = self.client.get(
            reverse("pwanimate:settings_about"),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="pwanimate-settings-modal-body",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context.get("pwanimate_version"), "2.3.0-rc1")
        self.assertContains(resp, "Version 2.3.0-rc1")
        self.assertContains(resp, "pwanimate-about-version-badge")

    def test_index_view_context_includes_pwanimate_version(self):
        """Index view context includes resolved pwanimate_version."""
        resp = self.client.get(reverse("pwanimate:index"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("pwanimate_version", resp.context)
        self.assertTrue(len(resp.context["pwanimate_version"]) > 0)

    def test_mobile_settings_page_scrollable_markup(self):
        """Mobile settings page renders scrollable classes and safe-area container."""
        resp = self.client.get(
            reverse("pwanimate:settings_personalization"),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="#page-content-target",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "pwanimate-workspace pwanimate-settings-mobile")
        self.assertContains(resp, "pwanimate-settings-mobile-container")

