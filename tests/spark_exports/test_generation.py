import io
import os
import tempfile
from pathlib import Path
from unittest import skipUnless
from unittest.mock import Mock, patch
from django.test import TestCase, SimpleTestCase, override_settings
from django.contrib.auth import get_user_model
from django.core.files.storage import FileSystemStorage
from rest_framework.test import APIClient
from pwanimate.models import PwanimateConversation, PwanimateMessage, SparkAgentRun
from pwanimate.ai.gateway import LLMResponse
from pwanimate.orchestrator.service import PwanimateOrchestrator
from pwanimate.services.document_exports import requested_formats, publish_requested_documents, saved_resources
from pwanimate.services.generated_documents import create_generated_resource
from pwanimate.orchestrator.types import OrchestrationRequest, OrchestrationResponse

CONTENT = '# Java revision notes\n\n## Polymorphism\n\nA superclass reference can refer to a subclass instance.\n\n- Dynamic dispatch selects the overridden method.\n- Overloading is resolved from declared signatures.\n\n## Practice\n\nExplain which method is called and why.\n'


class IntentTests(SimpleTestCase):
    def test_pptx_verbose_tables_and_source_lists_preserve_content(self):
        from types import SimpleNamespace
        from pptx import Presentation
        from pwanimate.services.export_formats import render_pptx
        message = SimpleNamespace(content='# Revision\n\n| A | B | C | D | E | F |\n|---|---|---|---|---|---|\n| FirstCell | b | c | d | e | FinalCell |\n',
            sources=[{'title':f'Source {i}', 'url':f'https://example.invalid/source-{i}'} for i in range(35)], citations=[])
        deck = Presentation(io.BytesIO(render_pptx('Revision', message)))
        text = '\n'.join(shape.text for slide in deck.slides for shape in slide.shapes if shape.has_text_frame)
        self.assertIn('A: FirstCell', text)
        self.assertIn('F: FinalCell', text)
        self.assertIn('https://example.invalid/source-34', text)

    def test_explicit_formats_and_input_pdf(self):
        for text, expected in [
            ('Generate a PDF explaining polymorphism.', ['pdf']),
            ('Summarize my Java PDF.', []),
            ('Summarize my Java PDF and give me a DOCX.', ['docx']),
            ('Prepare revision notes as PDF and DOCX.', ['pdf','docx']),
            ('Create a DOC file.', ['doc']), ('Generate a PPTX presentation.', ['pptx']),
            ('Create a Word document.', ['docx']), ('Create a slide deck.', ['pptx']),
            ('Do not generate a PDF.', []), ('How do I generate a PDF?', []),
        ]:
            with self.subTest(text=text): self.assertEqual(requested_formats(text), expected)


class PrivateTestStorage(FileSystemStorage):
    uses_s3 = False


