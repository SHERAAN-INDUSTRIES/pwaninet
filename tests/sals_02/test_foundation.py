from unittest import skipUnless
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, transaction, IntegrityError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient
from documents.models import AcademicYear, Semester, AcademicUnit, StudentUnitEnrollment
from pwanimate.models import (PwanimateConversation, PwanimateMessage, PwanimateStudySession,
    SparkUnitLearningRecord, SparkUnitActivity, SparkAgentRun, PendingAction, SparkPersonalMemory)
from pwanimate.services.unit_learning import UnitLearningService as Units
from pwanimate.services.actions import ActionService, ActionError


def setup_student():
    today = timezone.localdate()
    year = AcademicYear.objects.create(code='SALS2026', name='SALS 2026',
        start_date=today - timezone.timedelta(days=120), end_date=today + timezone.timedelta(days=240))
    term = Semester.objects.create(number=1, academic_year=year,
        start_date=today - timezone.timedelta(days=60), end_date=today + timezone.timedelta(days=60))
    user = get_user_model().objects.create_user(username='unit-owner')
    other = get_user_model().objects.create_user(username='unit-other')
    algebra = AcademicUnit.objects.create(code='MAT B201', name='Linear Algebra',
        description='Matrices; orthogonal lines; symmetric equations of lines in 3D; vector spaces.')
    programming = AcademicUnit.objects.create(code='SCS B201', name='Programming', description='Recursion and functions in Java.')
    enrollment = StudentUnitEnrollment.objects.create(user=user, academic_unit=algebra, semester=term, academic_year=year)
    second = StudentUnitEnrollment.objects.create(user=user, academic_unit=programming, semester=term, academic_year=year)
    return user, other, term, enrollment, second


