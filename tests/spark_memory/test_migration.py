"""Apply the real new migration to the pre-phase-1 memory schema.

This exercises 0029, without pretending SQLite can run legacy PostgreSQL
trigram migrations elsewhere in the project.
"""
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TransactionTestCase, override_settings

from pwanimate.models import SparkMemoryExtraction, SparkMemoryPreference, SparkPersonalMemory


class PhaseOneMigrationTests(TransactionTestCase):
    def test_upgrade_preserves_approved_facts_and_requires_automatic_opt_in(self):
        owner = get_user_model().objects.create_user(username='migration-owner')
        with override_settings(MIGRATION_MODULES={}):
            loader = MigrationLoader(None)
        state = loader.project_state([('pwanimate', '0028_alter_sparkagentinteraction_input_type')])
        old_pref = state.apps.get_model('pwanimate', 'SparkMemoryPreference')
        old_fact = state.apps.get_model('pwanimate', 'SparkPersonalMemory')
        # All other current application tables already exist in the test DB.
        with connection.schema_editor() as editor:
            editor.delete_model(SparkMemoryExtraction)
            editor.delete_model(SparkPersonalMemory)
            editor.delete_model(SparkMemoryPreference)
            editor.create_model(old_pref)
            editor.create_model(old_fact)
        old_pref.objects.create(user_id=owner.pk, enabled=True, consent_version=1)
        before = old_fact.objects.create(user_id=owner.pk, text='I prefer concise explanations', status='approved')
        for name in ['0029_persistent_memory_phase1', '0030_memory_extraction_cutoffs']:
            migration = loader.get_migration('pwanimate', name)
            with connection.schema_editor() as editor:
                state = migration.apply(state, editor)
        after = SparkPersonalMemory.objects.get(pk=before.pk)
        self.assertEqual(after.text, before.text)
        self.assertEqual(after.status, 'approved')
        self.assertEqual(after.category, 'semantic')
        pref = SparkMemoryPreference.objects.get(user=owner)
        self.assertFalse(pref.automatic_extraction)
        self.assertIsNone(pref.automatic_enabled_at)
        self.assertEqual(pref.extraction_cutoffs, {})
        self.assertEqual(SparkMemoryExtraction.objects.count(), 0)
