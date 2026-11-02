"""
Custom OAuth2 validator for Vasto OAuth Provider.
Adds OIDC standard claims to ID tokens.
"""
from oauth2_provider.oauth2_validators import OAuth2Validator


class CustomOAuth2Validator(OAuth2Validator):
    """
    Custom OAuth2 validator that adds additional OIDC claims to ID tokens.
    """

    def get_additional_claims(self, request):
        """
        Add OIDC standard claims to ID token.
        Scope-restricted claims based on requested scopes.
        """
        claims = {
            'preferred_username': request.user.username,
            'email': request.user.email,
            'email_verified': getattr(request.user, 'email_verified', False),
            'given_name': request.user.first_name,
            'family_name': request.user.last_name,
        }
        return claims
