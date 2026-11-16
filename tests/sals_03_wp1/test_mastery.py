from unittest import skipUnless
from unittest.mock import patch
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, IntegrityError, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from documents.models import StudentUnitEnrollment, Semester
from pwanimate.models import (PwanimateConversation, PwanimateMessage, PendingAction, SparkAssessment,
    SparkUnitConcept, SparkConceptQuestion, SparkConceptEvidence, SparkConceptTransition, SparkConceptPrerequisite,
    SparkPersonalMemory, SparkAgentRun)
from pwanimate.assessment_validation import REFERENCES, validate_questions
from pwanimate.mastery_policy import policy_config
from pwanimate.services.actions import ActionService
from pwanimate.services.unit_learning import UnitLearningService as Units
from pwanimate.services.assessment import AssessmentService
from pwanimate.services.concept_mastery import ConceptMasteryService as Mastery
from tests.sals_02.test_foundation import setup_student


def approve_record(user, enrollment):
    Units.preference(user)
    conv = PwanimateConversation.objects.create(user=user)
    msg = PwanimateMessage.objects.create(conversation=conv, role='user', content='Explain '+enrollment.academic_unit.name)
    Units.process_message(msg.pk)
    action = PendingAction.objects.get(user=user, conversation=conv, status='pending')
    ActionService.confirm(user, action.pk)
    return user.unit_learning_records.get(enrollment=enrollment, semester=enrollment.semester), conv


def setup_mastery():
    user, other, term, enrollment, second = setup_student()
    term.end_date = timezone.localdate() + timedelta(days=365)
    term.save()
    record, conv = approve_record(user, enrollment)
    concept = Mastery.create_concept(user, record.pk, key='determinants', title='Determinants')
    return user, other, term, enrollment, second, record, conv, concept


