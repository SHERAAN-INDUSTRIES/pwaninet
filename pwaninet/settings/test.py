"""
Settings for running automated tests.
"""
from .local import *

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    }
}

# Fast password hashing in tests
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

# Unit tests do not depend on a developer's locally running Ollama server.
PWANIMATE_ATTACHMENT_VISION_PROVIDER = 'gemini'
PWANIMATE_ATTACHMENT_VISION_MODEL = PWANIMATE_VISION_MODEL
PWANIMATE_ATTACHMENT_VISION_FALLBACK_CHAIN = PWANIMATE_VISION_FALLBACK_CHAIN
PWANIMATE_ATTACHMENT_VISION_MAX_TOKENS = 1600
TIMETABLE_OCR_PROVIDER = 'gemini'
