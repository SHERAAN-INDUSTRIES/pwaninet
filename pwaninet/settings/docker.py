"""Local Docker settings; service addresses are supplied by Compose."""
from .local import *
from pathlib import Path as _Path

CELERY_BROKER_URL = os.environ['CELERY_BROKER_URL']
CELERY_RESULT_BACKEND = os.environ['CELERY_RESULT_BACKEND']
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': os.environ['REDIS_URL'],
        'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient'},
    }
}
OAUTH2_PROVIDER = {
    **OAUTH2_PROVIDER,
    'OIDC_RSA_PRIVATE_KEY': _Path('/run/secrets/oidc.pem').read_text(),
    'OIDC_ISS_ENDPOINT': os.environ['LOCAL_BACKEND_URL'] + '/o',
}
CSRF_TRUSTED_ORIGINS += [os.environ['LOCAL_BACKEND_URL']]
