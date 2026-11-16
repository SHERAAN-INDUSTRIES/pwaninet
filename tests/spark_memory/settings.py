"""Disposable offline database; never imports local settings or credentials.

SQLite supports service/API regressions. PostgreSQL-only legacy migrations
and vector search require the existing WP0 pgvector runner instead.
"""
import ast
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
SECRET_KEY = 'spark-memory-disposable-tests-only'
DEBUG = False
ALLOWED_HOSTS = ['testserver', 'localhost']
tree = ast.parse((BASE_DIR / 'pwaninet/settings/base.py').read_text())
INSTALLED_APPS = ast.literal_eval(next(n.value for n in tree.body if isinstance(n, ast.Assign)
    and any(isinstance(t, ast.Name) and t.id == 'INSTALLED_APPS' for t in n.targets)))
AUTH_USER_MODEL = 'users.User'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True
TIME_ZONE = 'UTC'
ROOT_URLCONF = 'tests.spark_memory.urls'
MIDDLEWARE = ['django.contrib.sessions.middleware.SessionMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware']
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'APP_DIRS': True, 'OPTIONS': {'context_processors': ['django.template.context_processors.request', 'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages']}}]
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3',
    'NAME': os.environ.get('SPARK_MEMORY_TEST_DB', ':memory:')}}
# Only used by the offline regression runner; makemigrations uses the real graph.
if os.environ.get('SPARK_MEMORY_SYNC_SCHEMA') == '1':
    MIGRATION_MODULES = {name.split('.')[-1]: None for name in INSTALLED_APPS}
STORAGES = {'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
STATIC_URL = '/static/'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
REST_FRAMEWORK = {'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework.authentication.SessionAuthentication']}
CELERY_BROKER_URL = 'memory://'
CELERY_RESULT_BACKEND = 'cache+memory://'
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
PWANIMATE_DEFAULT_LLM_PROVIDER = 'mock'
PWANIMATE_EMBEDDING_PROVIDER = 'mock'
LOGGING_CONFIG = None
if os.environ.get('SPARK_MEMORY_SYNC_SCHEMA') == '1':
    TEST_RUNNER = 'tests.spark_memory.runner.MemorySQLiteRunner'
