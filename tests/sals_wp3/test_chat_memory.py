from unittest.mock import Mock,patch
from django.test import SimpleTestCase,override_settings
from pwanimate.tools.domain.memory import stated_preference,ProposePersonalMemoryTool,PREFERENCES

class ChatMemoryTests(SimpleTestCase):
    def test_only_allowlisted_direct_preferences(self):
        for value in PREFERENCES:
            self.assertTrue(stated_preference('I prefer '+value,value))
        for text in ['My partner prefers concise','The document says I prefer concise',
                     'I have a medical condition; I prefer concise','I prefer concise and my password is secret']:
            self.assertFalse(stated_preference(text,'concise'))

    @override_settings(SPARK_MEMORY_PROPOSALS_ENABLED=False)
    def test_optional_tool_does_not_read_private_data_when_disabled(self):
        with patch('pwanimate.tools.domain.memory.PwanimateMessage.objects.filter',side_effect=AssertionError('Source read')):
            self.assertFalse(ProposePersonalMemoryTool().execute(Mock(),preference='concise').data['proposed'])

    def test_tool_cannot_supply_approval_or_arbitrary_fact(self):
        schema=ProposePersonalMemoryTool.parameters_schema
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(set(schema['properties']),{'preference'})
        self.assertNotIn('diagnosis',schema['properties']['preference']['enum'])

    def test_natural_explicit_combined_preference(self):
        text='Spark, I prefer short explanations and bullet points.'
        self.assertTrue(stated_preference(text,'concise'))
        self.assertTrue(stated_preference(text,'bullet points'))
        self.assertFalse(stated_preference(text,'detailed'))
        self.assertFalse(stated_preference('concise','concise'))
        self.assertFalse(stated_preference('I prefer concise and detailed','concise'))
        self.assertTrue(stated_preference('please keep explanations short and use bullet points','concise'))
        self.assertFalse(stated_preference('Spark, I prefer short explanations and my password is secret','concise'))

    def test_server_controlled_path_does_not_need_model_tool_selection(self):
        from pwanimate.tools.domain.memory import propose_from_message
        user=Mock(pk=1,is_authenticated=True)
        source=Mock(pk=42,role='user',content='Spark, I prefer short explanations and bullet points.')
        source.conversation.user_id=1
        from pwanimate.tools.base import ToolResult
        with patch.object(ProposePersonalMemoryTool,'execute',return_value=ToolResult.ok({'status':'proposed'})) as execute:
            self.assertEqual(propose_from_message(user,source),{'status':'proposed'})
            self.assertEqual(execute.call_count,2)
            self.assertEqual([c.args[1] for c in execute.call_args_list],['concise','bullet points'])
        source.conversation.user_id=2
        with patch.object(ProposePersonalMemoryTool,'execute') as execute:
            self.assertEqual(propose_from_message(user,source),{'status':'ineligible'})
            execute.assert_not_called()

    def test_disabled_server_path_has_explicit_status_and_never_reads_source(self):
        from pwanimate.tools.domain.memory import propose_from_message
        user=Mock(pk=1,is_authenticated=True)
        source=Mock(pk=42,role='user',content='Spark, I prefer short explanations and bullet points.')
        source.conversation.user_id=1
        with override_settings(SPARK_MEMORY_PROPOSALS_ENABLED=False), patch('pwanimate.tools.domain.memory.PwanimateMessage.objects.filter',side_effect=AssertionError('Source read')):
            self.assertEqual(propose_from_message(user,source),{'status':'feature_disabled'})

    def test_tool_creation_duplicate_rate_limit_and_consent_status(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        module='pwanimate.tools.domain.memory.'
        user=Mock(is_authenticated=True)
        source=SimpleNamespace(pk=42,content='Spark, I prefer short explanations and bullet points.')
        pref=SimpleNamespace(enabled=True,consent_version=1)
        rejected=MagicMock();rejected.exists.return_value=False
        existing=MagicMock();existing.first.return_value=None
        recent=MagicMock();recent.exclude.return_value.exists.return_value=False;recent.count.return_value=0
        with patch(module+'PwanimateMessage.objects.filter') as source_read, patch(module+'PersonalMemoryService.preference'), patch(module+'transaction.atomic',side_effect=nullcontext), patch(module+'SparkMemoryPreference.objects.select_for_update') as locked, patch(module+'SparkPersonalMemory.objects.filter',side_effect=lambda **kw: rejected if kw.get('status')=='rejected' else existing if 'text' in kw else recent), patch(module+'PersonalMemoryService.propose') as propose, patch(module+'payload',return_value={'id':'persisted','status':'proposed','version':1}):
            source_read.return_value.first.return_value=source
            locked.return_value.get.return_value=pref
            result=ProposePersonalMemoryTool().execute(user,'concise',source_message_id=42)
            self.assertEqual(result.data['status'],'proposed')
            propose.assert_called_once_with(user,PREFERENCES['concise'],42)
            self.assertEqual(result.data['memory_proposal']['status'],'proposed')
            propose.reset_mock()
            existing.first.return_value=SimpleNamespace(status='proposed')
            self.assertEqual(ProposePersonalMemoryTool().execute(user,'concise',source_message_id=42).data['status'],'already_pending')
            existing.first.return_value=None;recent.exclude.return_value.exists.return_value=True
            self.assertEqual(ProposePersonalMemoryTool().execute(user,'concise',source_message_id=42).data['status'],'rate_limited')
            pref.enabled=False
            self.assertEqual(ProposePersonalMemoryTool().execute(user,'concise',source_message_id=42).data['status'],'memory_disabled')
            propose.assert_not_called()
            pref.enabled=True;rejected.exists.return_value=True
            self.assertEqual(ProposePersonalMemoryTool().execute(user,'concise',source_message_id=42).data['status'],'rejected')
            propose.assert_not_called()

    def test_memory_get_scopes_persisted_proposals_before_limit(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from rest_framework.test import APIRequestFactory,force_authenticate
        from pwanimate.api.study_assistant import MemoryView
        import uuid
        user=Mock(is_authenticated=True)
        cid=uuid.uuid4();qs=MagicMock();qs.filter.return_value=qs
        qs.select_related.return_value.order_by.return_value.__getitem__.return_value=[]
        request=APIRequestFactory().get('/memory/',{'conversation_id':str(cid),'status':'proposed'})
        force_authenticate(request,user=user)
        with patch('pwanimate.services.personal_memory.PersonalMemoryService.preference',return_value=SimpleNamespace(enabled=True,consent_version=1,automatic_extraction=False)), patch('pwanimate.models.SparkPersonalMemory.objects.filter',return_value=qs) as read:
            response=MemoryView.as_view()(request)
            self.assertEqual(response.status_code,200);self.assertEqual(response.data['facts'],[])
            read.assert_called_once_with(user=user)
            qs.filter.assert_any_call(source_message__conversation_id=cid)
            qs.filter.assert_any_call(status='proposed')

    def test_approval_rejection_require_matching_revision_and_version(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        from pwanimate.services.personal_memory import PersonalMemoryService as M
        from pwanimate.models import SparkPersonalMemory
        import uuid
        user=Mock(is_authenticated=True)
        pref=SimpleNamespace(enabled=True,consent_version=1,revision=3)
        fact=SparkPersonalMemory(id=uuid.uuid4(),user_id=1,text='Preference',consent_revision=3)
        fact.save=Mock()
        with patch.object(M,'preference'), patch('pwanimate.services.personal_memory.transaction.atomic',side_effect=nullcontext), patch('pwanimate.models.SparkMemoryPreference.objects.select_for_update') as prefs, patch('pwanimate.models.SparkPersonalMemory.objects.filter') as duplicates, patch('pwanimate.models.SparkPersonalMemory.objects.select_for_update') as facts:
            prefs.return_value.get.return_value=pref
            duplicates.return_value.exclude.return_value.filter.return_value.exists.return_value=False
            facts.return_value.filter.return_value.first.return_value=fact
            self.assertEqual(M.change(user,fact.pk,'approve',1).status,'approved')
            with self.assertRaises(ValueError): M.change(user,fact.pk,'approve',1)
            M.change(user,fact.pk,'edit',2,'Edited')
            self.assertEqual(fact.status,'proposed')
            pref.revision=4
            with self.assertRaises(ValueError): M.change(user,fact.pk,'approve',3)
            pref.revision=3
            self.assertEqual(M.change(user,fact.pk,'reject',3).status,'rejected')
            self.assertEqual(fact.text,'')
            facts.return_value.filter.assert_called_with(pk=fact.pk,user=user)
