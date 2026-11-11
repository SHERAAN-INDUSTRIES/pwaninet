"""Authenticated adapters for the reusable call service."""
import hashlib
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from . import media, services
from .models import Call


class StartInput(serializers.Serializer):
    conversation_id = serializers.IntegerField(min_value=1)


class ActionInput(serializers.Serializer):
    action = serializers.ChoiceField(choices=['ringing', 'accept', 'decline', 'cancel', 'leave'])


def device_key(request):
    credential = str(request.auth) if request.auth else request.session.session_key
    if not credential:
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied('An authenticated session or API token is required.')
    return hashlib.sha256(credential.encode()).hexdigest()


def result(data, status=200):
    response = Response(data, status=status)
    response['Cache-Control'] = 'private, no-store'
    return response


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def collection(request):
    if request.method == 'POST':
        media.require_configured()
        data = StartInput(data=request.data)
        data.is_valid(raise_exception=True)
        call = services.start(data.validated_data['conversation_id'], request.user, device_key(request))
        return result(services.snapshot(call), 201)
    if not settings.CALLING_ENABLED:
        return result({'calls': []})
    calls = Call.objects.filter(
        participants__user=request.user, status__in=services.OPEN,
        conversation__members__user=request.user,
    ).exclude(status='inviting', expires_at__lte=timezone.now()).order_by('-created_at')[:20]
    return result({'calls': [services.snapshot(call) for call in calls]})


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def detail(request, call_id):
    if request.method == 'POST':
        data = ActionInput(data=request.data)
        data.is_valid(raise_exception=True)
        call = services.transition(call_id, request.user, device_key(request), data.validated_data['action'])
        return result(services.snapshot(call))
    with transaction.atomic():
        call, _ = services.owned_call(call_id, request.user)
        return result(services.snapshot(call))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def connection(request, call_id):
    with transaction.atomic():
        call, participant = services.owned_call(call_id, request.user)
        if call.status not in services.OPEN or participant.status != 'accepted':
            raise services.Conflict({'detail': 'This call is no longer available for connection.', 'code': 'call_not_active'})
        if participant.answered_by != device_key(request):
            raise services.Conflict({'detail': 'This call belongs to another device session.', 'code': 'answered_elsewhere'})
        return result(media.connection_details(call, participant))
