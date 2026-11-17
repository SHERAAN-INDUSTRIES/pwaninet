"""WP1 integration tests: real Django inheritance and isolated static collection."""
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.template import Context, Template
from django.test import SimpleTestCase, override_settings

ROOT = Path(__file__).resolve().parents[2]


class LucideInfrastructureTests(SimpleTestCase):
    def test_override_without_super_keeps_single_ordered_loader(self):
        # Render the real base with a minimal URLconf: its unrelated navigation
        # tags are excluded, but its actual block boundaries remain unchanged.
        source = (ROOT / 'templates/base.html').read_text()
        import re
        source = re.sub(r'{%\s*(url|include|notification_badge|unread_message_count)\b.*?%}', '', source)
        with override_settings(TEMPLATES=[{
            'BACKEND': 'django.template.backends.django.DjangoTemplates',
            'OPTIONS': {'loaders': [('django.template.loaders.locmem.Loader', {'base-under-test.html': source})]},
        }]):
            html = Template('{% extends "base-under-test.html" %}{% block extra_js %}<script src="child.js"></script>{% endblock %}').render(Context({'app_version': 'wp1-test'}))
        self.assertEqual(html.count('vendor/lucide/lucide.min.js'), 1)
        self.assertEqual(html.count('js/vasto-icons.js'), 1)
        self.assertLess(html.index('vendor/lucide/lucide.min.js'), html.index('js/vasto-icons.js'))
        self.assertLess(html.index('js/vasto-icons.js'), html.index('child.js'))

    def test_isolated_collectstatic(self):
        with TemporaryDirectory(prefix='wp1-static-') as target:
            with override_settings(STATIC_ROOT=target, STORAGES={
                'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                'staticfiles': {'BACKEND': 'pwaninet.static_storage.FingerprintedStaticFilesStorage'},
            }):
                call_command('collectstatic', interactive=False, verbosity=0)
            for name in ['js/vasto-icons.js', 'vendor/lucide/lucide.min.js', 'js/pwanimate/spark-icons.js', 'js/pwanimate/pwanimate-chat.js', 'css/pwanimate/pwanimate.css', 'css/vasto-navigation-icons.css']:
                self.assertEqual((Path(target) / name).read_bytes(), (ROOT / 'static' / name).read_bytes())
