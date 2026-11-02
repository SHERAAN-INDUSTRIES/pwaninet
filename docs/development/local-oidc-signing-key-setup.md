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

The corresponding public key is published through Vasto's JWKS endpoint. Workspaces retrieves it from OIDC discovery; the private key never leaves Vasto.
