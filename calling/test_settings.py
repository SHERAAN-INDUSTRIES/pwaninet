"""Calling integration tests use PostgreSQL, matching the application's migrations.

Run: python manage.py test calling --settings=calling.test_settings --noinput
Only the dedicated test database is created; the application database is untouched.
"""
from pwaninet.settings.test import *
from pwaninet.settings.local import DATABASES as LOCAL_DATABASES
from copy import deepcopy

DATABASES = deepcopy(LOCAL_DATABASES)
DATABASES['default']['TEST'] = {'NAME': 'test_pwaninet_calling'}
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
CELERY_TASK_ALWAYS_EAGER = False
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'

