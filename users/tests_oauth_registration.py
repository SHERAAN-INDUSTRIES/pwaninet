import io
import os
import stat
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from django.contrib.auth.hashers import check_password

_TEST_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=4096).private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption(),
).decode("ascii")


@override_settings(OAUTH2_PROVIDER={"OIDC_ENABLED": True, "OIDC_RSA_PRIVATE_KEY": _TEST_PRIVATE_KEY})
class RegisterWorkspacesOAuthTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username="local-admin", password="test-only-password", is_active=True,
            is_staff=True, is_superuser=True,
        )
        self.tempdir = tempfile.TemporaryDirectory()
        self.credentials = Path(self.tempdir.name) / "promise-local.env"

    def tearDown(self):
        self.tempdir.cleanup()

    def register(self, **kwargs):
        options = {
            "developer": "promise",
            "owner_username": self.owner.username,
            "redirect_uri": "http://127.0.0.1:8001/api/auth/vasto/callback/",
            "credentials_file": str(self.credentials),
            "show_secret": False,
        }
        options.update(kwargs)
        output = io.StringIO()
        call_command("register_workspaces_oauth", stdout=output, **options)
        return output.getvalue()

    def test_creates_confidential_consenting_rs256_application_and_secure_credentials(self):
        output = self.register()
        from oauth2_provider.models import Application
        app = Application.objects.get(name="Vasto Workspaces Local - promise")
        self.assertEqual(app.user, self.owner)
        self.assertEqual(app.client_type, Application.CLIENT_CONFIDENTIAL)
        self.assertEqual(app.authorization_grant_type, Application.GRANT_AUTHORIZATION_CODE)
        self.assertEqual(app.redirect_uris, "http://127.0.0.1:8001/api/auth/vasto/callback/")
        self.assertFalse(app.skip_authorization)
        self.assertTrue(app.hash_client_secret)
        self.assertEqual(app.algorithm, Application.RS256_ALGORITHM)
        self.assertEqual(app.post_logout_redirect_uris, "")
        self.assertEqual(app.allowed_origins, "")
        self.assertTrue(self.credentials.exists())
        self.assertEqual(stat.S_IMODE(self.credentials.stat().st_mode), 0o600)
        values = dict(line.split("=", 1) for line in self.credentials.read_text().splitlines())
        self.assertEqual(values["VASTO_CLIENT_ID"], app.client_id)
        self.assertTrue(check_password(values["VASTO_CLIENT_SECRET"], app.client_secret))
        self.assertNotIn(values["VASTO_CLIENT_SECRET"], output)
        self.assertIn(app.client_id, output)

    def test_duplicate_registration_does_not_rotate_secret_or_overwrite_file(self):
        self.register()
        before = self.credentials.read_text()
        from oauth2_provider.models import Application
        app = Application.objects.get(name="Vasto Workspaces Local - promise")
        old_hash = app.client_secret
        with self.assertRaisesRegex(CommandError, "already exists"):
            self.register()
        app.refresh_from_db()
        self.assertEqual(app.client_secret, old_hash)
        self.assertEqual(self.credentials.read_text(), before)
        self.assertEqual(Application.objects.filter(name=app.name).count(), 1)

    def test_requires_existing_active_staff_owner(self):
        with self.assertRaisesRegex(CommandError, "does not exist"):
            self.register(owner_username="missing")
        self.assertFalse(self.credentials.exists())
        from oauth2_provider.models import Application
        self.assertFalse(Application.objects.filter(name="Vasto Workspaces Local - promise").exists())

    def test_refuses_invalid_signing_key_before_registration(self):
        with override_settings(OAUTH2_PROVIDER={"OIDC_ENABLED": True, "OIDC_RSA_PRIVATE_KEY": "not a pem"}):
            with self.assertRaisesRegex(CommandError, "could not be parsed"):
                self.register()
        self.assertFalse(self.credentials.exists())
