"""No DB connection: use WP0 standalone settings with all transports guarded."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from tests.sals_wp0.isolation import guards

with tempfile.TemporaryDirectory(prefix='sals-wp1-offline-') as scratch:
    path=Path(scratch)/'manifest.json'
    path.write_text(json.dumps(dict(host='127.0.0.1',port=65432,database='sals_wp0',
        user='sals_wp0',password='unused',container='sals-wp0-offline',scratch=scratch)))
    os.environ['SALS_WP0_MANIFEST']=str(path)
    os.environ['DJANGO_SETTINGS_MODULE']='tests.sals_wp0.settings'
    with guards(65432):
        import django
        django.setup()
        # Unlike the full WP0 runner this never creates/migrates a database.
        suite=unittest.defaultTestLoader.loadTestsFromNames([
            'tests.sals_wp1.test_validation', 'tests.sals_wp1.test_service',
            'tests.sals_wp1.test_api_checkpoint',
            'tests.sals_wp0.test_isolation','pwanimate.tests.test_gateway.ContractsTestCase'])
        result=unittest.TextTestRunner(verbosity=1).run(suite)
        raise SystemExit(not result.wasSuccessful())
