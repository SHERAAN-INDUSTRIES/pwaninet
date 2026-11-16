"""Validated assessment feedback; stored legacy percentages never certify mastery."""
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from django.utils import timezone
from django.db import transaction

from pwanimate.ai.gateway.gateway import AIGateway
from pwanimate.ai.gateway.types import ChatMessage, LLMRequest
from pwanimate.models import SparkAssessment
from pwanimate.assessment_validation import strict_object, validate_questions, grade, metrics, REFERENCES
from pwanimate.services.study_session import StudySessionService

logger = logging.getLogger(__name__)

MAX_QUESTIONS = 10
MIN_QUESTIONS = 1
GRADING_PROMPT = (
    "You are grading a student's answer inside a study session. Compare the "
    "student answer to the reference answer for the question. Reply ONLY with "
    "a JSON object {\"correct\": true|false, \"feedback\": \"one short "
    "sentence for the student\"}. Accept equivalent phrasing and correct "
    "reasoning; mark incorrect only when the substance is wrong or missing."
)


def _parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    return strict_object(text)


def public_question(item: Dict[str, Any]) -> Dict[str, Any]:
    """Client-safe question: answer key and raw student answer stay server-side."""
    answered = type(item.get('correct')) is bool
    return {
        'qid': item.get('qid'),
        'prompt': item.get('prompt', ''),
        'options': item.get('options') or None,
        'answered': answered,
        'correct': item.get('correct') if answered else None,
        'outcome': item.get('outcome', 'correct' if item.get('correct') is True else 'incorrect' if item.get('correct') is False else None),
        'certification_status': item.get('certification_status', 'legacy_uncertified'),
        'feedback': item.get('feedback') if answered or item.get('outcome') else None,
        'submitted': item.get('student_answer') is not None,
        'explanation': item.get('explanation') if answered else None,
    }


def public_assessment(assessment: SparkAssessment) -> Dict[str, Any]:
    questions = assessment.questions if isinstance(assessment.questions, list) else []
    graded = [q for q in questions if isinstance(q, dict) and type(q.get('correct')) is bool]
    correct = sum(1 for q in graded if q.get('correct') is True)
    return {
        'id': str(assessment.id),
        'mode': assessment.mode,
        'status': assessment.status,
        'title': assessment.title,
        'created_at': assessment.created_at.isoformat() if assessment.created_at else None,
        'completed_at': assessment.completed_at.isoformat() if assessment.completed_at else None,
        'total': len(questions),
        'answered': len(graded),
        'correct': correct,
        'counts_toward_mastery': bool(getattr(assessment, 'counts_toward_mastery', True)),
        'questions': [public_question(q) for q in questions if isinstance(q, dict)],
        'metrics': metrics(questions),
        'legacy_metric_semantics': 'answered counts decisive grades; legacy mastery is not certified',
        'certification_status': 'legacy_uncertified' if any(not isinstance(q, dict) or not q.get('validation_version') for q in questions) else 'no_mastery_certification',
    }


