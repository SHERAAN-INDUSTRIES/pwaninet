"""Policy tests require no application database or external services."""
import unittest
import json
import sys
from unittest.mock import Mock, patch
from types import SimpleNamespace
from django.conf import settings
if not settings.configured:
    settings.configure(DEFAULT_CHARSET='utf-8')
from django.http import HttpResponse
from pwaninet.offline import eligible_route
from pwaninet.middleware.cache_headers import CacheHeadersMiddleware


class OfflinePolicyTests(unittest.TestCase):
    def response(self, path='/', owner=1, status=200, method='GET', headers=None, query=None):
        request = SimpleNamespace(path=path, method=method, GET=query or {}, user=SimpleNamespace(pk=owner, is_authenticated=owner is not None))
        return CacheHeadersMiddleware(lambda _: HttpResponse('body', status=status, headers=headers or {}, content_type='text/html'))(request)

    def test_eligible_routes(self):
        for path in ['/', '/post/12345678-1234-1234-1234-123456789abc/', '/unit/2/', '/users/user/student/', '/groups/2/', '/groups/2/announcements/', '/documents/', '/documents/library/downloads/', '/messaging/', '/messaging/conversation/12/', '/notifications/', '/pwanimate/', '/pwanimate/history/', '/pwanimate/12345678-1234-1234-1234-123456789abc/']:
            with self.subTest(path=path):
                self.assertTrue(eligible_route(path))
                self.assertEqual(self.response(path)['X-Vasto-Offline'], 'rendered-v1')

    def test_sensitive_and_write_routes_excluded(self):
        for path in ['/accounts/login/', '/users/logout/', '/users/profile/edit/', '/messaging/create/', '/messaging/conversation/12/media/', '/notifications/read/12345678-1234-1234-1234-123456789abc/', '/search/', '/documents/search/', '/post/new/', '/groups/2/settings/', '/pwanimate/settings/', '/pwanimate/12345678-1234-1234-1234-123456789abc/delete/']:
            with self.subTest(path=path):
                self.assertFalse(eligible_route(path))
                self.assertNotIn('X-Vasto-Offline', self.response(path))

    def test_anonymous_errors_mutations_and_explicit_no_store_excluded(self):
        for kwargs in [{'owner': None}, {'status': 403}, {'status': 404}, {'status': 500}, {'method': 'POST'}, {'headers': {'Cache-Control': 'private, no-store'}}, {'query': {'resume': '1'}}]:
            with self.subTest(kwargs=kwargs):
                response = self.response(**kwargs)
                self.assertNotIn('X-Vasto-Offline', response)
                self.assertIn('no-store', response['Cache-Control'])

    def test_private_html_retains_htmx_vary_and_account(self):
        response = self.response(owner=42)
        self.assertEqual(response['X-Vasto-Account'], '42')
        self.assertIn('private', response['Cache-Control'])
        self.assertIn('HX-Request', response['Vary'])

class OfflinePostAccessTests(unittest.TestCase):
    def test_auth_and_method_required(self):
        from pwaninet.offline import offline_post_access
        anonymous = SimpleNamespace(method='POST', user=SimpleNamespace(is_authenticated=False))
        self.assertEqual(offline_post_access(anonymous).status_code, 401)
        user = SimpleNamespace(method='GET', user=SimpleNamespace(is_authenticated=True))
        self.assertEqual(offline_post_access(user).status_code, 405)

    def test_only_server_visible_ids_and_download_permission_returned(self):
        from pwaninet.offline import offline_post_access
        post_id = '12345678-1234-1234-1234-123456789abc'
        user = SimpleNamespace(is_authenticated=True)
        privacy = SimpleNamespace(visible_posts_for=Mock())
        privacy.visible_posts_for.return_value.filter.return_value.values_list.return_value = [(post_id, False)]
        request = SimpleNamespace(method='POST', user=user, body=json.dumps({'ids': [post_id]}).encode())
        with patch.dict(sys.modules, {'users.services.privacy': privacy}):
            response = offline_post_access(request)
        privacy.visible_posts_for.assert_called_once_with(user)
        privacy.visible_posts_for.return_value.filter.assert_called_once_with(share_id__in=[post_id])
        self.assertEqual(json.loads(response.content), {'posts': [{'id': post_id, 'downloadable': False}]})
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_malformed_ids_rejected(self):
        from pwaninet.offline import offline_post_access
        request = SimpleNamespace(method='POST', user=SimpleNamespace(is_authenticated=True), body=b'{"ids":["not-an-id"]}')
        self.assertEqual(offline_post_access(request).status_code, 400)



class PrivateHistoryAccessTests(unittest.TestCase):
    def test_private_endpoints_require_auth_and_valid_input(self):
        from pwaninet.offline import offline_message_access, offline_notification_access
        for endpoint in (offline_message_access, offline_notification_access):
            self.assertEqual(endpoint(SimpleNamespace(method='POST', user=SimpleNamespace(is_authenticated=False))).status_code, 401)
            self.assertEqual(endpoint(SimpleNamespace(method='GET', user=SimpleNamespace(is_authenticated=True))).status_code, 405)
            self.assertEqual(endpoint(SimpleNamespace(method='POST', user=SimpleNamespace(is_authenticated=True), body=b'{"ids":["bad"]}')).status_code, 400)

    def test_message_history_requires_current_membership(self):
        from pwaninet.offline import offline_message_access
        user = SimpleNamespace(is_authenticated=True)
        conversation = Mock()
        conversation.objects.filter.return_value.exists.return_value = False
        models = SimpleNamespace(Conversation=conversation, Message=Mock())
        serializers = SimpleNamespace(MessageSerializer=Mock())
        request = SimpleNamespace(method='POST', user=user, body=b'{"conversation":4,"ids":[1,2]}')
        accessible = Mock()
        accessible.return_value.filter.return_value.exists.return_value = False
        chat_service = SimpleNamespace(accessible_conversations=accessible)
        with patch.dict(sys.modules, {'messaging.models': models, 'messaging.serializers': serializers, 'groups.services.chat_service': chat_service}):
            response = offline_message_access(request)
        self.assertEqual(response.status_code, 403)
        accessible.assert_called_once_with(user)
        accessible.return_value.filter.assert_called_once_with(pk=4)
        serializers.MessageSerializer.assert_not_called()

if __name__ == '__main__':
    unittest.main()
