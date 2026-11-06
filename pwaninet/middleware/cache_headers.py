"""
HTTP Caching Headers Middleware
Adds cache control headers to improve performance by allowing browsers to cache static resources
"""

class CacheHeadersMiddleware:
    """
    Middleware to add cache control headers to responses
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, 'user', None)
        if not request.path.startswith(('/static/', '/media/')):
            response['X-Vasto-Account'] = str(user.pk) if user and user.is_authenticated else ''
        
        from django.utils.cache import patch_vary_headers

        # Cache static assets
        if request.path.startswith('/static/'):
            # If asset is versioned with a query param (e.g. ?v=1.6.2-96), safe to cache immutably
            if request.GET.get('v'):
                response['Cache-Control'] = 'public, max-age=31536000, immutable'
            else:
                # Unversioned static files must revalidate to prevent stale styles on reload
                response['Cache-Control'] = 'public, max-age=0, must-revalidate'
            return response

        # Media files
        if request.path.startswith('/media/'):
            response['Cache-Control'] = 'public, max-age=86400'
            return response

        # Don't cache dynamic content (API, POST, and all HTML pages)
        content_type = response.get('Content-Type', '').lower()
        is_html = 'text/html' in content_type

        if is_html:
            patch_vary_headers(response, ['HX-Request'])
            # Account identity is consumed by our same-origin service worker,
            # never by a shared HTTP cache. Only token-free rendered exports
            # are persisted; the original response is not stored by the worker.
            from pwaninet.offline import eligible_route
            user = getattr(request, 'user', None)
            owner = str(user.pk) if user and user.is_authenticated else ''
            response['X-Vasto-Account'] = owner
            if (request.method == 'GET' and response.status_code == 200
                    and owner and eligible_route(request.path) and not request.GET.get('resume')
                    and 'no-store' not in response.get('Cache-Control', '').lower()):
                response['X-Vasto-Offline'] = 'rendered-v1'
                response['Cache-Control'] = 'private, no-cache, must-revalidate'
                return response

        if request.path.startswith('/api/') or request.method in ('POST', 'PUT', 'PATCH', 'DELETE') or is_html or request.path.endswith('.html'):
            response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response['Pragma'] = 'no-cache'
            response['Expires'] = '0'
        
        return response
