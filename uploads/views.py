"""Phase 1 upload-session API: create/recover, status, history, events.

Transfer authorization (presigned R2 URLs) and idempotent completion land in
Phase 2; every shape here already matches the approved spec so clients can
build against it.
"""

import logging

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import r2
from .models import UploadEvent, UploadPostGroup, UploadRecord, UploadSession
from .tasks import finalize_upload_media
from .serializers import (
    AuthorizeSerializer,
    CompleteSerializer,
    UploadCreateSerializer,
    UploadEventSerializer,
    UploadRecordSerializer,
)

logger = logging.getLogger(__name__)


def _r2_key(user, client_upload_id, filename):
    from django.conf import settings
    dev_prefix = getattr(settings, "R2_DEV_PREFIX", "")
    safe_name = (filename or "upload").replace("/", "_")[-180:]
    base_key = f"media/uploads/staging/{user.id}/{client_upload_id}/{safe_name}"
    return f"{dev_prefix}/{base_key}" if dev_prefix else base_key


class UploadCollectionView(APIView):
    """GET lists history (?status=, ?page=, ?page_size=); POST creates/recovers."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = UploadRecord.objects.filter(
            user=request.user, deleted_at__isnull=True,
        ).select_related("session", "post", "document")
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        try:
            page_size = min(max(int(request.query_params.get("page_size", 20)), 1), 100)
            page = max(int(request.query_params.get("page", 1)), 1)
        except ValueError:
            page_size, page = 20, 1
        total = qs.count()
        records = qs[(page - 1) * page_size:page * page_size]
        return Response({
            "count": total,
            "page": page,
            "page_size": page_size,
            "results": UploadRecordSerializer(records, many=True).data,
        })

    def post(self, request):
        serializer = UploadCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            with transaction.atomic():
                record, created = UploadRecord.objects.get_or_create(
                    user=request.user,
                    client_upload_id=data["client_upload_id"],
                    defaults={
                        "filename": data["filename"],
                        "mime_type": data.get("mime_type", ""),
                        "file_size": data["file_size"],
                        "sha256": data.get("sha256", ""),
                        "total_bytes": data["file_size"],
                        "post_group": data.get("post_group"),
                        "kind": data.get("kind", UploadRecord.KIND_POST),
                    },
                )
                if created:
                    protocol, total_parts = UploadSession.plan_protocol(data["file_size"])
                    UploadSession.objects.create(
                        record=record,
                        protocol=protocol,
                        r2_key=_r2_key(request.user, data["client_upload_id"], data["filename"]),
                        total_parts=total_parts,
                    )
                    UploadEvent.objects.create(record=record, event_type=UploadEvent.EventType.CREATED)
                    http_status = status.HTTP_201_CREATED
                else:
                    UploadEvent.objects.create(
                        record=record,
                        event_type=UploadEvent.EventType.RECOVERED,
                        detail={"status": record.status},
                    )
                    http_status = status.HTTP_200_OK
            if record.deleted_at is not None:
                # Tombstone: history was deleted; a fresh client UUID is required.
                return Response(
                    {"error_code": "UPLOAD_DELETED",
                     "detail": "This upload history was deleted. Start a new upload."},
                    status=status.HTTP_410_GONE,
                )
        except IntegrityError:
            # Lost a get_or_create race: fetch the winner and treat as recovery.
            record = UploadRecord.objects.get(
                user=request.user, client_upload_id=data["client_upload_id"]
            )
            UploadEvent.objects.create(
                record=record,
                event_type=UploadEvent.EventType.RECOVERED,
                detail={"status": record.status, "raced": True},
            )
            http_status = status.HTTP_200_OK
        return Response(
            UploadRecordSerializer(record).data, status=http_status,
        )


class UploadDetailView(generics.RetrieveAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = UploadRecordSerializer

    def retrieve(self, request, *args, **kwargs):
        try:
            record = UploadRecord.objects.select_related("session", "post").get(
                id=self.kwargs["id"], user=request.user,
            )
        except UploadRecord.DoesNotExist:
            return Response(
                {"error_code": "NOT_FOUND",
                 "detail": "No UploadRecord matches the given query."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if record.deleted_at is not None:
            return Response(
                {"error_code": "UPLOAD_DELETED",
                 "detail": "This upload was deleted. Start a new upload."},
                status=status.HTTP_410_GONE,
            )
        serializer = self.get_serializer(record)
        return Response(serializer.data)


class UploadEventsView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = UploadEventSerializer

    def get_queryset(self):
        record = get_object_or_404(
            UploadRecord, id=self.kwargs["id"], user=self.request.user,
        )
        return record.events.all()


def _r2_503():
    return Response(
        {"error_code": "R2_NOT_CONFIGURED", "detail": "Direct upload is unavailable."},
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


class UploadAuthorizeView(APIView):
    """Mint short-lived presigned URLs. Re-call on expiry; never restarts."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id):
        import traceback
        try:
            record = get_object_or_404(
                UploadRecord.objects.select_related("session"),
                id=id, user=request.user,
            )
            if record.deleted_at is not None:
                return Response(
                    {"error_code": "UPLOAD_DELETED",
                     "detail": "This upload was deleted. Start a new upload."},
                    status=status.HTTP_410_GONE,
                )
            serializer = AuthorizeSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            try:
                client = r2.get_r2_client()
            except r2.R2Unavailable as e:
                logger.error(f"[UploadAuthorizeView] R2 unavailable: {e}")
                return _r2_503()
            bucket = r2.get_bucket()
            session = record.session

            with transaction.atomic():
                record = UploadRecord.objects.select_for_update().get(id=record.id)
                if record.status == "queued":
                    record.transition_to("preparing")
                if record.status in ("preparing", "paused", "failed"):
                    record.transition_to("uploading")
                if record.status != "uploading":
                    return Response(
                        {"error_code": "NOT_UPLOADING",
                         "detail": f"Cannot authorize in status {record.status}."},
                        status=status.HTTP_409_CONFLICT,
                    )
                record.save()
                if session.protocol == UploadSession.PROTOCOL_S3_MULTIPART:
                    if not session.r2_upload_id:
                        session.r2_upload_id = r2.ensure_multipart(
                            client, bucket, session.r2_key)
                session.expires_at = r2.credential_expiry()
                session.save(update_fields=["r2_upload_id", "expires_at", "updated_at"])
                UploadEvent.objects.create(
                    record=record, event_type=UploadEvent.EventType.AUTHORIZED,
                    detail={"protocol": session.protocol},
                )

            if session.protocol == UploadSession.PROTOCOL_SINGLE_PUT:
                urls = [{
                    "part_number": 1,
                    "url": str(r2.presign_single_put(
                        client, bucket, session.r2_key, record.mime_type)),
                }]
            else:
                wanted = serializer.validated_data.get("parts") or list(
                    range(1, session.total_parts + 1))
                urls = [{
                    "part_number": n,
                    "url": str(r2.presign_part(
                        client, bucket, session.r2_key, session.r2_upload_id, n)),
                } for n in wanted]
            return Response({
                "upload_id": session.r2_upload_id,
                "protocol": session.protocol,
                "part_size": session.part_size,
                "total_parts": session.total_parts,
                "parts": urls,
                "expires_at": session.expires_at,
            })
        except Exception as e:
            logger.error(f"[UploadAuthorizeView] Unexpected error: {e}\n{traceback.format_exc()}")
            raise


