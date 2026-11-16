"""R2 isolated DB and mocked-broker failure tests; no live infrastructure."""
from datetime import timedelta
from time import perf_counter
from unittest.mock import patch
from django.test import TestCase
from django.db import transaction, IntegrityError
from django.utils import timezone
from django.contrib.auth import get_user_model
from pwanimate.models import (PwanimateConversation, PwanimateMessage, SparkMemoryDispatch,
    SparkMemoryExtraction, SparkPersonalMemory)
from pwanimate.services.personal_memory import PersonalMemoryService as Personal
from pwanimate.services.persistent_memory import PersistentMemoryService as Memory
from pwanimate.services import memory_dispatch as delivery

class DispatchTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='dispatch-owner')
        self.other = get_user_model().objects.create_user(username='dispatch-other')
        self.conv = PwanimateConversation.objects.create(user=self.user)
        Personal.set_enabled(self.user, True)
        self.pref = Personal.set_automatic(self.user, True)
        self.source = PwanimateMessage.objects.create(conversation=self.conv,role='user',
            content="Spark, I'm studying calculus. I understand substitution, but I struggle with integration by parts.")
        self.answer = PwanimateMessage.objects.create(conversation=self.conv,role='assistant',content='Practice')
        self.job = delivery.schedule(self.answer, wake=False)

    def claim(self):
        with patch('pwanimate.tasks.conversation_memory.process_memory_dispatch.apply_async'):
            delivery.dispatch(self.job.pk)
        self.job.refresh_from_db()
        return self.job.claim_token

    def due(self):
        SparkMemoryDispatch.objects.filter(pk=self.job.pk).update(next_attempt_at=timezone.now()-timedelta(seconds=1))

    def test_duplicate_schedule_and_constraint(self):
        self.assertEqual(delivery.schedule(self.answer, wake=False).pk,self.job.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SparkMemoryDispatch.objects.create(user=self.user, source_message_id=self.answer.pk,
                source_conversation_id=str(self.conv.pk),consent_revision=self.pref.revision)

    def test_transaction_rollback_removes_intent(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                answer = PwanimateMessage.objects.create(conversation=self.conv,role='assistant',content='Another')
                delivery.schedule(answer,wake=False)
                raise RuntimeError()
        self.assertEqual(SparkMemoryDispatch.objects.count(),1)
        self.assertFalse(PwanimateMessage.objects.filter(content='Another').exists())

    def test_broker_failure_backoff_and_dead_letter(self):
        for failure in [ConnectionError, TimeoutError]:
            with patch('pwanimate.tasks.conversation_memory.process_memory_dispatch.apply_async',side_effect=failure):
                self.due()
                self.assertEqual(delivery.dispatch(self.job.pk),'retry')
            self.job.refresh_from_db()
            self.assertEqual(self.job.status,'pending')
            self.assertGreater(self.job.next_attempt_at,timezone.now())
        SparkMemoryDispatch.objects.filter(pk=self.job.pk).update(attempts=delivery.MAX_ATTEMPTS-1)
        self.due()
        with patch('pwanimate.tasks.conversation_memory.process_memory_dispatch.apply_async',side_effect=TimeoutError):
            delivery.dispatch(self.job.pk)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status,'failed')
        self.assertEqual(delivery.recover(),{})

    def test_ambiguous_publish_ack_after_completion(self):
        def publish(*,args,**kwargs):
            delivery.process(*args)
            raise TimeoutError()
        with patch('pwanimate.tasks.conversation_memory.process_memory_dispatch.apply_async',side_effect=publish):
            delivery.dispatch(self.job.pk)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status,'completed')
        self.assertGreater(SparkPersonalMemory.objects.count(),0)

    def test_duplicate_delivery_no_duplicate_facts_or_receipts(self):
        token=self.claim()
        self.assertEqual(delivery.process(self.job.pk,token)['status'],'completed')
        facts,receipts=SparkPersonalMemory.objects.count(),SparkMemoryExtraction.objects.count()
        self.assertEqual(delivery.process(self.job.pk,token)['status'],'duplicate_or_stale')
        self.assertEqual(SparkPersonalMemory.objects.count(),facts)
        self.assertEqual(SparkMemoryExtraction.objects.count(),receipts)

    def test_worker_crash_and_restart_fences_old_claim(self):
        old=self.claim()
        SparkMemoryDispatch.objects.filter(pk=self.job.pk).update(lease_until=timezone.now()-timedelta(seconds=1))
        self.assertEqual(delivery.recover(),{'pending':1})
        self.due()
        new=self.claim()
        self.assertNotEqual(new,old)
        self.assertEqual(delivery.process(self.job.pk,old)['status'],'duplicate_or_stale')
        self.assertEqual(delivery.process(self.job.pk,new)['status'],'completed')

    def test_processing_failure_rolls_back_facts_and_retry(self):
        token=self.claim()
        original=Memory.extract
        def fail(*args):
            original(*args)
            raise TimeoutError('simulated extraction failure')
        with patch.object(Memory,'extract',side_effect=fail):
            self.assertEqual(delivery.process(self.job.pk,token)['status'],'retry')
        self.assertFalse(SparkPersonalMemory.objects.exists())
        self.assertFalse(SparkMemoryExtraction.objects.exists())
        self.due()
        token=self.claim()
        self.assertEqual(delivery.process(self.job.pk,token)['status'],'completed')

    def test_disabled_before_dispatch_and_revision_changed(self):
        Personal.set_enabled(self.user,False)
        self.assertEqual(delivery.dispatch(self.job.pk),'cancelled')
        Personal.set_enabled(self.user,True)
        Personal.set_automatic(self.user,True)
        self.assertEqual(delivery.dispatch(self.job.pk),'terminal')
        self.assertIsNone(delivery.schedule(self.answer,wake=False))

    def test_revoked_between_dispatch_and_processing(self):
        token=self.claim()
        Personal.set_automatic(self.user,False)
        self.assertEqual(delivery.process(self.job.pk,token)['status'],'cancelled')
        self.assertFalse(SparkPersonalMemory.objects.exists())

    def test_deleted_message_and_wrong_owner(self):
        SparkMemoryDispatch.objects.filter(pk=self.job.pk).update(user=self.other)
        self.assertEqual(delivery.dispatch(self.job.pk),'cancelled')
        SparkMemoryDispatch.objects.filter(pk=self.job.pk).update(user=self.user,status='pending')
        self.answer.delete()
        self.assertEqual(delivery.dispatch(self.job.pk),'cancelled')

    def test_wrong_conversation_and_reset(self):
        conv=PwanimateConversation.objects.create(user=self.user)
        SparkMemoryDispatch.objects.filter(pk=self.job.pk).update(conversation=conv)
        self.assertEqual(delivery.dispatch(self.job.pk),'cancelled')

    def test_deleted_fact_cutoff_preserved(self):
        Memory.extract(self.answer.pk,self.pref.revision)
        fact=SparkPersonalMemory.objects.first()
        Personal.delete(self.user,fact.pk)
        expected=SparkPersonalMemory.objects.count()
        token=self.claim()
        delivery.process(self.job.pk,token)
        self.assertEqual(SparkPersonalMemory.objects.count(),expected)

    def test_local_scheduling_measurement(self):
        samples=[]
        for _ in range(20):
            start=perf_counter()
            delivery.schedule(self.answer,wake=False)
            samples.append((perf_counter()-start)*1000)
        print('R2_LOCAL_EXISTING_SCHEDULE_MS',{'samples':len(samples),'mean_ms':round(sum(samples)/len(samples),3),'max_ms':round(max(samples),3)})

    def test_reset_and_rejection_still_enforced(self):
        from pwanimate.models import SparkMemoryPreference
        SparkMemoryPreference.objects.filter(pk=self.pref.pk).update(reset_at=timezone.now())
        self.assertEqual(delivery.dispatch(self.job.pk),'cancelled')

    def test_claimed_processing_crash_recovers(self):
        token=self.claim()
        SparkMemoryDispatch.objects.filter(pk=self.job.pk).update(status='processing',lease_until=timezone.now()-timedelta(seconds=1))
        delivery.recover()
        self.due()
        new=self.claim()
        self.assertEqual(delivery.process(self.job.pk,token)['status'],'duplicate_or_stale')
        self.assertEqual(delivery.process(self.job.pk,new)['status'],'completed')

    def test_rejected_topic_is_not_resurrected(self):
        SparkPersonalMemory.objects.create(user=self.user,text='Rejected calculus',memory_key='topic:calculus',
            status='rejected',purpose='academic_learning')
        # Existing extraction quality rules remain authoritative.
        from pwanimate.services.persistent_memory import candidates
        keys=[c.key for c in candidates(self.source.content)]
        for key in keys:
            SparkPersonalMemory.objects.create(user=self.user,text='Rejected',memory_key=key,status='rejected')
        token=self.claim()
        delivery.process(self.job.pk,token)
        self.assertFalse(SparkPersonalMemory.objects.filter(automatic=True).exists())

    def test_scheduling_failure_rolls_back_real_message_write(self):
        from pwanimate.services.conversation import ConversationService
        with patch('pwanimate.services.memory_dispatch.schedule',side_effect=RuntimeError('DB unavailable')):
            with self.assertRaises(RuntimeError):
                ConversationService.persist_assistant_message(self.conv,'Must rollback')
        self.assertFalse(PwanimateMessage.objects.filter(content='Must rollback').exists())

    def test_new_scheduling_measurement(self):
        samples=[]
        for _ in range(20):
            answer=PwanimateMessage.objects.create(conversation=self.conv,role='assistant',content='Answer')
            start=perf_counter()
            delivery.schedule(answer,wake=False)
            samples.append((perf_counter()-start)*1000)
        print('R2_LOCAL_NEW_SCHEDULE_MS',{'samples':len(samples),'mean_ms':round(sum(samples)/len(samples),3),'max_ms':round(max(samples),3)})

