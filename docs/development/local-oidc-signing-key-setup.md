# Local Vasto OIDC signing key setup

This guide prepares a developer-owned local Vasto provider for the Vasto–Workspaces OAuth/OIDC flow. Vasto signs OIDC ID tokens with an RSA private key using RS256. Each developer running a separate local provider should use a separate local key.

The signing key belongs only to that local Vasto instance. Keep it persistent across restarts, outside version control, and separate from production. Never copy it from another developer, send it to Workspaces, or reuse it in production. If your team has a shared development Vasto provider, that provider environment owns its key; developers using it do **not** generate or download that key.

## How this repository loads the key

The current implementation is in `pwaninet/settings/base.py`:

1. `python-dotenv` loads the repository-root `.env` before settings are built.
2. `OAUTH2_PROVIDER['OIDC_RSA_PRIVATE_KEY']` uses a non-empty `OIDC_RSA_PRIVATE_KEY` environment value when provided.
3. Otherwise, it reads the contents of `<Vasto repository>/.secrets/oidc_rsa_private.pem` if that file exists.
4. If neither source is present, the setting is `None`.

`pwaninet.settings.local` inherits the base OAuth configuration and sets the local issuer to `http://127.0.0.1:8000/o`. Django OAuth Toolkit uses the resulting `OAUTH2_PROVIDER` setting for RS256 ID-token signing. The key is traditional RSA PEM compatible with `cryptography`/Django OAuth Toolkit. No new key-path setting is part of the implementation.

There is no dedicated Django management command for key creation. `.env.example` documents the file fallback. `.gitignore` excludes both `*.pem` and `.secrets/`. Do not set `OIDC_RSA_PRIVATE_KEY` to a filename: the environment variable expects the PEM **contents**, not a path. For a local developer setup, leave it empty/unset and use the ignored file fallback.

## Generate a local key

Run these commands from the Vasto repository root. They create a 4096-bit RSA private key in the exact path the settings load. OpenSSL's `-traditional` output is the RSA PEM format expected by this local configuration.

```sh
mkdir -p .secrets
chmod 700 .secrets

if [ -e .secrets/oidc_rsa_private.pem ]; then
  echo "A local Vasto signing key already exists; verify it instead of overwriting it."
else
  openssl genrsa -traditional -out .secrets/oidc_rsa_private.pem 4096
  chmod 600 .secrets/oidc_rsa_private.pem
fi
```

- `mkdir -p` creates the `.secrets` directory if needed.
- `chmod 700` restricts directory access to your account.
- `openssl genrsa -traditional ... 4096` creates a 4096-bit RSA private key in traditional PEM form.
- `chmod 600` restricts the key file to your account.

If the file already exists, do not overwrite it or assume it belongs to you: reuse it only if you know it was generated exclusively for this local Vasto instance. If its origin is unclear, arrange a deliberate local key replacement and account for previously issued ID tokens before starting with a different key. The generation block does not overwrite an existing file.

The repository's `.gitignore` excludes this path, but confirm it from the repository root before continuing:

```sh
git check-ignore -v .secrets/oidc_rsa_private.pem
```

The command should identify the ignore rule. Do not move the key to a tracked directory or remove the ignore rule. Do not paste its contents into a ticket, terminal transcript, `.env.example`, or Workspaces settings.

## Verify the key file

Check file permissions without displaying the key and ask OpenSSL to parse and validate it:

```sh
ls -ld .secrets
ls -l .secrets/oidc_rsa_private.pem
openssl pkey -in .secrets/oidc_rsa_private.pem -check -noout
```

OpenSSL should report `Key is valid`; the directory should be accessible only to your account and the file mode should be `-rw-------` (600). Common failures:

- **File not found:** Run from the Vasto repository root and check that the filename matches the setting path exactly.
- **Could not read key / invalid PEM:** Recreate a local key using the command above. Do not convert or copy a production key to work around the error.
- **Permission denied:** Confirm the file belongs to your account and its mode is 600; confirm `.secrets` is mode 700.

## Verify Django loaded and parsed the key

The following Django shell command reports only booleans, key type, and key size. It never prints any part of the private key. Run it from the repository root after creating the file:

```sh
python manage.py shell --settings=pwaninet.settings.local -c '
from django.conf import settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

pem = settings.OAUTH2_PROVIDER.get("OIDC_RSA_PRIVATE_KEY")
loaded = bool(pem)
print("OIDC_RSA_PRIVATE_KEY loaded:", loaded)
valid_pem = isinstance(pem, str) and pem.startswith("-----BEGIN ") and "-----END " in pem
print("PEM structure valid:", valid_pem)
if not loaded or not valid_pem:
    raise SystemExit(1)
try:
    key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
except Exception as exc:
    print("Private key successfully parsed: False")
    print("Parse error type:", type(exc).__name__)
    raise SystemExit(1)
is_rsa = isinstance(key, rsa.RSAPrivateKey)
print("Private key successfully parsed:", is_rsa)
print("Key type:", "RSA" if is_rsa else type(key).__name__)
if is_rsa:
    print("Key size:", key.key_size, "bits")
else:
    raise SystemExit(1)
'
```

Expected output includes `OIDC_RSA_PRIVATE_KEY loaded: True`, `PEM structure valid: True`, `Private key successfully parsed: True`, `Key type: RSA`, and `Key size: 4096 bits`. If loading reports false, check that `.env` does not contain an unintended non-empty `OIDC_RSA_PRIVATE_KEY` value: a non-empty environment value takes precedence over the file.

## JWKS: publish only the public key

Vasto publishes the public component of its configured signing key at:

