"""
Pwanimate Attachment System — Automated Test Suite.

Covers:
  - Upload authentication and authorization
  - File validation (size, extension, magic bytes, MIME)
  - Ownership security (cross-user attachment isolation)
  - Private media endpoint access control
  - Chat payload integration (attachment linking, context_resources)
  - Multimodal gateway behavior (Gemini inlineData, Groq/OpenRouter capability errors)
"""

import io
import json
import struct
import uuid
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from pwanimate.models import PwanimateAttachment, PwanimateConversation, PwanimateMessage
from pwanimate.services.attachment import (
    AttachmentService,
    AttachmentValidator,
    sanitize_filename,
)
from pwanimate.ai.exceptions import AIProviderConfigurationError

User = get_user_model()

# ---------------------------------------------------------------------------
# Helpers for building synthetic file payloads
# ---------------------------------------------------------------------------

JPEG_MAGIC = b'\xff\xd8\xff\xe0' + b'\x00' * 60
PNG_MAGIC = b'\x89PNG\r\n\x1a\n' + b'\x00' * 56
GIF89_MAGIC = b'GIF89a' + b'\x00' * 58
PDF_MAGIC = b'%PDF-1.4\n' + b'\x00' * 55
ZIP_MAGIC = b'PK\x03\x04' + b'\x00' * 60  # docx/pptx
UTF8_TEXT = b'Hello, Pwanimate test document.\n' * 4


def make_file(name: str, content: bytes, content_type: str = 'application/octet-stream'):
    """Return a Django-style InMemoryUploadedFile-compatible mock for tests."""
    from django.core.files.uploadedfile import SimpleUploadedFile
    return SimpleUploadedFile(name=name, content=content, content_type=content_type)


# ---------------------------------------------------------------------------
# 1. Sanitize Filename Tests
# ---------------------------------------------------------------------------

class SanitizeFilenameTestCase(TestCase):
    def test_strips_directory_traversal(self):
        self.assertNotIn('..', sanitize_filename('../../etc/passwd.txt'))

    def test_empty_input_returns_attachment(self):
        self.assertEqual(sanitize_filename(''), 'attachment')

    def test_normalizes_extension(self):
        result = sanitize_filename('MyFile.PDF')
        self.assertTrue(result.lower().endswith('.pdf'))

    def test_truncates_long_names(self):
        long_name = 'a' * 200 + '.txt'
        result = sanitize_filename(long_name)
        self.assertLessEqual(len(result), 200)

    def test_rejects_null_bytes(self):
        result = sanitize_filename('bad\x00file.pdf')
        self.assertNotIn('\x00', result)


# ---------------------------------------------------------------------------
# 2. AttachmentValidator Tests
# ---------------------------------------------------------------------------

