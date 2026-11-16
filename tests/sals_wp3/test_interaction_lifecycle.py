"""Isolated interaction contracts: no live provider, database or worker."""
import asyncio
import json
from types import SimpleNamespace as NS
from contextlib import nullcontext
from unittest.mock import Mock,patch
from django.test import SimpleTestCase
from django.utils import timezone
from pwanimate.models import SparkAgentRun,SparkAgentInteraction as I
from pwanimate.services.interactions import (normalize_group_questions,question_key,
    is_memory_consent_question,create_interaction,_validate_answer,InteractionError,
    extract_inline_choices)

class InteractionLifecycleTests(SimpleTestCase):
    def test_group_types_ids_and_duplicate_prompts(self):
        questions=normalize_group_questions([
            {'id':'a','question':'Which topic?','question_type':'single','question_options':[{'id':'x','label':'Matrices'}]},
            {'id':'b','question':' Which TOPIC! ','question_type':'text'},
            {'id':'a','question':'How long?','question_type':'multi','question_options':[{'id':'y','label':'Today'}]},
        ])
        self.assertEqual(len(questions),2)
        self.assertEqual([q['input_type'] for q in questions],[I.INPUT_SINGLE,I.INPUT_MULTI])
        self.assertNotEqual(questions[0]['id'],questions[1]['id'])

    def test_memory_cards_are_not_generic_confirmation_questions(self):
        for text in ['Do you approve saving your preference for short explanations?',
                     'Would you like me to save your preference to long-term memory?']:
            self.assertTrue(is_memory_consent_question(text))
            self.assertEqual(normalize_group_questions([{'question':text}]),[])
        self.assertFalse(is_memory_consent_question('Would you like an explanation of virtual memory?'))
        self.assertEqual(question_key('Which TOPIC?'),question_key('which topic!'))

    def test_group_requires_all_answers_and_preserves_id_order(self):
        questions=normalize_group_questions([{'id':'a','question':'A?'},{'id':'b','question':'B?'}])
        interaction=NS(input_type='group',options=questions)
        with self.assertRaises(InteractionError):_validate_answer(interaction,{'answers':{'a':{'text':'first'}}})
        answer=_validate_answer(interaction,{'answers':{'b':{'text':'second'},'a':{'text':'first'}}})
        self.assertEqual(list(answer['answers']),['a','b'])
        self.assertEqual(answer['text'],'A?: first; B?: second')

    def test_pending_contract_is_reused_without_overwriting_drafts(self):
        owner=NS(pk=1);run=NS(pk='run',user_id=1,user=owner,status='waiting',expires_at=timezone.now()+timezone.timedelta(minutes=10))
        old=NS(pk='persisted',version=7)
        with patch('pwanimate.services.interactions.transaction.atomic',side_effect=nullcontext),patch('pwanimate.services.interactions.SparkAgentRun.objects.select_for_update') as locked,patch('pwanimate.services.interactions.pending_for_run',return_value=old),patch('pwanimate.services.interactions.SparkAgentInteraction.objects.create') as create:
            locked.return_value.get.return_value=run
            self.assertIs(create_interaction(run,prompt='Different wording',user=owner),old)
            create.assert_not_called()

    def test_one_question_in_array_becomes_one_typed_contract(self):
        owner=NS(pk=1);run=NS(pk='run',user_id=1,user=owner,status='waiting',expires_at=timezone.now()+timezone.timedelta(minutes=10),user_message=NS(conversation='conversation'))
        with patch('pwanimate.services.interactions.transaction.atomic',side_effect=nullcontext),patch('pwanimate.services.interactions.SparkAgentRun.objects.select_for_update') as locked,patch('pwanimate.services.interactions.pending_for_run',return_value=None),patch('pwanimate.services.interactions.answered_questions',return_value=[]),patch('pwanimate.services.interactions.SparkAgentInteraction.objects.create') as create:
            locked.return_value.get.return_value=run
            create_interaction(run,questions=[{'id':'a','question':'Choose','question_type':'single','question_options':[{'id':'x','label':'X'}]}])
            self.assertEqual(create.call_args.kwargs['input_type'],'single_choice')
            self.assertEqual(create.call_args.kwargs['prompt'],'Choose')

    def test_repeated_answered_question_cannot_create_another_contract(self):
        owner=NS(pk=1);run=NS(pk='run',user_id=1,user=owner,status='waiting',expires_at=timezone.now()+timezone.timedelta(minutes=10))
        with patch('pwanimate.services.interactions.transaction.atomic',side_effect=nullcontext),patch('pwanimate.services.interactions.SparkAgentRun.objects.select_for_update') as locked,patch('pwanimate.services.interactions.pending_for_run',return_value=None),patch('pwanimate.services.interactions.answered_questions',return_value=['Which topic?']),patch('pwanimate.services.interactions.SparkAgentInteraction.objects.create') as create:
            locked.return_value.get.return_value=run
            with self.assertRaises(InteractionError):create_interaction(run,prompt='Which TOPIC!',user=owner)
            create.assert_not_called()

    def test_sse_post_replay_only_attaches_never_rearms_or_dispatches(self):
        from pwanimate.api.agent_runs import _claim_and_tail
        user=NS(pk=1);run=NS(id='run');interaction=NS(id='question')
        with patch('pwanimate.api.agent_runs._accepted_submission',return_value=run),patch('pwanimate.api.agent_runs._stream_run_events',return_value='stream') as stream,patch('pwanimate.services.agent_execution.claim_run') as claim,patch('pwanimate.api.agent_runs._arm_attempt') as arm,patch('pwanimate.tasks.agent_runs.dispatch_run') as dispatch:
            self.assertEqual(_claim_and_tail(NS(user=user),run,{},interaction=interaction,raw_answer={'text':'A'},legacy_text='',via='embedded'),'stream')
            claim.assert_not_called();arm.assert_not_called();dispatch.assert_not_called()
            stream.assert_called_once_with(user,'run',accepted_interaction_id='question')

    def test_sse_acceptance_precedes_progress_or_completion(self):
        from pwanimate.api.agent_runs import _stream_run_events
        response=_stream_run_events(NS(pk=1),'run',accepted_interaction_id='question')
        async def first():
            iterator=response.streaming_content.__aiter__()
            chunk=await iterator.__anext__()
            return chunk.decode() if isinstance(chunk,bytes) else chunk
        text=asyncio.run(first())
        self.assertIn('event: interaction_accepted',text);self.assertIn('question',text)

    def test_repaused_result_pointer_does_not_become_a_final_answer(self):
        from pwanimate.api.agent_runs import _terminal_payload
        run=NS(id='run',status='waiting',activity=[],wait_reason='clarification',result_message_id=99)
        with patch('pwanimate.api.agent_runs.AgentRunService.serialize',return_value={'interaction':{'interaction_id':'next'}}),patch('pwanimate.api.agent_runs.PwanimateMessage.objects.filter',side_effect=AssertionError('Stale paused answer read')):
            payload=_terminal_payload(run)
            self.assertTrue(payload['metadata']['awaiting_clarification'])
            self.assertEqual(payload['interaction']['interaction_id'],'next');self.assertNotIn('answer',payload)

    def test_planner_continues_instead_of_reasking_answered_or_memory_consent(self):
        from pwanimate.orchestrator.agent import StudyAgent
        from pwanimate.orchestrator.types import OrchestrationRequest
        from pwanimate.ai.gateway.types import LLMResponse
        registry=Mock();registry.list_tools.return_value=[]
        for question,answered in [('Which topic?', ['Which topic!']),('Do you approve saving your preference?',[])]:
            gateway=Mock();gateway.generate.return_value=LLMResponse(content=json.dumps({'question':question,'calls':[]}),provider='mock',model='mock')
            request=OrchestrationRequest(query='Teach matrices.\nStudent clarification: determinants',user=NS(pk=1,is_authenticated=True))
            with patch('pwanimate.services.interactions.answered_questions',return_value=answered),patch('pwanimate.services.agent_runs.AgentRunService.pause') as pause:
                result=StudyAgent(gateway,registry).run(request,resume_state={'rounds':0},run=NS(pk='run'))
                pause.assert_not_called();self.assertFalse(result['clarification'])
            sent=json.loads(gateway.generate.call_args.args[0].messages[0].content)
            self.assertEqual(sent['answered_questions'],answered)

    def test_paused_response_is_not_a_second_ordinary_assistant_answer(self):
        from pwanimate.orchestrator.types import OrchestrationResponse
        response=OrchestrationResponse(answer='Repeated question?',metadata={'awaiting_clarification':True})
        self.assertEqual(response.to_dict()['answer'],'')
        self.assertEqual(response.answer,'Repeated question?') # durable historical record retained

    def test_conflicting_replay_cannot_attach_as_the_accepted_answer(self):
        from pwanimate.api.agent_runs import _accepted_submission
        user=NS(pk=1);run=NS(pk='run')
        answered=I(input_type='text',answer={'text':'Original','selected':[],'custom':False})
        interaction=NS(pk='question')
        with patch('pwanimate.api.agent_runs.SparkAgentRun.objects.filter') as runs,patch('pwanimate.api.agent_runs.SparkAgentInteraction.objects.filter') as facts:
            runs.return_value.first.return_value=run;facts.return_value.first.return_value=answered
            self.assertIs(_accepted_submission(user,run,interaction,{'text':'Original'}),run)
            self.assertIsNone(_accepted_submission(user,run,interaction,{'text':'Changed'}))
            facts.assert_called_with(pk='question',run=run,user=user,status='answered')

    def test_mismatched_run_and_interaction_are_rejected(self):
        from pwanimate.api.agent_runs import _prepare_submission
        import uuid
        run=NS(pk=uuid.uuid4());interaction=NS(run=run)
        with patch('pwanimate.api.agent_runs.SparkAgentInteraction.objects.filter') as read:
            read.return_value.select_related.return_value.first.return_value=interaction
            with self.assertRaises(InteractionError):
                _prepare_submission(NS(user=NS(pk=1)),{'interaction_id':str(uuid.uuid4()),'resume_run_id':str(uuid.uuid4()),'answer':{'text':'Answer'}})

    def test_duplicate_worker_delivery_does_not_execute_again(self):
        from pwanimate.tasks.agent_runs import execute_agent_run
        run=NS(pk='run',attempt_id='attempt',status='running')
        with patch('pwanimate.models.SparkAgentRun.objects.filter') as runs,patch('pwanimate.services.agent_journal.append',return_value=None),patch('pwanimate.services.agent_execution.execute_claimed') as execute:
            runs.return_value.first.return_value=run
            self.assertEqual(execute_agent_run.run('run','attempt')['status'],'duplicate')
            execute.assert_not_called()

    def test_complete_question_group_wins_over_duplicate_single_field(self):
        from pwanimate.orchestrator.agent import StudyAgent
        from pwanimate.orchestrator.types import OrchestrationRequest
        from pwanimate.ai.gateway.types import LLMResponse
        import uuid
        registry=Mock();registry.list_tools.return_value=[]
        questions=[{'id':'topic','question':'Which topic?'},{'id':'time','question':'How long?'}]
        gateway=Mock();gateway.generate.return_value=LLMResponse(content=json.dumps({'question':'Which topic?','questions':questions,'calls':[]}),provider='mock',model='mock')
        run=NS(pk=uuid.uuid4(),id=uuid.uuid4(),user_message=NS(conversation='conversation'))
        persisted=I(prompt='Spark has a few questions for you.',input_type='group',options=normalize_group_questions(questions))
        request=OrchestrationRequest(query='Teach me matrices',user=NS(pk=1,is_authenticated=True),user_message_id=1)
        events=[]
        with patch('pwanimate.services.agent_runs.AgentRunService.pause',return_value=run),patch('pwanimate.services.interactions.create_interaction',return_value=persisted) as create,patch('pwanimate.services.agent_plans.sync_steps'):
            result=StudyAgent(gateway,registry).run(request,on_progress=events.append)
        self.assertEqual(len(create.call_args.kwargs['questions']),2)
        self.assertEqual(events[-1]['input_type'],'group')
        self.assertEqual([q['id'] for q in events[-1]['questions']],['topic','time'])
        self.assertEqual(result['run_id'],str(run.id))

    def test_inline_choice_prose_becomes_tappable_options(self):
        prompt,options=extract_inline_choices('Which is correct?\nA) det = 0\nB) det = 1\nC) det = -1')
        self.assertEqual(prompt,'Which is correct?')
        self.assertEqual([o['label'] for o in options],['det = 0','det = 1','det = -1'])
        self.assertEqual(len({o['id'] for o in options}),3)
        numbered_prompt,numbered=extract_inline_choices('What is 2+2?\n1. 3\n2. 4\n3. 5')
        self.assertEqual(numbered_prompt,'What is 2+2?')
        self.assertEqual(len(numbered),3)

    def test_inline_extraction_ignores_midtext_numbered_steps(self):
        text='Explain row reduction.\nStep 1. Write the matrix.\nStep 2. Eliminate below.'
        prompt,options=extract_inline_choices(text)
        self.assertEqual((prompt,options),(text,[]))
        self.assertEqual(extract_inline_choices('Just explain determinants please.'),( 'Just explain determinants please.',[]))

    def test_group_question_with_embedded_choices_upgrades_to_single_choice(self):
        questions=normalize_group_questions([{'question':'Pick one\nA) alpha\nB) beta','question_type':'text'}])
        self.assertEqual(len(questions),1)
        self.assertEqual(questions[0]['input_type'],I.INPUT_SINGLE)
        self.assertEqual(questions[0]['prompt'],'Pick one')
        self.assertEqual([o['label'] for o in questions[0]['options']],['alpha','beta'])

    def test_planner_prose_choices_become_single_choice_interaction(self):
        from pwanimate.orchestrator.agent import StudyAgent
        from pwanimate.orchestrator.types import OrchestrationRequest
        from pwanimate.ai.gateway.types import LLMResponse
        import uuid
        registry=Mock();registry.list_tools.return_value=[]
        question='What is det of a triangular matrix?\nA) Product of diagonal\nB) Always zero\nC) Sum of diagonal'
        gateway=Mock();gateway.generate.return_value=LLMResponse(content=json.dumps({'question':question,'calls':[]}),provider='mock',model='mock')
        run=NS(pk=uuid.uuid4(),id=uuid.uuid4(),user_message=NS(conversation='conversation'))
        persisted=I(prompt='What is det of a triangular matrix?',input_type='single_choice',
            options=[{'id':'a','label':'Product of diagonal'},{'id':'b','label':'Always zero'},{'id':'c','label':'Sum of diagonal'}])
        request=OrchestrationRequest(query='Quiz me on determinants',user=NS(pk=1,is_authenticated=True))
        with patch('pwanimate.services.agent_runs.AgentRunService.pause',return_value=run),patch('pwanimate.services.interactions.create_interaction',return_value=persisted) as create:
            result=StudyAgent(gateway,registry).run(request)
        kwargs=create.call_args.kwargs
        self.assertEqual(kwargs['input_type'],'single_choice')
        self.assertEqual(kwargs['prompt'],'What is det of a triangular matrix?')
        self.assertEqual([o['label'] for o in kwargs['options']],['Product of diagonal','Always zero','Sum of diagonal'])
        self.assertEqual(result['clarification'],'What is det of a triangular matrix?')

    def test_options_without_question_type_still_become_single_choice(self):
        from pwanimate.orchestrator.agent import StudyAgent
        from pwanimate.orchestrator.types import OrchestrationRequest
        from pwanimate.ai.gateway.types import LLMResponse
        import uuid
        registry=Mock();registry.list_tools.return_value=[]
        gateway=Mock();gateway.generate.return_value=LLMResponse(content=json.dumps({
            'question':'Pick the correct property',
            'question_options':['Determinant is basis-independent','Trace equals determinant'],
            'calls':[]}),provider='mock',model='mock')
        run=NS(pk=uuid.uuid4(),id=uuid.uuid4(),user_message=NS(conversation='conversation'))
        request=OrchestrationRequest(query='Quiz me',user=NS(pk=1,is_authenticated=True))
        with patch('pwanimate.services.agent_runs.AgentRunService.pause',return_value=run),patch('pwanimate.services.interactions.create_interaction') as create:
            StudyAgent(gateway,registry).run(request)
        kwargs=create.call_args.kwargs
        self.assertEqual(kwargs['input_type'],'single_choice')
        self.assertEqual(len(kwargs['options']),2)

    def test_resumed_run_can_emit_next_adaptive_question(self):
        from pwanimate.orchestrator.agent import StudyAgent
        from pwanimate.orchestrator.types import OrchestrationRequest
        from pwanimate.ai.gateway.types import LLMResponse
        import uuid
        registry=Mock();registry.list_tools.return_value=[]
        # Round 1 on the resumed run asks the next check with structured options.
        gateway=Mock();gateway.generate.return_value=LLMResponse(content=json.dumps({
            'question':'If det(A)=0, which is true?',
            'question_type':'single',
            'question_options':[{'id':'singular','label':'A is singular'},{'id':'invertible','label':'A is invertible'}],
            'calls':[]}),provider='mock',model='mock')
        run=NS(pk=uuid.uuid4(),id=uuid.uuid4(),user_message=NS(conversation='conversation'))
        persisted=I(prompt='If det(A)=0, which is true?',input_type='single_choice',
            options=[{'id':'singular','label':'A is singular'},{'id':'invertible','label':'A is invertible'}])
        request=OrchestrationRequest(query='Quiz me on determinants\n\nStudent clarification: Product of diagonal',
            user=NS(pk=1,is_authenticated=True))
        with patch('pwanimate.services.agent_runs.AgentRunService.pause',return_value=run),patch('pwanimate.services.interactions.create_interaction',return_value=persisted) as create,patch('pwanimate.services.interactions.answered_questions',return_value=['What is det of a triangular matrix?']):
            result=StudyAgent(gateway,registry).run(request,resume_state={'rounds':0,'observed':[],'seen':[],'items':[]},run=run)
        kwargs=create.call_args.kwargs
        self.assertEqual(kwargs['input_type'],'single_choice')
        self.assertEqual(kwargs['prompt'],'If det(A)=0, which is true?')
        self.assertEqual(result['run_id'],str(run.id))

    def test_question_identity_preserves_mathematical_meaning(self):
        self.assertNotEqual(question_key('Compute $1+2$?'),question_key('Compute $1-2$?'))
        self.assertNotEqual(question_key('Compute $A$?'),question_key('Compute $a$?'))
        self.assertNotEqual(question_key('Compute x²?'),question_key('Compute x2?'))
