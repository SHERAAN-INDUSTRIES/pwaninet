from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from courses.models import Course, Year

User = get_user_model()


class DjangoAdminLinkTests(TestCase):
    """Test cases for Django Admin links inside vasto UI"""

    def setUp(self):
        self.course = Course.objects.create(name='Computer Science')
        self.year = Year.objects.create(course=self.course, level=1)

        self.staff_user = User.objects.create_user(
            username='staffadmin',
            email='staff@example.com',
            course=self.course,
            year=self.year,
            password='Password123!',
            is_staff=True,
            is_superuser=True,
            has_completed_onboarding=True,
        )

        self.regular_user = User.objects.create_user(
            username='regularstudent',
            email='student@example.com',
            course=self.course,
            year=self.year,
            password='Password123!',
            is_staff=False,
            has_completed_onboarding=True,
        )

        self.staff_client = Client()
        self.staff_client.force_login(self.staff_user)

        self.regular_client = Client()
        self.regular_client.force_login(self.regular_user)

    def test_settings_page_has_django_admin_for_staff(self):
        """Staff users should see Django Admin link in settings"""
        response = self.staff_client.get(reverse('users:settings'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('/admin/', content)
        self.assertIn('Django Admin', content)
        self.assertIn('data-bypass-htmx="true"', content)

    def test_settings_page_hides_django_admin_for_regular_user(self):
        """Regular users should not see Django Admin link in settings"""
        response = self.regular_client.get(reverse('users:settings'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn('Django Admin', content)

    def test_profile_page_has_django_admin_for_staff(self):
        """Staff user viewing own profile should see DJANGO ADMIN button"""
        response = self.staff_client.get(reverse('users:profile', args=[self.staff_user.username]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('DJANGO ADMIN', content)
        self.assertIn('/admin/', content)
        self.assertIn('data-bypass-htmx="true"', content)

    def test_profile_page_hides_django_admin_for_regular_user(self):
        """Regular user viewing own profile should not see DJANGO ADMIN button"""
        response = self.regular_client.get(reverse('users:profile', args=[self.regular_user.username]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn('DJANGO ADMIN', content)

    def test_admin_dashboard_has_django_admin_button(self):
        """Admin dashboard should have a Django Admin button in header"""
        response = self.staff_client.get(reverse('admin_dashboard:dashboard_home'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('Django Admin', content)
        self.assertIn('/admin/', content)
        self.assertIn('data-bypass-htmx="true"', content)

    def test_top_nav_and_left_rail_have_django_admin_for_staff(self):
        """Top navigation and desktop left rail should contain Django Admin links for staff"""
        response = self.staff_client.get(reverse('posts:home'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('/admin/', content)
        self.assertIn('Django Admin', content)

    def test_django_admin_base_template_has_return_to_vasto_link(self):
        """Django admin site should render vasto branding and Return to vasto link"""
        response = self.staff_client.get(reverse('admin:index'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('VASTO', content)
        self.assertIn('Return to vasto App', content)
        self.assertIn('Admin Dashboard', content)


class CommandCenterRouteTests(TestCase):
    """The command-center routes: staff reach them, nobody else does.

    Every new route repeats ``@login_required`` + ``@user_passes_test(is_admin)``
    (no middleware gate), so each route needs its own guard coverage.
    """

    def setUp(self):
        self.course = Course.objects.create(name='Computer Science')
        self.year = Year.objects.create(course=self.course, level=1)

        self.staff_user = User.objects.create_user(
            username='commandstaff',
            email='command@example.com',
            course=self.course,
            year=self.year,
            password='Password123!',
            is_staff=True,
            has_completed_onboarding=True,
        )
        self.regular_user = User.objects.create_user(
            username='commandstudent',
            email='command.student@example.com',
            course=self.course,
            year=self.year,
            password='Password123!',
            is_staff=False,
            has_completed_onboarding=True,
        )

        self.staff_client = Client()
        self.staff_client.force_login(self.staff_user)
        self.regular_client = Client()
        self.regular_client.force_login(self.regular_user)
        self.anon_client = Client()

    # -- staff reach every page, wrapped in the workspace shell -------------
    def test_staff_can_open_all_new_pages(self):
        for name in ('dashboard_home', 'ai_providers', 'ai_models', 'ai_usage'):
            with self.subTest(route=name):
                response = self.staff_client.get(reverse(f'admin_dashboard:{name}'))
                self.assertEqual(response.status_code, 200)
                content = response.content.decode()
                self.assertIn('admin-workspace', content)
                self.assertIn('adw-active', content)

    def test_staff_usage_page_accepts_day_ranges(self):
        for days in (7, 30, 90):
            with self.subTest(days=days):
                response = self.staff_client.get(
                    reverse('admin_dashboard:ai_usage'), {'days': days}
                )
                self.assertEqual(response.status_code, 200)
        # unknown ranges fall back to 30 rather than 500ing
        response = self.staff_client.get(
            reverse('admin_dashboard:ai_usage'), {'days': 'banana'}
        )
        self.assertEqual(response.status_code, 200)

    def test_new_pages_render_hx_navigation_partial(self):
        response = self.staff_client.get(
            reverse('admin_dashboard:ai_models'), HTTP_HX_REQUEST='true'
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # OOB rails + marker, no full page chrome
        self.assertIn('id="left-nav-rail"', content)
        self.assertIn('id="context-rail-target"', content)
        self.assertIn('id="admin-workspace"', content)
        self.assertIn('hx-swap-oob="true"', content)

    # -- non-staff are redirected per route --------------------------------
    def test_regular_user_is_redirected_on_every_new_route(self):
        for name in ('dashboard_home', 'ai_providers', 'ai_models', 'ai_usage'):
            with self.subTest(route=name):
                response = self.regular_client.get(reverse(f'admin_dashboard:{name}'))
                self.assertEqual(response.status_code, 302)
                self.assertIn('login', response['Location'])

    def test_anonymous_user_is_redirected_on_every_new_route(self):
        for name in ('dashboard_home', 'ai_providers', 'ai_models', 'ai_usage'):
            with self.subTest(route=name):
                response = self.anon_client.get(reverse(f'admin_dashboard:{name}'))
                self.assertEqual(response.status_code, 302)
                self.assertIn('login', response['Location'])

    def test_regular_user_cannot_post_toggle(self):
        response = self.regular_client.post(
            reverse('admin_dashboard:toggle_model'),
            {'provider': 'mock', 'model_id': 'x', 'active': '1'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response['Location'])

    def test_anonymous_user_cannot_post_toggle(self):
        response = self.anon_client.post(
            reverse('admin_dashboard:toggle_model'),
            {'provider': 'mock', 'model_id': 'x', 'active': '1'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response['Location'])

    def test_staff_can_reach_user_logins_and_admin_manager(self):
        for name in ('user_logins', 'admin_manager'):
            with self.subTest(route=name):
                response = self.staff_client.get(reverse(f'admin_dashboard:{name}'))
                self.assertEqual(response.status_code, 200)
                self.assertIn('admin-workspace', response.content.decode())

    def test_regular_user_is_redirected_on_user_logins_and_admin_manager(self):
        for name in ('user_logins', 'admin_manager'):
            with self.subTest(route=name):
                response = self.regular_client.get(reverse(f'admin_dashboard:{name}'))
                self.assertEqual(response.status_code, 302)
                self.assertIn('login', response['Location'])

    def test_anonymous_user_is_redirected_on_user_logins_and_admin_manager(self):
        for name in ('user_logins', 'admin_manager'):
            with self.subTest(route=name):
                response = self.anon_client.get(reverse(f'admin_dashboard:{name}'))
                self.assertEqual(response.status_code, 302)
                self.assertIn('login', response['Location'])

    def test_non_superuser_cannot_revoke_admin_sessions(self):
        self.course  # silence linters about unused setUp state
        target = User.objects.create_user(
            username='targetadmin',
            email='target@example.com',
            course=self.course,
            year=self.year,
            password='Password123!',
            is_staff=True,
            has_completed_onboarding=True,
        )
        response = self.staff_client.post(
            reverse('admin_dashboard:revoke_user_sessions', args=[target.id]),
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response['Location'])

    def test_superuser_revoke_removes_sessions_and_redirects(self):
        from users.models import UserSession

        superuser = User.objects.create_user(
            username='commandsuper',
            email='command.super@example.com',
            course=self.course,
            year=self.year,
            password='Password123!',
            is_staff=True,
            is_superuser=True,
            has_completed_onboarding=True,
        )
        super_client = Client()
        super_client.force_login(superuser)

        target = User.objects.create_user(
            username='revoke_target',
            email='revoke@example.com',
            course=self.course,
            year=self.year,
            password='Password123!',
            is_staff=True,
            has_completed_onboarding=True,
        )
        UserSession.objects.create(
            user=target,
            session_key='revoke-session-key-0001',
            ip_address='127.0.0.1',
            user_agent='UnitTestAgent/1.0',
            device_name='UnitTestDevice',
            device_type='desktop',
            browser='TestBrowser',
            operating_system='TestOS',
            location='Test City',
            is_current=True,
        )

        resp = super_client.post(
            reverse('admin_dashboard:revoke_user_sessions', args=[target.id]),
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(UserSession.objects.filter(user=target).exists())

    # -- the kill switch itself ---------------------------------------------
    def test_toggle_requires_post(self):
        response = self.staff_client.get(reverse('admin_dashboard:toggle_model'))
        self.assertEqual(response.status_code, 400)

    def test_toggle_flips_registered_model(self):
        from pwanimate.billing.models import ModelOffering

        offering = ModelOffering.objects.create(
            key='mock:unit-test-model',
            provider='mock',
            model_id='unit-test-model',
            display_name='Unit Test Model',
        )
        self.assertTrue(offering.is_active)

        url = reverse('admin_dashboard:toggle_model')

        # disable
        response = self.staff_client.post(
            url, {'provider': 'mock', 'model_id': 'unit-test-model', 'active': '0'}
        )
        self.assertEqual(response.status_code, 302)
        offering.refresh_from_db()
        self.assertFalse(offering.is_active)

        # the switch publishes an audit event through the platform registry
        from notifications.models import PlatformEvent
        self.assertTrue(
            PlatformEvent.objects.filter(
                event_type='admin.model.toggled', target_id='mock:unit-test-model'
            ).exists()
        )

        # re-enable
        response = self.staff_client.post(
            url, {'provider': 'mock', 'model_id': 'unit-test-model', 'active': '1'}
        )
        self.assertEqual(response.status_code, 302)
        offering.refresh_from_db()
        self.assertTrue(offering.is_active)

    def test_toggle_hx_request_rerenders_models_content(self):
        from pwanimate.billing.models import ModelOffering

        offering = ModelOffering.objects.create(
            key='mock:hx-toggle-model', provider='mock', model_id='hx-toggle-model'
        )
        response = self.staff_client.post(
            reverse('admin_dashboard:toggle_model'),
            {'provider': 'mock', 'model_id': 'hx-toggle-model', 'active': '0'},
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('id="admin-workspace"', content)
        self.assertIn('Disabled', content)
        offering.refresh_from_db()
        self.assertFalse(offering.is_active)

    def test_toggle_refuses_unregistered_model(self):
        from pwanimate.billing.models import ModelOffering

        response = self.staff_client.post(
            reverse('admin_dashboard:toggle_model'),
            {'provider': 'mock', 'model_id': 'never-registered', 'active': '1'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            ModelOffering.objects.filter(
                provider='mock', model_id='never-registered'
            ).exists()
        )

    def test_models_page_marks_unregistered_models_not_toggleable(self):
        response = self.staff_client.get(reverse('admin_dashboard:ai_models'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('seed_model_registry', content)