@override_settings(ROOT_URLCONF='tests.spark_exports.urls', STATICFILES_DIRS=[Path(__file__).resolve().parents[2] / 'static'])
class GenerationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='export-owner')
        self.other = get_user_model().objects.create_user(username='export-other')
        self.conv = PwanimateConversation.objects.create(user=self.user)
        self.source = PwanimateMessage.objects.create(conversation=self.conv, role='user', content='Create revision notes as PDF and DOCX and DOC and PPTX.')
        self.message = PwanimateMessage.objects.create(conversation=self.conv, role='assistant', content=CONTENT)
        self.temp = tempfile.TemporaryDirectory(prefix='spark-test-exports-')
        self.addCleanup(self.temp.cleanup)
        self.storage = PrivateTestStorage(location=self.temp.name)
        self.patch = patch('pwanimate.services.generated_documents.get_private_resource_storage', return_value=self.storage)
        self.patch.start(); self.addCleanup(self.patch.stop)
        storage_patch = patch('documents.private_storage.get_private_resource_storage', return_value=self.storage)
        storage_patch.start(); self.addCleanup(storage_patch.stop)

    @skipUnless(os.environ.get('SPARK_NATIVE_EXPORTS'), 'Requires native Chromium and LibreOffice IPC')
    def test_native_math_all_formats(self):
        import zipfile
        self.message.content += '\n## Mathematics\n\n$$\\det(A) = ad - bc$$\n'
        self.message.save(update_fields=['content'])
        for fmt in ['pdf', 'docx', 'doc', 'pptx']:
            with self.subTest(format=fmt):
                _, file, _ = create_generated_resource(self.message, self.user, fmt)
                with self.storage.open(file.file.name, 'rb') as stream:
                    data = stream.read()
                self.assertGreater(len(data), 1000)
                if fmt == 'pdf':
                    self.assertTrue(data.startswith(b'%PDF'))
                elif fmt == 'doc':
                    self.assertTrue(data.startswith(bytes.fromhex('d0cf11e0a1b11ae1')))
                else:
                    archive = zipfile.ZipFile(io.BytesIO(data))
                    self.assertTrue(any('/media/' in name for name in archive.namelist()))
                    if fmt == 'pptx':
                        notes = b''.join(archive.read(name) for name in archive.namelist()
                            if name.startswith('ppt/notesSlides/notesSlide') and name.endswith('.xml'))
                        self.assertIn(b'Equation source:', notes)

    def test_all_formats_are_actual_private_files_and_replay_same_identity(self):
        native_doc = patch('pwanimate.services.generated_documents.render_doc', return_value=bytes.fromhex('d0cf11e0a1b11ae1') + b'synthetic') if not os.environ.get('SPARK_NATIVE_EXPORTS') else patch('pwanimate.services.generated_documents.render_doc', wraps=__import__('pwanimate.services.export_formats',fromlist=['render_doc']).render_doc)
        with native_doc:
            self.check_all_formats()

    def check_all_formats(self):
        for fmt in ['docx','pptx','doc','pdf']:
            with self.subTest(format=fmt), patch('pwanimate.services.generated_documents._render_pdf', return_value=b'%PDF-1.7\nmock'):
                document, file, created = create_generated_resource(self.message, self.user, fmt)
                self.assertTrue(created)
                self.assertTrue(file.storage_private)
                self.assertEqual(document.visibility, 'private')
                self.assertEqual(file.extension, fmt)
                with self.storage.open(file.file.name, 'rb') as stream: data = stream.read()
                self.assertTrue(data.startswith(b'%PDF') if fmt == 'pdf' else data.startswith(bytes.fromhex('d0cf11e0a1b11ae1')) if fmt == 'doc' else data.startswith(b'PK'))
                again, same_file, created = create_generated_resource(self.message, self.user, fmt)
                self.assertFalse(created)
                self.assertEqual((again.pk,same_file.pk), (document.pk,file.pk))

    def test_foreign_message_cannot_be_exported(self):
        with self.assertRaises(ValueError): create_generated_resource(self.message, self.other, 'docx')

    def test_edit_and_renderer_refresh_update_existing_document(self):
        first, file, _ = create_generated_resource(self.message, self.user, 'docx')
        self.message.content += '\nA new paragraph.'
        self.message.save(update_fields=['content'])
        again, newer, created = create_generated_resource(self.message, self.user, 'docx')
        self.assertTrue(created)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(file.pk, newer.pk)
        self.assertNotEqual(file.checksum, newer.checksum)

    def test_partial_or_waiting_answer_never_produces_file(self):
        request = OrchestrationRequest(query=self.source.content, user=self.user, user_message_id=self.source.pk)
        for finish, meta in [('length',{}), ('stop',{'awaiting_input':True})]:
            response = OrchestrationResponse(answer=CONTENT, finish_reason=finish, metadata=meta)
            with patch('pwanimate.tools.domain.exports.GenerateDocumentTool.execute') as tool:
                self.assertEqual(publish_requested_documents(request,response,self.message), [])
                tool.assert_not_called()

    def test_complete_response_gets_signed_links_and_reload(self):
        self.source.content = 'Create a DOCX document.'; self.source.save(update_fields=['content'])
        request = OrchestrationRequest(query=self.source.content, user=self.user, user_message_id=self.source.pk)
        response = OrchestrationResponse(answer=CONTENT, finish_reason='stop')
        files = publish_requested_documents(request,response,self.message)
        self.assertEqual(len(files),1)
        self.assertIn('?t=',files[0]['download_url'])
        self.assertEqual(saved_resources(self.user,[self.message.pk])[self.message.pk][0]['document_id'],files[0]['document_id'])
        self.assertEqual(saved_resources(self.other,[self.message.pk]),{})

    def test_chat_request_creates_durable_file_and_real_link(self):
        from pwanimate.api.views import PwanimateChatView
        from pwanimate.services.conversation import ConversationService
        gateway = Mock()
        gateway.generate.side_effect = [
            LLMResponse(content='{"calls":[]}',provider='mock',model='mock',finish_reason='stop'),
            LLMResponse(content=CONTENT,provider='mock',model='mock',finish_reason='stop')]
        service = PwanimateOrchestrator(gateway=gateway)
        client = APIClient(); client.force_authenticate(self.user)
        with patch.object(PwanimateChatView,'get_orchestrator',return_value=service), patch.object(service,'_record_usage'), \
             patch.object(ConversationService,'schedule_embedding'), patch.object(ConversationService,'schedule_digest'):
            result = client.post('/api/pwanimate/chat/',{'conversation_id':str(self.conv.pk), 'message':'Create a DOCX document explaining polymorphism.'},format='json')
        self.assertEqual(result.status_code,200,result.data)
        self.assertTrue(result.data['metadata']['artifacts_complete'], result.data)
        resource = result.data['metadata']['generated_documents'][0]
        self.assertEqual(resource['format'],'docx')
        self.assertIn('/download/?t=',resource['download_url'])
        run = SparkAgentRun.objects.get(pk=result.data['run_id'])
        self.assertEqual(run.status,'completed')
        self.assertEqual(run.result_message_id,result.data['message_id'])
        client.force_login(self.user)
        downloaded = client.get(resource['download_url'])
        self.assertEqual(downloaded.status_code,200)
        self.assertEqual(downloaded['Cache-Control'],'private, no-store')
        self.assertTrue(b''.join(downloaded.streaming_content).startswith(b'PK'))
        other_client = APIClient(); other_client.force_login(self.other)
        self.assertEqual(other_client.get(resource['download_url']).status_code,404)

    def test_conversion_failure_is_reported_without_fake_link(self):
        self.source.content='Create a DOC file.'; self.source.save(update_fields=['content'])
        request=OrchestrationRequest(query=self.source.content,user=self.user,user_message_id=self.source.pk)
        response=OrchestrationResponse(answer=CONTENT,finish_reason='stop')
        with patch('pwanimate.services.generated_documents.render_doc',side_effect=RuntimeError('not available')):
            files=publish_requested_documents(request,response,self.message)
        self.assertEqual(files,[])
        self.assertFalse(response.metadata['artifacts_complete'])
        self.assertEqual(response.metadata['export_errors'][0]['format'],'doc')