class UnitFoundationTests(TestCase):
    def setUp(self):
        self.user, self.other, self.term, self.enrollment, self.second = setup_student()
        self.conversation = PwanimateConversation.objects.create(user=self.user)
        Units.preference(self.user)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def message(self, text='Explain matrices', conversation=None):
        return PwanimateMessage.objects.create(conversation=conversation or self.conversation, role='user', content=text)

    def approve(self, text='Explain matrices'):
        message = self.message(text)
        Units.process_message(message.pk)
        for action in PendingAction.objects.filter(user=self.user, status='pending'):
            ActionService.confirm(self.user, action.pk)
        return SparkUnitLearningRecord.objects.get(user=self.user, enrollment=self.enrollment), message

    def test_reuses_enrollment_without_creating_any(self):
        result = Units.detect(self.user, 'Explain symmetric equations of lines in 3D.')
        self.assertEqual([m['enrollment_id'] for m in result['matches']], [self.enrollment.pk])
        self.assertEqual(StudentUnitEnrollment.objects.count(), 2)
        self.assertEqual(SparkUnitLearningRecord.objects.count(), 0)

    def test_catalog_description_resolves_special_topic(self):
        self.enrollment.academic_unit.name = 'Geometry II'
        self.enrollment.academic_unit.save()
        result = Units.detect(self.user, 'Explain symmetric equations of lines in 3D')
        self.assertEqual(result['matches'][0]['enrollment_id'], self.enrollment.pk)

    def test_non_enrolled_subject_not_inferred_from_personal_memory(self):
        self.second.is_active = False
        self.second.save()
        SparkPersonalMemory.objects.create(user=self.user, text='Interested in Java', status='approved')
        Units.process_message(self.message('Explain Java').pk)
        self.assertFalse(PendingAction.objects.exists())
        self.assertFalse(SparkUnitLearningRecord.objects.exists())

    def test_greetings_casual_and_quoted_text_are_not_activity(self):
        for text in ['Hello', 'I like Linear Algebra memes', 'My friend loves matrices',
                     'My friend asked me to explain matrices', 'I saw a meme saying explain matrices',
                     '```Explain matrices```', 'The document says explain matrices']:
            Units.process_message(self.message(text).pk)
        self.assertFalse(PendingAction.objects.exists())
        self.assertFalse(SparkUnitActivity.objects.exists())

    def test_empty_description_still_detects_core_unit_topics(self):
        # Real catalog rows ship with empty descriptions; subject vocabulary
        # must carry detection on its own.
        self.enrollment.academic_unit.description = ''
        self.enrollment.academic_unit.save()
        for text, topic in [
            ('Spark, teach me equations of planes in 3D.', 'equations of planes'),
            ('Quiz me on eigenvalues and vector spaces', 'eigenvalues'),
            ('Explain row reduction and rank', 'row reduction'),
            ('Teach me the dot product and cross product', 'dot product'),
        ]:
            result = Units.detect(self.user, text)
            topics = [m['topic'] for m in result['matches'] if m['enrollment_id'] == self.enrollment.pk]
            self.assertIn(topic, topics, text)

    def test_conversational_study_phrasing_reaches_detection(self):
        # Gate phrases students actually use, not only imperative verbs.
        for text in ["It's linear algebra, come up with something creative",
                     'Can we go through the problem sets in linear algebra',
                     'Quiz me on matrices',
                     'I am preparing for my linear algebra test',
                     'I struggle with determinants']:
            result = Units.detect(self.user, text)
            self.assertTrue(result['matches'], text)
            self.assertEqual(result['matches'][0]['enrollment_id'], self.enrollment.pk)

    def test_non_subject_chatter_still_ignored(self):
        for text in ['Any', 'Hello chief any updates',
                     'Do you have any past papers for me',
                     'Solve for me then invent other questions']:
            self.assertFalse(Units.detect(self.user, text)['matches'], text)

    def test_two_bare_topics_can_be_independently_attributed(self):
        result = Units.detect(self.user, 'Explain matrices and recursion')
        self.assertEqual({item['enrollment_id'] for item in result['matches']}, {self.enrollment.pk, self.second.pk})
        result = Units.detect(self.user, 'Explain matrices and my friend likes recursion')
        self.assertEqual([item['enrollment_id'] for item in result['matches']], [self.enrollment.pk])

    def test_ambiguity_abstains_instead_of_breaking_tie(self):
        self.second.academic_unit.description = 'Orthogonal lines and vector spaces.'
        self.second.academic_unit.save()
        result = Units.process_message(self.message('Explain orthogonal lines').pk)
        self.assertEqual(result['matches'], [])
        self.assertEqual(len(result['ambiguous'][0]['candidates']), 2)
        self.assertFalse(PendingAction.objects.exists())

    def test_explicit_unit_disambiguates(self):
        self.second.academic_unit.description = 'Orthogonal lines and vector spaces.'
        self.second.academic_unit.save()
        result = Units.detect(self.user, 'Explain orthogonal lines in Linear Algebra')
        self.assertEqual([m['enrollment_id'] for m in result['matches']], [self.enrollment.pk])

    def test_missing_term_is_not_invented(self):
        self.enrollment.semester = None
        self.enrollment.save()
        self.assertFalse(Units.detect(self.user, 'Explain matrices')['matches'])
        with self.assertRaises(ValueError):
            Units.get_record(self.user, self.enrollment)

    def test_student_profile_term_must_agree_when_present(self):
        other_term = Semester.objects.create(number=2, academic_year=self.term.academic_year,
            start_date=self.term.start_date, end_date=self.term.end_date)
        get_user_model().objects.filter(pk=self.user.pk).update(semester=other_term)
        self.assertEqual(Units.detect(self.user, 'Explain matrices')['matches'], [])

    def test_mismatched_academic_year_abstains(self):
        today = timezone.localdate()
        year = AcademicYear.objects.create(code='OTHER', name='Other', start_date=today, end_date=today)
        self.enrollment.academic_year = year
        self.enrollment.save()
        self.assertFalse(Units.detect(self.user, 'Explain matrices')['matches'])

    def test_one_record_per_real_enrollment_and_term(self):
        first = Units.get_record(self.user, self.enrollment)
        self.assertEqual(first.pk, Units.get_record(self.user, self.enrollment).pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SparkUnitLearningRecord.objects.create(user=self.user, enrollment=self.enrollment,
                academic_unit=self.enrollment.academic_unit, semester=self.term)

    def test_multiple_students_same_unit_have_separate_records(self):
        other_enrollment = StudentUnitEnrollment.objects.create(user=self.other,
            academic_unit=self.enrollment.academic_unit, semester=self.term)
        mine = Units.get_record(self.user, self.enrollment)
        theirs = Units.get_record(self.other, other_enrollment)
        self.assertNotEqual(mine.pk, theirs.pk)
        with self.assertRaises(ValueError):
            Units.get_record(self.user, other_enrollment)

    def test_term_snapshot_survives_enrollment_rollover_read_only(self):
        record, _ = self.approve()
        old_session = record.sessions.get()
        new_term = Semester.objects.create(number=2, academic_year=self.term.academic_year,
            start_date=self.term.start_date, end_date=self.term.end_date)
        self.enrollment.semester = new_term
        self.enrollment.save()
        other = Units.get_record(self.user, self.enrollment)
        record.refresh_from_db()
        self.assertNotEqual(record.pk, other.pk)
        self.assertEqual(record.semester_id, self.term.pk)
        self.assertTrue(Units.summary(record, self.user)['read_only'])
        response = self.client.post(f'/api/pwanimate/academic/records/{record.pk}/',
            {'action': 'resume', 'session_id': str(old_session.pk)}, format='json')
        self.assertEqual(response.status_code, 400)
        old_session.current_topic = 'An unauthorized historical edit'
        with self.assertRaises(ValidationError):
            old_session.save()

    def test_record_identity_and_session_link_cannot_be_reassigned(self):
        record, _ = self.approve()
        record.user = self.other
        with self.assertRaises(ValidationError):
            record.save()
        session = PwanimateStudySession.objects.get(learning_record_id=record.pk)
        session.learning_record = None
        with self.assertRaises(ValidationError):
            session.save()

    def test_legacy_unit_start_skips_readonly_old_term(self):
        from pwanimate.services.study_session import StudySessionService
        record, _ = self.approve()
        old_session = record.sessions.get()
        next_term = Semester.objects.create(number=2, academic_year=self.term.academic_year,
            start_date=self.term.start_date, end_date=self.term.end_date)
        self.enrollment.semester = next_term
        self.enrollment.save()
        session = StudySessionService.get_or_create_unit_session(self.user, unit=self.enrollment.academic_unit)
        self.assertNotEqual(session.pk, old_session.pk)
        self.assertIsNone(session.learning_record_id)  # Standalone study is not enrollment tracking consent.

    def test_academic_context_is_untrusted_and_has_no_mastery_claim(self):
        from pwanimate.context.types import ContextPackage
        record, _ = self.approve()
        Units.process_message(self.message('I understand matrices').pk)
        Units.process_message(self.message('Explain the difficulty of matrices').pk)
        context = Units.context_for_query(self.user, 'What have I studied in Linear Algebra?')
        packet = ContextPackage(query='Linear Algebra', academic_unit_context=context)
        self.assertIn('academic_unit_context', packet.to_dict())
        self.assertIn('self-reports are not demonstrated mastery', packet.format_context_text())
        self.assertEqual(context[0]['understanding_self_reports'], ['matrices'])
        self.assertFalse(context[0]['mastery_assessed'])
        self.assertEqual(context[0]['difficulties'], [])

    def test_legacy_session_endpoints_also_reject_historical_writes(self):
        record, _ = self.approve()
        session = record.sessions.get()
        self.enrollment.is_active = False
        self.enrollment.save()
        base = f'/api/pwanimate/study-sessions/{session.pk}/'
        self.assertEqual(self.client.get(base).status_code, 200)
        self.assertEqual(self.client.patch(base, {'current_topic': 'Changed'}, format='json').status_code, 403)
        self.assertEqual(self.client.post(base+'status/', {'action': 'resume'}, format='json').status_code, 403)
        self.assertEqual(self.client.post(base+'assessments/', {'action': 'generate'}, format='json').status_code, 403)

    def test_tracking_preference_and_pending_requests_api(self):
        Units.process_message(self.message().pk)
        data = self.client.get('/api/pwanimate/academic/').data
        self.assertEqual(data['tracking']['mode'], 'ask')
        self.assertEqual(len(data['pending_actions']), 1)
        self.assertEqual(data['records'], [])
        self.assertEqual(self.client.patch('/api/pwanimate/academic/', {'mode': []}, format='json').status_code, 400)
        self.assertEqual(self.client.patch('/api/pwanimate/academic/', {'mode': 'disabled'}, format='json').status_code, 200)
        self.assertEqual(self.client.get('/api/pwanimate/academic/').data['pending_actions'], [])

    def test_approved_history_reports_available_assessments_without_keys(self):
        from pwanimate.models import SparkAssessment
        record, _ = self.approve()
        SparkAssessment.objects.create(user=self.user, session=record.sessions.get(), title='Algebra practice',
            questions=[{'qid': 'a', 'correct': True, 'answer': 'secret-answer-key'}])
        data = Units.summary(record, self.user)
        self.assertEqual(data['assessments'][0]['correct'], 1)
        self.assertNotIn('secret-answer-key', str(data))
        self.assertFalse(data['mastery_assessed'])

    def test_approval_creates_record_but_never_imports_trigger(self):
        record, source = self.approve()
        self.assertEqual(record.sessions.count(), 1)
        self.assertFalse(SparkUnitActivity.objects.filter(source_message=source).exists())
        future = self.message('Explain orthogonal lines')
        Units.process_message(future.pk)
        self.assertEqual(record.activities.get().source_message_id, future.pk)

    def test_rejection_does_not_track_or_reask_in_same_conversation(self):
        source = self.message()
        Units.process_message(source.pk)
        action = PendingAction.objects.get()
        ActionService.cancel(self.user, action.pk)
        Units.process_message(self.message('Explain orthogonal lines').pk)
        self.assertEqual(PendingAction.objects.count(), 1)
        self.assertFalse(SparkUnitLearningRecord.objects.exists())

    def test_ask_mode_needs_new_conversation_approval(self):
        record, _ = self.approve()
        other_conversation = PwanimateConversation.objects.create(user=self.user)
        Units.process_message(self.message('Explain orthogonal lines', other_conversation).pk)
        self.assertFalse(record.activities.exists())
        self.assertEqual(PendingAction.objects.filter(status='pending').count(), 1)

    def test_automatic_is_not_authorization_for_an_unapproved_unit(self):
        Units.set_mode(self.user, 'automatic')
        Units.process_message(self.message().pk)
        self.assertFalse(SparkUnitLearningRecord.objects.exists())
        self.assertEqual(PendingAction.objects.filter(status='pending').count(), 1)

    def test_automatic_cross_conversation_continuity_and_history(self):
        Units.set_mode(self.user, 'automatic')
        record, _ = self.approve()
        for text in ['Explain matrices', 'Explain orthogonal lines']:
            conv = PwanimateConversation.objects.create(user=self.user)
            Units.process_message(self.message(text, conv).pk)
        self.assertEqual(record.activities.count(), 2)
        self.assertEqual(record.sessions.count(), 3)
        summary = Units.context_for_query(self.user, 'What have I studied in Linear Algebra?')[0]
        self.assertEqual(summary['topics'], ['matrices', 'orthogonal lines'])
        self.assertFalse(summary['mastery_assessed'])
        self.assertEqual(summary['assessments'], [])
        self.assertEqual(list(record.activities.values_list('occurred_at', flat=True)),
            sorted(record.activities.values_list('occurred_at', flat=True)))

    def test_replay_is_idempotent_and_history_question_is_not_new_activity(self):
        record, _ = self.approve()
        source = self.message('Explain matrices')
        Units.process_message(source.pk)
        Units.process_message(source.pk)
        Units.process_message(self.message('What have I studied in Linear Algebra?').pk)
        self.assertEqual(record.activities.count(), 1)

    def test_source_deletion_preserves_history_and_linked_session_deletion_is_explicit(self):
        record, _ = self.approve()
        Units.process_message(self.message('Explain matrices').pk)
        PwanimateMessage.objects.filter(conversation=self.conversation).delete()
        activity = record.activities.get()
        self.assertIsNone(activity.source_message_id)
        self.assertEqual(activity.source_conversation_id, self.conversation.pk)
        self.assertEqual(Units.summary(record, self.user)['topics'], ['matrices'])
        session = record.sessions.get()
        response = self.client.delete(f'/api/pwanimate/conversations/{session.conversation_id}/')
        self.assertEqual(response.status_code, 409)

    def test_account_deletion_cascades_academic_history(self):
        record, _ = self.approve()
        Units.process_message(self.message('Explain matrices').pk)
        self.user.delete()
        self.assertFalse(SparkUnitLearningRecord.objects.filter(pk=record.pk).exists())
        self.assertFalse(SparkUnitActivity.objects.exists())

    def test_multiple_units_keep_separate_canonical_sessions_and_observations(self):
        self.approve('Explain matrices')
        programming_conversation=PwanimateConversation.objects.create(user=self.user)
        trigger=self.message('Explain recursion',programming_conversation)
        Units.process_message(trigger.pk)
        ActionService.confirm(self.user,PendingAction.objects.get(conversation=programming_conversation,status='pending').pk)
        source=self.message('I struggle with matrices')
        Units.process_message(source.pk)
        Units.process_message(self.message('Explain recursion',programming_conversation).pk)
        facts=list(SparkUnitActivity.objects.order_by('topic'))
        self.assertEqual(len(facts),2)
        self.assertNotEqual(facts[0].record_id,facts[1].record_id)
        self.assertNotEqual(facts[0].session_id,facts[1].session_id)
        self.assertNotIn('recursion',facts[0].excerpt)
        self.assertNotIn('matrices',facts[1].excerpt)
        SparkPersonalMemory.objects.create(user=self.user,source_message=source,purpose='academic_learning',status='approved',text='Student reports difficulty with matrices.')
        programming=SparkUnitLearningRecord.objects.get(enrollment=self.second)
        self.assertEqual(Units.summary(programming,self.user)['approved_observations'],[])

    def test_revocation_blocks_future_and_stale_pending_approval(self):
        record, _ = self.approve()
        Units.process_message(self.message('Explain recursion').pk)
        pending = PendingAction.objects.get(status='pending')
        Units.set_mode(self.user, 'disabled')
        Units.process_message(self.message('Explain matrices').pk)
        self.assertFalse(record.activities.exists())
        with self.assertRaises(ActionError):
            ActionService.confirm(self.user, pending.pk)
        Units.set_mode(self.user, 'automatic')
        Units.process_message(self.message('Explain matrices').pk)
        self.assertFalse(record.activities.exists())

    def test_expired_term_and_inactive_enrollment_cannot_approve(self):
        Units.process_message(self.message().pk)
        action = PendingAction.objects.get()
        self.enrollment.is_active = False
        self.enrollment.save()
        with self.assertRaises(ActionError):
            ActionService.confirm(self.user, action.pk)
        self.assertFalse(SparkUnitLearningRecord.objects.exists())

    def test_lifecycle_reuses_engine_and_allows_many_sessions(self):
        record, _ = self.approve()
        session = record.sessions.get()
        path = f'/api/pwanimate/academic/records/{record.pk}/'
        for action, expected in [('pause', 'paused'), ('resume', 'active'), ('end', 'completed')]:
            response = self.client.post(path, {'action': action, 'session_id': str(session.pk)}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['session']['status'], expected)
        session.refresh_from_db()
        self.assertIsNotNone(session.ended_at)
        self.assertEqual(self.client.post(path, {'action': 'start'}, format='json').status_code, 400)
        response = self.client.post(path, {'action': 'start', 'tracking_consent': True}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(record.sessions.count(), 2)

    def test_owner_only_api_and_source_validation(self):
        record, source = self.approve()
        path = f'/api/pwanimate/academic/records/{record.pk}/'
        self.assertEqual(self.client.post('/api/pwanimate/academic/detect/', {'message_id': 'invalid'}, format='json').status_code, 400)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.post(path, {'action': 'start', 'tracking_consent': True}, format='json').status_code, 404)
        self.assertEqual(self.client.post('/api/pwanimate/academic/detect/', {'message_id': source.pk}, format='json').status_code, 404)
        listing = self.client.get('/api/pwanimate/academic/').data
        self.assertEqual(listing['records'], [])
        self.assertEqual(listing['enrollments'], [])
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(path).status_code, (401, 403))

    def test_consent_preserves_existing_waiting_run_and_continuation(self):
        from pwanimate.services.interactions import create_interaction
        source = self.message()
        run = SparkAgentRun.objects.create(user=self.user, user_message=source, status='waiting',
            wait_reason='clarification', snapshot={'query': source.content, 'history': [], 'user_message_id': source.pk},
            state={}, expires_at=timezone.now() + timezone.timedelta(minutes=15))
        interaction = create_interaction(run, prompt='How much time do you have?')
        Units.process_message(source.pk)
        action = PendingAction.objects.get()
        self.assertEqual(self.client.post(f'/api/pwanimate/actions/{action.pk}/', {}, format='json').status_code, 400)
        self.assertFalse(SparkUnitLearningRecord.objects.exists())
        response = self.client.post(f'/api/pwanimate/actions/{action.pk}/', {'decision': 'confirm'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        run.refresh_from_db()
        self.assertEqual(run.status, 'waiting')
        self.assertEqual(SparkAgentRun.objects.count(), 1)
        with patch('pwanimate.services.agent_execution.execute_claimed', return_value={'answer': 'Continuing'}) as execute:
            response = self.client.post('/api/pwanimate/chat/', {'resume_run_id': str(run.pk),
                'interaction_id': str(interaction.pk), 'answer': {'text': '20 minutes'}}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(execute.call_args.args[0].pk, run.pk)

    def test_personal_memory_consent_is_independent(self):
        from pwanimate.services.personal_memory import PersonalMemoryService
        self.assertFalse(PersonalMemoryService.preference(self.user).enabled)
        self.approve()
        self.assertFalse(PersonalMemoryService.preference(self.user).enabled)

    def test_persistence_and_capture_are_atomic(self):
        from pwanimate.services.conversation import ConversationService
        with patch.object(ConversationService, 'schedule_embedding'), patch.object(Units, 'process_message') as process:
            message = ConversationService.persist_user_message(self.conversation, 'Explain matrices')
            process.assert_called_once_with(message.pk)


@skipUnless(connection.vendor == 'postgresql', 'Owner executes PostgreSQL-specific verification')
class PostgreSQLUnitConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user, _, _, self.enrollment, _ = setup_student()
        Units.preference(self.user)

    def race(self, *operations):
        import threading
        from django.db import close_old_connections
        barrier, errors = threading.Barrier(len(operations)), []
        def call(operation):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                operation()
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()
        threads = [threading.Thread(target=call, args=(operation,)) for operation in operations]
        for thread in threads: thread.start()
        for thread in threads: thread.join(20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])

    def test_concurrent_record_creation(self):
        self.race(*[lambda: Units.get_record(self.user, self.enrollment) for _ in range(2)])
        self.assertEqual(SparkUnitLearningRecord.objects.count(), 1)

    def test_concurrent_approval_creates_one_session(self):
        conv = PwanimateConversation.objects.create(user=self.user)
        source = PwanimateMessage.objects.create(conversation=conv, role='user', content='Explain matrices')
        Units.process_message(source.pk)
        action = PendingAction.objects.get()
        self.race(*[lambda: ActionService.confirm(self.user, action.pk) for _ in range(2)])
        self.assertEqual(SparkUnitLearningRecord.objects.count(), 1)
        self.assertEqual(PwanimateStudySession.objects.count(), 1)
        self.assertEqual(SparkAgentRun.objects.count(), 0)

    def test_concurrent_attribution_is_idempotent(self):
        conv = PwanimateConversation.objects.create(user=self.user)
        source = PwanimateMessage.objects.create(conversation=conv, role='user', content='Explain matrices')
        Units.process_message(source.pk)
        ActionService.confirm(self.user, PendingAction.objects.get().pk)
        future = PwanimateMessage.objects.create(conversation=conv, role='user', content='Explain matrices')
        self.race(*[lambda: Units.process_message(future.pk) for _ in range(2)])
        self.assertEqual(SparkUnitActivity.objects.count(), 1)

    def test_approval_and_rejection_have_one_terminal_decision(self):
        conv = PwanimateConversation.objects.create(user=self.user)
        source = PwanimateMessage.objects.create(conversation=conv, role='user', content='Explain matrices')
        Units.process_message(source.pk)
        action = PendingAction.objects.get()
        def decide(approve):
            try:
                (ActionService.confirm if approve else ActionService.cancel)(self.user, action.pk)
            except ActionError:
                pass  # The other explicit decision won the row lock.
        self.race(lambda: decide(True), lambda: decide(False))
        action.refresh_from_db()
        self.assertIn(action.status, ['confirmed', 'cancelled'])
        self.assertEqual(SparkUnitLearningRecord.objects.count(), int(action.status == 'confirmed'))

    def test_revocation_serializes_with_new_activity(self):
        conv = PwanimateConversation.objects.create(user=self.user)
        source = PwanimateMessage.objects.create(conversation=conv, role='user', content='Explain matrices')
        Units.process_message(source.pk)
        ActionService.confirm(self.user, PendingAction.objects.get().pk)
        future = PwanimateMessage.objects.create(conversation=conv, role='user', content='Explain matrices')
        self.race(lambda: Units.set_mode(self.user, 'disabled'), lambda: Units.process_message(future.pk))
        pref = Units.preference(self.user)
        self.assertEqual(pref.mode, 'disabled')
        self.assertFalse(SparkUnitActivity.objects.filter(created_at__gt=pref.changed_at).exists())
