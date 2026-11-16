"""Service regression with fake persistence only; runner configures Django without DB."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from pwanimate.services.assessment import AssessmentService, public_assessment
from pwanimate.assessment_validation import REFERENCES, validate_questions

class ServiceTests(SimpleTestCase):
    def assessment(self, verified=True):
        raw=dict(prompt=REFERENCES['det-diag-234-v1']['prompt'],answer='24',options=['6','12','24','48'])
        if verified: raw['reference_id']='det-diag-234-v1'
        items=validate_questions([raw],1,'mcq')
        return SimpleNamespace(status='active',questions=items,completed_at=None,save=Mock(),
            session=SimpleNamespace(mastery_percent=77,progress_percent=55))

    def test_service_deterministic_and_preserves_legacy(self):
        a=self.assessment();gateway=Mock()
        result=AssessmentService._submit_answer_locked(a,'q1','24',gateway=gateway)
        self.assertIs(result['correct'],True)
        self.assertEqual(result['mastery_state'],'insufficient_evidence')
        self.assertEqual(a.session.mastery_percent,77)
        gateway.generate.assert_not_called();a.save.assert_called_once()

    def test_invalid_does_not_write_or_call_provider(self):
        a=self.assessment();gateway=Mock()
        self.assertEqual(AssessmentService._submit_answer_locked(a,'q1','invalid',gateway)['outcome'],'invalid')
        a.save.assert_not_called();gateway.generate.assert_not_called()

    def test_unverified_is_inconclusive_and_no_mastery_write(self):
        a=self.assessment(False)
        result=AssessmentService._submit_answer_locked(a,'q1','24',gateway=Mock())
        self.assertIsNone(result['correct']);self.assertEqual(result['outcome'],'inconclusive')
        self.assertEqual(result['metrics']['graded'],0)

    def test_generation_invalid_never_persists(self):
        s=SimpleNamespace(assessment_mode='mcq',unit=None,unit_label='',target_goal='',learning_objective='',roadmap=[])
        gateway=Mock();gateway.generate.return_value.content='{"questions":[]}'
        with patch('pwanimate.services.assessment.SparkAssessment.objects.create') as create:
            with self.assertRaises(ValueError):AssessmentService.generate(s,count=5,gateway=gateway)
            create.assert_not_called()
