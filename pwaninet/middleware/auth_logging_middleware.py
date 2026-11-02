import logging
from urllib.parse import urlsplit

logger = logging.getLogger('users.auth')


class AuthLoggingMiddleware:
    """
    Middleware that prints and logs every authentication-related HTTP request and response
    (registration and login) to guarantee real-time visibility in the server console.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        is_auth_route = path.startswith('/users/register') or path.startswith('/accounts/login') or path == '/login/' or path.startswith('/login/')

        if is_auth_route and request.method in ('POST', 'GET'):
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            client_ip = x_forwarded_for.split(',')[0].strip() if x_forwarded_for else request.META.get('REMOTE_ADDR', 'unknown')
            host = request.META.get('HTTP_HOST', '')
            origin = request.META.get('HTTP_ORIGIN', '')
            proto = request.META.get('HTTP_X_FORWARDED_PROTO', 'http')

            log_msg = f"[AUTH-TRAFFIC] {request.method} {path} | Client IP: {client_ip} | Proto: {proto} | Host: {host} | Origin: {origin}"
            print(log_msg, flush=True)
            logger.info(log_msg)

        response = self.get_response(request)

        if is_auth_route and request.method == 'POST':
            resp_msg = f"[AUTH-TRAFFIC] Response for {request.method} {path} -> Status {response.status_code}"
            if response.status_code == 403:
                resp_msg += " (403 FORBIDDEN - CSRF or Permission verification failed!)"
            elif response.status_code == 302:
                location = response.get('Location', '')
                parsed_location = urlsplit(location)
                safe_location = parsed_location.path or '/'
                if parsed_location.scheme and parsed_location.netloc:
                    safe_location = f'{parsed_location.scheme}://{parsed_location.netloc}{safe_location}'
                resp_msg += f" (REDIRECT -> {safe_location})"
            print(resp_msg, flush=True)
            logger.info(resp_msg)

        return response
