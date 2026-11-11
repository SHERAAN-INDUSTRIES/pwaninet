"""Local OCR payloads, private storage, task routing and failure handling."""
import base64
import io
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from PIL import Image

from pwanimate.ai.capabilities import provider_supports_images
from pwanimate.ai.exceptions import AIProviderAPIError, AIProviderConfigurationError, AIProviderTimeoutError
from pwanimate.ai.gateway import AIGateway, ChatMessage, LLMRequest
from pwanimate.ai.gateway.types import AttachmentData, LLMResponse
from pwanimate.ai.providers.ollama import OllamaOCRProvider


@override_settings(
    PWANIMATE_OLLAMA_BASE_URL='http://127.0.0.1:11434',
    PWANIMATE_OLLAMA_OCR_MODEL='deepseek-ocr:latest',
    PWANIMATE_ATTACHMENT_VISION_PROVIDER='ollama',
    PWANIMATE_ATTACHMENT_VISION_MODEL='deepseek-ocr:latest',
    PWANIMATE_ATTACHMENT_VISION_FALLBACK_CHAIN=[{'provider':'ollama','model':'deepseek-ocr:latest'}],
)
class OllamaOCRTests(SimpleTestCase):
    def setUp(self):
        buffer=io.BytesIO();Image.new('RGB',(32,32),'white').save(buffer,format='PNG')
        self.image=buffer.getvalue()
        self.attachment=AttachmentData(id='image-1',name='scan.png',mime_type='image/png',attachment_type='image',data_bytes=self.image)
        self.request=LLMRequest(task='attachment_vision_analysis',provider='ollama',model='deepseek-ocr:latest',messages=[ChatMessage(role='user',content='Private chat question',attachments=[self.attachment])],attachments=[self.attachment],max_tokens=4096,metadata={'required_capability':'image','fallback_chain':[{'provider':'ollama','model':'deepseek-ocr:latest'}]})

    def response(self,**changes):
        result={'response':'Total: 42','done':True,'done_reason':'stop','prompt_eval_count':20,'eval_count':4}
        result.update(changes)
        return Mock(status_code=200,json=Mock(return_value=result))

    @patch('pwanimate.ai.providers.ollama.requests.post')
    def test_gateway_sends_image_bytes_once_and_normalizes_response(self,post):
        post.return_value=self.response()
        result=AIGateway().generate(self.request)
        self.assertEqual((result.provider,result.model,result.content,result.total_tokens),('ollama','deepseek-ocr:latest','Total: 42',24))
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0],'http://127.0.0.1:11434/api/generate')
        payload=post.call_args.kwargs['json']
        self.assertEqual(payload['prompt'],'\nFree OCR.')
        self.assertEqual(len(payload['images']),1)
        self.assertEqual(base64.b64decode(payload['images'][0]),self.image)
        self.assertNotIn('Private chat question',str(payload))
        self.assertFalse(payload['stream'])
        self.assertFalse(post.call_args.kwargs['allow_redirects'])

    @patch('pwanimate.ai.providers.ollama.requests.post')
    def test_private_django_storage_is_read_without_using_media_url(self,post):
        post.return_value=self.response()
        stored=SimpleNamespace(id='private-image',attachment_type='image',mime_type='image/png',file=SimpleUploadedFile('private.png',self.image),url='https://cdn.invalid/private?token=SECRET')
        self.request.messages[0].attachments=[stored];self.request.attachments=[stored]
        OllamaOCRProvider().generate(self.request)
        payload=post.call_args.kwargs['json']
        self.assertEqual(base64.b64decode(payload['images'][0]),self.image)
        self.assertNotIn('SECRET',str(payload))

    def test_capabilities_allow_only_configured_model_and_one_image(self):
        self.assertTrue(provider_supports_images('ollama','deepseek-ocr:latest',1))
        self.assertFalse(provider_supports_images('ollama','deepseek-ocr:latest',2))
        self.assertFalse(provider_supports_images('ollama','unknown',1))

    @patch('pwanimate.ai.providers.ollama.requests.post')
    def test_ocr_cannot_replace_the_answer_model(self,post):
        self.request.task='rag'
        with self.assertRaises(AIProviderConfigurationError):OllamaOCRProvider().generate(self.request)
        post.assert_not_called()

    @patch('pwanimate.ai.providers.ollama.requests.post')
    def test_missing_image_bytes_fail_before_network(self,post):
        self.attachment.data_bytes=None
        with self.assertRaises(AIProviderAPIError):OllamaOCRProvider().generate(self.request)
        post.assert_not_called()

    @patch('pwanimate.ai.providers.ollama.requests.post')
    def test_timeout_connection_and_missing_model_errors(self,post):
        for error,expected in [(requests.Timeout(),AIProviderTimeoutError),(requests.ConnectionError(),AIProviderAPIError)]:
            post.side_effect=error
            with self.assertRaises(expected):OllamaOCRProvider().generate(self.request)
        post.side_effect=None;post.return_value=Mock(status_code=404)
        with self.assertRaises(AIProviderAPIError):OllamaOCRProvider().generate(self.request)

    @patch('pwanimate.ai.providers.ollama.requests.post')
    def test_empty_partial_truncated_or_invalid_json_cannot_be_saved_as_ready(self,post):
        for response in [self.response(response=''),self.response(done=False),self.response(done_reason='length'),Mock(status_code=200,json=Mock(side_effect=ValueError('invalid JSON')))]:
            post.return_value=response
            with self.assertRaises(AIProviderAPIError):OllamaOCRProvider().generate(self.request)

    @patch('pwanimate.ai.providers.ollama.requests.post',side_effect=requests.ConnectionError())
    @patch('pwanimate.ai.providers.gemini.GeminiLLMProvider.generate')
    def test_local_failure_does_not_send_image_to_cloud(self,cloud,post):
        with self.assertRaises(AIProviderAPIError):AIGateway().generate(self.request)
        cloud.assert_not_called()

    @patch('pwanimate.tasks.attachments.AIGateway')
    @patch('pwanimate.billing.global_budget.image_budget')
    @patch('pwanimate.billing.services.UsageService.record_image_analysis')
    def test_upload_analysis_uses_configured_local_route(self,meter,budget,gateway):
        from pwanimate.tasks.attachments import _analyze_image
        budget.return_value.consume.return_value=True
        gateway.return_value.generate.return_value=LLMResponse(content='Total: 42',provider='ollama',model='deepseek-ocr:latest')
        text,provider,model=_analyze_image(self.attachment)
        request=gateway.return_value.generate.call_args.args[0]
        self.assertEqual((request.provider,request.model),('ollama','deepseek-ocr:latest'))
        self.assertEqual(request.metadata['fallback_chain'],[{'provider':'ollama','model':'deepseek-ocr:latest'}])
        self.assertEqual((text,provider,model),('Total: 42','ollama','deepseek-ocr:latest'))
        meter.assert_called_once()

    @patch('pwanimate.tasks.attachments.AIGateway')
    def test_scanned_pdf_page_uses_same_local_route(self,gateway):
        from pwanimate.tasks.attachments import _transcribe_pdf_page
        gateway.return_value.generate.return_value=LLMResponse(content='Page text',provider='ollama',model='deepseek-ocr:latest')
        page=Mock();page.get_pixmap.return_value.tobytes.return_value=self.image
        result=_transcribe_pdf_page(page,SimpleNamespace(id='pdf-1',file_name='notes.pdf'),1)
        request=gateway.return_value.generate.call_args.args[0]
        self.assertEqual((request.provider,request.model,request.task),('ollama','deepseek-ocr:latest','attachment_vision_extraction'))
        self.assertEqual(request.attachments[0].data_bytes,self.image)
        self.assertEqual(result,('Page text','ollama','deepseek-ocr:latest'))
