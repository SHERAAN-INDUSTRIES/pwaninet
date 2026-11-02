"""
Temporary OAuth test settings using disposable PostgreSQL database.
DO NOT use for production or regular development.
"""
from .local import *

# Override database to use disposable test database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'pwaninet_oauth_test',
        'USER': 'postgres',
        'PASSWORD': 'postgres',
        'HOST': 'localhost',
        'PORT': '5432',
        'CONN_MAX_AGE': 60,
    }
}

# Override issuer for local testing
OAUTH2_PROVIDER['OIDC_ISS_ENDPOINT'] = 'http://127.0.0.1:8000/o'

# Load RSA key from file for testing
import os
_rsa_key_path = '/tmp/oauth_dev_private.pem'
if os.path.exists(_rsa_key_path):
    with open(_rsa_key_path, 'r') as f:
        OAUTH2_PROVIDER['OIDC_RSA_PRIVATE_KEY'] = f.read()
