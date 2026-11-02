# Local OIDC signing key setup

The bootstrap commands use Bash and run directly on Linux (including Ubuntu and Fedora) and macOS. On Windows, install Ubuntu in WSL 2 and run these commands in its terminal; do not use PowerShell or Command Prompt. See the [Vasto README's operating-system setup instructions](../../README.md#install-prerequisites-for-your-operating-system).

Run `./scripts/setup-dev.sh` from the Vasto repository root for the complete local provider setup. In addition to key handling, it reuses an activated or existing compatible Python environment (creating `.venv` only if none is available), prepares `.env`, starts PostgreSQL and Redis, checks and migrates the database, selects or creates an admin owner, and registers the local Workspaces OAuth client with a private credentials file. The script creates a persistent 4096-bit RSA signing key at `~/.config/vasto/keys/oidc_rsa_private.pem` only when neither that key nor the older `.secrets/oidc_rsa_private.pem` exists. If the older file exists, it preserves that key in the per-user location. The directory is mode 700 and the key file mode 600.

`pwaninet.settings.local` loads a non-empty `OIDC_RSA_PRIVATE_KEY` environment value when configured; otherwise it prefers the per-user key file. The base settings retain their existing secret-loading behavior for production and older deployments. Leave the variable blank in local `.env`; never put private key contents in `.env.example`.

The local key belongs only to this developer's Vasto provider. Do not copy it from another developer, send it to Workspaces, or reuse it in production. The setup script never regenerates an existing key. If the key is invalid, the complete setup reports the parse failure and stops; repair it deliberately rather than replacing it automatically. Once Vasto is running, use the Workspaces repository's `./scripts/setup-dev.sh --credentials-file ~/.config/vasto-workspaces/YOUR_IDENTIFIER-local.env` to import credentials, migrate Workspaces, and run its live OIDC preflight. After setup, use the server command printed by the script; its Python path may differ from the `.venv` examples if an existing environment was reused.

To inspect key permissions and verify its PEM without displaying it:

```sh
ls -ld ~/.config/vasto ~/.config/vasto/keys
ls -l ~/.config/vasto/keys/oidc_rsa_private.pem
openssl pkey -in ~/.config/vasto/keys/oidc_rsa_private.pem -check -noout
```

To confirm Django loads a valid RSA key without printing the key:

```sh
.venv/bin/python manage.py shell --settings=pwaninet.settings.local -c '
from django.conf import settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
pem = settings.OAUTH2_PROVIDER.get("OIDC_RSA_PRIVATE_KEY")
key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
assert isinstance(key, rsa.RSAPrivateKey) and key.key_size >= 4096
print("Local OIDC RSA key is loaded and valid.")
'
```

<<<<<<< HEAD
The corresponding public key is published through Vasto's JWKS endpoint. Workspaces retrieves it from OIDC discovery; the private key never leaves Vasto.
=======
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
>>>>>>> 0d2ec7e59454798cbb6af0122c761d0d288afe66