class MasteryTests(TestCase):
    def setUp(self):
        self.user, self.other, self.term, self.enrollment, self.second, self.record, self.conv, self.concept = setup_mastery()
        self.session = self.record.sessions.get()
        self.t0 = timezone.now() + timedelta(seconds=1)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def at(self, days):
        return patch('django.utils.timezone.now', return_value=self.t0 + timedelta(days=days))

    def deliver(self, count=3, purpose='practice'):
        return Mastery.deliver_reference_assessment(self.user, self.concept.pk, self.session.pk, count=count, purpose=purpose)

    def answer(self, assessment, correct=True):
        results = []
        for item in list(assessment.questions):
            value = REFERENCES[item['reference_id']]['expected'] if correct else '999'
            results.append(AssessmentService.submit_answer(assessment, item['qid'], value))
        self.concept.refresh_from_db()
        return results

    def mastered(self):
        with self.at(0):
            initial = self.deliver(5)
            self.answer(initial)
        with self.at(8):
            retention = self.deliver(2, 'retention')
            self.answer(retention)
        self.assertEqual(self.concept.state, 'MASTERED')
        return initial, retention

    def test_initial_identity_and_empty_evidence(self):
        self.assertEqual(self.concept.state, 'NOT_STARTED')
        self.assertEqual(self.concept.version, 1)
        again = Mastery.create_concept(self.user, self.record.pk, key='determinants', title='Ignored duplicate')
        self.assertEqual(again.pk, self.concept.pk)
        self.assertEqual(self.record.concepts.count(), 1)
        self.assertEqual(self.concept.transitions.count(), 1)
        self.assertEqual(Mastery.recalculate(self.user, self.concept.pk).state, 'NOT_STARTED')

    def test_correct_answer_alone_never_mastered(self):
        with self.at(0):
            self.answer(self.deliver(1))
        self.assertEqual(self.concept.state, 'PRACTICING')
        self.assertIsNotNone(self.concept.last_assessed_at)
        evidence = self.concept.evidence.get()
        self.assertEqual(evidence.kind, 'independent_solution')
        self.assertEqual(evidence.verification, 'verified')

    def test_many_immediate_correct_answers_need_retention(self):
        with self.at(0):
            self.answer(self.deliver(6))
            self.answer(self.deliver(6, 'retention'))
        self.assertEqual(self.concept.state, 'PRACTICING')
        self.assertFalse(self.concept.transitions.last().decision['requirements']['retention'])

    def test_retention_must_be_delivered_after_delay_not_merely_answered_late(self):
        with self.at(0):
            self.answer(self.deliver(5))
            retention = self.deliver(2, 'retention')
        with self.at(8):
            self.answer(retention)
        self.assertEqual(self.concept.state, 'PRACTICING')
        self.assertTrue(all(e.kind == 'retention_check' for e in self.concept.evidence.filter(question__assessment=retention)))

    def test_verified_variety_retention_and_scheduled_review(self):
        self.mastered()
        self.assertEqual(self.concept.evidence.count(), 7)
        self.assertEqual(self.concept.last_reviewed_at, self.t0 + timedelta(days=8))
        with self.at(39):
            payload = Mastery.concept_payload(self.concept)
            self.assertEqual(payload['effective_state'], 'REVIEW_DUE')
            Mastery.recalculate(self.user, self.concept.pk)
        self.concept.refresh_from_db()
        self.assertEqual(self.concept.state, 'REVIEW_DUE')
        with self.at(40):
            self.answer(self.deliver(2, 'retention'))
        self.assertEqual(self.concept.state, 'MASTERED')

    def test_one_later_error_requires_review_two_recent_errors_regress(self):
        self.mastered()
        with self.at(9):
            self.answer(self.deliver(1), False)
        self.assertEqual(self.concept.state, 'REVIEW_DUE')
        with self.at(10):
            self.answer(self.deliver(1), False)
        self.assertEqual(self.concept.state, 'PRACTICING')
        self.assertEqual(self.concept.evidence.count(), 9)
        versions = list(self.concept.transitions.values_list('version', flat=True))
        self.assertEqual(versions, list(range(1, self.concept.version + 1)))
        self.assertIn('MASTERED', self.concept.transitions.values_list('to_state', flat=True))

    def test_assisted_and_unknown_answers_never_certify(self):
        for assistance in ['hint', 'worked_solution', 'unknown']:
            with self.at(0):
                key, ref = next(iter(REFERENCES.items()))
                assessment = SparkAssessment.objects.create(user=self.user, session=self.session, counts_toward_mastery=True,
                    questions=validate_questions([{'qid':'q1', 'prompt':ref['prompt'], 'answer':ref['expected'], 'reference_id':key}], 1, 'immediate'))
                Mastery.bind_existing_question(self.user, self.concept.pk, assessment.pk, 'q1', assistance=assistance)
                AssessmentService.submit_answer(assessment, 'q1', ref['expected'])
        self.concept.refresh_from_db()
        self.assertNotEqual(self.concept.state, 'MASTERED')
        self.assertEqual(self.concept.transitions.last().decision['independent_questions'], 0)
        self.assertEqual(self.concept.evidence.filter(kind='assisted_solution').count(), 2)

    def test_incorrect_answers_and_supported_misconception(self):
        with self.at(0):
            assessment = self.deliver(1)  # Stable bank order starts with the 2x2 reference.
            self.assertEqual(assessment.questions[0]['reference_id'], 'det-2x2-v1')
            AssessmentService.submit_answer(assessment, 'q1', '2')
        self.concept.refresh_from_db()
        self.assertEqual(self.concept.state, 'PRACTICING')
        self.assertEqual(self.concept.misconceptions, ['reverses_determinant_sign'])
        self.assertEqual(self.concept.evidence.get().kind, 'attempt')

    def test_unverified_keys_are_attempts_not_verified_evidence(self):
        with self.at(0):
            assessment = SparkAssessment.objects.create(user=self.user, session=self.session,
                questions=validate_questions([{'prompt':'What is 2 plus 2?', 'answer':'4'}], 1, 'immediate'))
            Mastery.bind_existing_question(self.user, self.concept.pk, assessment.pk, 'q1', assistance='none')
            result = AssessmentService.submit_answer(assessment, 'q1', '4')
            replay = AssessmentService.submit_answer(assessment, 'q1', '999')
            self.assertTrue(replay['already_graded'])
            self.assertEqual(assessment.questions[0]['student_answer'], '4')
        self.concept.refresh_from_db()
        self.assertIsNone(result['correct'])
        self.assertEqual(self.concept.evidence.get().verification, 'unverified')
        self.assertEqual(self.concept.state, 'LEARNING')

    def test_self_report_is_learning_only_and_personal_memory_is_not_imported(self):
        source = PwanimateMessage.objects.create(conversation=self.conv, role='user', content='I understand matrices')
        Units.process_message(source.pk)
        activity = self.record.activities.get()
        Mastery.record_activity(self.user, self.concept.pk, activity.pk)
        self.concept.refresh_from_db()
        self.assertEqual(self.concept.state, 'LEARNING')
        self.assertEqual(self.concept.evidence.get().kind, 'self_report')
        self.assertEqual(self.concept.evidence.get().verification, 'unverified')
        SparkPersonalMemory.objects.create(user=self.user, text='I have mastered determinants', status='approved')
        Mastery.recalculate(self.user, self.concept.pk)
        self.concept.refresh_from_db()
        self.assertEqual(self.concept.state, 'LEARNING')

    def test_retry_and_recalculation_are_idempotent(self):
        with self.at(0):
            assessment = self.deliver(1)
            self.answer(assessment)
            version = self.concept.version
            for _ in range(3):
                AssessmentService.submit_answer(assessment, 'q1', REFERENCES[assessment.questions[0]['reference_id']]['expected'])
                Mastery.capture(self.user, assessment.pk, 'q1')
                Mastery.recalculate(self.user, self.concept.pk)
        self.concept.refresh_from_db()
        self.assertEqual(self.concept.evidence.count(), 1)
        self.assertEqual(self.concept.version, version)

    def test_database_evidence_uniqueness_and_payload_immutability(self):
        with self.at(0):
            self.answer(self.deliver(1))
        evidence = self.concept.evidence.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            SparkConceptEvidence.objects.create(concept=self.concept, question=evidence.question,
                session=self.session, kind='attempt', occurred_at=self.t0)
        evidence.student_response = 'malicious correction'
        with self.assertRaises(ValidationError):
            evidence.save()
        self.concept.state = 'MASTERED'
        with self.assertRaises(ValidationError):
            self.concept.save()

    def test_invalid_format_does_not_create_evidence_or_grade(self):
        with self.at(0):
            assessment = self.deliver(1)
            result = AssessmentService.submit_answer(assessment, 'q1', 'NaN')
        self.assertEqual(result['outcome'], 'invalid')
        self.assertEqual(self.concept.evidence.count(), 0)
        self.assertIsNone(assessment.questions[0]['student_answer'])

    def test_assessment_exclusion_flag_cannot_be_bypassed_by_correct_grades(self):
        with self.at(0):
            assessment = self.deliver(5)
            assessment.counts_toward_mastery = False
            assessment.save()
            self.answer(assessment)
        self.assertEqual(self.concept.transitions.last().decision['independent_questions'], 0)
        self.assertNotEqual(self.concept.state, 'MASTERED')

    def test_policy_changes_create_versioned_decisions_without_rewriting_history(self):
        with self.at(0):
            self.answer(self.deliver(1))
            original = self.concept.transitions.last()
            with override_settings(SALS_MASTERY_POLICY={'min_questions':6}):
                Mastery.recalculate(self.user, self.concept.pk)
        original.refresh_from_db()
        self.assertEqual(original.policy['min_questions'], 5)
        self.assertEqual(self.concept.transitions.last().policy['min_questions'], 6)
        self.assertNotEqual(original.policy_version, self.concept.transitions.last().policy_version)

    def test_account_deletion_can_cascade_owned_mastery_provenance(self):
        with self.at(0):
            self.answer(self.deliver(1))
        self.user.delete()
        self.assertFalse(SparkUnitConcept.objects.exists())
        self.assertFalse(SparkConceptEvidence.objects.exists())

    def test_revoked_and_invalid_evidence_preserves_history_and_removes_credit(self):
        self.mastered()
        count = self.concept.evidence.count()
        with self.at(9):
            for index, evidence in enumerate(list(self.concept.evidence.all())):
                Mastery.invalidate_evidence(self.user, evidence.pk, 'Reviewed grading/provenance error', invalid=index % 2 == 0)
                Mastery.invalidate_evidence(self.user, evidence.pk, 'Repeated retry')
        self.concept.refresh_from_db()
        self.assertEqual(self.concept.evidence.count(), count)
        self.assertEqual(self.concept.state, 'NOT_STARTED')
        self.assertTrue(self.concept.transitions.filter(to_state='MASTERED').exists())
        self.assertTrue(all(e.revocation_reason == 'Reviewed grading/provenance error' for e in self.concept.evidence.all()))

    def test_tampered_question_or_response_cannot_supply_credit(self):
        with self.at(0):
            assessment = self.deliver(1)
            self.answer(assessment)
            assessment.questions[0]['prompt'] = 'An unrelated question'
            assessment.save()
            Mastery.recalculate(self.user, self.concept.pk)
        self.concept.refresh_from_db()
        self.assertEqual(self.concept.state, 'NOT_STARTED')

    def test_moved_assessment_cannot_rewrite_its_source_session(self):
        with self.at(0):
            assessment = self.deliver(1)
            self.answer(assessment)
            other_session = Units._session(self.user, self.record, new=True)
            assessment.session = other_session
            assessment.save()
            Mastery.recalculate(self.user, self.concept.pk)
        self.concept.refresh_from_db()
        self.assertEqual(self.concept.state, 'NOT_STARTED')
        self.assertEqual(self.concept.evidence.get().session_id, self.session.pk)

    def test_model_authored_correct_flag_does_not_override_reference(self):
        with self.at(0):
            assessment = self.deliver(1)
            assessment.questions[0].update(student_answer='999', outcome='correct', correct=True, graded_at=self.t0.isoformat())
            assessment.save()
            evidence = Mastery.capture(self.user, assessment.pk, 'q1')
        self.concept.refresh_from_db()
        self.assertEqual(evidence.verification, 'unverified')
        self.assertNotEqual(self.concept.state, 'MASTERED')

    def test_prerequisites_are_same_scope_acyclic_and_block_mastery(self):
        prereq = Mastery.create_concept(self.user, self.record.pk, key='matrix-arithmetic', title='Matrix arithmetic')
        Mastery.update_concept(self.user, self.concept.pk, prerequisites=[str(prereq.pk)], parent_id=str(prereq.pk))
        with self.assertRaises(ValueError):
            Mastery.update_concept(self.user, prereq.pk, prerequisites=[str(self.concept.pk)])
        with self.at(0):
            self.answer(self.deliver(5))
        with self.at(8):
            self.answer(self.deliver(2, 'retention'))
        self.assertEqual(self.concept.state, 'PRACTICING')
        self.assertFalse(self.concept.transitions.last().decision['requirements']['prerequisites'])

    def test_students_units_and_semesters_are_separate(self):
        other_enrollment = StudentUnitEnrollment.objects.create(user=self.other, academic_unit=self.enrollment.academic_unit, semester=self.term)
        other_record, _ = approve_record(self.other, other_enrollment)
        other_concept = Mastery.create_concept(self.other, other_record.pk, key='determinants', title='Determinants')
        with self.assertRaises(LookupError):
            Mastery.recalculate(self.user, other_concept.pk)
        with self.assertRaises(ValueError):
            Mastery.update_concept(self.user, self.concept.pk, prerequisites=[str(other_concept.pk)])
        second_record, _ = approve_record(self.user, self.second)
        wrong_concept = Mastery.create_concept(self.user, second_record.pk, key='determinants', title='Determinants')
        with self.assertRaises(ValueError):
            Mastery.deliver_reference_assessment(self.user, wrong_concept.pk, second_record.sessions.get().pk)
        term2 = Semester.objects.create(number=2, academic_year=self.term.academic_year,
            start_date=self.term.start_date, end_date=self.term.end_date)
        self.enrollment.semester = term2
        self.enrollment.save()
        record2, _ = approve_record(self.user, self.enrollment)
        new = Mastery.create_concept(self.user, record2.pk, key='determinants', title='Determinants')
        self.assertNotEqual(new.pk, self.concept.pk)
        self.assertEqual(new.state, 'NOT_STARTED')
        with self.assertRaises(ValueError):
            Mastery.recalculate(self.user, self.concept.pk)

    def test_session_assessment_and_activity_provenance(self):
        second_record, second_conv = approve_record(self.user, self.second)
        wrong_session = second_record.sessions.get()
        with self.assertRaises(ValueError):
            Mastery.deliver_reference_assessment(self.user, self.concept.pk, wrong_session.pk)
        source = PwanimateMessage.objects.create(conversation=second_conv, role='user', content='Explain recursion')
        Units.process_message(source.pk)
        with self.assertRaises(LookupError):
            Mastery.record_activity(self.user, self.concept.pk, second_record.activities.get().pk)

    def test_disabled_consent_and_unapproved_past_response_cannot_be_imported(self):
        with self.at(0):
            assessment = self.deliver(1)
            # An old server response without a captured receipt must not be imported after reauthorization.
            AssessmentService._submit_answer_locked(assessment, 'q1', REFERENCES[assessment.questions[0]['reference_id']]['expected'])
        with self.at(1):
            Units.set_mode(self.user, 'disabled')
            with self.assertRaises(ValueError):
                Mastery.capture(self.user, assessment.pk, 'q1')
            Units.set_mode(self.user, 'automatic')
            self.record, self.conv = approve_record(self.user, self.enrollment)
            with self.assertRaises(ValueError):
                Mastery.capture(self.user, assessment.pk, 'q1')

    def test_trusted_negative_correction_remains_possible_after_consent_revocation(self):
        with self.at(0):
            self.answer(self.deliver(1))
        evidence = self.concept.evidence.get()
        Units.set_mode(self.user, 'disabled')
        Mastery.invalidate_evidence(self.user, evidence.pk, 'Correcting a discovered invalid reference', invalid=True)
        self.concept.refresh_from_db()
        self.assertEqual(self.concept.state, 'NOT_STARTED')
        self.assertEqual(self.concept.evidence.get().verification, 'invalid')

    def test_policy_configuration_rejects_weakened_or_invalid_guards(self):
        for value in [{'min_questions': 1}, {'retention_days': 0}, {'min_contexts': 1}, {'min_accuracy': float('nan')}, {'unknown': 2}]:
            with override_settings(SALS_MASTERY_POLICY=value), self.assertRaises(ValueError):
                policy_config()

    def test_owner_api_delivery_submission_capture_detail_and_summary(self):
        base = f'/api/pwanimate/academic/concepts/{self.concept.pk}/'
        with self.at(0):
            response = self.client.post(base+'assessments/', {'session_id': str(self.session.pk), 'count': 1}, format='json')
            self.assertEqual(response.status_code, 201, response.data)
            self.assertNotIn('answer_key', str(response.data))
            assessment = SparkAssessment.objects.get(pk=response.data['assessment']['id'])
            item = assessment.questions[0]
            result = self.client.post(f'/api/pwanimate/study-sessions/{self.session.pk}/assessments/',
                {'action':'answer', 'assessment_id':str(assessment.pk), 'qid':item['qid'],
                 'answer': REFERENCES[item['reference_id']]['expected']}, format='json')
            self.assertEqual(result.status_code, 200, result.data)
            self.assertEqual(result.data['result']['concept_mastery']['state'], 'PRACTICING')
            response = self.client.post(base+'evidence/', {'assessment_id':str(assessment.pk),'qid':item['qid']}, format='json')
            self.assertEqual(response.status_code, 201)
            self.assertEqual(self.concept.evidence.count(), 1)
        self.assertEqual(self.client.get(base).status_code, 200)
        self.assertEqual(self.client.get(base+'evidence/').data['evidence'][0]['verification'], 'verified')
        summary = self.client.get(f'/api/pwanimate/academic/records/{self.record.pk}/mastery/').data
        self.assertEqual(summary['states']['PRACTICING'], 1)
        self.assertIsNone(summary['probability'])
        self.assertEqual(SparkAgentRun.objects.count(), 0)

    def test_api_rejects_forged_states_verdicts_assistance_and_foreign_access(self):
        base = f'/api/pwanimate/academic/concepts/{self.concept.pk}/'
        for path, payload in [(base, {'state':'MASTERED'}), (base+'evidence/', {'verification':'verified','correct':True}),
                (base+'assessments/', {'session_id':str(self.session.pk),'assistance':'none'})]:
            response = self.client.patch(path, payload, format='json') if path == base else self.client.post(path, payload, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(base).status_code, 404)
        self.assertEqual(self.client.get(base+'evidence/').status_code, 404)
        self.assertEqual(self.client.post(base+'recalculate/', {}, format='json').status_code, 404)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(base).status_code, [401,403])


