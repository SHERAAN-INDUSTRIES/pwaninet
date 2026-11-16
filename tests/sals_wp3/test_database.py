from django.contrib.auth import get_user_model
from django.test import TestCase,TransactionTestCase
from pwanimate.models import SparkMemoryPreference,SparkPersonalMemory,PwanimateConversation,PwanimateMessage
from pwanimate.services.personal_memory import PersonalMemoryService as M
from pwanimate.services.study_memory import StudyMemoryService

class MemoryConsentTests(TestCase):
    def setUp(self):
        self.a=get_user_model().objects.create_user(username='memory-owner')
        self.b=get_user_model().objects.create_user(username='memory-other')

    def test_legacy_default_off_and_fact_approval(self):
        SparkMemoryPreference.objects.create(user=self.a,enabled=True)
        self.assertEqual(M.recall(self.a,''),[])
        M.set_enabled(self.a,True)
        f=M.propose(self.a,'I prefer concise explanations')
        self.assertEqual(M.recall(self.a,''),[])
        M.change(self.a,f.pk,'approve',1)
        self.assertEqual(len(M.recall(self.a,'')),1)
        self.assertEqual(M.recall(self.b,''),[])
        with self.assertRaises(LookupError):M.change(self.b,f.pk,'delete',2)

    def test_edit_reject_revoke_disable(self):
        M.set_enabled(self.a,True);f=M.propose(self.a,'Original fact')
        M.change(self.a,f.pk,'approve',1)
        M.change(self.a,f.pk,'edit',2,'Changed fact')
        self.assertEqual(M.recall(self.a,''),[])
        M.change(self.a,f.pk,'reject',3)
        f.refresh_from_db();self.assertEqual(f.text,'')
        M.set_enabled(self.a,False);self.assertEqual(M.recall(self.a,''),[])

    def test_stale_consent_cannot_approve(self):
        M.set_enabled(self.a,True);f=M.propose(self.a,'Proposed before disable')
        M.set_enabled(self.a,False);M.set_enabled(self.a,True)
        with self.assertRaises(ValueError):M.change(self.a,f.pk,'approve',1)
        self.assertEqual(M.recall(self.a,''),[])

    def test_forget_preserves_transcript_and_blocks_old_proposal(self):
        c=PwanimateConversation.objects.create(user=self.a)
        message=PwanimateMessage.objects.create(conversation=c,role='user',content='Keep transcript')
        M.set_enabled(self.a,True);f=M.propose(self.a,'Forget fact')
        StudyMemoryService.forget(self.a)
        self.assertTrue(PwanimateMessage.objects.filter(pk=message.pk).exists())
        self.assertFalse(SparkPersonalMemory.objects.filter(user=self.a).exists())
        with self.assertRaises(LookupError):M.change(self.a,f.pk,'approve',1)

class ConcurrentConsentTests(TransactionTestCase):
    def test_disable_and_approve_serialize(self):
        import threading
        from django.db import close_old_connections
        user=get_user_model().objects.create_user(username='memory-race')
        M.set_enabled(user,True);fact=M.propose(user,'Race proposal')
        barrier=threading.Barrier(2);errors=[]
        def run(approve):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                if approve:
                    try:M.change(user,fact.pk,'approve',1)
                    except ValueError:pass # disable won; approval must fail
                else:M.set_enabled(user,False)
            except Exception as e:errors.append(e)
            finally:close_old_connections()
        threads=[threading.Thread(target=run,args=(a,)) for a in (True,False)]
        for t in threads:t.start()
        for t in threads:t.join(10)
        self.assertFalse(any(t.is_alive() for t in threads));self.assertEqual(errors,[])
        self.assertEqual(M.recall(user,''),[])

