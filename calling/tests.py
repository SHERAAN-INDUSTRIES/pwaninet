from datetime import timedelta
from unittest.mock import patch

import jwt
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from messaging.models import Conversation, ConversationMember
from users.models import Block
from . import media, services
from .models import Call


@override_settings(CALLING_ENABLED=True, LIVEKIT_URL='wss://example.invalid',
                   LIVEKIT_API_KEY='test-key', LIVEKIT_API_SECRET='test-secret-at-least-thirty-two-bytes')
class CallingTests(TestCase):
    def setUp(self):
        self.caller, self.recipient, self.stranger = [
            get_user_model().objects.create_user(username=name, password='test')
            for name in ['caller', 'recipient', 'stranger']
        ]
        self.conversation = Conversation.objects.create()
        for user in [self.caller, self.recipient]:
            ConversationMember.objects.create(conversation=self.conversation, user=user)
        self.client = APIClient()
        self.client.force_login(self.caller)

    def start(self):
        return services.start(self.conversation.pk, self.caller, 'caller-device')

    def test_outsider_cannot_read_act_or_obtain_token(self):
        call = self.start()
        self.client.force_login(self.stranger)
        for path, method, data in [
            (f'/api/calling/{call.pk}/', 'get', None),
            (f'/api/calling/{call.pk}/', 'post', {'action': 'accept'}),
            (f'/api/calling/{call.pk}/connection/', 'post', {}),
        ]:
            response = getattr(self.client, method)(path, data, format='json')
            self.assertEqual(response.status_code, 404)

    def test_invitation_acceptance_and_device_ownership(self):
        call = self.start()
        with self.assertRaises(services.Conflict):
            services.transition(call.pk, self.caller, 'caller-device', 'accept')
        services.transition(call.pk, self.recipient, 'phone-a', 'ringing')
        call = services.transition(call.pk, self.recipient, 'phone-a', 'accept')
        self.assertEqual(call.status, 'active')
        self.assertEqual(services.transition(call.pk, self.recipient, 'phone-a', 'accept').status, 'active')
        with self.assertRaises(services.Conflict):
            services.transition(call.pk, self.recipient, 'phone-b', 'accept')
        with self.assertRaises(services.Conflict):
            services.transition(call.pk, self.recipient, 'phone-b', 'leave')

    def test_expired_invitation_cannot_be_accepted(self):
        call = self.start()
        Call.objects.filter(pk=call.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        call = services.transition(call.pk, self.recipient, 'phone', 'accept')
        self.assertEqual(call.status, 'missed')
        self.assertIsNone(call.accepted_at)
        self.assertEqual(call.participants.get(user=self.recipient).status, 'missed')

    def test_cancelled_call_stays_cancelled_on_late_accept(self):
        call = self.start()
        services.transition(call.pk, self.caller, 'caller-device', 'cancel')
        self.assertEqual(services.transition(call.pk, self.recipient, 'phone', 'accept').status, 'cancelled')

    def test_blocks_in_either_direction_prevent_calls(self):
        for blocker, blocked in [(self.caller, self.recipient), (self.recipient, self.caller)]:
            block = Block.objects.create(blocker=blocker, blocked=blocked)
            with self.assertRaises(services.PermissionDenied):
                self.start()
            block.delete()
        self.assertFalse(Call.objects.exists())

    def test_busy_recipient_and_group_calls_rejected(self):
        self.start()
        with self.assertRaises(services.Conflict):
            self.start()
        self.conversation.type = 'group'
        self.conversation.save()
        with self.assertRaises(services.Conflict):
            self.start()

    def test_removed_member_cannot_get_connection(self):
        call = self.start()
        ConversationMember.objects.filter(conversation=self.conversation, user=self.caller).delete()
        with self.assertRaises(services.PermissionDenied):
            with services.transaction.atomic():
                services.owned_call(call.pk, self.caller)

    def test_livekit_grants_are_audio_only_short_lived_and_private(self):
        call = self.start()
        participant = call.participants.get(user=self.caller)
        details = media.connection_details(call, participant)
        claims = jwt.decode(details['participant_token'],
                            'test-secret-at-least-thirty-two-bytes', algorithms=['HS256'])
        self.assertEqual(claims['sub'], str(participant.pk))
        self.assertEqual(claims['video']['room'], call.room_name)
        self.assertEqual(claims['video']['canPublishSources'], ['microphone'])
        self.assertFalse(claims['video']['canPublishData'])
        self.assertLessEqual(claims['exp'] - claims['nbf'], 120)
        self.assertNotIn('roomAdmin', claims['video'])

    def test_api_validation_and_no_store(self):
        response = self.client.post('/api/calling/', {'conversation_id': self.conversation.pk}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertNotIn('participant_token', response.data)
        self.assertEqual(self.client.post('/api/calling/', {'conversation_id': 'bad'}, format='json').status_code, 400)

    @override_settings(CALLING_ENABLED=False)
    def test_disabled_calling_returns_503(self):
        self.assertEqual(self.client.post('/api/calling/', {'conversation_id': self.conversation.pk}, format='json').status_code, 503)

    def test_events_go_only_to_participants_after_commit(self):
        with patch('calling.services.get_channel_layer') as layer:
            sent = []
            async def capture(group, event):
                sent.append((group, event))
            layer.return_value.group_send = capture
            with self.captureOnCommitCallbacks(execute=True):
                self.start()
            self.assertEqual({group for group, _ in sent}, {
                f'notifications_{self.caller.pk}', f'notifications_{self.recipient.pk}',
            })
            self.assertEqual(len(sent), 2)


    def test_invited_recipient_cannot_get_audio_token_before_accepting(self):
        call = self.start()
        self.client.force_login(self.recipient)
        response = self.client.post(f'/api/calling/{call.pk}/connection/', {}, format='json')
        self.assertEqual(response.status_code, 409)

    def test_decline_frees_participants_for_a_new_call(self):
        call = self.start()
        call = services.transition(call.pk, self.recipient, 'phone', 'decline')
        self.assertEqual(call.status, 'declined')
        self.assertEqual(call.participants.get(user=self.recipient).status, 'declined')
        self.assertNotEqual(self.start().pk, call.pk)

    def test_recovery_only_returns_own_open_calls(self):
        call = self.start()
        response = self.client.get('/api/calling/')
        self.assertEqual([c['id'] for c in response.data['calls']], [str(call.pk)])
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get('/api/calling/').data['calls'], [])

    def test_expiry_command_persists_missed_state(self):
        from django.core.management import call_command
        from io import StringIO
        call = self.start()
        Call.objects.filter(pk=call.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        call_command('expire_calls', stdout=StringIO())
        call.refresh_from_db()
        self.assertEqual(call.status, 'missed')

    def test_call_snapshot_has_actual_participant_identity_without_session_secrets(self):
        call = self.start()
        snapshot = services.snapshot(call)
        caller = next(p for p in snapshot['participants'] if p['user_id'] == self.caller.pk)
        self.assertEqual(caller['display_name'], 'caller')
        self.assertEqual(caller['avatar_url'], self.caller.profile_pic.url if self.caller.profile_pic else '')
        self.assertNotIn('answered_by', caller)

    @override_settings(CALLING_ENABLED=False)
    def test_disabled_recovery_does_not_access_call_tables(self):
        from calling.views import collection
        from rest_framework.test import APIRequestFactory, force_authenticate
        request = APIRequestFactory().get('/api/calling/')
        force_authenticate(request, user=self.caller)
        with self.assertNumQueries(0):
            self.assertEqual(collection(request).data, {'calls': []})

    def test_connection_conflict_identifies_device_ownership_explicitly(self):
        call = self.start()
        response = self.client.post(f'/api/calling/{call.pk}/connection/', {}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'answered_elsewhere')

    def test_finished_call_has_one_history_bubble_owned_by_caller(self):
        from messaging.serializers import MessageSerializer
        call = self.start()
        services.transition(call.pk, self.recipient, 'phone', 'accept')
        services.transition(call.pk, self.recipient, 'phone', 'leave')
        services.transition(call.pk, self.caller, 'caller-device', 'leave')
        call.refresh_from_db()
        self.assertEqual(self.conversation.messages.count(), 1)
        message = call.history_message
        self.assertEqual(message.sender_id, self.caller.pk)
        self.assertEqual(message.message_type, 'voice_call')
        data = MessageSerializer(message).data['voice_call']
        self.assertEqual(data['started_at'], call.created_at.isoformat())
        self.assertEqual(data['status'], 'ended')
        self.assertIsNotNone(data['ended_at'])

    def test_missed_call_history_and_forged_history_rejected(self):
        call = self.start()
        Call.objects.filter(pk=call.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        services.transition(call.pk, self.recipient, 'phone', 'accept')
        call.refresh_from_db()
        self.assertEqual(call.history_message.content, 'Missed voice call')
        response = self.client.post('/messaging/v1/messages/', {
            'conversation': self.conversation.pk, 'message_type': 'voice_call', 'content': 'Fake call',
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_chat_navigation_returns_layout_for_page_target_and_fragment_for_pane(self):
        path = f'/messaging/conversation/{self.conversation.pk}/'
        with patch('users.services.friend_suggestion_service.get_friend_suggestions_for_user', return_value=[]):
            response = self.client.get(path, HTTP_HX_REQUEST='true', HTTP_HX_TARGET='page-content-target')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="whatsappLayout"')
        self.assertContains(response, 'css/messaging/conversation-detail.css')
        fragment = self.client.get(path, HTTP_HX_REQUEST='true', HTTP_HX_TARGET='chatMainAreaWrapper')
        self.assertEqual(fragment.status_code, 200)
        self.assertNotContains(fragment, 'id="whatsappLayout"')
        self.assertContains(fragment, 'id="chatMainArea"')

    def test_history_is_readable_in_both_participants_chat_history(self):
        call = self.start()
        services.transition(call.pk, self.caller, 'caller-device', 'cancel')
        for user in (self.caller, self.recipient):
            self.client.force_login(user)
            response = self.client.get(f'/messaging/v1/messages/?conversation={self.conversation.pk}&limit=20')
            self.assertEqual(response.status_code, 200)
            bubbles = [m for m in response.data if m['message_type'] == 'voice_call']
            self.assertEqual(len(bubbles), 1)
            self.assertEqual(bubbles[0]['sender']['id'], self.caller.pk)
            self.assertEqual(bubbles[0]['voice_call']['status'], 'cancelled')

    def test_backfill_restores_old_calls_once_with_original_ordering(self):
        import importlib
        from django.apps import apps
        from django.db import connection
        migration = importlib.import_module('calling.migrations.0003_backfill_call_history')
        call = self.start()
        services.transition(call.pk, self.caller, 'caller-device', 'cancel')
        call.refresh_from_db()
        call.history_message.delete()
        with connection.schema_editor() as editor:
            migration.backfill(apps, editor)
            migration.backfill(apps, editor)
        call.refresh_from_db()
        self.assertEqual(self.conversation.messages.count(), 1)
        self.assertEqual(call.history_message.created_at, call.ended_at)
