"""Student APIs never accept grading verdicts or directly assign mastery."""
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from pwanimate.models import SparkUnitLearningRecord, SparkUnitConcept
from pwanimate.services.concept_mastery import ConceptMasteryService as Mastery
from pwanimate.services.assessment import public_assessment


def body(request, allowed):
    if not isinstance(request.data, dict) or set(request.data) - set(allowed):
        raise ValueError('Unsupported fields; mastery/evaluation metadata is server-controlled')
    return dict(request.data)


class MasteryAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def handle_exception(self, exc):
        if isinstance(exc, LookupError):
            return Response({'detail': str(exc)}, status=404)
        if isinstance(exc, (ValueError, ValidationError, TypeError)):
            return Response({'detail': str(exc)}, status=400)
        return super().handle_exception(exc)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        return response


class UnitConceptListView(MasteryAPIView):
    def get(self, request, record_id):
        record = get_object_or_404(SparkUnitLearningRecord, pk=record_id, user=request.user)
        return Response({'concepts': [Mastery.concept_payload(c) for c in record.concepts.all()]})

    def post(self, request, record_id):
        get_object_or_404(SparkUnitLearningRecord, pk=record_id, user=request.user)
        data = body(request, {'key', 'title', 'description', 'parent_id', 'prerequisites'})
        if 'key' not in data or 'title' not in data:
            raise ValueError('key and title are required')
        concept = Mastery.create_concept(request.user, record_id, **data)
        return Response({'concept': Mastery.concept_payload(concept)}, status=201)


class UnitMasterySummaryView(MasteryAPIView):
    def get(self, request, record_id):
        get_object_or_404(SparkUnitLearningRecord, pk=record_id, user=request.user)
        return Response(Mastery.unit_summary(request.user, record_id))


class ConceptDetailView(MasteryAPIView):
    def get(self, request, concept_id):
        concept = Mastery.owned_concept(request.user, concept_id)
        return Response({'concept': Mastery.concept_payload(concept),
            'transitions': list(concept.transitions.order_by('-version').values('version', 'from_state',
                'to_state', 'policy_version', 'policy', 'decision', 'created_at')[:100])})

    def patch(self, request, concept_id):
        concept = Mastery.update_concept(request.user, concept_id,
            **body(request, {'title', 'description', 'parent_id', 'prerequisites'}))
        return Response({'concept': Mastery.concept_payload(concept)})


class ConceptRecalculateView(MasteryAPIView):
    def post(self, request, concept_id):
        body(request, set())
        return Response({'concept': Mastery.concept_payload(Mastery.recalculate(request.user, concept_id))})


class ConceptEvidenceView(MasteryAPIView):
    def get(self, request, concept_id):
        concept = Mastery.owned_concept(request.user, concept_id)
        rows = concept.evidence.select_related('question__assessment__session', 'concept__record', 'activity').order_by('-occurred_at')[:100]
        return Response({'evidence': [Mastery.evidence_payload(e) for e in rows],
            'truncated': concept.evidence.count() > 100})

    def post(self, request, concept_id):
        concept = Mastery.owned_concept(request.user, concept_id)
        data = body(request, {'assessment_id', 'qid', 'activity_id'})
        if set(data) == {'activity_id'}:
            evidence = Mastery.record_activity(request.user, concept_id, data['activity_id'])
        elif set(data) == {'assessment_id', 'qid'}:
            # Check the binding before capture to prevent cross-concept writes.
            if not concept.question_bindings.filter(assessment_id=data['assessment_id'], qid=data['qid']).exists():
                raise LookupError('Concept question not found')
            evidence = Mastery.capture(request.user, data['assessment_id'], data['qid'])
        else:
            raise ValueError('Provide assessment_id/qid or activity_id, never a grading verdict')
        return Response({'evidence_id': str(evidence.pk)}, status=201)


class ConceptAssessmentView(MasteryAPIView):
    def post(self, request, concept_id):
        data = body(request, {'session_id', 'purpose', 'count'})
        if 'session_id' not in data:
            raise ValueError('session_id is required')
        assessment = Mastery.deliver_reference_assessment(request.user, concept_id, **data)
        return Response({'assessment': public_assessment(assessment)}, status=201)