@override_settings(ROOT_URLCONF='tests.spark_exports.urls', STATICFILES_DIRS=[Path(__file__).resolve().parents[2] / 'static'])
class SummaryTests(TestCase):
    setUp = GenerationTests.setUp

    def test_partial_source_section_is_checkpointed_and_retry_does_not_repeat_it(self):
        from pwanimate.models import PwanimateAttachment, PwanimateAttachmentChunk
        from pwanimate.services.agent_runs import AgentRunService, RunError
        from pwanimate.services.agent_execution import execute_claimed
        from pwanimate.api.views import PwanimateChatView
        attachment = PwanimateAttachment.objects.create(user=self.user, conversation=self.conv,
            file_name='Book.pdf', file_size=100, mime_type='application/pdf', processing_status='ready')
        for index in range(2):
            PwanimateAttachmentChunk.objects.create(attachment=attachment, chunk_index=index,
                content='Source material. ' * 700, content_hash=str(index), page_number=index + 1)
        self.source.content = 'Summarize the attached book as DOCX.'
        self.source.save(update_fields=['content'])
        request = OrchestrationRequest(query=self.source.content, user=self.user, user_message_id=self.source.pk,
            context_resources=[{'type':'attachment', 'attachment_id':str(attachment.pk)}])
        run, _ = AgentRunService.start_document(request)
        gateway = Mock()
        gateway.generate.side_effect = [
            LLMResponse(content='## First section\n\nSaved revision material.', provider='mock', model='mock', finish_reason='stop'),
            LLMResponse(content='Unfinished', provider='mock', model='mock', finish_reason='length')]
        service = PwanimateOrchestrator(gateway=gateway)
        with patch.object(PwanimateChatView, 'get_orchestrator', return_value=service), patch.object(service, '_record_usage'):
            with self.assertRaisesMessage(RunError, 'incomplete'):
                execute_claimed(run)
        run.refresh_from_db()
        self.assertEqual(len(run.state['document_sections']['sections']), 1)
        self.assertIsNone(run.result_message_id)
        self.assertEqual(saved_resources(self.user, [self.message.pk]), {})
        gateway.generate.reset_mock()
        gateway.generate.side_effect = None
        gateway.generate.return_value = LLMResponse(content='## Second section\n\nCompleted revision material.',
            provider='mock', model='mock', finish_reason='stop')
        with patch.object(PwanimateChatView, 'get_orchestrator', return_value=service), patch.object(service, '_record_usage'):
            result = execute_claimed(run)
        self.assertTrue(result['metadata']['artifacts_complete'])
        self.assertEqual(gateway.generate.call_count, 1)
        self.assertIn('First section', result['answer'])

    def test_uploaded_book_covers_all_chunks_and_exports_completed_summary(self):
        from pwanimate.models import PwanimateAttachment, PwanimateAttachmentChunk
        from pwanimate.services.agent_runs import AgentRunService
        from pwanimate.services.agent_execution import execute_claimed
        from pwanimate.api.views import PwanimateChatView
        attachment = PwanimateAttachment.objects.create(user=self.user, conversation=self.conv,
            file_name='Java.pdf', file_size=100, mime_type='application/pdf', processing_status='ready')
        for index in range(3):
            PwanimateAttachmentChunk.objects.create(attachment=attachment, chunk_index=index,
                content=('Interface contract and method dispatch. ' * 180), content_hash=str(index), page_number=index + 1)
        self.source.content = 'Summarize the attached book as DOCX.'; self.source.save(update_fields=['content'])
        request=OrchestrationRequest(query=self.source.content,user=self.user,user_message_id=self.source.pk,
            context_resources=[{'type':'attachment','attachment_id':str(attachment.pk)}])
        run,_=AgentRunService.start_document(request)
        gateway=Mock()
        gateway.generate.return_value=LLMResponse(content='### Key ideas\n\nInterfaces specify contracts.\n\n### Recall\n\nExplain dispatch.',provider='mock',model='mock',finish_reason='stop')
        service=PwanimateOrchestrator(gateway=gateway)
        with patch.object(PwanimateChatView,'get_orchestrator',return_value=service),patch.object(service,'_record_usage'):
            payload=execute_claimed(run)
        self.assertTrue(payload['metadata']['artifacts_complete'],payload)
        coverage=payload['metadata']['source_coverage']
        self.assertEqual(coverage['indexed_chunks'],3)
        self.assertEqual(coverage['sections'],gateway.generate.call_count)
        self.assertGreater(gateway.generate.call_count,1)
        self.assertFalse(coverage['whole_book_extraction_verified'])
        run.refresh_from_db()
        self.assertEqual(len(run.state['document_sections']['sections']),coverage['sections'])
        self.assertEqual(run.status,'completed')

    def test_failure_retry_reuses_saved_content_without_model_calls(self):
        from pwanimate.services.agent_runs import AgentRunService
        from pwanimate.services.agent_execution import execute_claimed
        from pwanimate.api.views import PwanimateChatView
        self.source.content='Create a DOCX document.'; self.source.save(update_fields=['content'])
        request=OrchestrationRequest(query=self.source.content,user=self.user,user_message_id=self.source.pk)
        run,_=AgentRunService.start_document(request)
        gateway=Mock()
        gateway.generate.side_effect=[LLMResponse(content='{"calls":[]}',provider='mock',model='mock'),
            LLMResponse(content=CONTENT,provider='mock',model='mock',finish_reason='stop')]
        service=PwanimateOrchestrator(gateway=gateway)
        with patch.object(PwanimateChatView,'get_orchestrator',return_value=service),patch.object(service,'_record_usage'), \
             patch('pwanimate.services.generated_documents._render_docx',side_effect=RuntimeError('temporary')):
            first=execute_claimed(run)
        run.refresh_from_db(); self.assertEqual(run.status,'failed')
        first_id=run.result_message_id
        run.status='running'; run.attempt_id='controlled-new-attempt'; run.save(update_fields=['status','attempt_id'])
        gateway.generate.reset_mock()
        with patch.object(PwanimateChatView,'get_orchestrator',return_value=service),patch.object(service,'_record_usage'):
            second=execute_claimed(run)
        self.assertTrue(second['metadata']['artifacts_complete'])
        self.assertEqual(second['message_id'],first_id)
        gateway.generate.assert_not_called()
        run.refresh_from_db(); self.assertEqual(run.status,'completed')

