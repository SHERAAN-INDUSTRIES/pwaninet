"""Reproducible local controller baseline; no external provider latency claim."""
import json
import time
from unittest.mock import Mock
from django.test import TestCase
from pwanimate.models import PwanimateConversation, PwanimateStudySession, PwanimateMessage
from pwanimate.orchestrator.types import OrchestrationRequest
from pwanimate.services.teaching import TeachingService as Tutor
from tests.sals_03_wp1.test_mastery import setup_mastery
from tests.sals_03_wp2.test_teaching import LINE


class TeachingBaselineTests(TestCase):
    def test_representative_controller_baselines(self):
        user,*_=setup_mastery()
        for name,query in [('mathematics',LINE),('programming','Explain recursion'),('theory','Explain photosynthesis')]:
            conversation=PwanimateConversation.objects.create(user=user)
            session=PwanimateStudySession.objects.create(user=user,conversation=conversation)
            source=PwanimateMessage.objects.create(conversation=conversation,role='user',content=query)
            request=OrchestrationRequest(query=query,user=user,user_message_id=source.pk,study_mode=True)
            started=time.perf_counter()
            prepared=Tutor.prepare(request)
            orchestrator=Mock()
            result=Tutor.finish(request,prepared,orchestrator,started)
            orchestrator._run_tool.assert_not_called()
            self.assertEqual(result.metadata['teaching_baseline']['model_calls'],0)
            print('WP2_LOCAL_BASELINE '+json.dumps({'scenario':name,**result.metadata['teaching_baseline']}))
            session.status='completed'; session.save()
