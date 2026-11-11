from unittest.mock import patch
from django.test import TestCase, Client
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model

from documents.models import Document, DocumentVersion, DocumentFile, Category

User = get_user_model()


class DocumentUploadTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='student1',
            email='student1@example.com',
            password='Password123!'
        )
        self.client.force_login(self.user)
        self.category = Category.objects.create(
            code='lecture_notes',
            name='Lecture Notes',
            is_active=True
        )

    def test_upload_valid_document_success(self):
        pdf_content = b"%PDF-1.4 simulated pdf document content"
        uploaded_file = SimpleUploadedFile(
            "sample_cat.pdf",
            pdf_content,
            content_type="application/pdf"
        )
        data = {
            'files': [uploaded_file],
            'category': self.category.id,
            'file_0_title': 'Sample CAT Notes',
            'file_0_description': 'Lecture notes for week 1',
        }

        with patch('documents.tasks.processing.process_document.delay') as mock_task:
            mock_task.return_value.id = 'task-uuid-1234'
            response = self.client.post(reverse('documents:upload'), data)

        self.assertEqual(response.status_code, 200)
        json_data = response.json()
        self.assertTrue(json_data['success'])
        self.assertEqual(json_data['document_count'], 1)
        self.assertEqual(json_data['task_ids'], ['task-uuid-1234'])

        doc = Document.objects.filter(title='Sample CAT Notes', uploaded_by=self.user).first()
        self.assertIsNotNone(doc)
        self.assertEqual(doc.status, 'processing')
        self.assertEqual(doc.category, self.category)

        version = doc.versions.first()
        self.assertIsNotNone(version)
        self.assertEqual(version.version_number, 1)

        doc_file = version.files.first()
        self.assertIsNotNone(doc_file)
        self.assertEqual(doc_file.original_filename, 'sample_cat.pdf')
        self.assertEqual(doc_file.extension, 'pdf')

    def test_upload_file_too_large_rejected(self):
        from documents.services.upload_service import UploadService
        large_file = SimpleUploadedFile(
            "huge_book.pdf",
            b"data",
            content_type="application/pdf"
        )
        large_file.size = 55 * 1024 * 1024
        is_valid, msg = UploadService().validate_file(large_file)
        self.assertFalse(is_valid)
        self.assertIn("exceeds maximum", msg)

        with patch.object(UploadService, 'validate_file', return_value=(False, "File size exceeds maximum of 50MB")):
            sample_file = SimpleUploadedFile(
                "huge_book.pdf",
                b"data",
                content_type="application/pdf"
            )
            response = self.client.post(reverse('documents:upload'), {
                'files': [sample_file],
                'category': self.category.id,
            })
            self.assertEqual(response.status_code, 400)
            json_data = response.json()
            self.assertFalse(json_data['success'])
            self.assertIn("exceeds maximum", json_data['error'])

    def test_upload_disallowed_extension_rejected(self):
        bad_file = SimpleUploadedFile(
            "malicious.exe",
            b"MZ...",
            content_type="application/x-msdownload"
        )
        data = {
            'files': [bad_file],
            'category': self.category.id,
        }

        response = self.client.post(reverse('documents:upload'), data)
        self.assertEqual(response.status_code, 400)
        json_data = response.json()
        self.assertFalse(json_data['success'])
        self.assertIn("not allowed", json_data['error'])

    def test_upload_duplicate_title_gets_unique_suffix(self):
        Document.objects.create(
            title='Existing Document',
            uploaded_by=self.user,
            category=self.category,
            status='ready'
        )

        pdf_content = b"%PDF-1.4 duplicate test content"
        uploaded_file = SimpleUploadedFile(
            "document.pdf",
            pdf_content,
            content_type="application/pdf"
        )
        data = {
            'files': [uploaded_file],
            'category': self.category.id,
            'file_0_title': 'Existing Document',
        }

        with patch('documents.tasks.processing.process_document.delay') as mock_task:
            mock_task.return_value.id = 'task-uuid-5678'
            response = self.client.post(reverse('documents:upload'), data)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])

        # Check that duplicate title received unique suffix
        new_doc = Document.objects.filter(title='Existing Document (1)', uploaded_by=self.user).first()
        self.assertIsNotNone(new_doc)

    def test_upload_missing_category_uses_other_category(self):
        uploaded_file = SimpleUploadedFile(
            "test.pdf",
            b"%PDF-1.4 test",
            content_type="application/pdf"
        )
        data = {
            'files': [uploaded_file],
        }

        response = self.client.post(reverse('documents:upload'), data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
        self.assertEqual(Document.objects.filter(uploaded_by=self.user).latest('created_at').category.code, 'other')

    def test_htmx_get_upload_page_contains_fixed_script(self):
        response = self.client.get(reverse('documents:upload'), HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Check accept attribute
        self.assertIn('.doc', content)
        self.assertIn('.pdf', content)
        self.assertIn('.ppt', content)
        self.assertIn('.rar', content)

        # Check script initialization is invoked and attached
        self.assertIn('initDocumentUpload();', content)
        self.assertIn('htmx:afterSwap', content)
        self.assertNotIn('});\n</script>', content)

    def test_my_library_shows_processing_document_with_spinner_overlay(self):
        # Create a processing document
        doc = Document.objects.create(
            title='Processing Document Test',
            uploaded_by=self.user,
            category=self.category,
            status='processing'
        )
        version = DocumentVersion.objects.create(
            document=doc,
            version_number=1,
            is_latest=True,
            created_by=self.user
        )
        DocumentFile.objects.create(
            document_version=version,
            original_filename='processing_test.pdf',
            storage_path='documents/processing_test.pdf',
            mime_type='application/pdf',
            extension='pdf',
            size_bytes=1024,
            uploaded_by=self.user,
            processing_status='pending'
        )

        response = self.client.get(reverse('documents:my_library'), HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['has_processing_uploads'])
        self.assertIn(doc, response.context['recent_uploads'])

        content = response.content.decode('utf-8')
        # Check processing spinner overlay and badge are rendered
        self.assertIn('doc-processing-overlay', content)
        self.assertIn('Processing...', content)
        self.assertIn('doc-processing-badge', content)
        # Check background polling is active while processing
        self.assertIn('hx-trigger="every 4s"', content)

    def test_my_uploads_shows_processing_document_with_spinner_overlay(self):
        doc = Document.objects.create(
            title='Processing Upload Test',
            uploaded_by=self.user,
            category=self.category,
            status='processing'
        )

        response = self.client.get(reverse('documents:my_uploads'), HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['has_processing_uploads'])
        self.assertIn(doc, response.context['documents'])

        content = response.content.decode('utf-8')
        self.assertIn('doc-processing-overlay', content)
        self.assertIn('Processing...', content)
        self.assertIn('hx-trigger="every 4s"', content)

    def test_ready_document_does_not_show_processing_overlay(self):
        doc = Document.objects.create(
            title='Ready Document Test',
            uploaded_by=self.user,
            category=self.category,
            status='ready'
        )

        response = self.client.get(reverse('documents:my_library'), HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['has_processing_uploads'])

        content = response.content.decode('utf-8')
        self.assertNotIn('doc-processing-overlay', content)
        self.assertNotIn('hx-trigger="every 4s"', content)


    def test_direct_upload_storage_keys_do_not_overwrite_same_filename(self):
        from documents.services.direct_upload_service import storage_path_for
        first = storage_path_for('notes.pdf')
        second = storage_path_for('notes.pdf')
        self.assertNotEqual(first, second)
        self.assertTrue(first.endswith('/notes.pdf'))
        self.assertNotIn('..', storage_path_for('../../notes.pdf'))