from django.test import TransactionTestCase, override_settings
from django.db import connection, close_old_connections
from django.db.migrations.loader import MigrationLoader
from unittest import skipUnless
import threading

class DispatchMigrationTests(TransactionTestCase):
    def test_real_additive_migration_preserves_facts(self):
        user=get_user_model().objects.create_user(username='outbox-migration')
        fact=SparkPersonalMemory.objects.create(user=user,text='Preserved approved memory',status='approved')
        with override_settings(MIGRATION_MODULES={}):
            loader=MigrationLoader(None)
        state=loader.project_state([('pwanimate','0035_followup_execution')])
        migration=loader.get_migration('pwanimate','0036_memory_dispatch_outbox')
        with connection.schema_editor() as editor:
            editor.delete_model(SparkMemoryDispatch)
            migration.apply(state,editor)
        self.assertEqual(SparkPersonalMemory.objects.get(pk=fact.pk).text,'Preserved approved memory')
        self.assertFalse(SparkMemoryDispatch.objects.exists())

@skipUnless(connection.vendor == 'postgresql','Owner-run PostgreSQL transaction races')
class DispatchConcurrencyTests(TransactionTestCase):
    setUp=DispatchTests.setUp

    def test_concurrent_dispatch_claim_and_duplicate_delivery(self):
        started,release=threading.Event(),threading.Event()
        errors=[]
        def publish(**kwargs):
            started.set()
            if not release.wait(10):
                raise RuntimeError('test timeout')
        def run():
            close_old_connections()
            try:
                delivery.dispatch(self.job.pk)
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()
        with patch('pwanimate.tasks.conversation_memory.process_memory_dispatch.apply_async',side_effect=publish) as task:
            worker=threading.Thread(target=run)
            worker.start()
            try:
                self.assertTrue(started.wait(10))
                self.assertEqual(delivery.dispatch(self.job.pk),'busy')
            finally:
                release.set()
                worker.join(10)
            self.assertFalse(errors)
            self.assertEqual(task.call_count,1)
        self.job.refresh_from_db()
        self.assertEqual(self.job.attempts,1)
        self.assertEqual(delivery.process(self.job.pk,self.job.claim_token)['status'],'completed')
        self.assertEqual(delivery.process(self.job.pk,self.job.claim_token)['status'],'duplicate_or_stale')

    def test_revocation_serializes_with_fact_commit(self):
        started,release=threading.Event(),threading.Event()
        errors=[]
        token=DispatchTests.claim(self)
        original=Memory.extract
        def held(*args):
            started.set()
            if not release.wait(10):
                raise RuntimeError('test timeout')
            return original(*args)
        def process():
            close_old_connections()
            try:
                delivery.process(self.job.pk,token)
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()
        def revoke():
            close_old_connections()
            try:
                Personal.set_enabled(self.user,False)
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()
        with patch.object(Memory,'extract',side_effect=held):
            worker=threading.Thread(target=process)
            worker.start()
            self.assertTrue(started.wait(10))
            revoker=threading.Thread(target=revoke)
            revoker.start()
            release.set()
            worker.join(10)
            revoker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertFalse(revoker.is_alive())
        self.assertFalse(errors)
        self.pref.refresh_from_db()
        self.assertFalse(self.pref.enabled)
        self.assertEqual(Memory.retrieve(self.user,'calculus'),[])

class DispatchOperationsTests(DispatchTests):
    def test_operator_requeue_and_inspection(self):
        from django.core.management import call_command
        from io import StringIO
        SparkMemoryDispatch.objects.filter(pk=self.job.pk).update(status='failed',attempts=8,last_error='TimeoutError')
        output=StringIO()
        call_command('spark_memory_dispatch',stdout=output)
        self.assertIn('failed',output.getvalue())
        call_command('spark_memory_dispatch',retry_failed=str(self.job.pk),stdout=StringIO())
        self.job.refresh_from_db()
        self.assertEqual(self.job.status,'pending')
        self.assertEqual(self.job.attempts,0)

    def test_requeue_cannot_revive_stale_consent(self):
        from django.core.management import call_command, CommandError
        SparkMemoryDispatch.objects.filter(pk=self.job.pk).update(status='failed')
        Personal.set_enabled(self.user,False)
        with self.assertRaises(CommandError):
            call_command('spark_memory_dispatch',retry_failed=str(self.job.pk))
