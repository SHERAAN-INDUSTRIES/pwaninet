"""
Regression tests for automatic static-asset cache busting.

Production serves static assets through Cloudflare with a very long cache
lifetime, so the only thing that keeps users off a stale stylesheet is the
cache key changing whenever the asset's content changes. These tests pin
that behaviour down.

Run with:  python manage.py test pwaninet.tests_static_storage
"""

import os
import tempfile

from django.contrib.staticfiles.storage import staticfiles_storage
from django.test import SimpleTestCase, override_settings

from pwaninet.static_storage import _fingerprint, invalidate_static_fingerprint, static_fingerprint


class StaticFingerprintTests(SimpleTestCase):
    """The fingerprint must track content, and nothing else."""

    def _root_with(self, filename, content):
        root = tempfile.mkdtemp()
        with open(os.path.join(root, filename), "wb") as handle:
            handle.write(content)
        return root

    def _fingerprint_of(self, root):
        """Recompute from scratch -- the production code caches by design."""
        invalidate_static_fingerprint()
        return _fingerprint(root)

    def test_fingerprint_is_stable_for_unchanged_files(self):
        root = self._root_with("app.css", b"body{color:red}")
        self.addCleanup(_remove_tree, root)

        first = self._fingerprint_of(root)
        second = self._fingerprint_of(root)

        self.assertTrue(first)
        self.assertEqual(first, second)

    def test_fingerprint_changes_when_content_changes(self):
        root = self._root_with("app.css", b"body{color:red}")
        self.addCleanup(_remove_tree, root)
        before = self._fingerprint_of(root)

        with open(os.path.join(root, "app.css"), "wb") as handle:
            handle.write(b"body{color:blue}")

        self.assertNotEqual(before, self._fingerprint_of(root))

    def test_fingerprint_ignores_modification_time(self):
        """collectstatic rewrites files it copies, even when nothing changed."""
        root = self._root_with("app.css", b"body{color:red}")
        self.addCleanup(_remove_tree, root)
        before = self._fingerprint_of(root)

        path = os.path.join(root, "app.css")
        stat = os.stat(path)
        os.utime(path, (stat.st_atime, stat.st_mtime + 600))

        self.assertEqual(before, self._fingerprint_of(root))

    def test_fingerprint_changes_when_a_file_is_added(self):
        root = self._root_with("app.css", b"body{color:red}")
        self.addCleanup(_remove_tree, root)
        before = self._fingerprint_of(root)

        with open(os.path.join(root, "extra.css"), "wb") as handle:
            handle.write(b"body{margin:0}")

        self.assertNotEqual(before, self._fingerprint_of(root))


class StaticUrlTests(SimpleTestCase):
    """Every {% static %} URL must carry the fingerprint."""

    def test_static_url_carries_the_fingerprint(self):
        url = staticfiles_storage.url("css/custom.css")

        self.assertIn("?v=", url)
        self.assertEqual(url.split("?v=")[1], static_fingerprint())

    def test_static_url_keeps_the_plain_path(self):
        """File names must stay stable so hard-coded /static/ refs keep working."""
        url = staticfiles_storage.url("css/custom.css")

        self.assertTrue(url.endswith(".css?v=" + static_fingerprint()))
        self.assertIn("/static/css/custom.css", url)


def _remove_tree(path):
    import shutil

    shutil.rmtree(path, ignore_errors=True)
