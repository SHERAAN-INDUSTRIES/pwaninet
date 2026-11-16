"""Owner-only academic record access; all writes use existing session/action services."""
from django.db import transaction
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from pwanimate.models import (PendingAction, PwanimateMessage, PwanimateStudySession,
    SparkAcademicTrackingPreference, SparkUnitLearningRecord)
from pwanimate.services.unit_learning import UnitLearningService as Units
from pwanimate.services.actions import ActionService
from pwanimate.services.study_session import StudySessionService


class AcademicUnitsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        pref = Units.preference(request.user)
        actions = PendingAction.objects.filter(user=request.user, action_type=PendingAction.ACTION_TRACK_UNIT,
            status='pending', expires_at__gt=timezone.now(), params__tracking_revision=pref.revision)
        attribution = None
        if request.query_params.get('conversation_id'):
            import uuid
            try:
                conversation_id = uuid.UUID(request.query_params['conversation_id'])
            except (ValueError, TypeError):
                return Response({'detail': 'Invalid conversation'}, status=400)
            actions = actions.filter(conversation_id=conversation_id)
            source = PwanimateMessage.objects.filter(conversation_id=conversation_id,
                conversation__user=request.user, role='user').order_by('-id').first()
            if source:
                attribution = Units.detect(request.user, source.content)
        units = Units.enrollments(request.user)
        records = SparkUnitLearningRecord.objects.filter(user=request.user).select_related('academic_unit', 'semester__academic_year')
        response = Response({'tracking': {'mode': pref.mode, 'revision': pref.revision},
            'enrollments': [{'id': e.pk, 'unit_id': e.academic_unit_id, 'name': e.academic_unit.name,
                'code': e.academic_unit.code, 'semester_id': e.semester_id,
                'term': str(e.semester) if e.semester_id else None,
                'tracking_available': Units.eligible(e), 'source': e.source} for e in units[:100]],
            'records': [{'id': str(r.pk), 'name': r.academic_unit.name, 'semester_id': r.semester_id,
                'term': str(r.semester)} for r in records.order_by('-semester__start_date')[:100]],
            'pending_actions': [ActionService.serialize(a) for a in actions[:30]], 'attribution': attribution})
        response['Cache-Control'] = 'private, no-store'
        return response

    def patch(self, request):
        try:
            pref = Units.set_mode(request.user, request.data.get('mode'))
            return Response({'mode': pref.mode, 'revision': pref.revision})
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=400)


class AcademicDetectionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            if type(request.data.get('message_id')) not in (int, str):
                raise ValueError
            message_id = int(request.data['message_id'])
        except (ValueError, TypeError, KeyError):
            return Response({'detail': 'A valid message_id is required'}, status=400)
        source = get_object_or_404(PwanimateMessage, pk=message_id,
            conversation__user=request.user, role='user')
        return Response(Units.detect(request.user, source.content))


class UnitLearningRecordView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, record_id):
        record = get_object_or_404(SparkUnitLearningRecord, pk=record_id, user=request.user)
        response = Response(Units.summary(record, request.user))
        response['Cache-Control'] = 'private, no-store'
        return response

    def post(self, request, record_id):
        record = get_object_or_404(SparkUnitLearningRecord, pk=record_id, user=request.user)
        try:
            with transaction.atomic():
                pref = SparkAcademicTrackingPreference.objects.select_for_update().get(user=request.user)
                Units.require_writable(record, request.user)
                action = request.data.get('action')
                if action == 'start':
                    if request.data.get('tracking_consent') is not True or pref.mode == 'disabled':
                        raise ValueError('Enable tracking and explicitly approve this new unit session.')
                    session = Units._session(request.user, record, new=True)
                    record.authorized_at = timezone.now()
                    record.consent_revision = pref.revision
                    record.authorized_conversation = session.conversation
                    record.save()
                else:
                    session = get_object_or_404(PwanimateStudySession, pk=request.data.get('session_id'),
                        user=request.user, learning_record=record)
                    if action == 'resume' and (pref.mode == 'disabled' or record.consent_revision != pref.revision):
                        raise ValueError('Tracking consent must be renewed before resuming.')
                    methods = {'pause': StudySessionService.pause_session, 'resume': StudySessionService.resume_session,
                        'end': StudySessionService.end_session}
                    if action not in methods:
                        raise ValueError('action must be start, pause, resume or end')
                    session = methods[action](request.user, session.pk)
            return Response({'session': StudySessionService.serialize_session(session)})
        except (ValueError, ValidationError) as exc:
            return Response({'detail': str(exc)}, status=400)
