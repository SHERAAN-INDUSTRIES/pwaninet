"""
Base settings for pwaninet project.
"""
from pathlib import Path
import os
from datetime import timedelta

from dotenv import load_dotenv
import os

# Import version information (single source of truth)
from pwaninet import version

# Expose version metadata to settings
APP_VERSION = version.__version__
APP_BUILD_NUMBER = version.__build_number__
APP_ENVIRONMENT = version.__environment__
APP_RELEASE_DATE = version.__release_date__

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

load_dotenv(os.path.join(BASE_DIR, ".env"))

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-change-this-in-production')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = False

# ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1,10.20.152.125').split(',')
ALLOWED_HOSTS = [
    host.split(':')[0].strip()
    for host in os.environ.get(
        'ALLOWED_HOSTS',
        'localhost,127.0.0.1,10.20.152.125,pwaninet.app,192.168.1.179,192.168.114.221,192.168.43.170,192.168.127.221'
    ).split(',')
    if host.strip()
]

# Application definition
INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'django.contrib.postgres',
    'channels',
    'rest_framework',
    'corsheaders',
    'django_filters',
    'drf_spectacular',
    'rest_framework.authtoken',
    'oauth2_provider',
    'axes',
    'courses',
    'users',
    'posts',
    'groups',
    'notifications',
    'documents',
    'releases',
    'messaging',
    'search',
    'recommendations',
    'pwanimate',
    'pwanimate.billing',
    'admin_dashboard',
    'uploads',
    'workspace',
]

MIDDLEWARE = [
    'pwaninet.middleware.performance_timing.PerformanceTimingMiddleware',
    'pwaninet.middleware.auth_logging_middleware.AuthLoggingMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'pwaninet.middleware.device_block_middleware.BlockedDeviceMiddleware',
    'pwaninet.middleware.maintenance.MaintenanceModeMiddleware',
    'pwaninet.middleware.user_activity.UserActivityMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'axes.middleware.AxesMiddleware',
    'pwaninet.middleware.language_preference.LanguagePreferenceMiddleware',
    'pwaninet.middleware.cache_headers.CacheHeadersMiddleware',
]

# Enable only during a controlled performance run. Adds Server-Timing headers
# and one structured application/database timing log line per request.
PERFORMANCE_TIMING_ENABLED = os.environ.get('PERFORMANCE_TIMING_ENABLED', '0').lower() in ('1', 'true', 'yes')

ROOT_URLCONF = 'pwaninet.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'notifications.context_processors.notification_count',
                'uploads.context_processors.upload_flags',
                'pwaninet.context_processors.release_metadata',
                'pwaninet.context_processors.session_identity',
                'groups.context_processors.groups_nav_context',
                'messaging.context_processors.messaging_nav_context',
                'workspace.context_processors.workspace_notification',
            ],
        },
    }
]

WSGI_APPLICATION = 'pwaninet.wsgi.application'
ASGI_APPLICATION = 'pwaninet.asgi.application'

# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'pwaninet_db'),
        'USER': os.environ.get('DB_USER', 'postgres'),
        'PASSWORD': os.environ.get('DB_PASSWORD', 'postgres'),
        'HOST': os.environ.get('DB_HOST', 'localhost'),
        'PORT': os.environ.get('DB_PORT', '5432'),
        'CONN_MAX_AGE': 60,
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Nairobi'
USE_I18N = True
USE_L10N = True
USE_TZ = True

LOCALE_PATHS = [
    BASE_DIR / 'locale',
]

# Static files
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]

# Static file caching - Cache fonts for 1 year (fonts rarely change)
# This improves performance by avoiding repeated font downloads
STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# File upload settings
FILE_UPLOAD_TEMP_DIR = BASE_DIR / 'media' / 'temp'
# Must match the client-side validator in upload_validator.js (150MB)
FILE_UPLOAD_MAX_MEMORY_SIZE = 150 * 1024 * 1024  # 150MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 150 * 1024 * 1024  # 150MB

# Auth settings
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = 'posts:home'
LOGOUT_REDIRECT_URL = 'users:logout'
AUTH_USER_MODEL = 'users.User'

# vasto Workspace (external system) — configure per environment.
VASTO_WORKSPACE_BASE_URL = os.environ.get('VASTO_WORKSPACE_BASE_URL', '')

AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesStandaloneBackend',
    'users.backends.CaseInsensitiveAuthBackend',
    'django.contrib.auth.backends.ModelBackend',
]

# Security settings
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

# Session settings
SESSION_COOKIE_SECURE = False  # Set to True in production with HTTPS
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = False  # Set to True in production with HTTPS
CSRF_COOKIE_HTTPONLY = False  # Must be False so JS can read the csrftoken cookie for AJAX requests

# Two-Factor Authentication (2FA / TOTP) and Recovery Codes
MFA_ENCRYPTION_KEY = os.environ.get('MFA_ENCRYPTION_KEY', None)
TOTP_DIGITS = int(os.environ.get('TOTP_DIGITS', 6))
TOTP_INTERVAL = int(os.environ.get('TOTP_INTERVAL', 30))
TOTP_ALLOWED_DRIFT = int(os.environ.get('TOTP_ALLOWED_DRIFT', 1))
TOTP_ISSUER = os.environ.get('TOTP_ISSUER', 'pwaninet')
RECOVERY_CODE_COUNT = int(os.environ.get('RECOVERY_CODE_COUNT', 8))

