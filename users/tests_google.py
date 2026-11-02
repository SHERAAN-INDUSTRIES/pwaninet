"""Tests for website Google sign-in (GIS ID-token flow, Phases 1-3)."""
import json
import time
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import TestCase, Client, TransactionTestCase, override_settings
from django.urls import reverse

from users.models import GoogleAccount, User

TEST_CLIENT_ID = 'test-google-web-client-id.apps.googleusercontent.com'
GOOGLE_OVERRIDE = {
    'GOOGLE_WEB_CLIENT_ID': TEST_CLIENT_ID,
    'GOOGLE_LOGIN_ENABLED': True,
    'GOOGLE_ALLOWED_HD': '',
}


def _rsa_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    numbers = key.public_key().public_numbers()
    import base64

    def _b64(n, length):
        return base64.urlsafe_b64encode(n.to_bytes(length, 'big')).rstrip(b'=').decode()

    jwk = {
        'kty': 'RSA',
        'kid': 'test-kid-1',
        'use': 'sig',
        'alg': 'RS256',
        'n': _b64(numbers.n, 256),
        'e': _b64(numbers.e, 3),
    }
    return private_pem, {'keys': [jwk]}


PRIVATE_PEM, JWKS = _rsa_keypair()
OTHER_PRIVATE_PEM, _ = _rsa_keypair()


def mint_token(sub='google-sub-123', email='guser@example.com', audience=TEST_CLIENT_ID,
               issuer='https://accounts.google.com', expired=False, key=PRIVATE_PEM,
               kid='test-kid-1', extra=None):
    now = int(time.time())
    payload = {
        'iss': issuer,
        'aud': audience,
        'sub': sub,
        'email': email,
        'email_verified': True,
        'given_name': 'Gina',
        'family_name': 'User',
        'exp': now - 60 if expired else now + 3600,
        'iat': now - 60,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, key, algorithm='RS256', headers={'kid': kid})


def mock_jwks():
    return patch('users.services.google_auth_service._get_jwks', return_value=JWKS)


@override_settings(**GOOGLE_OVERRIDE)
class GoogleVerificationServiceTest(TestCase):
    def test_valid_token_returns_claims(self):
        from users.services.google_auth_service import verify_google_id_token

        with mock_jwks():
            claims = verify_google_id_token(mint_token())
        self.assertEqual(claims['sub'], 'google-sub-123')
        self.assertEqual(claims['email'], 'guser@example.com')

    def test_expired_token_rejected(self):
        from users.services.google_auth_service import GoogleVerificationError, verify_google_id_token

        with mock_jwks(), self.assertRaises(GoogleVerificationError) as ctx:
            verify_google_id_token(mint_token(expired=True))
        self.assertEqual(ctx.exception.code, 'expired')

    def test_wrong_audience_rejected(self):
        from users.services.google_auth_service import GoogleVerificationError, verify_google_id_token

        with mock_jwks(), self.assertRaises(GoogleVerificationError) as ctx:
            verify_google_id_token(mint_token(audience='other-client.apps.googleusercontent.com'))
        self.assertEqual(ctx.exception.code, 'wrong_audience')

    def test_wrong_issuer_rejected(self):
        from users.services.google_auth_service import GoogleVerificationError, verify_google_id_token

        with mock_jwks(), self.assertRaises(GoogleVerificationError) as ctx:
            verify_google_id_token(mint_token(issuer='https://evil.example.com'))
        self.assertIn(ctx.exception.code, ('wrong_issuer', 'invalid_token'))

    def test_tampered_signature_rejected(self):
        from users.services.google_auth_service import GoogleVerificationError, verify_google_id_token

        with mock_jwks(), self.assertRaises(GoogleVerificationError) as ctx:
            verify_google_id_token(mint_token(key=OTHER_PRIVATE_PEM))
        self.assertEqual(ctx.exception.code, 'invalid_token')

    def test_unknown_key_rejected(self):
        from users.services.google_auth_service import GoogleVerificationError, verify_google_id_token

        with mock_jwks(), self.assertRaises(GoogleVerificationError) as ctx:
            verify_google_id_token(mint_token(kid='unknown-kid'))
        self.assertEqual(ctx.exception.code, 'unknown_key')

    @override_settings(GOOGLE_ALLOWED_HD='school.edu', **{k: v for k, v in GOOGLE_OVERRIDE.items() if k != 'GOOGLE_ALLOWED_HD'})
    def test_hosted_domain_restriction(self):
        from users.services.google_auth_service import GoogleVerificationError, verify_google_id_token

        with mock_jwks():
            with self.assertRaises(GoogleVerificationError) as ctx:
                verify_google_id_token(mint_token(extra={'hd': 'other.edu'}))
            self.assertEqual(ctx.exception.code, 'wrong_domain')
            claims = verify_google_id_token(mint_token(extra={'hd': 'school.edu'}))
            self.assertEqual(claims['hd'], 'school.edu')


