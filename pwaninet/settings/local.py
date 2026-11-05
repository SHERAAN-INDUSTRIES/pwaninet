"""
Local development settings.
"""
from .base import *
from pathlib import Path as _Path

DEBUG = True           

# In local development, permit all hosts/IPs on local network, tunnels, and mobile devices
ALLOWED_HOSTS = ['*']



# Database - PostgreSQL for local development
db_host = os.environ.get('DB_HOST', '127.0.0.1')
if db_host.lower() == 'localhost':
    db_host = '127.0.0.1'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'pwaninet_db'),
        'USER': os.environ.get('DB_USER', 'postgres'),
        'PASSWORD': os.environ.get('DB_PASSWORD', 'postgres'),
        'HOST': db_host,
        'PORT': os.environ.get('DB_PORT', '5432'),
        'CONN_MAX_AGE': 0,
    }
}

# Disable security features for local development
SECURE_BROWSER_XSS_FILTER = False
SECURE_CONTENT_TYPE_NOSNIFF = False
# Cookie isolation to prevent collisions with production in the same browser
SESSION_COOKIE_NAME = 'pwaninet_local_sessionid'
CSRF_COOKIE_NAME = 'pwaninet_local_csrftoken'
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# Proxy SSL header for reverse proxies and tunnels (Cloudflare Tunnel, Nginx)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True
USE_X_FORWARDED_PORT = True

# CSRF Trusted Origins for local development (supports LAN IPs, mobile testing & tunnels)
_local_ips = [
    '192.168.1.179',
    '192.168.114.221',
    '192.168.43.170',
    '192.168.127.221',
    '10.20.152.125',
    'localhost',
    '127.0.0.1',
    '0.0.0.0',
]
try:
    import subprocess
    _local_ips.extend(subprocess.check_output(['hostname', '-I'], text=True).strip().split())
except Exception:
    pass

_ports = ['8000', '8001', '3000']
_detected_origins = []
for _ip in set(_local_ips):
    if not _ip:
        continue
    _detected_origins.extend([f"http://{_ip}", f"https://{_ip}"])
    for _port in _ports:
        _detected_origins.extend([f"http://{_ip}:{_port}", f"https://{_ip}:{_port}"])

_env_csrf = [o.strip() for o in os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',') if o.strip()]

CSRF_TRUSTED_ORIGINS = list(set([
    'http://localhost:8000',
    'http://127.0.0.1:8000',
    'http://localhost:8001',
    'http://127.0.0.1:8001',
    'https://*.trycloudflare.com',
    'capacitor://localhost',
    'http://localhost',
    'https://localhost',
] + _detected_origins + _env_csrf))

CORS_ALLOW_ALL_ORIGINS = True

# Disable CSRF for API endpoints in local development
CSRF_COOKIE_HTTPONLY = False

# Disable Django cache in local dev
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.dummy.DummyCache"
    }
}

# Add CSRF exemption middleware for API endpoints in local dev
MIDDLEWARE = [
    'pwaninet.middleware.csrf_exempt.CSRFExemptMiddleware',
] + MIDDLEWARE

# WhiteNoise dev behavior
WHITENOISE_AUTOREFRESH = True
WHITENOISE_USE_FINDERS = True
WHITENOISE_MAX_AGE = 0

# Browser/static cache headers
SEND_FILE_MAX_AGE_DEFAULT = 0

AXES_ENABLED = False

# Celery configuration for local development
CELERY_BROKER_URL = 'redis://127.0.0.1:6379/0'
CELERY_RESULT_BACKEND = 'redis://127.0.0.1:6379/0'

# OAuth/OIDC - Allow HTTP redirect URIs in local development
OAUTH2_PROVIDER = {
    **OAUTH2_PROVIDER,
    'ALLOWED_REDIRECT_URI_SCHEMES': ['http', 'https'],
    'OIDC_ISS_ENDPOINT': 'http://127.0.0.1:8000/o',
}

# A developer-owned signing key lives outside the checkout. The base settings
# retain their existing environment/legacy fallback for production deployments.
_local_oidc_key = _Path.home() / ".config" / "vasto" / "keys" / "oidc_rsa_private.pem"
if not os.environ.get("OIDC_RSA_PRIVATE_KEY") and _local_oidc_key.is_file():
    OAUTH2_PROVIDER = {
        **OAUTH2_PROVIDER,
        "OIDC_RSA_PRIVATE_KEY": _local_oidc_key.read_text(),
    }