# Google sign-in (Google Identity Services ID-token flow)
# Sign-in stays disabled until a Web Client ID is configured.
GOOGLE_WEB_CLIENT_ID = os.environ.get('GOOGLE_WEB_CLIENT_ID', '')
GOOGLE_ALLOWED_HD = os.environ.get('GOOGLE_ALLOWED_HD', '')
GOOGLE_JWKS_CACHE_TIMEOUT = int(os.environ.get('GOOGLE_JWKS_CACHE_TIMEOUT', 3600))
GOOGLE_LOGIN_ENABLED = os.environ.get('GOOGLE_LOGIN_ENABLED', 'True') == 'True'
GOOGLE_JWKS_URL = 'https://www.googleapis.com/oauth2/v3/certs'
# Fixed-window rate limit for POST /users/google/complete/ (per client IP).
# Effective only where a real cache backend is configured (production Redis);
# local/dev DummyCache performs no counting.
GOOGLE_COMPLETE_RATELIMIT_ATTEMPTS = int(os.environ.get('GOOGLE_COMPLETE_RATELIMIT_ATTEMPTS', 20))
GOOGLE_COMPLETE_RATELIMIT_WINDOW = int(os.environ.get('GOOGLE_COMPLETE_RATELIMIT_WINDOW', 300))

# New-device login alerts (Phase 1). GeoIP is optional: point GEOIP_PATH at a
# GeoLite2-City database directory (and install the geoip2 package) to show
# approximate city/country in device alerts.
DEVICE_VERIFICATION_EXPIRY_HOURS = int(os.environ.get('DEVICE_VERIFICATION_EXPIRY_HOURS', 24))
DEVICE_ALERT_EMAIL_FALLBACK = os.environ.get('DEVICE_ALERT_EMAIL_FALLBACK', 'True') == 'True'
GEOIP_PATH = os.environ.get('GEOIP_PATH', '')

# Caching configuration
CACHES = {
    'default': {
        'BACKEND': 'pwaninet.cache_backends.FallbackRedisCache',
        'LOCATION': os.environ.get('REDIS_URL', 'redis://127.0.0.1:6379/1'),
        'TIMEOUT': 300,
        'KEY_PREFIX': 'pwaninet',
    },
    'fallback': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'pwaninet-fallback-cache',
    }
}

# REST Framework configuration
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.TokenAuthentication',
        # 'rest_framework_simplejwt.authentication.JWTAuthentication',  # Temporarily disabled due to pkg_resources issue
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '100/hour',
        'user': '1000/hour',
        # Messaging throttling - FROZEN FOR MVP
        # 'message_send': '60/minute',
        # 'message_reaction': '30/minute',
        # 'conversation_create': '10/minute',
        # 'ws_message': '100/minute',
    },
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_VERSIONING_CLASS': 'rest_framework.versioning.URLPathVersioning',
    'DEFAULT_VERSION': 'v1',
    'ALLOWED_VERSIONS': ['v1'],
    'VERSION_PARAM': 'version',
}

# OAuth2 Provider Configuration
OAUTH2_PROVIDER = {
    # Scopes
    'SCOPES': {
        'openid': 'OpenID Connect identity',
        'profile': 'User profile information',
        'email': 'User email address',
    },

    # Token Lifetimes
    'ACCESS_TOKEN_EXPIRE_SECONDS': 7200,  # 2 hours
    'REFRESH_TOKEN_EXPIRE_SECONDS': 2592000,  # 30 days
    'AUTHORIZATION_CODE_EXPIRE_SECONDS': 600,  # 10 minutes

    # Refresh Token Settings
    'ROTATE_REFRESH_TOKEN': True,
    'REFRESH_TOKEN_REUSE_PROTECTION': True,
    'REFRESH_TOKEN_GRACE_PERIOD_SECONDS': 0,

    # PKCE Settings
    'PKCE_REQUIRED': True,
    'COMPLIANT_BCP_RFC9700_PKCE_METHOD': True,
    'COMPLIANT_BCP_RFC9700_PKCE_REQUIRED': True,

    # Redirect URI Settings
    'ALLOWED_REDIRECT_URI_SCHEMES': ['https'],
    'COMPLIANT_BCP_RFC9700_REDIRECT_URI_SCHEME': True,
    'COMPLIANT_BCP_RFC9700_REDIRECT_URI_MATCHING': True,
    'ALLOW_URI_WILDCARDS': False,

    # Grant Type Restrictions
    'COMPLIANT_BCP_RFC9700_IMPLICIT_GRANT': True,
    'COMPLIANT_BCP_RFC9700_PASSWORD_GRANT': True,

    # Access Token Transport
    'COMPLIANT_BCP_RFC9700_ACCESS_TOKEN_TRANSPORT': True,

    # Authorization Response Issuer
    'COMPLIANT_BCP_RFC9700_AUTHZ_RESPONSE_ISS': True,

    # OIDC Settings
    'OIDC_ENABLED': True,
    'OIDC_RSA_PRIVATE_KEY': os.environ.get('OIDC_RSA_PRIVATE_KEY') or (
        open(BASE_DIR / '.secrets' / 'oidc_rsa_private.pem').read()
        if (BASE_DIR / '.secrets' / 'oidc_rsa_private.pem').exists()
        else None
    ),
    'OIDC_ISS_ENDPOINT': 'https://pwaninet.app/o',
    'OIDC_COMPLIANT_SCOPE_CLAIMS': True,

    # Explicit Response Types and Grant Types
    'OAUTH2_RESPONSE_TYPES_SUPPORTED': ["code"],
    'OIDC_RESPONSE_TYPES_SUPPORTED': ["code"],
    'OAUTH2_GRANT_TYPES_SUPPORTED': ["authorization_code", "refresh_token"],

    # Token Endpoint Authentication Metadata
    'OAUTH_TOKEN_ENDPOINT_AUTH_METHODS_SUPPORTED': ['client_secret_basic'],
    'OAUTH2_REVOCATION_ENDPOINT_AUTH_METHODS_SUPPORTED': ['client_secret_basic'],
    'OAUTH2_INTROSPECTION_ENDPOINT_AUTH_METHODS_SUPPORTED': ['client_secret_basic'],

    # Consent Behavior
    'REQUEST_APPROVAL_PROMPT': 'auto',

    # Login URL for authorization (use Vasto's login route)
    'LOGIN_URL': '/login/',

    # Custom Validator
    'OAUTH2_VALIDATOR_CLASS': 'users.validators.CustomOAuth2Validator',

    # RFC 9700 Config Validation Gates
    'COMPLIANT_BCP_RFC9700_REFRESH_TOKEN': True,
}

