"""Actual assessment view/service/serializer path with owner lookup and writes mocked."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView
from pwanimate.api.academic_guard import AcademicSessionWriteGuard
from pwanimate.services.assessment import AssessmentService
from pwanimate.api.study_assistant import AssessmentView
from pwanimate.services.assessment import public_assessment
from pwanimate.assessment_validation import validate_questions, REFERENCES


class APICheckpointTests(SimpleTestCase):
    def setUp(self):
        guard = patch.object(AcademicSessionWriteGuard, 'initial', APIView.initial)
        guard.start()
        self.addCleanup(guard.stop)
        self.user=SimpleNamespace(is_authenticated=True,pk=1)
        self.session=SimpleNamespace(mastery_percent=77,progress_percent=55)
        q=dict(prompt=REFERENCES['det-diag-234-v1']['prompt'],answer='24',reference_id='det-diag-234-v1')
        self.assessment=SimpleNamespace(id='assessment',mode='immediate',title='Test',created_at=None,
            completed_at=None,status='active',session=self.session,save=Mock(),
            questions=validate_questions([q],1,'immediate'),counts_toward_mastery=False)

    def post(self,answer):
        request=APIRequestFactory().post('/api/pwanimate/study-sessions/session/assessments/',
            dict(action='answer',assessment_id='assessment',qid='q1',answer=answer),format='json')
        force_authenticate(request,user=self.user)
        # Database-free grading/serialization regression. Real guard/transaction
        # behavior is exercised by tests.sals_03_wp1's actual database/API tests.
        with patch.object(AcademicSessionWriteGuard, 'initial', APIView.initial), \
             patch.object(AssessmentService, 'submit_answer', side_effect=AssessmentService._submit_answer_locked), \
             patch.object(AssessmentView,'_session',return_value=self.session), \
             patch('pwanimate.api.study_assistant.SparkAssessment.objects.filter') as lookup:
            lookup.return_value.first.return_value=self.assessment
            response=AssessmentView.as_view()(request,session_id='session')
            lookup.assert_called_once_with(pk='assessment',session=self.session,user=self.user)
            return response

    def test_numeric_zero_is_incorrect_not_credit(self):
        r=self.post(0);self.assertEqual(r.status_code,200)
        self.assertIs(r.data['result']['correct'],False)
        self.assertEqual(r.data['result']['metrics']['correct'],0)
        self.assertEqual(self.session.mastery_percent,77)

    def test_invalid_input_does_not_persist(self):
        for value in ['NaN','2**4',True,{'value':'24'}]:
            r=self.post(value)
            self.assertEqual(r.data['result']['outcome'],'invalid')
            self.assertIsNone(r.data['result']['correct'])
        self.assessment.save.assert_not_called()

    def test_unverified_reference_is_inconclusive(self):
        self.assessment.questions[0].pop('reference_id')
        r=self.post('24')
        self.assertEqual(r.data['result']['outcome'],'inconclusive')
        self.assertEqual(r.data['result']['metrics']['graded'],0)
        self.assertNotIn('answer_key',str(r.data))

    def test_reference_failure_cannot_award_credit(self):
        self.assessment.questions[0]['answer_key']='12'
        r=self.post('24')
        self.assertEqual(r.data['result']['outcome'],'evaluation_failed')
        self.assertIsNone(r.data['result']['correct'])
        self.assertEqual(r.data['result']['metrics']['correct'],0)
        self.assertEqual(self.session.mastery_percent,77)

    def test_legacy_history_and_partial_metrics(self):
        self.assessment.questions=[dict(qid='old',prompt='Legacy',correct=True)]+[
            dict(qid=str(i),prompt='Pending',correct=None) for i in range(4)]
        p=public_assessment(self.assessment)
        self.assertEqual(p['certification_status'],'legacy_uncertified')
        self.assertEqual(p['metrics']['completion_percent'],20)
        self.assertEqual(p['metrics']['accuracy_percent'],100)
        self.assertEqual(p['metrics']['mastery_state'],'insufficient_evidence')

    def test_missing_questions_rejected_through_api(self):
        request=APIRequestFactory().post('/',dict(action='generate',count=5),format='json')
        force_authenticate(request,user=self.user)
        for key,value in dict(assessment_mode='mcq',unit=None,unit_label='',target_goal='',learning_objective='',roadmap=[]).items():
            setattr(self.session,key,value)
        with patch.object(AssessmentView,'_session',return_value=self.session), \
             patch('pwanimate.services.assessment.AIGateway') as gateway, \
             patch('pwanimate.services.assessment.SparkAssessment.objects.create') as create:
            gateway.return_value.generate.return_value.content='{"questions":[]}'
            response=AssessmentView.as_view()(request,session_id='session')
            self.assertEqual(response.status_code,400);create.assert_not_called()
