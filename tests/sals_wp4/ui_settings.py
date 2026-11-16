"""Offline UI coverage: real URL namespaces/templates, disposable SQLite only."""
from copy import deepcopy
from tests.spark_memory.settings import *
ROOT_URLCONF='pwaninet.urls'
TEMPLATES=deepcopy(TEMPLATES)
TEMPLATES[0]['DIRS']=[str(BASE_DIR/'templates')]

TEMPLATES[0]['OPTIONS']['context_processors'].append('pwaninet.context_processors.release_metadata')