# JWT Settings
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=60),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'AUTH_HEADER_TYPES': ('Bearer',),
}

# drf-spectacular settings
SPECTACULAR_SETTINGS = {
    'TITLE': 'Vasto API',
    'DESCRIPTION': 'Social networking platform for students and academic communities',
    'VERSION': version.__version__,
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,
    'COMPONENT_NO_READ_ONLY_REQUIRED': True,
    'ENUM_ADD_EXPLICIT_BLANK_NULL_CHOICE': True,
}

# django-axes configuration for rate limiting
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=5)
AXES_LOCKOUT_TEMPLATE = 'axes/lockout.html'
AXES_RESET_ON_SUCCESS = True
# AXES_LOCK_OUT_BY_COMBINATION_USER_AND_IP = True  # Deprecated in django-axes 6.0

# Channels configuration
#
# channels_redis only forwards extra connection kwargs (socket timeouts, retry,
# health checks) to the async Redis pool when each host entry is a dict. Bare
# URL strings / tuples are passed straight to from_url() with no options, so a
# stalled Redis connection can hang a WebSocket indefinitely with no retry.
_CHANNEL_REDIS_KWARGS = {
    "socket_connect_timeout": 5,
    "socket_timeout": 10,
    "retry_on_timeout": True,
    "health_check_interval": 30,
}


def _channel_hosts():
    """Build RedisChannelLayer host entries with connection-resilience options.

    Returns a list of dicts so channels_redis forwards socket timeouts, retry
    and health checks straight to the async Redis connection pool. A single
    stalled Redis connection can no longer block WebSocket group operations
    indefinitely.
    """
    url = os.environ.get('REDIS_URL', '')
    if url:
        # from_url() still receives the URL (db/password encoded in it), plus
        # the resilience kwargs below.
        return [{"address": url, **_CHANNEL_REDIS_KWARGS}]
    host = os.environ.get('REDIS_HOST', '127.0.0.1')
    port = int(os.environ.get('REDIS_PORT', 6379))
    password = os.environ.get('REDIS_PASSWORD') or None
    entry = {"host": host, "port": port, **_CHANNEL_REDIS_KWARGS}
    if password:
        entry["password"] = password
    return [entry]


CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': _channel_hosts(),
            'capacity': 300,
            'expiry': 15,
        },
    },
}

# Celery configuration
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://127.0.0.1:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://127.0.0.1:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = os.environ.get('TIME_ZONE', TIME_ZONE)
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_MAX_TASKS_PER_CHILD = 1000
CELERY_IMPORTS = (
    'notifications.tasks',
    # Keep Pwanimate's task package explicit so workers register attachment
    # processing as well as the existing ingestion and reconciliation tasks.
    'pwanimate.tasks',
    'pwanimate.tasks.conversation_memory',
    'messaging.tasks',
)

CELERY_TASK_DEFAULT_QUEUE = 'default'

CELERY_TASK_QUEUES = {
    'default': {},
    'media_queue': {},
    'docs_queue': {},
    'search_queue': {},
}

CELERY_TASK_ROUTES = {
    # Heavy video processing
    'posts.tasks.process_large_video': {'queue': 'media_queue'},
    # Chat video attachments share the media queue so default stays responsive
    'messaging.tasks.process_video_attachment_task': {'queue': 'media_queue'},
    'messaging.tasks.*': {'queue': 'media_queue'},
    
    # Background speech/transcription tasks (routed to search_queue so media_queue stays fast & responsive)
    'posts.tasks.extract_video_transcript': {'queue': 'search_queue'},
    
    # Document extraction & rendering pipelines
    'documents.tasks.processing.process_document': {'queue': 'docs_queue'},
    'documents.tasks.processing.process_file': {'queue': 'docs_queue'},
    'documents.tasks.processing.generate_thumbnail': {'queue': 'docs_queue'},
    'documents.tasks.processing.generate_preview': {'queue': 'docs_queue'},
    'documents.tasks.processing.extract_ocr_text_for_document': {'queue': 'docs_queue'},
    
    # Search indexing & suggestions tasks
    'search.tasks.*': {'queue': 'search_queue'},
    'documents.tasks.search_indexing.*': {'queue': 'search_queue'},
    
    # Upload Manager: finalize copies verified staging objects to public keys
    'uploads.tasks.finalize_upload_media': {'queue': 'media_queue'},
    'uploads.tasks.reconcile_stale_uploads': {'queue': 'default'},

    # Pwanimate ingestion & chunking tasks
    'pwanimate.tasks.*': {'queue': 'docs_queue'},

    # Fallback to default
    '*': {'queue': 'default'},
}

