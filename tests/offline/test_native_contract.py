"""Native reader permission and redaction contracts, without external services."""
import json
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from django.conf import settings
if not settings.configured:
    settings.configure(DEFAULT_CHARSET='utf-8')
from pwaninet.native_offline import state, reel, message_record


def request(owner=7, query=None):
    return NS(method='GET', GET=query or {}, user=NS(pk=owner, is_authenticated=owner is not None,
              username='student', get_full_name=lambda: 'Student'))


class NativeContractTests(unittest.TestCase):
    def test_anonymous_requests_do_not_export_private_content(self):
        for view, args in ((state, ()), (reel, ('00000000-0000-0000-0000-000000000001',))):
            response = view(request(None), *args)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response['Cache-Control'], 'no-store')

    def test_deleted_and_encrypted_messages_never_export_original_bodies(self):
        sender = NS(pk=9, username='member', get_full_name=lambda: 'Member')
        source = NS(conversation_id=12, pk=1, sender=sender, sender_id=9, content='private body', is_deleted=False,
                    is_encrypted=True, attachment_type='video', reply_to=None, edited_at=None,
                    created_at=datetime.now(timezone.utc))
        encrypted = message_record(source, request().user)
        self.assertNotIn('private body', json.dumps(encrypted))
        source.is_deleted = True
        deleted = message_record(source, request().user)
        self.assertEqual(deleted['text'], 'This message was deleted')
        self.assertFalse(deleted['encrypted'])
        self.assertEqual(deleted['attachment'], '')

    def test_deleted_reply_body_is_redacted(self):
        sender = NS(username='member', get_full_name=lambda: 'Member')
        reply = NS(conversation_id=12, sender=sender, content='deleted secret', is_deleted=True, is_encrypted=False)
        source = NS(conversation_id=12, pk=1, sender=sender, sender_id=9, content='reply', is_deleted=False,
                    is_encrypted=False, attachment_type='', reply_to=reply, edited_at=None,
                    created_at=datetime.now(timezone.utc))
        self.assertNotIn('deleted secret', json.dumps(message_record(source, request().user)))

    def modules(self, conversations=()):
        accessible = Mock()
        accessible.return_value.select_related.return_value.prefetch_related.return_value.distinct.return_value = conversations
        messages = Mock()
        messages.objects.filter.return_value.select_related.return_value = []
        visible = Mock()
        visible.return_value.filter.return_value.values_list.return_value = []
        return accessible, messages, visible, {
            'groups.services.chat_service': NS(accessible_conversations=accessible),
            'messaging.models': NS(Message=messages),
            'users.services.privacy': NS(visible_posts_for=visible),
        }

    def test_history_requires_current_conversation_access(self):
        accessible, messages, _, modules = self.modules()
        req = request(query={'conversation': '999'})
        with patch.dict(sys.modules, modules):
            response = state(req)
        self.assertEqual(response.status_code, 403)
        accessible.assert_called_once_with(req.user)
        messages.objects.filter.assert_called_once_with(id__in=[], conversation_id__in=[])

    def test_allowed_list_and_revisions_are_account_scoped(self):
        conversation = NS(pk=12, group_id=None, name='Chat', type='direct',
                          members=NS(all=lambda: []), updated_at=datetime.now(timezone.utc))
        accessible, messages, visible, modules = self.modules([conversation])
        req = request(query={'messages': '1,2'})
        with patch.dict(sys.modules, modules):
            response = state(req)
        data = json.loads(response.content)
        self.assertEqual(data['owner'], '7')
        self.assertEqual(data['conversations'][0]['id'], 12)
        messages.objects.filter.assert_called_once_with(id__in=[1, 2], conversation_id__in=[12])
        visible.assert_called_once_with(req.user)

    def test_video_requires_visibility_and_download_permission(self):
        visible = Mock()
        visible.return_value.filter.return_value.first.return_value = None
        with patch.dict(sys.modules, {'users.services.privacy': NS(visible_posts_for=visible)}):
            response = reel(request(), '00000000-0000-0000-0000-000000000001')
        self.assertEqual(response.status_code, 404)
        visible.return_value.filter.assert_called_once_with(share_id='00000000-0000-0000-0000-000000000001', allow_downloads=True)


if __name__ == '__main__':
    unittest.main()