```text
http://127.0.0.1:8000/o/.well-known/jwks.json
```

JWKS is a JSON Web Key Set: a public collection of keys that clients can use to verify signed tokens. Workspaces reads the `jwks_uri` from Vasto discovery and uses the RSA public key to verify the ID-token signature. The `kid` (key ID) in a signed token identifies which published key verifies it. The corresponding private key never leaves Vasto.

After Vasto is running, this check validates that the public endpoint contains an RSA signing key with the fields required for an RS256 verification key. It prints metadata only, not key material:

```sh
python -c '
import json, urllib.request
url = "http://127.0.0.1:8000/o/.well-known/jwks.json"
with urllib.request.urlopen(url) as response:
    document = json.load(response)
keys = [key for key in document.get("keys", []) if key.get("kty") == "RSA" and key.get("use", "sig") == "sig"]
if not keys:
    raise SystemExit("No RSA signing key was published")
for key in keys:
    if not key.get("kid") or not key.get("n") or not key.get("e"):
        raise SystemExit("RSA key is missing kid, modulus, or exponent")
    if key.get("alg") not in (None, "RS256"):
        raise SystemExit("Published RSA key advertises an unexpected algorithm")
    print("RSA signing key published: True")
    print("Key ID present:", bool(key.get("kid")))
    print("Algorithm:", key.get("alg") or "check discovery and Application algorithm")
'
```

The JWKS response contains public key data and is safe for Workspaces to retrieve. Do not include a private key in JWKS or manually copy one to Workspaces.

## Prepare the local Vasto environment

Vasto's documented development runtime is Python 3.11 or newer. Its standard local settings are `pwaninet.settings.local`, which `manage.py` selects by default. The repository's `.env.example` is a template; keep the real `.env` untracked.

From a fresh checkout:

```sh
python --version
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

Configure the local PostgreSQL values in `.env`: `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST=127.0.0.1`, and `DB_PORT=5432`. The checked-in Compose file publishes PostgreSQL on host port 5432. If you change the Compose mapping or use another PostgreSQL service, set `DB_PORT` to its published port. Using `127.0.0.1` avoids `localhost` resolving to IPv6 when PostgreSQL only accepts IPv4. Start PostgreSQL and Redis:

```sh
docker compose up -d db redis
```

Keep `OIDC_RSA_PRIVATE_KEY` empty/unset in `.env` to use the `.secrets` file. No key contents belong in `.env.example`. Then run migrations and start the provider:

```sh
python manage.py migrate --settings=pwaninet.settings.local
python manage.py check --settings=pwaninet.settings.local
python manage.py runserver 127.0.0.1:8000 --settings=pwaninet.settings.local
```

Open Vasto in a browser at `http://127.0.0.1:8000/`. Use that same hostname in the Workspaces issuer and redirect flow. Do not use `0.0.0.0` as a browser address or mix `localhost` and `127.0.0.1`; cookie host scoping can make the two login flows appear to lose or share sessions.

The repository also contains `scripts/setup-env.sh`, which switches `.env` between repository environment files. It is optional and not needed for the commands above; review its effect before using it on a configured checkout because it replaces `.env`.

## Check provider readiness

With Vasto running at `http://127.0.0.1:8000`, run:

```sh
curl -fsS http://127.0.0.1:8000/o/.well-known/openid-configuration | python -c '
import json, sys
metadata = json.load(sys.stdin)
print("Issuer:", metadata.get("issuer"))
print("Authorization endpoint:", metadata.get("authorization_endpoint"))
print("Token endpoint:", metadata.get("token_endpoint"))
print("JWKS URI:", metadata.get("jwks_uri"))
print("ID-token signing algorithms:", metadata.get("id_token_signing_alg_values_supported"))
'

curl -fsS http://127.0.0.1:8000/o/.well-known/jwks.json >/dev/null
```

Readiness checklist:

- `python manage.py check --settings=pwaninet.settings.local` reports no system-check errors.
- The Django shell check above confirms an RSA private key loaded and parsed, with a 4096-bit key.
- Discovery returns HTTP 200 and advertises issuer `http://127.0.0.1:8000/o`.
- Discovery advertises authorization at `/o/authorize/`, token exchange at `/o/token/`, and the local JWKS URL above.
- Discovery lists `RS256` in `id_token_signing_alg_values_supported`.
- JWKS contains an RSA signing key with `kid`, modulus, and exponent.
- Local settings are active: `OIDC_ENABLED` is true and `OIDC_ISS_ENDPOINT` is `http://127.0.0.1:8000/o`.

The provider's authorization endpoint is `http://127.0.0.1:8000/o/authorize/`. A complete login is initiated through the Workspaces start endpoint after a local Workspaces Application has been registered.

## Keep development and production keys separate

Never configure the local key in production. Production key material must be provisioned by the production secret-management process and remain stable across restarts. The application must not generate a replacement key at startup: changing keys can make previously issued ID tokens unverifiable after their signing key disappears from JWKS.

Key rotation is a deliberate provider operation. Plan the overlap between the new signing key and the prior public key so clients can verify tokens issued before rotation until their useful lifetime and verification caches have passed. This guide does not implement a rotation procedure. Workspaces' OAuth client secret is a different credential: it authenticates the Workspaces backend to Vasto's token endpoint and does not sign ID tokens.

## Workspaces setup guide

The Vasto Workspaces backend repository contains `docs/development/local-vasto-oidc-client-setup.md`, which covers local client registration and Workspaces configuration. Developers using a shared development Vasto provider use that provider's issued credentials and public JWKS; they do not create or copy its signing key.