# Upload Manager: resumable direct-to-R2 engine for the post composer.
# Always enabled by default - all post uploads use the new engine.
USE_RESUMABLE_UPLOADS = os.environ.get('USE_RESUMABLE_UPLOADS', '1') == '1'

# Celery Beat periodic task schedules
CELERY_BEAT_SCHEDULE = {
    'pwanimate-reconcile-document-ingestion': {
        'task': 'pwanimate.tasks.reconciliation.reconcile_document_ingestion',
        'schedule': float(os.environ.get('PWANIMATE_RECONCILE_INTERVAL_SECONDS', 600.0)),  # every 10 minutes
        'options': {'queue': 'docs_queue'},
    },
    'uploads-reconcile-stale-uploads': {
        'task': 'uploads.tasks.reconcile_stale_uploads',
        'schedule': 1800.0,  # every 30 minutes
        'options': {'queue': 'default'},
    },
    'users-expire-device-verifications': {
        'task': 'users.tasks.expire_device_verifications',
        'schedule': 900.0,  # every 15 minutes
        'options': {'queue': 'default'},
    },
}

# Pwanimate reconciliation operational limits
PWANIMATE_RECONCILE_MAX_INSPECT = int(os.environ.get('PWANIMATE_RECONCILE_MAX_INSPECT', 50))
PWANIMATE_RECONCILE_MAX_INGEST = int(os.environ.get('PWANIMATE_RECONCILE_MAX_INGEST', 10))
PWANIMATE_RECONCILE_MAX_EMBED = int(os.environ.get('PWANIMATE_RECONCILE_MAX_EMBED', 10))

# CORS settings (includes Capacitor origins and CDN)
_default_cors = [
    'http://localhost',
    'https://localhost',
    'http://localhost:3000',
    'http://localhost:8000',
    'capacitor://localhost',
    'http://10.0.2.2:8000',
    'https://pwaninet.app',
    'https://cdn.pwaninet.app',
]
_env_cors = [c.strip() for c in os.environ.get('CORS_ALLOWED_ORIGINS', '').split(',') if c.strip()]
CORS_ALLOWED_ORIGINS = list(set(_default_cors + _env_cors))
CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^https?://192\.168\.\d+\.\d+(:\d+)?$",
    r"^https?://10\.\d+\.\d+\.\d+(:\d+)?$",
    r"^https?://172\.(1[6-9]|2\d|3[01])\.\d+\.\d+(:\d+)?$",
]
CORS_ALLOW_CREDENTIALS = True

# Proxy SSL header for reverse proxies and tunnels (Cloudflare Tunnel, Nginx)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True
USE_X_FORWARDED_PORT = True

# CSRF settings (supports both http:// and https:// for web and Capacitor)
_csrf_hosts = [h.split(':')[0].strip() for h in ALLOWED_HOSTS if h and h != '*']
CSRF_TRUSTED_ORIGINS = list(set([
    'https://pwaninet.app',
    'https://*.pwaninet.app',
    'http://pwaninet.app',
    'https://*.trycloudflare.com',
    'https://cdn.pwaninet.app',
    'capacitor://localhost',
    'http://localhost',
    'http://localhost:8000',
    'https://localhost',
    'https://localhost:8000',
    'http://127.0.0.1',
    'http://127.0.0.1:8000',
    'https://127.0.0.1',
    'https://127.0.0.1:8000',
    *[f"http://{h}" for h in _csrf_hosts],
    *[f"https://{h}" for h in _csrf_hosts],
    *[f"http://{h}:8000" for h in _csrf_hosts],
    *[f"https://{h}:8000" for h in _csrf_hosts],
]))

# VAPID keys for Web Push notifications
VAPID_PUBLIC_KEY = os.getenv('VAPID_PUBLIC_KEY', '')
VAPID_PRIVATE_KEY = os.getenv('VAPID_PRIVATE_KEY', '')
VAPID_SUBJECT = os.getenv('VAPID_SUBJECT', 'mailto:admin@pwaninet.app')
FIREBASE_CREDENTIALS_PATH = os.getenv('FIREBASE_CREDENTIALS_PATH', os.path.join(BASE_DIR, 'firebase-adminsdk.json'))

# Email configuration
EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'localhost')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True') == 'True'
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@pwaninet.app')
SERVER_EMAIL = os.environ.get('SERVER_EMAIL', DEFAULT_FROM_EMAIL)

# Notification processing configuration
ENABLE_NOTIFICATION_PROCESSING = True

# ---------------------------------------------------------------------------
# Cloud Storage & CDN (django-storages + boto3 → Cloudflare R2)
# ---------------------------------------------------------------------------
# Set USE_S3=1 in .env to activate.  Locally the app stays on local disk.
# All values come from .env — no secrets are hardcoded here.
# ---------------------------------------------------------------------------
USE_S3 = os.environ.get('USE_S3', '0') == '1'
PWANIMATE_PRIVATE_MEDIA_BUCKET = os.environ.get('PWANIMATE_PRIVATE_MEDIA_BUCKET', '')
PWANIMATE_PRIVATE_MEDIA_ROOT = os.environ.get(
    'PWANIMATE_PRIVATE_MEDIA_ROOT',
    str(BASE_DIR / 'private_media'),
)

