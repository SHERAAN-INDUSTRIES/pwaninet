"""Settings shim for running the test suite on a dedicated database.

The shared test database is often busy with another run; this keeps the two
apart so a suite never collides with a concurrent run.
"""

from pwaninet.settings.local import *  # noqa: F401,F403

DATABASES = {
    'default': {
        **DATABASES['default'],  # noqa: F405
        'TEST': {'NAME': 'test_pwaninet_db_agent'},
    },
}