class ChatProposalTests(TestCase):
    def test_tool_proposes_once_and_never_approves(self):
        from pwanimate.tools.domain.memory import ProposePersonalMemoryTool
        user=get_user_model().objects.create_user(username='chat-memory')
        M.set_enabled(user,True)
        conversation=PwanimateConversation.objects.create(user=user)
        message=PwanimateMessage.objects.create(conversation=conversation,role='user',content='I prefer concise')
        tool=ProposePersonalMemoryTool()
        self.assertTrue(tool.execute(user,'concise',source_message_id=message.pk).data['proposed'])
        self.assertFalse(tool.execute(user,'concise',source_message_id=message.pk).data['proposed'])
        fact=SparkPersonalMemory.objects.get(user=user)
        self.assertEqual(fact.status,'proposed');self.assertEqual(M.recall(user,''),[])
        M.change(user,fact.pk,'approve',1)
        with self.assertRaises(ValueError):M.change(user,fact.pk,'approve',1)

    def test_tool_ignores_foreign_source_and_disabled_memory(self):
        from pwanimate.tools.domain.memory import ProposePersonalMemoryTool
        owner=get_user_model().objects.create_user(username='source-owner')
        other=get_user_model().objects.create_user(username='source-other')
        conversation=PwanimateConversation.objects.create(user=owner)
        message=PwanimateMessage.objects.create(conversation=conversation,role='user',content='I prefer detailed')
        M.set_enabled(other,True)
        self.assertFalse(ProposePersonalMemoryTool().execute(other,'detailed',source_message_id=message.pk).data['proposed'])
        self.assertFalse(ProposePersonalMemoryTool().execute(owner,'detailed',source_message_id=message.pk).data['proposed'])

    def test_explicit_combined_request_without_planner_and_memory_api(self):
        from pwanimate.tools.domain.memory import propose_from_message
        from pwanimate.api.study_assistant import MemoryView
        from rest_framework.test import APIRequestFactory,force_authenticate
        user=get_user_model().objects.create_user(username='explicit-memory')
        M.set_enabled(user,True)
        c=PwanimateConversation.objects.create(user=user)
        message=PwanimateMessage.objects.create(conversation=c,role='user',content='Spark, I prefer short explanations and bullet points.')
        self.assertEqual(propose_from_message(user,message)['status'],'proposed')
        facts=list(SparkPersonalMemory.objects.filter(user=user).order_by('created_at'))
        self.assertEqual(len(facts),2)
        self.assertTrue(all(f.status=='proposed' for f in facts));self.assertEqual(M.recall(user,''),[])
        # Same persisted request/retry cannot create duplicates.
        self.assertEqual(propose_from_message(user,message)['status'],'already_pending')
        self.assertEqual(SparkPersonalMemory.objects.filter(user=user).count(),2)
        request=APIRequestFactory().get('/api/pwanimate/memory/');force_authenticate(request,user=user)
        response=MemoryView.as_view()(request)
        self.assertEqual(response.status_code,200)
        self.assertTrue(all(f['conversation_id']==str(c.id) for f in response.data['facts']))
        for fact,action in zip(facts,['approve','reject']):
            request=APIRequestFactory().patch('/api/pwanimate/memory/',{'fact_id':str(fact.pk),'action':action,'version':1},format='json')
            force_authenticate(request,user=user)
            self.assertEqual(MemoryView.as_view()(request).status_code,200)
        self.assertEqual(len(M.recall(user,'')),1)
        self.assertEqual(propose_from_message(user,message)['status'],'rejected')
        self.assertEqual(SparkPersonalMemory.objects.filter(user=user).count(),2)
        other=PwanimateMessage.objects.create(conversation=c,role='user',content='I prefer detailed')
        self.assertEqual(propose_from_message(user,other)['status'],'rate_limited')


class InteractionLifecycleDatabaseTests(TestCase):
    def setUp(self):
        from pwanimate.models import SparkAgentRun
        from django.utils import timezone
        self.user=get_user_model().objects.create_user(username='interaction-owner')
        self.conversation=PwanimateConversation.objects.create(user=self.user)
        self.source=PwanimateMessage.objects.create(conversation=self.conversation,role='user',content='Teach matrices')
        self.run=SparkAgentRun.objects.create(user=self.user,user_message=self.source,snapshot={'query':self.source.content,'history':[],'user_message_id':self.source.pk},state={'observed':[],'seen':[]},status='waiting',wait_reason='clarification',expires_at=timezone.now()+timezone.timedelta(minutes=15))

    def test_group_contract_reused_and_resolves_original_run_once(self):
        from pwanimate.services.interactions import create_interaction
        from pwanimate.models import SparkAgentInteraction
        from rest_framework.test import APIClient
        from unittest.mock import patch
        interaction=create_interaction(self.run,questions=[{'id':'topic','question':'Which topic?'},{'id':'time','question':'How long?'}])
        replay=create_interaction(self.run,prompt='A reworded question')
        self.assertEqual(interaction.pk,replay.pk)
        self.assertEqual(SparkAgentInteraction.objects.filter(run=self.run,status='pending').count(),1)
        client=APIClient();client.force_authenticate(self.user)
        body={'resume_run_id':str(self.run.pk),'interaction_id':str(interaction.pk),'answer':{'answers':{'topic':{'text':'Determinants'}}}}
        self.assertEqual(client.post('/api/pwanimate/chat/',body,format='json').status_code,400)
        self.run.refresh_from_db();self.assertEqual(self.run.status,'waiting')
        body['answer']['answers']['time']={'text':'20 minutes'}
        with patch('pwanimate.services.agent_execution.execute_claimed',return_value={'answer':'Continuing','run_id':str(self.run.pk)}) as execute:
            response=client.post('/api/pwanimate/chat/',body,format='json')
            self.assertEqual(response.status_code,200)
            self.assertEqual(execute.call_args.args[0].pk,self.run.pk)
            self.assertEqual(client.post('/api/pwanimate/chat/',body,format='json').status_code,409)
            self.assertEqual(execute.call_count,1)
        interaction.refresh_from_db();self.run.refresh_from_db()
        self.assertEqual(interaction.status,'answered');self.assertEqual(self.run.status,'running')
        self.assertEqual(self.run.snapshot['query'],self.source.content)
        self.assertIn('Determinants',self.run.continuation['query_suffix'])
        self.assertEqual(PwanimateMessage.objects.filter(conversation=self.conversation,role='user').count(),2)