# Expose CDN domain at module level so Post.get_hls_url can use it
CDN_DOMAIN = os.environ.get('CDN_DOMAIN', '')

if USE_S3:
    from botocore.config import Config

    # --- Credentials & bucket ------------------------------------------------
    AWS_ACCESS_KEY_ID       = os.environ.get('AWS_ACCESS_KEY_ID', '')
    AWS_SECRET_ACCESS_KEY   = os.environ.get('AWS_SECRET_ACCESS_KEY', '')
    AWS_STORAGE_BUCKET_NAME = os.environ.get('AWS_STORAGE_BUCKET_NAME', '')

    # Cloudflare R2 endpoint:  https://<accountid>.r2.cloudflarestorage.com
    # MinIO endpoint:          http://localhost:9000
    AWS_S3_ENDPOINT_URL     = os.environ.get('AWS_S3_ENDPOINT_URL', '')
    AWS_S3_REGION_NAME      = os.environ.get('AWS_S3_REGION_NAME', 'auto')
    AWS_S3_ADDRESSING_STYLE = 'path'
    AWS_S3_SIGNATURE_VERSION = 's3v4'

    # Development prefix to separate dev uploads from production
    R2_DEV_PREFIX = os.environ.get('R2_DEV_PREFIX', '')

    # --- R2/MinIO-specific: ACLs are NOT supported -------------------------
    # Empty string from .env must become None so boto3 never sends an ACL header
    _acl_raw        = os.environ.get('AWS_DEFAULT_ACL', '')
    AWS_DEFAULT_ACL = _acl_raw if _acl_raw else None

    # Boto3 client config for Cloudflare R2 and MinIO compatibility:
    AWS_S3_CLIENT_CONFIG = Config(
        signature_version='s3v4',
        s3={'addressing_style': 'path'},
    )

    AWS_S3_OBJECT_PARAMETERS = {
        'CacheControl': 'max-age=86400, public',
    }
    AWS_S3_FILE_OVERWRITE = False
    AWS_QUERYSTRING_AUTH  = False   # public bucket — no signed URLs for reads

    # Expose boto3 config so Celery tasks can upload HLS segments directly
    R2_BOTO3_CONFIG = {
        'endpoint_url':          AWS_S3_ENDPOINT_URL,
        'aws_access_key_id':     AWS_ACCESS_KEY_ID,
        'aws_secret_access_key': AWS_SECRET_ACCESS_KEY,
        'region_name':           AWS_S3_REGION_NAME,
        'config':                AWS_S3_CLIENT_CONFIG,
    }

    # --- CDN edge domain (cdn.pwaninet.app) ----------------------------------
    AWS_S3_CUSTOM_DOMAIN = CDN_DOMAIN

    # Allow opting into S3 for staticfiles if explicitly set (default: keep static local for WhiteNoise)
    USE_S3_STATIC = os.environ.get('USE_S3_STATIC', '0') == '1'

    # --- Storage backend routing (django-storages) ---------------------------
    # Add dev prefix to location to separate dev from production
    media_location = f'{R2_DEV_PREFIX}/media' if R2_DEV_PREFIX else 'media'

    STORAGES = {
        'default': {
            # All media uploads (video, images, HLS segments, docs, audio)
            'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage',
            'OPTIONS': {
                'bucket_name':       AWS_STORAGE_BUCKET_NAME,
                'endpoint_url':      AWS_S3_ENDPOINT_URL,
                'region_name':       AWS_S3_REGION_NAME,
                'access_key':        AWS_ACCESS_KEY_ID,
                'secret_key':        AWS_SECRET_ACCESS_KEY,
                'location':          media_location,
                'default_acl':       None,          # R2/MinIO: no ACLs
                'file_overwrite':    False,
                'custom_domain':     CDN_DOMAIN or None,
                'object_parameters': {'CacheControl': 'max-age=604800, public'},  # 7 days
                'signature_version': 's3v4',
                'addressing_style':  'path',
                'client_config':     AWS_S3_CLIENT_CONFIG,
            },
        },
        'staticfiles': {
            # Static files stay local for WhiteNoise / Nginx unless USE_S3_STATIC=1.
            # The fingerprinted backends append a content-derived ?v= to every
            # static URL. Without it the URLs only changed when the release
            # record in the database was bumped, and Cloudflare's multi-year
            # cache kept serving months-old CSS/JS. See pwaninet/static_storage.py.
            'BACKEND': 'pwaninet.static_storage.FingerprintedS3StaticFilesStorage' if USE_S3_STATIC else 'pwaninet.static_storage.FingerprintedStaticFilesStorage',
            **({
                'OPTIONS': {
                    'bucket_name':       AWS_STORAGE_BUCKET_NAME,
                    'endpoint_url':      AWS_S3_ENDPOINT_URL,
                    'region_name':       AWS_S3_REGION_NAME,
                    'access_key':        AWS_ACCESS_KEY_ID,
                    'secret_key':        AWS_SECRET_ACCESS_KEY,
                    'location':          'static',
                    'default_acl':       None,
                    'file_overwrite':    True,
                    'custom_domain':     CDN_DOMAIN or None,
                    'object_parameters': {'CacheControl': 'max-age-31536000, public'},
                    'signature_version': 's3v4',
                    'addressing_style':  'path',
                    'client_config':     AWS_S3_CLIENT_CONFIG,
                }
            } if USE_S3_STATIC else {})
        },
    }

    # Rewrite Django's MEDIA_URL to the CDN edge (only when set;
    # empty CDN_DOMAIN would build the broken 'https:///media/').
    if CDN_DOMAIN:
        MEDIA_URL  = f'https://{CDN_DOMAIN}/media/'
    if USE_S3_STATIC:
        if CDN_DOMAIN:
            STATIC_URL = f'https://{CDN_DOMAIN}/static/'