@override_settings(**GOOGLE_OVERRIDE)
class GoogleCompleteViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = reverse('users:google_complete')

    def _complete(self, token):
        with mock_jwks():
            return self.client.post(
                self.url, data=json.dumps({'credential': token}),
                content_type='application/json',
            )

    def test_new_user_created_and_logged_in(self):
        response = self._complete(mint_token(sub='new-sub-1', email='brandnew@example.com'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'ok')

        user = User.objects.get(email__iexact='brandnew@example.com')
        self.assertFalse(user.has_usable_password())
        self.assertTrue(user.email_verified)
        self.assertFalse(user.has_completed_onboarding)
        link = GoogleAccount.objects.get(sub='new-sub-1')
        self.assertEqual(link.user_id, user.id)
        self.assertEqual(link.link_method, 'new_signup')
        self.assertIsNotNone(link.last_login_at)
        # Session is authenticated
        self.assertIn('_auth_user_id', self.client.session)

    def test_existing_sub_logs_in_without_new_user(self):
        user = User.objects.create_user(username='guser1', email='g1@example.com', password='x')
        user.set_unusable_password()
        user.save()
        GoogleAccount.objects.create(user=user, sub='known-sub', email='g1@example.com',
                                     email_verified=True)
        before = User.objects.count()
        response = self._complete(mint_token(sub='known-sub', email='g1@example.com'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')
        self.assertEqual(User.objects.count(), before)
        self.assertEqual(str(self.client.session['_auth_user_id']), str(user.id))

    def test_invalid_token_rejected(self):
        response = self._complete('not-a-jwt')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['status'], 'error')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_expired_token_rejected(self):
        response = self._complete(mint_token(expired=True))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 'expired')

    def test_wrong_audience_rejected(self):
        response = self._complete(mint_token(audience='other-client'))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 'wrong_audience')

    def test_disabled_google_login_returns_403(self):
        with override_settings(GOOGLE_LOGIN_ENABLED=False):
            response = self._complete(mint_token())
        self.assertEqual(response.status_code, 403)

    def test_email_collision_requires_link_not_merge(self):
        owner = User.objects.create_user(username='victim', email='taken@example.com',
                                         password='VictimPass123!')
        response = self._complete(mint_token(sub='attacker-sub', email='taken@example.com'))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['status'], 'link_required')
        # No new account, no GoogleAccount, nobody logged in
        self.assertFalse(User.objects.filter(email__iexact='taken@example.com').exclude(pk=owner.pk).exists())
        self.assertFalse(GoogleAccount.objects.filter(sub='attacker-sub').exists())
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_new_google_user_hits_onboarding_gate(self):
        self._complete(mint_token(sub='onboard-sub', email='onboard@example.com'))
        response = self.client.get(reverse('posts:home'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('users:onboarding'), response.url)

    def test_inactive_linked_account_rejected(self):
        user = User.objects.create_user(username='inactive1', email='in1@example.com', password='x')
        user.is_active = False
        user.save()
        GoogleAccount.objects.create(user=user, sub='inactive-sub', email='in1@example.com')
        response = self._complete(mint_token(sub='inactive-sub', email='in1@example.com'))
        self.assertEqual(response.status_code, 403)

    def test_legacy_password_login_preserved(self):
        User.objects.create_user(username='legacyuser', email='legacy@example.com',
                                 password='LegacyPass123!')
        response = self.client.post(reverse('login'), {
            'username': 'legacyuser', 'password': 'LegacyPass123!'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('_auth_user_id', self.client.session)


@override_settings(**GOOGLE_OVERRIDE)
class GoogleLinkViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(username='owner1', email='owner1@example.com',
                                              password='OwnerPass123!')

    def _start_collision(self, sub='link-sub-1'):
        with mock_jwks():
            response = self.client.post(
                reverse('users:google_complete'),
                data=json.dumps({'credential': mint_token(sub=sub, email='owner1@example.com')}),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 409)
        return sub

    def _link(self, payload):
        return self.client.post(reverse('users:google_link'), data=json.dumps(payload),
                                content_type='application/json')

    def test_link_with_password_logs_in(self):
        sub = self._start_collision()
        response = self._link({'username': 'owner1', 'password': 'OwnerPass123!'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')
        link = GoogleAccount.objects.get(sub=sub)
        self.assertEqual(link.user_id, self.owner.id)
        self.assertEqual(link.link_method, 'explicit_link')
        self.assertEqual(str(self.client.session['_auth_user_id']), str(self.owner.id))

    def test_link_wrong_password_rejected(self):
        self._start_collision()
        response = self._link({'username': 'owner1', 'password': 'WrongPass!'})
        self.assertEqual(response.status_code, 401)
        self.assertFalse(GoogleAccount.objects.exists())
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_link_without_pending_expired(self):
        response = self._link({'username': 'owner1', 'password': 'OwnerPass123!'})
        self.assertEqual(response.status_code, 410)

    def test_link_marks_email_verified_when_google_verified(self):
        self.assertFalse(self.owner.email_verified)
        self._start_collision()
        self._link({'username': 'owner1', 'password': 'OwnerPass123!'})
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.email_verified)

    def test_link_does_not_weaken_existing_password(self):
        self._start_collision()
        self._link({'username': 'owner1', 'password': 'OwnerPass123!'})
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.has_usable_password())
        self.assertTrue(self.owner.check_password('OwnerPass123!'))

    def test_two_factor_user_must_provide_code(self):
        import pyotp

        from users.services.two_factor_service import confirm_and_enable_two_factor, initialize_two_factor

        _, secret = initialize_two_factor(self.owner)
        confirm_and_enable_two_factor(self.owner, pyotp.TOTP(secret).now())
        self._start_collision(sub='link-sub-2fa')

        no_code = self._link({'username': 'owner1', 'password': 'OwnerPass123!'})
        self.assertEqual(no_code.status_code, 401)
        self.assertEqual(no_code.json()['status'], 'two_factor_required')
        self.assertFalse(GoogleAccount.objects.filter(sub='link-sub-2fa').exists())

        from users.models import UserTwoFactor

        UserTwoFactor.objects.filter(user=self.owner).update(last_used_timestep=None)
        with_code = self._link({'username': 'owner1', 'password': 'OwnerPass123!',
                                'code': pyotp.TOTP(secret).now()})
        self.assertEqual(with_code.status_code, 200)
        # 2FA-enrolled users go through the 2FA challenge, not straight in
        self.assertEqual(with_code.json()['redirect'], reverse('login_2fa_challenge'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_google_login_two_factor_challenge_completes_link(self):
        import pyotp

        from users.services.two_factor_service import confirm_and_enable_two_factor, initialize_two_factor

        _, secret = initialize_two_factor(self.owner)
        confirm_and_enable_two_factor(self.owner, pyotp.TOTP(secret).now())
        GoogleAccount.objects.create(user=self.owner, sub='known-2fa-sub',
                                     email='owner1@example.com', email_verified=True)
        with mock_jwks():
            response = self.client.post(
                reverse('users:google_complete'),
                data=json.dumps({'credential': mint_token(sub='known-2fa-sub',
                                                          email='owner1@example.com')}),
                content_type='application/json',
            )
        self.assertEqual(response.json()['redirect'], reverse('login_2fa_challenge'))
        # Finish the standard 2FA challenge (reset replay guard first)
        from users.models import UserTwoFactor

        UserTwoFactor.objects.filter(user=self.owner).update(last_used_timestep=None)
        challenge = self.client.post(reverse('login_2fa_challenge'),
                                     {'code': pyotp.TOTP(secret).now()})
        self.assertEqual(challenge.status_code, 302)
        self.assertEqual(str(self.client.session['_auth_user_id']), str(self.owner.id))


class GoogleCsrfTest(TestCase):
    def test_complete_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        with mock_jwks():
            response = csrf_client.post(
                reverse('users:google_complete'),
                data=json.dumps({'credential': mint_token()}),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 403)

    def test_link_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        response = csrf_client.post(reverse('users:google_link'), data=json.dumps({}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 403)


@override_settings(**GOOGLE_OVERRIDE, GOOGLE_COMPLETE_RATELIMIT_ATTEMPTS=3,
                   GOOGLE_COMPLETE_RATELIMIT_WINDOW=300,
                   CACHES={'default': {
                       'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class GoogleCompleteRateLimitTest(TestCase):
    """Rate-limit logic runs against a real (locmem) cache backend.

    The default local/dev settings use DummyCache, under which no counting
    happens by design; these tests therefore pin a functional backend.
    """
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.client = Client()
        self.url = reverse('users:google_complete')

    def tearDown(self):
        from django.core.cache import cache

        cache.clear()

    def _complete(self, sub, email=None, **extra_client_kwargs):
        # Fresh client per attempt: a completed sign-in logs the session in,
        # and authenticated requests bypass the limiter by design.
        client = Client()
        with mock_jwks():
            return client.post(
                self.url,
                data=json.dumps({'credential': mint_token(
                    sub=sub, email=email or f"{sub}@example.com")}),
                content_type='application/json',
                **extra_client_kwargs,
            )

    def test_requests_below_limit_succeed(self):
        for i in range(3):
            response = self._complete(f"rl-ok-{i}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['status'], 'ok')

    def test_requests_exceeding_limit_rejected_without_verification(self):
        from users.services import google_auth_service

        real_verify = google_auth_service.verify_google_id_token
        with patch.object(google_auth_service, 'verify_google_id_token',
                          side_effect=real_verify) as spy:
            with mock_jwks():
                for i in range(3):
                    self._complete(f"rl-block-{i}")
                blocked = self._complete("rl-blocked")
        self.assertEqual(blocked.status_code, 429)
        body = blocked.json()
        self.assertEqual(body['status'], 'error')
        self.assertEqual(body['code'], 'rate_limited')
        self.assertNotIn('credential', str(body))
        # The blocked request never reached token verification.
        self.assertEqual(spy.call_count, 3)

    def test_limit_resets_after_window_cleared(self):
        for i in range(3):
            self._complete(f"rl-reset-{i}")
        self.assertEqual(self._complete("rl-reset-blocked").status_code, 429)
        from django.core.cache import cache

        cache.clear()
        response = self._complete("rl-reset-fresh")
        self.assertEqual(response.status_code, 200)

    def test_limit_is_per_client_ip(self):
        for i in range(3):
            self._complete(f"rl-ip-{i}")
        self.assertEqual(self._complete("rl-ip-blocked").status_code, 429)
        # A different client IP has its own budget.
        response = self._complete("rl-ip-other", HTTP_X_FORWARDED_FOR='203.0.113.9')
        self.assertEqual(response.status_code, 200)

    def test_rate_limit_ip_validation(self):
        from django.test import RequestFactory

        from users.services import google_auth_service

        factory = RequestFactory()
        # Valid XFF: leftmost entry wins.
        request = factory.post('/', HTTP_X_FORWARDED_FOR='203.0.113.9, 70.41.3.18',
                               REMOTE_ADDR='10.0.0.1')
        self.assertEqual(google_auth_service.get_rate_limit_ip(request), '203.0.113.9')
        # Garbage XFF entries are skipped in favour of REMOTE_ADDR.
        request = factory.post('/', HTTP_X_FORWARDED_FOR='not-an-ip, also-bad',
                               REMOTE_ADDR='10.0.0.1')
        self.assertEqual(google_auth_service.get_rate_limit_ip(request), '10.0.0.1')
        # Nothing usable at all degrades to a shared 'unknown' bucket.
        request = factory.post('/', HTTP_X_FORWARDED_FOR='nope', REMOTE_ADDR='')
        self.assertEqual(google_auth_service.get_rate_limit_ip(request), 'unknown')
        # IPv6 is accepted.
        request = factory.post('/', REMOTE_ADDR='2001:db8::1')
        self.assertEqual(google_auth_service.get_rate_limit_ip(request), '2001:db8::1')

    def test_authenticated_users_do_not_consume_budget(self):
        user = User.objects.create_user(username='rl-authed', password='x')
        self.client.force_login(user)
        for i in range(5):
            with mock_jwks():
                response = self.client.post(
                    self.url,
                    data=json.dumps({'credential': mint_token(sub=f"rl-authed-{i}")}),
                    content_type='application/json',
                )
            self.assertEqual(response.status_code, 200)

    def test_link_flow_unaffected_by_complete_budget(self):
        for i in range(3):
            self._complete(f"rl-linkfill-{i}")
        self.assertEqual(self._complete("rl-linkfill-blocked").status_code, 429)
        owner = User.objects.create_user(username='rl-owner', email='rl-owner@example.com',
                                         password='OwnerPass123!')
        session = self.client.session
        session['_google_pending'] = {
            'sub': 'rl-link-sub', 'email': 'rl-owner@example.com',
            'email_verified': True, 'given_name': '', 'family_name': '',
            'hosted_domain': '', 'ts': int(time.time()),
        }
        session.save()
        response = self.client.post(
            reverse('users:google_link'),
            data=json.dumps({'username': 'rl-owner', 'password': 'OwnerPass123!'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')
        self.assertTrue(GoogleAccount.objects.filter(sub='rl-link-sub').exists())


@override_settings(**GOOGLE_OVERRIDE)
class GoogleSignupRaceTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = reverse('users:google_complete')

    def _complete(self, token):
        with mock_jwks():
            return self.client.post(
                self.url, data=json.dumps({'credential': token}),
                content_type='application/json',
            )

    def test_username_conflict_retries_with_fresh_candidate(self):
        # Force a genuine write-time conflict: derivation first returns the
        # taken 'clash', then falls through to a free candidate on retry.
        from users.services import google_auth_service

        User.objects.create_user(username='clash', email='someone-else@example.com',
                                 password='x')
        real_derive = google_auth_service.derive_username_from_email
        calls = []

        def derive_once(email):
            if not calls:
                calls.append(1)
                return 'clash'
            return real_derive(email)

        with patch.object(google_auth_service, 'derive_username_from_email',
                          side_effect=derive_once):
            response = self._complete(mint_token(sub='race-user-1', email='clash@example.com'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')
        user = User.objects.get(email__iexact='clash@example.com')
        self.assertEqual(user.username, 'clash1')
        self.assertTrue(GoogleAccount.objects.filter(sub='race-user-1', user=user).exists())

    def test_sub_conflict_logs_into_race_winner(self):
        rival = User.objects.create_user(username='rival', email='rival@example.com',
                                         password='x')
        GoogleAccount.objects.create(user=rival, sub='race-sub-2', email='rival@example.com',
                                     email_verified=True)
        before = User.objects.count()

        def create_once(*args, **kwargs):
            # Simulate losing a race: the rival link already exists.
            from django.db import IntegrityError as _IntegrityError

            raise _IntegrityError('duplicate key value violates unique constraint')

        with patch.object(GoogleAccount.objects, 'create', side_effect=create_once):
            response = self._complete(mint_token(sub='race-sub-2', email='late@example.com'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')
        self.assertEqual(User.objects.count(), before)
        self.assertEqual(str(self.client.session['_auth_user_id']), str(rival.id))

    def test_email_appearing_mid_race_requires_linking(self):
        # Simulate our write losing a race: the owner row is committed ground
        # truth by re-query time, so the helper reports a collision instead
        # of creating a duplicate.
        from django.db import IntegrityError

        from users.views import _create_google_user_account

        User.objects.create_user(username='midrace-owner', email='midrace@example.com',
                                 password='x')
        claims = {'sub': 'race-sub-3', 'email': 'midrace@example.com',
                  'email_verified': True, 'given_name': '', 'family_name': '', 'hd': ''}
        with patch.object(User, 'save', autospec=True,
                          side_effect=IntegrityError('simulated race on write')):
            outcome, user = _create_google_user_account(claims, 'race-sub-3',
                                                        'midrace@example.com')
        self.assertEqual(outcome, 'email_collision')
        self.assertIsNone(user)
        self.assertFalse(GoogleAccount.objects.filter(sub='race-sub-3').exists())

    def test_username_exhaustion_returns_controlled_error(self):
        from users.services import google_auth_service

        User.objects.create_user(username='taken', email='other@example.com', password='x')
        with patch.object(google_auth_service, 'derive_username_from_email',
                          return_value='taken'):
            response = self._complete(mint_token(sub='race-sub-4', email='new@example.com'))
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body['status'], 'error')
        self.assertEqual(body['code'], 'username_unavailable')
        self.assertFalse(User.objects.filter(email__iexact='new@example.com').exists())
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_unrelated_errors_are_not_swallowed(self):
        def save_boom(self_user, *args, **kwargs):
            raise ValueError('simulated programming error')

        with patch.object(User, 'save', autospec=True, side_effect=save_boom):
            with self.assertRaises(ValueError):
                self._complete(mint_token(sub='race-sub-5', email='boom@example.com'))


@override_settings(**GOOGLE_OVERRIDE)
class GoogleConcurrentSignupTest(TransactionTestCase):
    """Real concurrent POSTs against PostgreSQL: no mocks on the write path.

    Only the JWKS network fetch is stubbed; JWTs are genuinely signed and
    all account writes go through real transactions.
    """

    def test_concurrent_same_sub_creates_single_account(self):
        import threading

        barrier = threading.Barrier(2)
        outcomes = {}

        def attempt(idx):
            try:
                client = Client()
                barrier.wait(timeout=60)
                response = client.post(
                    reverse('users:google_complete'),
                    data=json.dumps({'credential': mint_token(
                        sub='conc-sub-1', email='conc@example.com')}),
                    content_type='application/json',
                )
                outcomes[idx] = (response.status_code, response.json(),
                                 str(client.session.get('_auth_user_id')))
            except Exception as exc:  # surface thread failures in the main thread
                outcomes[idx] = exc

        with mock_jwks():
            threads = [threading.Thread(target=attempt, args=(i,)) for i in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=120)

        for idx in (0, 1):
            self.assertNotIsInstance(outcomes[idx], Exception, f"thread {idx} raised")
            status_code, body, auth_id = outcomes[idx]
            self.assertEqual(status_code, 200, f"thread {idx}: {body}")
            self.assertEqual(body['status'], 'ok')
        # Exactly one account exists and both sessions belong to it.
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(GoogleAccount.objects.count(), 1)
        sole_user = User.objects.get()
        self.assertEqual(outcomes[0][2], outcomes[1][2])
        self.assertEqual(outcomes[0][2], str(sole_user.id))
        self.assertFalse(sole_user.has_usable_password())


@override_settings(**GOOGLE_OVERRIDE,
                   CACHES={'default': {
                       'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class GoogleRedirectViewTest(TestCase):
    """GIS redirect mode: Google POSTs credential+g_csrf_token cross-site."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.client = Client()
        self.url = reverse('users:google_redirect')
        self.login_url = reverse('login')

    def tearDown(self):
        from django.core.cache import cache

        cache.clear()

    def _redirect_post(self, credential, csrf_token='csrf-token-123', client=None, state=None):
        http_client = client or Client()
        http_client.cookies['g_csrf_token'] = csrf_token
        data = {'credential': credential, 'g_csrf_token': csrf_token}
        if state is not None:
            data['state'] = state
        with mock_jwks():
            return http_client.post(self.url, data=data), http_client

    def test_g_csrf_mismatch_rejected(self):
        from users.services import google_auth_service

        client = Client()
        client.cookies['g_csrf_token'] = 'cookie-value'
        with patch.object(google_auth_service, 'verify_google_id_token') as spy:
            with mock_jwks():
                response = client.post(
                    self.url,
                    data={'credential': mint_token(), 'g_csrf_token': 'other-value'},
                )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.login_url)
        self.assertEqual(spy.call_count, 0)
        self.assertNotIn('_auth_user_id', client.session)

    def test_g_csrf_missing_rejected(self):
        response, client = self._redirect_post(mint_token(), csrf_token='')
        # Empty token on both sides still fails the presence check.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.login_url)
        self.assertNotIn('_auth_user_id', client.session)

    def test_new_user_created_and_logged_in(self):
        response, client = self._redirect_post(
            mint_token(sub='redir-new-1', email='redirnew@example.com'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('posts:home'))
        user = User.objects.get(email__iexact='redirnew@example.com')
        self.assertFalse(user.has_usable_password())
        self.assertEqual(str(client.session['_auth_user_id']), str(user.id))
        self.assertTrue(GoogleAccount.objects.filter(sub='redir-new-1', user=user).exists())

    def test_login_page_keeps_google_callback_uri_fixed_and_signs_continuation(self):
        from urllib.parse import urlencode, urlparse
        from django.core import signing
        from importlib import import_module
        from django.conf import settings

        oauth_url = '/o/authorize/?' + urlencode({
            'client_id': 'workspaces-client', 'response_type': 'code',
            'redirect_uri': 'http://localhost:8001/api/auth/vasto/callback/',
            'scope': 'openid profile email', 'state': 'workspace-state', 'nonce': 'workspace-nonce',
            'code_challenge': 'challenge', 'code_challenge_method': 'S256',
        })
        response = self.client.get(self.login_url + '?' + urlencode({'next': oauth_url}))
        self.assertEqual(response.status_code, 200)
        context = response.context
        self.assertEqual(urlparse(context['google_login_uri']).path, self.url)
        self.assertEqual(urlparse(context['google_login_uri']).query, '')
        handle = signing.loads(
            context['google_login_state'],
            salt='users.google_login_continuation', max_age=900,
        )
        continuation = import_module(settings.SESSION_ENGINE).SessionStore(session_key=handle)
        self.assertEqual(continuation.get('_google_login_next'), oauth_url)

    def test_known_google_user_returns_to_safe_oauth_continuation(self):
        from urllib.parse import urlencode

        user = User.objects.create_user(username='redirnext', email='redirnext@example.com',
                                        password='x')
        GoogleAccount.objects.create(user=user, sub='redir-next-sub',
                                     email='redirnext@example.com', email_verified=True)
        oauth_url = '/o/authorize/?' + urlencode({
            'client_id': 'workspaces-client', 'response_type': 'code',
            'redirect_uri': 'http://localhost:8001/api/auth/vasto/callback/',
            'scope': 'openid', 'state': 'st', 'nonce': 'no',
            'code_challenge': 'cc', 'code_challenge_method': 'S256',
        })
        client = Client()
        client.cookies['g_csrf_token'] = 'csrf-token-123'
        from django.core import signing
        from users.views import _make_google_login_state
        google_state = _make_google_login_state(oauth_url)
        with mock_jwks():
            response = client.post(
                self.url,
                data={'credential': mint_token(sub='redir-next-sub', email='redirnext@example.com'),
                      'g_csrf_token': 'csrf-token-123', 'state': google_state},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, oauth_url)
        self.assertEqual(str(client.session['_auth_user_id']), str(user.id))

    def test_known_sub_logs_in(self):
        user = User.objects.create_user(username='redirknown', email='redirknown@example.com',
                                        password='x')
        user.set_unusable_password()
        user.save()
        GoogleAccount.objects.create(user=user, sub='redir-known-sub',
                                     email='redirknown@example.com', email_verified=True)
        before = User.objects.count()
        response, client = self._redirect_post(
            mint_token(sub='redir-known-sub', email='redirknown@example.com'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.count(), before)
        self.assertEqual(str(client.session['_auth_user_id']), str(user.id))

    def test_email_collision_bounces_to_login_with_link_form(self):
        User.objects.create_user(username='redirvictim', email='redirvictim@example.com',
                                 password='VictimPass123!')
        response, client = self._redirect_post(
            mint_token(sub='redir-collide', email='redirvictim@example.com'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(self.login_url))
        self.assertIn('google_link=1', response.url)
        self.assertFalse(GoogleAccount.objects.filter(sub='redir-collide').exists())
        self.assertNotIn('_auth_user_id', client.session)
        self.assertIn('_google_pending', client.session)
        # The bounced login page reveals the link form.
        page = client.get(response.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'google-link-actual-form')

    def test_two_factor_user_goes_through_challenge(self):
        import pyotp

        from users.models import UserTwoFactor
        from users.services.two_factor_service import (
            confirm_and_enable_two_factor, initialize_two_factor)

        owner = User.objects.create_user(username='redirtfa', email='redirtfa@example.com',
                                         password='OwnerPass123!')
        _, secret = initialize_two_factor(owner)
        confirm_and_enable_two_factor(owner, pyotp.TOTP(secret).now())
        GoogleAccount.objects.create(user=owner, sub='redir-2fa-sub',
                                     email='redirtfa@example.com', email_verified=True)
        from django.core import signing
        from urllib.parse import urlencode

        oauth_url = '/o/authorize/?' + urlencode({
            'client_id': 'workspaces-client', 'response_type': 'code',
            'redirect_uri': 'http://localhost:8001/api/auth/vasto/callback/',
            'scope': 'openid profile email', 'state': 'google-workspaces-state',
            'nonce': 'google-workspaces-nonce', 'code_challenge': 'challenge',
            'code_challenge_method': 'S256',
        })
        from users.views import _make_google_login_state
        google_state = _make_google_login_state(oauth_url)
        response, client = self._redirect_post(
            mint_token(sub='redir-2fa-sub', email='redirtfa@example.com'), state=google_state)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('login_2fa_challenge'))
        self.assertEqual(client.session['_2fa_next'], oauth_url)
        self.assertNotIn('_auth_user_id', client.session)
        UserTwoFactor.objects.filter(user=owner).update(last_used_timestep=None)
        challenge = client.post(reverse('login_2fa_challenge'),
                                {'code': pyotp.TOTP(secret).now()})
        self.assertEqual(challenge.status_code, 302)
        self.assertEqual(challenge.url, oauth_url)
        self.assertEqual(str(client.session['_auth_user_id']), str(owner.id))

    def test_invalid_token_bounces_to_login(self):
        response, client = self._redirect_post(mint_token(expired=True))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.login_url)
        self.assertNotIn('_auth_user_id', client.session)

    def test_disabled_redirect_bounces_to_login(self):
        with override_settings(GOOGLE_LOGIN_ENABLED=False):
            response, _ = self._redirect_post(mint_token())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.login_url)

    @override_settings(GOOGLE_COMPLETE_RATELIMIT_ATTEMPTS=1)
    def test_rate_limited_redirect_bounces_to_login(self):
        self._redirect_post(mint_token(sub='redir-rl-1', email='redir-rl-1@example.com'))
        response, client = self._redirect_post(
            mint_token(sub='redir-rl-2', email='redir-rl-2@example.com'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.login_url)
        self.assertFalse(User.objects.filter(email__iexact='redir-rl-2@example.com').exists())

    def test_authenticated_post_goes_home(self):
        user = User.objects.create_user(username='redirauthed', password='x')
        self.client.force_login(user)
        self.client.cookies['g_csrf_token'] = 'tok'
        with mock_jwks():
            response = self.client.post(
                self.url, data={'credential': mint_token(), 'g_csrf_token': 'tok'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('posts:home'))
