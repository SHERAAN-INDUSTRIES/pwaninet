"""Phase 6: processing retries, reconciliation, cleanup.

No upload may sit 'in progress' forever with nobody working it: the finalize
task promotes verified staging objects to public keys with retries, and the
beat-driven reconciler stalls out dead transfers and sweeps R2 debris.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from . import r2
from .models import UploadEvent, UploadRecord

logger = logging.getLogger(__name__)

STALLED_AFTER = timedelta(hours=2)
R2_DEBRIS_AFTER = timedelta(hours=24)

from django.conf import settings
dev_prefix = getattr(settings, "R2_DEV_PREFIX", "")
FINAL_PREFIX = f"{dev_prefix}/media/posts" if dev_prefix else "media/posts"

# File-signature table: (mime prefix or exact, magic check on head bytes).
# Unknown types pass (extension + MIME only); known types must match.
SIGNATURES = (
    ("image/jpeg", lambda h: h[:3] == b"\xff\xd8\xff"),
    ("image/png", lambda h: h[:4] == b"\x89PNG"),
    ("image/gif", lambda h: h[:4] in (b"GIF8",)),
    ("image/webp", lambda h: h[:4] == b"RIFF" and h[8:12] == b"WEBP"),
    ("application/pdf", lambda h: h[:4] == b"%PDF"),
    ("video/", lambda h: h[4:8] == b"ftyp" or h[:4] == b"\x00\x00\x00\x1c"),
    ("audio/mpeg", lambda h: h[:3] == b"ID3" or h[:2] == b"\xff\xfb"),
    ("audio/wav", lambda h: h[:4] == b"RIFF" and h[8:12] == b"WAVE"),
    ("audio/ogg", lambda h: h[:4] == b"OggS"),
)


def verify_signature(mime, head):
    mime = (mime or "").lower()
    for prefix, check in SIGNATURES:
        if mime == prefix or (prefix.endswith("/") and mime.startswith(prefix)):
            try:
                return bool(check(head or b""))
            except Exception:
                return False
    return True


def final_key_for(record):
    from django.conf import settings
    dev_prefix = getattr(settings, "R2_DEV_PREFIX", "")
    safe_name = (record.filename or "upload").replace("/", "_")[-180:]
    base_key = f"{FINAL_PREFIX}/{record.user_id}/{record.id}/{safe_name}"
    return f"{dev_prefix}/{base_key}" if dev_prefix else base_key


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def finalize_upload_media(self, record_id):
    """Copy the verified staging object to its public key and complete."""
    try:
        record = UploadRecord.objects.select_related("session").get(id=record_id)
    except UploadRecord.DoesNotExist:
        logger.warning("finalize: record %s gone, acking", record_id)
        return "gone"
    if record.status in ("completed", "cancelled") or record.deleted_at is not None:
        return "already-done"  # idempotent exit for redelivered tasks
    if not record.session.finalized:
        raise self.retry(exc=Exception("transfer not finalized yet"),
                         countdown=120)
    try:
        client = r2.get_r2_client()
    except r2.R2Unavailable as exc:
        raise self.retry(exc=exc, countdown=300)
    try:
        client.copy_object(
            CopySource={"Bucket": r2.get_bucket(), "Key": record.session.r2_key},
            Bucket=r2.get_bucket(),
            Key=final_key_for(record),
        )
    except Exception as exc:
        logger.warning("finalize copy failed for %s", record_id, exc_info=True)
        raise self.retry(exc=exc)
    attach_error = attach_media_to_post(record)
    if attach_error:
        with transaction.atomic():
            record = UploadRecord.objects.select_for_update().get(id=record_id)
            if record.status in ("completed", "cancelled"):
                return "already-done"
            record.transition_to(
                "failed", error_code="MEDIA_REJECTED", error_message=attach_error)
            record.save()
            UploadEvent.objects.create(
                record=record, event_type=UploadEvent.EventType.FAILED,
                detail={"error_code": "MEDIA_REJECTED"},
            )
        return "rejected"
    with transaction.atomic():
        record = UploadRecord.objects.select_for_update().get(id=record_id)
        if record.status == "completed":
            return "already-done"
        record.transition_to("publishing")
        record.save()
        record.transition_to("completed")
        record.progress_bytes = record.total_bytes
        record.save()
        UploadEvent.objects.create(
            record=record, event_type=UploadEvent.EventType.COMPLETED,
            detail={"final_key": final_key_for(record)},
        )
    return "completed"


def attach_media_to_post(record):
    """Verify file signature and materialize staged bytes into post media.

    Returns an error string on permanent rejection, else None. Images and
    docs/audio (bounded sizes) load into memory; video streams to a temp file
    and reuses the legacy local-spool + transcode pipeline.
    """
    from django.core.files.base import ContentFile
    from django.core.files import File as DjangoFile

    client = r2.get_r2_client()
    bucket = r2.get_bucket()
    key = record.session.r2_key
    try:
        head = client.get_object(Bucket=bucket, Key=key, Range="bytes=0-15")["Body"].read()
    except Exception:
        head = b""
    if not verify_signature(record.mime_type, head):
        return f"File signature does not match {record.mime_type or 'declared type'}."
    post = record.post
    if post is None:
        return "No post linked to this upload."
    mime = (record.mime_type or "").lower()
    safe_name = (record.filename or "upload").replace("/", "_")[-180:]
    try:
        if mime.startswith("image/"):
            from posts.models import PostImage
            body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
            PostImage.objects.create(post=post, image=ContentFile(body, name=safe_name))
        elif mime.startswith("video/"):
            import tempfile, os
            from posts.tasks import generate_video_poster, process_large_video
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(safe_name)[1])
            try:
                stream = client.get_object(Bucket=bucket, Key=key)["Body"]
                while True:
                    chunk = stream.read(4 * 1024 * 1024)
                    if not chunk:
                        break
                    tmp.write(chunk)
                tmp.close()
                with open(tmp.name, "rb") as fh:
                    post.video.save(safe_name, DjangoFile(fh), save=True)
            finally:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
            transaction.on_commit(lambda: process_large_video.delay(post.id))
            transaction.on_commit(lambda: generate_video_poster.delay(post.id))
        elif mime == "application/pdf":
            body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
            post.docs.save(safe_name, ContentFile(body), save=True)
        elif mime.startswith("audio/"):
            body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
            post.audio.save(safe_name, ContentFile(body), save=True)
    except Exception as exc:
        logger.warning("attach failed for %s", record.id, exc_info=True)
        return f"Media processing failed: {exc}"
    return None


@shared_task
def reconcile_stale_uploads():
    """Beat task: fail dead transfers, redrive stuck processing, sweep R2."""
    now = timezone.now()
    stalled = UploadRecord.objects.filter(
        status__in=("uploading", "verifying"),
        session__finalized=False,
        deleted_at__isnull=True,
        updated_at__lt=now - STALLED_AFTER,
    )
    for record in stalled.iterator():
        try:
            with transaction.atomic():
                record = UploadRecord.objects.select_for_update().get(id=record.id)
                if record.status not in ("uploading", "verifying"):
                    continue
                record.transition_to(
                    "failed", error_code="STALLED",
                    error_message="No progress for over 2 hours. Retry to resume.",
                )
                record.save()
                UploadEvent.objects.create(
                    record=record, event_type=UploadEvent.EventType.FAILED,
                    detail={"error_code": "STALLED"},
                )
        except Exception:
            logger.warning("reconcile: stall-mark failed for %s", record.id, exc_info=True)

    stuck = UploadRecord.objects.filter(
        status__in=("processing", "publishing"),
        deleted_at__isnull=True,
        updated_at__lt=now - STALLED_AFTER,
    )
    for record in stuck.iterator():
        if record.retry_count >= 3:
            with transaction.atomic():
                record = UploadRecord.objects.select_for_update().get(id=record.id)
                if record.status not in ("processing", "publishing"):
                    continue
                record.transition_to(
                    "failed", error_code="PROCESSING_EXHAUSTED",
                    error_message="Processing could not finish. Retry available.",
                )
                record.save()
        else:
            record.retry_count += 1
            record.save(update_fields=["retry_count", "updated_at"])
            transaction.on_commit(
                lambda rid=str(record.id): finalize_upload_media.delay(rid))

    try:
        sweep_r2_debris()
    except r2.R2Unavailable:
        pass
    except Exception:
        logger.warning("reconcile: R2 sweep failed", exc_info=True)
    return "ok"


def sweep_r2_debris():
    """Abort stale staging multiparts and delete staging copies of finished work."""
    client = r2.get_r2_client()
    bucket = r2.get_bucket()
    cutoff = timezone.now() - R2_DEBRIS_AFTER
    staging_prefix = r2.STAGING_PREFIX  # This already includes dev prefix from r2.py
    paginator = client.get_paginator("list_multipart_uploads")
    for page in paginator.paginate(Bucket=bucket, Prefix=staging_prefix):
        for upload in page.get("Uploads", []):
            initiated = upload.get("Initiated")
            if initiated is not None:
                if timezone.is_naive(initiated):
                    initiated = timezone.make_aware(initiated)
                if initiated > cutoff:
                    continue
            try:
                client.abort_multipart_upload(
                    Bucket=bucket, Key=upload["Key"], UploadId=upload["UploadId"])
            except Exception:
                logger.warning("sweep: abort failed for %s", upload.get("Key"), exc_info=True)
    finished_keys = UploadRecord.objects.filter(
        status__in=("completed", "cancelled", "failed"),
        updated_at__lt=cutoff,
        session__finalized=True,
    ).values_list("session__r2_key", flat=True)[:500]
    for key in finished_keys:
        try:
            client.delete_object(Bucket=bucket, Key=key)
        except Exception:
            logger.warning("sweep: delete failed for %s", key, exc_info=True)