# Logging configuration
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
            'formatter': 'verbose',
        },
        'video_console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'performance': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'users.auth': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'users': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'pwanimate': {
            'handlers': ['console'],
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

# ---------------------------------------------------------------------------
# Pwanimate Configuration
# ---------------------------------------------------------------------------
PWANIMATE_ENABLED = os.environ.get('PWANIMATE_ENABLED', 'True').lower() in ('true', '1', 'yes')
# Public web search is optional. Empty provider keys leave Pwanimate running and
# produce a safe unavailable result only when a web search is requested.
WEB_SEARCH_ENABLED = os.environ.get('WEB_SEARCH_ENABLED', 'true').lower() in ('true', '1', 'yes', 'on')
WEB_SEARCH_PROVIDER_ORDER = os.environ.get('WEB_SEARCH_PROVIDER_ORDER', 'tavily,brave,serper').split(',')
TAVILY_API_KEY = os.environ.get('TAVILY_API_KEY', '')
BRAVE_SEARCH_API_KEY = os.environ.get('BRAVE_SEARCH_API_KEY', '')
SERPER_API_KEY = os.environ.get('SERPER_API_KEY', '')
WEB_SEARCH_TIMEOUT_SECONDS = float(os.environ.get('WEB_SEARCH_TIMEOUT_SECONDS', '10'))
WEB_SEARCH_MAX_RESULTS = int(os.environ.get('WEB_SEARCH_MAX_RESULTS', '5'))
PWANIMATE_GEMINI_API_KEY = os.environ.get('PWANIMATE_GEMINI_API_KEY', '')
PWANIMATE_GROQ_API_KEY = os.environ.get('PWANIMATE_GROQ_API_KEY', '')
PWANIMATE_SPEECH_TO_TEXT_MODEL = os.environ.get('PWANIMATE_SPEECH_TO_TEXT_MODEL', 'whisper-large-v3-turbo')
PWANIMATE_SPEECH_TO_TEXT_TIMEOUT = int(os.environ.get('PWANIMATE_SPEECH_TO_TEXT_TIMEOUT', '60'))
PWANIMATE_VOICE_MAX_BYTES = int(os.environ.get('PWANIMATE_VOICE_MAX_BYTES', str(25 * 1024 * 1024)))
PWANIMATE_OPENROUTER_API_KEY = os.environ.get('PWANIMATE_OPENROUTER_API_KEY', '')
PWANIMATE_TTS_PROVIDER = os.environ.get('PWANIMATE_TTS_PROVIDER', 'azure')
PWANIMATE_AZURE_SPEECH_KEY = os.environ.get('PWANIMATE_AZURE_SPEECH_KEY', '')
PWANIMATE_AZURE_SPEECH_REGION = os.environ.get('PWANIMATE_AZURE_SPEECH_REGION', '')
PWANIMATE_TTS_TIMEOUT = int(os.environ.get('PWANIMATE_TTS_TIMEOUT', '15'))
PWANIMATE_TTS_MAX_CHARACTERS = int(os.environ.get('PWANIMATE_TTS_MAX_CHARACTERS', '4000'))

# Embedding Subsystem
PWANIMATE_EMBEDDING_PROVIDER = os.environ.get('PWANIMATE_EMBEDDING_PROVIDER', 'gemini')
PWANIMATE_EMBEDDING_MODEL = os.environ.get('PWANIMATE_EMBEDDING_MODEL', 'gemini-embedding-2')
PWANIMATE_EMBEDDING_DIMENSIONS = int(os.environ.get('PWANIMATE_EMBEDDING_DIMENSIONS', '768'))
PWANIMATE_EMBEDDING_BATCH_SIZE = int(os.environ.get('PWANIMATE_EMBEDDING_BATCH_SIZE', '32'))

# Self-hosted spark-vector embedding worker (docs/local-models-design.md §4-§5).
# Set PWANIMATE_EMBEDDING_PROVIDER=selfhosted to cut docs + queries over to it.
PWANIMATE_SELFHOSTED_EMBEDDINGS_URL = os.environ.get('PWANIMATE_SELFHOSTED_EMBEDDINGS_URL', 'http://spark_vector:8100/v1/embeddings')
PWANIMATE_SELFHOSTED_EMBEDDINGS_MODEL = os.environ.get('PWANIMATE_SELFHOSTED_EMBEDDINGS_MODEL', 'multilingual-e5-base')
PWANIMATE_SELFHOSTED_EMBEDDINGS_TIMEOUT = int(os.environ.get('PWANIMATE_SELFHOSTED_EMBEDDINGS_TIMEOUT', '10'))
PWANIMATE_SELFHOSTED_EMBEDDINGS_TOKEN = os.environ.get('PWANIMATE_SELFHOSTED_EMBEDDINGS_TOKEN', '')
# Sub-batch size per HTTP request. One big request would hold the worker's
# single inference slot (and exceed its per-request token budget), starving
# interactive chat queries — ingestion is split into small batches instead.
# 4 chunks ≈ 1.7 s of inference: the worst-case wait an interactive query
# can face while ingestion runs.
PWANIMATE_SELFHOSTED_EMBEDDING_BATCH_SIZE = int(os.environ.get('PWANIMATE_SELFHOSTED_EMBEDDING_BATCH_SIZE', '4'))

