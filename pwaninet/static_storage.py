"""
Static file storage with automatic, content-derived cache busting.

Why this module exists
----------------------
Production serves static assets through Cloudflare, which caches them for a
very long time (``Cache-Control: public, max-age=315360000, immutable``).
The only thing that ever changed the cache key was the manual
``?v={{ app_version }}-{{ app_build }}`` suffix in the templates, and that
value comes from the database release record.

That release record was frozen at ``1.14.3-154`` (created 2026-10-17) while
CSS and JS kept changing every day. The URLs therefore stayed identical
across deploys, so Cloudflare kept serving the 2026-10-13 copy of every
stylesheet and script -- the deployed code was correct, the *assets* were
years old.

``whitenoise.storage.CompressedManifestStaticFilesStorage`` was supposed to
prevent this by hashing file names, but Django 4.2+ ignores the legacy
``STATICFILES_STORAGE`` setting whenever ``STORAGES`` is defined, and this
project defines ``STORAGES``. The hashing backend was therefore dead code
that never ran.

What this module does
---------------------
It keeps file names stable -- so the hard-coded ``/static/...`` references
(PWA icons, the web manifest) keep working -- and appends a fingerprint of
the static tree to every URL produced by ``{% static %}``. Change any
stylesheet or script and the URL changes with it, so the browser, the
service worker and Cloudflare all fetch the new file.

The fingerprint is derived from the *content* of every collected static file.
Hashing content rather than modification times matters: ``collectstatic``
rewrites app-provided files (admin, rest_framework) whenever the image is
rebuilt, and a timestamp-based fingerprint would then change on every deploy
even when no asset had actually changed.
"""

import hashlib
import os
from functools import lru_cache

from django.contrib.staticfiles.storage import StaticFilesStorage


def _hash_file(path):
    """sha256 of a file, read in chunks so large fonts do not blow memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _walk(root):
    """Yield ``(relative_path, sha256)`` for every file under root."""
    if not root or not os.path.isdir(root):
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            try:
                content_hash = _hash_file(path)
            except OSError:
                # File vanished mid-walk or is unreadable; skip it rather
                # than failing the whole request.
                continue
            yield os.path.relpath(path, root), content_hash


@lru_cache(maxsize=8)
def _fingerprint(root):
    digest = hashlib.sha256()
    count = 0
    for relpath, content_hash in _walk(root):
        digest.update(f"{relpath}:{content_hash}\n".encode("utf-8", "replace"))
        count += 1
    if not count:
        return ""
    return digest.hexdigest()[:12]


def static_fingerprint():
    """
    Short digest of the deployed static tree.

    Prefers the collected tree (``STATIC_ROOT``), which is what is actually
    served. Falls back to the source directories when nothing has been
    collected yet -- for example in a management command that runs before
    ``collectstatic``.
    """
    try:
        from django.conf import settings

        static_root = getattr(settings, "STATIC_ROOT", None)
        source_dirs = getattr(settings, "STATICFILES_DIRS", []) or []
    except Exception:
        return ""

    if static_root:
        fingerprint = _fingerprint(str(static_root))
        if fingerprint:
            return fingerprint

    for path in source_dirs:
        fingerprint = _fingerprint(str(path))
        if fingerprint:
            return fingerprint

    return ""


def invalidate_static_fingerprint():
    """
    Drop the cached fingerprint so it is recomputed on next use.

    Static files only change through a deploy, which restarts the process
    and clears the cache anyway. This exists for tests and for any future
    "the assets changed under a running process" case.
    """
    _fingerprint.cache_clear()


class StaticFingerprintMixin:
    """
    Append the static-tree fingerprint to every generated static URL.

    Mixed in front of a ``StaticFilesStorage`` (or S3) backend so the URL
    keeps the backend's own base URL and only gains a cache-busting query
    string.
    """

    def url(self, name):
        url = super().url(name)
        if not url:
            return url

        fingerprint = static_fingerprint()
        if not fingerprint:
            return url

        separator = "&" if "?" in url else "?"
        return f"{url}{separator}v={fingerprint}"


class FingerprintedStaticFilesStorage(StaticFingerprintMixin, StaticFilesStorage):
    """Local static storage (WhiteNoise) with automatic cache busting."""


try:
    from storages.backends.s3boto3 import S3Boto3Storage
except ImportError:  # pragma: no cover - django-storages is a hard dependency
    S3Boto3Storage = None


if S3Boto3Storage is not None:

    class FingerprintedS3StaticFilesStorage(StaticFingerprintMixin, S3Boto3Storage):
        """S3/R2 static storage with automatic cache busting."""

    __all__ = [
        "FingerprintedStaticFilesStorage",
        "FingerprintedS3StaticFilesStorage",
        "static_fingerprint",
    ]
else:  # pragma: no cover
    __all__ = ["FingerprintedStaticFilesStorage", "static_fingerprint"]
