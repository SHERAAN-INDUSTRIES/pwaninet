"""Document uploads through the direct-to-R2 engine.

The bytes never pass through Django: the browser PUTs them into the bucket
with presigned URLs, the server verifies the parts, promotes the object and
writes the document rows. Processing then continues in the background.
"""

import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from documents.models import Document
from users.models import User

from .models import UploadRecord


def make_user(username="doc_uploader"):
    return User.objects.create_user(username=username, password="pass12345")


def mock_r2():
    client = MagicMock()
    client.create_multipart_upload.return_value = {"UploadId": "mpu-123"}
    client.generate_presigned_url.side_effect = lambda op, Params, ExpiresIn: (
        f"https://r2.test/{op}/{Params.get('PartNumber', 0)}"
    )
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Parts": [{"PartNumber": 1, "ETag": '"aaa"'}]}]
    client.get_paginator.return_value = paginator
    client.head_object.return_value = {"ContentLength": 1024}
    return client


@override_settings(USE_S3=True, AWS_STORAGE_BUCKET_NAME="test-bucket")
class DocumentUploadEngineTest(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _create(self, filename="notes.pdf", file_size=2048, document=None, **extra):
        payload = {
            "client_upload_id": str(uuid.uuid4()),
            "filename": filename,
            "mime_type": "application/pdf",
            "file_size": file_size,
            "kind": "document",
        }
        if document is not None:
            payload["document"] = document
        payload.update(extra)
        return self.client.post("/api/uploads/", payload, format="json")

    def _category_id(self):
        from documents.models import Category
        category, _ = Category.objects.get_or_create(
            code="other", defaults={"name": "Other", "is_active": True},
        )
        return str(category.pk)

    # --- session creation ----------------------------------------------------

    def test_document_kind_is_stored_on_the_record(self):
        response = self._create()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["kind"], "document")
        record = UploadRecord.objects.get(id=response.data["id"])
        self.assertEqual(record.kind, UploadRecord.KIND_DOCUMENT)

    def test_post_kind_is_the_default(self):
        response = self.client.post("/api/uploads/", {
            "client_upload_id": str(uuid.uuid4()),
            "filename": "clip.mp4",
            "mime_type": "video/mp4",
            "file_size": 1000,
        }, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["kind"], "post")

    def test_disallowed_extension_rejected_before_any_transfer(self):
        response = self._create(filename="payload.exe")
        self.assertEqual(response.status_code, 400)
        self.assertIn("filename", response.data)

    def test_oversized_document_rejected(self):
        response = self._create(file_size=60 * 1024 * 1024)
        self.assertEqual(response.status_code, 400)
        self.assertIn("file_size", response.data)

    def test_version_target_requires_document_share_id(self):
        response = self._create(document={"target": "version"})
        self.assertEqual(response.status_code, 400)

    # --- completion ----------------------------------------------------------

    def _complete(self, upload_id, document_payload):
        r2mock = mock_r2()
        with patch("uploads.views.r2.get_r2_client", return_value=r2mock):
            self.client.post(f"/api/uploads/{upload_id}/authorize/", {}, format="json")
            response = self.client.post(
                f"/api/uploads/{upload_id}/complete/",
                {"parts": [{"part_number": 1, "etag": "aaa"}], "document": document_payload},
                format="json",
            )
        return response, r2mock

    def test_complete_creates_document_and_queues_processing(self):
        category_id = self._category_id()
        created = self._create(document={"category": category_id})
        upload_id = created.data["id"]

        with patch("documents.tasks.processing.process_document.delay") as delay:
            # The pipeline is dispatched on commit - capture it explicitly.
            with self.captureOnCommitCallbacks(execute=True):
                response, r2mock = self._complete(upload_id, {
                    "title": "Intro notes",
                    "description": "week 1",
                    "category": category_id,
                    "academic_unit": "other",
                    "tags": ["notes", "intro"],
                })

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["kind"], "document")
        self.assertIsNotNone(response.data["document_id"])
        self.assertIsNotNone(response.data["document_share_id"])

        document = Document.objects.get(id=response.data["document_id"])
        self.assertEqual(document.title, "Intro notes")
        self.assertEqual(document.description, "week 1")
        self.assertEqual(document.status, "processing")
        self.assertEqual(document.uploaded_by, self.user)

        # The file row points at the promoted object, nothing was re-uploaded.
        document_file = document.latest_version.files.first()
        self.assertIsNotNone(document_file)
        self.assertEqual(document_file.extension, "pdf")
        self.assertEqual(document_file.processing_status, "pending")
        self.assertTrue(document_file.storage_path.startswith("documents/"))
        self.assertEqual(document_file.file.name, document_file.storage_path)

        # Tags are attached.
        self.assertEqual(document.document_tags.count(), 2)

        # Background pipeline was handed the document.
        delay.assert_called_once()
        self.assertEqual(delay.call_args[0][0], document.id)

        # The object was promoted inside the bucket (no bytes via Django).
        copy_args = r2mock.copy_object.call_args
        self.assertEqual(copy_args[1]["Bucket"], "test-bucket")
        self.assertTrue(copy_args[1]["Key"].endswith(document_file.storage_path))

        # And the record is complete and linked to the document.
        record = UploadRecord.objects.get(id=upload_id)
        self.assertEqual(record.status, "completed")
        self.assertEqual(record.document_id, document.id)

    def test_complete_is_idempotent(self):
        category_id = self._category_id()
        created = self._create(document={"category": category_id})
        upload_id = created.data["id"]

        with patch("documents.tasks.processing.process_document.delay"):
            with self.captureOnCommitCallbacks(execute=True):
                first, _ = self._complete(upload_id, {"category": category_id})
                second, _ = self._complete(upload_id, {"category": category_id})

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data["document_id"], second.data["document_id"])
        self.assertEqual(Document.objects.filter(uploaded_by=self.user).count(), 1)

    def test_complete_creates_a_new_version_on_an_existing_document(self):
        category_id = self._category_id()
        created = self._create(document={"category": category_id})
        upload_id = created.data["id"]
        with patch("documents.tasks.processing.process_document.delay"):
            with self.captureOnCommitCallbacks(execute=True):
                response, _ = self._complete(upload_id, {"category": category_id})
        document = Document.objects.get(id=response.data["document_id"])

        # A second upload targets the same document.
        version_created = self._create(document={
            "target": "version",
            "document_share_id": str(document.share_id),
            "change_notes": "fixed the typo",
        })
        version_upload_id = version_created.data["id"]
        with patch("documents.tasks.processing.process_document.delay"):
            with self.captureOnCommitCallbacks(execute=True):
                response2, _ = self._complete(version_upload_id, {
                    "target": "version",
                    "document_share_id": str(document.share_id),
                    "change_notes": "fixed the typo",
                })

        self.assertEqual(response2.status_code, 201)
        document.refresh_from_db()
        self.assertEqual(document.versions.count(), 2)
        self.assertEqual(document.versions.order_by("version_number").last().version_number, 2)
        self.assertEqual(
            document.versions.order_by("version_number").last().change_notes,
            "fixed the typo",
        )
        self.assertEqual(Document.objects.filter(uploaded_by=self.user).count(), 1)

    def test_blank_category_falls_back_to_other(self):
        """Same rule as the classic upload: no category means 'Other'."""
        created = self._create()
        upload_id = created.data["id"]
        with patch("documents.tasks.processing.process_document.delay"):
            response, _ = self._complete(upload_id, {"category": ""})
        self.assertEqual(response.status_code, 201)
        document = Document.objects.get(id=response.data["document_id"])
        self.assertIsNotNone(document.category_id)

    def test_complete_rejects_an_unknown_category(self):
        created = self._create()
        upload_id = created.data["id"]
        with patch("documents.tasks.processing.process_document.delay"):
            response, _ = self._complete(upload_id, {"category": "999999"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error_code"], "INVALID_DOCUMENT")
        record = UploadRecord.objects.get(id=upload_id)
        self.assertEqual(record.status, "failed")
        self.assertEqual(Document.objects.filter(uploaded_by=self.user).count(), 0)


@override_settings(USE_S3=True, AWS_STORAGE_BUCKET_NAME="test-bucket")
class TimetableUploadEngineTest(TestCase):
    """Timetable images go through the same engine: straight to the bucket,
    record created in PROCESSING state, AI extraction in the background."""

    def setUp(self):
        self.user = make_user("tt_user")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _create(self, filename="timetable_cropped.jpg", file_size=4096, **extra):
        payload = {
            "client_upload_id": str(uuid.uuid4()),
            "filename": filename,
            "mime_type": "image/jpeg",
            "file_size": file_size,
            "kind": "timetable",
        }
        payload.update(extra)
        return self.client.post("/api/uploads/", payload, format="json")

    def test_disallowed_image_type_rejected(self):
        response = self._create(filename="payload.exe")
        self.assertEqual(response.status_code, 400)

    def test_oversized_image_rejected(self):
        response = self._create(file_size=20 * 1024 * 1024)
        self.assertEqual(response.status_code, 400)

    def test_complete_without_a_programme_is_rejected(self):
        created = self._create()
        self.assertEqual(created.status_code, 201)
        upload_id = created.data["id"]
        r2mock = mock_r2()
        with patch("uploads.views.r2.get_r2_client", return_value=r2mock):
            self.client.post(f"/api/uploads/{upload_id}/authorize/", {}, format="json")
            response = self.client.post(
                f"/api/uploads/{upload_id}/complete/",
                {"parts": [{"part_number": 1, "etag": "aaa"}], "timetable": {}},
                format="json",
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error_code"], "NO_PROGRAMME")
        record = UploadRecord.objects.get(id=upload_id)
        self.assertEqual(record.status, "failed")

    def test_complete_creates_a_processing_timetable(self):
        from documents.academic.models import (
            AcademicLevel,
            Department,
            Faculty,
            Institution,
            Programme,
            School,
        )

        institution = Institution.objects.create(name="Engine Test University", short_name="ETU")
        faculty = Faculty.objects.create(code="ETF", name="Engine Faculty", institution=institution)
        school = School.objects.create(code="ETS", name="Engine School", faculty=faculty)
        department = Department.objects.create(code="ETD", name="Engine Dept", school=school)
        programme = Programme.objects.create(
            code="ETP", name="Engine Programme", department=department,
            degree_type="Bachelor", duration_years=4,
        )
        level = AcademicLevel.objects.filter(level=1).first() or AcademicLevel.objects.create(
            level=1, name="Year 1",
        )
        self.user.programme = programme
        self.user.academic_level = level
        self.user.save()

        created = self._create()
        upload_id = created.data["id"]
        r2mock = mock_r2()
        with patch("uploads.views.r2.get_r2_client", return_value=r2mock):
            self.client.post(f"/api/uploads/{upload_id}/authorize/", {}, format="json")
            with patch(
                "documents.tasks.timetable.process_timetable_image_task.apply_async"
            ) as task:
                response = self.client.post(
                    f"/api/uploads/{upload_id}/complete/",
                    {
                        "parts": [{"part_number": 1, "etag": "aaa"}],
                        "timetable": {"programme": str(programme.pk)},
                    },
                    format="json",
                )

        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data.get("timetable_id"))

        from documents.academic.models import ProgrammeTimetable
        timetable = ProgrammeTimetable.objects.get(id=response.data["timetable_id"])
        self.assertEqual(timetable.status, "PROCESSING")
        self.assertTrue(timetable.source_image.name.startswith("timetables/images/"))
        task.assert_called_once()

        record = UploadRecord.objects.get(id=upload_id)
        self.assertEqual(record.status, "completed")