class UploadCompleteView(APIView):
    """Verify against R2, finalize, create/link the post. Fully idempotent."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id):
        serializer = CompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            client = r2.get_r2_client()
        except r2.R2Unavailable:
            return _r2_503()
        bucket = r2.get_bucket()

        with transaction.atomic():
            record = UploadRecord.objects.select_for_update().get(
                id=id, user=request.user,
            )
            if record.deleted_at is not None:
                return Response(
                    {"error_code": "DELETED", "detail": "Upload was deleted."},
                    status=status.HTTP_410_GONE,
                )
            if record.post_id is not None:
                # Already finalized: second call returns the same post, always.
                return Response(self._done_payload(record), status=status.HTTP_200_OK)
            if record.document_id is not None:
                # Already finalized: second call returns the same document.
                return Response(self._done_payload(record), status=status.HTTP_200_OK)
            if record.status not in ("uploading", "paused", "failed", "verifying"):
                return Response(
                    {"error_code": "BAD_STATE",
                     "detail": f"Cannot complete from status {record.status}."},
                    status=status.HTTP_409_CONFLICT,
                )
            if record.status != "verifying":
                record.transition_to("verifying")
                record.save()
            session = record.session

            if session.protocol == UploadSession.PROTOCOL_S3_MULTIPART:
                claimed = {
                    p["part_number"]: p["etag"].strip('"')
                    for p in serializer.validated_data.get("parts", [])
                }
                verified = r2.list_verified_parts(
                    client, bucket, session.r2_key, session.r2_upload_id)
                missing = [
                    n for n in range(1, session.total_parts + 1)
                    if verified.get(n) != claimed.get(n)
                ]
                if missing:
                    record.transition_to("uploading")
                    record.save()
                    UploadEvent.objects.create(
                        record=record,
                        event_type=UploadEvent.EventType.RETRY_SCHEDULED,
                        detail={"reason": "parts_mismatch", "missing": missing},
                    )
                    return Response(
                        {"error_code": "PARTS_MISMATCH",
                         "detail": "Some parts failed server verification.",
                         "verified_parts": sorted(verified),
                         "missing_parts": missing},
                        status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    )
                r2.complete_multipart(
                    client, bucket, session.r2_key, session.r2_upload_id,
                    [{"PartNumber": n, "ETag": f'"{verified[n]}"'}
                     for n in range(1, session.total_parts + 1)],
                )
            else:
                if not r2.head_object_ok(client, bucket, session.r2_key):
                    return Response(
                        {"error_code": "OBJECT_MISSING",
                         "detail": "Uploaded object not found on R2."},
                        status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    )
            session.finalized = True
            session.save(update_fields=["finalized", "updated_at"])

            if record.kind == UploadRecord.KIND_DOCUMENT:
                return self._complete_document(
                    request, record, session, serializer.validated_data
                )

            if record.kind == UploadRecord.KIND_TIMETABLE:
                return self._complete_timetable(
                    request, record, session, serializer.validated_data
                )

            post = self._publish(record, serializer.validated_data.get("post", {}))
            record.transition_to("processing")
            record.progress_bytes = record.total_bytes
            record.save()
            UploadEvent.objects.create(
                record=record, event_type=UploadEvent.EventType.FINALIZED,
                detail={"r2_key": session.r2_key},
            )
            record_id = str(record.id)
            transaction.on_commit(
                lambda: finalize_upload_media.delay(record_id))
            return Response(self._done_payload(record), status=status.HTTP_201_CREATED)

    def _complete_document(self, request, record, session, data):
        """Turn a verified R2 object into a Document (or a new version of one).

        The bytes already live in the bucket, so this only promotes the object
        to its public key, writes the document rows, and queues the background
        pipeline. The caller gets an answer immediately and can carry on.
        """
        from documents.services import direct_upload_service as doc_service
        from documents.services.direct_upload_service import DocumentUploadError

        metadata = data.get("document") or {}
        bucket = r2.get_bucket()
        client = r2.get_r2_client()

        # Promote the staging object to the public documents key. This is a
        # server-side copy inside the bucket - no bytes cross the app server.
        storage_path = self._available_storage_path(
            doc_service.storage_path_for(record.filename)
        )
        final_key = self._public_object_key(storage_path)
        try:
            r2.promote_object(client, bucket, session.r2_key, final_key)
        except Exception as exc:
            logger.error("Failed to promote upload %s to %s: %s", record.id, final_key, exc)
            record.transition_to("failed", error_code="PROMOTE_FAILED", error_message=str(exc))
            record.save()
            return Response(
                {"error_code": "PROMOTE_FAILED",
                 "detail": "Could not store the uploaded file."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # Create the document rows (fast) - heavy processing happens in Celery.
        try:
            if metadata.get("target") == "version":
                document = self._target_document(request, metadata)
                doc_service.create_version_from_stored_object(
                    document=document,
                    user=request.user,
                    storage_path=storage_path,
                    filename=record.filename,
                    mime_type=record.mime_type,
                    size_bytes=record.file_size,
                    checksum=record.sha256,
                    change_notes=metadata.get("change_notes") or "",
                )
            else:
                document, _version = doc_service.create_document_from_stored_object(
                    user=request.user,
                    storage_path=storage_path,
                    filename=record.filename,
                    mime_type=record.mime_type,
                    size_bytes=record.file_size,
                    checksum=record.sha256,
                    metadata=metadata,
                    change_notes=metadata.get("change_notes") or "",
                )
        except DocumentUploadError as exc:
            record.transition_to("failed", error_code="INVALID_DOCUMENT", error_message=str(exc))
            record.save()
            UploadEvent.objects.create(
                record=record, event_type=UploadEvent.EventType.FAILED,
                detail={"reason": "invalid_document"},
            )
            r2.delete_object(client, bucket, session.r2_key)
            return Response(
                {"error_code": "INVALID_DOCUMENT", "detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except PermissionError as exc:
            record.transition_to("failed", error_code="FORBIDDEN", error_message=str(exc))
            record.save()
            r2.delete_object(client, bucket, session.r2_key)
            return Response(
                {"error_code": "FORBIDDEN", "detail": str(exc)},
                status=status.HTTP_403_FORBIDDEN,
            )
        except Exception as exc:
            logger.error("Failed to create document for upload %s: %s", record.id, exc, exc_info=True)
            record.transition_to("failed", error_code="DOCUMENT_FAILED", error_message=str(exc))
            record.save()
            r2.delete_object(client, bucket, session.r2_key)
            return Response(
                {"error_code": "DOCUMENT_FAILED", "detail": "Could not create the document."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        record.document = document
        record.transition_to("processing")
        record.progress_bytes = record.total_bytes
        record.save()
        UploadEvent.objects.create(
            record=record, event_type=UploadEvent.EventType.FINALIZED,
            detail={"r2_key": final_key, "document_id": str(document.id)},
        )
        record.transition_to("publishing")
        record.save()
        # Background pipeline: preview, thumbnail, OCR, search indexing.
        doc_service.queue_processing(document)
        record.transition_to("completed")
        record.save()
        UploadEvent.objects.create(
            record=record, event_type=UploadEvent.EventType.COMPLETED,
            detail={"document_id": str(document.id)},
        )
        # The staging copy has served its purpose.
        r2.delete_object(client, bucket, session.r2_key)
        return Response(self._done_payload(record), status=status.HTTP_201_CREATED)

    def _complete_timetable(self, request, record, session, data):
        """Turn a verified R2 object into a timetable record.

        The image is already in the bucket; the record is created in
        PROCESSING state and the AI extraction runs in the background.
        """
        from documents.academic.services import StudentAcademicEnrollmentService
        from documents.academic.timetable_service import ProgrammeTimetableManager
        from documents.academic.models import Institution

        metadata = data.get("timetable") or {}
        bucket = r2.get_bucket()
        client = r2.get_r2_client()

        storage_path = self._available_storage_path(
            f"timetables/images/{timezone.now():%Y/%m}/{record.filename}"
        )
        final_key = self._public_object_key(storage_path)
        try:
            r2.promote_object(client, bucket, session.r2_key, final_key)
        except Exception as exc:
            logger.error("Failed to promote timetable upload %s: %s", record.id, exc)
            record.transition_to("failed", error_code="PROMOTE_FAILED", error_message=str(exc))
            record.save()
            return Response(
                {"error_code": "PROMOTE_FAILED", "detail": "Could not store the uploaded image."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        user = request.user
        programme = None
        raw_programme = (metadata.get("programme") or "").strip()
        if raw_programme and str(raw_programme).isdigit():
            from documents.academic.models import Programme
            programme = Programme.objects.filter(pk=int(raw_programme), is_active=True).first()
        if not programme:
            programme = getattr(user, "programme", None)
        if not programme:
            record.transition_to("failed", error_code="NO_PROGRAMME", error_message="No programme")
            record.save()
            r2.delete_object(client, bucket, session.r2_key)
            return Response(
                {"error_code": "NO_PROGRAMME",
                 "detail": "Please set your academic programme before uploading a timetable."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        academic_level = None
        raw_level = (metadata.get("academic_level") or "").strip()
        if raw_level and str(raw_level).isdigit():
            academic_level = StudentAcademicEnrollmentService.resolve_or_ensure_academic_level(int(raw_level))
        if not academic_level:
            academic_level = getattr(user, "academic_level", None) or \
                StudentAcademicEnrollmentService.resolve_or_ensure_academic_level(1)

        semester = None
        raw_sem = (metadata.get("semester") or "").strip()
        if raw_sem and str(raw_sem).isdigit():
            from documents.academic.models import Semester
            semester = Semester.objects.filter(pk=int(raw_sem)).first()
        if not semester:
            semester = getattr(user, "semester", None)

        institution = getattr(user, "institution", None)
        if not institution:
            institution = Institution.objects.filter(is_active=True).first()

        try:
            timetable = ProgrammeTimetableManager.save_timetable_image_from_storage(
                user=user,
                storage_path=storage_path,
                institution=institution,
                programme=programme,
                academic_level=academic_level,
                semester=semester,
                academic_year=getattr(user, "academic_year", None),
                change_summary=(metadata.get("change_summary") or "").strip(),
            )
        except Exception as exc:
            logger.error("Failed to create timetable for upload %s: %s", record.id, exc, exc_info=True)
            record.transition_to("failed", error_code="TIMETABLE_FAILED", error_message=str(exc))
            record.save()
            r2.delete_object(client, bucket, session.r2_key)
            return Response(
                {"error_code": "TIMETABLE_FAILED", "detail": "Could not create the timetable."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        record.transition_to("processing")
        record.progress_bytes = record.total_bytes
        record.save()
        UploadEvent.objects.create(
            record=record, event_type=UploadEvent.EventType.FINALIZED,
            detail={"r2_key": final_key, "timetable_id": timetable.id},
        )
        record.transition_to("publishing")
        record.save()
        record.transition_to("completed")
        record.save()
        UploadEvent.objects.create(
            record=record, event_type=UploadEvent.EventType.COMPLETED,
            detail={"timetable_id": timetable.id},
        )
        r2.delete_object(client, bucket, session.r2_key)
        return Response(
            dict(self._done_payload(record), timetable_id=timetable.id, success=True),
            status=status.HTTP_201_CREATED,
        )

    @staticmethod
    def _target_document(request, metadata):
        from documents.models import Document

        share_id = metadata.get("document_share_id")
        document = Document.objects.filter(
            share_id=share_id, uploaded_by=request.user,
        ).first()
        if not document:
            raise PermissionError("Document not found or not yours.")
        return document

    @staticmethod
    def _public_object_key(storage_path):
        """Full bucket key for a storage-relative path (honours the R2 prefix)."""
        from django.core.files.storage import default_storage

        location = (getattr(default_storage, "location", "") or "").strip("/")
        return f"{location}/{storage_path}" if location else storage_path

    @staticmethod
    def _available_storage_path(storage_path):
        """Never overwrite an existing object.

        Two people can upload `notes.pdf` on the same day; the storage picks
        a free name (same rule the classic upload path used) so the second
        one does not silently replace the first.
        """
        from django.core.files.storage import default_storage

        try:
            return default_storage.get_available_name(storage_path)
        except Exception:
            logger.warning("Could not check name availability for %s", storage_path, exc_info=True)
            return storage_path

    @staticmethod
    def _publish(record, post_data):
        from posts.models import Post
        post_data = post_data or {}
        group = None
        group_id = post_data.get("group")
        if group_id:
            from groups.models import Group, Membership
            try:
                group = Group.objects.get(id=group_id)
            except Group.DoesNotExist:
                from rest_framework.exceptions import ValidationError as DRFValidationError
                raise DRFValidationError({"post": {"group": "Unknown group."}})
            approved = Membership.objects.filter(
                group=group, user=record.user, status="APPROVED",
            ).exists()
            if not approved:
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("You are not a member of this group.")
        if record.post_group:
            group_row = UploadPostGroup.objects.select_for_update().get_or_create(
                user=record.user,
                group_id=record.post_group,
            )[0]
            if group_row.post_id is None:
                group_row.post = UploadCompleteView._build_post(record, post_data, group)
                group_row.save(update_fields=["post"])
            record.post = group_row.post
            return record.post
        record.post = UploadCompleteView._build_post(record, post_data, group)
        return record.post

    @staticmethod
    def _build_post(record, post_data, group):
        from posts.models import Post
        kwargs = {
            "author": record.user,
            "content": post_data.get("content", ""),
            "visibility": post_data.get("visibility", "AUTHENTICATED"),
            "allow_downloads": post_data.get("allow_downloads", True),
        }
        # Provisional reel dims from client preview metadata; ffprobe in
        # process_large_video overwrites with measured truth on ready.
        for field in ("video_width", "video_height", "video_duration"):
            value = post_data.get(field)
            if isinstance(value, int) and value > 0:
                kwargs[field] = value
        if group is not None:
            kwargs["group"] = group
        unit_id = post_data.get("unit")
        if unit_id:
            from courses.models import Unit
            kwargs["unit"] = Unit.objects.filter(id=unit_id).first()
            if kwargs["unit"] is None:
                from rest_framework.exceptions import ValidationError as DRFValidationError
                raise DRFValidationError({"post": {"unit": "Unknown unit."}})
        gradient = post_data.get("gradient_class") or ""
        if gradient:
            kwargs["gradient_class"] = gradient
        return Post.objects.create(**{k: v for k, v in kwargs.items() if v is not None})

    @staticmethod
    def _done_payload(record):
        post = record.post
        document = record.document
        return {
            "id": str(record.id),
            "status": record.status,
            "kind": record.kind,
            "post_id": record.post_id,
            "post_share_id": str(post.share_id) if post else None,
            "is_reel": bool(post.is_reel) if post else False,
            "document_id": record.document_id,
            "document_share_id": str(document.share_id) if document else None,
            "document_status": document.status if document else None,
        }


class UploadRetryView(APIView):
    """Resume at the correct step based on verified server state. Never dupes."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id):
        with transaction.atomic():
            record = UploadRecord.objects.select_for_update().get(
                id=id, user=request.user,
            )
            if record.status not in ("paused", "failed"):
                return Response(
                    UploadRecordSerializer(record).data, status=status.HTTP_200_OK)
            target = ("processing" if record.session.finalized
                      else "uploading")
            record.transition_to(target)
            record.retry_count += 1
            record.error_code = ""
            record.error_message = ""
            record.save()
            UploadEvent.objects.create(
                record=record, event_type=UploadEvent.EventType.RETRY_SCHEDULED,
                detail={"target": target, "attempt": record.retry_count},
            )
            UploadEvent.objects.create(
                record=record, event_type=UploadEvent.EventType.RESUMED,
                detail={"target": target},
            )
            return Response(
                UploadRecordSerializer(record).data, status=status.HTTP_200_OK)


