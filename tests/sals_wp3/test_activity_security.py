"""Offline public-boundary regressions; no database or provider transport."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from pwanimate.orchestrator.activity import event, public_event, public_events
from pwanimate.orchestrator.types import OrchestrationResponse
from pwanimate.ai.gateway.types import LLMRequest, ChatMessage
from pwanimate.ai.exceptions import AIProviderAPIError
from pwanimate.ai.providers.gemini import GeminiLLMProvider
from pwanimate.ai.providers.groq import GroqLLMProvider
from pwanimate.ai.providers.openrouter import OpenRouterLLMProvider

SECRET = 'PRIVATE PROMPT password=secret'

class PublicActivityTests(SimpleTestCase):
    def test_narration_never_emits(self):
        from pwanimate.orchestrator.activity import emit
        callback=Mock()
        for kind in ('thinking','reasoning','plan','say','prompt'):
            self.assertIsNone(emit(callback,kind,SECRET))
        callback.assert_not_called()

    def test_tool_args_and_checklist_findings_are_not_public(self):
        tool=event('tool',SECRET,tool='web_search',detail=SECRET,arguments={'query':SECRET},summary=SECRET)
        self.assertNotIn(SECRET,str(tool));self.assertEqual(tool['text'],'Searching the web')
        checklist=event('checklist',SECRET,items=[{'label':SECRET,'finding':SECRET,'status':'done'}])
        self.assertNotIn(SECRET,str(checklist));self.assertEqual(checklist['items'][0]['status'],'done')

    def test_legacy_replay_deduplicates_timestamp_churn_and_keeps_outcomes(self):
        original={'type':'activity','id':'stage-writing_answer','kind':'stage','text':SECRET,'at':'old'}
        values=public_events([original,{**original,'at':'new'},event('thinking',SECRET),
                             event('tool',SECRET,tool='web_search',event_id='tool-1'),
                             event('tool',SECRET,tool='web_search',event_id='tool-1',status='completed')])
        self.assertEqual(len(values),3);self.assertNotIn(SECRET,str(values))
        self.assertEqual(public_events(values),values)

    def test_history_and_terminal_replay_apply_same_projection(self):
        from pwanimate.api.serializers import MessageSerializer
        from pwanimate.services.agent_journal import serialize_event
        from pwanimate.api.agent_runs import _journal_event_sse, _terminal_payload
        from pwanimate.models import SparkAgentRun
        from datetime import datetime, timezone
        raw={'type':'activity','kind':'thinking','id':'legacy','text':SECRET}
        self.assertEqual(MessageSerializer().get_activity(SimpleNamespace(activity=[raw])),[])
        entry=SimpleNamespace(seq=1,type='activity',payload=raw,created_at=datetime.now(timezone.utc))
        self.assertIsNone(serialize_event(entry)['payload'])
        self.assertNotIn(SECRET,_journal_event_sse(entry))
        run=SimpleNamespace(id='run',status=SparkAgentRun.STATUS_CANCELLED,activity=[raw])
        self.assertEqual(_terminal_payload(run)['activity'],[])

    def test_response_metadata_has_no_internal_traces(self):
        response=OrchestrationResponse(answer='Visible answer',metadata={
            'agent_trace':SECRET,'memory_trace':SECRET,'retrieval_trace':SECRET,
            'reasoning':SECRET,'system_instruction':SECRET,'awaiting_approval':False})
        payload=response.to_dict()
        self.assertNotIn(SECRET,str(payload));self.assertFalse(payload['metadata']['awaiting_approval'])

    def test_gemini_does_not_join_thought_parts_into_answer(self):
        provider=GeminiLLMProvider(api_key='offline-test')
        req=LLMRequest(task='chat',messages=[ChatMessage(role='user',content='Hello')])
        response=provider._parse_response({'candidates':[{'content':{'parts':[
            {'text':SECRET,'thought':True},{'text':'Visible answer'}]},'finishReason':'STOP'}]},req,'offline')
        self.assertEqual(response.content,'Visible answer')

    def test_reasoning_only_provider_responses_fail_closed(self):
        req=LLMRequest(task='chat',messages=[ChatMessage(role='user',content='Hello')])
        for cls in (GroqLLMProvider,OpenRouterLLMProvider):
            provider=cls(api_key='offline-test')
            data={'choices':[{'message':{'content':'','reasoning':SECRET},'finish_reason':'stop'}]}
            with self.assertRaises(AIProviderAPIError): provider._parse_response(data,req,'offline')
            data['choices'][0]['message']['content']='Visible answer'
            response=provider._parse_response(data,req,'offline')
            self.assertEqual(response.content,'Visible answer');self.assertNotIn(SECRET,str(response.metadata))

    def test_streaming_provider_thoughts_never_reach_token_callback(self):
        from pwanimate.tests.test_streaming import FakeStreamResponse, _sse_lines, _stream_request
        for cls in (GeminiLLMProvider,GroqLLMProvider,OpenRouterLLMProvider):
            provider=cls(api_key='offline-test')
            if cls is GeminiLLMProvider:
                chunks=_sse_lines({'candidates':[{'content':{'parts':[{'text':SECRET,'thought':True},{'text':'Visible'}]},'finishReason':'STOP'}]})
            else:
                chunks=_sse_lines({'choices':[{'delta':{'reasoning':SECRET},'finish_reason':None}]},
                                  {'choices':[{'delta':{'content':'Visible'},'finish_reason':'stop'}]},'[DONE]')
            tokens=[]
            with patch.object(provider,'session') as session:
                session.post.return_value=FakeStreamResponse(chunks)
                response=provider.generate_stream(_stream_request(),tokens.append)
            self.assertEqual(tokens,['Visible']);self.assertEqual(response.content,'Visible')
            self.assertNotIn(SECRET,str(response.metadata))

    def test_malformed_legacy_activity_fails_closed(self):
        self.assertIsNone(public_event({'type':'activity','kind':[]}))
        self.assertEqual(public_events(42),[])
        self.assertEqual(public_event({'type':'activity','kind':'checklist','items':42})['items'],[])
        value=public_event({'type':'activity','kind':'tool','tool':{},'status':[],'activity_type':{}})
        self.assertEqual(value['text'],'Checking the requested information')
