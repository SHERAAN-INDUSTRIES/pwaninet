"""Compile actual completion lock queries with PostgreSQL, without connecting.

SQLite exercises behavior but cannot reject outer-join lock targets. Capturing
and compiling the querysets used by the services prevents that blind spot.
"""
from unittest.mock import patch
from django.test import TestCase
from django.db.models.query import QuerySet
from django.db.backends.postgresql.base import DatabaseWrapper
from pwanimate.models import SparkTeachingTurn
from pwanimate.services.conversation import ConversationService as Conversation
from pwanimate.services.teaching import TeachingService as Tutor
from pwanimate.orchestrator.types import OrchestrationRequest
from tests.sals_academic_continuity.test_continuity import ContinuityTests, LINE

class CompletionLockSQLTests(TestCase):
    setUp = ContinuityTests.setUp

    def test_actual_nullable_completion_queries_lock_only_turn(self):
        source=Conversation.persist_user_message(self.conv,LINE)
        prepared=Tutor.prepare(OrchestrationRequest(user=self.user,query=LINE,user_message_id=source.pk))
        turn=SparkTeachingTurn.objects.get(pk=prepared['teaching_turn_id'])
        self.assertIsNone(turn.completed_message_id)
        queries=[]
        lock_order=[]
        original=QuerySet.get
        original_lock=QuerySet.select_for_update
        def capture_lock(queryset,*args,**kwargs):
            lock_order.append(queryset.model.__name__)
            return original_lock(queryset,*args,**kwargs)
        def capture(queryset,*args,**kwargs):
            if queryset.model is SparkTeachingTurn and queryset.query.select_for_update:
                queries.append(queryset.query.clone())
            return original(queryset,*args,**kwargs)
        with patch.object(QuerySet,'get',new=capture), patch.object(QuerySet,'select_for_update',new=capture_lock):
            message=Conversation.persist_assistant_message(self.conv,'Completed explanation',
                teaching_turn_id=turn.pk,teaching_complete=True)
            replay=Conversation.persist_assistant_message(self.conv,'Must not duplicate',
                teaching_turn_id=turn.pk,teaching_complete=True)
        self.assertEqual(message.pk,replay.pk)
        self.assertLess(lock_order.index('SparkAcademicTrackingPreference'),lock_order.index('PwanimateStudySession'))
        self.assertLess(lock_order.index('PwanimateStudySession'),lock_order.index('SparkTeachingTurn'))
        self.assertGreaterEqual(len(queries),3)
        postgres=DatabaseWrapper({'ENGINE':'django.db.backends.postgresql','NAME':'offline_compiler'},alias='offline_compiler')
        # Compiling only: no cursor/connection to PostgreSQL is requested.
        with patch.object(postgres,'get_autocommit',return_value=False), patch.object(postgres,'ensure_connection',side_effect=AssertionError('No PostgreSQL access permitted')):
            for query in queries:
                self.assertEqual(query.select_for_update_of,('self',))
                sql,_=query.get_compiler(connection=postgres).as_sql()
                self.assertIn('LEFT OUTER JOIN',sql)
                self.assertEqual(sql.split(' FOR UPDATE ')[1],'OF "pwanimate_sparkteachingturn"')

from unittest import skipUnless
from django.test import TransactionTestCase
from django.db import connection, close_old_connections
from pwanimate.models import PwanimateMessage
from pwanimate.services.unit_learning import UnitLearningService as Units
from pwanimate.services.academic_progress import finalize
from pwanimate.services.concept_mastery import ConceptMasteryService
import threading

@skipUnless(connection.vendor=='postgresql','Owner-run PostgreSQL write/consent races')
class CompletionWriteRaceTests(TransactionTestCase):
    setUp=ContinuityTests.setUp

    def prepare(self):
        source=Conversation.persist_user_message(self.conv,LINE)
        return Tutor.prepare(OrchestrationRequest(user=self.user,query=LINE,user_message_id=source.pk))['teaching_turn_id']

    def test_concurrent_persistence_returns_one_message(self):
        turn_id=self.prepare()
        barrier=threading.Barrier(2)
        results,errors=[],[]
        def work():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                results.append(Conversation.persist_assistant_message(self.conv,'Concurrent completion',
                    teaching_turn_id=turn_id,teaching_complete=True).pk)
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()
        with patch.object(Conversation,'schedule_embedding'), patch.object(Conversation,'schedule_digest'), patch('pwanimate.services.memory_dispatch.wake_dispatch'):
            workers=[threading.Thread(target=work) for _ in range(2)]
            for worker in workers: worker.start()
            for worker in workers: worker.join(15)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertFalse(errors)
        self.assertEqual(len(results),2)
        self.assertEqual(len(set(results)),1)
        self.assertEqual(self.conv.messages.filter(content='Concurrent completion').count(),1)
        self.assertEqual(SparkTeachingTurn.objects.get(pk=turn_id).completed_message_id,results[0])

    def test_revocation_wins_before_completion_write(self):
        turn_id=self.prepare()
        message=PwanimateMessage.objects.create(conversation=self.conv,role='assistant',content='Untracked after revocation')
        started,release=threading.Event(),threading.Event()
        errors=[]
        original=ConceptMasteryService.lock_record
        def before_lock(*args,**kwargs):
            started.set()
            if not release.wait(10):
                raise RuntimeError('test timeout')
            return original(*args,**kwargs)
        def work():
            close_old_connections()
            try:
                finalize(turn_id,message,complete=True)
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()
        with patch.object(ConceptMasteryService,'lock_record',side_effect=before_lock):
            worker=threading.Thread(target=work)
            worker.start()
            try:
                self.assertTrue(started.wait(10))
                Units.set_mode(self.user,'disabled')
            finally:
                release.set()
                worker.join(15)
        self.assertFalse(worker.is_alive())
        self.assertFalse(errors)
        self.assertIsNone(SparkTeachingTurn.objects.get(pk=turn_id).completed_at)
