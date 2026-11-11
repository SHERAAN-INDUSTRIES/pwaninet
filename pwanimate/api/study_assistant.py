from django.shortcuts import get_object_or_404
from django.db import transaction
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from pwanimate.models import (PwanimateStudySession, SparkAssessment, SparkConversationDigest,
                              SparkReminder, SparkReminderDevice, SparkStudyArtifact, SparkStudyPlan)
from pwanimate.services.assessment import AssessmentService, public_assessment
from pwanimate.services.study_report import artifact_payload, generate_pack, session_stats
from pwanimate.services.study_memory import StudyMemoryService
from pwanimate.services.personal_planner import PersonalPlannerService, reminder_payload


class MemoryView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        pref = StudyMemoryService.preference(request.user)
        memories = SparkConversationDigest.objects.filter(conversation__user=request.user).order_by('-updated_at')[:20]
        return Response({'enabled': pref.enabled, 'memories': [
            {'conversation_id': str(m.conversation_id), 'topic': m.topic, 'summary': m.summary, 'next_step': m.next_step} for m in memories]})
    def patch(self, request):
        if type(request.data.get('enabled')) is not bool:
            return Response({'detail': 'enabled must be a boolean.'}, status=400)
        pref = StudyMemoryService.preference(request.user)
        pref.enabled = request.data['enabled']
        pref.save(update_fields=['enabled'])
        return Response({'enabled': pref.enabled})
    def delete(self, request):
        StudyMemoryService.forget(request.user)
        return Response({'forgotten': True, 'chat_history_deleted': False})


class WelcomeView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        response = Response(StudyMemoryService.welcome(request.user))
        response['Cache-Control'] = 'private, no-store'
        return response


class ReminderView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        return Response({'reminders': [reminder_payload(i) for i in SparkReminder.objects.filter(user=request.user).prefetch_related('devices').order_by('-created_at')[:200]]})
    def post(self, request):
        try:
            item = PersonalPlannerService.create_reminder(request.user, str(request.data.get('title', '')),
                request.data.get('due_at'), request.data.get('timezone'), request.headers.get('Idempotency-Key', ''))
            return Response(reminder_payload(item), status=201)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=400)
    def delete(self, request, reminder_id):
        with transaction.atomic():
            item = get_object_or_404(SparkReminder.objects.select_for_update(), pk=reminder_id, user=request.user)
            if item.status == 'scheduled':
                item.status = 'cancelled'
                item.save(update_fields=['status'])
        return Response(reminder_payload(item))


class ReminderDeviceView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request, reminder_id):
        installation = str(request.data.get('installation', '')).strip()
        if not 10 <= len(installation) <= 100:
            return Response({'detail': 'A stable installation ID is required.'}, status=400)
        with transaction.atomic():
            item = get_object_or_404(SparkReminder.objects.select_for_update(), pk=reminder_id, user=request.user)
            if item.status != 'scheduled':
                return Response({'detail': 'Reminder is no longer scheduled.'}, status=409)
            SparkReminderDevice.objects.update_or_create(reminder=item, installation=installation)
        return Response({'phone_scheduled': True})


class StudyPlansView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        return Response({'plans': list(SparkStudyPlan.objects.filter(user=request.user).order_by('-created_at').values('id', 'title', 'objective', 'sessions')[:30])})


class AssessmentView(APIView):
    """Generate and answer quizzes inside a unit-scoped study session.

    Answer keys never leave the server; grading is server-side only.
    """
    permission_classes = [IsAuthenticated]

    def _session(self, request, session_id):
        return get_object_or_404(PwanimateStudySession, pk=session_id, user=request.user)

    def get(self, request, session_id):
        session = self._session(request, session_id)
        return Response({'assessments': [public_assessment(a) for a in session.assessments.all()[:20]]})

    def post(self, request, session_id):
        session = self._session(request, session_id)
        action = str(request.data.get('action') or '').strip().lower()
        try:
            if action == 'generate':
                assessment = AssessmentService.generate(
                    session,
                    count=request.data.get('count', 5),
                    mode=request.data.get('mode') or None,
                    topic=str(request.data.get('topic') or ''),
                )
                return Response({'assessment': public_assessment(assessment)}, status=201)
            if action == 'answer':
                assessment = SparkAssessment.objects.filter(
                    pk=request.data.get('assessment_id'), session=session, user=request.user).first()
                if assessment is None:
                    return Response({'detail': 'Assessment not found.'}, status=404)
                result = AssessmentService.submit_answer(
                    assessment, str(request.data.get('qid') or ''),
                    str(request.data.get('answer') or ''))
                return Response({'result': result, 'assessment': public_assessment(assessment)})
            return Response({'detail': "action must be 'generate' or 'answer'."}, status=400)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=400)


class StudyReportView(APIView):
    """End-of-session pack: performance report, practice quiz, summary."""
    permission_classes = [IsAuthenticated]

    def _session(self, request, session_id):
        return get_object_or_404(PwanimateStudySession, pk=session_id, user=request.user)

    def get(self, request, session_id):
        session = self._session(request, session_id)
        return Response({
            'stats': session_stats(session),
            'artifacts': [artifact_payload(a) for a in session.artifacts.all()[:30]],
        })

    def post(self, request, session_id):
        session = self._session(request, session_id)
        data = request.data or {}
        pack = generate_pack(
            session,
            include_practice=data.get('include_practice', True) is not False,
            include_summary=data.get('include_summary', True) is not False,
        )
        return Response({
            'stats': pack['stats'],
            'report': artifact_payload(pack['report']),
            'practice': artifact_payload(pack['practice']) if pack['practice'] else None,
            'summary': artifact_payload(pack['summary']) if pack['summary'] else None,
        }, status=201)
