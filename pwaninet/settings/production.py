"""
Production settings.
"""
import os
import sys
from .base import *

DEBUG = False

# Allowed hosts configuration
_default_hosts = ['pwaninet.app', 'www.pwaninet.app', 'localhost', '127.0.0.1']
_env_hosts = [
    host.split(':')[0].strip()
    for host in os.environ.get('ALLOWED_HOSTS', '').split(',')
    if host.strip()
]
ALLOWED_HOSTS = list(set(_default_hosts + _env_hosts))

# PostgreSQL database configuration
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'pwaninet_db'),
        'USER': os.environ.get('DB_USER', 'postgres'),
        'PASSWORD': os.environ.get('DB_PASSWORD', ''),
        'HOST': os.environ.get('DB_HOST', 'localhost'),
        'PORT': os.environ.get('DB_PORT', '5432'),
        'CONN_MAX_AGE': int(os.environ.get('DB_CONN_MAX_AGE', 600)),
    }
}

# Proxy SSL Header configuration for Cloudflare Tunnel / Nginx / reverse proxies
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True
USE_X_FORWARDED_PORT = True

# Security settings for production
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_SSL_REDIRECT = os.environ.get('SECURE_SSL_REDIRECT', 'True') == 'True'

# Cookie and session security
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False  # Must be False — JS reads csrftoken cookie for AJAX POSTs (upload, etc.)
                               # Django docs: unlike SESSION_COOKIE_HTTPONLY, this provides no security benefit
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'

# Brute-force rate limiting
AXES_ENABLED = True

# CSRF Trusted Origins configuration
_prod_csrf = [
    'https://pwaninet.app',
    'https://*.pwaninet.app',
    'https://*.trycloudflare.com',
    'https://cdn.pwaninet.app',
    'capacitor://localhost',
    *[f"https://{h}" for h in ALLOWED_HOSTS if h and not h.startswith('.')],
]
CSRF_TRUSTED_ORIGINS = list(set(CSRF_TRUSTED_ORIGINS + _prod_csrf))

# Channels layer Redis configuration for production multi-worker environments
REDIS_URL = os.environ.get('REDIS_URL')
REDIS_PASSWORD = os.environ.get('REDIS_PASSWORD')
if REDIS_URL:
    # Embed the password into the connection URL when Redis requires auth
    # (production Redis is started with --requirepass). Without this the
    # channel layer connects unauthenticated and every group operation fails.
    if REDIS_PASSWORD and "@" not in REDIS_URL.split("://", 1)[-1]:
        _scheme, _rest = REDIS_URL.split("://", 1)
        REDIS_URL = f"{_scheme}://:{REDIS_PASSWORD}@{_rest}"
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {
                # Dict entries (not bare URL strings) let channels_redis forward
                # socket timeouts, retry and health checks to the async Redis
                # pool, so a stalled Redis connection cannot hang WebSocket
                # group_add/group_discard indefinitely.
                'hosts': [
                    {
                        'address': REDIS_URL,
                        'socket_connect_timeout': 5,
                        'socket_timeout': 10,
                        'retry_on_timeout': True,
                        'health_check_interval': 30,
                    }
                ],
                'capacity': 300,
                'expiry': 15,
            },
        },
    }

# Logging bootstrap - ensure logs directory exists to prevent startup crashes
LOGS_DIR = BASE_DIR / 'logs'
try:
    os.makedirs(LOGS_DIR, exist_ok=True)
except Exception:
    pass

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'stream': sys.stdout,
            'formatter': 'verbose',
        },
        'video_console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'stream': sys.stdout,
            'formatter': 'verbose',
        },
        'file': {
            'level': 'WARNING',
            'class': 'logging.FileHandler',
            'filename': LOGS_DIR / 'django.log',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'performance': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'django': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': True,
        },
        'users.auth': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'search': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'pwanimate': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': True,
        },
        'posts.video_processing': {
            'handlers': ['video_console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# ==============================================================================
# WHITENOISE PRODUCTION CACHE & ASSET OPTIMIZATION
# ==============================================================================
# Enable compressed manifest storage for hashed, immutable static assets
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Bump static file caching to 1 year (31,536,000 seconds)
WHITENOISE_MAX_AGE = 31536000

# Set immutable flag on static files for zero-revalidation browser caching
WHITENOISE_IMMUTABLE_FILE_TEST = lambda path, url: True

# OAuth/OIDC - Enforce HTTPS-only redirect URIs in production
OAUTH2_PROVIDER = {
    **OAUTH2_PROVIDER,
    'ALLOWED_REDIRECT_URI_SCHEMES': ['https'],
}
