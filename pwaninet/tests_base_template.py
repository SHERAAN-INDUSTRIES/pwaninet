"""
Regression tests for template tags that never render.

``templates/base.html`` used to wrap its notification WebSocket script in a
``{% verbatim %}`` block. Everything inside a verbatim block is emitted
verbatim, so the five ``{% static 'images/...' %}`` tags in that script were
never resolved -- they reached the browser as literal text:

    const avatar = ... || '{% static 'images/web-app-manifest-192x192-rounded.png' %}';

Chrome parses that as a string followed by a bare identifier and aborts the
whole script with:

    Uncaught SyntaxError: Unexpected identifier 'images'

The script is the one that wires up the chat-notification banner and the
notification toast, so the chat banner and the push-notification toast were
dead on every page of the site. The error surfaced on ``/uploads/`` because
that is where it was noticed.

These tests pin the two halves of the fix down:

* no unrendered template tag may reach a rendered page, and
* no template may hide a ``{% %}`` tag inside a ``{% verbatim %}`` block.

The rendering test deliberately avoids the database: it renders the template
with a plain context (no context processors), so it stays a fast unit test.

Run with:  python manage.py test pwaninet.tests_base_template
"""

import os
import re

from django.template import engines
from django.template.context import Context
from django.test import RequestFactory, SimpleTestCase

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A template tag that was never resolved. "{{" is deliberately absent from
# this check: a few scripts probe for the literal string "{{" to detect an
# unrendered {% csrf_token %}, so "{{" alone is not a reliable signal.
LEAKED_TAG = re.compile(r"\{%")

VERBATIM_BLOCK = re.compile(r"\{%\s*verbatim\b.*?\{%\s*endverbatim\s*%\}", re.S)
TEMPLATE_TAG = re.compile(r"\{%.*?%\}", re.S)


class _ProbeUser:
    """Just enough of a user for base.html to render."""

    is_authenticated = True
    is_anonymous = False
    id = 1
    username = "template-probe"
    profile_pic = None
    global_role = ""
    theme_preference = ""
    font_family_preference = ""
    font_size_preference = ""
    font_style_preference = ""
    chat_theme_preference = ""
    audio_preference = ""
    is_two_factor_enabled = False

    def get_full_name(self):
        return "Template Probe"


class BaseTemplateRendersItsTagsTests(SimpleTestCase):
    """A rendered page must not contain unrendered template syntax."""

    def _render_uploads_page(self):
        request = RequestFactory().get("/uploads/")
        request.user = _ProbeUser()
        request.resolver_match = None

        template = engines["django"].get_template("uploads/manager.html")
        # A plain Context, not a RequestContext: the context processors are
        # not needed here and they would query the database.
        context = Context(
            {
                "request": request,
                "app_version": "0.0.0-test",
                "app_build": "0",
            }
        )
        return template.template.render(context)

    def test_page_contains_no_unrendered_template_tags(self):
        html = self._render_uploads_page()

        leaked = LEAKED_TAG.findall(html)
        self.assertEqual(
            leaked,
            [],
            "base.html leaked unrendered template tags into the page: "
            f"{sorted(set(leaked))}",
        )

    def test_static_fallbacks_inside_scripts_are_resolved(self):
        """The script fallbacks must come out as real, fingerprinted URLs."""
        html = self._render_uploads_page()

        self.assertIn("/static/images/web-app-manifest-192x192-rounded.png?v=", html)
        self.assertIn("/static/images/vastomonochrome.png?v=", html)


class VerbatimBlocksDoNotHideTagsTests(SimpleTestCase):
    """A {% verbatim %} block must not swallow a tag that has to render.

    This is the structural guard: it fails on the template source, so it
    catches the mistake even in a branch of the site that no test renders.
    """

    def _templates(self):
        base = os.path.join(BASE_DIR, "templates")
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in filenames:
                if name.endswith(".html"):
                    yield os.path.join(dirpath, name)

    def test_no_template_hides_a_tag_inside_verbatim(self):
        offenders = []
        for path in self._templates():
            source = open(path, encoding="utf-8").read()
            for block in VERBATIM_BLOCK.finditer(source):
                for tag in TEMPLATE_TAG.finditer(block.group(0)):
                    # The verbatim/endverbatim pair itself is expected.
                    if re.match(r"\{%\s*(end)?verbatim\b", tag.group(0)):
                        continue
                    line = source.count("\n", 0, block.start() + tag.start()) + 1
                    offenders.append(
                        f"{os.path.relpath(path, BASE_DIR)}:{line} {tag.group(0)[:80]}"
                    )

        self.assertEqual(
            offenders,
            [],
            "these template tags sit inside a {% verbatim %} block and will "
            "never render: " + "; ".join(offenders),
        )
