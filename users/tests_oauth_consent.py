"""Regression coverage for the standard Django OAuth Toolkit consent flow."""
from datetime import timedelta
from urllib.parse import parse_qs, urlencode, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from oauth2_provider.models import AccessToken, Application


User = get_user_model()
REDIRECT_URI = 'http://localhost:8001/api/auth/vasto/callback/'
REQUESTED_SCOPES = 'openid profile email'


class WorkspacesOAuthConsentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='oauth_consent_user',
            email='oauth_consent_user@example.com',
            password='ConsentPass123!',
        )
        self.application = Application.objects.create(
            user=self.user,
            name='Vasto Workspaces Consent Test',
            client_type=Application.CLIENT_CONFIDENTIAL,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris=REDIRECT_URI,
            skip_authorization=False,
            algorithm=Application.RS256_ALGORITHM,
        )
        self.authorization_params = {
            'client_id': self.application.client_id,
            'redirect_uri': REDIRECT_URI,
            'response_type': 'code',
            'scope': REQUESTED_SCOPES,
            'state': 'workspaces-state-test',
            'nonce': 'workspaces-nonce-test',
            'code_challenge': 'A' * 43,
            'code_challenge_method': 'S256',
        }
        self.authorization_url = '/o/authorize/?' + urlencode(self.authorization_params)

    def test_first_time_password_login_returns_to_standard_consent_and_issues_code(self):
        login_redirect = self.client.get(self.authorization_url)
        self.assertEqual(login_redirect.status_code, 302)
        login_query = parse_qs(urlparse(login_redirect.url).query)
        self.assertEqual(login_query['next'], [self.authorization_url])

        login_page = self.client.get(login_redirect.url)
        self.assertEqual(login_page.status_code, 200)
        self.assertEqual(login_page.context['next'], self.authorization_url)

        login_response = self.client.post(reverse('login'), {
            'username': 'oauth_consent_user',
            'password': 'ConsentPass123!',
            'next': self.authorization_url,
        })
        self.assertRedirects(login_response, self.authorization_url, fetch_redirect_response=False)

        consent = self.client.get(self.authorization_url)
        self.assertEqual(consent.status_code, 200)
        self.assertTemplateUsed(consent, 'workspace/sync.html')
        self.assertContains(consent, 'Authorize Vasto Workspace?')
        self.assertContains(consent, 'Authorize and continue')
        self.assertContains(consent, 'name="allow"')

        approval = self.client.post('/o/authorize/', {
            **self.authorization_params,
            'allow': 'on',
        })
        self.assertEqual(approval.status_code, 302)
        callback_query = parse_qs(urlparse(approval.url).query)
        self.assertIn('code', callback_query)
        self.assertEqual(callback_query['state'], ['workspaces-state-test'])
        self.assertEqual(urlparse(approval.url).path, urlparse(REDIRECT_URI).path)

    def test_account_creation_preserves_pending_authorization_through_login(self):
        login_redirect = self.client.get(self.authorization_url)
        login_page = self.client.get(login_redirect.url)
        self.assertEqual(login_page.status_code, 200)
        self.assertContains(
            login_page,
            reverse('users:register') + '?next=' + urlencode({'next': self.authorization_url})['next'],
        )

        signup_url = reverse('users:register') + '?' + urlencode({'next': self.authorization_url})
        signup_page = self.client.get(signup_url)
        self.assertEqual(signup_page.status_code, 200)
        self.assertEqual(signup_page.context['next'], self.authorization_url)

        signup = self.client.post(reverse('users:register'), {
            'username': 'new_oauth_consent_user',
            'password1': 'NewConsentPass123!',
            'password2': 'NewConsentPass123!',
            'next': self.authorization_url,
        })
        self.assertEqual(signup.status_code, 302)
        login_query = parse_qs(urlparse(signup.url).query)
        self.assertEqual(login_query['next'], [self.authorization_url])

        login = self.client.post(reverse('login'), {
            'username': 'new_oauth_consent_user',
            'password': 'NewConsentPass123!',
            'next': login_query['next'][0],
        })
        self.assertRedirects(login, self.authorization_url, fetch_redirect_response=False)

        consent = self.client.get(self.authorization_url)
        self.assertEqual(consent.status_code, 200)
        self.assertTemplateUsed(consent, 'workspace/sync.html')
        self.assertContains(consent, 'Authorize Vasto Workspace?')

    def test_unexpired_matching_grant_uses_remembered_consent_under_auto_policy(self):
        self.client.force_login(self.user)
        AccessToken.objects.create(
            user=self.user,
            application=self.application,
            token='test-access-token-for-consent-policy',
            expires=timezone.now() + timedelta(hours=1),
            scope=REQUESTED_SCOPES,
        )

        response = self.client.get(self.authorization_url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlparse(response.url).path, urlparse(REDIRECT_URI).path)
        self.assertIn('code', parse_qs(urlparse(response.url).query))

    def test_workspaces_application_requires_explicit_consent_for_new_grant(self):
        self.assertFalse(self.application.skip_authorization)
        self.assertEqual(
            __import__('django.conf', fromlist=['settings']).settings.OAUTH2_PROVIDER[
                'REQUEST_APPROVAL_PROMPT'
            ],
            'auto',
        )
