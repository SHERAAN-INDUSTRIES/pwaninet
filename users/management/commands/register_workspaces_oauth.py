import os
import re
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from oauth2_provider.generators import generate_client_id, generate_client_secret
from oauth2_provider.models import Application

CALLBACK = "http://127.0.0.1:8001/api/auth/vasto/callback/"
DISCOVERY = "http://127.0.0.1:8000/o/.well-known/openid-configuration"


def validate_key():
    pem = settings.OAUTH2_PROVIDER.get("OIDC_RSA_PRIVATE_KEY")
    if not pem:
        raise CommandError("No OIDC RSA key is configured. Run ./scripts/setup-dev.sh first.")
    try:
        key = serialization.load_pem_private_key(pem.encode("ascii") if isinstance(pem, str) else pem, password=None)
    except Exception as exc:
        raise CommandError(f"OIDC signing key is invalid ({type(exc).__name__}).") from None
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 4096:
        raise CommandError("OIDC signing key must be RSA with at least 4096 bits.")


class Command(BaseCommand):
    help = "Register a developer-specific local Workspaces OIDC client."

    def add_arguments(self, parser):
        parser.add_argument("--developer", required=True)
        parser.add_argument("--owner-username", required=True)
        parser.add_argument("--redirect-uri", default=CALLBACK)
        parser.add_argument("--credentials-file")
        parser.add_argument("--show-secret", action="store_true", help="Explicitly print the generated secret once.")

    def handle(self, *args, **opts):
        developer = opts["developer"].strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", developer):
            raise CommandError("Developer identifier must use 1–64 letters, digits, dots, underscores, or hyphens.")
        name = f"Vasto Workspaces Local - {developer}"
        callback = opts["redirect_uri"].strip()
        from urllib.parse import urlsplit
        try:
            parsed_callback = urlsplit(callback)
            valid_callback = (
                parsed_callback.scheme == "http"
                and parsed_callback.hostname == "127.0.0.1"
                and parsed_callback.port is not None
                and parsed_callback.path == "/api/auth/vasto/callback/"
                and not parsed_callback.query
                and not parsed_callback.fragment
            )
        except ValueError:
            valid_callback = False
        if not valid_callback:
            raise CommandError("Callback must use http://127.0.0.1:PORT/api/auth/vasto/callback/ exactly.")
        User = get_user_model()
        try:
            owner = User.objects.get(username=opts["owner_username"])
        except User.DoesNotExist:
            raise CommandError(f"Owner {opts['owner_username']!r} does not exist; create/select an admin first.") from None
        if not owner.is_active or not owner.is_staff:
            raise CommandError("Owner must be an active staff/admin account.")
        output = Path(opts["credentials_file"]).expanduser() if opts["credentials_file"] else Path.home()/".config/vasto-workspaces"/f"{developer}-local.env"
        if Application.objects.filter(name=name).exists():
            raise CommandError(f"{name!r} already exists. It was not changed or rotated; recover its existing credentials at {output}.")
        if output.exists():
            raise CommandError(f"{output} already exists; refusing to overwrite it. Choose another --credentials-file.")
        if not settings.OAUTH2_PROVIDER.get("OIDC_ENABLED"):
            raise CommandError("OIDC is disabled in active Vasto settings.")
        validate_key()
        raw_secret, client_id = generate_client_secret(), generate_client_id()
        app = Application(
            name=name, user=owner, client_id=client_id, client_secret=raw_secret,
            client_type=Application.CLIENT_CONFIDENTIAL,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris=callback, post_logout_redirect_uris="", skip_authorization=False,
            hash_client_secret=True, algorithm=Application.RS256_ALGORITHM, allowed_origins="",
        )
        body = (f"VASTO_DISCOVERY_URL={DISCOVERY}\nVASTO_ISSUER=http://127.0.0.1:8000/o\n"
                f"VASTO_CLIENT_ID={client_id}\nVASTO_CLIENT_SECRET={raw_secret}\nVASTO_REDIRECT_URI={callback}\n")
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(output.parent, 0o700)
        temp = None
        linked = False
        try:
            with transaction.atomic():
                app.save()  # DOT's ClientSecretField hashes the supplied value.
                fd, temp = tempfile.mkstemp(prefix=".vasto-client-", dir=output.parent)
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w") as stream:
                    stream.write(body); stream.flush(); os.fsync(stream.fileno())
                os.link(temp, output)  # no-clobber atomic publish
                linked = True
                os.unlink(temp); temp = None
        except FileExistsError:
            raise CommandError(f"{output} appeared during setup; it was not overwritten.") from None
        except OSError as exc:
            if linked: output.unlink(missing_ok=True)
            raise CommandError(f"Secure credential provisioning failed ({exc.strerror or type(exc).__name__}); registration rolled back.") from None
        except Exception:
            if linked: output.unlink(missing_ok=True)
            raise
        finally:
            if temp and os.path.exists(temp): os.unlink(temp)
        os.chmod(output, 0o600)
        self.stdout.write(self.style.SUCCESS(f"Registered confidential RS256 Authorization Code client {client_id}."))
        self.stdout.write(f"Credential file: {output} (mode 600; secret hidden)")
        self.stdout.write("Import into Workspaces with: ./scripts/setup-dev.sh --credentials-file " + str(output))
        if opts["show_secret"]: self.stdout.write("One-time Client Secret: " + raw_secret)