class AttachmentValidatorTestCase(TestCase):

    def test_valid_jpeg(self):
        f = make_file('test.jpg', JPEG_MAGIC, 'image/jpeg')
        ok, code, msg, att_type = AttachmentValidator.validate_file(f)
        self.assertTrue(ok)
        self.assertEqual(att_type, 'image')

    def test_valid_png(self):
        f = make_file('test.png', PNG_MAGIC, 'image/png')
        ok, code, msg, att_type = AttachmentValidator.validate_file(f)
        self.assertTrue(ok)
        self.assertEqual(att_type, 'image')

    def test_valid_gif(self):
        f = make_file('test.gif', GIF89_MAGIC, 'image/gif')
        ok, code, msg, att_type = AttachmentValidator.validate_file(f)
        self.assertTrue(ok)
        self.assertEqual(att_type, 'image')

    def test_valid_pdf(self):
        f = make_file('doc.pdf', PDF_MAGIC, 'application/pdf')
        ok, code, msg, att_type = AttachmentValidator.validate_file(f)
        self.assertTrue(ok)
        self.assertEqual(att_type, 'document')

    def test_valid_txt(self):
        f = make_file('notes.txt', UTF8_TEXT, 'text/plain')
        ok, code, msg, att_type = AttachmentValidator.validate_file(f)
        self.assertTrue(ok)
        self.assertEqual(att_type, 'document')

    def test_rejects_svg_extension(self):
        f = make_file('evil.svg', b'<svg xmlns="http://www.w3.org/2000/svg"/>', 'image/svg+xml')
        ok, code, msg, att_type = AttachmentValidator.validate_file(f)
        self.assertFalse(ok)
        self.assertEqual(code, 'DANGEROUS_FILE_TYPE')

    def test_rejects_exe_extension(self):
        f = make_file('malware.exe', b'MZ\x00\x00', 'application/octet-stream')
        ok, code, msg, att_type = AttachmentValidator.validate_file(f)
        self.assertFalse(ok)
        self.assertEqual(code, 'DANGEROUS_FILE_TYPE')

    def test_rejects_unsupported_extension(self):
        f = make_file('archive.zip', ZIP_MAGIC, 'application/zip')
        ok, code, msg, att_type = AttachmentValidator.validate_file(f)
        self.assertFalse(ok)
        self.assertEqual(code, 'UNSUPPORTED_FORMAT')

    def test_rejects_empty_file(self):
        f = make_file('empty.jpg', b'', 'image/jpeg')
        f.size = 0
        ok, code, msg, att_type = AttachmentValidator.validate_file(f)
        self.assertFalse(ok)

    def test_rejects_corrupt_jpeg_wrong_magic(self):
        f = make_file('fake.jpg', b'PNG' + b'\x00' * 60, 'image/jpeg')
        ok, code, msg, att_type = AttachmentValidator.validate_file(f)
        self.assertFalse(ok)
        self.assertEqual(code, 'CORRUPT_OR_SPOOFED_IMAGE')

    def test_rejects_corrupt_pdf_wrong_magic(self):
        f = make_file('fake.pdf', b'This is not a PDF' + b'\x00' * 50, 'application/pdf')
        ok, code, msg, att_type = AttachmentValidator.validate_file(f)
        self.assertFalse(ok)
        self.assertEqual(code, 'CORRUPT_OR_SPOOFED_PDF')

    @override_settings(PWANIMATE_MAX_IMAGE_SIZE=100)
    def test_rejects_oversized_image(self):
        f = make_file('big.jpg', JPEG_MAGIC + b'\x00' * 500, 'image/jpeg')
        ok, code, msg, att_type = AttachmentValidator.validate_file(f)
        self.assertFalse(ok)
        self.assertEqual(code, 'FILE_TOO_LARGE')

    def test_rejects_null_bytes_in_text(self):
        f = make_file('bad.txt', b'Hello\x00world', 'text/plain')
        ok, code, msg, att_type = AttachmentValidator.validate_file(f)
        self.assertFalse(ok)
        self.assertEqual(code, 'BINARY_TEXT_FILE')


# ---------------------------------------------------------------------------
# 3. Upload Endpoint Tests
# ---------------------------------------------------------------------------

UPLOAD_URL = '/api/pwanimate/attachments/upload/'


