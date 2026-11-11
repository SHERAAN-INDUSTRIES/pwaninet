"""LiveKit boundary. No client-visible secrets or arbitrary room selection."""
from datetime import timedelta

from django.conf import settings
from rest_framework.exceptions import APIException


class CallingUnavailable(APIException):
    status_code = 503
    default_detail = 'Voice calling is not configured.'


def require_configured():
    if not settings.CALLING_ENABLED or not all((
        settings.LIVEKIT_URL, settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET,
    )):
        raise CallingUnavailable()


def connection_details(call, participant):
    require_configured()
    from livekit import api

    token = (api.AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
             .with_identity(str(participant.pk))
             .with_ttl(timedelta(seconds=120))
             .with_grants(api.VideoGrants(
                 room_join=True, room=call.room_name, can_publish=True,
                 can_subscribe=True, can_publish_data=False,
                 can_publish_sources=['microphone'], can_update_own_metadata=False,
             )).to_jwt())
    return {'server_url': settings.LIVEKIT_URL, 'participant_token': token}