@skipUnless(connection.vendor == 'postgresql', 'Owner executes PostgreSQL-specific WP1 verification')
class PostgreSQLMasteryConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user, _, _, _, _, self.record, _, self.concept = setup_mastery()
        self.session = self.record.sessions.get()

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
        for thread in threads: thread.join(25)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])

    def test_concurrent_concept_creation_is_unique(self):
        self.race(*[lambda: Mastery.create_concept(self.user, self.record.pk, key='new-concept', title='New') for _ in range(2)])
        self.assertEqual(self.record.concepts.filter(key='new-concept').count(), 1)

    def test_concurrent_same_submission_yields_one_evidence_and_state_revision(self):
        assessment = Mastery.deliver_reference_assessment(self.user, self.concept.pk, self.session.pk, count=1)
        answer = REFERENCES[assessment.questions[0]['reference_id']]['expected']
        self.race(*[lambda: AssessmentService.submit_answer(SparkAssessment.objects.get(pk=assessment.pk), 'q1', answer) for _ in range(2)])
        self.assertEqual(self.concept.evidence.count(), 1)
        self.assertEqual(self.concept.transitions.count(), 2)

    def test_concurrent_different_answers_do_not_lose_grades(self):
        assessment = Mastery.deliver_reference_assessment(self.user, self.concept.pk, self.session.pk, count=2)
        items = list(assessment.questions)
        self.race(*[lambda item=item: AssessmentService.submit_answer(SparkAssessment.objects.get(pk=assessment.pk),
            item['qid'], REFERENCES[item['reference_id']]['expected']) for item in items])
        assessment.refresh_from_db()
        self.assertTrue(all(q['correct'] is True for q in assessment.questions))
        self.assertEqual(self.concept.evidence.count(), 2)

    def test_concurrent_capture_and_recalculation_are_idempotent(self):
        assessment = Mastery.deliver_reference_assessment(self.user, self.concept.pk, self.session.pk, count=1)
        AssessmentService.submit_answer(assessment, 'q1', REFERENCES[assessment.questions[0]['reference_id']]['expected'])
        before = self.concept.transitions.count()
        self.race(lambda: Mastery.capture(self.user, assessment.pk, 'q1'), lambda: Mastery.recalculate(self.user, self.concept.pk))
        self.assertEqual(self.concept.transitions.count(), before)

    def test_revocation_and_replay_do_not_resurrect_credit(self):
        assessment = Mastery.deliver_reference_assessment(self.user, self.concept.pk, self.session.pk, count=1)
        answer = REFERENCES[assessment.questions[0]['reference_id']]['expected']
        AssessmentService.submit_answer(assessment, 'q1', answer)
        evidence = self.concept.evidence.get()
        self.race(lambda: Mastery.invalidate_evidence(self.user, evidence.pk, 'Operator invalidation'),
            lambda: AssessmentService.submit_answer(SparkAssessment.objects.get(pk=assessment.pk), 'q1', answer))
        evidence.refresh_from_db()
        self.concept.refresh_from_db()
        self.assertEqual(evidence.verification, 'revoked')
        self.assertEqual(self.concept.state, 'NOT_STARTED')

    def test_opposing_prerequisite_edits_cannot_create_a_cycle(self):
        other = Mastery.create_concept(self.user, self.record.pk, key='other', title='Other')
        def change(concept, prerequisite):
            try:
                Mastery.update_concept(self.user, concept.pk, prerequisites=[str(prerequisite.pk)])
            except ValueError:
                pass  # The conflicting acyclic edit won the record lock.
        self.race(lambda: change(self.concept, other), lambda: change(other, self.concept))
        self.assertEqual(SparkConceptPrerequisite.objects.count(), 1)
