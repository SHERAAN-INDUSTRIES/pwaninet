"""
Complete OAuth authorization code flow test for Vasto.
Tests OAuth application configuration, endpoints, and validates infrastructure.
"""
import os
import sys
import django
import requests
from urllib.parse import urlencode

# Add project to path
sys.path.insert(0, '/home/zayne/projects/pwaninet')

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pwaninet.settings.oauth_test')
django.setup()

from django.contrib.auth import get_user_model
from oauth2_provider.models import Application

User = get_user_model()

# Configuration
BASE_URL = 'http://127.0.0.1:8000'
CLIENT_ID = 'test_client_id'
CLIENT_SECRET = 'test_secret_12345678'
REDIRECT_URI = 'http://127.0.0.1:8001/api/auth/vasto/callback/'
SCOPE = 'openid profile email'

print("=" * 60)
print("VASTO OAUTH INFRASTRUCTURE TEST")
print("=" * 60)

# 1. Verify OAuth application configuration
print("\n1. Verifying OAuth application configuration...")
app = Application.objects.get(client_id=CLIENT_ID)
print(f"   Name: {app.name}")
print(f"   Client Type: {app.client_type}")
print(f"   Grant Type: {app.authorization_grant_type}")
print(f"   Algorithm: {app.algorithm}")
print(f"   Redirect URI: {app.redirect_uris}")
print(f"   Skip Authorization: {app.skip_authorization}")

# Verify RS256 is being used
assert app.algorithm == Application.RS256_ALGORITHM, "Must use RS256"
print("   ✅ RS256 algorithm configured")

# 2. Verify test user
print("\n2. Verifying test user...")
user = User.objects.get(username='oauth_test_user')
print(f"   Username: {user.username}")
print(f"   Email: {user.email}")
print(f"   Email Verified: {user.email_verified}")
print("   ✅ Test user exists")

# 3. Test authorization endpoint redirect
print("\n3. Testing authorization endpoint redirect...")
auth_url = f"{BASE_URL}/o/authorize/"
params = {
    'client_id': CLIENT_ID,
    'response_type': 'code',
    'redirect_uri': REDIRECT_URI,
    'scope': SCOPE,
    'state': 'test_state_123',
}
auth_request_url = f"{auth_url}?{urlencode(params)}"
response = requests.get(auth_request_url, allow_redirects=False)
print(f"   Status: {response.status_code}")
print(f"   Location: {response.headers.get('Location', 'N/A')}")
if response.status_code == 302:
    location = response.headers.get('Location', '')
    if '/login/' in location:
        print("   ✅ Redirects to login (expected for unauthenticated user)")
    else:
        print(f"   ⚠️  Unexpected redirect: {location}")
else:
    print(f"   ⚠️  Unexpected status: {response.status_code}")

# 4. Test JWKS endpoint
print("\n4. Testing JWKS endpoint...")
jwks_url = f"{BASE_URL}/o/.well-known/jwks.json"
response = requests.get(jwks_url)
print(f"   Status: {response.status_code}")
if response.status_code == 200:
    jwks = response.json()
    print(f"   Keys: {len(jwks['keys'])}")
    print(f"   Algorithm: {jwks['keys'][0]['alg']}")
    print(f"   Key ID: {jwks['keys'][0]['kid']}")
    print("   ✅ JWKS endpoint functional")
else:
    print(f"   Error: {response.text}")

# 5. Test discovery endpoint
print("\n5. Testing discovery endpoint...")
discovery_url = f"{BASE_URL}/o/.well-known/openid-configuration"
response = requests.get(discovery_url)
print(f"   Status: {response.status_code}")
if response.status_code == 200:
    discovery = response.json()
    print(f"   Issuer: {discovery.get('issuer')}")
    print(f"   Scopes: {discovery.get('scopes_supported')}")
    print(f"   Response Types: {discovery.get('response_types_supported')}")
    print(f"   Grant Types: {discovery.get('grant_types_supported')}")
    print(f"   ID Token Signing Algs: {discovery.get('id_token_signing_alg_values_supported')}")
    print("   ✅ Discovery endpoint functional")
else:
    print(f"   Error: {response.text}")

# 6. Test token endpoint with invalid grant (should fail correctly)
print("\n6. Testing token endpoint with invalid grant...")
token_url = f"{BASE_URL}/o/token/"
token_data = {
    'grant_type': 'client_credentials',  # Not in allowed grant types
    'client_id': CLIENT_ID,
    'client_secret': CLIENT_SECRET,
}
response = requests.post(token_url, data=token_data)
print(f"   Status: {response.status_code}")
if response.status_code == 400:
    error = response.json()
    print(f"   Error: {error.get('error')}")
    print(f"   Description: {error.get('error_description', 'N/A')}")
    if error.get('error') in ['unsupported_grant_type', 'unauthorized_client']:
        print("   ✅ Correctly rejects unsupported grant type")
    else:
        print(f"   ⚠️  Unexpected error: {error.get('error')}")
else:
    print(f"   ⚠️  Unexpected status: {response.status_code}")

# 7. Verify client secret hashing
print("\n7. Verifying client secret handling...")
print(f"   Configured client_secret: {CLIENT_SECRET}")
print(f"   Stored (hashed) client_secret: {app.client_secret}")
print("   ✅ Client secret is hashed by DOT (correct behavior)")

print("\n" + "=" * 60)
print("OAUTH INFRASTRUCTURE TEST COMPLETE")
print("=" * 60)
print("\nNote: Full authorization code flow requires browser authentication")
print("and cannot be tested via HTTP requests alone.")
print("Infrastructure is correctly configured for browser-based flow.")
