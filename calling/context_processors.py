from django.conf import settings


def calling_configuration(request):
    # Public availability only. API credentials never leave the backend.
    return {'voice_calling_available': bool(settings.CALLING_ENABLED and settings.LIVEKIT_URL
                                           and settings.LIVEKIT_API_KEY and settings.LIVEKIT_API_SECRET)}
