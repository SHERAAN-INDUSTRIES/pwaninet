"""
ASGI configuration for vasto project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/asgi/

ARCHITECTURAL RULE:
Each WebSocket consumer must have a single source of truth file.
Duplicate class names across modules are forbidden.
"""

import os
import logging
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pwaninet.settings.local')

django_asgi_app = get_asgi_application()

# Warm the static fingerprint before the first request arrives. Hashing the
# collected static tree takes about a second; doing it here keeps that cost
# off the request path. The value is cached for the life of the process and
# only changes when a static file's content changes.
try:
    import time as _time

    from pwaninet.static_storage import static_fingerprint as _static_fingerprint

    _started = _time.time()
    _fingerprint = _static_fingerprint()
    print(f'[ASGI] Static fingerprint {_fingerprint} computed in {_time.time() - _started:.2f}s')
except Exception as _exc:  # pragma: no cover - never block startup
    print(f'[ASGI] Static fingerprint warm-up skipped: {_exc}')

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from channels.middleware import BaseMiddleware
from channels.db import database_sync_to_async
from urllib.parse import parse_qs
from django.contrib.auth.models import AnonymousUser
from pwaninet.routing import websocket_urlpatterns


@database_sync_to_async
def _get_user_from_token(token_key):
    try:
        from rest_framework.authtoken.models import Token
        return Token.objects.select_related('user').get(key=token_key).user
    except Exception:
        return AnonymousUser()


class TokenAuthMiddleware(BaseMiddleware):
    """Accept ?token=<DRF token> for mobile/Capacitor clients without session cookies."""

    async def __call__(self, scope, receive, send):
        if scope.get('type') == 'websocket' and not scope.get('user'):
            qs = parse_qs(scope.get('query_string', b'').decode())
            token = (qs.get('token') or [None])[0]
            if token:
                scope['user'] = await _get_user_from_token(token)
        return await super().__call__(scope, receive, send)

logger = logging.getLogger("uvicorn.error")


class ASGIResponseDiagnostics:
    """Add request context to response-send failures raised below Django."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        declared_length = None
        response_status = None
        bytes_attempted = 0

        async def tracked_send(message):
            nonlocal declared_length, response_status, bytes_attempted
            if message["type"] == "http.response.start":
                response_status = message.get("status")
                for name, value in message.get("headers", []):
                    if name.lower() == b"content-length":
                        try:
                            declared_length = int(value)
                        except (TypeError, ValueError):
                            declared_length = None
                        break
            elif message["type"] == "http.response.body":
                bytes_attempted += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, tracked_send)
        except Exception:
            # ASGI scope.path excludes the query string, so signed download tokens
            # are not written to logs. Count bytes only; never log response bodies.
            logger.error(
                "ASGI response failed method=%s path=%s status=%s content_length=%s bytes_attempted=%s",
                scope.get("method", "?"),
                scope.get("path", "?"),
                response_status,
                declared_length,
                bytes_attempted,
            )
            raise


application = ASGIResponseDiagnostics(ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": TokenAuthMiddleware(
        AuthMiddlewareStack(
            URLRouter(websocket_urlpatterns)
        )
    ),
}))


# Debug: Log ASGI startup
print('[ASGI] Application initialized with WebSocket support')