class AttachmentUploadEndpointTestCase(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='uploader_test',
            email='uploader@vasto.local',
            password='pass',
        )
        self.other_user = User.objects.create_user(
            username='other_uploader',
            email='other_upload@vasto.local',
            password='pass',
        )

    def test_unauthenticated_upload_returns_401(self):
        f = make_file('test.jpg', JPEG_MAGIC, 'image/jpeg')
        response = self.client.post(UPLOAD_URL, {'file': f}, format='multipart')
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_authenticated_image_upload_returns_201(self):
        self.client.force_authenticate(user=self.user)
        f = make_file('photo.jpg', JPEG_MAGIC, 'image/jpeg')
        response = self.client.post(UPLOAD_URL, {'file': f}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertIn('id', data)
        self.assertEqual(data['attachment_type'], 'image')
        self.assertIn('/view/', data['url'])

    def test_authenticated_pdf_upload_returns_201(self):
        self.client.force_authenticate(user=self.user)
        f = make_file('report.pdf', PDF_MAGIC, 'application/pdf')
        response = self.client.post(UPLOAD_URL, {'file': f}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertEqual(data['attachment_type'], 'document')

    def test_authenticated_txt_upload_returns_201(self):
        self.client.force_authenticate(user=self.user)
        f = make_file('notes.txt', UTF8_TEXT, 'text/plain')
        response = self.client.post(UPLOAD_URL, {'file': f}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_missing_file_field_returns_400(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(UPLOAD_URL, {}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_dangerous_extension_rejected(self):
        self.client.force_authenticate(user=self.user)
        f = make_file('virus.exe', b'MZ\x00\x00', 'application/octet-stream')
        response = self.client.post(UPLOAD_URL, {'file': f}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_svg_extension_rejected(self):
        self.client.force_authenticate(user=self.user)
        f = make_file('icon.svg', b'<svg/>', 'image/svg+xml')
        response = self.client.post(UPLOAD_URL, {'file': f}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_corrupt_jpeg_rejected(self):
        self.client.force_authenticate(user=self.user)
        f = make_file('fake.jpg', b'NOTAJPEG' * 10, 'image/jpeg')
        response = self.client.post(UPLOAD_URL, {'file': f}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_attachment_linked_to_user(self):
        self.client.force_authenticate(user=self.user)
        f = make_file('photo.png', PNG_MAGIC, 'image/png')
        response = self.client.post(UPLOAD_URL, {'file': f}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        att_id = response.json()['id']
        att = PwanimateAttachment.objects.get(id=att_id)
        self.assertEqual(att.user, self.user)


# ---------------------------------------------------------------------------
# 4. Ownership & Cross-User Security Tests
# ---------------------------------------------------------------------------

class AttachmentOwnershipTestCase(TestCase):

    def setUp(self):
        self.user_a = User.objects.create_user(
            username='user_a', email='user_a@vasto.local', password='pass')
        self.user_b = User.objects.create_user(
            username='user_b', email='user_b@vasto.local', password='pass')
        self.client = APIClient()

    def _upload_as(self, user, filename='photo.jpg', content=JPEG_MAGIC, ct='image/jpeg'):
        self.client.force_authenticate(user=user)
        f = make_file(filename, content, ct)
        resp = self.client.post(UPLOAD_URL, {'file': f}, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        return resp.json()['id']

    def test_user_b_cannot_view_user_a_attachment(self):
        att_id = self._upload_as(self.user_a)
        self.client.force_authenticate(user=self.user_b)
        url = f'/api/pwanimate/attachments/{att_id}/view/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_user_b_cannot_download_user_a_attachment(self):
        att_id = self._upload_as(self.user_a)
        self.client.force_authenticate(user=self.user_b)
        url = f'/api/pwanimate/attachments/{att_id}/download/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_owner_can_view_own_attachment(self):
        att_id = self._upload_as(self.user_a)
        self.client.force_authenticate(user=self.user_a)
        url = f'/api/pwanimate/attachments/{att_id}/view/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_user_b_cannot_include_user_a_attachment_in_chat(self):
        att_id = self._upload_as(self.user_a)
        self.client.force_authenticate(user=self.user_b)

        mock_orch = MagicMock()
        mock_orch.run.return_value = MagicMock(
            answer='reply', citations=[], sources=[], people=[],
            provider='mock', model='mock', finish_reason='stop',
            prompt_tokens=1, completion_tokens=1, total_tokens=2,
            metadata={}, quota_info=None,
            to_dict=lambda: {'answer': 'reply'},
        )

        from pwanimate.api.views import PwanimateChatView
        with patch.object(PwanimateChatView, 'get_orchestrator', return_value=mock_orch):
            response = self.client.post(
                '/api/pwanimate/chat/',
                data=json.dumps({'message': 'test', 'attachments': [att_id]}),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('not found or unauthorized', response.json().get('error', '').lower())

    def test_service_get_authorized_attachment_enforces_ownership(self):
        att_id = self._upload_as(self.user_a)
        att_uuid = uuid.UUID(att_id)
        result = AttachmentService.get_authorized_attachment(user=self.user_b, attachment_id=att_uuid)
        self.assertIsNone(result)

    def test_service_get_authorized_attachment_allows_owner(self):
        att_id = self._upload_as(self.user_a)
        att_uuid = uuid.UUID(att_id)
        result = AttachmentService.get_authorized_attachment(user=self.user_a, attachment_id=att_uuid)
        self.assertIsNotNone(result)
        self.assertEqual(result.user, self.user_a)


# ---------------------------------------------------------------------------
# 5. Private Media Access Control (unauthenticated)
# ---------------------------------------------------------------------------

class AttachmentMediaAuthTestCase(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='media_auth_test', email='media@vasto.local', password='pass')
        self.client = APIClient()

    def _upload(self):
        self.client.force_authenticate(user=self.user)
        f = make_file('photo.jpg', JPEG_MAGIC, 'image/jpeg')
        resp = self.client.post(UPLOAD_URL, {'file': f}, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.client.force_authenticate(user=None)  # logout
        return resp.json()['id']

    def test_unauthenticated_view_returns_401(self):
        att_id = self._upload()
        url = f'/api/pwanimate/attachments/{att_id}/view/'
        response = self.client.get(url)
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_unauthenticated_download_returns_401(self):
        att_id = self._upload()
        url = f'/api/pwanimate/attachments/{att_id}/download/'
        response = self.client.get(url)
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_nonexistent_attachment_returns_404(self):
        self.client.force_authenticate(user=self.user)
        url = f'/api/pwanimate/attachments/{uuid.uuid4()}/view/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


# ---------------------------------------------------------------------------
# 6. Chat Payload Integration Tests
# ---------------------------------------------------------------------------

class ChatAttachmentIntegrationTestCase(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='chat_attach_test', email='chat_att@vasto.local', password='pass')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _upload(self, content=PDF_MAGIC, name='doc.pdf', ct='application/pdf', processed=True):
        self.client.force_authenticate(user=self.user)
        f = make_file(name, content, ct)
        resp = self.client.post(UPLOAD_URL, {'file': f}, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        att_id = resp.json()['id']
        if processed:
            # Uploads are queued for processing. The chat path refuses a
            # turn while an attachment is still being read, so the test
            # starts from the state the product produces once the worker
            # has finished.
            PwanimateAttachment.objects.filter(id=att_id).update(
                processing_status="ready"
            )
        return att_id

    def _make_mock_orchestrator(self):
        mock_orch = MagicMock()
        result = MagicMock()
        result.answer = 'I analyzed your attachment.'
        result.citations = []
        result.sources = []
        result.people = []
        result.provider = 'mock'
        result.model = 'mock-model'
        result.finish_reason = 'stop'
        result.prompt_tokens = 10
        result.completion_tokens = 5
        result.total_tokens = 15
        result.metadata = {}
        result.quota_info = None
        result.to_dict.return_value = {
            'answer': result.answer,
            'citations': [],
            'sources': [],
        }
        mock_orch.run.return_value = result
        return mock_orch

    def test_chat_links_attachment_to_user_message(self):
        att_id = self._upload()
        mock_orch = self._make_mock_orchestrator()

        from pwanimate.api.views import PwanimateChatView
        with patch.object(PwanimateChatView, 'get_orchestrator', return_value=mock_orch):
            response = self.client.post(
                '/api/pwanimate/chat/',
                data=json.dumps({'message': 'Explain this doc', 'attachments': [att_id]}),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('conversation_id', data)

        # Attachment must be linked to a user message
        att = PwanimateAttachment.objects.get(id=att_id)
        self.assertIsNotNone(att.message)
        self.assertEqual(att.message.role, 'user')
        self.assertIsNotNone(att.conversation)

    def test_chat_returns_attachments_in_response(self):
        att_id = self._upload(content=PNG_MAGIC, name='img.png', ct='image/png')
        mock_orch = self._make_mock_orchestrator()

        from pwanimate.api.views import PwanimateChatView
        with patch.object(PwanimateChatView, 'get_orchestrator', return_value=mock_orch):
            response = self.client.post(
                '/api/pwanimate/chat/',
                data=json.dumps({'message': 'Describe this image', 'attachments': [att_id]}),
                content_type='application/json',
            )
        data = response.json()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('attachments', data)
        self.assertEqual(len(data['attachments']), 1)
        self.assertEqual(data['attachments'][0]['id'], att_id)

    def test_chat_passes_context_resources_to_orchestrator(self):
        mock_orch = self._make_mock_orchestrator()
        ctx_resource = {'type': 'document', 'id': str(uuid.uuid4()), 'page': 2}

        from pwanimate.api.views import PwanimateChatView
        with patch.object(PwanimateChatView, 'get_orchestrator', return_value=mock_orch):
            response = self.client.post(
                '/api/pwanimate/chat/',
                data=json.dumps({'message': 'Summarize page 2', 'context_resources': [ctx_resource]}),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        call_args = mock_orch.run.call_args
        req = call_args[0][0]
        self.assertEqual(req.context_resources, [ctx_resource])

    def test_chat_rejects_nonexistent_attachment_id(self):
        response = self.client.post(
            '/api/pwanimate/chat/',
            data=json.dumps({'message': 'test', 'attachments': [str(uuid.uuid4())]}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_chat_without_attachments_works_normally(self):
        mock_orch = self._make_mock_orchestrator()

        from pwanimate.api.views import PwanimateChatView
        with patch.object(PwanimateChatView, 'get_orchestrator', return_value=mock_orch):
            response = self.client.post(
                '/api/pwanimate/chat/',
                data=json.dumps({'message': 'Hello Pwanimate'}),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        # No attachments key when none sent
        self.assertNotIn('attachments', data)


# ---------------------------------------------------------------------------
# 7. Multimodal Provider Capability Tests
# ---------------------------------------------------------------------------

class ProviderMultimodalCapabilityTestCase(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='provider_cap_test', email='prov@vasto.local', password='pass')
        self.client = APIClient()

    def _upload_image(self):
        self.client.force_authenticate(user=self.user)
        f = make_file('photo.jpg', JPEG_MAGIC, 'image/jpeg')
        resp = self.client.post(UPLOAD_URL, {'file': f}, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        att_id = resp.json()['id']
        # An upload is queued for its vision pass; the chat path refuses a
        # turn while that is still running. Move it to the finished state so
        # the test reaches the behaviour it is actually about.
        PwanimateAttachment.objects.filter(id=att_id).update(
            processing_status="ready"
        )
        return att_id

    def test_groq_raises_config_error_for_images(self):
        from pwanimate.ai.providers.groq import GroqLLMProvider
        provider = GroqLLMProvider()
        from pwanimate.ai.gateway.types import LLMRequest, ChatMessage, AttachmentData
        att = AttachmentData(
            attachment_id='test-id',
            file_name='test.jpg',
            file_path='/tmp/test.jpg',
            mime_type='image/jpeg',
            attachment_type='image',
        )
        req = LLMRequest(
            messages=[ChatMessage(role='user', content='Describe', attachments=[att])],
            model='llama-3.3-70b-versatile',
        )
        with self.assertRaises(AIProviderConfigurationError) as ctx:
            provider._build_payload(req, model='llama-3.3-70b-versatile')
        self.assertIn('image', str(ctx.exception).lower())

    def test_openrouter_raises_config_error_for_images(self):
        from pwanimate.ai.providers.openrouter import OpenRouterLLMProvider
        provider = OpenRouterLLMProvider()
        from pwanimate.ai.gateway.types import LLMRequest, ChatMessage, AttachmentData
        att = AttachmentData(
            attachment_id='test-id',
            file_name='test.jpg',
            file_path='/tmp/test.jpg',
            mime_type='image/jpeg',
            attachment_type='image',
        )
        req = LLMRequest(
            messages=[ChatMessage(role='user', content='Describe', attachments=[att])],
            model='openrouter/auto',
        )
        with self.assertRaises(AIProviderConfigurationError) as ctx:
            provider._build_payload(req, model='openrouter/auto')
        self.assertIn('image', str(ctx.exception).lower())

    def test_chat_returns_400_when_provider_lacks_image_capability(self):
        att_id = self._upload_image()

        from pwanimate.ai.exceptions import AIProviderConfigurationError
        from pwanimate.api.views import PwanimateChatView

        mock_orch = MagicMock()
        mock_orch.run.side_effect = AIProviderConfigurationError(
            message="Provider 'groq' does not support image attachments.",
            provider='groq',
        )

        with patch.object(PwanimateChatView, 'get_orchestrator', return_value=mock_orch):
            response = self.client.post(
                '/api/pwanimate/chat/',
                data=json.dumps({
                    'message': 'Describe this image',
                    'provider': 'groq',
                    'attachments': [att_id],
                }),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('groq', response.json().get('error', '').lower())

    def test_gemini_builds_inline_data_for_images(self):
        """Gemini provider must produce inlineData parts for image attachments."""
        from pwanimate.ai.providers.gemini import GeminiLLMProvider
        from pwanimate.ai.gateway.types import LLMRequest, ChatMessage, AttachmentData
        import base64

        # Create a temp file with JPEG content
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            tmp.write(JPEG_MAGIC + b'\x00' * 64)
            tmp_path = tmp.name

        try:
            provider = GeminiLLMProvider.__new__(GeminiLLMProvider)
            att = AttachmentData(
                attachment_id='img-test',
                file_name='photo.jpg',
                file_path=tmp_path,
                mime_type='image/jpeg',
                attachment_type='image',
            )
            req = LLMRequest(
                messages=[ChatMessage(role='user', content='Describe', attachments=[att])],
                model='gemini-2.0-flash',
            )
            payload = provider._build_payload(req)
            contents = payload.get('contents', [])
            # Find the user turn
            user_turn = next((c for c in contents if c.get('role') == 'user'), None)
            self.assertIsNotNone(user_turn)
            parts = user_turn.get('parts', [])
            inline_parts = [p for p in parts if 'inlineData' in p]
            self.assertTrue(len(inline_parts) > 0, "Expected inlineData part for image attachment")
            self.assertEqual(inline_parts[0]['inlineData']['mimeType'], 'image/jpeg')
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# 8. AttachmentService create_attachment Tests
# ---------------------------------------------------------------------------

class AttachmentServiceTestCase(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='svc_test_user', email='svc@vasto.local', password='pass')

    def test_create_image_attachment(self):
        f = make_file('photo.jpg', JPEG_MAGIC, 'image/jpeg')
        att = AttachmentService.create_attachment(user=self.user, uploaded_file=f)
        self.assertIsInstance(att, PwanimateAttachment)
        self.assertEqual(att.attachment_type, 'image')
        self.assertEqual(att.user, self.user)
        self.assertEqual(att.mime_type, 'image/jpeg')

    def test_create_pdf_attachment(self):
        f = make_file('report.pdf', PDF_MAGIC, 'application/pdf')
        att = AttachmentService.create_attachment(user=self.user, uploaded_file=f)
        self.assertEqual(att.attachment_type, 'document')
        self.assertEqual(att.mime_type, 'application/pdf')

    def test_raises_on_invalid_file(self):
        f = make_file('virus.exe', b'MZ\x00\x00', 'application/octet-stream')
        with self.assertRaises(ValidationError):
            AttachmentService.create_attachment(user=self.user, uploaded_file=f)

    def test_get_authorized_attachment_unauthenticated_user(self):
        from django.contrib.auth.models import AnonymousUser
        result = AttachmentService.get_authorized_attachment(
            user=AnonymousUser(), attachment_id=uuid.uuid4())
        self.assertIsNone(result)

    def test_get_authorized_attachment_invalid_uuid(self):
        result = AttachmentService.get_authorized_attachment(
            user=self.user, attachment_id='not-a-uuid')
        self.assertIsNone(result)

    def test_attachment_url_properties(self):
        f = make_file('photo.jpg', JPEG_MAGIC, 'image/jpeg')
        att = AttachmentService.create_attachment(user=self.user, uploaded_file=f)
        self.assertIn(str(att.id), att.url)
        self.assertIn('/view/', att.url)
        self.assertIn('/download/', att.download_url)