@override_settings(ROOT_URLCONF='tests.spark_exports.urls', STATICFILES_DIRS=[Path(__file__).resolve().parents[2] / 'static'])
class SafetyTests(TestCase):
    setUp = GenerationTests.setUp

    def test_missing_book_waits_and_source_selection_can_continue_same_run(self):
        from pwanimate.services.agent_runs import AgentRunService
        from pwanimate.services.agent_execution import execute_claimed, claim_run
        from pwanimate.api.views import PwanimateChatView
        from pwanimate.models import PwanimateAttachment, PwanimateAttachmentChunk, SparkAgentInteraction
        self.source.content='Summarize this book as DOCX.'; self.source.save(update_fields=['content'])
        request=OrchestrationRequest(query=self.source.content,user=self.user,user_message_id=self.source.pk)
        run,_=AgentRunService.start_document(request)
        gateway=Mock(); gateway.generate.return_value=LLMResponse(content='## Key concept\n\nA contract describes supported operations.',provider='mock',model='mock',finish_reason='stop')
        service=PwanimateOrchestrator(gateway=gateway)
        with patch.object(PwanimateChatView,'get_orchestrator',return_value=service),patch.object(service,'_record_usage'):
            waiting=execute_claimed(run)
        self.assertTrue(waiting['metadata']['awaiting_input']); gateway.generate.assert_not_called()
        run.refresh_from_db(); self.assertEqual(run.status,'waiting')
        interaction=SparkAgentInteraction.objects.get(run=run,status='pending')
        attachment=PwanimateAttachment.objects.create(user=self.user,conversation=self.conv,file_name='Book.pdf',file_size=100,
            mime_type='application/pdf',processing_status='ready')
        PwanimateAttachmentChunk.objects.create(attachment=attachment,chunk_index=0,content='An interface is a contract.',content_hash='one',page_number=1)
        claimed,_=claim_run(self.user,run,interaction=interaction,raw_answer={'text':'Use the selected book.'},
            context_resources=[{'type':'attachment','attachment_id':str(attachment.pk)}])
        with patch.object(PwanimateChatView,'get_orchestrator',return_value=service),patch.object(service,'_record_usage'):
            done=execute_claimed(claimed)
        self.assertTrue(done['metadata']['artifacts_complete'],done)
        self.assertEqual(done['run_id'],str(run.pk))
        self.assertEqual(gateway.generate.call_count,1)

    def test_repeated_delivery_does_not_start_second_run(self):
        from pwanimate.services.agent_runs import AgentRunService
        request=OrchestrationRequest(query=self.source.content,user=self.user,user_message_id=self.source.pk)
        first,new=AgentRunService.start_document(request)
        second,new_again=AgentRunService.start_document(request)
        self.assertTrue(new); self.assertFalse(new_again); self.assertEqual(first.pk,second.pk)

    def test_large_content_uses_configurable_limit_without_silent_truncation(self):
        self.message.content='x' * 190_000; self.message.save(update_fields=['content'])
        with patch('pwanimate.services.generated_documents._render_docx',return_value=b'PKsynthetic') as renderer:
            document,file,_=create_generated_resource(self.message,self.user,'docx')
        self.assertEqual(len(renderer.call_args.args[1].content),190_000)
        with override_settings(SPARK_EXPORT_MAX_CHARACTERS=1000),self.assertRaises(ValueError):
            create_generated_resource(self.message,self.user,'pptx')

    def test_export_cancelled_before_start_has_no_tool_calls_or_links(self):
        request=OrchestrationRequest(query=self.source.content,user=self.user,user_message_id=self.source.pk)
        response=OrchestrationResponse(answer=CONTENT,finish_reason='stop')
        with patch('pwanimate.tools.domain.exports.GenerateDocumentTool.execute') as tool:
            self.assertEqual(publish_requested_documents(request,response,self.message,cancel_check=lambda:True),[])
        tool.assert_not_called(); self.assertFalse(response.metadata['artifacts_complete'])