# --- M-Pesa Daraja (payments) ---------------------------------------------
# Sandbox by default: with no credentials configured the payment flow runs in
# DEMO mode (no network, synthetic checkout id, simulated callback). Adding
# the sandbox credentials and flipping DARAJA_ENV=production is the only
# difference between the demo and the real thing.
DARAJA_ENV = os.environ.get('DARAJA_ENV', 'sandbox').strip().lower()  # sandbox|production
DARAJA_BASE_URL = os.environ.get(
    'DARAJA_BASE_URL',
    'https://sandbox.safaricom.co.ke/mpesa/' if DARAJA_ENV != 'production'
    else 'https://api.safaricom.co.ke/mpesa/',
).rstrip('/') + '/'
DARAJA_CONSUMER_KEY = os.environ.get('DARAJA_CONSUMER_KEY', '')
DARAJA_CONSUMER_SECRET = os.environ.get('DARAJA_CONSUMER_SECRET', '')
DARAJA_SHORTCODE = os.environ.get('DARAJA_SHORTCODE', '')      # paybill or till number
DARAJA_PASSKEY = os.environ.get('DARAJA_PASSKEY', '')          # used to build the request password
DARAJA_INITIATOR = os.environ.get('DARAJA_INITIATOR', '')      # for the reconciliation APIs
DARAJA_CREDENTIAL = os.environ.get('DARAJA_CREDENTIAL', '')    # initiator security credential
DARAJA_CALLBACK_URL = os.environ.get(
    'DARAJA_CALLBACK_URL', 'https://pwaninet.app/api/pwanimate/billing/mpesa/callback/'
)
DARAJA_TIMEOUT = int(os.environ.get('DARAJA_TIMEOUT', '30'))
# Allow the demo endpoints only when no live credentials are configured.
DARAJA_DEMO_ENABLED = os.environ.get(
    'DARAJA_DEMO_ENABLED',
    '1' if not (DARAJA_CONSUMER_KEY and DARAJA_CONSUMER_SECRET and DARAJA_SHORTCODE and DARAJA_PASSKEY) else '0',
) == '1'

# Multi-Provider Embedding Configuration Placeholders
PWANIMATE_JINA_API_KEY = os.environ.get('PWANIMATE_JINA_API_KEY', os.environ.get('JINA_API_KEY', ''))
PWANIMATE_JINA_MODEL = os.environ.get('PWANIMATE_JINA_MODEL', 'jina-embeddings-v3')

PWANIMATE_VOYAGE_API_KEY = os.environ.get('PWANIMATE_VOYAGE_API_KEY', os.environ.get('VOYAGE_API_KEY', ''))
PWANIMATE_VOYAGE_MODEL = os.environ.get('PWANIMATE_VOYAGE_MODEL', 'voyage-3')

PWANIMATE_CLOUDFLARE_API_TOKEN = os.environ.get('PWANIMATE_CLOUDFLARE_API_TOKEN', os.environ.get('CLOUDFLARE_API_TOKEN', ''))
PWANIMATE_CLOUDFLARE_ACCOUNT_ID = os.environ.get('PWANIMATE_CLOUDFLARE_ACCOUNT_ID', os.environ.get('CLOUDFLARE_ACCOUNT_ID', ''))
PWANIMATE_CLOUDFLARE_MODEL = os.environ.get('PWANIMATE_CLOUDFLARE_MODEL', os.environ.get('CLOUDFLARE_EMBEDDING_MODEL', '@cf/baai/bge-base-en-v1.5'))

