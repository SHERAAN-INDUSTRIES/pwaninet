"""Standalone settings; intentionally never imports local/base or loads .env."""
import ast
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
manifest = json.loads(Path(os.environ['SALS_WP0_MANIFEST']).read_text())
if (manifest.get('host') != '127.0.0.1' or manifest.get('database') != 'sals_wp0'
        or manifest.get('user') != 'sals_wp0' or not 1024 < int(manifest['port']) < 65536
        or not manifest.get('container', '').startswith('sals-wp0-')):
    raise RuntimeError('Refusing non-disposable database manifest')
SECRET_KEY = 'isolated-wp0-test-only'
DEBUG = False
ALLOWED_HOSTS = ['testserver', 'localhost']
# Reuse the declared app list without executing base.py or reading credentials.
tree = ast.parse((BASE_DIR / 'pwaninet/settings/base.py').read_text())
INSTALLED_APPS = ast.literal_eval(next(n.value for n in tree.body if isinstance(n, ast.Assign)
    and any(isinstance(t, ast.Name) and t.id == 'INSTALLED_APPS' for t in n.targets)))
DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql',
    'NAME': 'sals_wp0', 'USER': 'sals_wp0', 'PASSWORD': manifest['password'],
    'HOST': '127.0.0.1', 'PORT': manifest['port'], 'CONN_MAX_AGE': 0,
    'OPTIONS': {'connect_timeout': 5}, 'TEST': {'NAME': 'test_sals_wp0'}}}
AUTH_USER_MODEL = 'users.User'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True
TIME_ZONE = 'UTC'
ROOT_URLCONF = 'pwaninet.urls'
MIDDLEWARE = ['django.contrib.sessions.middleware.SessionMiddleware',
 'django.contrib.auth.middleware.AuthenticationMiddleware',
 'django.contrib.messages.middleware.MessageMiddleware']
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'APP_DIRS': True, 'DIRS': [BASE_DIR / 'templates'], 'OPTIONS': {'context_processors': [
 'django.template.context_processors.request', 'django.contrib.auth.context_processors.auth',
 'django.contrib.messages.context_processors.messages']}}]
MEDIA_ROOT = manifest['scratch'] + '/media'
STATIC_ROOT = manifest['scratch'] + '/static'
STATIC_URL = '/static/'
STORAGES = {'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
REST_FRAMEWORK = {'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework.authentication.SessionAuthentication']}
CELERY_BROKER_URL = 'memory://'
CELERY_RESULT_BACKEND = 'cache+memory://'
CELERY_TASK_ALWAYS_EAGER = False
PWANIMATE_DEFAULT_LLM_PROVIDER = 'mock'
PWANIMATE_EMBEDDING_PROVIDER = 'mock'
PWANIMATE_LLM_FALLBACK_CHAIN = [{'provider': 'mock', 'model': 'mock-llm-v1'}]
LOGGING_CONFIG = None
