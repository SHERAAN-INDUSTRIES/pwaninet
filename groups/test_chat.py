"""Integration coverage for group-owned conversations using the shared chat stack."""
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.apps import apps
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from groups.models import Group, Membership, GroupMessage, GroupMessageAttachment, GroupMessageReaction
from groups.services.chat_service import open_group_chat, accessible_conversations
from messaging.consumers import ChatConsumer
from messaging.models import Conversation, ConversationMember, Message
from users.models import User


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class GroupChatTests(TransactionTestCase):
    def setUp(self):
        self.events = patch('groups.signals.publish_event').start()
        self.addCleanup(patch.stopall)
        self.notify = patch('messaging.views.ChatNotificationService.notify_new_message').start()
        patch('messaging.views.LinkPreviewService.generate_preview_for_message').start()
        self.users = [User.objects.create_user(username=f'chat_user_{i}', email=f'chat{i}@example.com', password='test') for i in range(4)]
        self.group = Group.objects.create(name='Study group', created_by=self.users[0])
        for user in self.users[:3]:
            Membership.objects.create(group=self.group, user=user)
        self.client = APIClient()
        self.client.force_authenticate(self.users[0])

    def test_open_reuses_one_conversation_with_approved_members(self):
        Membership.objects.create(group=self.group, user=self.users[3], status='PENDING')
        conv = open_group_chat(self.group.pk, self.users[0])
        self.assertEqual(open_group_chat(self.group.pk, self.users[1]).pk, conv.pk)
        self.assertEqual(conv.type, 'group')
        self.assertFalse(conv.is_encrypted)
        self.assertEqual(set(conv.members.values_list('user_id', flat=True)), {u.pk for u in self.users[:3]})
        with self.assertRaises(PermissionDenied):
            open_group_chat(self.group.pk, self.users[3])

    def test_concurrent_first_opens(self):
        def open_chat(user):
            close_old_connections()
            try:
                return open_group_chat(self.group.pk, user).pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(open_chat, self.users[:2]))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(Conversation.objects.filter(group=self.group).count(), 1)

    def test_membership_and_name_stay_in_sync(self):
        conv = open_group_chat(self.group.pk, self.users[0])
        membership = Membership.objects.create(group=self.group, user=self.users[3], status='PENDING')
        self.assertFalse(conv.members.filter(user=self.users[3]).exists())
        membership.status = 'APPROVED'
        membership.save()
        self.assertTrue(conv.members.filter(user=self.users[3]).exists())
        membership.delete()
        self.assertFalse(conv.members.filter(user=self.users[3]).exists())
        self.group.name = 'Renamed group'
        self.group.save()
        conv.refresh_from_db()
        self.assertEqual(conv.name, 'Renamed group')

    def test_group_link_redirects_to_shared_screen(self):
        self.client.force_login(self.users[0])
        response = self.client.get(reverse('groups:group_chat', args=[self.group.pk]))
        conv = Conversation.objects.get(group=self.group)
        target = reverse('messaging:conversation_detail', args=[conv.pk])
        self.assertRedirects(response, target, fetch_redirect_response=False)
        response = self.client.get(reverse('groups:group_chat', args=[self.group.pk]), HTTP_HX_REQUEST='true')
        self.assertEqual(response['HX-Redirect'], target)
        response = self.client.get(target, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-conversation-type="group"')
        self.assertContains(response, reverse('groups:groups_detail', args=[self.group.pk]))
        self.assertNotContains(response, 'Group Chat Coming Soon')

    def test_send_history_notifications_and_individual_read_state(self):
        conv = open_group_chat(self.group.pk, self.users[0])
        response = self.client.post('/messaging/v1/messages/', {'conversation': conv.pk, 'content': 'Meeting at 4'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        msg_id = response.data['id']
        self.notify.assert_called_once()
        self.client.force_authenticate(self.users[1])
        history = self.client.get('/messaging/v1/messages/', {'conversation': conv.pk, 'limit': 40})
        self.assertEqual(history.data[0]['id'], msg_id)
        self.assertEqual(history.data[0]['sender']['id'], self.users[0].pk)
        response = self.client.post(f'/messaging/v1/conversations/{conv.pk}/mark_read/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(conv.members.get(user=self.users[1]).last_read_message_id, msg_id)
        self.assertIsNone(conv.members.get(user=self.users[2]).last_read_message_id)

    def test_removed_member_denied_even_after_bulk_update(self):
        conv = open_group_chat(self.group.pk, self.users[0])
        Message.objects.create(conversation=conv, sender=self.users[0], content='private')
        Membership.objects.filter(group=self.group, user=self.users[0]).update(status='REJECTED')
        self.assertFalse(accessible_conversations(self.users[0]).filter(pk=conv.pk).exists())
        response = self.client.get('/messaging/v1/messages/', {'conversation': conv.pk})
        self.assertEqual(response.data, [])
        response = self.client.post('/messaging/v1/messages/', {'conversation': conv.pk, 'content': 'blocked'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.client.force_login(self.users[0])
        self.assertEqual(self.client.get(reverse('messaging:conversation_detail', args=[conv.pk])).status_code, 404)
        # Check the function directly so this test does not depend on endpoint spelling.
        from pwaninet.offline import offline_message_access
        from django.test import RequestFactory
        request = RequestFactory().post('/', {'conversation': conv.pk, 'ids': []}, content_type='application/json')
        request.user = self.users[0]
        self.assertEqual(offline_message_access(request).status_code, 403)

    def test_generic_conversation_endpoints_cannot_change_group_membership_or_identity(self):
        conv = open_group_chat(self.group.pk, self.users[0])
        Message.objects.create(conversation=conv, sender=self.users[0], content='hello')
        for action in ['add_member', 'remove_member']:
            response = self.client.post(f'/messaging/v1/conversations/{conv.pk}/{action}/', {'user_id': self.users[3].pk})
            self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(self.client.patch(f'/messaging/v1/conversations/{conv.pk}/', {'type': 'direct'}, format='json').status_code, 403)
        self.assertEqual(self.client.delete(f'/messaging/v1/conversations/{conv.pk}/').status_code, 403)

    def test_other_member_cannot_edit_delete_or_reply_across_chats(self):
        conv = open_group_chat(self.group.pk, self.users[0])
        message = Message.objects.create(conversation=conv, sender=self.users[1], content='keep')
        self.assertEqual(self.client.patch(f'/messaging/v1/messages/{message.pk}/', {'content': 'changed'}, format='json').status_code, 403)
        self.assertEqual(self.client.delete(f'/messaging/v1/messages/{message.pk}/').status_code, 403)
        other = Conversation.objects.create()
        foreign = Message.objects.create(conversation=other, sender=self.users[0])
        response = self.client.post('/messaging/v1/messages/', {'conversation': conv.pk, 'content': 'reply', 'reply_to': foreign.pk}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_socket_permission_and_downlink_revoked(self):
        conv = open_group_chat(self.group.pk, self.users[0])
        consumer = ChatConsumer()
        consumer.user, consumer.user_id, consumer.conversation_id = self.users[0], self.users[0].pk, conv.pk
        self.assertTrue(async_to_sync(consumer._is_member)())
        Membership.objects.filter(group=self.group, user=self.users[0]).update(status='REJECTED')
        from unittest.mock import AsyncMock
        consumer.close = AsyncMock()
        async_to_sync(consumer.receive)('{"type":"typing"}')
        consumer.close.assert_awaited_with(code=4003)
        consumer.close.reset_mock()
        async_to_sync(consumer.chat_message_event)({'message': {'content': 'private'}})
        consumer.close.assert_awaited_with(code=4003)

    def test_legacy_history_import_keeps_replies_media_reactions_and_timestamp(self):
        old = GroupMessage.objects.create(group=self.group, sender=self.users[0], content='old briefing')
        reply = GroupMessage.objects.create(group=self.group, sender=self.users[1], content='understood', reply_to=old)
        GroupMessageAttachment.objects.create(message=old, file='group_message_attachments/brief.pdf', attachment_type='document')
        GroupMessageReaction.objects.create(message=old, user=self.users[1], emoji='👍')
        import_module('messaging.migrations.0021_import_group_messages').import_history(apps, SimpleNamespace(connection=SimpleNamespace(alias='default')))
        conv = Conversation.objects.get(group=self.group)
        migrated = conv.messages.get(content='old briefing')
        self.assertEqual(migrated.created_at, old.created_at)
        self.assertEqual(conv.messages.get(content='understood').reply_to_id, migrated.pk)
        self.assertEqual(migrated.attachments.get().file.name, 'group_message_attachments/brief.pdf')
        self.assertEqual(migrated.reactions.get().emoji, '👍')
        self.assertTrue(GroupMessage.objects.filter(pk=reply.pk).exists())

    def test_legacy_api_is_retired_and_direct_chat_still_works(self):
        self.client.force_login(self.users[0])
        response = self.client.post(reverse('groups:group_messages_api', args=[self.group.pk]), {'content': 'old endpoint'})
        self.assertEqual(response.status_code, 410)
        conv = Conversation.objects.create(type='direct')
        for user in self.users[:2]:
            ConversationMember.objects.create(conversation=conv, user=user)
        response = self.client.post('/messaging/v1/messages/', {'conversation': conv.pk, 'content': 'direct still works'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