# AI Gateway (LLM Generation)
PWANIMATE_DEFAULT_LLM_PROVIDER = os.environ.get('PWANIMATE_DEFAULT_LLM_PROVIDER', 'gemini')
PWANIMATE_GEMINI_MODEL = os.environ.get('PWANIMATE_GEMINI_MODEL', 'gemini-3.8-flash')
PWANIMATE_VISION_MODEL = os.environ.get('PWANIMATE_VISION_MODEL', 'gemini-3.8-flash')
PWANIMATE_VISION_FALLBACK_CHAIN = [
    {"provider": "cloudflare", "model": os.environ.get('PWANIMATE_CLOUDFLARE_VISION_MODEL', '@cf/meta/llama-3.2-11b-vision-instruct')},
    {"provider": "groq", "model": os.environ.get('PWANIMATE_GROQ_VISION_MODEL', 'qwen/qwen3.8-27b')},
]
PWANIMATE_ATTACHMENT_VISION_MAX_PAGES = int(os.environ.get('PWANIMATE_ATTACHMENT_VISION_MAX_PAGES', '20'))
# Account-wide daily cap on image analyses. The Workers AI free allocation
# (10,000 neurons/day) is shared by every user, so the per-user tier cap is
# not enough to protect it: 56 Ember students at 5 images/day would exhaust
# it. 0 disables the budget. See pwanimate/billing/global_budget.py.
PWANIMATE_GLOBAL_DAILY_IMAGE_BUDGET = int(os.environ.get('PWANIMATE_GLOBAL_DAILY_IMAGE_BUDGET', '200'))
# Largest edge (px) of an image sent to a vision model. Larger inputs cost
# more tokens (and therefore more neurons) for no extra OCR accuracy.
PWANIMATE_VISION_MAX_IMAGE_EDGE = int(os.environ.get('PWANIMATE_VISION_MAX_IMAGE_EDGE', '1024'))
# Maximum images in a single multimodal turn.
PWANIMATE_VISION_MAX_IMAGES = int(os.environ.get('PWANIMATE_VISION_MAX_IMAGES', '10'))
# Cloudflare Workers AI -- the vision inference tier.
#
# The token is AI-scoped: it can run inference but cannot read zone
# settings. Workers AI bills at $0.011 / 1,000 neurons with a free daily
# allocation of 10,000 neurons, which is the account-wide budget the
# per-user image caps are sized against.
PWANIMATE_CLOUDFLARE_VISION_MODEL = os.environ.get(
    'PWANIMATE_CLOUDFLARE_VISION_MODEL', '@cf/meta/llama-3.2-11b-vision-instruct'
)
PWANIMATE_CLOUDFLARE_VISION_BACKUP_MODEL = os.environ.get(
    'PWANIMATE_CLOUDFLARE_VISION_BACKUP_MODEL', '@cf/meta/llama-4-scout-17b-16e-instruct'
)
# Which (provider, model) pairs may receive images. Anything not listed is
# treated as text-only. See pwanimate/ai/capabilities.py.
PWANIMATE_VISION_CAPABLE_MODELS = {
    'cloudflare': [
        PWANIMATE_CLOUDFLARE_VISION_MODEL,
        PWANIMATE_CLOUDFLARE_VISION_BACKUP_MODEL,
    ],
}
PWANIMATE_GROQ_MODEL = os.environ.get('PWANIMATE_GROQ_MODEL', 'openai/gpt-oss-120b')
PWANIMATE_OPENROUTER_MODEL = os.environ.get('PWANIMATE_OPENROUTER_MODEL', 'nvidia/nemotron-3-ultra-550b-a55b:free')
PWANIMATE_LLM_TIMEOUT = float(os.environ.get('PWANIMATE_LLM_TIMEOUT', '30.0'))
PWANIMATE_LLM_CONNECT_TIMEOUT = float(os.environ.get('PWANIMATE_LLM_CONNECT_TIMEOUT', '2.0'))
PWANIMATE_PROVIDER_FALLBACK_ORDER = os.environ.get(
    'PWANIMATE_PROVIDER_FALLBACK_ORDER', 'gemini,groq,openrouter'
).split(',')
PWANIMATE_RATE_LIMIT_COOLDOWN = float(os.environ.get('PWANIMATE_RATE_LIMIT_COOLDOWN', '60'))

# Model-level fallback chain: interleaved across providers so that when one provider
# is throttled or quota-depleted, the gateway immediately fails over to an available provider.
PWANIMATE_LLM_FALLBACK_CHAIN = [
    {"provider": "gemini", "model": "gemini-3.8-flash"},
    {"provider": "groq", "model": "openai/gpt-oss-120b"},
    {"provider": "gemini", "model": "gemini-3.6-flash"},
    {"provider": "openrouter", "model": "nvidia/nemotron-3-ultra-550b-a55b:free"},
    {"provider": "groq", "model": "openai/gpt-oss-20b"},
    {"provider": "gemini", "model": "gemini-3.5-flash-lite"},
    {"provider": "openrouter", "model": "nvidia/nemotron-3.5-lightning:free"},
    {"provider": "groq", "model": "qwen/qwen3.8-27b"},
    {"provider": "gemini", "model": "gemini-flash-lite-latest"},
    {"provider": "openrouter", "model": "nvidia/nemotron-3-super-120b-a12b:free"},
    {"provider": "gemini", "model": "gemini-flash-latest"},
    {"provider": "gemini", "model": "gemini-pro-latest"},
    {"provider": "openrouter", "model": "liquid/lfm-2.5-2.6b:free"},
    # Final fallback: mock provider returns a friendly error message
    # instead of raising a 502 when all real providers are unreachable
    {"provider": "mock", "model": "unavailable"},
]

# Pwanimate Attachments Subsystem
# KLIPY Integration Settings
KLIPY_API_KEY = os.environ.get('KLIPY_API_KEY', 'eTJSGDKLf98x2n9QMKeNQoObF8fxavwQC2wGrfbWyinrYSH8mTtlySyZ7zlPW1Yf')
KLIPY_API_BASE_URL = 'https://api.klipy.com/api/v1'

PWANIMATE_ATTACHMENTS_DIR = 'pwanimate/attachments'
PWANIMATE_MAX_IMAGE_SIZE = int(os.environ.get('PWANIMATE_MAX_IMAGE_SIZE', 10 * 1024 * 1024))   # 10MB
PWANIMATE_MAX_DOC_SIZE = int(os.environ.get('PWANIMATE_MAX_DOC_SIZE', 25 * 1024 * 1024))       # 25MB
PWANIMATE_MAX_EXTRACT_CHARS = int(os.environ.get('PWANIMATE_MAX_EXTRACT_CHARS', 15000))        # 15,000 chars per attachment

# Internal API for Workspaces Integration
VASTO_SERVICE_TOKEN = os.environ.get('VASTO_SERVICE_TOKEN', '')
VASTO_INTERNAL_API_ENABLED = os.environ.get('VASTO_INTERNAL_API_ENABLED', 'false').lower() == 'true'
INTERNAL_API_ALLOWED_IPS = os.environ.get('INTERNAL_API_ALLOWED_IPS', '127.0.0.1').split(',')
