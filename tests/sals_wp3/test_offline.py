from unittest.mock import Mock,patch
from django.test import SimpleTestCase
from pwanimate.models import SparkMemoryPreference
from pwanimate.services.personal_memory import enabled
from pwanimate.services.study_memory import StudyMemoryService
from pwanimate.services.conversation_memory import ConversationMemoryService
from pwanimate.tasks.conversation_memory import embed_message
from pwanimate.tasks.study_memory import refresh_study_memory

class MemoryIsolationTests(SimpleTestCase):
    def test_default_and_legacy_are_not_consent(self):
        self.assertFalse(SparkMemoryPreference().enabled)
        self.assertFalse(enabled(SparkMemoryPreference(enabled=True)))
        self.assertTrue(enabled(SparkMemoryPreference(enabled=True,consent_version=1)))

    def test_legacy_jobs_do_not_read_or_publish_on_retry(self):
        gateway=Mock()
        for _ in range(2):
            self.assertFalse(StudyMemoryService.refresh('legacy',gateway))
            self.assertFalse(refresh_study_memory.run('legacy'))
            self.assertEqual(embed_message.run(42)['status'],'personal_fact_approval_required')
        gateway.generate.assert_not_called()

    def test_both_recall_routes_use_only_approved_fact_service(self):
        with patch('pwanimate.services.personal_memory.PersonalMemoryService.recall',return_value=[]) as read:
            self.assertEqual(ConversationMemoryService.search('actor','query'),[])
            self.assertEqual(ConversationMemoryService._lexical_fallback('actor','query',exclude_conversation_id=None,limit=6),[])
            self.assertEqual(read.call_count,2)

    def test_welcome_never_reads_legacy_digest(self):
        user=Mock();user.get_short_name.return_value='Owner'
        with patch('pwanimate.services.study_memory.SparkConversationDigest.objects.filter',side_effect=AssertionError('Legacy digest read')):
            self.assertFalse(StudyMemoryService.welcome(user)['memory'])
