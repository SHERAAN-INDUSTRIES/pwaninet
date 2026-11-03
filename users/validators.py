"""
Custom OAuth2 validator for Vasto OAuth Provider.
Adds OIDC standard claims to ID tokens.
"""
from urllib.parse import urljoin, urlsplit

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
        profile_picture = getattr(request.user, 'profile_pic', None)
        if profile_picture:
            try:
                picture_url = profile_picture.url
                picture_parts = urlsplit(picture_url)
                if picture_parts.scheme in ('http', 'https') and picture_parts.netloc:
                    claims['picture'] = picture_url
                elif not picture_parts.netloc:
                    request_parts = urlsplit(getattr(request, 'uri', '') or '')
                    if request_parts.scheme in ('http', 'https') and request_parts.netloc:
                        origin = f'{request_parts.scheme}://{request_parts.netloc}/'
                        absolute_picture_url = urljoin(origin, picture_url)
                        absolute_parts = urlsplit(absolute_picture_url)
                        if absolute_parts.scheme in ('http', 'https') and absolute_parts.netloc:
                            claims['picture'] = absolute_picture_url
            except (AttributeError, OSError, TypeError, ValueError):
                # A missing/unavailable profile image must not break token issuance.
                pass
        return claims
