"""Direct-to-R2 transfer helpers (Phase 2).

Django never touches media bytes: it mints short-lived presigned URLs, R2
receives the parts, and completion is verified with ListParts before the
multipart upload is finalized. Staging keys live under an unguessable
per-upload prefix; only verified objects are ever promoted to public paths.
"""

import logging

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

PRESIGNED_URL_TTL_SECONDS = 15 * 60
# Use dev prefix if configured to separate development uploads
dev_prefix = getattr(settings, "R2_DEV_PREFIX", "")
STAGING_PREFIX = f"{dev_prefix}/media/uploads/staging" if dev_prefix else "media/uploads/staging"


class R2Unavailable(Exception):
    pass


def get_r2_client():
    if not getattr(settings, "USE_S3", False):
        raise R2Unavailable("R2 is not configured (USE_S3=0)")
    try:
        import boto3
    except ImportError as exc:
        raise R2Unavailable("boto3 is not installed") from exc
    return boto3.client("s3", **settings.R2_BOTO3_CONFIG)


def get_bucket():
    return settings.AWS_STORAGE_BUCKET_NAME


def ensure_multipart(client, bucket, key):
    """Return the active multipart upload ID for key, creating one if needed."""
    listed = client.list_multipart_uploads(Bucket=bucket, Prefix=key)
    for upload in listed.get("Uploads", []):
        if upload["Key"] == key:
            return upload["UploadId"]
    created = client.create_multipart_upload(Bucket=bucket, Key=key)
    return created["UploadId"]


def presign_part(client, bucket, key, upload_id, part_number, content_md5=None):
    params = {
        "Bucket": bucket,
        "Key": key,
        "UploadId": upload_id,
        "PartNumber": part_number,
    }
    if content_md5:
        params["ContentMD5"] = content_md5
    return client.generate_presigned_url(
        "upload_part",
        Params=params,
        ExpiresIn=PRESIGNED_URL_TTL_SECONDS,
    )


def presign_single_put(client, bucket, key, mime_type=""):
    params = {"Bucket": bucket, "Key": key}
    if mime_type:
        params["ContentType"] = mime_type
    return client.generate_presigned_url(
        "put_object",
        Params=params,
        ExpiresIn=PRESIGNED_URL_TTL_SECONDS,
    )


def list_verified_parts(client, bucket, key, upload_id):
    """Server-side truth: part numbers + ETags actually held by R2."""
    paginator = client.get_paginator("list_parts")
    verified = {}
    for page in paginator.paginate(Bucket=bucket, Key=key, UploadId=upload_id):
        for part in page.get("Parts", []):
            verified[part["PartNumber"]] = part["ETag"].strip('"')
    return verified


def complete_multipart(client, bucket, key, upload_id, parts):
    return client.complete_multipart_upload(
        Bucket=bucket,
        Key=key,
        UploadId=upload_id,
        MultipartUpload={"Parts": parts},
    )


def abort_multipart(client, bucket, key, upload_id):
    try:
        client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
    except Exception:
        logger.warning("R2 abort failed for %s (non-fatal)", key, exc_info=True)


def head_object_ok(client, bucket, key):
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def promote_object(client, bucket, staging_key, final_key):
    """Move a verified staging object to its public key.

    This is a server-side copy inside the bucket: the bytes never travel
    through the application server.
    """
    client.copy_object(
        CopySource={"Bucket": bucket, "Key": staging_key},
        Bucket=bucket,
        Key=final_key,
    )
    return final_key


def delete_object(client, bucket, key):
    """Best-effort cleanup of a staging object."""
    try:
        client.delete_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        logger.warning("R2 delete failed for %s (non-fatal)", key, exc_info=True)
        return False


def credential_expiry():
    return timezone.now() + timezone.timedelta(seconds=PRESIGNED_URL_TTL_SECONDS)
