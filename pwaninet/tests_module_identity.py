"""
Regression tests for ES module identity across template entry points.

The upload platform is reached from two directions: template entry points
(base.html, the post composer) import the modules through the static template
tag, which appends a content fingerprint (``pwaninet/static_storage.py``),
while ``static/js/upload/*.js`` imports its siblings relatively with a pinned
version query. A browser evaluates an ES module once *per URL*.

If a template appends its own ``?v=`` to a ``{% static %}`` import, it loads
a second copy of the module. That is exactly what broke posting in October
2026: after ``fb5508a`` (the fingerprint deploy) base.html loaded
``upload_manager.js?v=<fingerprint>`` while the composer still pinned
``upload_manager.js?v=<fingerprint>?v=20261016-02``. base.html called
``initialize()`` on instance A; the composer created uploads on instance B.
Instance A's ``UPLOAD_ADDED`` handler looked the session up in its own empty
Map and returned silently, ``processUpload`` never ran for anyone, the queue
item never reached a terminal state -- and because the queue is sequential,
every post after that hung at "Preparing…" forever without a single request
reaching Django.

Two guards keep that class of bug dead:

* no template may append a manual query string to a static import of an
  upload module (URL identity), and
* ``upload_events.js`` and ``upload_manager.js`` must keep their
  window-scoped singletons (identity that survives any future URL drift).

Run with:  python manage.py test pwaninet.tests_module_identity
"""

import os
import re

from django.test import SimpleTestCase

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Directories that never hold Django templates (or hold copies of them).
SKIP_DIRS = {
    ".git",
    "venv",
    ".venv",
    "node_modules",
    "staticfiles",
    "static",
    "media",
    "android",
    "static_root",
}

# import ... from '{% static "js/upload/upload_manager.js" %}?v=...'
# The "?v=" after the tag makes the URL differ from every other entry point
# that renders the same tag without a suffix -> a second module instance.
STATIC_JS_IMPORT_WITH_QUERY = re.compile(
    r"""from\s+['"]\{%\s*static\s+["'][^"']+\.js["']\s*%\}\?"""
)


def _iter_template_files():
    for dirpath, dirnames, filenames in os.walk(BASE_DIR):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        # Only look inside template directories of the project.
        parts = os.path.relpath(dirpath, BASE_DIR).split(os.sep)
        if "templates" not in parts:
            continue
        for name in filenames:
            if name.endswith(".html"):
                yield os.path.join(dirpath, name)


class UploadModuleImportTests(SimpleTestCase):
    """Template module imports must resolve to the exact URL every other
    entry point renders for the same file -- no manual query suffix."""

    def test_no_template_pins_a_query_onto_a_static_module_import(self):
        offenders = []
        for path in _iter_template_files():
            source = open(path, encoding="utf-8").read()
            for match in STATIC_JS_IMPORT_WITH_QUERY.finditer(source):
                line = source.count("\n", 0, match.start()) + 1
                offenders.append(
                    f"{os.path.relpath(path, BASE_DIR)}:{line} {match.group(0)[:100]}"
                )

        self.assertEqual(
            offenders,
            [],
            "these template imports append a manual '?v=' after {% static %}, "
            "so the browser loads a SECOND module instance (this is what "
            "froze every post at 'Preparing…' in Oct 2026): "
            + "; ".join(offenders),
        )


class UploadSingletonGuardTests(SimpleTestCase):
    """The window-keyed singletons are the URL-proof half of the fix: even
    if two URLs for the same file ever reappear, there must still be exactly
    one event bus and one upload manager per window."""

    GUARDS = (
        ("static/js/upload/upload_events.js", "__pwaninetUploadEvents"),
        ("static/js/upload/upload_manager.js", "__pwaninetUploadManager"),
    )

    def test_singletons_are_keyed_on_window(self):
        for relpath, key in self.GUARDS:
            path = os.path.join(BASE_DIR, relpath)
            source = open(path, encoding="utf-8").read()
            self.assertIn(
                key,
                source,
                f"{relpath} lost its window-scoped singleton guard ({key}): a "
                "second module URL for this file would again split the upload "
                "platform into instances that cannot see each other's state.",
            )
