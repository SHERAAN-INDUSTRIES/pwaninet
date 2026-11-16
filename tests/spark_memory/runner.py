"""Offline service tests only; do not claim PostgreSQL index/migration coverage."""
from django.apps import apps
from django.contrib.postgres.indexes import PostgresIndex
from django.test.runner import DiscoverRunner


class MemorySQLiteRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        # Legacy app models declare PostgreSQL GIN/trigram indexes that SQLite
        # cannot create. Keep all ordinary memory indexes/unique/check constraints.
        for model in apps.get_models():
            model._meta.indexes = [i for i in model._meta.indexes if not isinstance(i, PostgresIndex)]
        return super().setup_databases(**kwargs)
