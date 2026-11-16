"""Run with .venv/bin/python -m tests.sals_wp0.run. Never accepts a database URL."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import uuid


def main(test_labels=None, suite='wp0'):
    name = 'sals-wp0-' + uuid.uuid4().hex
    password = secrets.token_hex(24)
    with tempfile.TemporaryDirectory(prefix='sals-wp0-') as scratch:
        manifest = {'container': name, 'host': '127.0.0.1', 'database': 'sals_wp0',
                    'user': 'sals_wp0', 'password': password, 'scratch': scratch}
        created = False
        try:
            # No existing container, production compose, shared volume or network is reused.
            subprocess.run(['docker', 'run', '-d', '--name', name, '--label', 'sals.wp0=true', '--label', 'sals.suite=' + suite,
                '--network', 'bridge', '-p', '127.0.0.1::5432', '--tmpfs', '/var/lib/postgresql/data',
                '-e', 'POSTGRES_PASSWORD=' + password, 'pgvector/pgvector:pg15'], check=True,
                stdout=subprocess.DEVNULL)
            created = True
            manifest['port'] = int(subprocess.check_output(['docker','port',name,'5432/tcp'],text=True).strip().rsplit(':',1)[1])
            for _ in range(40):
                if subprocess.run(['docker','exec',name,'pg_isready','-U','postgres'],
                        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode == 0: break
                time.sleep(.25)
            else: raise RuntimeError('Disposable database did not become ready')
            sql = ("CREATE ROLE sals_wp0 LOGIN NOSUPERUSER CREATEDB PASSWORD '" + password + "';\n"
                   "CREATE DATABASE sals_wp0 OWNER sals_wp0;\n")
            subprocess.run(['docker','exec','-i',name,'psql','-U','postgres','-v','ON_ERROR_STOP=1'],
                           input=sql,text=True,check=True,stdout=subprocess.DEVNULL)
            subprocess.run(['docker','exec',name,'psql','-U','postgres','-d','template1','-v','ON_ERROR_STOP=1',
                            '-c','CREATE EXTENSION vector'],check=True,stdout=subprocess.DEVNULL)
            file = Path(scratch)/'manifest.json'; file.write_text(json.dumps(manifest));file.chmod(0o600)
            # Strip inherited credentials/settings before loading any app module.
            preserved = {k:v for k,v in os.environ.items() if k in {'PATH','LANG','LC_ALL','TZ'}}
            os.environ.clear(); os.environ.update(preserved)
            os.environ.update(SALS_WP0_MANIFEST=str(file), DJANGO_SETTINGS_MODULE='tests.sals_wp0.settings')
            from .isolation import guards
            with guards(manifest['port']):
                from .probe import verify
                verify(manifest)
                import django
                django.setup()
                from django.db.migrations.loader import MigrationLoader
                from django.core.management import call_command
                from django.test.utils import get_runner
                loader = MigrationLoader(None)
                print('Migration conflicts:', loader.detect_conflicts())
                if loader.detect_conflicts(): raise RuntimeError('Conflicting migration leaves')
                # Django creates and migrates test_sals_wp0, runs tests, and removes it.
                failures = get_runner(__import__('django.conf',fromlist=['settings']).settings)(
                    verbosity=1, interactive=False).run_tests(test_labels or [
                    'tests.sals_wp0.test_isolation',
                    'tests.sals_wp0.test_database',
                    'tests.sals_wp3.test_offline',
                    'tests.sals_wp3.test_chat_memory',
                    'tests.sals_wp3.test_database',
                    'tests.sals_wp2.test_policy',
                    'tests.sals_wp2.test_database',
                    'pwanimate.tests.test_gateway.ContractsTestCase',
                    'pwanimate.tests.test_chunker'])
                if failures: raise RuntimeError('Isolated tests failed')
        finally:
            if created:
                subprocess.run(['docker','rm','-f',name],check=True,stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    try:
        import argparse
        parser = argparse.ArgumentParser(description='Run tests against disposable PostgreSQL/pgvector only.')
        parser.add_argument('test_labels', nargs='*', help='Optional Django test labels; defaults to the WP0 suite')
        parser.add_argument('--suite', choices=['wp0', 'sals02', 'sals03wp1', 'sals03wp2'], default='wp0', help='Disposable resource label')
        args = parser.parse_args()
        main(args.test_labels, args.suite)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f'WP0 subprocess failed (exit {exc.returncode}); no credentials printed') from None
