import time
from unittest.mock import Mock, patch
from django.test import TestCase
from rest_framework.test import APIClient
from pwanimate.models import SparkTeachingState, SparkTeachingTurn, SparkAgentRun, SparkAgentInteraction, PwanimateMessage, SparkConceptEvidence
from pwanimate.orchestrator.types import OrchestrationRequest, OrchestrationResponse
from pwanimate.services.teaching import TeachingService as Tutor
from pwanimate.services.agent_execution import claim_run
from pwanimate.services.concept_mastery import ConceptMasteryService as Mastery
from pwanimate.services.agent_runs import RunError
from pwanimate.assessment_validation import REFERENCES
from tests.sals_03_wp1.test_mastery import setup_mastery

LINE = 'Explain symmetric equations of lines in 3D through P(1,2,3) with direction vector (2,-1,4)'

class TeachingTests(TestCase):
    def setUp(self):
        self.user, self.other, _, _, self.second, self.record, self.conv, self.concept = setup_mastery()
        self.session = self.record.sessions.get()
        self.conv = self.session.conversation
        self.session.learning_objective = ''
        self.session.save()
        self.source = PwanimateMessage.objects.create(conversation=self.conv, role='user', content=LINE)
        self.request = OrchestrationRequest(query=LINE, user=self.user, user_message_id=self.source.pk, study_mode=True)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_authorization_lock_precedes_session_and_child_locks(self):
        from django.db.models.query import QuerySet
        from pwanimate.models import SparkAcademicTrackingPreference, PwanimateStudySession
        calls=[]
        original=QuerySet.select_for_update
        def track(queryset,*args,**kwargs):
            calls.append(queryset.model)
            return original(queryset,*args,**kwargs)
        with patch.object(QuerySet,'select_for_update',new=track):
            Tutor.prepare(self.request)
        self.assertLess(calls.index(SparkAcademicTrackingPreference),calls.index(PwanimateStudySession))
        self.assertLess(calls.index(PwanimateStudySession),calls.index(SparkTeachingState))

    def unlinked(self):
        from pwanimate.models import PwanimateConversation, PwanimateStudySession
        self.conv = PwanimateConversation.objects.create(user=self.user)
        self.session = PwanimateStudySession.objects.create(user=self.user,conversation=self.conv)
        self.source = PwanimateMessage.objects.create(conversation=self.conv,role='user',content='Explain')
        self.request.user_message_id = self.source.pk

    def start(self, mode='GUIDED', concept=False, query=None):
        if mode != 'GUIDED' or concept:
            Tutor.configure(self.session, self.user, mode=mode, concept_id=self.concept.pk if concept else None)
        if query:
            self.request.query = query
        self.prepared = Tutor.prepare(self.request)
        self.state = SparkTeachingState.objects.get(session=self.session)
        return self.prepared

    def answer(self, value, text=False):
        self.state.refresh_from_db()
        run = self.state.active_run
        interaction = SparkAgentInteraction.objects.get(run=run, status='pending')
        self.run, _ = claim_run(self.user, run, interaction=interaction,
            raw_answer={'text':value} if text else {'selected':[value]})
        self.prepared = Tutor.prepare(self.request, run=self.run)
        self.state.refresh_from_db()
        return self.prepared

    def practice(self, **kwargs):
        self.start(**kwargs)
        self.answer('practice')

    def test_transitions_keep_one_original_run_and_one_question(self):
        self.start()
        original = self.state.active_run_id
        self.assertEqual(self.state.phase, 'EXPLAIN')
        self.answer('continue'); self.assertEqual(self.state.phase, 'DEMONSTRATE')
        self.answer('continue'); self.assertEqual(self.state.phase, 'PRACTICE')
        self.answer(self.state.exercise['expected'], text=True)
        self.assertEqual(self.state.phase, 'EVALUATE')
        self.assertEqual(self.state.difficulty, 2)
        self.assertEqual(self.state.active_run_id, original)
        self.assertEqual(SparkAgentRun.objects.count(), 1)
        self.assertEqual(SparkAgentInteraction.objects.filter(status='pending').count(), 1)
        self.assertFalse(SparkConceptEvidence.objects.exists())

    def test_retry_same_source_does_not_repeat_transition(self):
        first=self.start(); again=Tutor.prepare(self.request)
        self.assertEqual(first['teaching_turn_id'], again['teaching_turn_id'])
        self.assertEqual(SparkTeachingTurn.objects.count(),1)

    def test_replay_answer_claim_rejected_without_duplicate_run(self):
        self.start(); run=self.state.active_run; interaction=SparkAgentInteraction.objects.get(run=run,status='pending')
        claimed,_=claim_run(self.user,run,interaction=interaction,raw_answer={'selected':['continue']})
        with self.assertRaises(RunError): claim_run(self.user,claimed,interaction=interaction,raw_answer={'selected':['continue']})
        Tutor.prepare(self.request,run=claimed)
        cached=Tutor.prepare(self.request,run=claimed)
        self.assertEqual(SparkTeachingTurn.objects.count(),2)
        self.assertTrue(cached['teaching_turn_id'])

    def test_hint_levels_are_progressive_and_full_solution_is_assisted(self):
        self.practice()
        hints=[]
        for level in range(1,5):
            self.answer('hint',text=True); hints.append(self.prepared['answer_markdown'])
            self.assertEqual(self.state.hint_level,level)
        self.assertEqual(self.state.phase,'ADAPT')
        self.assertEqual(len(set(hints)),4)
        self.assertEqual(self.state.turns.last().assistance,'worked_solution')
        self.assertFalse(SparkConceptEvidence.objects.exists())

    def test_incorrect_and_uncertain_answers_do_not_assert_misconception(self):
        self.practice(); self.answer('999',text=True)
        self.assertFalse(self.state.turns.last().evaluation['correct'])
        self.answer('practice'); self.answer('I think I get it',text=True)
        self.assertIsNone(self.state.turns.last().evaluation['correct'])
        self.assertFalse(SparkConceptEvidence.objects.exists())

    def test_repeated_errors_simplify(self):
        self.practice(); self.state.difficulty=3; self.state.save()
        self.answer('999',text=True); self.answer('practice'); self.answer('999',text=True)
        self.assertEqual(self.state.difficulty,2)

    def test_direct_solution_respects_request_without_question_or_evidence(self):
        result=self.start(query=LINE+' just give me the full solution')
        self.assertIsNone(result['run_id'])
        self.assertEqual(self.state.turns.get().assistance,'worked_solution')
        self.assertFalse(SparkConceptEvidence.objects.exists())

    def test_mode_switch_keeps_history_and_original_run(self):
        self.start(); original=self.state.active_run_id
        changed=Tutor.configure(self.session,self.user,mode='DEEP')
        self.answer('continue')
        self.assertEqual(self.state.mode,'DEEP'); self.assertEqual(self.state.active_run_id,original)
        self.assertEqual(self.state.turns.count(),2)
        self.assertIn('Another viewpoint',self.prepared['answer_markdown'])

    def test_quick_has_no_compulsory_question(self):
        self.start(mode='QUICK'); self.assertFalse(self.prepared['run_id'])

    def test_practice_mode_starts_with_attempt_not_solution(self):
        self.start(mode='PRACTICE'); self.assertEqual(self.state.phase,'PRACTICE')
        self.assertNotIn(self.state.exercise['substitution'],self.prepared['answer_markdown'])

    def test_prerequisite_missing_evidence_diagnosis(self):
        prerequisite=Mastery.create_concept(self.user,self.record.pk,key='arithmetic',title='Arithmetic')
        Mastery.update_concept(self.user,self.concept.pk,prerequisites=[str(prerequisite.pk)])
        self.start(concept=True,query='Explain determinants')
        self.assertEqual(self.state.phase,'DIAGNOSE')
        self.assertIn('missing evidence',self.prepared['answer_markdown'])
        self.answer('simpler'); self.assertEqual(self.state.phase,'EXPLAIN')

    def test_formal_hint_marks_assessment_assisted_and_never_mastered(self):
        self.practice(concept=True,query='Explain determinants')
        spec=self.state.question
        self.answer('hint',text=True)
        self.answer(REFERENCES[spec.reference_id]['expected'],text=True)
        evidence=SparkConceptEvidence.objects.get(question=spec)
        self.assertEqual(evidence.assistance,'hint')
        self.assertFalse(Mastery._validated_row(evidence)['independent'])
        self.concept.refresh_from_db(); self.assertNotEqual(self.concept.state,'MASTERED')

    def test_later_hint_does_not_rewrite_original_independence(self):
        self.practice(concept=True,query='Explain determinants'); spec=self.state.question
        self.answer(REFERENCES[spec.reference_id]['expected'],text=True)
        evidence=SparkConceptEvidence.objects.get(question=spec)
        SparkTeachingTurn.objects.create(state=self.state,question=spec,receipt_key='later-hint',
            from_phase='EVALUATE',to_phase='ADAPT',action='hint',assistance='hint',payload={})
        self.assertTrue(Mastery._validated_row(evidence)['independent'])

    def test_limited_help_request_enters_hint_machine_without_revealing_result(self):
        self.practice()
        exercise = dict(self.state.exercise)
        self.answer("Show me where I went wrong, but don't give me the complete solution", text=True)
        turn = self.state.turns.last()
        self.assertEqual(turn.action, 'hint')
        self.assertEqual(turn.assistance, 'hint')
        self.assertEqual(self.state.hint_level, 1)
        answer = self.prepared['answer_markdown']
        self.assertNotIn(str(exercise.get('expected')), answer)
        self.assertNotIn(str(exercise.get('substitution')), answer)

    def test_help_requests_escalate_then_full_solution_is_assisted(self):
        self.practice()
        self.answer("Show me where I went wrong, but don't give me the complete solution", text=True)
        self.assertEqual(self.state.hint_level, 1)
        self.answer('another hint', text=True)
        self.assertEqual(self.state.hint_level, 2)
        self.answer('show me the full solution', text=True)
        self.assertEqual(self.state.hint_level, 4)
        turn = self.state.turns.last()
        self.assertEqual(turn.action, 'solution')
        self.assertEqual(turn.assistance, 'worked_solution')

    def test_fresh_limited_help_request_routes_to_hint_not_free_answer(self):
        result = self.start(query=LINE + "; show me where I went wrong but don't give me the complete solution")
        turn = self.state.turns.get()
        self.assertEqual(turn.action, 'hint')
        self.assertEqual(turn.assistance, 'hint')
        # Restrained requests reach the answer model flagged as a hint, never
        # as an unrestricted explanation that can reveal the final result.
        self.assertEqual(result['answer_markdown'], '')
        self.assertEqual(result['context']['requested_action'], 'hint')

    def test_prepare_emits_truthful_teaching_check_and_replay_stays_quiet(self):
        from pwanimate.orchestrator.activity import public_event
        events = []
        Tutor.prepare(self.request, on_progress=events.append)
        check = [e for e in events if isinstance(e, dict) and e.get('id') == 'stage-teaching_check']
        self.assertEqual(len(check), 1)
        self.assertIsNotNone(public_event(check[0]))
        # Cached receipt replay returns before preparation work — no re-emit.
        replay = []
        Tutor.prepare(self.request, on_progress=replay.append)
        self.assertFalse(any(isinstance(e, dict) and e.get('id') == 'stage-teaching_check' for e in replay))

    def test_assistance_request_classification(self):
        from pwanimate.services.teaching import _assistance_request
        for text in ["Show me where I went wrong, but don't give me the complete solution",
                     'give me a hint', 'another hint', 'just a clue', 'where did I go wrong',
                     "don't give me the answer yet", 'not the full solution']:
            self.assertEqual(_assistance_request(text), 'hint', text)
        for text in ['show me the full solution', 'just give me the answer',
                     'what is the answer', 'direct solution please',
                     "I don't want a hint, show me the solution"]:
            self.assertEqual(_assistance_request(text), 'solution', text)
        for text in ['3, -1, 2', 'is this wrong?', 'I think the normal is (1,0,0)',
                     'the answer is 5', 'thanks']:
            self.assertIsNone(_assistance_request(text), text)

    def test_greetings_and_unrelated_conversations_stay_outside_teacher(self):
        self.request.query='hello'; self.assertFalse(Tutor.applies(self.request))
        self.request.user_message_id=None; self.request.query=LINE
        self.assertFalse(Tutor.applies(self.request)); self.assertFalse(SparkTeachingState.objects.exists())

    def test_wrong_owner_and_invalid_api_writes(self):
        url=f'/api/pwanimate/study-sessions/{self.session.pk}/teaching/'
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(url).status_code,404)
        self.assertEqual(self.client.patch(url,{'mode':'DEEP'},format='json').status_code,404)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.patch(url,{'mode':'DEEP','phase':'MASTERED'},format='json').status_code,400)
        self.assertEqual(self.client.patch(url,{'mode':[]},format='json').status_code,400)
        self.assertEqual(self.client.patch(url,{'mode':'DEEP'},format='json').status_code,200)

    def test_paused_session_does_not_consume_queued_answer(self):
        self.start(); self.session.status='paused'; self.session.save()
        with self.assertRaises(RunError): self.answer('continue')
        self.assertEqual(SparkTeachingTurn.objects.count(),1)

    def test_model_fallback_uses_one_answer_path_without_planning(self):
        self.unlinked()
        self.start(query='Explain electric potential')
        fake=Mock(); fake._run_tool.return_value=OrchestrationResponse(answer='Potential is energy per charge.',provider='mock',model='mock',finish_reason='stop')
        result=Tutor.finish(self.request,self.prepared,fake,time.perf_counter())
        fake._run_tool.assert_called_once()
        self.assertEqual(result.metadata['teaching_baseline']['planning_calls'],0)
        Tutor.finish(self.request,self.prepared,fake,time.perf_counter())
        fake._run_tool.assert_called_once()

    def test_choice_answer_is_evaluated_not_treated_as_command(self):
        self.unlinked()
        self.practice(query='Explain photosynthesis'); self.answer('a')
        self.assertTrue(self.state.turns.last().evaluation['correct'])

    def test_revocation_blocks_saved_pending_teaching_without_new_writes(self):
        from pwanimate.services.unit_learning import UnitLearningService
        self.start(); UnitLearningService.set_mode(self.user, 'disabled')
        with self.assertRaises(ValueError): self.answer('continue')
        self.assertEqual(SparkTeachingTurn.objects.count(),1)

    def test_unrelated_unit_is_not_attributed_to_current_session(self):
        self.request.query='Explain recursion in Java'
        self.assertFalse(Tutor.applies(self.request))
        with self.assertRaises(ValueError): Tutor.prepare(self.request)
        self.assertFalse(SparkTeachingState.objects.exists())

    def test_cross_unit_concept_cannot_be_attached(self):
        from tests.sals_03_wp1.test_mastery import approve_record
        other_record,_=approve_record(self.user,self.second)
        other_concept=Mastery.create_concept(self.user,other_record.pk,key='recursion',title='Recursion')
        with self.assertRaises(ValueError): Tutor.configure(self.session,self.user,mode='GUIDED',concept_id=other_concept.pk)
        self.assertFalse(SparkTeachingState.objects.exists())

    def test_real_agent_entry_uses_server_teaching_state_and_no_planner(self):
        from pwanimate.orchestrator.agent import StudyAgent
        gateway=Mock(); registry=Mock()
        result=StudyAgent(gateway,registry).run(self.request)
        self.assertTrue(result['teaching_turn_id']); registry.list_tools.assert_not_called(); gateway.generate.assert_not_called()

    def test_existing_executor_resumes_original_run_with_visible_explanation(self):
        from pwanimate.services.agent_execution import execute_claimed
        from pwanimate.orchestrator.service import PwanimateOrchestrator
        self.start(); original=self.state.active_run_id
        interaction=SparkAgentInteraction.objects.get(run_id=original,status='pending')
        run,_=claim_run(self.user,self.state.active_run,interaction=interaction,raw_answer={'selected':['continue']})
        orchestrator=PwanimateOrchestrator()
        with patch('pwanimate.api.views.PwanimateChatView.get_orchestrator',return_value=orchestrator), \
             patch('pwanimate.context.user_context.UserContextService.build',return_value=None):
            result=execute_claimed(run)
        self.state.refresh_from_db(); run.refresh_from_db()
        self.assertEqual(self.state.active_run_id,original); self.assertEqual(run.status,'waiting')
        self.assertEqual(run.result_message.task,'teaching_pause')
        self.assertIn('Equate the parameter',run.result_message.content)
        self.assertEqual(SparkAgentRun.objects.count(),1)

    def test_verified_error_signature_gets_focused_explanation_not_asserted_diagnosis(self):
        self.practice(concept=True,query='Explain determinants')
        spec=self.state.question
        signatures=REFERENCES[spec.reference_id]['misconceptions']
        self.answer(next(iter(signatures)),text=True)
        evaluation=self.state.turns.last().evaluation
        self.assertEqual(evaluation['signals'],list(signatures.values()))
        self.assertEqual(evaluation['next_action'],'alternative_representation')
        self.assertIn('?',self.prepared['answer_markdown'])

    def test_clarification_changes_representation(self):
        self.start(); first=self.prepared['answer_markdown']
        self.answer('clarify'); self.assertNotEqual(first,self.prepared['answer_markdown'])
        self.assertIn('shared slider',self.prepared['answer_markdown'])

    def test_generic_direct_solution_uses_model_instead_of_stopping(self):
        self.unlinked(); result=self.start(query='Explain electric potential; just give me the full solution')
        self.assertEqual(result['answer_markdown'],'')
        fake=Mock(); fake._run_tool.return_value=OrchestrationResponse(answer='Requested solution.',provider='mock',model='mock')
        response=Tutor.finish(self.request,result,fake,time.perf_counter())
        fake._run_tool.assert_called_once(); self.assertEqual(response.answer,'Requested solution.')
        self.assertEqual(self.state.turns.get().assistance,'worked_solution')

    def test_cancelled_claim_does_not_create_next_teaching_question(self):
        self.start()
        run=self.state.active_run; interaction=SparkAgentInteraction.objects.get(run=run,status='pending')
        claimed,_=claim_run(self.user,run,interaction=interaction,raw_answer={'selected':['continue']})
        SparkAgentRun.objects.filter(pk=claimed.pk).update(cancel_requested=True)
        result=Tutor.prepare(self.request,run=claimed)
        self.assertTrue(result['cancelled'])
        self.assertEqual(self.state.turns.count(),1)
        self.assertFalse(SparkAgentInteraction.objects.filter(run=run,status='pending').exists())

    def test_formal_independent_success_selects_harder_reviewed_question(self):
        self.practice(concept=True,query='Explain determinants')
        spec=self.state.question
        self.assertEqual(spec.difficulty,1)
        self.answer(REFERENCES[spec.reference_id]['expected'],text=True)
        self.answer('practice')
        self.assertEqual(self.state.question.difficulty,2)

    def test_quick_math_reaches_result_with_division_conditions(self):
        self.start(mode='QUICK')
        self.assertIn(r'\frac{x-1}{2}=\frac{y-2}{-1}=\frac{z-3}{4}',self.prepared['answer_markdown'])
        self.assertIn('do not divide by zero',self.prepared['answer_markdown'])

    def test_existing_concept_resolves_only_inside_owned_unit(self):
        self.start(query='Explain determinants')
        self.assertEqual(self.state.concept_id,self.concept.pk)

    def test_different_known_topic_does_not_reuse_old_concept_binding(self):
        self.start(concept=True)
        self.assertIsNone(self.state.concept_id)
        self.answer('practice')
        self.assertIsNone(self.state.question_id)

    def test_selected_documents_use_grounded_model_path_and_survive_pause(self):
        self.request.context_resources=[{'type':'document','id':17,'pageNumber':3}]
        result=self.start()
        self.assertEqual(self.state.lesson_key,'general')
        self.assertEqual(result['answer_markdown'],'')
        self.assertEqual(self.state.active_run.snapshot['context_resources'],self.request.context_resources)

    def test_attachments_keep_existing_multimodal_route(self):
        self.request.attachments=[{'name':'solution.png'}]
        self.assertFalse(Tutor.applies(self.request))

    def test_revoked_tracking_falls_back_to_normal_conversation_routing(self):
        from pwanimate.services.unit_learning import UnitLearningService
        self.start(); UnitLearningService.set_mode(self.user,'disabled')
        self.assertFalse(Tutor.applies(self.request))

    def test_generation_failure_retries_saved_answer_on_same_run_without_advancing_again(self):
        from pwanimate.services.agent_execution import execute_claimed, mark_failed
        from pwanimate.orchestrator.service import PwanimateOrchestrator
        self.unlinked(); self.start(query='Explain electric potential')
        original=self.state.active_run_id
        interaction=SparkAgentInteraction.objects.get(run_id=original,status='pending')
        run,_=claim_run(self.user,self.state.active_run,interaction=interaction,raw_answer={'selected':['continue']})
        orchestrator=PwanimateOrchestrator()
        with patch('pwanimate.api.views.PwanimateChatView.get_orchestrator',return_value=orchestrator), \
             patch('pwanimate.context.user_context.UserContextService.build',return_value=None), \
             patch.object(orchestrator,'_run_tool',side_effect=RuntimeError('transient model failure')):
            with self.assertRaises(RuntimeError): execute_claimed(run)
        mark_failed(run,RuntimeError('transient model failure'))
        run.refresh_from_db()
        self.assertEqual(run.continuation['interaction_id'],str(interaction.pk))
        before=list(self.state.turns.values_list('pk',flat=True))
        with patch('pwanimate.tasks.agent_runs.dispatch_run') as dispatch:
            result=self.client.post(f'/api/pwanimate/agent-runs/{original}/retry/',{},format='json')
            self.assertEqual(result.status_code,202); dispatch.assert_called_once()
        run.refresh_from_db()
        with patch('pwanimate.api.views.PwanimateChatView.get_orchestrator',return_value=orchestrator), \
             patch('pwanimate.context.user_context.UserContextService.build',return_value=None), \
             patch.object(orchestrator,'_run_tool',return_value=OrchestrationResponse(answer='A worked example of potential.',provider='',model='')):
            execute_claimed(run)
        run.refresh_from_db()
        self.assertEqual(run.status,'waiting')
        self.assertEqual(list(self.state.turns.values_list('pk',flat=True)),before)
        self.assertEqual(SparkAgentRun.objects.count(),1)
        self.assertEqual(SparkAgentInteraction.objects.filter(run=run,status='pending').count(),1)
        self.assertEqual(SparkAgentInteraction.objects.get(pk=interaction.pk).status,'answered')
