#!/usr/bin/env bash
set -Eeuo pipefail
echo 'Preparing the local sign-in key'
KD="$HOME/.config/vasto/keys"; KF="$KD/oidc_rsa_private.pem"; LEGACY_KEY="$ROOT/.secrets/oidc_rsa_private.pem"
mkdir -p "$KD"; chmod 700 "$HOME/.config/vasto" "$KD"
if [[ -e "$KF" ]]; then
  echo "Retaining existing local signing key."
elif [[ -f "$LEGACY_KEY" ]]; then
  cp -n "$LEGACY_KEY" "$KF"
  [[ -e "$KF" ]] || { echo "Could not preserve the existing legacy signing key." >&2; exit 1; }
  chmod 600 "$KF"
  echo "Preserved the existing repository-local key in the per-user config directory."
else
  TEMP_KEY="$(mktemp "$KD/.oidc-rsa.XXXXXX")"
  if ! openssl genrsa -traditional -out "$TEMP_KEY" 4096 >/dev/null 2>&1; then
    rm -f "$TEMP_KEY"; echo "OpenSSL RSA key generation failed." >&2; exit 1
  fi
  chmod 600 "$TEMP_KEY"
  if ln "$TEMP_KEY" "$KF" 2>/dev/null; then
    echo "Created local RSA signing key at $KF."
  elif [[ -e "$KF" ]]; then
    echo "Another local signing key appeared; retaining it without replacement."
  else
    rm -f "$TEMP_KEY"; echo "Could not securely publish the RSA key file." >&2; exit 1
  fi
  rm -f "$TEMP_KEY"
fi
chmod 600 "$KF"
"$PY" - "$KF" <<'PY'
import sys
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
try: key=serialization.load_pem_private_key(open(sys.argv[1],"rb").read(),password=None)
except Exception as exc: raise SystemExit(f"Local RSA key is invalid ({type(exc).__name__}); preserve it and repair deliberately.")
if not isinstance(key,rsa.RSAPrivateKey) or key.key_size<4096: raise SystemExit("OIDC key must be RSA and at least 4096 bits.")
print("Signing key parsed as RSA (4096 bits or larger).")
PY
# Validate the setting Django will actually load, including environment overrides.
"$PY" manage.py shell --no-imports --settings=pwaninet.settings.local -c 'from django.conf import settings; from cryptography.hazmat.primitives import serialization; from cryptography.hazmat.primitives.asymmetric import rsa; p=settings.OAUTH2_PROVIDER.get("OIDC_RSA_PRIVATE_KEY"); k=serialization.load_pem_private_key(p.encode() if isinstance(p,str) else p,password=None); assert isinstance(k,rsa.RSAPrivateKey) and k.key_size>=4096; print("Django loaded a valid local RSA signing key.")' || { echo "Django did not load a valid 4096-bit RSA signing key. Check OIDC_RSA_PRIVATE_KEY and the local key file." >&2; exit 1; }