class AssessmentService:
    """Generate, administer, and grade session assessments."""

    @staticmethod
    def _unit_context(session) -> str:
        parts = []
        if getattr(session, 'unit', None) is not None:
            unit = session.unit
            parts.append(f"{getattr(unit, 'code', '')} {getattr(unit, 'name', '')}".strip())
        if session.unit_label:
            parts.append(session.unit_label)
        if session.target_goal:
            parts.append(f"Goal: {session.target_goal}")
        if session.learning_objective:
            parts.append(f"Objective: {session.learning_objective}")
        roadmap = session.roadmap if isinstance(session.roadmap, list) else []
        labels = [str(i.get('label')) for i in roadmap if isinstance(i, dict) and i.get('label')]
        if labels:
            parts.append('Roadmap: ' + '; '.join(labels))
        return ' | '.join(p for p in parts if p) or 'general study'

    @classmethod
    def generate(cls, session, count: int = 5, mode: Optional[str] = None,
                 topic: str = '', gateway: Optional[AIGateway] = None,
                 practice: bool = False) -> SparkAssessment:
        """Create a new assessment with server-side answer keys.

        `practice=True` produces revision material: answers still grade for
        feedback, but the quiz is excluded from mastery calculations.
        """
        if type(count) is not int or not MIN_QUESTIONS <= count <= MAX_QUESTIONS:
            raise ValueError('Question count must be an integer from 1 to 10')
        mode = (mode or session.assessment_mode or SparkAssessment.MODE_IMMEDIATE).strip().lower()
        if mode not in {c for c, _ in SparkAssessment.MODE_CHOICES}:
            mode = SparkAssessment.MODE_IMMEDIATE
        ai_gateway = gateway or AIGateway()
        focus = (topic or '').strip()[:300] or cls._unit_context(session)
        mcq_instruction = (
            ' Every question MUST be multiple choice with exactly 4 options; '
            'put the correct option text in "answer".' if mode == SparkAssessment.MODE_MCQ else '')
        prompt = (
            f'Create {count} study assessment questions for this student.\n'
            f'Context: {focus}\n'
            f'Question style: short, precise, exam-style.{mcq_instruction}\n'
            'Return ONLY a JSON object {"questions":[{"prompt":"...","options":["a","b","c","d"] (mcq only),'
            '"answer":"correct answer or option text","explanation":"one-line why"}]}. '
            'Vary difficulty; test understanding, not memorization of phrasing. '
            'For numerical answers use dimensionless decimal or rational literals. '
            'Optional verified references (use the exact prompt and reference_id only when relevant): '
            + json.dumps(REFERENCES)
        )
        llm_req = LLMRequest(
            task='general',
            messages=[ChatMessage(role='user', content=prompt)],
            system_instruction='You are an assessment writer for a study app. Respond ONLY with valid JSON.',
            temperature=0.4,
            max_tokens=2000,
        )
        parsed = _parse_json_object((ai_gateway.generate(llm_req).content or ''))
        raw_questions = parsed.get('questions') if isinstance(parsed, dict) else None
        questions = validate_questions(raw_questions, count, mode)
        assessment = SparkAssessment.objects.create(
            session=session, user=session.user, mode=mode,
            title=(f'Practice — ' if practice else 'Assessment — ') + cls._unit_context(session)[:110],
            questions=questions,
            counts_toward_mastery=False)
        # A fresh assessment means the student is being tested — mark the
        # session's current focus as 'doing' if a roadmap exists.
        return assessment

    @classmethod
    def list_for_session(cls, session) -> List[SparkAssessment]:
        return list(session.assessments.all())

    @classmethod
    def get_owned(cls, user, assessment_id) -> Optional[SparkAssessment]:
        return SparkAssessment.objects.filter(pk=assessment_id, user=user).select_related('session').first()

    @classmethod
    def _find_question(cls, assessment: SparkAssessment, qid: str) -> Optional[Dict[str, Any]]:
        for item in assessment.questions if isinstance(assessment.questions, list) else []:
            if isinstance(item, dict) and item.get('qid') == qid:
                return item
        return None

    @classmethod
    def submit_answer(cls, assessment: SparkAssessment, qid: str, answer: str,
                      gateway: Optional[AIGateway] = None) -> Dict[str, Any]:
        """Serialize actual submissions and concept evidence in one transaction."""
        from pwanimate.services.concept_mastery import ConceptMasteryService
        with transaction.atomic():
            if assessment.session.learning_record_id:
                ConceptMasteryService.lock_record(assessment.user, assessment.session.learning_record_id)
            locked = SparkAssessment.objects.select_for_update().get(pk=assessment.pk, user_id=assessment.user_id)
            from pwanimate.models import SparkConceptQuestion
            item = cls._find_question(locked, qid)
            frozen_attempt = bool(item and item.get('student_answer') is not None
                and SparkConceptQuestion.objects.filter(assessment=locked, qid=qid).exists())
            if frozen_attempt:
                # A bound question has one accepted response, even when grading
                # was inconclusive. Reassessment must issue a new question;
                # replay must never rewrite the source of immutable evidence.
                result = {'already_graded': True, 'correct': item.get('correct'),
                    'outcome': item.get('outcome'), 'feedback': item.get('feedback'),
                    'explanation': item.get('explanation'), 'metrics': metrics(locked.questions),
                    'mastery_state': 'insufficient_evidence'}
            else:
                result = cls._submit_answer_locked(locked, qid, answer, gateway=gateway)
            if locked.session.learning_record_id and result.get('outcome') != 'invalid':
                evidence = ConceptMasteryService.capture_locked(locked.user, locked, qid)
                if evidence:
                    evidence.concept.refresh_from_db()
                    result['concept_mastery'] = ConceptMasteryService.concept_payload(evidence.concept)
                    result['evidence_id'] = str(evidence.pk)
            assessment.refresh_from_db()
            return result

    @classmethod
    def _submit_answer_locked(cls, assessment: SparkAssessment, qid: str, answer: str,
                              gateway: Optional[AIGateway] = None) -> Dict[str, Any]:
        """Grade supported answers without provider calls or mastery promotion."""
        item = cls._find_question(assessment, qid)
        if item is None:
            raise ValueError('Question not found in this assessment.')
        if type(item.get('correct')) is bool:
            return {'already_graded': True, 'correct': item['correct'],
                    'outcome': 'correct' if item['correct'] else 'incorrect',
                    'feedback': item.get('feedback'), 'explanation': item.get('explanation'),
                    'mastery_state': 'insufficient_evidence', 'metrics': metrics(assessment.questions)}
        if assessment.status != SparkAssessment.STATUS_ACTIVE:
            raise ValueError('This assessment is already completed.')
        result = grade(item, answer)
        if result['outcome'] == 'invalid':
            return dict(result, metrics=metrics(assessment.questions), mastery_state='insufficient_evidence')
        correct = result['correct']
        feedback = result['feedback']
        item['outcome'] = result['outcome']
        item['student_answer'] = answer
        item['correct'] = correct
        item['feedback'] = feedback or ('Correct.' if correct is True else 'Not quite — review this section.' if correct is False else 'Unable to establish correctness.')
        item['graded_at'] = timezone.now().isoformat()
        assessment.questions = [
            item if isinstance(q, dict) and q.get('qid') == qid else q
            for q in (assessment.questions if isinstance(assessment.questions, list) else [])
        ]
        all_answered = all(
            isinstance(q, dict) and type(q.get('correct')) is bool for q in assessment.questions)
        if all_answered:
            assessment.status = SparkAssessment.STATUS_COMPLETED
            assessment.completed_at = timezone.now()
        assessment.save()

        return {
            'correct': correct,
            'outcome': result['outcome'],
            'metrics': metrics(assessment.questions),
            'mastery_state': 'insufficient_evidence',
            'legacy_metric_semantics': 'Stored mastery/progress percentages are legacy, not certified mastery',
            'feedback': item.get('feedback'),
            'explanation': item.get('explanation'),
            'assessment_complete': assessment.status == SparkAssessment.STATUS_COMPLETED,
            'mastery_percent': assessment.session.mastery_percent,
            'progress_percent': assessment.session.progress_percent,
        }

    @classmethod
    def _recompute_mastery(cls, session) -> int:
        """Compatibility hook: this subset cannot certify mastery."""
        # Certification requires versioned independent/retention evidence (deferred).
        # Preserve historical fields, but never update them from graded accuracy.
        return 0
