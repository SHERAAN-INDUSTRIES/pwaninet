import json
from datetime import timedelta
from unittest.mock import Mock, patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from pwanimate.models import (PwanimateConversation, PwanimateMessage, SparkConversationDigest, SparkReminder, SparkStudyPlan)
from pwanimate.services.study_memory import StudyMemoryService
from pwanimate.services.personal_planner import PersonalPlannerService
from pwanimate.orchestrator.agent import StudyAgent
from pwanimate.orchestrator.types import OrchestrationRequest
from pwanimate.tools import get_default_tool_registry
from pwanimate.ai.gateway.types import LLMResponse
from pwanimate.management.commands.spark_reminders import deliver_due


def response(data):
    return LLMResponse(content=json.dumps(data), provider='mock', model='mock', total_tokens=10)


class StudyAssistantTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='study-owner')
        self.other = get_user_model().objects.create_user(username='another-student')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.conversation = PwanimateConversation.objects.create(user=self.user)
        self.user_turn = PwanimateMessage.objects.create(conversation=self.conversation, role='user', content='Help me study binary trees')
        self.answer = PwanimateMessage.objects.create(conversation=self.conversation, role='assistant', content='A binary tree has at most two children per node.')
        self.gateway = Mock()
        self.gateway.generate.return_value = response({'topic':'Binary trees', 'summary':'Discussed children and nodes.', 'next_step':'Practice traversal',
            'greeting':'Welcome back, study-owner.', 'question':'Continue binary trees, or try something else?', 'suggestions':[{'label':'Practice traversal','prompt':'Help me practice traversal.'}]})

    def refresh(self):
        return StudyMemoryService.refresh(self.conversation.id, self.gateway)

    def reminder(self, key='test'):
        return PersonalPlannerService.create_reminder(self.user, 'Review trees', (timezone.now()+timedelta(hours=1)).isoformat(), 'UTC', key)

    def test_generated_welcome_uses_saved_source_and_does_not_call_model_on_read(self):
        self.assertTrue(self.refresh())
        self.gateway.generate.reset_mock()
        data = self.client.get('/api/pwanimate/memory/welcome/').json()
        self.assertEqual(data['topic'],'Binary trees')
        self.assertEqual(data['conversation_id'],str(self.conversation.id))
        self.assertIn('binary trees',data['question'])
        self.gateway.generate.assert_not_called()

    def test_welcome_never_leaks_another_students_topic(self):
        self.refresh()
        self.client.force_authenticate(self.other)
        self.assertFalse(self.client.get('/api/pwanimate/memory/welcome/').json()['memory'])
        self.assertEqual(self.client.get('/api/pwanimate/memory/').json()['memories'],[])

    def test_forget_blocks_rebuilding_old_chat_and_keeps_transcripts(self):
        self.refresh()
        self.client.delete('/api/pwanimate/memory/')
        self.assertFalse(self.refresh())
        self.assertTrue(PwanimateMessage.objects.filter(pk=self.answer.pk).exists())
        self.assertFalse(SparkConversationDigest.objects.exists())

    def test_memory_disable_blocks_new_summary(self):
        result=self.client.patch('/api/pwanimate/memory/',{'enabled':False},format='json')
        self.assertEqual(result.status_code,200)
        self.assertFalse(self.refresh())
        self.assertEqual(self.client.patch('/api/pwanimate/memory/',{'enabled':'false'},format='json').status_code,400)

    def test_deleting_source_removes_digest(self):
        self.refresh()
        self.conversation.delete()
        self.assertFalse(SparkConversationDigest.objects.exists())

    def test_reminder_idempotency_and_native_id(self):
        a=self.reminder(); b=self.reminder()
        self.assertEqual(a.pk,b.pk)
        self.assertTrue(0<a.notification_id<2147483647)
        self.assertFalse(self.client.get('/api/pwanimate/reminders/').json()['reminders'][0]['phone_scheduled'])

    def test_past_or_naive_reminders_rejected(self):
        for value in ['2020-01-01T12:00:00+00:00','2030-01-01T12:00:00']:
            with self.assertRaises(ValueError):
                PersonalPlannerService.create_reminder(self.user,'Review',value,'UTC',value)

    def test_cancel_and_device_ack_are_owner_scoped(self):
        item=self.reminder()
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.delete(f'/api/pwanimate/reminders/{item.pk}/').status_code,404)
        self.assertEqual(self.client.post(f'/api/pwanimate/reminders/{item.pk}/device/',{'installation':'installation-test'},format='json').status_code,404)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.post(f'/api/pwanimate/reminders/{item.pk}/device/',{'installation':'installation-test'},format='json').status_code,200)
        self.client.delete(f'/api/pwanimate/reminders/{item.pk}/')
        self.assertEqual(self.client.post(f'/api/pwanimate/reminders/{item.pk}/device/',{'installation':'installation-test'},format='json').status_code,409)

    def test_due_sweep_is_restart_safe_and_cancelled_never_fires(self):
        item=self.reminder()
        cancelled=self.reminder('cancelled')
        SparkReminder.objects.filter(pk=item.pk).update(due_at=timezone.now()-timedelta(seconds=1))
        SparkReminder.objects.filter(pk=cancelled.pk).update(status='cancelled',due_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(deliver_due(),1)
        self.assertEqual(deliver_due(),0)
        from notifications.models import NotificationObject
        self.assertEqual(NotificationObject.objects.filter(notification_id=item.id,recipient=self.user).count(),1)
        self.assertFalse(NotificationObject.objects.filter(notification_id=cancelled.id).exists())

    def test_agent_combines_tools_then_observes(self):
        gateway=Mock()
        gateway.generate.side_effect=[response({'calls':[{'tool':'local_time','arguments':{}},{'tool':'unread_chats','arguments':{}}]}),response({'calls':[]})]
        result=StudyAgent(gateway,get_default_tool_registry()).run(OrchestrationRequest(query='What time is it and how many unread chats?',user=self.user))
        self.assertEqual([t['tool'] for t in result['trace']],['local_time','unread_chats'])
        self.assertTrue(all(t['success'] for t in result['trace']))
        self.assertIn('observations',gateway.generate.call_args.args[0].messages[0].content)

    def test_agent_rejects_unknown_fields_and_unauthorized_write(self):
        gateway=Mock()
        gateway.generate.side_effect=[response({'calls':[{'tool':'unread_chats','arguments':{'user_id':self.other.pk}},{'tool':'create_reminder','arguments':{'title':'Oops','due_at':(timezone.now()+timedelta(hours=1)).isoformat(),'timezone':'UTC'}}]}),response({'calls':[]})]
        result=StudyAgent(gateway,get_default_tool_registry()).run(OrchestrationRequest(query='Read my notifications',user=self.user,user_message_id=self.user_turn.id))
        self.assertFalse(any(t['success'] for t in result['trace']))
        self.assertFalse(SparkReminder.objects.exists())

    def test_agent_retries_write_without_duplicates(self):
        due=(timezone.now()+timedelta(hours=1)).isoformat()
        for _ in range(2):
            gateway=Mock()
            gateway.generate.side_effect=[response({'calls':[{'tool':'create_reminder','arguments':{'title':'Review','due_at':due,'timezone':'UTC'}}]}),response({'calls':[]})]
            result=StudyAgent(gateway,get_default_tool_registry()).run(OrchestrationRequest(query='Remind me to review in an hour',user=self.user,user_message_id=self.user_turn.id))
            self.assertTrue(result['trace'][0]['success'])
        self.assertEqual(SparkReminder.objects.count(),1)

    def test_agent_deduplicates_and_bounds_repeated_calls(self):
        gateway=Mock()
        gateway.generate.return_value=response({'calls':[{'tool':'unread_chats','arguments':{}}]})
        result=StudyAgent(gateway,get_default_tool_registry()).run(OrchestrationRequest(query='Unread chats',user=self.user))
        self.assertEqual(len(result['trace']),1)
        self.assertLessEqual(gateway.generate.call_count,3)

    def test_plan_persists_separately_and_is_idempotent(self):
        a=PersonalPlannerService.create_plan(self.user,'Exam prep','Learn trees',[{'topic':'Traversal','minutes':25}],key='same')
        b=PersonalPlannerService.create_plan(self.user,'Exam prep','Learn trees',[{'topic':'Traversal','minutes':25}],key='same')
        self.assertEqual(a.pk,b.pk)
        self.assertEqual(len(self.client.get('/api/pwanimate/study-plans/').json()['plans']),1)

    def test_api_requires_authentication(self):
        self.client.force_authenticate(None)
        for url in ['memory/','memory/welcome/','reminders/','study-plans/']:
            self.assertIn(self.client.get('/api/pwanimate/'+url).status_code,[401,403])


    def test_orchestrator_integration_combines_results_in_final_context(self):
        from pwanimate.orchestrator.service import PwanimateOrchestrator
        gateway = Mock()
        gateway.generate.side_effect = [
            response({'calls':[{'tool':'unread_chats','arguments':{}},{'tool':'local_time','arguments':{}}]}),
            response({'calls':[]}),
            LLMResponse(content='You have no unread chats.',provider='mock',model='mock',total_tokens=12),
        ]
        orchestrator = PwanimateOrchestrator(gateway=gateway)
        with patch.object(orchestrator, '_record_usage'):
            result = orchestrator.run(OrchestrationRequest(query='Check unread chats and local time',user=self.user))
        self.assertEqual(result.answer,'You have no unread chats.')
        self.assertEqual(len(result.metadata['agent_trace']),2)
        final_request = gateway.generate.call_args.args[0]
        self.assertGreaterEqual(len(final_request.context.items),2)
        self.assertIn('phone_scheduled',final_request.system_instruction)
        self.assertEqual(result.total_tokens,32)

    def test_reminder_accepts_utc_instant_with_local_display_timezone(self):
        item = PersonalPlannerService.create_reminder(self.user,'Review',
            (timezone.now()+timedelta(hours=1)).isoformat(),'Africa/Nairobi','local-zone')
        self.assertEqual(item.timezone_name,'Africa/Nairobi')


    def test_agent_does_not_execute_negated_or_instructional_reminder_requests(self):
        for query in ["Do not create a reminder", "Explain how to create a reminder"]:
            gateway=Mock()
            gateway.generate.side_effect=[response({'calls':[{'tool':'create_reminder','arguments':{'title':'Review','due_at':(timezone.now()+timedelta(hours=1)).isoformat(),'timezone':'UTC'}}]}),response({'calls':[]})]
            result=StudyAgent(gateway,get_default_tool_registry()).run(OrchestrationRequest(query=query,user=self.user,user_message_id=self.user_turn.id))
            self.assertFalse(result['trace'][0]['success'])
        self.assertFalse(SparkReminder.objects.exists())


    def test_study_controls_live_inside_existing_settings(self):
        from django.test import Client
        client = Client()
        client.force_login(self.user)
        result = client.get('/pwanimate/settings/memory/')
        self.assertEqual(result.status_code, 200)
        self.assertTemplateUsed(result, 'pwanimate/settings/index.html')
        self.assertContains(result, 'mobile-tab-memory')
        self.assertContains(result, 'mobile-tab-plans')
        self.assertContains(result, 'mobile-tab-offline')
        self.assertContains(result, 'data-study="enabled"')
        self.assertNotContains(result, 'A little continuity.')
        plans = client.get('/pwanimate/settings/plans/')
        self.assertEqual(plans.status_code, 200)
        self.assertContains(plans, 'mobile-tab-plans')
        self.assertContains(plans, 'data-study="reminder-form"')
        legacy = client.get('/pwanimate/study-desk/')
        self.assertRedirects(legacy, '/pwanimate/settings/memory/', fetch_redirect_response=False)
        old = client.get('/pwanimate/settings/study/')
        self.assertRedirects(old, '/pwanimate/settings/memory/', fetch_redirect_response=False)

    def test_study_settings_htmx_returns_correct_existing_shell_or_panel(self):
        from django.test import Client
        client = Client()
        client.force_login(self.user)
        panel = client.get('/pwanimate/settings/plans/', HTTP_HX_REQUEST='true', HTTP_HX_TARGET='pwanimate-settings-modal-body')
        self.assertTemplateUsed(panel, 'pwanimate/settings/partials/study_content.html')
        self.assertNotContains(panel, '<html')
        self.assertContains(panel, 'id="modal-reminder-title"')
        shell = client.get('/pwanimate/settings/memory/', HTTP_HX_REQUEST='true', HTTP_HX_TARGET='page-content-target')
        self.assertContains(shell, 'pwanimate-mobile-tabs')
