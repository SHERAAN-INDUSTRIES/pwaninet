"""Owner-only instructional preferences; verdicts and phase writes are never accepted."""
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from pwanimate.api.concept_mastery import MasteryAPIView, body
from pwanimate.models import PwanimateStudySession, SparkTeachingState
from pwanimate.services.teaching import TeachingService


class TeachingStateView(MasteryAPIView):
    def get(self, request, session_id):
        session = get_object_or_404(PwanimateStudySession, pk=session_id, user=request.user)
        state = SparkTeachingState.objects.filter(session=session).first()
        return Response({'teaching': TeachingService.payload(state) if state else None,
            'history': list(state.turns.values('from_phase','to_phase','activity_kind','assistance',
                'evaluation','created_at').order_by('-created_at')[:100]) if state else []})

    def patch(self, request, session_id):
        session = get_object_or_404(PwanimateStudySession, pk=session_id, user=request.user)
        data = body(request, {'mode','version','concept_id','objective'})
        if 'mode' not in data:
            raise ValueError('mode is required')
        return Response({'teaching': TeachingService.payload(TeachingService.configure(session, request.user, **data))})
