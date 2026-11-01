import os
from celery import Celery

# Set the default Django settings module for the 'celery' program.
# Defaults safely to production if DJANGO_ENV is production; otherwise local for dev ergonomics.
if not os.environ.get('DJANGO_SETTINGS_MODULE'):
    if os.environ.get('DJANGO_ENV', '').strip().lower() == 'production':
        os.environ['DJANGO_SETTINGS_MODULE'] = 'pwaninet.settings.production'
    else:
        os.environ['DJANGO_SETTINGS_MODULE'] = 'pwaninet.settings.local'

app = Celery('pwaninet')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

try:
    import notifications.tasks  # noqa
except Exception:
    pass

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
