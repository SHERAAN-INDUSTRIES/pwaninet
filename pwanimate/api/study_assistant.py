from django.core.exceptions import ValidationError
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
from pwanimate.api.academic_guard import AcademicSessionWriteGuard


class MemoryView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        from pwanimate.services.personal_memory import PersonalMemoryService, enabled, payload
        from pwanimate.models import SparkPersonalMemory
        pref = PersonalMemoryService.preference(request.user)
        facts = SparkPersonalMemory.objects.filter(user=request.user)
        conversation_id = request.query_params.get('conversation_id')
        if conversation_id:
            import uuid
            try:
                conversation_id = uuid.UUID(conversation_id)
            except (ValueError, TypeError):
                return Response({'detail':'Invalid conversation'},status=400)
            facts = facts.filter(source_message__conversation_id=conversation_id)
        if request.query_params.get('status') == 'proposed':
            facts = facts.filter(status='proposed')
        response = Response({'enabled': enabled(pref), 'automatic_extraction': pref.automatic_extraction, 'memories': [],
            'facts': [payload(f) for f in facts.select_related('source_message').order_by('-updated_at')[:100]],
            'raw_conversation_recall_enabled': False})
        response['Cache-Control'] = 'private, no-store'
        return response

    def post(self, request):
        from pwanimate.services.personal_memory import PersonalMemoryService, payload
        try:
            fact = PersonalMemoryService.propose(request.user, request.data.get('text'), request.data.get('source_message_id'),
                category=request.data.get('category','semantic'))
            return Response({'fact': payload(fact)}, status=201)
        except (ValueError, ValidationError) as exc:
            return Response({'detail': str(exc)},status=400)

    def patch(self, request):
        from pwanimate.services.personal_memory import PersonalMemoryService, enabled, payload
        try:
            if 'fact_id' in request.data:
                fact = PersonalMemoryService.change(request.user, request.data['fact_id'], request.data.get('action'),
                    request.data.get('version'), request.data.get('text'))
                return Response({'fact': payload(fact) if fact else None})
            if 'automatic_extraction' in request.data:
                pref=PersonalMemoryService.set_automatic(request.user,request.data['automatic_extraction'])
            else:
                pref=PersonalMemoryService.set_enabled(request.user,request.data.get('enabled'))
            return Response({'enabled': enabled(pref), 'automatic_extraction': pref.automatic_extraction})
        except LookupError:
            return Response({'detail':'Memory not found'},status=404)
        except (ValueError, TypeError, ValidationError) as exc:
            return Response({'detail': str(exc)},status=400)

    def delete(self, request):
        StudyMemoryService.forget(request.user)
        return Response({'forgotten':True,'chat_history_deleted':False,'academic_records_deleted':False})


class MemoryDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, memory_id):
        from pwanimate.models import SparkPersonalMemory
        from pwanimate.services.personal_memory import payload
        fact = get_object_or_404(SparkPersonalMemory.objects.select_related('source_message'), pk=memory_id, user=request.user)
        response = Response({'fact': payload(fact)})
        response['Cache-Control'] = 'private, no-store'
        return response

    def patch(self, request, memory_id):
        from pwanimate.services.personal_memory import PersonalMemoryService, payload
        try:
            fact = PersonalMemoryService.change(request.user, memory_id, request.data.get('action','edit'),
                request.data.get('version'), request.data.get('text'))
            return Response({'fact': payload(fact) if fact else None})
        except LookupError:
            return Response({'detail':'Memory not found'}, status=404)
        except (ValueError, TypeError, ValidationError) as exc:
            return Response({'detail':str(exc)}, status=400)

    def delete(self, request, memory_id):
        from pwanimate.services.personal_memory import PersonalMemoryService
        try:
            PersonalMemoryService.delete(request.user, memory_id)
        except LookupError:
            return Response({'detail':'Memory not found'}, status=404)
        return Response(status=204)


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


class AssessmentView(AcademicSessionWriteGuard, APIView):
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
                    str(request.data.get('answer')) if type(request.data.get('answer')) in (str, int, float) else '')
                return Response({'result': result, 'assessment': public_assessment(assessment)})
            return Response({'detail': "action must be 'generate' or 'answer'."}, status=400)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=400)


class StudyReportView(AcademicSessionWriteGuard, APIView):
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