class UploadCancelView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id):
        with transaction.atomic():
            record = UploadRecord.objects.select_for_update().get(
                id=id, user=request.user,
            )
            if record.status in ("completed", "cancelled"):
                return Response(
                    UploadRecordSerializer(record).data, status=status.HTTP_200_OK)
            record.transition_to("cancelled")
            record.save()
            UploadEvent.objects.create(
                record=record, event_type=UploadEvent.EventType.CANCELLED)
            post_id = record.post_id
        session = record.session
        if session.protocol == UploadSession.PROTOCOL_S3_MULTIPART and session.r2_upload_id:
            try:
                r2.abort_multipart(
                    r2.get_r2_client(), r2.get_bucket(),
                    session.r2_key, session.r2_upload_id)
            except r2.R2Unavailable:
                pass
        # Cancelling mid-transcode must nuke the post too, or Celery keeps
        # burning on media the user rejected (guard self-aborts the tasks).
        if post_id:
            live = UploadRecord.objects.filter(
                post_id=post_id, deleted_at__isnull=True,
            ).exclude(status="cancelled").exists()
            if not live:
                _delete_upload_post(post_id)
        return Response(
            UploadRecordSerializer(record).data, status=status.HTTP_200_OK)


class UploadDeleteView(APIView):
    """Soft-delete history. The tombstone blocks UUID reuse; R2 staging is
    aborted best-effort and finished copies are swept by the reconciler."""

    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, id):
        return self._soft_delete(request, id)

    def post(self, request, id):
        # Alias for clients (e.g. cached manager_page.js) that send POST.
        return self._soft_delete(request, id)

    def _soft_delete(self, request, id):
        with transaction.atomic():
            record = UploadRecord.objects.select_for_update().get(
                id=id, user=request.user,
            )
            if record.deleted_at is not None:
                return Response(status=status.HTTP_204_NO_CONTENT)
            if record.status not in ("completed", "cancelled", "failed"):
                try:
                    record.transition_to("cancelled")
                except Exception:
                    pass
            record.deleted_at = timezone.now()
            record.save(update_fields=["status", "completed_at", "deleted_at", "updated_at"])
            UploadEvent.objects.create(
                record=record, event_type=UploadEvent.EventType.DELETED)
            post_id = record.post_id
        session = record.session
        if session.protocol == UploadSession.PROTOCOL_S3_MULTIPART and session.r2_upload_id:
            try:
                r2.abort_multipart(
                    r2.get_r2_client(), r2.get_bucket(),
                    session.r2_key, session.r2_upload_id)
            except r2.R2Unavailable:
                pass
        # Cancelling/deleting must stop the waste, not just hide history:
        # an in-flight transcode keeps burning CPU + R2 space otherwise.
        # A COMPLETED (ready) post is done work: deleting its card only
        # clears history and keeps the post + media. Anything unfinished
        # deletes the linked post too (when no other live upload record
        # still references it). In-flight Celery tasks also self-abort
        # via _source_upload_dead() once this lands.
        if post_id and record.status != "completed":
            live = UploadRecord.objects.filter(
                post_id=post_id, deleted_at__isnull=True,
            ).exclude(status="cancelled").exists()
            if not live:
                _delete_upload_post(post_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _delete_upload_post(post_id):
    """Delete a post born from an upload plus its media and HLS output.

    Best-effort everywhere: never let a storage error fail the delete.
    """
    from posts.models import PostImage
    try:
        from posts.models import Post
        post = Post.objects.get(id=post_id)
    except Exception:
        return
    storage_fields = (
        "video", "video_preview", "video_poster",
        "docs", "audio", "thumbnail",
    )
    try:
        for image in post.images.all():
            try:
                if image.image:
                    image.image.delete(save=False)
            except Exception:
                pass
        for field_name in storage_fields:
            try:
                field = getattr(post, field_name, None)
                if field:
                    field.delete(save=False)
            except Exception:
                pass
        _delete_post_hls(post_id)
    finally:
        try:
            post.delete()
        except Exception:
            pass


def _delete_post_hls(post_id):
    """Remove HLS output for a post locally and on R2 (best-effort)."""
    import os
    import shutil
    from django.conf import settings
    rel_dir = os.path.join("posts", "videos", str(post_id), "hls")
    try:
        shutil.rmtree(os.path.join(settings.MEDIA_ROOT, rel_dir), ignore_errors=True)
    except Exception:
        pass
    try:
        client = r2.get_r2_client()
        bucket = r2.get_bucket()
        prefix = f"media/{rel_dir.replace(os.sep, '/')}/"
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                try:
                    client.delete_object(Bucket=bucket, Key=obj["Key"])
                except Exception:
                    pass
    except Exception:
        pass
