import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from django.db import connection, connections
from django.db.migrations.loader import MigrationLoader
from django.test import TransactionTestCase, override_settings
from pwanimate.models import (SparkUnitLearningRecord, SparkAssessment, SparkUnitConcept,
    SparkConceptQuestion, SparkConceptEvidence, SparkConceptTransition, SparkConceptPrerequisite, SparkTeachingTurn, SparkTeachingState)
from pwanimate.services.concept_mastery import ConceptMasteryService as Mastery
from pwanimate.services.assessment import AssessmentService
from pwanimate.assessment_validation import REFERENCES
from tests.sals_03_wp1.test_mastery import setup_mastery, approve_record
from tests.sals_02.test_foundation import setup_student


def drop_mastery_schema(editor):
    """Disposable migration harness only, in dependency order."""
    for model in [SparkTeachingTurn, SparkTeachingState, SparkConceptEvidence, SparkConceptQuestion, SparkConceptTransition, SparkConceptPrerequisite, SparkUnitConcept]:
        editor.delete_model(model)


class MasteryPersistenceTests(TransactionTestCase):
    def test_fresh_interpreter_reads_evidence_state_and_transition_versions(self):
        user, _, _, _, _, record, _, concept = setup_mastery()
        assessment = Mastery.deliver_reference_assessment(user, concept.pk, record.sessions.get().pk, count=1)
        AssessmentService.submit_answer(assessment, 'q1', REFERENCES[assessment.questions[0]['reference_id']]['expected'])
        concept.refresh_from_db()
        if connection.vendor != 'sqlite':
            connections.close_all()
            self.assertEqual(Mastery.owned_concept(user, concept.pk).state, 'PRACTICING')
            return
        with tempfile.TemporaryDirectory(prefix='sals03-restart-') as scratch:
            import sqlite3
            db = str(Path(scratch) / 'restart.sqlite3')
            with sqlite3.connect(db) as target:
                connection.connection.backup(target)
            code = '''import django,json;django.setup()
from pwanimate.models import SparkUnitConcept
c=SparkUnitConcept.objects.get(key="determinants")
print(json.dumps({"id":str(c.pk),"state":c.state,"version":c.version,"evidence":c.evidence.count(),"transitions":c.transitions.count()}))'''
            output = subprocess.run([sys.executable, '-c', code], check=True, capture_output=True, text=True,
                env={**os.environ, 'DJANGO_SETTINGS_MODULE':'tests.spark_memory.settings', 'SPARK_MEMORY_TEST_DB':db})
            data = json.loads(output.stdout.strip().splitlines()[-1])
            self.assertEqual(data, {'id':str(concept.pk), 'state':'PRACTICING', 'version':concept.version,
                'evidence':1, 'transitions':concept.transitions.count()})

    def test_actual_0032_operations_preserve_unit_history_and_do_not_certify_legacy_grades(self):
        user, _, _, enrollment, _ = setup_student()
        record, _ = approve_record(user, enrollment)
        assessment = SparkAssessment.objects.create(user=user, session=record.sessions.get(),
            counts_toward_mastery=True, questions=[{'qid':'legacy', 'correct':True, 'student_answer':'24'}])
        with override_settings(MIGRATION_MODULES={}):
            loader = MigrationLoader(None)
        state = loader.project_state([('pwanimate', '0031_unit_learning_foundation')])
        with connection.schema_editor() as editor:
            drop_mastery_schema(editor)
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
        self.assertTrue(SparkUnitLearningRecord.objects.filter(pk=record.pk).exists())
        self.assertEqual(SparkAssessment.objects.get(pk=assessment.pk).questions[0]['student_answer'], '24')
        self.assertEqual(SparkUnitConcept.objects.count(), 0)
        self.assertEqual(SparkConceptEvidence.objects.count(), 0)
