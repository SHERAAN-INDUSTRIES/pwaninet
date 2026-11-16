import os
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from django.db import connection, connections
from django.db.migrations.loader import MigrationLoader
from django.test import TransactionTestCase, override_settings
from pwanimate.models import SparkTeachingState, SparkTeachingTurn, PwanimateMessage
from pwanimate.services.teaching import TeachingService as Tutor
from pwanimate.orchestrator.types import OrchestrationRequest
from tests.sals_03_wp1.test_mastery import setup_mastery
from tests.sals_03_wp2.test_teaching import LINE


class TeachingPersistenceTests(TransactionTestCase):
    def setUp(self):
        self.user, _, _, _, _, self.record, _, _ = setup_mastery()
        self.session = self.record.sessions.get()
        self.session.learning_objective = ''
        self.session.save()
        self.source = PwanimateMessage.objects.create(conversation=self.session.conversation,role='user',content=LINE)
        self.request = OrchestrationRequest(query=LINE,user=self.user,user_message_id=self.source.pk,study_mode=True)

    def test_actual_0033_operations_preserve_existing_unit_sessions(self):
        with override_settings(MIGRATION_MODULES={}): loader=MigrationLoader(None)
        state=loader.project_state([('pwanimate','0032_concept_mastery_foundation')])
        with connection.schema_editor() as editor:
            editor.delete_model(SparkTeachingTurn); editor.delete_model(SparkTeachingState)
        with connection.schema_editor() as editor:
            loader.get_migration('pwanimate','0033_adaptive_teaching').apply(state,editor)
        # Current runtime also needs the additive completion migration after
        # deliberately recreating its historical 0033 tables.
        state = loader.project_state([('pwanimate','0036_memory_dispatch_outbox')])
        with connection.schema_editor() as editor:
            migration = loader.get_migration('pwanimate','0037_academic_completion')
            # The legacy test rebuilds teaching tables only; session binding already exists.
            migration.operations = [op for op in migration.operations if op.name != 'academic_conversation']
            migration.apply(state,editor)
        self.assertEqual(self.record.sessions.get().pk,self.session.pk)
        self.assertEqual(SparkTeachingState.objects.count(),0)
        self.assertTrue(Tutor.prepare(self.request)['teaching_turn_id'])

    def test_restart_reads_state_interactions_and_teaching_history(self):
        result=Tutor.prepare(self.request)
        if connection.vendor != 'sqlite':
            connections.close_all()
            state=SparkTeachingState.objects.get(session=self.session)
            self.assertEqual(str(state.active_run_id),result['run_id'])
            self.assertEqual(state.turns.count(),1)
            return
        import sqlite3
        with tempfile.TemporaryDirectory(prefix='sals-wp2-restart-') as scratch:
            db=str(Path(scratch)/'restart.sqlite3')
            with sqlite3.connect(db) as target: connection.connection.backup(target)
            code='''import django,json;django.setup()
from pwanimate.models import SparkTeachingState
s=SparkTeachingState.objects.get()
print(json.dumps({'phase':s.phase,'turns':s.turns.count(),'run':str(s.active_run_id),'questions':s.active_run.interactions.filter(status='pending').count()}))'''
            output=subprocess.run([sys.executable,'-c',code],check=True,capture_output=True,text=True,
                env={**os.environ,'DJANGO_SETTINGS_MODULE':'tests.spark_memory.settings','SPARK_MEMORY_TEST_DB':db})
            data=json.loads(output.stdout.strip().splitlines()[-1])
            self.assertEqual(data,{'phase':'EXPLAIN','turns':1,'run':result['run_id'],'questions':1})
