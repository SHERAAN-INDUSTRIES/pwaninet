import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.test import TestCase, SimpleTestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.test import APIClient
from pwanimate.ai.gateway.types import LLMResponse
from pwanimate.models import PwanimateConversation, PwanimateMessage, PendingAction, SparkAgentRun
from pwanimate.orchestrator.agent import StudyAgent
from pwanimate.orchestrator.service import PwanimateOrchestrator
from pwanimate.orchestrator.types import OrchestrationRequest
from pwanimate.orchestrator.activity import event
from pwanimate.services.agent_runs import AgentRunService, RunError
from pwanimate.services.actions import ActionService, ActionError
from pwanimate.tools import get_default_tool_registry


def reply(data):
    return LLMResponse(content=json.dumps(data),provider='mock',model='mock',total_tokens=10)


class AgentActivityTests(TestCase):
    def setUp(self):
        User=get_user_model()
        self.user=User.objects.create_user(username='agent-owner')
        self.target=User.objects.create_user(username='agent-peer')
        self.other=User.objects.create_user(username='unrelated')
        self.conversation=PwanimateConversation.objects.create(user=self.user)
        self.query='Send a message to agent-peer saying Hello and follow agent-peer.'
        self.turn=PwanimateMessage.objects.create(conversation=self.conversation,role='user',content=self.query)
        self.request=OrchestrationRequest(query=self.query,user=self.user,user_message_id=self.turn.id)
        self.client=APIClient();self.client.force_authenticate(self.user)

    def pause(self):
        self.events=[]
        gateway=Mock()
        gateway.generate.return_value=reply({'update':'I’ll prepare both actions for your review.','calls':[
            {'tool':'send_message','arguments':{'username':'agent-peer','message':'Hello'}},
            {'tool':'follow_user','arguments':{'username':'agent-peer'}}]})
        prepared=StudyAgent(gateway,get_default_tool_registry()).run(self.request,on_progress=self.events.append)
        self.assertTrue(prepared['run_id'])
        run=SparkAgentRun.objects.get(pk=prepared['run_id']);run.activity=self.events;run.save(update_fields=['activity'])
        return run

    def test_public_plan_streams_before_tool_and_tool_result_before_approval(self):
        with patch.object(ActionService,'_execute_send_message') as execute:
            run=self.pause()
            execute.assert_not_called()
        self.assertEqual(self.events[0]['kind'],'plan')
        self.assertEqual(self.events[1]['kind'],'tool')
        self.assertEqual(self.events[1]['status'],'running')
        self.assertEqual(self.events[2]['id'],self.events[1]['id'])
        self.assertEqual(self.events[2]['status'],'completed')
        self.assertEqual(self.events[-1]['kind'],'approval')
        self.assertEqual(run.status,'waiting')
        self.assertEqual(PendingAction.objects.filter(status='pending').count(),2)

    def test_approval_pauses_without_final_model_answer(self):
        run=self.pause()
        orchestrator=PwanimateOrchestrator(gateway=Mock())
        prepared={'run_id':str(run.id),'clarification':'','trace':[],'tokens':{}}
        with patch.object(orchestrator,'_record_usage'):
            result=orchestrator.finish_agent(self.request,prepared,0)
        orchestrator.gateway.generate.assert_not_called()
        self.assertTrue(result.metadata['awaiting_approval'])

    def test_single_approval_waits_for_remaining_action(self):
        run=self.pause()
        with patch.object(ActionService,'_execute_send_message',return_value={'message_id':999}) as send:
            run,ready=AgentRunService.decide(self.user,run.id,'confirm',[run.action_ids[0]])
        self.assertFalse(ready)
        self.assertEqual(run.status,'waiting')
        self.assertEqual(send.call_count,1)
        self.assertEqual(PendingAction.objects.filter(status='pending').count(),1)

    def test_approve_all_is_scoped_and_cannot_execute_twice(self):
        run=self.pause()
        with patch.object(ActionService,'_execute_send_message',return_value={'message_id':999}) as send, patch.object(ActionService,'_execute_follow_user',return_value={'followed':True}) as follow:
            run,ready=AgentRunService.decide(self.user,run.id,'confirm_all',run.action_ids)
            self.assertTrue(ready)
            self.assertEqual(run.status,'running')
            with self.assertRaises(RunError):AgentRunService.decide(self.user,run.id,'confirm_all',run.action_ids)
            self.assertEqual(send.call_count,1);self.assertEqual(follow.call_count,1)
        self.assertTrue(all(o['result']['data']['executed'] for o in run.state['observed']))

    def test_cannot_approve_another_users_run_or_unseen_action(self):
        run=self.pause()
        with self.assertRaises(RunError):AgentRunService.decide(self.other,run.id,'confirm_all',run.action_ids)
        with self.assertRaises(RunError):AgentRunService.decide(self.user,run.id,'confirm',['00000000-0000-0000-0000-000000000000'])
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f'/api/pwanimate/agent-runs/{run.id}/').status_code,404)
        self.assertEqual(PendingAction.objects.filter(status='confirmed').count(),0)

    def test_stale_approve_all_rejected(self):
        run=self.pause()
        with self.assertRaises(RunError):AgentRunService.decide(self.user,run.id,'confirm_all',[run.action_ids[0]])
        self.assertEqual(PendingAction.objects.filter(status='confirmed').count(),0)

    def test_decline_all_resumes_with_declined_observations_without_execution(self):
        run=self.pause()
        with patch.object(ActionService,'_execute_send_message') as send:
            run,ready=AgentRunService.decide(self.user,run.id,'cancel_all',run.action_ids)
            send.assert_not_called()
        self.assertTrue(ready)
        self.assertTrue(all(not o['result']['data']['executed'] for o in run.state['observed']))

    def test_expired_run_rejects_approval(self):
        run=self.pause();run.expires_at=timezone.now()-timedelta(seconds=1);run.save(update_fields=['expires_at'])
        with self.assertRaises(RunError):AgentRunService.decide(self.user,run.id,'confirm_all',run.action_ids)
        self.assertFalse(PendingAction.objects.filter(status='confirmed').exists())

    def test_failed_execution_status_persists(self):
        run=self.pause()
        with patch.object(ActionService,'_execute_send_message',side_effect=ActionError('Unavailable')):
            with self.assertRaises(ActionError):ActionService.confirm(self.user,run.action_ids[0])
        self.assertEqual(PendingAction.objects.get(pk=run.action_ids[0]).status,'failed')

    def test_resume_restores_server_request_and_persists_activity_without_new_user_turn(self):
        run=self.pause()
        gateway=Mock();gateway.generate.side_effect=[reply({'update':'Your approved actions are complete.','calls':[]}),LLMResponse(content='The message and follow are complete.',provider='mock',model='mock')]
        orchestrator=PwanimateOrchestrator(gateway=gateway)
        from pwanimate.api.views import PwanimateChatView
        with patch.object(PwanimateChatView,'orchestrator',orchestrator),patch.object(orchestrator,'_record_usage'),patch.object(ActionService,'_execute_send_message',return_value={'message_id':999}),patch.object(ActionService,'_execute_follow_user',return_value={'followed':True}):
            result=self.client.post('/api/pwanimate/chat/',{'resume_run_id':str(run.id),'decision':'confirm_all','action_ids':run.action_ids,'message':'IGNORED CLIENT PROMPT'},format='json')
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(result.data['answer'],'The message and follow are complete.')
        self.assertEqual(PwanimateMessage.objects.filter(conversation=self.conversation,role='user').count(),1)
        message=PwanimateMessage.objects.get(pk=result.data['message_id'])
        self.assertTrue(message.activity)
        run.refresh_from_db();self.assertEqual(run.status,'completed')
        self.assertIn(self.query,gateway.generate.call_args_list[0].args[0].messages[0].content)

    def test_model_clarification_is_public_and_short_circuits_tools(self):
        gateway=Mock();gateway.generate.return_value=reply({'update':'I can help schedule that.','question':'What time should I remind you?','calls':[],'reasoning':'PRIVATE PLACEHOLDER'})
        events=[]
        result=StudyAgent(gateway,get_default_tool_registry()).run(self.request,on_progress=events.append)
        self.assertEqual(result['clarification'],'What time should I remind you?')
        self.assertFalse(PendingAction.objects.exists())
        self.assertNotIn('PRIVATE PLACEHOLDER',json.dumps(events))

    def test_short_clarification_answer_can_complete_the_students_prior_request(self):
        self.turn.content='Remind me to review trees';self.turn.save(update_fields=['content'])
        PwanimateMessage.objects.create(conversation=self.conversation,role='assistant',content='When?',activity=[event('clarification','When?',status='waiting')])
        answer=PwanimateMessage.objects.create(conversation=self.conversation,role='user',content='Tomorrow at 5 pm')
        req=OrchestrationRequest(query=answer.content,user=self.user,user_message_id=answer.id)
        gateway=Mock();gateway.generate.side_effect=[reply({'calls':[{'tool':'create_reminder','arguments':{'title':'Review trees','due_at':(timezone.now()+timedelta(days=1)).isoformat(),'timezone':'UTC'}}]}),reply({'calls':[]})]
        result=StudyAgent(gateway,get_default_tool_registry()).run(req)
        self.assertTrue(result['trace'][0]['success'])

    def test_spark_identity_question_reaches_model_without_keyword_gate(self):
        gateway=Mock();gateway.generate.side_effect=[reply({'update':'I’ll check the official platform information.','calls':[{'tool':'site_info','arguments':{'topic':'founder'}}]}),reply({'calls':[]}),LLMResponse(content='The official About page credits Zayne Earthman.',provider='mock',model='mock')]
        orchestrator=PwanimateOrchestrator(gateway=gateway)
        with patch.object(orchestrator,'_record_usage'):
            result=orchestrator.run(OrchestrationRequest(query='Who built Spark?',user=self.user))
        self.assertEqual(result.metadata['agent_trace'][0]['tool'],'site_info')
        self.assertTrue(result.metadata['agent_trace'][0]['success'])


class ActivityStreamTests(SimpleTestCase):
    def test_activity_is_emitted_before_answer_tokens(self):
        from pwanimate.api.views import PwanimateChatView
        view=PwanimateChatView()
        def work(request,progress_callback=None,token_callback=None):
            progress_callback(event('plan','I’ll search the selected document.',status='completed'))
            progress_callback(event('tool','Searching document content',event_id='search'))
            token_callback('Here is the answer.')
            return Response({'answer':'Here is the answer.'})
        with patch.object(view,'_post_chat',side_effect=work),patch('pwanimate.api.views.close_old_connections'):
            response=view._stream_chat_response(SimpleNamespace())
            async def collect():
                return ''.join([chunk.decode() if isinstance(chunk,bytes) else chunk async for chunk in response.streaming_content])
            body=asyncio.run(collect())
        self.assertLess(body.index('event: activity'),body.index('event: chunk'))
        self.assertLess(body.index('event: chunk'),body.index('event: response'))
        self.assertIn('I’ll search',body.replace('\\u2019','’'))
