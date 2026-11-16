import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from django.db import connection, connections
from django.db.migrations.loader import MigrationLoader
from django.test import TransactionTestCase, override_settings
from pwanimate.models import (PwanimateConversation, PwanimateMessage, PendingAction, PwanimateStudySession, SparkUnitActivity,
    SparkUnitLearningRecord, SparkAcademicTrackingPreference)
from pwanimate.services.unit_learning import UnitLearningService as Units
from tests.sals_02.test_foundation import setup_student


class UnitPersistenceTests(TransactionTestCase):
    def test_fresh_process_reads_same_unit_term_record(self):
        user, _, _, enrollment, _ = setup_student()
        record = Units.get_record(user, enrollment)
        from pwanimate.services.actions import ActionService
        conv = PwanimateConversation.objects.create(user=user)
        trigger = PwanimateMessage.objects.create(conversation=conv, role='user', content='Explain matrices')
        Units.process_message(trigger.pk)
        ActionService.confirm(user, PendingAction.objects.get().pk)
        future = PwanimateMessage.objects.create(conversation=conv, role='user', content='Explain matrices')
        Units.process_message(future.pk)
        if connection.vendor != 'sqlite':
            connections.close_all()
            self.assertEqual(Units.context_for_query(user, 'What have I studied in Linear Algebra?')[0]['id'], str(record.pk))
            return
        with tempfile.TemporaryDirectory(prefix='sals02-restart-') as scratch:
            import sqlite3
            db = str(Path(scratch) / 'restart.sqlite3')
            with sqlite3.connect(db) as target:
                connection.connection.backup(target)
            code = '''import django, json; django.setup()
from django.contrib.auth import get_user_model
from pwanimate.services.unit_learning import UnitLearningService
u=get_user_model().objects.get(username="unit-owner")
print(json.dumps(UnitLearningService.context_for_query(u,"What have I studied in Linear Algebra?"),default=str))'''
            output = subprocess.run([sys.executable, '-c', code], check=True, capture_output=True, text=True,
                env={**os.environ, 'DJANGO_SETTINGS_MODULE':'tests.spark_memory.settings', 'SPARK_MEMORY_TEST_DB':db})
            summary = json.loads(output.stdout.strip().splitlines()[-1])
            self.assertEqual(summary[0]['id'], str(record.pk))
            self.assertEqual(summary[0]['semester_id'], record.semester_id)
            self.assertEqual(summary[0]['topics'], ['matrices'])
            self.assertEqual(len(summary[0]['sessions']), 1)

    def test_actual_0031_upgrade_preserves_old_sessions_without_enrollment_invention(self):
        user, _, _, _, _ = setup_student()
        conversation = PwanimateConversation.objects.create(user=user)
        session = PwanimateStudySession.objects.create(user=user, conversation=conversation)
        with override_settings(MIGRATION_MODULES={}):
            loader = MigrationLoader(None)
        state = loader.project_state([('pwanimate', '0030_memory_extraction_cutoffs')])
        with connection.schema_editor() as editor:
            # Descendant tables must be rebuilt too: PostgreSQL DROP CASCADE
            # would otherwise remove their foreign keys during the old harness.
            from tests.sals_03_wp1.test_persistence import drop_mastery_schema
            drop_mastery_schema(editor)
            editor.delete_model(SparkUnitActivity)
            editor.remove_field(PwanimateStudySession, PwanimateStudySession._meta.get_field('learning_record'))
            editor.delete_model(SparkUnitLearningRecord)
            editor.delete_model(SparkAcademicTrackingPreference)
        with connection.schema_editor() as editor:
            state = loader.get_migration('pwanimate', '0031_unit_learning_foundation').apply(state, editor)
        with connection.schema_editor() as editor:
            state = loader.get_migration('pwanimate', '0032_concept_mastery_foundation').apply(state, editor)
        with connection.schema_editor() as editor:
            loader.get_migration('pwanimate', '0033_adaptive_teaching').apply(state, editor)
        state = loader.project_state([('pwanimate','0036_memory_dispatch_outbox')])
        with connection.schema_editor() as editor:
            migration = loader.get_migration('pwanimate','0037_academic_completion')
            # The legacy test rebuilds teaching tables only; session binding already exists.
            migration.operations = [op for op in migration.operations if op.name != 'academic_conversation']
            migration.apply(state,editor)
        session.refresh_from_db()
        self.assertIsNone(session.learning_record_id)
        self.assertEqual(SparkUnitLearningRecord.objects.count(), 0)
        self.assertEqual(Units.preference(user).mode, 'ask')
