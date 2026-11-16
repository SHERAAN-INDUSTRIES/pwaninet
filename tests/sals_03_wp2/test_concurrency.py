"""Owner-run PostgreSQL cases. SQLite explicitly skips real row-lock races."""
from unittest import skipUnless
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, local
from unittest.mock import patch
from django.db import connection, connections
from django.test import TransactionTestCase
from pwanimate.models import SparkTeachingState, SparkTeachingTurn, SparkAgentRun, SparkAgentInteraction, PwanimateMessage
from pwanimate.services.teaching import TeachingService as Tutor
from pwanimate.services.agent_execution import claim_run
from pwanimate.services.agent_runs import RunError
from pwanimate.orchestrator.types import OrchestrationRequest
from tests.sals_03_wp1.test_mastery import setup_mastery
from tests.sals_03_wp2.test_teaching import LINE


@skipUnless(connection.vendor=='postgresql','PostgreSQL row-lock verification is owner-run')
class PostgreSQLTeachingConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user,_,_,_,_,record,_,_=setup_mastery()
        self.session=record.sessions.get(); self.session.learning_objective=''; self.session.save()
        self.source=PwanimateMessage.objects.create(conversation=self.session.conversation,role='user',content=LINE)

    def request(self):
        return OrchestrationRequest(query=LINE,user=self.user,user_message_id=self.source.pk,study_mode=True)

    def race(self,operation):
        barrier=Barrier(2)
        def work():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                return operation()
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(work) for _ in range(2)]
            return [future.result(timeout=30) for future in futures]

    def test_initial_retry_creates_one_state_turn_run_and_question(self):
        results=self.race(lambda:Tutor.prepare(self.request()))
        self.assertEqual(results[0]['teaching_turn_id'],results[1]['teaching_turn_id'])
        for model in [SparkTeachingState,SparkTeachingTurn,SparkAgentRun,SparkAgentInteraction]:
            self.assertEqual(model.objects.count(),1)

    def test_duplicate_answer_claim_has_one_winner(self):
        result=Tutor.prepare(self.request()); run=SparkAgentRun.objects.get(pk=result['run_id'])
        interaction=SparkAgentInteraction.objects.get(run=run,status='pending')
        def answer():
            try:
                claim_run(self.user,run,interaction=interaction,raw_answer={'selected':['continue']})
                return 'accepted'
            except RunError: return 'duplicate'
        self.assertEqual(sorted(self.race(answer)),['accepted','duplicate'])
        self.assertEqual(SparkAgentInteraction.objects.filter(status='answered').count(),1)

    def test_simultaneous_mode_changes_keep_versions_and_history(self):
        state=Tutor.configure(self.session,self.user,mode='GUIDED')
        self.race(lambda:Tutor.configure(self.session,self.user,mode='DEEP'))
        state.refresh_from_db(); self.assertEqual(state.version,4)
        self.assertEqual(state.mode,'DEEP'); self.assertEqual(state.turns.count(),0)

    def test_hint_and_external_grading_serialize_assistance_at_submission(self):
        self.grading_during_prepare('hint')

    def test_answer_preparation_and_external_submission_share_frozen_evidence(self):
        self.grading_during_prepare('answer')

    def grading_during_prepare(self,operation):
        from pwanimate.services.concept_mastery import ConceptMasteryService as Mastery
        from pwanimate.services.assessment import AssessmentService
        from pwanimate.models import SparkConceptEvidence
        from pwanimate.assessment_validation import REFERENCES
        concept=self.record.concepts.get() if hasattr(self,'record') else self.session.learning_record.concepts.get()
        Tutor.configure(self.session,self.user,mode='PRACTICE',concept_id=concept.pk)
        request=self.request(); request.query='Explain determinants'
        result=Tutor.prepare(request)
        state=SparkTeachingState.objects.get(session=self.session); spec=state.question
        run=SparkAgentRun.objects.get(pk=result['run_id'])
        interaction=SparkAgentInteraction.objects.get(run=run,status='pending')
        expected=REFERENCES[spec.reference_id]['expected']
        claimed,_=claim_run(self.user,run,interaction=interaction,raw_answer={'text':'hint' if operation=='hint' else expected})
        # Force grading to own consent first, then let hint preparation reach
        # that same lock. Old WP4 held the session here, so grading's deferred
        # evidence FK check and hint's consent wait formed a real deadlock.
        grading_locked, hint_waiting = Event(), Event()
        role=local()
        original=Mastery.lock_record
        def ordered_lock(*args,**kwargs):
            if role.name == 'grading':
                record=original(*args,**kwargs)
                grading_locked.set()
                if not hint_waiting.wait(10): raise RuntimeError('hint did not reach consent lock')
                return record
            hint_waiting.set()
            return original(*args,**kwargs)
        def work(name,operation):
            connections.close_all()
            role.name=name
            try:
                with connections['default'].cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                if name == 'hint' and not grading_locked.wait(10):
                    raise RuntimeError('grading did not acquire consent lock')
                return operation()
            finally: connections.close_all()
        expected=REFERENCES[spec.reference_id]['expected']
        with patch.object(Mastery,'lock_record',side_effect=ordered_lock):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures=[pool.submit(work,'hint',lambda:Tutor.prepare(request,run=claimed)),
                    pool.submit(work,'grading',lambda:AssessmentService.submit_answer(spec.assessment,spec.qid,expected))]
                for future in futures: future.result(timeout=30)
        evidence=SparkConceptEvidence.objects.get(question=spec)
        if operation=='hint':
            hint=SparkTeachingTurn.objects.get(question=spec,action='hint')
            self.assertEqual(evidence.assistance,'hint' if hint.created_at<=evidence.occurred_at else 'none')
        self.assertEqual(SparkConceptEvidence.objects.filter(question=spec).count(),1)

        # A retry after the later hint cannot rewrite the frozen first attempt.
        replay=AssessmentService.submit_answer(spec.assessment,spec.qid,expected)
        self.assertEqual(replay['evidence_id'],str(evidence.pk))
        evidence.refresh_from_db()
        self.assertEqual(evidence.assistance,'none')
        self.assertTrue(replay['already_graded'])
